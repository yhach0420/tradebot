"""
Phase356: Evaluate EXIT rebaseline candidates on live session events.

ENTRY population: exclude trades Phase355 Dynamic40 pullback guard would block.
Actual EXIT baseline: production observer_exit (board dynamic trailing).
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional
from zoneinfo import ZoneInfo

from small_paper.exit_candidate_shadow import _pnl_pct
from small_paper.phase356_exit_rebaseline_pack import Phase356ExitRebaselinePack
from small_paper.pullback_misread_entry_guard_shadow import (
    enrich_trade_features_for_review,
    would_block_pullback_dynamic40_shadow,
)
from small_paper.pullback_misread_entry_guard_shadow import _stream_events_csv
from small_paper.realtime_board_exit_shadow import make_position_id

JST = ZoneInfo("Asia/Tokyo")


def _default_reports_dir() -> Path:
    here = Path(__file__).resolve()
    native = here.parents[2]
    return native / "results" / "reports"


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _parse_dt(raw: str) -> datetime:
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return dt.replace(tzinfo=JST)
        return dt.astimezone(JST)
    except (TypeError, ValueError):
        return datetime.now(JST)


def _entry_shadow_from_rows(acc: Mapping[str, Any], ex: Mapping[str, Any]) -> dict[str, Any]:
    return {
        k: v
        for k, v in {
            "entry_rise_5min_pct": acc.get("entry_rise_5min_pct") or ex.get("entry_rise_5min_pct"),
            "entry_vwap_dev_pct": acc.get("entry_vwap_dev_pct") or ex.get("entry_vwap_dev_pct"),
            "entry_imbalance_percentile": acc.get("entry_imbalance_percentile")
            or ex.get("entry_imbalance_percentile"),
            "universe_slot": acc.get("universe_slot") or ex.get("universe_slot"),
            "universe_bucket": acc.get("universe_bucket") or ex.get("universe_bucket"),
            "source_bucket": acc.get("source_bucket") or ex.get("source_bucket"),
        }.items()
        if v not in (None, "")
    }


def _payload_from_event(row: Mapping[str, Any], *, price: float, event_ts: str = "") -> dict[str, Any]:
    payload = dict(row)
    payload["CurrentPrice"] = price
    ts = event_ts or str(row.get("event_time") or row.get("entry_time") or "")
    if ts:
        payload["RecordedAt"] = ts
        payload["CurrentPriceTime"] = ts
    for src, dst in (
        ("entry_order_book_imbalance", "BidAskImbalance"),
        ("order_book_imbalance", "BidAskImbalance"),
        ("vwap", "VWAP"),
        ("current_vwap", "VWAP"),
    ):
        if row.get(src) not in (None, "") and dst not in payload:
            payload[dst] = row.get(src)
    return payload


def evaluate_live_session_phase356(session_meta: Mapping[str, Any]) -> dict[str, Any]:
    from small_paper.limit_up_proximity_entry_guard_shadow import (
        _infer_session_kind,
        _load_session_summary,
        _load_universe,
        _session_source_label,
        _universe_path_for_session,
    )

    sess_dir = Path(str(session_meta["session_dir"]))
    events_path = sess_dir / "small_paper_events.csv"
    if not events_path.is_file():
        return {"error": "missing_events_csv", "trade_rows": [], "session_meta": dict(session_meta)}

    summary = _load_session_summary(sess_dir)
    session_kind = str(session_meta.get("session_kind") or _infer_session_kind(sess_dir, summary))
    day = str(session_meta.get("day") or sess_dir.parent.name)
    reports_dir = Path(str(session_meta.get("reports_dir") or _default_reports_dir()))
    universe = _load_universe(
        _universe_path_for_session(day, session_kind, summary, reports_dir)
    )

    accepted: dict[tuple[str, str], dict[str, str]] = {}
    for row in _stream_events_csv(events_path):
        if row.get("event_type") == "accepted":
            accepted[(row.get("symbol", ""), row.get("entry_time", ""))] = row

    kept: dict[tuple[str, str], dict[str, Any]] = {}
    skipped_pullback = 0
    for row in _stream_events_csv(events_path):
        if row.get("event_type") != "observer_exit" or row.get("pnl_pct") in (None, ""):
            continue
        key = (row.get("symbol", ""), row.get("entry_time", ""))
        acc = accepted.get(key, {})
        trade = enrich_trade_features_for_review(acc, row, universe)
        entry_shadow = _entry_shadow_from_rows(acc, row)
        trade["entry_shadow"] = entry_shadow
        block_fields = {
            **entry_shadow,
            "universe_slot": trade.get("universe_slot") or entry_shadow.get("universe_slot"),
            "source_bucket": trade.get("source_bucket") or entry_shadow.get("source_bucket"),
        }
        if would_block_pullback_dynamic40_shadow(block_fields):
            skipped_pullback += 1
            continue
        kept[key] = trade

    if not kept:
        return {
            "session_meta": dict(session_meta),
            "session_kind": session_kind,
            "trade_rows": [],
            "positions_evaluated": 0,
            "skipped_pullback_dynamic40": skipped_pullback,
            "error": "no_kept_observer_exit_trades",
        }

    pack = Phase356ExitRebaselinePack()
    open_keys: set[tuple[str, str]] = set()
    active_symbol: dict[str, tuple[str, str]] = {}
    position_ids: dict[tuple[str, str], str] = {}
    peak_mfe: dict[tuple[str, str], float] = {}

    ordered = sorted(
        _stream_events_csv(events_path),
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

        if et == "accepted" and key in kept and key not in open_keys:
            trade = kept[key]
            entry_price = float(trade.get("entry_price") or 0.0)
            if entry_price <= 0:
                continue
            ent_dt = _parse_dt(ent)
            position_id = make_position_id(sym, ent_dt)
            position_ids[key] = position_id
            entry_shadow = {
                **(trade.get("entry_shadow") or {}),
                "universe_slot": trade.get("universe_slot") or "",
                "universe_bucket": trade.get("source_bucket") or trade.get("universe_bucket") or "",
                "source_bucket": trade.get("source_bucket") or "",
            }
            payload = _payload_from_event(row, price=entry_price, event_ts=ent)
            pack.register_position(
                position_id=position_id,
                symbol=sym,
                entry_time=ent_dt,
                entry_price=entry_price,
                payload=payload,
                entry_shadow=entry_shadow,
            )
            open_keys.add(key)
            active_symbol[sym] = key
            peak_mfe[key] = 0.0
            continue

        active_key = active_symbol.get(sym)
        if active_key is None or active_key not in open_keys:
            continue

        trade = kept[active_key]
        entry_price = float(trade.get("entry_price") or 0.0)
        if entry_price <= 0:
            continue
        trade_ent = str(trade.get("entry_time") or active_key[1])
        ent_dt = _parse_dt(trade_ent)
        position_id = position_ids[active_key]

        if et in ("candidate", "accepted", "observer_hold", "observer_take") and price and price > 0:
            pnl = _pnl_pct(entry_price, float(price))
            peak_mfe[active_key] = max(peak_mfe.get(active_key, 0.0), pnl)
            tick_payload = _payload_from_event(row, price=float(price), event_ts=event_ts)
            pack.record_holding_tick(
                symbol=sym,
                position_id=position_id,
                entry_time=ent_dt,
                payload=tick_payload,
                current_price=float(price),
                entry_price=entry_price,
                mfe_pct=peak_mfe[active_key],
                entry_shadow=trade.get("entry_shadow") or {},
            )
            continue

        if et == "observer_exit" and key == active_key:
            exit_price = _float(row.get("exit_price")) or float(price or 0.0)
            exit_dt = _parse_dt(str(row.get("exit_time") or event_ts))
            reason = str(
                row.get("structural_exit_reason") or row.get("exit_reason") or trade.get("exit_reason") or ""
            )
            pack.finalize_position(
                position_id=position_id,
                actual_exit_reason=reason,
                actual_exit_time=exit_dt,
                actual_exit_price=float(exit_price),
                entry_price=entry_price,
            )
            open_keys.discard(active_key)
            if active_symbol.get(sym) == active_key:
                del active_symbol[sym]

    trade_rows = pack.export_trade_rows()
    return {
        "session_meta": dict(session_meta),
        "session_kind": session_kind,
        "session_source": str(session_meta.get("session_source") or _session_source_label(sess_dir)),
        "trade_rows": trade_rows,
        "positions_evaluated": len(kept),
        "skipped_pullback_dynamic40": skipped_pullback,
        "error": "",
    }


def discover_live_sessions_for_phase356(
    small_paper_root: Path,
    *,
    min_day: str,
) -> list[dict[str, Any]]:
    import json

    from small_paper.limit_up_proximity_entry_guard_shadow import (
        _infer_session_kind,
        _load_session_summary,
        _session_source_label,
    )

    sessions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for summary_path in sorted(small_paper_root.rglob("small_paper_summary.json")):
        sess_dir = summary_path.parent
        key = str(sess_dir.resolve())
        if key in seen:
            continue
        seen.add(key)
        day = sess_dir.parent.name
        if not day.isdigit() or len(day) != 8 or day < min_day:
            continue
        if not (sess_dir / "small_paper_events.csv").is_file():
            continue
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(summary.get("source") or "") == "push-replay":
            continue
        kind = _infer_session_kind(sess_dir, summary)
        sessions.append(
            {
                "session_id": f"{day}/{sess_dir.name}",
                "day_key": day,
                "day": day,
                "session_dir": str(sess_dir),
                "session_kind": kind,
                "session_source": _session_source_label(sess_dir),
            }
        )
    sessions.sort(key=lambda s: s["session_id"])
    return sessions
