"""V24 funnel, legacy parity, recovered stale coverage. No horizon selection. No PnL decision."""
from __future__ import annotations

from collections import Counter
from typing import Any

from research.simple_tech_entry_family.v13_analyze import identity_keys, set_hash
from research.simple_tech_redesign.v22_analyze import _canon_num, fill_tuples_e4
from research.simple_tech_redesign.v24_spec import (
    B1_EXECUTABLE_N_EXPECTED,
    B1_SIGNAL_N_EXPECTED,
    CASE_A_NEW_FILL_MIN_DAYS,
    CASE_A_NEW_FILL_MIN_N,
    CASE_A_NEW_FILL_MIN_SYMBOLS,
    CASE_A_RECOVERED_FRAC,
    CASE_D_UNRESOLVED_FRAC,
    E4_FILLED_N_EXPECTED,
    E4_UNFILLED_N_EXPECTED,
    PARITY_ABS_TOL,
    STALE_N_EXPECTED,
)


def _finite(v: Any) -> bool:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return False
    return x == x


def _close(a: Any, b: Any, tol: float = PARITY_ABS_TOL) -> bool:
    try:
        return abs(float(a) - float(b)) <= float(tol)
    except (TypeError, ValueError):
        return False


def _key(row: dict[str, Any]) -> tuple[str, str, float]:
    return identity_keys(row)


def legacy_fill_tuple(row: dict[str, Any], e4_key: str = "e4") -> tuple[Any, ...]:
    e4 = dict(row.get(e4_key) or {})
    d, s, t0 = identity_keys(row)
    return (
        d,
        s,
        t0,
        _canon_num(e4.get("fill_t")),
        _canon_num(e4.get("fill_price")),
        _canon_num(e4.get("limit_price")),
        bool(e4.get("collapsed_to_bid")),
    )


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    former = [r for r in rows if r.get("former_stale")]
    legacy_eval = [r for r in rows if r.get("v22_executable")]
    legacy_fill_v22 = [r for r in legacy_eval if dict(r.get("v22_e4") or {}).get("filled")]
    legacy_nonfill_v22 = [r for r in legacy_eval if not dict(r.get("v22_e4") or {}).get("filled")]
    evaluable = [r for r in rows if r.get("executable_signal")]
    uneval = [r for r in rows if not r.get("executable_signal")]
    filled = [r for r in evaluable if r.get("e4_filled")]
    nonfill = [r for r in evaluable if not r.get("e4_filled")]
    recovered = [r for r in former if r.get("executable_signal")]
    still_uneval = [r for r in former if not r.get("executable_signal")]
    new_fill = [r for r in recovered if r.get("e4_filled")]
    new_nonfill = [r for r in recovered if not r.get("e4_filled")]
    sources = Counter(str(r.get("BOARD_FRESHNESS_CLOCK_SOURCE") or "UNRESOLVED") for r in rows)
    former_sources = Counter(str(r.get("BOARD_FRESHNESS_CLOCK_SOURCE") or "UNRESOLVED") for r in former)
    unresolved_n = int(sources.get("UNRESOLVED", 0))
    new_fill_days = {str(r.get("date") or "") for r in new_fill}
    new_fill_syms = {str(r.get("symbol") or "").replace(".T", "") for r in new_fill}
    rec_days = {str(r.get("date") or "") for r in recovered}
    rec_syms = {str(r.get("symbol") or "").replace(".T", "") for r in recovered}
    fills_per_day = dict(sorted(Counter(str(r.get("date") or "") for r in filled).items()))
    new_fills_per_day = dict(sorted(Counter(str(r.get("date") or "") for r in new_fill).items()))
    uneval_split = dict(Counter(str(r.get("uneval_class") or "other") for r in uneval))
    return {
        "SIGNAL_N": n,
        "LEGACY_EVALUABLE_N": len(legacy_eval),
        "LEGACY_E4_FILLED_N": len(legacy_fill_v22),
        "LEGACY_E4_NONFILLED_N": len(legacy_nonfill_v22),
        "FORMER_STALE_N": len(former),
        "CORRECTED_EXECUTION_EVALUABLE_N": len(evaluable),
        "CORRECTED_EXECUTION_UNEVALUABLE_N": len(uneval),
        "RECOVERED_FROM_STALE_N": len(recovered),
        "STILL_UNEVALUABLE_FROM_STALE_N": len(still_uneval),
        "CORRECTED_E4_FILLED_N": len(filled),
        "CORRECTED_E4_NONFILLED_N": len(nonfill),
        "NEW_FILL_FROM_FORMER_STALE_N": len(new_fill),
        "NEW_NONFILL_FROM_FORMER_STALE_N": len(new_nonfill),
        "NEW_FILL_DAY_N": len(new_fill_days),
        "NEW_FILL_SYMBOL_N": len(new_fill_syms),
        "RECOVERED_DAY_N": len(rec_days),
        "RECOVERED_SYMBOL_N": len(rec_syms),
        "FILLS_PER_DAY": fills_per_day,
        "NEW_FILLS_PER_DAY": new_fills_per_day,
        "BOARD_FRESHNESS_CLOCK_SOURCE_COUNTS": dict(sources),
        "FORMER_STALE_CLOCK_SOURCE_COUNTS": dict(former_sources),
        "UNEVAL_SPLIT": uneval_split,
        "UNRESOLVED_CLOCK_N": unresolved_n,
        "UNRESOLVED_FRAC": (float(unresolved_n) / float(n)) if n else 0.0,
        "RECOVERED_FRAC": (float(len(recovered)) / float(len(former))) if former else 0.0,
        "legacy_eval_rows": legacy_eval,
        "legacy_fill_v22": legacy_fill_v22,
        "legacy_nonfill_v22": legacy_nonfill_v22,
        "new_fill_rows": new_fill,
        "recovered_rows": recovered,
    }


