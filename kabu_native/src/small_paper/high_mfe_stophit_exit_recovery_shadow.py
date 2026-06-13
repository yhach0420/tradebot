"""
Phase371: High-MFE stop_hit EXIT recovery shadow (events-based tick path).

Uses live small_paper_events candidate/accepted rows as holding ticks (Phase356 pattern).
No tick CSV. No board-dynamic production re-sim counterfactual.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.phase360_eother_classification import entry_time_bucket
from research.phase366_stophit_reclassification import classify_mfe_band, production_kept_trades
from research.structural_exit_policies import trailing_mfe_exit_triggered, trailing_mfe_params
from small_paper.board_dynamic_trailing_shadow import board_tier_from_percentile
from small_paper.pullback_misread_entry_guard_shadow import (
    _stream_events_csv,
    enrich_trade_features_for_review,
)

JST = ZoneInfo("Asia/Tokyo")
HARD_STOP_PCT = 1.20
HIGH_MFE_THRESHOLD = 0.3

EXIT_CANDIDATES = (
    "mfe_0p3_profit_protect",
    "mfe_0p5_profit_protect",
    "board_low_tighter_trailing",
    "time_after_mfe_decay",
)

CANDIDATE_LABELS = {
    "mfe_0p3_profit_protect": "peak_mfe>=0.3% then exit before pnl<=0%",
    "mfe_0p5_profit_protect": "peak_mfe>=0.5% then exit when pnl<=0.1%",
    "board_low_tighter_trailing": "board_low only: activate 0.4% / giveback 0.55",
    "time_after_mfe_decay": "3min after last MFE high without renewal, pnl<50% of peak",
}

BOARD_LOW_TIGHTER_ACTIVATE = 0.4
BOARD_LOW_TIGHTER_GIVEBACK = 0.55
MFE_DECAY_MINUTES = 3.0
MFE_DECAY_RETENTION = 0.5


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _bool(val: Any) -> bool:
    return str(val or "").strip().lower() in ("true", "1", "yes")


def _pf(yens: Sequence[float]) -> Optional[float]:
    gp = sum(max(y, 0.0) for y in yens)
    gl = abs(sum(min(y, 0.0) for y in yens))
    if gl <= 0:
        return None if gp <= 0 else float("inf")
    return round(gp / gl, 4)


def _pnl_pct(entry_price: float, price: float) -> float:
    if entry_price <= 0:
        return 0.0
    return round((price - entry_price) / entry_price * 100.0, 4)


def _parse_dt(raw: str) -> datetime:
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return dt.replace(tzinfo=JST)
        return dt.astimezone(JST)
    except (TypeError, ValueError):
        return datetime.now(JST)


def _pnl_yen(entry_price: float, pnl_pct: float) -> float:
    exit_price = entry_price * (1.0 + pnl_pct / 100.0)
    return round((exit_price - entry_price) * 100.0, 2)


def is_high_mfe_stop(trade: Mapping[str, Any]) -> bool:
    peak = _float(trade.get("peak_mfe_pct")) or 0.0
    return (
        trade.get("exit_reason_canonical") == "stop_hit"
        and peak >= HIGH_MFE_THRESHOLD
    )


@dataclass
class _TickPoint:
    ts_epoch: float
    pnl_pct: float
    price: float


def _build_tick_paths(
    events_path: Any,
    trade_keys: set[tuple[str, str]],
) -> dict[tuple[str, str], list[_TickPoint]]:
    from pathlib import Path

    paths: dict[tuple[str, str], list[_TickPoint]] = {k: [] for k in trade_keys}
    open_keys: set[tuple[str, str]] = set()
    active_symbol: dict[str, tuple[str, str]] = {}
    entry_prices: dict[tuple[str, str], float] = {}
    peak_pnl: dict[tuple[str, str], float] = {}

    ordered = sorted(
        _stream_events_csv(Path(events_path)),
        key=lambda r: int(r.get("message_index") or 0),
    )
    for row in ordered:
        sym = str(row.get("symbol") or "")
        if not sym:
            continue
        et = str(row.get("event_type") or "")
        ent = str(row.get("entry_time") or "")
        key = (sym, ent)
        price = _float(row.get("current_price") or row.get("exit_price") or row.get("entry_price"))
        event_ts = str(row.get("event_time") or row.get("exit_time") or ent)
        ts_epoch = _parse_dt(event_ts).timestamp()

        if et == "accepted" and key in trade_keys and key not in open_keys:
            entry_price = _float(row.get("entry_price")) or price
            if not entry_price or entry_price <= 0:
                continue
            entry_prices[key] = float(entry_price)
            open_keys.add(key)
            active_symbol[sym] = key
            peak_pnl[key] = 0.0
            paths[key].append(
                _TickPoint(ts_epoch=ts_epoch, pnl_pct=0.0, price=float(entry_price))
            )
            continue

        active_key = active_symbol.get(sym)
        if active_key is None or active_key not in open_keys:
            continue

        entry_price = entry_prices.get(active_key, 0.0)
        if entry_price <= 0 or not price or price <= 0:
            continue

        if et in ("candidate", "accepted", "observer_hold", "observer_take"):
            pnl = _pnl_pct(entry_price, float(price))
            peak_pnl[active_key] = max(peak_pnl.get(active_key, 0.0), pnl)
            paths[active_key].append(
                _TickPoint(ts_epoch=ts_epoch, pnl_pct=pnl, price=float(price))
            )
            continue

        if et == "observer_exit" and key == active_key:
            exit_price = _float(row.get("exit_price")) or float(price)
            pnl = _float(row.get("pnl_pct"))
            if pnl is None:
                pnl = _pnl_pct(entry_price, float(exit_price))
            paths[active_key].append(
                _TickPoint(ts_epoch=ts_epoch, pnl_pct=float(pnl), price=float(exit_price))
            )
            open_keys.discard(active_key)
            if active_symbol.get(sym) == active_key:
                del active_symbol[sym]

    return paths


def _analytical_counterfactual(
    trade: Mapping[str, Any], candidate_id: str
) -> tuple[float, str, bool]:
    """Fallback when tick path unavailable."""
    actual_pct = _float(trade.get("pnl_pct")) or 0.0
    peak = _float(trade.get("peak_mfe_pct")) or 0.0
    entry = _float(trade.get("entry_price")) or 0.0
    tier = str(trade.get("board_dynamic_trailing_tier") or "")

    if candidate_id == "mfe_0p3_profit_protect":
        if peak >= 0.3 and actual_pct < 0:
            return 0.0, candidate_id, True
    elif candidate_id == "mfe_0p5_profit_protect":
        if peak >= 0.5 and actual_pct < 0.1:
            return 0.1, candidate_id, True
    elif candidate_id == "board_low_tighter_trailing":
        if tier == "board_low" and peak >= BOARD_LOW_TIGHTER_ACTIVATE:
            cf = round(peak * BOARD_LOW_TIGHTER_GIVEBACK, 4)
            if actual_pct < cf:
                return cf, candidate_id, True
    elif candidate_id == "time_after_mfe_decay":
        if peak >= 0.3 and actual_pct < peak * MFE_DECAY_RETENTION:
            return round(max(actual_pct, peak * MFE_DECAY_RETENTION * 0.5), 4), candidate_id, True
    return actual_pct, str(trade.get("exit_reason_canonical") or ""), False


def simulate_candidate_on_ticks(
    ticks: Sequence[_TickPoint],
    trade: Mapping[str, Any],
    candidate_id: str,
) -> dict[str, Any]:
    entry = _float(trade.get("entry_price")) or 0.0
    actual_pct = _float(trade.get("pnl_pct")) or 0.0
    actual_yen = _float(trade.get("pnl_yen_100")) or 0.0
    actual_reason = str(trade.get("exit_reason_canonical") or "")

    if not ticks or entry <= 0:
        cf_pct, cf_reason, changed = _analytical_counterfactual(trade, candidate_id)
        cf_yen = _pnl_yen(entry, cf_pct) if entry > 0 else actual_yen
        return {
            "shadow_pnl_pct": cf_pct,
            "shadow_pnl_yen_100": cf_yen,
            "shadow_exit_reason": cf_reason,
            "shadow_delta_yen": round(cf_yen - actual_yen, 2),
            "shadow_applied": changed,
            "simulation_method": "analytical_fallback",
        }

    imb = _float(trade.get("entry_imbalance_percentile"))
    tier = str(
        trade.get("board_dynamic_trailing_tier") or board_tier_from_percentile(imb)
    )
    activate_prod, giveback_prod, _ = trailing_mfe_params(imb)

    peak = 0.0
    last_high_ts = ticks[0].ts_epoch
    mfe_03_reached = False
    mfe_05_reached = False
    trailing_activated = False
    entry_to_mfe_sec: Optional[float] = None
    peak_ts: Optional[float] = None

    for tick in ticks:
        pnl = float(tick.pnl_pct)
        ts = float(tick.ts_epoch)
        if pnl > peak:
            peak = pnl
            last_high_ts = ts
            peak_ts = ts
        if pnl >= 0.3 and entry_to_mfe_sec is None and ticks:
            entry_to_mfe_sec = round(ts - ticks[0].ts_epoch, 1)
        if pnl >= 0.3:
            mfe_03_reached = True
        if pnl >= 0.5:
            mfe_05_reached = True
        if peak >= activate_prod:
            trailing_activated = True

        if pnl <= -HARD_STOP_PCT:
            break

        if candidate_id == "mfe_0p3_profit_protect":
            if peak >= 0.3 and pnl <= 0.0:
                cf_yen = _pnl_yen(entry, 0.0)
                return _result(
                    trade,
                    cf_pct=0.0,
                    cf_yen=cf_yen,
                    cf_reason=candidate_id,
                    method="events_tick_path",
                    entry_to_mfe_sec=entry_to_mfe_sec,
                    mfe_to_stop_sec=_mfe_to_stop(peak_ts, ts),
                    trailing_activated=trailing_activated,
                )
        elif candidate_id == "mfe_0p5_profit_protect":
            if peak >= 0.5 and pnl <= 0.1:
                cf_yen = _pnl_yen(entry, 0.1)
                return _result(
                    trade,
                    cf_pct=0.1,
                    cf_yen=cf_yen,
                    cf_reason=candidate_id,
                    method="events_tick_path",
                    entry_to_mfe_sec=entry_to_mfe_sec,
                    mfe_to_stop_sec=_mfe_to_stop(peak_ts, ts),
                    trailing_activated=trailing_activated,
                )
        elif candidate_id == "board_low_tighter_trailing":
            if tier == "board_low":
                if peak >= BOARD_LOW_TIGHTER_ACTIVATE and pnl <= peak * BOARD_LOW_TIGHTER_GIVEBACK:
                    cf_yen = _pnl_yen(entry, pnl)
                    return _result(
                        trade,
                        cf_pct=pnl,
                        cf_yen=cf_yen,
                        cf_reason=candidate_id,
                        method="events_tick_path",
                        entry_to_mfe_sec=entry_to_mfe_sec,
                        mfe_to_stop_sec=_mfe_to_stop(peak_ts, ts),
                        trailing_activated=trailing_activated,
                    )
        elif candidate_id == "time_after_mfe_decay":
            if peak >= 0.3:
                minutes_since_high = (ts - last_high_ts) / 60.0
                if minutes_since_high >= MFE_DECAY_MINUTES and pnl < peak * MFE_DECAY_RETENTION:
                    cf_yen = _pnl_yen(entry, pnl)
                    return _result(
                        trade,
                        cf_pct=pnl,
                        cf_yen=cf_yen,
                        cf_reason=candidate_id,
                        method="events_tick_path",
                        entry_to_mfe_sec=entry_to_mfe_sec,
                        mfe_to_stop_sec=_mfe_to_stop(peak_ts, ts),
                        trailing_activated=trailing_activated,
                    )

    cf_pct, cf_reason, changed = _analytical_counterfactual(trade, candidate_id)
    if not changed:
        cf_pct, cf_reason = actual_pct, actual_reason
    cf_yen = _pnl_yen(entry, cf_pct)
    hold_end = ticks[-1].ts_epoch
    return _result(
        trade,
        cf_pct=cf_pct,
        cf_yen=cf_yen,
        cf_reason=cf_reason,
        method="events_tick_path_actual",
        entry_to_mfe_sec=entry_to_mfe_sec,
        mfe_to_stop_sec=_mfe_to_stop(peak_ts, hold_end),
        trailing_activated=trailing_activated,
        shadow_applied=changed,
    )


def _mfe_to_stop(peak_ts: Optional[float], stop_ts: float) -> Optional[float]:
    if peak_ts is None:
        return None
    return round(max(0.0, stop_ts - peak_ts), 1)


def _result(
    trade: Mapping[str, Any],
    *,
    cf_pct: float,
    cf_yen: float,
    cf_reason: str,
    method: str,
    entry_to_mfe_sec: Optional[float],
    mfe_to_stop_sec: Optional[float],
    trailing_activated: bool,
    shadow_applied: bool = True,
) -> dict[str, Any]:
    actual_yen = _float(trade.get("pnl_yen_100")) or 0.0
    return {
        "shadow_pnl_pct": round(cf_pct, 4),
        "shadow_pnl_yen_100": round(cf_yen, 2),
        "shadow_exit_reason": cf_reason,
        "shadow_delta_yen": round(cf_yen - actual_yen, 2),
        "shadow_applied": shadow_applied,
        "simulation_method": method,
        "entry_to_mfe_sec": entry_to_mfe_sec,
        "mfe_to_stop_sec": mfe_to_stop_sec,
        "trailing_activated_on_path": trailing_activated,
    }


def enrich_forensic_trade(trade: Mapping[str, Any], ticks: Sequence[_TickPoint]) -> dict[str, Any]:
    row = dict(trade)
    peak = _float(row.get("peak_mfe_pct")) or 0.0
    pnl = _float(row.get("pnl_pct")) or 0.0
    imb = _float(row.get("entry_imbalance_percentile"))
    activate, giveback, tier = trailing_mfe_params(imb)
    if not row.get("board_dynamic_trailing_tier"):
        row["board_dynamic_trailing_tier"] = tier
    row["board_dynamic_trailing_activate_pct"] = activate
    row["board_dynamic_trailing_giveback_frac"] = giveback

    mfe_left = round(peak - pnl, 4) if peak > 0 else None
    row["mfe_left_pct"] = mfe_left
    row["trailing_activation_missed"] = (
        peak >= activate and not _bool(row.get("trailing_mfe_activated"))
    )
    row["giveback_too_wide"] = (
        peak >= activate
        and pnl is not None
        and not trailing_mfe_exit_triggered(
            peak_pnl=peak,
            pnl=pnl,
            entry_imbalance_percentile=imb,
        )
        and pnl < peak * giveback
        and row.get("exit_reason_canonical") == "stop_hit"
    )
    row["overlap_exit"] = _bool(row.get("overlap_replaced_review")) or str(
        row.get("exit_reason_canonical") or ""
    ) == "overlap_replaced"

    entry_to_mfe = None
    mfe_to_stop = None
    if ticks:
        entry_ts = ticks[0].ts_epoch
        peak_ts = None
        peak_run = 0.0
        for t in ticks:
            if t.pnl_pct > peak_run:
                peak_run = t.pnl_pct
                peak_ts = t.ts_epoch
            if entry_to_mfe is None and t.pnl_pct >= HIGH_MFE_THRESHOLD:
                entry_to_mfe = round(t.ts_epoch - entry_ts, 1)
        if peak_ts is not None:
            mfe_to_stop = round(ticks[-1].ts_epoch - peak_ts, 1)
    row["entry_to_mfe_sec"] = entry_to_mfe
    row["mfe_to_stop_sec"] = mfe_to_stop
    row["tick_count"] = len(ticks)
    band_id, band_label = classify_mfe_band(peak)
    row["mfe_band"] = band_id
    row["mfe_band_label"] = band_label
    row["entry_time_bucket"] = entry_time_bucket(str(row.get("entry_time") or ""))
    return row


def load_session_high_mfe_stophit(
    session_meta: Mapping[str, Any], *, reports_dir: Any
) -> dict[str, Any]:
    from pathlib import Path

    from research.phase365_production_stack_validation import load_session_production_stack_trades
    from small_paper.limit_up_proximity_entry_guard_shadow import (
        _infer_session_kind,
        _load_session_summary,
        _load_universe,
        _universe_path_for_session,
    )

    base = load_session_production_stack_trades(session_meta, reports_dir=Path(reports_dir))
    if base.get("error"):
        return {**base, "production_trades": [], "high_mfe_stops": [], "error": base.get("error")}

    sess_dir = Path(str(session_meta["session_dir"]))
    events_path = sess_dir / "small_paper_events.csv"
    summary = _load_session_summary(sess_dir)
    session_kind = str(session_meta.get("session_kind") or _infer_session_kind(sess_dir, summary))
    day = str(session_meta.get("day_key") or session_meta.get("day") or sess_dir.parent.name)
    universe = _load_universe(
        _universe_path_for_session(day, session_kind, summary, Path(reports_dir))
    )

    accepted: dict[tuple[str, str], dict[str, str]] = {}
    if events_path.is_file():
        for row in _stream_events_csv(events_path):
            if row.get("event_type") == "accepted":
                accepted[(row.get("symbol", ""), row.get("entry_time", ""))] = row

    production = production_kept_trades(base)
    trade_keys = {(t.get("symbol", ""), t.get("entry_time", "")) for t in production}
    tick_paths = (
        _build_tick_paths(events_path, trade_keys) if events_path.is_file() else {}
    )

    enriched: list[dict[str, Any]] = []
    for t in production:
        key = (t.get("symbol", ""), t.get("entry_time", ""))
        acc = accepted.get(key, {})
        ex = {**t, **acc}
        trade = enrich_trade_features_for_review(acc, ex, universe)
        trade.update(
            {
                "session_id": session_meta.get("session_id"),
                "day_key": day,
                "session_kind": session_kind,
                "universe_group": t.get("universe_group")
                or trade.get("universe_group")
                or "other",
                "entry_price": _float(t.get("entry_price") or ex.get("entry_price")),
                "exit_price": _float(t.get("exit_price") or ex.get("exit_price")),
                "pnl_pct": _float(t.get("pnl_pct") or ex.get("pnl_pct")),
                "pnl_yen_100": _float(t.get("pnl_yen_100")),
                "peak_mfe_pct": _float(t.get("peak_mfe_pct") or ex.get("peak_mfe_pct")),
                "exit_reason_canonical": t.get("exit_reason_canonical"),
                "trailing_mfe_activated": _bool(
                    t.get("trailing_mfe_activated") or ex.get("trailing_mfe_activated")
                ),
                "overlap_replaced_review": _bool(
                    t.get("overlap_replaced_review") or ex.get("overlap_replaced_review")
                ),
                "board_dynamic_trailing_tier": ex.get("board_dynamic_trailing_tier")
                or t.get("board_dynamic_trailing_tier"),
                "entry_imbalance_percentile": _float(
                    ex.get("entry_imbalance_percentile") or acc.get("entry_imbalance_percentile")
                ),
            }
        )
        ticks = tick_paths.get(key, [])
        row = enrich_forensic_trade(trade, ticks)
        row["candidate_shadow"] = {
            cid: simulate_candidate_on_ticks(ticks, row, cid) for cid in EXIT_CANDIDATES
        }
        enriched.append(row)

    high_mfe = [t for t in enriched if is_high_mfe_stop(t)]
    return {
        **base,
        "session_meta": dict(session_meta),
        "production_trades": enriched,
        "high_mfe_stops": high_mfe,
        "production_trade_count": len(enriched),
        "high_mfe_stop_count": len(high_mfe),
        "error": "",
    }
