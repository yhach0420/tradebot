"""Canonical E1_X5 offline replay — window-based (VALID_COMPLETE_WINDOW).

Day labels COMPLETE/PARTIAL/CORRUPTED stay strict. Research uses continuous
ingress windows that do not cross gaps/session boundaries.
"""
from __future__ import annotations

import json
import math
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

LEGACY_ENRICHED_REFERENCE = {
    "label": "LEGACY_ENRICHED_REFERENCE",
    "path": "historical enriched + simulate_x5",
    "TRAIN": {"trades": 69, "pnl": 546557.29},
    "VALIDATION": {"trades": 58, "pnl": 72841.0},
    "HOLD": {"trades": 16, "pnl": 79969.4},
    "TOTAL": {"trades": 143, "pnl": 699367.69, "pf": 11.787},
    "note": "Not a force-match target for decision_core BASE.",
}

PARITY_STRESS_REFERENCE = {
    "label": "PARITY_STRESS_REFERENCE",
    "day": "20260727_PM",
    "trades": 70,
    "pnl": 45023.825,
    "ledger_sha256_v1_frozen_expected": "b5837b4871273aad64445e76c251a3bc72ff6aa98c41107c04dffaefe04ef2d4",
    "ledger_sha256_v2_actual_observed": "a3007cbc11ec0630645b2e89f559ae42aeb342bf840608320c427ed918b84649",
    "note": "Do not copy actual into expected. Compare only within the same hash schema version.",
}

EXCLUDE_STRATEGY_PNL_DAYS = {"20260728"}
GAP_THRESHOLD_SEC = 120.0


def norm_sym(s: str) -> str:
    s = str(s or "").strip()
    return s if not s or s.endswith(".T") else f"{s}.T"


def parse_ts(v: Any) -> Optional[datetime]:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=JST)
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return dt.replace(tzinfo=JST) if dt.tzinfo is None else dt.astimezone(JST)
    except Exception:
        return None


@dataclass
class ValidWindow:
    day: str
    window_id: str
    session_id: str
    start_key: str
    end_key: str
    start_time: str
    end_time: str
    event_count: int
    day_label: str
    classification: str = "VALID_COMPLETE_WINDOW"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExcludedWindow:
    day: str
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_universe(native_root: Path, day: str) -> set[str]:
    import pandas as pd

    p = Path(native_root) / "results" / "reports" / f"universe_core10_dynamic40_price_risk_am_{day}.csv"
    if not p.is_file():
        return set()
    df = pd.read_csv(p)
    col = "symbol" if "symbol" in df.columns else df.columns[0]
    return {norm_sym(x) for x in df[col].tolist()}


