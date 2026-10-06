"""V23 stale class distributions, day/symbol coverage, CASE A–D. No threshold search. No PnL."""
from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np

from research.simple_tech_redesign.v23_spec import (
    AVOIDABLE_CLASSES,
    CASE_A_AVOIDABLE_MAX,
    CASE_A_REAL_FRAC,
    CASE_B_AVOIDABLE_FRAC,
    CASE_B_MIN_DAYS,
    CASE_B_MIN_SYMBOLS,
    CASE_D_UNRESOLVED_FRAC,
    REAL_CLASS,
    STALE_CLASSES,
    STALE_N_EXPECTED,
)


def _finite(v: Any) -> bool:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return False
    return x == x


def dist(xs: list[Any]) -> dict[str, Any]:
    vals = [float(x) for x in xs if _finite(x)]
    missing_n = int(len(xs) - len(vals))
    if not vals:
        return {
            "n": 0,
            "missing_n": missing_n,
            "min": None,
            "p25": None,
            "median": None,
            "p75": None,
            "p90": None,
            "max": None,
        }
    a = np.asarray(vals, dtype=float)
    return {
        "n": int(a.size),
        "missing_n": missing_n,
        "min": float(np.min(a)),
        "p25": float(np.percentile(a, 25)),
        "median": float(np.median(a)),
        "p75": float(np.percentile(a, 75)),
        "p90": float(np.percentile(a, 90)),
        "max": float(np.max(a)),
    }


