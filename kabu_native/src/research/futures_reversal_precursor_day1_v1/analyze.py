"""Evaluate frozen precursors. No threshold search. No Day2 primary rewrite."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.futures_reversal_precursor_day1_v1 import (
    ANALYSIS_ID,
    COMBINATIONS,
    DAY2_PRIMARY,
    DAY2_PRIMARY_ID,
    KIND,
    NEXT_A,
    NEXT_B,
    NEXT_C,
    ORIGINAL_FIVE,
    PARENT_ID,
    PRIMARY_PRECURSORS,
    TRADING_DATE,
    VERDICT_A,
    VERDICT_B,
    VERDICT_C,
)
from research.futures_reversal_precursor_day1_v1.engine import build_both_down_grid
from research.futures_reversal_precursor_day1_v1.isolation import NATIVE
from research.new_causal_information_acquisition_v1.launcher import live_order_counts

JST = ZoneInfo("Asia/Tokyo")
KEYS = PRIMARY_PRECURSORS + COMBINATIONS
MATERIAL_LONG_BPS = 5.0


def _mean(xs: list[Optional[float]]) -> Optional[float]:
    ys = [float(x) for x in xs if x is not None]
    return (sum(ys) / len(ys)) if ys else None


def _median(xs: list[Optional[float]]) -> Optional[float]:
    ys = sorted(float(x) for x in xs if x is not None)
    if not ys:
        return None
    n = len(ys)
    if n % 2:
        return ys[n // 2]
    return 0.5 * (ys[n // 2 - 1] + ys[n // 2])


def _top_long(row: dict[str, Any]) -> Optional[float]:
    top = row.get("top") or {}
    pack = top.get("10m") or {}
    v = pack.get("LONG_mean")
    return None if v is None else float(v)


def _top_long_med(row: dict[str, Any]) -> Optional[float]:
    top = row.get("top") or {}
    pack = top.get("10m") or {}
    v = pack.get("LONG_median")
    return None if v is None else float(v)


def _rate(xs: list[bool]) -> Optional[float]:
    return (sum(1 for v in xs if v) / len(xs)) if xs else None


def eval_key(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    true = [r for r in rows if r.get(key) is True]
    false = [r for r in rows if r.get(key) is False]
    t_r1 = _rate([bool(r["REVERSAL_BY_1M"]) for r in true])
    f_r1 = _rate([bool(r["REVERSAL_BY_1M"]) for r in false])
    t_r3 = _rate([bool(r["REVERSAL_BY_3M"]) for r in true])
    f_r3 = _rate([bool(r["REVERSAL_BY_3M"]) for r in false])
    t_long = [_top_long(r) for r in true]
    f_long = [_top_long(r) for r in false]
    t_lm = _mean(t_long)
    f_lm = _mean(f_long)
    rd3 = None if t_r3 is None or f_r3 is None else float(t_r3) - float(f_r3)
    rr3 = None if t_r3 is None or f_r3 in (None, 0) else float(t_r3) / float(f_r3)
    lift = None if t_lm is None or f_lm is None else float(t_lm) - float(f_lm)
    return {
        "key": key,
        "n_true": len(true),
        "n_false": len(false),
        "prevalence": (len(true) / (len(true) + len(false))) if (true or false) else None,
        "rev1_true": t_r1,
        "rev1_false": f_r1,
        "rev3_true": t_r3,
        "rev3_false": f_r3,
        "risk_difference_3m": rd3,
        "risk_ratio_3m": rr3,
        "top_long_true_mean": t_lm,
        "top_long_true_median": _median(t_long),
        "top_long_false_mean": f_lm,
        "top_long_false_median": _median(f_long),
        "executable_lift": lift,
        "rev3_separates": bool(rd3 is not None and rd3 > 0 and t_r3 is not None and t_r3 > 0),
        "long_improves": bool(lift is not None and lift > 0),
        "false_long_weak": bool(f_lm is not None and (f_lm <= 0 or (lift is not None and lift >= MATERIAL_LONG_BPS))),
        "true_long_positive": bool(t_lm is not None and t_lm > 0),
    }


def _episode_rows(episodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e["anchor"] for e in episodes]


def _five_table(minutes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by = {r["clock"]: r for r in minutes}
    rows = []
    for lab in ORIGINAL_FIVE:
        r = by.get(lab)
        if r is None:
            rows.append({"clock": lab, "in_both_down_grid": False})
            continue
        rows.append(
            {
                "clock": lab,
                "in_both_down_grid": True,
                "P1": r.get("P1"),
                "P2": r.get("P2"),
                "P3": r.get("P3"),
                "P4": r.get("P4"),
                "C1": r.get("C1"),
                "C2": r.get("C2"),
                "short_rebound_class": r.get("short_rebound_class"),
                "activity_relative_state": r.get("activity_relative_state"),
                "REVERSAL_BY_1M": r.get("REVERSAL_BY_1M"),
                "REVERSAL_BY_3M": r.get("REVERSAL_BY_3M"),
                "TOP_LONG_10m": _top_long(r),
                "TOP_LONG_10m_median": _top_long_med(r),
            }
        )
    return rows


def _pattern(five: list[dict[str, Any]], key: str) -> dict[str, Any]:
    by = {r["clock"]: r for r in five if r.get("in_both_down_grid")}
    want_true = ("09:20", "10:20")
    want_false = ("09:40", "10:40")
    mid = "10:30"
    picks = all(by.get(c, {}).get(key) is True for c in want_true)
    avoids = all(by.get(c, {}).get(key) is False for c in want_false)
    dist_1030 = by.get(mid, {}).get(key) is False if mid in by else None
    return {
        "picks_0920_1020": picks,
        "avoids_0940_1040": avoids,
        "separates_continued": bool(picks and avoids),
        "distinguishes_1030": dist_1030,
        "states": {c: by.get(c, {}).get(key) for c in ORIGINAL_FIVE},
    }


def _full_sequence(minute_row: dict[str, Any], episode_row: dict[str, Any], five_pat: dict[str, Any]) -> dict[str, Any]:
    a = bool(minute_row.get("rev3_separates"))
    b = bool(minute_row.get("long_improves") and minute_row.get("true_long_positive"))
    c = bool(minute_row.get("false_long_weak"))
    d = bool(episode_row.get("rev3_separates") and episode_row.get("long_improves"))
    sep = bool(five_pat.get("separates_continued"))
    return {
        "A_reversal_rate_higher": a,
        "B_true_top_long_improves": b,
        "C_false_top_long_weak": c,
        "D_episode_same_direction": d,
        "five_clock_separates_continued": sep,
        "candidate": bool(a and b and c and d and sep),
        "partial": bool((a or d) and not (a and b and c and d and sep)),
    }


def run_precursor_check(*, native_root: Optional[Path] = None, trading_date: str = TRADING_DATE) -> dict[str, Any]:
    root = Path(native_root) if native_root else NATIVE
    day = str(trading_date)
    grid = build_both_down_grid(native_root=root, day=day)
    minutes = list(grid["minutes"])
    episodes = list(grid["episodes"])
    ep_rows = _episode_rows(episodes)
    minute_stats = {k: eval_key(minutes, k) for k in KEYS}
    episode_stats = {k: eval_key(ep_rows, k) for k in KEYS}
    five = _five_table(minutes)
    patterns = {k: _pattern(five, k) for k in KEYS}
    sequences = {k: _full_sequence(minute_stats[k], episode_stats[k], patterns[k]) for k in KEYS}

    def _score(k: str) -> tuple[float, float, int]:
        e = episode_stats[k]
        rd = float(e["risk_difference_3m"] or -1.0)
        lift = float(e["executable_lift"] or -1e9)
        return (rd, lift, int(sequences[k]["candidate"]))

    strongest = max(KEYS, key=_score)
    candidates = [k for k, s in sequences.items() if s["candidate"]]
    partials = [k for k, s in sequences.items() if s["partial"]]
    if candidates:
        case = "A"
        verdict = VERDICT_A
        nxt = NEXT_A
        usable = True
        strongest = max(candidates, key=_score)
    elif partials or any(minute_stats[k]["rev3_separates"] for k in KEYS):
        case = "B"
        verdict = VERDICT_B
        nxt = NEXT_B
        usable = False
    else:
        case = "C"
        verdict = VERDICT_C
        nxt = NEXT_C
        usable = False

    n = len(minutes)
    rev1_n = sum(1 for r in minutes if r.get("REVERSAL_BY_1M"))
    rev3_n = sum(1 for r in minutes if r.get("REVERSAL_BY_3M"))
    orders = live_order_counts()
    st = strongest
    mst = minute_stats[st]
    est = episode_stats[st]
    pst = patterns[st]
    five_by = {r["clock"]: r for r in five}

    sentence = (
        f"On 20260911 BOTH_DOWN minutes, strongest frozen precursor is {st}. "
        f"Minute rev3 TRUE/FALSE={mst.get('rev3_true')}/{mst.get('rev3_false')}; "
        f"episode rev3 TRUE/FALSE={est.get('rev3_true')}/{est.get('rev3_false')}; "
        f"TOP LONG TRUE/FALSE mean={mst.get('top_long_true_mean')}/{mst.get('top_long_false_mean')}. "
        f"09:20/10:20 pick={pst.get('picks_0920_1020')} continued avoid={pst.get('avoids_0940_1040')} "
        f"10:30 distinct={pst.get('distinguishes_1030')}. "
        f"T+3m BOTH_UP is a noncausal label only."
    )
    if case == "C":
        sentence += " Day1 interaction remains POST_HOC_REVERSAL_DEPENDENT."

    freeze = None
    if case in ("A", "B"):
        freeze = {
            "kind": "DAY1_EXPLORATORY_NEW_MECHANISM",
            "analysis_id": "FUTURES_REVERSAL_PRECURSOR_DAY2_DIAGNOSTIC_V1",
            "parent": ANALYSIS_ID,
            "day2_primary_id": DAY2_PRIMARY_ID,
            "day2_primary_unchanged": True,
            "day2_primary": dict(DAY2_PRIMARY),
            "not_an_entry": True,
            "not_a_day2_primary_substitution": True,
            "precursor": st,
            "definition": {
                "P1": "BOTH_SHORT_REBOUND: NK_RET_30S>0 AND TOPIX_RET_30S>0",
                "P2": "BOTH_DECELERATING: RATE30>RATE60>RATE180 on NK and TOPIX",
                "P3": "CASH_EW_RET_180S > mean(NK_RET_180S, TOPIX_RET_180S)",
                "P4": "ACTIVITY_RELATIVE_STATE = TOP_PRE-BOTTOM_PRE < 0 on OBSERVED_TRADE_N_180S",
                "C1": "P1 AND P4",
                "C2": "P2 AND P4",
            },
            "selected": st,
            "recovers_original_five_long_positive_clocks": bool(patterns[st]["picks_0920_1020"]),
            "sample": "AGREEMENT_180S=BOTH_DOWN on 1-minute grid; episode view primary",
            "label": "REVERSAL_BY_3M is NONCAUSAL; never an input",
            "day2_use": "prospective diagnostic section only; definition frozen before Day2 results",
            "verdict_day1": verdict,
        }

    answers = {
        "1_minute_BOTH_DOWN_N": n,
        "2_BOTH_DOWN_episode_N": len(episodes),
        "3_reversal_by_1m_N_rate": {"n": rev1_n, "rate": (rev1_n / n) if n else None},
        "4_reversal_by_3m_N_rate": {"n": rev3_n, "rate": (rev3_n / n) if n else None},
        "5_P1_prevalence": minute_stats["P1"]["prevalence"],
        "6_P1_reversal3m_TRUE_FALSE": {"TRUE": minute_stats["P1"]["rev3_true"], "FALSE": minute_stats["P1"]["rev3_false"], "episode": episode_stats["P1"]},
        "7_P2_prevalence": minute_stats["P2"]["prevalence"],
        "8_P2_reversal3m_TRUE_FALSE": {"TRUE": minute_stats["P2"]["rev3_true"], "FALSE": minute_stats["P2"]["rev3_false"], "episode": episode_stats["P2"]},
        "9_P3_result": {"minute": minute_stats["P3"], "episode": episode_stats["P3"]},
        "10_P4_result": {"minute": minute_stats["P4"], "episode": episode_stats["P4"]},
        "11_C1_result": {"minute": minute_stats["C1"], "episode": episode_stats["C1"]},
        "12_C2_result": {"minute": minute_stats["C2"], "episode": episode_stats["C2"]},
        "13_strongest_precursor": st,
        "14_episode_level_same_direction": sequences[st]["D_episode_same_direction"],
        "15_precursor_TRUE_TOP_LONG_10m_mean_median": {
            "mean": mst["top_long_true_mean"],
            "median": mst["top_long_true_median"],
        },
        "16_precursor_FALSE_TOP_LONG": {
            "mean": mst["top_long_false_mean"],
            "median": mst["top_long_false_median"],
        },
        "17_executable_lift": mst["executable_lift"],
        "18_09:20_precursor_states": five_by.get("09:20"),
        "19_09:40": five_by.get("09:40"),
        "20_10:20": five_by.get("10:20"),
        "21_10:30": five_by.get("10:30"),
        "22_10:40": five_by.get("10:40"),
        "23_separates_0920_1020_from_continued": pst["separates_continued"],
        "24_distinguishes_1030": pst["distinguishes_1030"],
        "25_exact_causal_sequence": sentence,
        "26_usable_ENTRY_thesis_now_plausible": usable,
        "27_ENTRY_built": False,
        "28_EXIT_built": False,
        "29_original_Day2_freeze_changed": False,
        "30_Runtime_changed": False,
        "31_Paper_changed": False,
        "32_submit_cancel_live": f"{orders['submit']}/{orders['cancel']}/{orders['live']}",
        "33_VERDICT": verdict,
        "34_NEXT": nxt,
    }
    decision = {
        "CASE": case,
        "VERDICT": verdict,
        "NEXT": nxt,
        "strongest_precursor": st,
        "candidates": candidates,
        "ENTRY_built": False,
        "EXIT_built": False,
        "Day2_primary_changed": False,
        "Day2_substitutions_allowed": False,
        "usable_ENTRY_thesis": usable,
        "POST_HOC_REVERSAL_DEPENDENT": case == "C",
        "submit_cancel_live": answers["32_submit_cancel_live"],
    }
    return {
        "analysis_id": ANALYSIS_ID,
        "parent_id": PARENT_ID,
        "kind": KIND,
        "trading_date": day,
        "built_at_jst": datetime.now(JST).isoformat(timespec="seconds"),
        "day2_primary": dict(DAY2_PRIMARY),
        "day2_primary_id": DAY2_PRIMARY_ID,
        "future_leakage_n": grid["future_leakage_n"],
        "grid_n": grid["grid_n"],
        "both_down_n": grid["both_down_n"],
        "episode_n": len(episodes),
        "minute_stats": minute_stats,
        "episode_stats": episode_stats,
        "sequences": sequences,
        "patterns": patterns,
        "original_five": five,
        "episodes": [
            {
                "episode_id": e["episode_id"],
                "start": e["start"],
                "end": e["end"],
                "n_minutes": e["n_minutes"],
                "P1": e["anchor"].get("P1"),
                "P2": e["anchor"].get("P2"),
                "P3": e["anchor"].get("P3"),
                "P4": e["anchor"].get("P4"),
                "C1": e["anchor"].get("C1"),
                "C2": e["anchor"].get("C2"),
                "REVERSAL_BY_3M": e["anchor"].get("REVERSAL_BY_3M"),
                "TOP_LONG_10m": _top_long(e["anchor"]),
            }
            for e in episodes
        ],
        "minutes_slim": [
            {
                "clock": r["clock"],
                "P1": r.get("P1"),
                "P2": r.get("P2"),
                "P3": r.get("P3"),
                "P4": r.get("P4"),
                "C1": r.get("C1"),
                "C2": r.get("C2"),
                "short_rebound_class": r.get("short_rebound_class"),
                "REVERSAL_BY_1M": r.get("REVERSAL_BY_1M"),
                "REVERSAL_BY_3M": r.get("REVERSAL_BY_3M"),
                "TOP_LONG_10m": _top_long(r),
            }
            for r in minutes
        ],
        "freeze_manifest": freeze,
        "answers": answers,
        "decision": decision,
    }