def _slim_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep only fields required by FeatureEngine / board / score path."""
    if not isinstance(payload, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in payload.items():
        ks = str(k)
        if ks in (
            "Symbol",
            "CurrentPrice",
            "CurrentPriceTime",
            "TradingVolume",
            "TradingVolumeTime",
            "BidTime",
            "AskTime",
            "sequence",
        ) or ks.startswith("Buy") or ks.startswith("Sell"):
            out[ks] = v
    return out


def normalize_day(native_root: Path, day: str, *, cache_dir: Optional[Path] = None, use_cache: bool = True):
    import gzip
    import pickle

    from small_paper.replay_session_normalizer import NormalizedEvent, normalize_day_capture

    day_dir = Path(native_root) / "data" / "market_capture" / day
    cache_dir = cache_dir or (Path(native_root) / "results" / "research" / "_e1_x5_norm_cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    meta_p = cache_dir / f"{day}_normalize_report.json"
    pkl_p = cache_dir / f"{day}_events_slim_v3.pkl.gz"
    cache_ver = "ingress_v4_payload_fix_slim"
    if use_cache and pkl_p.is_file() and meta_p.is_file():
        try:
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
            if meta.get("_cache_ver") == cache_ver and int(meta.get("normalized_rows") or 0) > 0:
                with gzip.open(pkl_p, "rb") as fh:
                    events = pickle.load(fh)
                from small_paper.replay_session_normalizer import NormalizeReport

                report = NormalizeReport(**{k: v for k, v in meta.items() if k != "_cache_ver"})
                return events, report
        except Exception:
            pass
    events, report = normalize_day_capture(day_dir, day=day, gap_threshold_sec=GAP_THRESHOLD_SEC)
    # Slim in place to cut memory before cache / multi-window replay
    slimmed: list[Any] = []
    for e in events:
        slimmed.append(
            NormalizedEvent(
                session_id=e.session_id,
                sequence=e.sequence,
                event_time=e.event_time,
                received_at=e.received_at,
                symbol=e.symbol,
                payload=_slim_payload(e.payload if isinstance(e.payload, dict) else {}),
                source_part=e.source_part,
                unique_key=e.unique_key,
                ts=e.ts,
            )
        )
    del events
    events = slimmed
    meta = report.to_dict()
    meta["_cache_ver"] = cache_ver
    meta_p.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (cache_dir / f"{day}_gap_map.json").write_text(
        json.dumps(report.gaps, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    try:
        with gzip.open(pkl_p, "wb", compresslevel=3) as fh:
            pickle.dump(events, fh, protocol=pickle.HIGHEST_PROTOCOL)
    except MemoryError:
        # Cache optional — replay can re-normalize.
        if pkl_p.is_file():
            pkl_p.unlink()
    return events, report


def day_label_strict(native_root: Path, day: str, report: Any) -> str:
    from small_paper.capture_completeness_gate import evaluate_capture_completeness

    if day in EXCLUDE_STRATEGY_PNL_DAYS:
        return "EXCLUDED_LAG_RESYNC"
    gate = evaluate_capture_completeness(
        trading_date=day,
        first_event_at=report.first_event_at,
        last_event_at=report.last_event_at,
        raw_row_count=report.normalized_rows,
        seal_row_count=report.normalized_rows,
        duplicate_key_count=report.duplicate_keys,
        registration_symbol_count=50,
        largest_gap_sec=max((float(g.get("gap_sec") or 0) for g in report.gaps), default=0.0),
    )
    return str(gate.get("status") or gate.get("label") or "PARTIAL_CAPTURE")


def build_valid_windows(
    day: str,
    events: Sequence[Any],
    report: Any,
    *,
    day_label: str,
) -> tuple[list[ValidWindow], list[ExcludedWindow], list[list[Any]]]:
    """Split ingress-ordered events into continuous VALID_COMPLETE_WINDOW segments."""
    excluded: list[ExcludedWindow] = []
    if day in EXCLUDE_STRATEGY_PNL_DAYS:
        excluded.append(ExcludedWindow(day=day, reason="lag_resync_excluded_from_strategy_pnl"))
        return [], excluded, []

    # Split indices: session change or TIME_GAP from report
    split_after: set[int] = set()
    key_to_idx = {e.unique_key: i for i, e in enumerate(events)}
    for g in report.gaps:
        kind = str(g.get("kind") or "TIME_GAP")
        fk = str(g.get("from_key") or "")
        if fk in key_to_idx:
            split_after.add(key_to_idx[fk])
        if kind == "SESSION_BOUNDARY" and fk in key_to_idx:
            split_after.add(key_to_idx[fk])

    segments: list[list[Any]] = []
    cur: list[Any] = []
    for i, e in enumerate(events):
        cur.append(e)
        if i in split_after:
            segments.append(cur)
            cur = []
    if cur:
        segments.append(cur)

    windows: list[ValidWindow] = []
    event_segments: list[list[Any]] = []
    for si, seg in enumerate(segments):
        if len(seg) < 2:
            excluded.append(
                ExcludedWindow(day=day, reason="segment_too_short", detail={"n": len(seg), "idx": si})
            )
            continue
        # Drop empty-board junk
        usable = []
        for e in seg:
            op = e.payload if isinstance(e.payload, dict) else {}
            if isinstance(op.get("Buy1"), dict) and isinstance(op.get("Sell1"), dict):
                usable.append(e)
        if len(usable) < 2:
            excluded.append(ExcludedWindow(day=day, reason="no_board_payload", detail={"idx": si}))
            continue
        wid = f"{day}:W{si:03d}:{usable[0].session_id[:12]}"
        windows.append(
            ValidWindow(
                day=day,
                window_id=wid,
                session_id=usable[0].session_id,
                start_key=usable[0].unique_key,
                end_key=usable[-1].unique_key,
                start_time=usable[0].event_time,
                end_time=usable[-1].event_time,
                event_count=len(usable),
                day_label=day_label,
            )
        )
        event_segments.append(usable)
    return windows, excluded, event_segments


def make_warmup_gated_session(*, variant: str, state_rearm: bool, provider: Any) -> Any:
    from small_paper.e1_x5_g1_confirmation_guard import E1X5GuardSession, GuardVariant

    gv = GuardVariant.BASE if variant == "BASE" else GuardVariant[variant]

    class WarmupGatedGuardSession(E1X5GuardSession):
        def try_entry(self, **kwargs: Any) -> Optional[str]:  # type: ignore[override]
            sym = kwargs.get("symbol")
            ts = kwargs.get("ts")
            if not provider.symbol_feature_warmed(str(sym or ""), ts):
                self._log_candidate(
                    ts,
                    sym,
                    kwargs.get("score"),
                    kwargs.get("spread_bps"),
                    kwargs.get("bid"),
                    kwargs.get("ask"),
                    kwargs.get("mid"),
                    False,
                    "INVALID_LOOKBACK",
                )
                return "INVALID_LOOKBACK"
            return super().try_entry(**kwargs)

    return WarmupGatedGuardSession(enabled=True, variant=gv, state_rearm=bool(state_rearm))


def canonical_trade_row(exit_row: Mapping[str, Any], *, window_id: str = "", day: str = "") -> dict[str, Any]:
    et = exit_row.get("entry_time")
    xt = exit_row.get("exit_time")
    et_s = et.isoformat() if hasattr(et, "isoformat") else str(et or "")
    xt_s = xt.isoformat() if hasattr(xt, "isoformat") else str(xt or "")
    hold = float(exit_row.get("holding_sec") or 0)
    if hasattr(et, "timestamp") and hasattr(xt, "timestamp"):
        hold = (xt - et).total_seconds()
    return {
        "day": day or str(exit_row.get("day") or ""),
        "window_id": window_id,
        "symbol": str(exit_row.get("symbol") or ""),
        "entry_time": et_s,
        "exit_time": xt_s,
        "entry_ask": round(float(exit_row.get("entry_ask") or 0), 6),
        "exit_bid": round(float(exit_row.get("exit_bid") or 0), 6),
        "exit_reason": str(exit_row.get("exit_reason") or ""),
        "net_pnl_yen_100": round(float(exit_row.get("net_pnl_yen_100") or 0), 6),
        "holding_sec": round(hold, 6),
        "score": round(float(exit_row.get("score") or 0), 12),
        "market_entry_time": et_s,
        "market_exit_time": xt_s,
    }


def trade_ledger_hash(rows: Sequence[Mapping[str, Any]]) -> str:
    from small_paper.e1_x5_artifact_sot import canonical_ledger_hash

    return canonical_ledger_hash(rows, version="v1")


def _adopt_trade(
    exit_row: Mapping[str, Any],
    *,
    window_events: Sequence[Any],
    gap_intervals: Sequence[tuple[Any, Any]],
    lookback_sec: float,
    window_id: str,
    day: str,
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    from small_paper.capture_window_validator import (
        DATA_END_INCOMPLETE,
        VALID_COMPLETE_WINDOW,
        validate_trade_window,
    )

    et = exit_row.get("entry_time")
    xt = exit_row.get("exit_time")
    reason = str(exit_row.get("exit_reason") or "")
    if reason.upper() in ("DATA_END", "DATAEND", "WINDOW_END", "WINDOW_FORCE_CLOSE"):
        return None, f"excluded:{reason or 'WINDOW_END'}"
    et_dt = parse_ts(et)
    xt_dt = parse_ts(xt)
    if et_dt is None or xt_dt is None:
        return None, "excluded:missing_time"
    if xt_dt < et_dt:
        return None, "excluded:CORRUPTED_ORDER"
    lb = et_dt.timestamp() - float(lookback_sec)
    lookback_start = datetime.fromtimestamp(lb, tz=JST)
    # Window bounds use processed/received time when available (matches decision clock).
    def _proc_ts(e: Any) -> Optional[datetime]:
        return parse_ts(getattr(e, "received_at", None)) or parse_ts(getattr(e, "event_time", None))

    w0 = _proc_ts(window_events[0])
    if w0 is not None and lookback_start < w0:
        return None, "excluded:INVALID_LOOKBACK"
    event_times = [(_proc_ts(e) or parse_ts(e.event_time)) for e in window_events]
    v = validate_trade_window(
        lookback_start=lookback_start,
        entry_time=et,
        exit_time=xt,
        event_times=event_times,
        entry_ask=exit_row.get("entry_ask"),
        exit_bid=exit_row.get("exit_bid"),
        gap_intervals=gap_intervals,
        exit_reason=reason,
        max_internal_gap_sec=GAP_THRESHOLD_SEC,
        require_feature_history=True,
    )
    if not v.window_valid or v.classification != VALID_COMPLETE_WINDOW:
        return None, f"excluded:{v.classification}:{v.invalid_reason}"
    row = canonical_trade_row(exit_row, window_id=window_id, day=day)
    if row["holding_sec"] < 0:
        return None, "excluded:CORRUPTED_ORDER_negative_holding"
    return row, None


def replay_window(
    *,
    day: str,
    window: ValidWindow,
    events: Sequence[Any],
    gap_intervals: Sequence[tuple[Any, Any]],
    variant: str = "BASE",
    state_rearm: bool = False,
    universe: Optional[set[str]] = None,
) -> dict[str, Any]:
    from small_paper.e1_x5_dmid_score_provider import DMidD4H6ScoreProvider
    from small_paper.e1_x5_g1_guard_process import process_e1_x5_guard_event

    provider = DMidD4H6ScoreProvider.maybe_create()
    if provider is None or not provider.ready:
        raise RuntimeError("DMidD4H6ScoreProvider unavailable")
    lookback_sec = provider.required_feature_lookback_sec()
    session = make_warmup_gated_session(variant=variant, state_rearm=state_rearm, provider=provider)

    uni = universe
    n = 0
    for e in events:
        sym = norm_sym(e.symbol)
        if uni and sym not in uni:
            continue
        payload = dict(e.payload or {})
        payload.setdefault("Symbol", sym.replace(".T", ""))
        recv = parse_ts(e.received_at) or e.ts
        # Separate market CPT (audit) from processed decision time (ingress/received).
        if payload.get("CurrentPriceTime"):
            payload["_market_CurrentPriceTime"] = payload.get("CurrentPriceTime")
        payload["CurrentPriceTime"] = recv.isoformat()
        process_e1_x5_guard_event(
            provider=provider,
            session=session,
            symbol=sym,
            payload=payload,
            day=day,
            event_sequence=e.sequence,
            event_id=e.unique_key,
            decision_time=recv,
        )
        n += 1

    # Do NOT force-close opens at window end — exclude them.
    orphan_open = []
    for sym, pos in list(getattr(session, "positions", {}).items()):
        orphan_open.append(
            {
                "symbol": sym,
                "entry_time": getattr(pos, "entry_time", None),
                "reason": "WINDOW_END_OPEN_EXCLUDED",
                "window_id": window.window_id,
            }
        )
    # Clear opens without recording PnL
    if hasattr(session, "positions"):
        session.positions.clear()
    if hasattr(session, "cancel_all_pending"):
        session.cancel_all_pending("WINDOW_END")

    adopted = []
    excluded_trades = []
    for x in list(getattr(session, "exits", []) or []):
        row, why = _adopt_trade(
            x,
            window_events=events,
            gap_intervals=gap_intervals,
            lookback_sec=lookback_sec,
            window_id=window.window_id,
            day=day,
        )
        if row is None:
            excluded_trades.append({"raw": canonical_trade_row(x, window_id=window.window_id, day=day), "reason": why})
        else:
            adopted.append(row)

    from dataclasses import asdict

    from small_paper.e1_x5_artifact_sot import canonical_ledger_hash

    pending_logs = []
    for row in list(getattr(session, "pending_logs", []) or []):
        pending_logs.append(asdict(row) if hasattr(row, "__dataclass_fields__") else dict(row))

    cand_n = len(getattr(session, "candidate_logs", []) or [])
    wiring = {
        "variant_id": f"{variant}{'+STATE_REARM' if state_rearm else ''}",
        "config_fingerprint": str(getattr(session, "config_hash", lambda: "")()),
        "candidate": int(cand_n),
        "armed": int(getattr(session, "arm_count", 0) or 0),
        "confirmed": int(getattr(session, "confirm_count", 0) or 0),
        "cancelled_by_reason": dict(getattr(session, "cancel_reasons", {}) or {}),
        "rearm_transition": int(getattr(session, "rearm_transition_count", 0) or 0),
        "accepted": len(adopted),
        "blocked_by_cap": int(getattr(session, "cap_blocked", 0) or 0),
        "blocked_by_same_symbol": int(getattr(session, "same_symbol_blocked", 0) or 0),
        "trade_ledger_hash": canonical_ledger_hash(adopted),
        "state_transition_ledger_hash": canonical_ledger_hash(
            [
                {
                    "symbol": p.get("symbol"),
                    "entry_time": p.get("arm_time"),
                    "exit_time": p.get("confirmation_time") or p.get("arm_time"),
                    "entry_ask": p.get("arm_ask") or 0,
                    "exit_bid": p.get("confirmation_bid") or p.get("arm_bid") or 0,
                    "exit_reason": f"{p.get('action')}:{p.get('reason')}",
                    "net_pnl_yen_100": 0.0,
                    "holding_sec": 0.0,
                    "score": 0.0,
                }
                for p in pending_logs
            ]
        ),
    }

    return {
        "day": day,
        "window": window.to_dict(),
        "variant": variant,
        "state_rearm": bool(state_rearm),
        "events_fed": n,
        "lookback_sec": lookback_sec,
        "trades": adopted,
        "excluded_trades": excluded_trades,
        "orphan_open": orphan_open,
        "completed_trades": len(adopted),
        "realized_pnl_yen_100": float(sum(float(t["net_pnl_yen_100"]) for t in adopted)),
        "ledger_sha256": wiring["trade_ledger_hash"],
        "arm_count": wiring["armed"],
        "confirm_count": wiring["confirmed"],
        "cancel_count": int(getattr(session, "cancel_count", 0) or 0),
        "cap_blocked": wiring["blocked_by_cap"],
        "same_symbol_blocked": wiring["blocked_by_same_symbol"],
        "exit_reasons": _count_by(adopted, "exit_reason"),
        "wiring": wiring,
        "state_transitions": pending_logs,
    }


def replay_day_windows(
    native_root: Path,
    day: str,
    *,
    variant: str = "BASE",
    state_rearm: bool = False,
) -> dict[str, Any]:
    events, report = normalize_day(native_root, day)
    label = day_label_strict(native_root, day, report)
    windows, excluded_w, segs = build_valid_windows(day, events, report, day_label=label)
    uni = load_universe(native_root, day)
    gap_intervals = [(g.get("from"), g.get("to")) for g in report.gaps]
    window_results = []
    all_trades = []
    all_excluded = []
    all_orphans = []
    for w, seg in zip(windows, segs):
        r = replay_window(
            day=day,
            window=w,
            events=seg,
            gap_intervals=gap_intervals,
            variant=variant,
            state_rearm=state_rearm,
            universe=uni,
        )
        window_results.append(r)
        all_trades.extend(r["trades"])
        all_excluded.extend(r["excluded_trades"])
        all_orphans.extend(r["orphan_open"])
    return {
        "day": day,
        "day_label": label,
        "normalized_rows": report.normalized_rows,
        "sessions": list(report.sessions),
        "gaps": report.gaps,
        "windows": [w.to_dict() for w in windows],
        "excluded_windows": [e.to_dict() for e in excluded_w],
        "window_results": window_results,
        "trades": all_trades,
        "excluded_trades": all_excluded,
        "orphan_open": all_orphans,
        "completed_trades": len(all_trades),
        "realized_pnl_yen_100": float(sum(float(t["net_pnl_yen_100"]) for t in all_trades)),
        "ledger_sha256": trade_ledger_hash(all_trades),
        "variant": variant,
        "state_rearm": bool(state_rearm),
        "usage_label": "PARTIAL_CAPTURE / VALID_COMPLETE_WINDOW_USED"
        if label != "COMPLETE_CAPTURE"
        else "COMPLETE_CAPTURE / VALID_COMPLETE_WINDOW_USED",
    }


def summarize_trades(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    pnls = [float(t["net_pnl_yen_100"]) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    draws = [p for p in pnls if p == 0]
    holds = [float(t.get("holding_sec") or 0) for t in trades]
    stops = [t for t in trades if str(t.get("exit_reason") or "").upper().startswith("STOP")]
    stop_pnl = sum(float(t["net_pnl_yen_100"]) for t in stops)
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    by_sym: dict[str, float] = {}
    for t in trades:
        by_sym[str(t["symbol"])] = by_sym.get(str(t["symbol"]), 0.0) + float(t["net_pnl_yen_100"])
    top_trade = max(pnls) if pnls else 0.0
    top_sym = max(by_sym.values()) if by_sym else 0.0
    total = float(sum(pnls)) if pnls else 0.0
    pf = (sum(wins) / abs(sum(losses))) if losses else (float("inf") if wins else None)
    return {
        "completed_trades": len(trades),
        "realized_pnl_yen_100": total,
        "profit_factor": pf,
        "wins": len(wins),
        "losses": len(losses),
        "draws": len(draws),
        "win_rate": (len(wins) / len(trades)) if trades else None,
        "stop_n": len(stops),
        "stop_rate": (len(stops) / len(trades)) if trades else None,
        "stop_pnl": stop_pnl,
        "avg_pnl": (total / len(trades)) if trades else None,
        "median_pnl": float(statistics.median(pnls)) if pnls else None,
        "avg_holding_sec": (sum(holds) / len(holds)) if holds else None,
        "median_holding_sec": float(statistics.median(holds)) if holds else None,
        "max_dd_yen": max_dd,
        "negative_holding_n": sum(1 for h in holds if h < 0),
        "pnl_ex_top1_trade": total - top_trade if pnls else 0.0,
        "pnl_ex_top1_symbol": total - top_sym if by_sym else 0.0,
        "exit_reasons": _count_by(trades, "exit_reason"),
        "by_symbol": {k: {"pnl": v, "n": sum(1 for t in trades if t["symbol"] == k)} for k, v in by_sym.items()},
    }


def _count_by(rows: Sequence[Mapping[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        k = str(r.get(key) or "")
        out[k] = out.get(k, 0) + 1
    return out


def am_pm_split(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    am, pm = [], []
    for t in trades:
        et = parse_ts(t.get("entry_time"))
        if et is None:
            continue
        if et.hour < 12:
            am.append(t)
        else:
            pm.append(t)
    return {"AM": summarize_trades(am), "PM": summarize_trades(pm)}


def timeband_split(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    bands = {
        "09-10": [],
        "10-11": [],
        "11-12": [],
        "12-13": [],
        "13-14": [],
        "14-15": [],
        "other": [],
    }
    for t in trades:
        et = parse_ts(t.get("entry_time"))
        if et is None:
            bands["other"].append(t)
            continue
        h = et.hour
        key = f"{h:02d}-{h+1:02d}" if 9 <= h <= 14 else "other"
        if key not in bands:
            key = "other"
        bands[key].append(t)
    return {k: summarize_trades(v) for k, v in bands.items()}