def diagnose_parity_breaks(rows: list[dict[str, Any]]) -> dict[str, Any]:
    lost_eval: list[dict[str, Any]] = []
    extra_fill: list[dict[str, Any]] = []
    fill_time_shift: list[dict[str, Any]] = []
    for r in rows:
        v22e = bool(r.get("v22_executable"))
        e24 = bool(r.get("executable_signal"))
        f22 = bool(dict(r.get("v22_e4") or {}).get("filled"))
        f24 = bool(r.get("e4_filled"))
        v22 = dict(r.get("v22_e4") or {})
        v24 = dict(r.get("e4") or {})
        pack = {
            "date": r.get("date"),
            "symbol": str(r.get("symbol") or "").replace(".T", ""),
            "t0": r.get("t0"),
            "BOARD_FRESHNESS_CLOCK_SOURCE": r.get("BOARD_FRESHNESS_CLOCK_SOURCE"),
            "board_fresh_sec": r.get("board_fresh_sec"),
            "PRICE_FRESHNESS_SEC": r.get("PRICE_FRESHNESS_SEC"),
            "v22_cohort": r.get("v22_cohort"),
            "ask_reason": r.get("ask_reason"),
            "v22_fill_t": v22.get("fill_t"),
            "v24_fill_t": v24.get("fill_t"),
            "v22_fill_price": v22.get("fill_price"),
            "v24_fill_price": v24.get("fill_price"),
            "v22_limit_price": v22.get("limit_price"),
            "v24_limit_price": v24.get("limit_price"),
        }
        if v22e and not e24:
            pack["mechanism"] = "INVERTED_CLOCK_LAST_TRADE_FRESH_BOARD_EVENT_STALE"
            lost_eval.append(pack)
        if v22e and (not f22) and f24:
            pack["mechanism"] = "WAIT_WINDOW_QUOTE_NOW_BOARD_FRESH_ASK_CROSS"
            extra_fill.append(pack)
        if v22e and f22 and f24 and legacy_fill_tuple(r, "v22_e4") != legacy_fill_tuple(r, "e4"):
            dt = None
            if _finite(v22.get("fill_t")) and _finite(v24.get("fill_t")):
                dt = float(v24["fill_t"]) - float(v22["fill_t"])
            pack["fill_t_delta_sec"] = dt
            pack["mechanism"] = "LEGACY_FILL_EARLIER_BECAUSE_WAIT_QUOTE_NOW_BOARD_FRESH"
            fill_time_shift.append(pack)
    return {
        "lost_legacy_eval": lost_eval,
        "extra_legacy_fill": extra_fill,
        "legacy_fill_identity_shift": fill_time_shift,
        "cause": (
            "BidTime/AskTime is not a superset of CurrentPriceTime freshness. "
            "2 legacy evaluable names have last-trade age <5s but board event age >5s. "
            "3 legacy nonfills and 1 legacy fill-time shift are wait-window quotes that "
            "were CurrentPriceTime-stale in V22 and BidTime-fresh in V24. E4 price/wait unchanged."
        ),
    }


