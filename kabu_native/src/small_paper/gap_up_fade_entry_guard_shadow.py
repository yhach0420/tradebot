"""
Phase359: Gap-up fade ENTRY guard shadow (C from Phase358).

Block shadow counterfactual when:
  - entry_rise_5min_pct >= 0.3 AND entry_vwap_dev_pct > 0
  OR
  - entry_rise_10min_pct >= 0.5 AND entry_rise_5min_pct < 0
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

GUARD_VARIANT = "C_gap_up_fade_guard"

SPLIT_VARIANTS = (
    "A_all_symbols",
    "B_dynamic40_only",
    "C_core10_only",
    "D_am_only",
    "E_am_dynamic40_only",
)


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _pf(yens: list[float]) -> Optional[float]:
    gp = sum(max(y, 0) for y in yens)
    gl = abs(sum(min(y, 0) for y in yens))
    if gl <= 0:
        return None if gp <= 0 else float("inf")
    return round(gp / gl, 4)


def would_block_gap_up_fade_guard(fields: Mapping[str, Any]) -> bool:
    rise5 = _float(fields.get("entry_rise_5min_pct"))
    rise10 = _float(fields.get("entry_rise_10min_pct"))
    vwap_dev = _float(fields.get("entry_vwap_dev_pct"))
    if rise5 is not None and rise5 >= 0.3 and vwap_dev is not None and vwap_dev > 0:
        return True
    if rise10 is not None and rise10 >= 0.5 and rise5 is not None and rise5 < 0:
        return True
    return False


def _is_dynamic40(trade: Mapping[str, Any]) -> bool:
    from small_paper.pullback_misread_dynamic40_entry_guard import is_dynamic40_universe

    if is_dynamic40_universe(trade):
        return True
    slot = str(trade.get("universe_slot") or "")
    bucket = str(trade.get("source_bucket") or trade.get("universe_bucket") or "")
    return slot == "dynamic" or bucket in ("dynamic40", "vol_liq_dynamic40")


def _is_core10(trade: Mapping[str, Any]) -> bool:
    return str(trade.get("universe_slot") or "") == "core"


def variant_blocked(
    variant: str,
    trade: Mapping[str, Any],
    *,
    session_kind: str,
) -> bool:
    if not would_block_gap_up_fade_guard(trade):
        return False
    if variant == "A_all_symbols":
        return True
    if variant == "B_dynamic40_only":
        return _is_dynamic40(trade)
    if variant == "C_core10_only":
        return _is_core10(trade)
    if variant == "D_am_only":
        return session_kind == "am"
    if variant == "E_am_dynamic40_only":
        return session_kind == "am" and _is_dynamic40(trade)
    return False


def _variant_metrics(
    trades: list[dict[str, Any]],
    *,
    variant: str,
    session_kind: str,
    actual_yens: list[float],
    stops_actual: int,
) -> dict[str, Any]:
    blocked = [t for t in trades if variant_blocked(variant, t, session_kind=session_kind)]
    kept = [t for t in trades if not variant_blocked(variant, t, session_kind=session_kind)]
    yens_kept = [float(t["pnl_yen_100"]) for t in kept if t.get("pnl_yen_100") is not None]
    yens_skip = [float(t["pnl_yen_100"]) for t in blocked if t.get("pnl_yen_100") is not None]
    stops_shadow = sum(1 for t in kept if t.get("is_stop_hit"))
    dyn_actual = [
        float(t["pnl_yen_100"])
        for t in trades
        if _is_dynamic40(t) and t.get("pnl_yen_100") is not None
    ]
    core_actual = [
        float(t["pnl_yen_100"])
        for t in trades
        if _is_core10(t) and t.get("pnl_yen_100") is not None
    ]
    dyn_kept = [t for t in kept if _is_dynamic40(t)]
    core_kept = [t for t in kept if _is_core10(t)]
    dyn_yens = [float(t["pnl_yen_100"]) for t in dyn_kept if t.get("pnl_yen_100") is not None]
    core_yens = [float(t["pnl_yen_100"]) for t in core_kept if t.get("pnl_yen_100") is not None]
    actual_total = round(sum(actual_yens), 2) if actual_yens else 0.0
    shadow_total = round(sum(yens_kept), 2) if yens_kept else 0.0
    delta = round(shadow_total - actual_total, 2)
    dyn_shadow = round(sum(dyn_yens), 2) if dyn_yens else 0.0
    core_shadow = round(sum(core_yens), 2) if core_yens else 0.0
    dyn_actual_total = round(sum(dyn_actual), 2) if dyn_actual else 0.0
    core_actual_total = round(sum(core_actual), 2) if core_actual else 0.0
    return {
        "variant": variant,
        "actual_total_pnl_yen_100": actual_total,
        "shadow_total_pnl_yen_100": shadow_total,
        "delta_yen": delta,
        "actual_pf": _pf(actual_yens),
        "shadow_pf": _pf(yens_kept),
        "trade_count_actual": len(trades),
        "trade_count_shadow": len(kept),
        "skipped_trade_count": len(blocked),
        "skipped_trade_pnl_actual": round(sum(yens_skip), 2) if yens_skip else 0.0,
        "stop_hit_count_actual": stops_actual,
        "stop_hit_count_shadow": stops_shadow,
        "stop_hit_reduction_count": stops_actual - stops_shadow,
        "improved_vs_actual": delta > 0,
        "dynamic40_actual_pnl_yen_100": dyn_actual_total,
        "dynamic40_shadow_pnl_yen_100": dyn_shadow,
        "dynamic40_delta_yen": round(dyn_shadow - dyn_actual_total, 2),
        "core10_actual_pnl_yen_100": core_actual_total,
        "core10_shadow_pnl_yen_100": core_shadow,
        "core10_delta_yen": round(core_shadow - core_actual_total, 2),
    }


def enrich_trade_for_gap_up_fade_shadow(
    trade: Mapping[str, Any],
    acc: Mapping[str, str],
) -> dict[str, Any]:
    rise5 = _float(acc.get("entry_rise_5min_pct") or trade.get("entry_rise_5min_pct"))
    rise10 = _float(acc.get("entry_rise_10min_pct") or trade.get("entry_rise_10min_pct"))
    vwap_dev = _float(acc.get("entry_vwap_dev_pct") or trade.get("entry_vwap_dev_pct"))
    fields = {
        "entry_rise_5min_pct": rise5,
        "entry_rise_10min_pct": rise10,
        "entry_vwap_dev_pct": vwap_dev,
        "universe_slot": trade.get("universe_slot"),
        "source_bucket": trade.get("source_bucket"),
        "universe_bucket": trade.get("universe_bucket"),
    }
    blocked = would_block_gap_up_fade_guard(fields)
    yen = _float(trade.get("pnl_yen_100"))
    shadow_yen = 0.0 if blocked else (yen or 0.0)
    return {
        **dict(trade),
        "entry_rise_5min_pct": rise5,
        "entry_rise_10min_pct": rise10,
        "entry_vwap_dev_pct": vwap_dev,
        "gap_up_fade_guard_shadow_blocked": blocked,
        "gap_up_fade_shadow_pnl_yen_100": shadow_yen,
        "gap_up_fade_shadow_delta_yen": round(shadow_yen - (yen or 0.0), 2) if yen is not None else None,
        "is_stop_hit": trade.get("exit_reason_canonical") == "stop_hit"
        or str(trade.get("structural_exit_reason") or trade.get("exit_reason") or "") == "stop_hit",
    }


def evaluate_session(session_meta: Mapping[str, Any], *, reports_dir: Any) -> dict[str, Any]:
    from pathlib import Path

    from research.phase357_actual_exit_audit import _load_session_trades
    from small_paper.limit_up_proximity_entry_guard_shadow import (
        _session_source_label,
    )
    from small_paper.pullback_misread_entry_guard_shadow import _stream_events_csv

    base = _load_session_trades(session_meta, reports_dir=Path(reports_dir))
    if base.get("error"):
        return {**base, "trades": [], "variants": {}, "trade_count_actual": 0}

    sess_dir = Path(str(session_meta["session_dir"]))
    session_kind = str(base.get("session_kind") or "")

    accepted: dict[tuple[str, str], dict[str, str]] = {}
    for row in _stream_events_csv(sess_dir / "small_paper_events.csv"):
        if row.get("event_type") == "accepted":
            accepted[(row.get("symbol", ""), row.get("entry_time", ""))] = row

    trades: list[dict[str, Any]] = []
    for trade in base.get("kept") or []:
        key = (trade.get("symbol", ""), trade.get("entry_time", ""))
        acc = accepted.get(key, {})
        t = enrich_trade_for_gap_up_fade_shadow(trade, acc)
        t["session_id"] = session_meta.get("session_id") or ""
        t["day_key"] = session_meta.get("day_key") or session_meta.get("day") or ""
        t["session_kind"] = session_kind
        trades.append(t)

    actual_yens = [float(t["pnl_yen_100"]) for t in trades if t.get("pnl_yen_100") is not None]
    stops_actual = sum(1 for t in trades if t.get("is_stop_hit"))

    variants = {
        v: _variant_metrics(
            trades,
            variant=v,
            session_kind=session_kind,
            actual_yens=actual_yens,
            stops_actual=stops_actual,
        )
        for v in SPLIT_VARIANTS
    }
    return {
        "session_meta": dict(session_meta),
        "session_kind": session_kind,
        "session_source": str(session_meta.get("session_source") or _session_source_label(sess_dir)),
        "trades": trades,
        "variants": variants,
        "trade_count_actual": len(trades),
        **variants["A_all_symbols"],
    }
