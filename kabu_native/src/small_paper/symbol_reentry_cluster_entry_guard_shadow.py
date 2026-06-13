"""
Phase368: Symbol reentry cluster ENTRY guard shadow.

After a low-MFE stop_hit (peak_mfe < 0.3%) on same day/symbol,
subsequent entries are shadow-blocked per variant scope.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

LOW_MFE_THRESHOLD_PCT = 0.3

SPLIT_VARIANTS = (
    "A_all_symbols",
    "B_dynamic40_only",
    "C_core10_only",
    "D_same_session_only",
    "E_same_day_am_pm_carryover",
)

VARIANT_LABELS = {
    "A_all_symbols": "All symbols; prior low-MFE stop same day",
    "B_dynamic40_only": "Dynamic40 only; prior low-MFE stop same day",
    "C_core10_only": "Core10 only; prior low-MFE stop same day",
    "D_same_session_only": "All symbols; prior low-MFE stop same session only",
    "E_same_day_am_pm_carryover": "All symbols; prior low-MFE stop earlier session same day",
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


def _is_core10(trade: Mapping[str, Any]) -> bool:
    return str(trade.get("universe_slot") or "") == "core"


def _variant_applies_to_trade(variant: str, trade: Mapping[str, Any]) -> bool:
    if variant == "B_dynamic40_only":
        return _is_dynamic40(trade)
    if variant == "C_core10_only":
        return _is_core10(trade)
    return True


def annotate_day_variants(
    day_trades: Sequence[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    from collections import defaultdict

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

            blocked = False
            if _variant_applies_to_trade(variant, row):
                if variant == "D_same_session_only":
                    blocked = sym in session_stopped
                elif variant == "E_same_day_am_pm_carryover":
                    blocked = cross_for_sym
                else:
                    blocked = sym in day_stopped

            yen = _float(row.get("pnl_yen_100"))
            shadow_yen = 0.0 if blocked else (yen or 0.0)
            row["symbol_reentry_guard_shadow_blocked"] = blocked
            row["symbol_reentry_shadow_pnl_yen_100"] = shadow_yen
            row["symbol_reentry_shadow_delta_yen"] = (
                round(shadow_yen - (yen or 0.0), 2) if yen is not None else None
            )
            row["symbol_reentry_guard_variant"] = variant
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
        0.0
        if t.get("symbol_reentry_guard_shadow_blocked")
        else float(_float(t.get("pnl_yen_100")) or 0.0)
        for t in trades
    ]
    blocked = [t for t in trades if t.get("symbol_reentry_guard_shadow_blocked")]
    kept = [t for t in trades if not t.get("symbol_reentry_guard_shadow_blocked")]
    skipped_pnl = sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in blocked)
    stops_actual = sum(1 for t in trades if t.get("exit_reason_canonical") == "stop_hit")
    stops_shadow = sum(
        1 for t in kept if t.get("exit_reason_canonical") == "stop_hit"
    )
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
    shadow_total = round(sum(shadow_yens), 2) if shadow_yens else 0.0
    return {
        "variant": variant,
        "label": VARIANT_LABELS.get(variant, variant),
        "actual_total_pnl_yen_100": actual_total,
        "shadow_total_pnl_yen_100": shadow_total,
        "delta_yen": round(shadow_total - actual_total, 2),
        "actual_pf": _pf(actual_yens),
        "shadow_pf": _pf(shadow_yens),
        "skipped_trade_count": len(blocked),
        "skipped_trade_pnl_actual": round(skipped_pnl, 2),
        "stop_hit_reduction_count": stops_actual - stops_shadow,
        "trade_count_actual": len(trades),
        "trade_count_shadow": len(kept),
        "dynamic40_actual_pnl_yen_100": round(sum(dyn_actual), 2) if dyn_actual else 0.0,
        "dynamic40_shadow_pnl_yen_100": round(sum(dyn_yens), 2) if dyn_yens else 0.0,
        "dynamic40_delta_yen": round(sum(dyn_yens) - sum(dyn_actual), 2) if dyn_actual else 0.0,
        "core10_actual_pnl_yen_100": round(sum(core_actual), 2) if core_actual else 0.0,
        "core10_shadow_pnl_yen_100": round(sum(core_yens), 2) if core_yens else 0.0,
        "core10_delta_yen": round(sum(core_yens) - sum(core_actual), 2) if core_actual else 0.0,
    }


def load_session_production_trades_for_shadow(
    session_meta: Mapping[str, Any], *, reports_dir: Any
) -> dict[str, Any]:
    from pathlib import Path

    from research.phase366_stophit_reclassification import production_kept_trades
    from research.phase365_production_stack_validation import load_session_production_stack_trades
    from small_paper.limit_up_proximity_entry_guard_shadow import _session_source_label

    base = load_session_production_stack_trades(session_meta, reports_dir=Path(reports_dir))
    if base.get("error"):
        return {**base, "production_trades": [], "error": base.get("error")}

    trades = production_kept_trades(base)
    sid = str(session_meta.get("session_id") or "")
    dkey = str(session_meta.get("day_key") or session_meta.get("day") or "")
    for t in trades:
        t["session_id"] = t.get("session_id") or sid
        t["day_key"] = t.get("day_key") or dkey
        t["is_stop_hit"] = t.get("exit_reason_canonical") == "stop_hit"
        t["is_low_mfe_stop"] = is_low_mfe_stop(t)

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
