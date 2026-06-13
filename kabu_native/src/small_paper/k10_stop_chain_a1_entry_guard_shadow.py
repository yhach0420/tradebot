"""
Phase370: K10 stop-chain A1 ENTRY guard shadow.

Blocks Dynamic40 entries only when:
  1) same day/symbol had a prior low-MFE stop_hit (peak_mfe < 0.3%), and
  2) current entry matches A1 profile (board_low or imb_pctile threshold + weak momentum).

Distinct from Phase368 symbol reentry guard which blocks ALL subsequent entries.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping, Optional, Sequence

LOW_MFE_THRESHOLD_PCT = 0.3
A1_IMB_THRESHOLD = 25.0
A1_MOM_THRESHOLD = 0.30
D_IMB_THRESHOLD = 15.0
E_MOM_THRESHOLD = 0.25

SPLIT_VARIANTS = (
    "A_k10_exact",
    "B_k10_same_session_only",
    "C_k10_same_day_carryover",
    "D_k10_stricter_board",
    "E_k10_stricter_momentum",
)

VARIANT_LABELS = {
    "A_k10_exact": "Dynamic40 + prior same-day low-MFE stop + A1 (board_low/imb<25, mom<0.30)",
    "B_k10_same_session_only": "Dynamic40 + prior low-MFE stop same session only + A1",
    "C_k10_same_day_carryover": "Dynamic40 + prior low-MFE stop earlier session (AM→PM) + A1",
    "D_k10_stricter_board": "Dynamic40 + prior same-day low-MFE stop + imb<15, mom<0.30",
    "E_k10_stricter_momentum": "Dynamic40 + prior same-day low-MFE stop + board_low/imb<25, mom<0.25",
}


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _pf(yens: Sequence[float]) -> Optional[float]:
    gp = sum(max(y, 0.0) for y in yens)
    gl = abs(sum(min(y, 0.0) for y in yens))
    if gl <= 0:
        return None if gp <= 0 else float("inf")
    return round(gp / gl, 4)


def is_low_mfe_stop(trade: Mapping[str, Any]) -> bool:
    return trade.get("exit_reason_canonical") == "stop_hit" and (
        _float(trade.get("peak_mfe_pct")) or 0.0
    ) < LOW_MFE_THRESHOLD_PCT


def _is_dynamic40(trade: Mapping[str, Any]) -> bool:
    from small_paper.pullback_misread_dynamic40_entry_guard import is_dynamic40_universe

    return is_dynamic40_universe(trade)


def _is_board_low_standard(trade: Mapping[str, Any]) -> bool:
    tier = str(trade.get("board_dynamic_tier") or "")
    if tier == "board_low":
        return True
    pctile = _float(trade.get("entry_imbalance_percentile"))
    return pctile is not None and pctile < A1_IMB_THRESHOLD


def matches_a1_entry(trade: Mapping[str, Any], variant: str) -> bool:
    mom_thresh = E_MOM_THRESHOLD if variant == "E_k10_stricter_momentum" else A1_MOM_THRESHOLD
    mom = _float(trade.get("entry_momentum_score"))
    if mom is None or mom >= mom_thresh:
        return False
    if variant == "D_k10_stricter_board":
        pctile = _float(trade.get("entry_imbalance_percentile"))
        return pctile is not None and pctile < D_IMB_THRESHOLD
    return _is_board_low_standard(trade)


def _prior_stop_blocks(
    variant: str,
    *,
    sym: str,
    day_stopped: set[str],
    session_stopped: set[str],
    cross_for_sym: bool,
) -> bool:
    if not sym:
        return False
    if variant == "B_k10_same_session_only":
        return sym in session_stopped
    if variant == "C_k10_same_day_carryover":
        return cross_for_sym
    return sym in day_stopped


def would_block_k10_guard(trade: Mapping[str, Any], variant: str, *, prior_blocked: bool) -> bool:
    if not _is_dynamic40(trade):
        return False
    if not matches_a1_entry(trade, variant):
        return False
    return prior_blocked


def annotate_day_variants(
    day_trades: Sequence[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    sorted_trades = sorted(day_trades, key=lambda x: str(x.get("entry_time") or ""))
    result: dict[str, list[dict[str, Any]]] = {}

    for variant in SPLIT_VARIANTS:
        day_stopped: set[str] = set()
        session_stopped: set[str] = set()
        sym_stop_sessions: dict[str, list[str]] = defaultdict(list)
        current_session = ""
        rows: list[dict[str, Any]] = []

        for raw in sorted_trades:
            row = dict(raw)
            sym = str(row.get("symbol") or "")
            sess = str(row.get("session_id") or "")
            if sess != current_session:
                current_session = sess
                session_stopped = set()

            cross_for_sym = False
            if sym and sym in sym_stop_sessions:
                prior_sess = sym_stop_sessions[sym]
                if any(s != sess for s in prior_sess):
                    cross_for_sym = True

            prior_blocked = _prior_stop_blocks(
                variant,
                sym=sym,
                day_stopped=day_stopped,
                session_stopped=session_stopped,
                cross_for_sym=cross_for_sym,
            )
            blocked = would_block_k10_guard(row, variant, prior_blocked=prior_blocked)
            yen = _float(row.get("pnl_yen_100"))
            shadow_yen = 0.0 if blocked else (yen or 0.0)
            row["k10_guard_shadow_blocked"] = blocked
            row["k10_shadow_pnl_yen_100"] = shadow_yen
            row["k10_shadow_delta_yen"] = (
                round(shadow_yen - (yen or 0.0), 2) if yen is not None else None
            )
            row["k10_guard_variant"] = variant
            row["k10_matches_a1"] = matches_a1_entry(row, variant)
            row["k10_prior_low_mfe_stop"] = prior_blocked
            rows.append(row)

            if is_low_mfe_stop(row) and sym:
                if sess and sess not in sym_stop_sessions[sym]:
                    sym_stop_sessions[sym].append(sess)
                day_stopped.add(sym)
                session_stopped.add(sym)

        result[variant] = rows
    return result


def _variant_metrics(
    trades: Sequence[Mapping[str, Any]],
    *,
    variant: str,
) -> dict[str, Any]:
    actual_yens = [float(_float(t.get("pnl_yen_100")) or 0.0) for t in trades]
    shadow_yens = [
        0.0 if t.get("k10_guard_shadow_blocked") else float(_float(t.get("pnl_yen_100")) or 0.0)
        for t in trades
    ]
    blocked = [t for t in trades if t.get("k10_guard_shadow_blocked")]
    kept = [t for t in trades if not t.get("k10_guard_shadow_blocked")]
    skipped_pnl = sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in blocked)
    stops_actual = sum(1 for t in trades if t.get("exit_reason_canonical") == "stop_hit")
    stops_shadow = sum(1 for t in kept if t.get("exit_reason_canonical") == "stop_hit")
    low_mfe_stops_blocked = sum(1 for t in blocked if is_low_mfe_stop(t))

    dyn_actual = [
        float(t["pnl_yen_100"])
        for t in trades
        if _is_dynamic40(t) and t.get("pnl_yen_100") is not None
    ]
    core_actual = [
        float(t["pnl_yen_100"])
        for t in trades
        if str(t.get("universe_group") or "") == "core10" and t.get("pnl_yen_100") is not None
    ]
    dyn_kept = [t for t in kept if _is_dynamic40(t)]
    core_kept = [t for t in kept if str(t.get("universe_group") or "") == "core10"]
    dyn_yens = [float(t["pnl_yen_100"]) for t in dyn_kept if t.get("pnl_yen_100") is not None]
    core_yens = [float(t["pnl_yen_100"]) for t in core_kept if t.get("pnl_yen_100") is not None]
    actual_total = round(sum(actual_yens), 2) if actual_yens else 0.0
    shadow_total = round(sum(shadow_yens), 2) if shadow_yens else 0.0
    return {
        "variant": variant,
        "label": VARIANT_LABELS.get(variant, variant),
        "actual_total_pnl_yen_100": actual_total,
        "shadow_total_pnl_yen_100": shadow_total,
        "delta_yen": round(shadow_total - actual_total, 2),
        "actual_pf": _pf(actual_yens),
        "shadow_pf": _pf(shadow_yens),
        "delta_pf": (
            round((_pf(shadow_yens) or 0) - (_pf(actual_yens) or 0), 4)
            if _pf(shadow_yens) is not None
            and _pf(actual_yens) is not None
            and _pf(shadow_yens) != float("inf")
            and _pf(actual_yens) != float("inf")
            else None
        ),
        "skipped_trade_count": len(blocked),
        "skipped_trade_pnl_actual": round(skipped_pnl, 2),
        "stop_hit_reduction_count": stops_actual - stops_shadow,
        "low_mfe_stop_hit_reduction_count": low_mfe_stops_blocked,
        "trade_count_actual": len(trades),
        "trade_count_shadow": len(kept),
        "dynamic40_actual_pnl_yen_100": round(sum(dyn_actual), 2) if dyn_actual else 0.0,
        "dynamic40_shadow_pnl_yen_100": round(sum(dyn_yens), 2) if dyn_yens else 0.0,
        "dynamic40_delta_yen": round(sum(dyn_yens) - sum(dyn_actual), 2) if dyn_actual else 0.0,
        "core10_actual_pnl_yen_100": round(sum(core_actual), 2) if core_actual else 0.0,
        "core10_shadow_pnl_yen_100": round(sum(core_yens), 2) if core_yens else 0.0,
        "core10_delta_yen": round(sum(core_yens) - sum(core_actual), 2) if core_actual else 0.0,
    }


def load_session_production_trades_for_k10_shadow(
    session_meta: Mapping[str, Any], *, reports_dir: Any
) -> dict[str, Any]:
    from pathlib import Path

    from research.phase365_production_stack_validation import load_session_production_stack_trades
    from research.phase366_stophit_reclassification import production_kept_trades
    from research.phase367_low_mfe_residual_forensic import enrich_residual_trade
    from small_paper.limit_up_proximity_entry_guard_shadow import _session_source_label
    from small_paper.pullback_misread_entry_guard_shadow import _stream_events_csv

    base = load_session_production_stack_trades(session_meta, reports_dir=Path(reports_dir))
    if base.get("error"):
        return {**base, "production_trades": [], "error": base.get("error")}

    sess_dir = Path(str(session_meta["session_dir"]))
    accepted: dict[tuple[str, str], dict[str, str]] = {}
    for row in _stream_events_csv(sess_dir / "small_paper_events.csv"):
        if row.get("event_type") == "accepted":
            accepted[(row.get("symbol", ""), row.get("entry_time", ""))] = row

    trades: list[dict[str, Any]] = []
    sid = str(session_meta.get("session_id") or "")
    dkey = str(session_meta.get("day_key") or session_meta.get("day") or "")
    for trade in production_kept_trades(base):
        key = (trade.get("symbol", ""), trade.get("entry_time", ""))
        acc = accepted.get(key, {})
        row = enrich_residual_trade(trade, acc)
        row["session_id"] = row.get("session_id") or sid
        row["day_key"] = row.get("day_key") or dkey
        row["is_stop_hit"] = row.get("exit_reason_canonical") == "stop_hit"
        row["is_low_mfe_stop"] = is_low_mfe_stop(row)
        trades.append(row)

    return {
        **base,
        "session_meta": dict(session_meta),
        "production_trades": trades,
        "trade_count_actual": len(trades),
        "session_kind": str(base.get("session_kind") or ""),
        "session_source": str(
            session_meta.get("session_source")
            or _session_source_label(Path(str(session_meta["session_dir"])))
        ),
        "error": "",
    }
