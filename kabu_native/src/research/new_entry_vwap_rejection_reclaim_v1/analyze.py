"""VWAP Rejection/Reclaim gates. Discovery metrics never see holdout. No threshold search. No fallback after economic fail."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.new_entry_vwap_rejection_reclaim_v1 import (
    DEV_MIN_EVALUABLE,
    HOLDOUT_MIN_EVALUABLE,
    HORIZONS_SEC,
    STRESS_MIN_EVALUABLE,
)
from research.new_entry_vwap_rejection_reclaim_v1.harvest import AUDIT
from research.new_entry_vwap_rejection_reclaim_v1.rule import EXACT_RULE_TEXT

CASE_A = "NEW_ENTRY_VWAP_RECLAIM_EDGE_SUPPORTED"
CASE_B = "NEW_ENTRY_VWAP_RECLAIM_DEV_FAILED"
CASE_B_COV = "NEW_ENTRY_VWAP_RECLAIM_COVERAGE_INSUFFICIENT"
CASE_C = "NEW_ENTRY_VWAP_RECLAIM_HOLDOUT_FAILED"
CASE_C_COV = "NEW_ENTRY_VWAP_RECLAIM_HOLDOUT_COVERAGE_FAILED"
CASE_D = "NEW_ENTRY_VWAP_RECLAIM_STRESS_FAILED"
CASE_D_EXEC = "NEW_ENTRY_VWAP_RECLAIM_EXEC_SCREEN_FAILED"
CASE_E = "NEW_ENTRY_VWAP_RECLAIM_INTEGRITY_FAILED"


def _mean(xs: list[Any]) -> Optional[float]:
    vs = [float(x) for x in xs if x is not None and isinstance(x, (int, float)) and x == x]
    if not vs:
        return None
    return float(np.mean(vs))


def _median(xs: list[Any]) -> Optional[float]:
    vs = [float(x) for x in xs if x is not None and isinstance(x, (int, float)) and x == x]
    if not vs:
        return None
    return float(np.median(vs))


def _vals(rows: list[dict[str, Any]], key: str) -> list[Any]:
    return [r.get(key) for r in rows]


def leakage_n() -> dict[str, int]:
    keys = (
        "HOLDOUT_READ_BEFORE_RULE_FREEZE_N",
        "STRESS_READ_BEFORE_HOLDOUT_DECISION_N",
        "RULE_CHANGE_AFTER_HOLDOUT_OPEN_N",
        "HOLDOUT_USED_FOR_THRESHOLD_N",
        "STRESS_USED_FOR_THRESHOLD_N",
        "FUTURE_DATA_N",
        "CURRENT_PRICE_TIME_AS_BOARD_FRESH_N",
        "SPLIT_LEAKAGE_N",
    )
    return {k: int(AUDIT.get(k) or 0) for k in keys}


def integrity_ok(extra: dict[str, Any] | None = None) -> bool:
    leak = leakage_n()
    if extra:
        for k, v in extra.items():
            leak[k] = int(leak.get(k) or 0) + int(v or 0)
    return all(int(v) == 0 for v in leak.values())


def pack_split(rows: list[dict[str, Any]], days: list[str]) -> dict[str, Any]:
    sig = list(rows)
    exe = [r for r in sig if r.get("executable_signal")]
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in exe:
        by[str(r.get("date") or "")].append(r)
    daily = []
    for d in days:
        xs = by.get(d) or []
        daily.append(
            {
                "date": d,
                "SIGNAL_N": sum(1 for r in sig if str(r.get("date") or "") == d),
                "EXECUTABLE_N": len(xs),
                "MEAN_60": _mean(_vals(xs, "markout_60")),
                "MEAN_180": _mean(_vals(xs, "markout_180")),
                "MEAN_300": _mean(_vals(xs, "markout_300")),
            }
        )
    def _day_signs(key: str) -> dict[str, Any]:
        nums = [r.get(key) for r in daily if r.get("EXECUTABLE_N")]
        vs = [float(v) for v in nums if v is not None and v == v]
        pos = sum(1 for v in vs if v > 1e-12)
        neg = sum(1 for v in vs if v < -1e-12)
        zero = len(vs) - pos - neg
        return {"positive_day_n": pos, "negative_day_n": neg, "zero_day_n": zero}

    def _ex_best(key: str) -> Optional[float]:
        usable = [(str(r.get("date") or ""), r.get(key)) for r in exe]
        byd: dict[str, list[float]] = defaultdict(list)
        for d, v in usable:
            if v is None or not isinstance(v, (int, float)) or v != v:
                continue
            byd[d].append(float(v))
        if not byd:
            return None
        day_mean = {d: float(np.mean(vs)) for d, vs in byd.items()}
        best = max(day_mean.keys(), key=lambda d: day_mean[d])
        rest = [v for d, vs in byd.items() if d != best for v in vs]
        return _mean(rest)

    def _drop_top(key: str) -> Optional[float]:
        counts: dict[str, int] = defaultdict(int)
        for r in exe:
            counts[str(r.get("symbol") or "")] += 1
        if not counts:
            return None
        top = max(counts.keys(), key=lambda s: (counts[s], s))
        rest = [r.get(key) for r in exe if str(r.get("symbol") or "") != top]
        return _mean(rest)

    def _conc(key: str) -> dict[str, Any]:
        usable = [(str(r.get("date") or ""), str(r.get("symbol") or ""), r.get(key)) for r in exe]
        ok = [(d, s, float(v)) for d, s, v in usable if v is not None and isinstance(v, (int, float)) and v == v]
        if not ok:
            return {"top_day": None, "top_day_contribution": None, "top_symbol": None, "top_symbol_contribution": None}
        total = float(sum(v for _d, _s, v in ok))
        by_day: dict[str, float] = defaultdict(float)
        by_sym: dict[str, float] = defaultdict(float)
        for d, s, v in ok:
            by_day[d] += v
            by_sym[s] += v
        top_d = max(by_day.keys(), key=lambda d: by_day[d])
        top_s = max(by_sym.keys(), key=lambda s: by_sym[s])
        contrib_d = (by_day[top_d] / total) if abs(total) > 1e-12 else None
        contrib_s = (by_sym[top_s] / total) if abs(total) > 1e-12 else None
        return {
            "top_day": top_d,
            "top_day_contribution": contrib_d,
            "top_symbol": top_s,
            "top_symbol_contribution": contrib_s,
        }

    signs180 = _day_signs("MEAN_180")
    signs300 = _day_signs("MEAN_300")
    c180 = _conc("markout_180")
    c300 = _conc("markout_300")
    n_days = max(len(days), 1)
    return {
        "SIGNAL_N": len(sig),
        "EXECUTION_EVALUABLE_N": len(exe),
        "signals_per_day": float(len(sig) / n_days),
        "MEAN_60": _mean(_vals(exe, "markout_60")),
        "MEDIAN_60": _median(_vals(exe, "markout_60")),
        "MEAN_180": _mean(_vals(exe, "markout_180")),
        "MEDIAN_180": _median(_vals(exe, "markout_180")),
        "MEAN_300": _mean(_vals(exe, "markout_300")),
        "MEDIAN_300": _median(_vals(exe, "markout_300")),
        "MFE300": _mean(_vals(exe, "mfe_bps")),
        "MAE300": _mean(_vals(exe, "mae_bps")),
        "positive_n": sum(1 for r in exe if isinstance(r.get("markout_300"), (int, float)) and float(r["markout_300"]) > 1e-12),
        "negative_n": sum(1 for r in exe if isinstance(r.get("markout_300"), (int, float)) and float(r["markout_300"]) < -1e-12),
        "zero_n": sum(1 for r in exe if isinstance(r.get("markout_300"), (int, float)) and abs(float(r["markout_300"])) <= 1e-12),
        "positive_day_n_180": signs180["positive_day_n"],
        "negative_day_n_180": signs180["negative_day_n"],
        "zero_day_n_180": signs180["zero_day_n"],
        "positive_day_n_300": signs300["positive_day_n"],
        "negative_day_n_300": signs300["negative_day_n"],
        "zero_day_n_300": signs300["zero_day_n"],
        "daily": daily,
        "EX_BEST_DAY_MEAN_180": _ex_best("markout_180"),
        "EX_BEST_DAY_MEAN_300": _ex_best("markout_300"),
        "DROP_TOP_SYMBOL_MEAN_180": _drop_top("markout_180"),
        "DROP_TOP_SYMBOL_MEAN_300": _drop_top("markout_300"),
        "top_day_180": c180.get("top_day"),
        "top_day_contribution_180": c180.get("top_day_contribution"),
        "top_symbol_180": c180.get("top_symbol"),
        "top_symbol_contribution_180": c180.get("top_symbol_contribution"),
        "top_day_300": c300.get("top_day"),
        "top_day_contribution_300": c300.get("top_day_contribution"),
        "top_symbol_300": c300.get("top_symbol"),
        "top_symbol_contribution_300": c300.get("top_symbol_contribution"),
        "horizons": list(HORIZONS_SEC),
        "EXACT_RULE_TEXT": EXACT_RULE_TEXT,
    }


def _gt0(v: Any) -> bool:
    return v is not None and float(v) > 0.0


def _ge0(v: Any) -> bool:
    return v is not None and float(v) >= 0.0


def dev_gates(p: dict[str, Any]) -> dict[str, Any]:
    n = int(p.get("EXECUTION_EVALUABLE_N") or 0)
    cov = n >= int(DEV_MIN_EVALUABLE)
    gates = {
        "COVERAGE": cov,
        "MEAN_180_GT_0": _gt0(p.get("MEAN_180")),
        "MEAN_300_GT_0": _gt0(p.get("MEAN_300")),
        "POS_DAY_180_GT_NEG": int(p.get("positive_day_n_180") or 0) > int(p.get("negative_day_n_180") or 0),
        "POS_DAY_300_GE_NEG": int(p.get("positive_day_n_300") or 0) >= int(p.get("negative_day_n_300") or 0),
        "EX_BEST_180_GT_0": _gt0(p.get("EX_BEST_DAY_MEAN_180")),
        "EX_BEST_300_GT_0": _gt0(p.get("EX_BEST_DAY_MEAN_300")),
        "DROP_TOP_180_GE_0": _ge0(p.get("DROP_TOP_SYMBOL_MEAN_180")),
        "DROP_TOP_300_GE_0": _ge0(p.get("DROP_TOP_SYMBOL_MEAN_300")),
    }
    return {
        "gates": gates,
        "COVERAGE_PASS": cov,
        "EDGE_PASS": bool(cov) and all(bool(gates[k]) for k in gates if k != "COVERAGE"),
        "PASS": bool(cov) and all(bool(v) for v in gates.values()),
    }


def holdout_gates(p: dict[str, Any]) -> dict[str, Any]:
    n = int(p.get("EXECUTION_EVALUABLE_N") or 0)
    cov = n >= int(HOLDOUT_MIN_EVALUABLE)
    gates = {
        "COVERAGE": cov,
        "MEAN_180_GT_0": _gt0(p.get("MEAN_180")),
        "MEAN_300_GT_0": _gt0(p.get("MEAN_300")),
        "POS_DAY_180_GE_NEG": int(p.get("positive_day_n_180") or 0) >= int(p.get("negative_day_n_180") or 0),
        "POS_DAY_300_GE_NEG": int(p.get("positive_day_n_300") or 0) >= int(p.get("negative_day_n_300") or 0),
        "EX_BEST_180_GE_0": _ge0(p.get("EX_BEST_DAY_MEAN_180")),
        "EX_BEST_300_GE_0": _ge0(p.get("EX_BEST_DAY_MEAN_300")),
        "DROP_TOP_180_GE_0": _ge0(p.get("DROP_TOP_SYMBOL_MEAN_180")),
        "DROP_TOP_300_GE_0": _ge0(p.get("DROP_TOP_SYMBOL_MEAN_300")),
    }
    return {
        "gates": gates,
        "COVERAGE_PASS": cov,
        "PASS": bool(cov) and all(bool(v) for v in gates.values()),
    }


def stress_gates(p: dict[str, Any]) -> dict[str, Any]:
    n = int(p.get("EXECUTION_EVALUABLE_N") or 0)
    cov = n >= int(STRESS_MIN_EVALUABLE)
    gates = {
        "COVERAGE": cov,
        "MEAN_180_GE_0": _ge0(p.get("MEAN_180")),
        "MEAN_300_GE_0": _ge0(p.get("MEAN_300")),
    }
    return {"gates": gates, "COVERAGE_PASS": cov, "PASS": bool(cov) and all(bool(v) for v in gates.values())}


def decay_ratios(dev: dict[str, Any], hold: dict[str, Any]) -> dict[str, Any]:
    def _ratio(a: Any, b: Any) -> Optional[float]:
        if a is None or b is None:
            return None
        bb = float(b)
        if abs(bb) < 1e-18:
            return None
        return float(a) / bb

    return {
        "HOLDOUT_DECAY_RATIO_180": _ratio(hold.get("MEAN_180"), dev.get("MEAN_180")),
        "HOLDOUT_DECAY_RATIO_300": _ratio(hold.get("MEAN_300"), dev.get("MEAN_300")),
    }


def pack_screen(rows: list[dict[str, Any]]) -> dict[str, Any]:
    sig = list(rows)
    q = [r for r in sig if r.get("qty100_ok") and r.get("executable_signal")]
    aq = [float(r["ask_qty"]) for r in q if isinstance(r.get("ask_qty"), (int, float)) and r["ask_qty"] == r["ask_qty"]]
    return {
        "signal_n": len(sig),
        "executable100_n": len(q),
        "execution_coverage": (float(len(q)) / float(len(sig))) if sig else None,
        "MEAN_180": _mean(_vals(q, "markout_180")),
        "MEAN_300": _mean(_vals(q, "markout_300")),
        "spread_bps_mean": _mean(_vals(q, "spread_bps")),
        "ask_qty_median": _median(aq),
        "ask_qty_mean": _mean(aq),
        "rows": q,
    }


def screen_pass(neutral: dict[str, Any], screen: dict[str, Any], *, mean_gt: bool) -> bool:
    for h in (180, 300):
        a = neutral.get(f"MEAN_{h}")
        b = screen.get(f"MEAN_{h}")
        if a is None or b is None:
            return False
        if mean_gt:
            if float(b) <= 0.0:
                return False
        else:
            if float(b) < 0.0:
                return False
        if float(a) > 0 and float(b) <= 0:
            return False
        if float(a) >= 0 and float(b) < 0:
            return False
    return int(screen.get("executable100_n") or 0) > 0


def decide(
    *,
    integrity: bool,
    dev_gate: dict[str, Any] | None,
    hold_gate: dict[str, Any] | None,
    stress_gate: dict[str, Any] | None,
    exec_ok: bool | None,
    holdout_opened: bool,
    stress_opened: bool,
    exec_ran: bool,
) -> dict[str, Any]:
    if not integrity:
        return {
            "CASE": "E",
            "VERDICT": CASE_E,
            "NEXT": "STOP. DO_NOT_INTERPRET_ECONOMICS.",
            "FAMILY_CLOSED": True,
            "ENTRY_BASE_CANDIDATE_FROZEN": False,
            "EXIT_DESIGN_ALLOWED": False,
            "NEW_ENTRY_FAMILY_NEXT": False,
        }
    dg = dev_gate or {}
    if not bool(dg.get("COVERAGE_PASS")):
        return {
            "CASE": "B",
            "VERDICT": CASE_B_COV,
            "NEXT": "CLOSE. Do not open holdout. Next analysis: new family from scratch. No threshold loosening.",
            "FAMILY_CLOSED": True,
            "ENTRY_BASE_CANDIDATE_FROZEN": False,
            "EXIT_DESIGN_ALLOWED": False,
            "NEW_ENTRY_FAMILY_NEXT": True,
        }
    if not bool(dg.get("PASS")):
        return {
            "CASE": "B",
            "VERDICT": CASE_B,
            "NEXT": "CLOSE. Holdout unused. Next analysis: new family from scratch.",
            "FAMILY_CLOSED": True,
            "ENTRY_BASE_CANDIDATE_FROZEN": False,
            "EXIT_DESIGN_ALLOWED": False,
            "NEW_ENTRY_FAMILY_NEXT": True,
        }
    if not holdout_opened:
        return {
            "CASE": "E",
            "VERDICT": CASE_E,
            "NEXT": "STOP. Development PASS but holdout was not opened after freeze.",
            "FAMILY_CLOSED": True,
            "ENTRY_BASE_CANDIDATE_FROZEN": False,
            "EXIT_DESIGN_ALLOWED": False,
            "NEW_ENTRY_FAMILY_NEXT": False,
        }
    hg = hold_gate or {}
    if not bool(hg.get("COVERAGE_PASS")):
        return {
            "CASE": "C",
            "VERDICT": CASE_C_COV,
            "NEXT": "CLOSE. Do not add wick/VWAP-distance/volume. New Analysis ID required for a new hypothesis.",
            "FAMILY_CLOSED": True,
            "ENTRY_BASE_CANDIDATE_FROZEN": False,
            "EXIT_DESIGN_ALLOWED": False,
            "NEW_ENTRY_FAMILY_NEXT": True,
        }
    if not bool(hg.get("PASS")):
        return {
            "CASE": "C",
            "VERDICT": CASE_C,
            "NEXT": "CLOSE. Research-overfit candidate. Rule modification forbidden. New Analysis ID for a new hypothesis.",
            "FAMILY_CLOSED": True,
            "ENTRY_BASE_CANDIDATE_FROZEN": False,
            "EXIT_DESIGN_ALLOWED": False,
            "NEW_ENTRY_FAMILY_NEXT": True,
        }
    if not stress_opened:
        return {
            "CASE": "E",
            "VERDICT": CASE_E,
            "NEXT": "STOP. Holdout PASS but stress was not opened.",
            "FAMILY_CLOSED": True,
            "ENTRY_BASE_CANDIDATE_FROZEN": False,
            "EXIT_DESIGN_ALLOWED": False,
            "NEW_ENTRY_FAMILY_NEXT": False,
        }
    sg = stress_gate or {}
    if not bool(sg.get("PASS")):
        return {
            "CASE": "D",
            "VERDICT": CASE_D,
            "NEXT": "Candidate freeze forbidden. STOP this family.",
            "FAMILY_CLOSED": True,
            "ENTRY_BASE_CANDIDATE_FROZEN": False,
            "EXIT_DESIGN_ALLOWED": False,
            "NEW_ENTRY_FAMILY_NEXT": True,
        }
    if not exec_ran or exec_ok is not True:
        return {
            "CASE": "D",
            "VERDICT": CASE_D_EXEC,
            "NEXT": "Execution screen failed or skipped. Candidate freeze forbidden.",
            "FAMILY_CLOSED": True,
            "ENTRY_BASE_CANDIDATE_FROZEN": False,
            "EXIT_DESIGN_ALLOWED": False,
            "NEW_ENTRY_FAMILY_NEXT": True,
        }
    return {
        "CASE": "A",
        "VERDICT": CASE_A,
        "NEXT": "NEW_VWAP_RECLAIM_EXIT_DESIGN_V1. Not Full Causal yet. Not Sizing.",
        "FAMILY_CLOSED": False,
        "ENTRY_BASE_CANDIDATE_FROZEN": True,
        "EXIT_DESIGN_ALLOWED": True,
        "NEW_ENTRY_FAMILY_NEXT": False,
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    from research.new_entry_vwap_rejection_reclaim_v1 import BREAKOUT_CASE, BREAKOUT_VERDICT

    dev = dict(report.get("development") or {})
    hold = dict(report.get("holdout") or {})
    st = dict(report.get("stress") or {})
    scr = dict(report.get("execution_screen") or {})
    d = dict(report.get("decision") or {})
    pin = dict(report.get("pin") or {})
    ae = dict(report.get("already_executed") or {})
    hold_opened = bool(report.get("HOLDOUT_OPENED"))
    return {
        "1_prior_Breakout_verdict": f"CASE {BREAKOUT_CASE} {BREAKOUT_VERDICT}. HOLDOUT_OPENED=false. BREAKOUT_FAMILY_CLOSED=true.",
        "2_why_Breakout_stays_closed": "Local-high chase had coverage but negative Ask1→Bid edge on all 10 DEVELOPMENT days. Not a retune of 5-bar/10-volume/VWAP-distance. Family stays CLOSED.",
        "3_new_family_concept": "VWAP rejection/reclaim: same completed 1m bar sold through VWAP (Low < VWAP) then closed back above VWAP as a bullish bar (Close > VWAP and Close > Open). Not momentum chase. Not T3/RCI pullback.",
        "4_already_executed_check": ae.get("comparisons") or [],
        "5_duplicate": bool(ae.get("DUPLICATE_ARCHITECTURE")),
        "6_fallback_used": bool(report.get("FALLBACK_USED")),
        "7_exact_ENTRY_rule": EXACT_RULE_TEXT,
        "8_parameter_search": False,
        "9_DEV_days": list(report.get("DEVELOPMENT_DAYS") or []),
        "10_Holdout_days": list(report.get("LOCKED_HOLDOUT_DAYS") or []),
        "11_Stress_days": list(report.get("STRESS_DAYS") or []),
        "12_DEV_signal_n": dev.get("SIGNAL_N"),
        "13_DEV_executable_n": dev.get("EXECUTION_EVALUABLE_N"),
        "14_DEV_mean180_300": {"MEAN_180": dev.get("MEAN_180"), "MEAN_300": dev.get("MEAN_300")},
        "15_DEV_median180_300": {"MEDIAN_180": dev.get("MEDIAN_180"), "MEDIAN_300": dev.get("MEDIAN_300")},
        "16_DEV_day_signs": {
            "180": {"pos": dev.get("positive_day_n_180"), "neg": dev.get("negative_day_n_180"), "zero": dev.get("zero_day_n_180")},
            "300": {"pos": dev.get("positive_day_n_300"), "neg": dev.get("negative_day_n_300"), "zero": dev.get("zero_day_n_300")},
        },
        "17_DEV_ex_best": {"180": dev.get("EX_BEST_DAY_MEAN_180"), "300": dev.get("EX_BEST_DAY_MEAN_300")},
        "18_DEV_drop_top_symbol": {"180": dev.get("DROP_TOP_SYMBOL_MEAN_180"), "300": dev.get("DROP_TOP_SYMBOL_MEAN_300")},
        "19_DEV_gate": (report.get("dev_gates") or {}).get("PASS"),
        "20_rule_frozen": bool(pin.get("ENTRY_RULE_FROZEN")),
        "21_Holdout_opened": hold_opened,
        "22_Holdout_executable_n": hold.get("EXECUTION_EVALUABLE_N") if hold_opened else None,
        "23_Holdout_mean180_300": {"MEAN_180": hold.get("MEAN_180"), "MEAN_300": hold.get("MEAN_300")} if hold_opened else {"MEAN_180": None, "MEAN_300": None},
        "24_Holdout_day_signs": {
            "180": {"pos": hold.get("positive_day_n_180"), "neg": hold.get("negative_day_n_180"), "zero": hold.get("zero_day_n_180")},
            "300": {"pos": hold.get("positive_day_n_300"), "neg": hold.get("negative_day_n_300"), "zero": hold.get("zero_day_n_300")},
        }
        if hold_opened
        else {"180": {"pos": None, "neg": None, "zero": None}, "300": {"pos": None, "neg": None, "zero": None}},
        "25_Holdout_ex_best": {"180": hold.get("EX_BEST_DAY_MEAN_180"), "300": hold.get("EX_BEST_DAY_MEAN_300")} if hold_opened else {"180": None, "300": None},
        "26_Holdout_drop_top_symbol": {"180": hold.get("DROP_TOP_SYMBOL_MEAN_180"), "300": hold.get("DROP_TOP_SYMBOL_MEAN_300")} if hold_opened else {"180": None, "300": None},
        "27_Holdout_gate": (report.get("holdout_gates") or {}).get("PASS") if hold_opened else None,
        "28_Stress_opened": bool(report.get("STRESS_OPENED")),
        "29_Stress_metrics": {
            "EXECUTION_EVALUABLE_N": st.get("EXECUTION_EVALUABLE_N"),
            "MEAN_180": st.get("MEAN_180"),
            "MEAN_300": st.get("MEAN_300"),
            "MEDIAN_180": st.get("MEDIAN_180"),
            "MEDIAN_300": st.get("MEDIAN_300"),
            "day_signs_180": {"pos": st.get("positive_day_n_180"), "neg": st.get("negative_day_n_180")},
            "day_signs_300": {"pos": st.get("positive_day_n_300"), "neg": st.get("negative_day_n_300")},
            "MFE300": st.get("MFE300"),
            "MAE300": st.get("MAE300"),
        }
        if report.get("STRESS_OPENED")
        else None,
        "30_Stress_gate": (report.get("stress_gates") or {}).get("PASS") if report.get("STRESS_OPENED") else None,
        "31_execution_screen": bool(report.get("EXECUTION_SCREEN_RAN")),
        "32_executable100_n": {
            "DEV": (scr.get("DEV") or {}).get("executable100_n"),
            "HOLDOUT": (scr.get("HOLDOUT") or {}).get("executable100_n"),
            "STRESS": (scr.get("STRESS") or {}).get("executable100_n"),
        },
        "33_edge_maintained": bool(report.get("EXECUTION_EDGE_MAINTAINED")),
        "34_ENTRY_BASE_CANDIDATE_FROZEN": bool(d.get("ENTRY_BASE_CANDIDATE_FROZEN")),
        "35_verdict": d.get("VERDICT"),
        "36_family_closed": bool(d.get("FAMILY_CLOSED")),
        "37_EXIT_design_allowed": bool(d.get("EXIT_DESIGN_ALLOWED")),
        "38_Full_Causal_allowed": False,
        "39_Sizing_allowed": False,
        "40_Runtime_changed": False,
        "41_future_used": bool(int((report.get("leakage") or {}).get("FUTURE_DATA_N") or 0)),
        "42_MAX_RESEARCH_DATE": "20260902",
        "43_prospective_suspended": True,
        "44_TRUE_OOS": False,
        "45_CERTIFIED": False,
        "46_submit_cancel_live": "0/0/0",
        "47_next": d.get("NEXT"),
    }