def parity(rows: list[dict[str, Any]], summary: dict[str, Any]) -> dict[str, Any]:
    n = len(rows)
    former_n = int(summary.get("FORMER_STALE_N") or 0)
    legacy_eval = list(summary.get("legacy_eval_rows") or [])
    legacy_fill_v22 = list(summary.get("legacy_fill_v22") or [])
    legacy_nonfill_v22 = list(summary.get("legacy_nonfill_v22") or [])
    signal_ok = n == int(B1_SIGNAL_N_EXPECTED)
    eval_n_ok = len(legacy_eval) == int(B1_EXECUTABLE_N_EXPECTED)
    fill_n_ok = len(legacy_fill_v22) == int(E4_FILLED_N_EXPECTED)
    nonfill_n_ok = len(legacy_nonfill_v22) == int(E4_UNFILLED_N_EXPECTED)
    stale_n_ok = former_n == int(STALE_N_EXPECTED)
    legacy_still_eval = all(bool(r.get("executable_signal")) for r in legacy_eval) if legacy_eval else False
    v22_fill_tuples = sorted(legacy_fill_tuple(r, "v22_e4") for r in legacy_fill_v22)
    v24_fill_on_legacy = sorted(legacy_fill_tuple(r, "e4") for r in legacy_fill_v22 if r.get("e4_filled"))
    fill_identity_ok = v22_fill_tuples == v24_fill_on_legacy and len(v24_fill_on_legacy) == int(E4_FILLED_N_EXPECTED)
    legacy_nonfill_still = all((not r.get("e4_filled")) and r.get("executable_signal") for r in legacy_nonfill_v22)
    extra_legacy_fills = [r for r in legacy_nonfill_v22 if r.get("e4_filled")]
    lost_legacy_fills = [r for r in legacy_fill_v22 if not r.get("e4_filled")]
    lost_legacy_eval = [r for r in legacy_eval if not r.get("executable_signal")]
    return {
        "SIGNAL_N_OK": bool(signal_ok),
        "FORMER_STALE_N_OK": bool(stale_n_ok),
        "LEGACY_EVALUABLE_PARITY": bool(eval_n_ok and legacy_still_eval and not lost_legacy_eval),
        "LEGACY_E4_FILL_PARITY": bool(fill_n_ok and fill_identity_ok and not lost_legacy_fills),
        "LEGACY_E4_NONFILL_PARITY": bool(nonfill_n_ok and legacy_nonfill_still and not extra_legacy_fills),
        "lost_legacy_eval_n": len(lost_legacy_eval),
        "lost_legacy_fill_n": len(lost_legacy_fills),
        "extra_legacy_fill_n": len(extra_legacy_fills),
        "v22_fill_hash": set_hash(v22_fill_tuples),
        "v24_legacy_fill_hash": set_hash(v24_fill_on_legacy),
        "corrected_fill_hash": set_hash(fill_tuples_e4(rows)),
    }


