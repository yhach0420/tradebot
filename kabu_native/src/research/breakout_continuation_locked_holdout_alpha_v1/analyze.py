"""Holdout MID alpha gates. ASK_BID diagnostic only. No threshold search."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.breakout_continuation_locked_holdout_alpha_v1 import (
    DEV_EXPECTED_ASK_BID_180,
    DEV_EXPECTED_ASK_BID_300,
    DEV_EXPECTED_EVALUABLE_N,
    DEV_EXPECTED_MID_180,
    DEV_EXPECTED_MID_300,
    DEV_EXPECTED_MID_60,
    DEV_EXPECTED_SIGNAL_N,
    HORIZONS_SEC,
    LOCKED_HOLDOUT_DAYS,
    MIN_DAILY_EVALUABLE_N,
    MIN_DAYS_WITH_DAILY_N,
    MIN_HOLDOUT_TOTAL_N,
)
from research.breakout_continuation_locked_holdout_alpha_v1.harvest import AUDIT

CASE_A = "BREAKOUT_CONTINUATION_HOLDOUT_ALPHA_SUPPORTED"
CASE_B = "BREAKOUT_CONTINUATION_HOLDOUT_ALPHA_MIXED"
CASE_C = "BREAKOUT_CONTINUATION_HOLDOUT_ALPHA_FAILED"
CASE_D = "BREAKOUT_HOLDOUT_ALPHA_INSUFFICIENT_DATA"
CASE_E = "BREAKOUT_HOLDOUT_ALPHA_INTEGRITY_FAILED"


def _num(v: Any) -> Optional[float]:
    if v is None or not isinstance(v, (int, float)):
        return None
    x = float(v)
    return x if x == x else None


def _mean(xs: list[Any]) -> Optional[float]:
    vs = [x for x in (_num(v) for v in xs) if x is not None]
    if not vs:
        return None
    return float(np.mean(vs))


def _median(xs: list[Any]) -> Optional[float]:
    vs = [x for x in (_num(v) for v in xs) if x is not None]
    if not vs:
        return None
    return float(np.median(vs))


def _pct(xs: list[Any], q: float) -> Optional[float]:
    vs = [x for x in (_num(v) for v in xs) if x is not None]
    if not vs:
        return None
    return float(np.percentile(vs, q))


def _vals(rows: list[dict[str, Any]], key: str) -> list[Any]:
    return [r.get(key) for r in rows]


def trimmed_mean_5p(xs: list[Any]) -> Optional[float]:
    vs = np.sort(np.asarray([x for x in (_num(v) for v in xs) if x is not None], dtype=float))
    n = int(vs.size)
    if n <= 0:
        return None
    k = int(np.floor(n * 0.05))
    if n - 2 * k <= 0:
        return float(np.mean(vs))
    return float(np.mean(vs[k : n - k]))


def winsorized_mean_5p(xs: list[Any]) -> Optional[float]:
    vs = np.asarray([x for x in (_num(v) for v in xs) if x is not None], dtype=float)
    if vs.size <= 0:
        return None
    lo, hi = np.percentile(vs, [5.0, 95.0])
    return float(np.mean(np.clip(vs, lo, hi)))


def leakage_n() -> dict[str, int]:
    keys = (
        "HOLDOUT_READ_BEFORE_PRIMARY_FREEZE_N",
        "STRESS_READ_N",
        "VWAP_HOLDOUT_READ_N",
        "RULE_CHANGE_AFTER_HOLDOUT_OPEN_N",
        "PARAMETER_CHANGE_N",
        "HOLDOUT_USED_FOR_THRESHOLD_N",
        "FUTURE_DATA_N",
        "CURRENT_PRICE_TIME_AS_BOARD_FRESH_N",
        "DEV_PARITY_FAIL_N",
        "SPLIT_LEAKAGE_N",
    )
    return {k: int(AUDIT.get(k) or 0) for k in keys}


def integrity_ok(extra: dict[str, Any] | None = None) -> bool:
    leak = leakage_n()
    if extra:
        for k, v in extra.items():
            leak[k] = int(leak.get(k) or 0) + int(v or 0)
    return all(int(v) == 0 for v in leak.values())


def _day_signs(daily: list[dict[str, Any]], key: str) -> dict[str, int]:
    pos = neg = zero = 0
    for r in daily:
        v = _num(r.get(key))
        if v is None:
            continue
        if v > 1e-12:
            pos += 1
        elif v < -1e-12:
            neg += 1
        else:
            zero += 1
    return {"positive_day_n": pos, "negative_day_n": neg, "zero_day_n": zero}


def _ex_best(rows: list[dict[str, Any]], key: str) -> Optional[float]:
    byd: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        v = _num(r.get(key))
        if v is None:
            continue
        byd[str(r.get("date") or "")].append(v)
    if not byd:
        return None
    day_mean = {d: float(np.mean(vs)) for d, vs in byd.items()}
    best = max(day_mean.keys(), key=lambda d: day_mean[d])
    rest = [v for d, vs in byd.items() if d != best for v in vs]
    return _mean(rest)


def _drop_top(rows: list[dict[str, Any]], key: str) -> Optional[float]:
    counts: dict[str, int] = defaultdict(int)
    for r in rows:
        counts[str(r.get("symbol") or "")] += 1
    if not counts:
        return None
    top = max(counts.keys(), key=lambda s: (counts[s], s))
    rest = [r.get(key) for r in rows if str(r.get("symbol") or "") != top]
    return _mean(rest)


def _obs_mix(xs: list[Any]) -> dict[str, Any]:
    vs = [x for x in (_num(v) for v in xs) if x is not None]
    n = len(vs)
    if n == 0:
        return {"positive_pct": None, "negative_pct": None, "zero_pct": None, "n": 0}
    pos = sum(1 for v in vs if v > 1e-12)
    neg = sum(1 for v in vs if v < -1e-12)
    zero = n - pos - neg
    return {
        "n": n,
        "positive_pct": 100.0 * pos / n,
        "negative_pct": 100.0 * neg / n,
        "zero_pct": 100.0 * zero / n,
    }


def pack_pop(rows: list[dict[str, Any]], *, days: list[str], label: str, signal_n: int | None = None) -> dict[str, Any]:
    all_rows = list(rows)
    exe = [r for r in all_rows if r.get("eligible")]
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in exe:
        by[str(r.get("date") or "")].append(r)
    daily = []
    for d in days:
        xs = by.get(d) or []
        rec = {"date": d, "N": len(xs), "DAILY_EVALUABLE_N": len(xs)}
        for h in HORIZONS_SEC:
            rec[f"MEAN_MID_{h}"] = _mean(_vals(xs, f"MID_{h}"))
            rec[f"MEAN_ASK_BID_{h}"] = _mean(_vals(xs, f"ASK_BID_{h}"))
        daily.append(rec)
    sp = _vals(exe, "spread0")
    sym_means_180: dict[str, float] = {}
    bys: dict[str, list[float]] = defaultdict(list)
    for r in exe:
        v = _num(r.get("MID_180"))
        if v is not None:
            bys[str(r.get("symbol") or "")].append(v)
    for s, vs in bys.items():
        if vs:
            sym_means_180[s] = float(np.mean(vs))
    pos_sym = sum(1 for v in sym_means_180.values() if v > 1e-12)
    neg_sym = sum(1 for v in sym_means_180.values() if v < -1e-12)
    n_sym = len(sym_means_180)
    out: dict[str, Any] = {
        "label": label,
        "SIGNAL_N": int(signal_n) if signal_n is not None else len(all_rows),
        "EVALUABLE_N": len(exe),
        "N": len(exe),
        "daily": daily,
        "SPREAD0_MEAN": _mean(sp),
        "SPREAD0_MEDIAN": _median(sp),
        "positive_symbol_n": pos_sym,
        "negative_symbol_n": neg_sym,
        "positive_symbol_fraction": (float(pos_sym) / float(n_sym)) if n_sym else None,
    }
    for h in HORIZONS_SEC:
        mids = _vals(exe, f"MID_{h}")
        out[f"MEAN_MID_{h}"] = _mean(mids)
        out[f"MEDIAN_MID_{h}"] = _median(mids)
        out[f"MEAN_ASK_MID_{h}"] = _mean(_vals(exe, f"ASK_MID_{h}"))
        out[f"MEAN_ASK_BID_{h}"] = _mean(_vals(exe, f"ASK_BID_{h}"))
        out[f"MEDIAN_ASK_BID_{h}"] = _median(_vals(exe, f"ASK_BID_{h}"))
        out[f"MID_DAY_SIGNS_{h}"] = _day_signs(daily, f"MEAN_MID_{h}")
        out[f"EX_BEST_MID_{h}"] = _ex_best(exe, f"MID_{h}")
        out[f"DROP_TOP_MID_{h}"] = _drop_top(exe, f"MID_{h}")
        out[f"TRIMMED_MEAN_5P_{h}"] = trimmed_mean_5p(mids)
        out[f"WINSORIZED_MEAN_5P_{h}"] = winsorized_mean_5p(mids)
        mix = _obs_mix(mids)
        out[f"OBS_MIX_{h}"] = mix
        out[f"P10_MID_{h}"] = _pct(mids, 10)
        out[f"P25_MID_{h}"] = _pct(mids, 25)
        out[f"P50_MID_{h}"] = _pct(mids, 50)
        out[f"P75_MID_{h}"] = _pct(mids, 75)
        out[f"P90_MID_{h}"] = _pct(mids, 90)
    tot = float(sum(_num(r.get("MID_180")) or 0.0 for r in exe if _num(r.get("MID_180")) is not None))
    by_day_c: dict[str, float] = defaultdict(float)
    by_sym_c: dict[str, float] = defaultdict(float)
    for r in exe:
        v = _num(r.get("MID_180"))
        if v is None:
            continue
        by_day_c[str(r.get("date") or "")] += v
        by_sym_c[str(r.get("symbol") or "")] += v
    if by_day_c and abs(tot) > 1e-12:
        top_d = max(by_day_c.keys(), key=lambda d: by_day_c[d])
        out["top_day_180"] = top_d
        out["top_day_contribution_180"] = by_day_c[top_d] / tot
    if by_sym_c and abs(tot) > 1e-12:
        top_s = max(by_sym_c.keys(), key=lambda s: by_sym_c[s])
        out["top_symbol_180"] = top_s
        out["top_symbol_contribution_180"] = by_sym_c[top_s] / tot
    return out


def coverage_gates(hold: dict[str, Any], day_ok: dict[str, bool]) -> dict[str, Any]:
    daily = list(hold.get("daily") or [])
    n = int(hold.get("EVALUABLE_N") or 0)
    days_ge = sum(1 for r in daily if int(r.get("DAILY_EVALUABLE_N") or 0) >= int(MIN_DAILY_EVALUABLE_N))
    all8 = all(bool(day_ok.get(d)) for d in LOCKED_HOLDOUT_DAYS) and len(day_ok) == 8
    gates = {
        "MIN_HOLDOUT_TOTAL_N": n >= int(MIN_HOLDOUT_TOTAL_N),
        "ALL_8_HOLDOUT_DAYS_INTEGRITY_PASS": bool(all8),
        "DAYS_GE_25_GE_6": days_ge >= int(MIN_DAYS_WITH_DAILY_N),
    }
    return {
        "gates": gates,
        "EVALUABLE_N": n,
        "days_ge_25": days_ge,
        "PASS": all(bool(v) for v in gates.values()),
    }


def _gt0(v: Any) -> bool:
    return v is not None and float(v) > 0.0


def _ge0(v: Any) -> bool:
    return v is not None and float(v) >= 0.0


def alpha_gates(hold: dict[str, Any], uncond: dict[str, Any]) -> dict[str, Any]:
    lift180 = None
    lift300 = None
    if hold.get("MEAN_MID_180") is not None and uncond.get("MEAN_MID_180") is not None:
        lift180 = float(hold["MEAN_MID_180"]) - float(uncond["MEAN_MID_180"])
    if hold.get("MEAN_MID_300") is not None and uncond.get("MEAN_MID_300") is not None:
        lift300 = float(hold["MEAN_MID_300"]) - float(uncond["MEAN_MID_300"])
    s180 = dict(hold.get("MID_DAY_SIGNS_180") or {})
    s300 = dict(hold.get("MID_DAY_SIGNS_300") or {})
    g = {
        "G1_MID180_GT_0": _gt0(hold.get("MEAN_MID_180")),
        "G2_MID300_GT_0": _gt0(hold.get("MEAN_MID_300")),
        "G3_POS_DAY_180_GE_NEG": int(s180.get("positive_day_n") or 0) >= int(s180.get("negative_day_n") or 0),
        "G4_POS_DAY_300_GE_NEG": int(s300.get("positive_day_n") or 0) >= int(s300.get("negative_day_n") or 0),
        "G5_EX_BEST_180_GE_0": _ge0(hold.get("EX_BEST_MID_180")),
        "G6_EX_BEST_300_GE_0": _ge0(hold.get("EX_BEST_MID_300")),
        "G7_DROP_TOP_180_GE_0": _ge0(hold.get("DROP_TOP_MID_180")),
        "G8_DROP_TOP_300_GE_0": _ge0(hold.get("DROP_TOP_MID_300")),
        "G9_LIFT180_GT_0": _gt0(lift180),
        "G10_LIFT300_GT_0": _gt0(lift300),
        "G11_TRIM5_180_GE_0": _ge0(hold.get("TRIMMED_MEAN_5P_180")),
        "G12_TRIM5_300_GE_0": _ge0(hold.get("TRIMMED_MEAN_5P_300")),
        "G13_WINSOR5_180_GE_0": _ge0(hold.get("WINSORIZED_MEAN_5P_180")),
        "G14_WINSOR5_300_GE_0": _ge0(hold.get("WINSORIZED_MEAN_5P_300")),
    }
    tail = not all(bool(g[k]) for k in ("G11_TRIM5_180_GE_0", "G12_TRIM5_300_GE_0", "G13_WINSOR5_180_GE_0", "G14_WINSOR5_300_GE_0"))
    return {
        "gates": g,
        "LIFT180": lift180,
        "LIFT300": lift300,
        "TAIL_FRAGILE": bool(tail),
        "PASS": all(bool(v) for v in g.values()) and (not tail),
    }


def dev_parity(dev: dict[str, Any]) -> bool:
    checks = (
        (int(dev.get("SIGNAL_N") or -1), DEV_EXPECTED_SIGNAL_N),
        (int(dev.get("EVALUABLE_N") or -1), DEV_EXPECTED_EVALUABLE_N),
        (dev.get("MEAN_MID_60"), DEV_EXPECTED_MID_60),
        (dev.get("MEAN_MID_180"), DEV_EXPECTED_MID_180),
        (dev.get("MEAN_MID_300"), DEV_EXPECTED_MID_300),
        (dev.get("MEAN_ASK_BID_180"), DEV_EXPECTED_ASK_BID_180),
        (dev.get("MEAN_ASK_BID_300"), DEV_EXPECTED_ASK_BID_300),
    )
    ok = True
    for got, exp in checks:
        if isinstance(exp, int):
            if int(got) != int(exp):
                ok = False
        else:
            if got is None or abs(float(got) - float(exp)) > 1e-9:
                ok = False
    if not ok:
        AUDIT["DEV_PARITY_FAIL_N"] += 1
    return ok


def decide(
    *,
    integrity: bool,
    coverage: dict[str, Any] | None,
    alpha: dict[str, Any] | None,
    hold: dict[str, Any] | None,
) -> dict[str, Any]:
    base = {
        "PROFIT_CANDIDATE": False,
        "EXECUTION_CANDIDATE": False,
        "CERTIFIED": False,
        "VWAP_HOLDOUT_TEST_ALLOWED": False,
        "LOCKED_HOLDOUT_BURNED_FOR_BREAKOUT": True,
        "REVIVE_VWAP": False,
    }
    if not integrity:
        return {
            **base,
            "CASE": "E",
            "VERDICT": CASE_E,
            "NEXT": "STOP. DO_NOT_INTERPRET_ECONOMICS.",
            "PREDICTIVE_ALPHA_SUPPORTED": False,
            "BREAKOUT_PERMANENTLY_CLOSED": False,
        }
    cov = coverage or {}
    if not bool(cov.get("PASS")):
        return {
            **base,
            "CASE": "D",
            "VERDICT": CASE_D,
            "NEXT": "Insufficient Holdout data. Do not hop to a new family.",
            "PREDICTIVE_ALPHA_SUPPORTED": False,
            "BREAKOUT_PERMANENTLY_CLOSED": False,
        }
    h = hold or {}
    a = alpha or {}
    g = dict(a.get("gates") or {})
    mid_fail = (h.get("MEAN_MID_180") is not None and float(h["MEAN_MID_180"]) <= 0.0) or (
        h.get("MEAN_MID_300") is not None and float(h["MEAN_MID_300"]) <= 0.0
    )
    lift180 = a.get("LIFT180")
    lift300 = a.get("LIFT300")
    lift_both_fail = lift180 is not None and lift300 is not None and float(lift180) <= 0.0 and float(lift300) <= 0.0
    if mid_fail or lift_both_fail:
        return {
            **base,
            "CASE": "C",
            "VERDICT": CASE_C,
            "NEXT": "FAILED_BREAKDOWN_RECLAIM in a new Analysis ID from Development. Same Holdout 8 days burned for Breakout. Do not hop VWAP onto these days.",
            "PREDICTIVE_ALPHA_SUPPORTED": False,
            "BREAKOUT_PERMANENTLY_CLOSED": True,
        }
    if bool(a.get("PASS")) and not bool(a.get("TAIL_FRAGILE")):
        return {
            **base,
            "CASE": "A",
            "VERDICT": CASE_A,
            "NEXT": "BREAKOUT_REUSED_HISTORY_STRESS_ALPHA_V1. Do not open execution/EXIT yet.",
            "PREDICTIVE_ALPHA_SUPPORTED": True,
            "BREAKOUT_PERMANENTLY_CLOSED": False,
        }
    failed = [k for k, v in g.items() if not bool(v)]
    return {
        **base,
        "CASE": "B",
        "VERDICT": CASE_B,
        "NEXT": "BREAKOUT_ALPHA_FAILURE_CHARACTERIZATION_V1 once only. No threshold tuning. No VWAP hop.",
        "PREDICTIVE_ALPHA_SUPPORTED": False,
        "BREAKOUT_PERMANENTLY_CLOSED": False,
        "FAILED_GATES": failed,
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    from research.new_entry_breakout_continuation_v1.rule import EXACT_RULE_TEXT

    d = dict(report.get("decision") or {})
    pin = dict(report.get("pin") or {})
    dev = dict(report.get("development") or {})
    hold = dict(report.get("holdout") or {})
    unc = dict(report.get("unconditional") or {})
    cov = dict(report.get("coverage") or {})
    ag = dict(report.get("alpha_gates") or {})
    return {
        "1_old_measurement_correction": "NEW_ENTRY_BREAKOUT_CONTINUATION_DEV_FAILED is IMMEDIATE_CROSS_EXECUTION_DEV_FAILED=true. PREDICTIVE_ALPHA_DEV_FAILED=false. OLD_SIGNAL_ALPHA_FAILURE_INTERPRETATION_SUPERSEDED=true. MID_IS_NOT_TRADABLE_PNL=true.",
        "2_why_minus_20bps_did_not_prove_no_signal_alpha": "ASK_BID ≈ MID − entry half-spread − exit half-spread (residual ~0.05bps). DEV MID180/300 were positive while ASK_BID was −spread. Immediate-cross cost is not signal-alpha failure.",
        "3_PRIMARY_family": "BREAKOUT_CONTINUATION",
        "4_Primary_selected_before_Holdout": bool(pin.get("PRIMARY_SELECTION_FROZEN_BEFORE_HOLDOUT")),
        "5_why_Breakout_selected": "Development MID only: MID180=+3.7448 MID300=+3.0048, day signs 7/3 and 6/4, EX_BEST and DROP_TOP positive. VWAP_RECLAIM SECONDARY_NOT_OPENED.",
        "6_VWAP_Holdout_opened": False,
        "7_exact_ENTRY_rule": EXACT_RULE_TEXT,
        "8_parameter_change_n": 0,
        "9_DEV_parity": bool(report.get("DEV_PARITY_OK")),
        "10_DEV_MID60_180_300": {
            "60": dev.get("MEAN_MID_60"),
            "180": dev.get("MEAN_MID_180"),
            "300": dev.get("MEAN_MID_300"),
        },
        "11_Holdout_days": list(LOCKED_HOLDOUT_DAYS),
        "12_all_8_integrity_pass": (cov.get("gates") or {}).get("ALL_8_HOLDOUT_DAYS_INTEGRITY_PASS"),
        "13_Holdout_total_evaluable_n": hold.get("EVALUABLE_N"),
        "14_daily_coverage_gate": {
            "days_ge_25": cov.get("days_ge_25"),
            "PASS": (cov.get("gates") or {}).get("DAYS_GE_25_GE_6"),
        },
        "15_MID60": hold.get("MEAN_MID_60"),
        "16_MID180": hold.get("MEAN_MID_180"),
        "17_MID300": hold.get("MEAN_MID_300"),
        "18_median180": hold.get("MEDIAN_MID_180"),
        "19_median300": hold.get("MEDIAN_MID_300"),
        "20_day_signs180": hold.get("MID_DAY_SIGNS_180"),
        "21_day_signs300": hold.get("MID_DAY_SIGNS_300"),
        "22_EX_BEST180_300": {"180": hold.get("EX_BEST_MID_180"), "300": hold.get("EX_BEST_MID_300")},
        "23_DROP_TOP_SYMBOL180_300": {"180": hold.get("DROP_TOP_MID_180"), "300": hold.get("DROP_TOP_MID_300")},
        "24_unconditional_MID180_300": {"180": unc.get("MEAN_MID_180"), "300": unc.get("MEAN_MID_300")},
        "25_LIFT180_300": {"180": ag.get("LIFT180"), "300": ag.get("LIFT300")},
        "26_trimmed5pct_180_300": {"180": hold.get("TRIMMED_MEAN_5P_180"), "300": hold.get("TRIMMED_MEAN_5P_300")},
        "27_winsorized5pct_180_300": {"180": hold.get("WINSORIZED_MEAN_5P_180"), "300": hold.get("WINSORIZED_MEAN_5P_300")},
        "28_TAIL_FRAGILE": ag.get("TAIL_FRAGILE"),
        "29_positive_observation_pct": {
            "180": (hold.get("OBS_MIX_180") or {}).get("positive_pct"),
            "300": (hold.get("OBS_MIX_300") or {}).get("positive_pct"),
        },
        "30_positive_symbol_fraction": hold.get("positive_symbol_fraction"),
        "31_Holdout_DEV_ratio180": report.get("RATIO180"),
        "32_Holdout_DEV_ratio300": report.get("RATIO300"),
        "33_ASK_BID180_300_diagnostic": {"180": hold.get("MEAN_ASK_BID_180"), "300": hold.get("MEAN_ASK_BID_300")},
        "34_spread_mean_median_diagnostic": {"mean": hold.get("SPREAD0_MEAN"), "median": hold.get("SPREAD0_MEDIAN")},
        "35_G1_G14_table": ag.get("gates"),
        "36_Holdout_gate": bool(cov.get("PASS")) and bool(ag.get("PASS")),
        "37_Stress_read": bool(int((report.get("leakage") or {}).get("STRESS_READ_N") or 0)),
        "38_VWAP_read": bool(int((report.get("leakage") or {}).get("VWAP_HOLDOUT_READ_N") or 0)),
        "39_Execution_design_ran": False,
        "40_EXIT_design_ran": False,
        "41_Full_Causal_ran": False,
        "42_Sizing_ran": False,
        "43_verdict": d.get("VERDICT"),
        "44_predictive_alpha_supported": bool(d.get("PREDICTIVE_ALPHA_SUPPORTED")),
        "45_profit_candidate": False,
        "46_execution_candidate": False,
        "47_next": d.get("NEXT"),
        "48_Runtime_changed": False,
        "49_future_used": bool(int((report.get("leakage") or {}).get("FUTURE_DATA_N") or 0)),
        "50_MAX_RESEARCH_DATE": "20260827",
        "51_TRUE_OOS": False,
        "52_CERTIFIED": False,
        "53_prospective_suspended": True,
        "54_submit_cancel_live": "0/0/0",
    }