def class_coverage(rows: list[dict[str, Any]], cls: str) -> dict[str, Any]:
    sub = [r for r in rows if str(r.get("stale_class") or "") == cls]
    days = [str(r.get("date") or "") for r in sub]
    syms = [str(r.get("symbol") or "") for r in sub]
    day_c = Counter(days)
    sym_c = Counter(syms)
    top_day, top_day_n = (day_c.most_common(1)[0] if day_c else ("", 0))
    top_sym, top_sym_n = (sym_c.most_common(1)[0] if sym_c else ("", 0))
    n = len(sub)
    return {
        "class": cls,
        "N": n,
        "distinct_days": int(len(set(days))),
        "distinct_symbols": int(len(set(syms))),
        "top_day": top_day or None,
        "top_day_n": int(top_day_n),
        "top_day_share": (float(top_day_n) / float(n)) if n else None,
        "top_symbol": top_sym or None,
        "top_symbol_n": int(top_sym_n),
        "top_symbol_share": (float(top_sym_n) / float(n)) if n else None,
        "reason_top": Counter(str(r.get("reason") or "") for r in sub).most_common(5),
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    counts = Counter(str(r.get("stale_class") or "S6_UNRESOLVED") for r in rows)
    class_counts = {c: int(counts.get(c, 0)) for c in STALE_CLASSES}
    cov = {c: class_coverage(rows, c) for c in STALE_CLASSES}
    day_counts = {c: cov[c]["distinct_days"] for c in STALE_CLASSES}
    symbol_counts = {c: cov[c]["distinct_symbols"] for c in STALE_CLASSES}
    real_n = int(class_counts.get(REAL_CLASS, 0))
    avoidable_n = int(sum(class_counts.get(c, 0) for c in AVOIDABLE_CLASSES))
    unresolved_n = int(class_counts.get("S6_UNRESOLVED", 0))
    other_n = int(class_counts.get("S5_OTHER_PROVEN", 0))
    real_frac = (float(real_n) / float(n)) if n else 0.0
    avoidable_frac = (float(avoidable_n) / float(n)) if n else 0.0
    unresolved_frac = (float(unresolved_n) / float(n)) if n else 0.0
    avoid_rows = [r for r in rows if str(r.get("stale_class") or "") in AVOIDABLE_CLASSES]
    avoid_days = {str(r.get("date") or "") for r in avoid_rows}
    avoid_syms = {str(r.get("symbol") or "") for r in avoid_rows}
    replay_stale_n = sum(1 for r in rows if bool(r.get("replay_stale_by_fresh")))
    v22_stale_n = sum(1 for r in rows if str(r.get("v22_ask_reason") or "") == "STALE")
    identity_ok = n == int(STALE_N_EXPECTED) and replay_stale_n == n and v22_stale_n == n
    event_age = dist([r.get("canonical_fresh_sec") for r in rows])
    return {
        "STALE_N": n,
        "STALE_CLASS_COUNTS": class_counts,
        "STALE_CLASS_DAY_COUNTS": day_counts,
        "STALE_CLASS_SYMBOL_COUNTS": symbol_counts,
        "STALE_CLASS_COVERAGE": cov,
        "EVENT_TIME_AGE_DISTRIBUTION": {
            "canonical_fresh_sec": event_age,
            "price_age_event_sec": dist([r.get("price_age_event_sec") for r in rows]),
            "board_age_event_sec": dist([r.get("board_age_event_sec") for r in rows]),
        },
        "RECEIVED_TIME_AGE_DISTRIBUTION": {
            "price_age_received_sec": dist([r.get("price_age_received_sec") for r in rows]),
            "board_age_received_sec": dist([r.get("board_age_received_sec") for r in rows]),
            "last_ingress_age_sec": dist([r.get("last_ingress_age_sec") for r in rows]),
            "last_push_age_sec": dist([r.get("last_push_age_sec") for r in rows]),
        },
        "TIME_TO_NEXT_FRESH_QUOTE": dist([r.get("time_to_next_fresh_quote_sec") for r in rows]),
        "REAL_MARKET_STALE_N": real_n,
        "AVOIDABLE_TIMESTAMP_OR_JOIN_STALE_N": avoidable_n,
        "UNRESOLVED_N": unresolved_n,
        "OTHER_PROVEN_N": other_n,
        "REAL_FRAC": real_frac,
        "AVOIDABLE_FRAC": avoidable_frac,
        "UNRESOLVED_FRAC": unresolved_frac,
        "AVOIDABLE_DISTINCT_DAYS": int(len(avoid_days)),
        "AVOIDABLE_DISTINCT_SYMBOLS": int(len(avoid_syms)),
        "IDENTITY_OK": bool(identity_ok),
        "REPLAY_STALE_N": int(replay_stale_n),
        "canonical_fresh_source_counts": dict(Counter(str(r.get("canonical_fresh_source") or "") for r in rows)),
        "event_stamp_kind_counts": dict(Counter(str(r.get("event_stamp_kind") or "") for r in rows)),
    }


def decide(summary: dict[str, Any], *, identity_ok: bool, leak_ok: bool) -> dict[str, Any]:
    n = int(summary.get("STALE_N") or 0)
    real_frac = float(summary.get("REAL_FRAC") or 0.0)
    avoidable_frac = float(summary.get("AVOIDABLE_FRAC") or 0.0)
    unresolved_frac = float(summary.get("UNRESOLVED_FRAC") or 0.0)
    avoid_days = int(summary.get("AVOIDABLE_DISTINCT_DAYS") or 0)
    avoid_syms = int(summary.get("AVOIDABLE_DISTINCT_SYMBOLS") or 0)
    q1 = bool(real_frac >= 0.5)
    q2 = bool(avoidable_frac >= float(CASE_B_AVOIDABLE_FRAC))
    q3 = bool(avoid_days >= int(CASE_B_MIN_DAYS) and avoid_syms >= int(CASE_B_MIN_SYMBOLS))
    case = "C"
    verdict = "SIMPLE_TECH_V23_STALE_COVERAGE_MIXED"
    next_step = (
        "Stale cause is mixed. Do not add the 149 as trades. Do not change ENTRY/E4/freshness. "
        "Do not start technical EXIT redesign until the dominant stale cause is closed."
    )
    if (not identity_ok) or (not leak_ok) or n != int(STALE_N_EXPECTED) or unresolved_frac >= float(CASE_D_UNRESOLVED_FRAC):
        case = "D"
        verdict = "SIMPLE_TECH_V23_STALE_CAUSE_UNRESOLVED"
        next_step = "STOP. Stale cause unresolved. Do not change strategy rules. Do not start EXIT redesign."
    elif real_frac >= float(CASE_A_REAL_FRAC) and avoidable_frac < float(CASE_A_AVOIDABLE_MAX):
        case = "A"
        verdict = "SIMPLE_TECH_V23_STALE_COVERAGE_GENUINELY_UNTRADEABLE"
        next_step = (
            "ENTRY coverage line close. Accept 38 E4 fills as the current executable baseline. "
            "Do not add the 149 as new trades. Technical EXIT redesign may proceed next."
        )
    elif avoidable_frac >= float(CASE_B_AVOIDABLE_FRAC) and q3:
        case = "B"
        verdict = "SIMPLE_TECH_V23_AVOIDABLE_STALE_COVERAGE_FOUND"
        next_step = (
            "Strategy rules unchanged. Next run is execution-evidence pipeline only "
            "(timestamp/join semantics). No ENTRY/E4/freshness-threshold change in that run's strategy layer."
        )
    primary = "MIXED"
    real_n = int(summary.get("REAL_MARKET_STALE_N") or 0)
    avoid_n = int(summary.get("AVOIDABLE_TIMESTAMP_OR_JOIN_STALE_N") or 0)
    unresolved_n = int(summary.get("UNRESOLVED_N") or 0)
    if case == "A":
        primary = "REAL_MARKET_STALE"
    elif case == "B":
        primary = "DATA_TIMESTAMP_OR_JOIN_STALE"
    elif case == "D":
        primary = "UNRESOLVED"
    elif avoid_n > real_n and avoid_n > unresolved_n:
        primary = "DATA_TIMESTAMP_OR_JOIN_STALE"
    elif real_n > avoid_n and real_n > unresolved_n:
        primary = "REAL_MARKET_STALE"
    elif unresolved_n > real_n and unresolved_n > avoid_n:
        primary = "UNRESOLVED"
    return {
        "CASE": case,
        "VERDICT": verdict,
        "NEXT": next_step,
        "PRIMARY_STALE_CAUSE": primary,
        "Q1_MAJORITY_REAL_MARKET_STALE": q1,
        "Q2_AVOIDABLE_TIMESTAMP_OR_JOIN_MATERIAL": q2,
        "Q3_AVOIDABLE_MULTI_DAY_MULTI_SYMBOL": q3,
        "Q1": q1,
        "Q2": q2,
        "Q3": q3,
        "ENTRY_CHANGED": False,
        "E4_CHANGED": False,
        "FRESHNESS_THRESHOLD_CHANGED": False,
        "TRUE_OOS": False,
        "NEW_TRADE_FROM_STALE_N": 0,
    }