def decide(
    summary: dict[str, Any],
    par: dict[str, Any],
    *,
    leak_ok: bool,
    identity_ok: bool,
    future_board_n: int,
    future_ts_n: int,
    cpt_as_board_n: int,
) -> dict[str, Any]:
    recovered_n = int(summary.get("RECOVERED_FROM_STALE_N") or 0)
    former_n = int(summary.get("FORMER_STALE_N") or 0)
    recovered_frac = float(summary.get("RECOVERED_FRAC") or 0.0)
    new_fill_n = int(summary.get("NEW_FILL_FROM_FORMER_STALE_N") or 0)
    new_fill_days = int(summary.get("NEW_FILL_DAY_N") or 0)
    new_fill_syms = int(summary.get("NEW_FILL_SYMBOL_N") or 0)
    unresolved_frac = float(summary.get("UNRESOLVED_FRAC") or 0.0)
    clock_causal = int(future_board_n) == 0 and int(future_ts_n) == 0 and int(cpt_as_board_n) == 0
    legacy_ok = bool(
        par.get("LEGACY_EVALUABLE_PARITY")
        and par.get("LEGACY_E4_FILL_PARITY")
        and par.get("LEGACY_E4_NONFILL_PARITY")
        and par.get("SIGNAL_N_OK")
        and par.get("FORMER_STALE_N_OK")
    )
    material_recovered = former_n == int(STALE_N_EXPECTED) and recovered_frac >= float(CASE_A_RECOVERED_FRAC)
    material_new_fills = (
        new_fill_n >= int(CASE_A_NEW_FILL_MIN_N)
        and new_fill_days >= int(CASE_A_NEW_FILL_MIN_DAYS)
        and new_fill_syms >= int(CASE_A_NEW_FILL_MIN_SYMBOLS)
    )
    case = "B"
    verdict = "SIMPLE_TECH_V24_SEMANTICS_FIXED_FILL_SCARCITY_REMAINS"
    next_step = (
        "Joint ENTRY coverage + technical EXIT research may proceed. Treat fill scarcity as a real "
        "problem. Do not retune ENTRY/E4/freshness to manufacture fills. EXIT must not use elapsed seconds."
    )
    if (not identity_ok) or (not leak_ok) or (not clock_causal) or unresolved_frac >= float(CASE_D_UNRESOLVED_FRAC):
        case = "D"
        verdict = "SIMPLE_TECH_V24_BOARD_CLOCK_UNRESOLVED"
        next_step = "STOP. Board freshness clock causality was not proven. Do not change strategy rules."
    elif not legacy_ok:
        case = "C"
        verdict = "SIMPLE_TECH_V24_LEGACY_PARITY_FAILED"
        next_step = "STOP. Legacy 126/38/88 identity changed. Diagnose before any further coverage work."
    elif material_recovered and material_new_fills:
        case = "A"
        verdict = "SIMPLE_TECH_V24_BOARD_FRESHNESS_SEMANTICS_CORRECTED"
        next_step = (
            "Corrected E4 fill count is the new development execution baseline. "
            "Next: joint ENTRY coverage + technical EXIT research. No simultaneous parameter optimization. "
            "EXIT uses 1m/3m/5m EMA/BB/RCI/Volume state deterioration, not elapsed seconds."
        )
    return {
        "CASE": case,
        "VERDICT": verdict,
        "NEXT": next_step,
        "MATERIAL_RECOVERED": bool(material_recovered),
        "MATERIAL_NEW_FILLS": bool(material_new_fills),
        "RECOVERED_FRAC": recovered_frac,
        "CLOCK_CAUSAL": bool(clock_causal),
        "LEGACY_OK": bool(legacy_ok),
        "ENTRY_CHANGED": False,
        "E4_CHANGED": False,
        "FRESHNESS_THRESHOLD_CHANGED": False,
        "EXIT_CHANGED": False,
        "SIZING_CHANGED": False,
        "TRUE_OOS": False,
        "NEW_TRADE_VIRTUAL_N": 0,
    }
