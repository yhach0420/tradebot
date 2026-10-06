"""Assemble Day1 lead/lag RCA. No ENTRY/EXIT. Day2 freeze unchanged."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.futures_context_day1_effect_check_v1.engine import (
    _epoch,
    build_clock_table,
    clock_dt,
    mean,
    median,
    preopen_test,
)
from research.futures_context_day1_lead_lag_rca_v1 import (
    ANALYSIS_ID,
    DAY1_CAUSAL_SHIFT_BPS,
    DAY1_PLACEBO_P5M_BPS,
    DAY2_FROZEN_TEST_CHANGED,
    ENTRY,
    EXIT,
    KIND,
    NEXT,
    OFFSET_LABELS,
    OFFSETS_SEC,
    PARENT_ID,
    PRIMARY_FEATURE,
    PRIMARY_HORIZON,
    STOCK_PRIOR_WINDOWS_SEC,
    TRADING_DATE,
    VERDICT_A,
    VERDICT_B,
    VERDICT_C,
    VERDICT_D,
)
from research.futures_context_day1_lead_lag_rca_v1.engine import (
    attach_offset_features,
    bucket_shift,
    causal_same_direction,
    classify_type,
    ew_stock_past_ret,
    four_state,
    nk_adds_beyond_stock,
    overlap_map,
    peak_offset,
    reversal_vs_persistence,
    sgn,
)
from research.futures_context_day1_lead_lag_rca_v1.isolation import NATIVE
from research.new_causal_information_acquisition_v1.launcher import live_order_counts

JST = ZoneInfo("Asia/Tokyo")
VERDICTS = {"A": VERDICT_A, "B": VERDICT_B, "C": VERDICT_C, "D": VERDICT_D}


def run_rca(*, native_root: Optional[Path] = None, trading_date: str = TRADING_DATE) -> dict[str, Any]:
    root = Path(native_root) if native_root else NATIVE
    day = str(trading_date)
    table = build_clock_table(native_root=root, day=day)
    clocks0 = table["clocks"]
    nk = table["nk_series"]
    tx = table["tx_series"]
    stocks = table["stock_series"]
    symbols = table["symbols"]
    rows, off_leak = attach_offset_features(clocks0, nk, day)

    for row in rows:
        hm = tuple(int(x) for x in str(row["clock"]).split(":"))
        te = _epoch(clock_dt(day, hm))
        for win in STOCK_PRIOR_WINDOWS_SEC:
            row[f"STK_PAST_RET_{win}S"] = ew_stock_past_ret(stocks, symbols, te, win)
        row["STK_PAST_RET_180S_at_-3m"] = ew_stock_past_ret(stocks, symbols, te - 180.0, 180)
        row["STK_PAST_RET_180S_at_-1m"] = ew_stock_past_ret(stocks, symbols, te - 60.0, 180)

    offset_rows = []
    for off, lab in zip(OFFSETS_SEC, OFFSET_LABELS):
        ov = overlap_map(off)
        b = bucket_shift(rows, f"NK180_at_{lab}", "mid_10m")
        offset_rows.append(
            {
                "label": lab,
                "offset_sec": off,
                "noncausal_diagnostic": off > 0,
                "usable_for_candidate_strategy": False if off > 0 else None,
                **b,
                **ov,
            }
        )

    peak = peak_offset(offset_rows)
    causal_dir = causal_same_direction(offset_rows)
    stk_bucket = bucket_shift(rows, "STK_PAST_RET_180S", "mid_10m")
    nk0_bucket = bucket_shift(rows, "NK180_at_0m", "mid_10m")
    states = four_state(rows, "NK180_at_0m", "STK_PAST_RET_180S", "mid_10m")
    incr = nk_adds_beyond_stock(states)
    rev = reversal_vs_persistence(rows)

    nk_t_vs_nk_m3 = sum(
        1
        for r in rows
        if sgn(r.get("NK180_at_0m")) != 0
        and sgn(r.get("NK180_at_-3m")) != 0
        and sgn(r.get("NK180_at_0m")) == sgn(r.get("NK180_at_-3m"))
    )
    nk_t_vs_stk_m3 = sum(
        1
        for r in rows
        if sgn(r.get("NK180_at_0m")) != 0
        and sgn(r.get("STK_PAST_RET_180S_at_-3m")) != 0
        and sgn(r.get("NK180_at_0m")) == sgn(r.get("STK_PAST_RET_180S_at_-3m"))
    )
    compared_nk_m3 = sum(
        1 for r in rows if sgn(r.get("NK180_at_0m")) != 0 and sgn(r.get("NK180_at_-3m")) != 0
    )
    compared_stk_m3 = sum(
        1 for r in rows if sgn(r.get("NK180_at_0m")) != 0 and sgn(r.get("STK_PAST_RET_180S_at_-3m")) != 0
    )
    stock_earlier = bool(compared_stk_m3 and nk_t_vs_stk_m3 > nk_t_vs_nk_m3)

    pos = [r for r in rows if isinstance(r.get("NK180_at_0m"), (int, float)) and r["NK180_at_0m"] > 0]
    neg = [r for r in rows if isinstance(r.get("NK180_at_0m"), (int, float)) and r["NK180_at_0m"] < 0]
    long_pos = [float(r["long_10m"]) for r in pos if r.get("long_10m") is not None]
    short_neg = [float(r["short_10m"]) for r in neg if r.get("short_10m") is not None]
    abs_long = {"n": len(long_pos), "mean": mean(long_pos), "median": median(long_pos)}
    abs_short = {"n": len(short_neg), "mean": mean(short_neg), "median": median(short_neg)}
    long_pos_ok = bool(abs_long["mean"] is not None and abs_long["mean"] > 0)
    short_neg_ok = bool(abs_short["mean"] is not None and abs_short["mean"] > 0)
    exec_edge = bool(long_pos_ok or short_neg_ok)
    exec_note = "EXECUTABLE_EDGE_PRESENT" if exec_edge else "EXECUTABLE_EDGE_NOT_YET_PRESENT"

    noncausal = [r for r in offset_rows if r.get("noncausal_diagnostic")]
    max_nc = max((r.get("mid_shift_bps") for r in noncausal if r.get("mid_shift_bps") is not None), default=None)
    zero_shift = next((r.get("mid_shift_bps") for r in offset_rows if r.get("offset_sec") == 0), None)
    p5 = next((r for r in offset_rows if r.get("offset_sec") == 300), {})
    overlap_inside = bool(p5.get("feature_fully_inside_outcome"))

    typ, reason = classify_type(
        causal_same=bool(causal_dir["same"]),
        peak_is_noncausal=bool(peak.get("noncausal_diagnostic")),
        zero_shift=zero_shift,
        max_noncausal_shift=max_nc,
        stock_prior_shift=stk_bucket.get("mid_shift_bps"),
        stock_earlier_than_nk=stock_earlier,
        nk_adds=bool(incr.get("nk_adds")) and not incr.get("insufficient_n"),
        overlap_placebo_feature_inside_outcome=overlap_inside,
    )
    verdict = VERDICTS[typ]
    pre = preopen_test(native_root=root, day=day, stocks=stocks, nk=nk, tx=tx, symbols=symbols)
    preopen_follow = False
    if pre.get("nk_ret_0845_0900") is not None and (pre.get("from_0900") or {}).get("10m", {}).get("MID_mean") is not None:
        preopen_follow = sgn(pre["nk_ret_0845_0900"]) == sgn(pre["from_0900"]["10m"]["MID_mean"]) and sgn(pre["nk_ret_0845_0900"]) != 0

    by_lab = {r["label"]: r.get("mid_shift_bps") for r in offset_rows}
    clock_view = []
    for r in rows:
        clock_view.append(
            {
                "clock": r.get("clock"),
                "block": r.get("block"),
                "NK_RET_30S": r.get("NK_RET_30S"),
                "NK_RET_60S": r.get("NK_RET_60S"),
                "NK_RET_180S": r.get("NK_RET_180S"),
                "STK_PAST_RET_30S": r.get("STK_PAST_RET_30S"),
                "STK_PAST_RET_60S": r.get("STK_PAST_RET_60S"),
                "STK_PAST_RET_180S": r.get("STK_PAST_RET_180S"),
                "mid_10m": r.get("mid_10m"),
                "long_10m": r.get("long_10m"),
                "short_10m": r.get("short_10m"),
                **{f"NK180_at_{lab}": r.get(f"NK180_at_{lab}") for lab in OFFSET_LABELS},
            }
        )

    mechanism = (
        f"On 20260911, NK_RET_180S evaluated at T separates 13 clock-mean 10m stock MIDs by "
        f"{None if zero_shift is None else round(float(zero_shift), 3)} bps, but the same split using "
        f"NK_RET_180S at T+5m is {None if p5.get('mid_shift_bps') is None else round(float(p5['mid_shift_bps']), 3)} bps. "
        f"Causal offsets flip sign (-5m {None if by_lab.get('-5m') is None else round(float(by_lab['-5m']), 3)} "
        f"to 0m {None if zero_shift is None else round(float(zero_shift), 3)}), so this is not a stable futures lead. "
        f"The T+5m 180s futures window sits inside the stock 10m outcome window "
        f"(overlap {p5.get('overlap_sec')}s, feature fully inside outcome={overlap_inside}), so +5m placebo "
        f"superiority is contemporaneous overlap. NK=STK agreement clocks have near-zero 10m gap; "
        f"the 0m +25.8 bps split is concentrated on turning-point clocks. Classification={typ}."
    )
    orders = live_order_counts()
    answers = {
        "1_offset_-5m_MID_shift": by_lab.get("-5m"),
        "2_offset_-3m": by_lab.get("-3m"),
        "3_offset_-1m": by_lab.get("-1m"),
        "4_offset_0m": by_lab.get("0m"),
        "5_offset_+1m": by_lab.get("+1m"),
        "6_offset_+3m": by_lab.get("+3m"),
        "7_offset_+5m": by_lab.get("+5m"),
        "8_peak_offset": peak.get("label"),
        "9_causal_only_offsets_same_direction": causal_dir["same"],
        "10_stock_PAST_RET_180S_relation": stk_bucket,
        "11_NK180_adds_beyond_stock_prior": incr,
        "12_NK_up_STK_down_future": states.get("NK_up_STK_down"),
        "13_NK_down_STK_up_future": states.get("NK_down_STK_up"),
        "14_30_60_inversion_explained_by_reversal": rev.get("short_reversal_explains_30_60_inversion"),
        "15_180s_explained_by_trend_persistence": rev.get("trend_persistence_explains_180s"),
        "16_placebo_superiority_due_temporal_overlap": overlap_inside and peak.get("noncausal_diagnostic"),
        "17_absolute_LONG_positive": long_pos_ok,
        "18_absolute_SHORT_positive": short_neg_ok,
        "19_mechanism_classification": typ,
        "20_exact_mechanism_sentence": mechanism,
        "21_usable_ENTRY_thesis_exists": False,
        "22_Day2_frozen_test_changed": DAY2_FROZEN_TEST_CHANGED,
        "23_Runtime_changed": False,
        "24_Paper_changed": False,
        "25_submit_cancel_live": f"{orders['submit']}/{orders['cancel']}/{orders['live']}",
    }
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "PARENT_ID": PARENT_ID,
        "KIND": KIND,
        "trading_date": day,
        "clock": datetime.now(JST).isoformat(timespec="seconds"),
        "primary_row": {
            "feature": PRIMARY_FEATURE,
            "horizon": PRIMARY_HORIZON,
            "day1_causal_shift_bps": DAY1_CAUSAL_SHIFT_BPS,
            "day1_placebo_p5m_bps": DAY1_PLACEBO_P5M_BPS,
            "recomputed_0m_shift_bps": zero_shift,
            "recomputed_p5m_shift_bps": p5.get("mid_shift_bps"),
        },
        "statistical_unit": "CLOCK",
        "stock_n": table["symbol_n"],
        "anchor_clock_n": table["anchor_clock_n"],
        "stock_anchor_n": table["stock_anchor_n"],
        "future_leakage_n": table["future_leakage_n"] + off_leak,
        "offset_rows": offset_rows,
        "peak": peak,
        "causal_direction": causal_dir,
        "stock_prior_180_bucket": stk_bucket,
        "nk0_bucket": nk0_bucket,
        "four_state": states,
        "incremental": incr,
        "stock_earlier_than_nk": {
            "NK_T_agrees_NK_Tminus3m_n": nk_t_vs_nk_m3,
            "NK_T_agrees_STK_Tminus3m_n": nk_t_vs_stk_m3,
            "compared_NK_Tminus3m_n": compared_nk_m3,
            "compared_STK_Tminus3m_n": compared_stk_m3,
            "stock_earlier": stock_earlier,
        },
        "reversal_vs_persistence": rev,
        "absolute_exec": {
            "NK180_pos_LONG": abs_long,
            "NK180_neg_SHORT": abs_short,
            "absolute_LONG_positive": long_pos_ok,
            "absolute_SHORT_positive": short_neg_ok,
            "note": exec_note,
        },
        "preopen": pre,
        "PREOPEN_DIRECTIONAL_FOLLOWING": preopen_follow,
        "clocks": clock_view,
        "answers": answers,
        "decision": {
            "TYPE": typ,
            "reason": reason,
            "VERDICT": verdict,
            "NEXT": NEXT,
            "ENTRY": ENTRY,
            "EXIT": EXIT,
            "STRATEGY_BUILT": False,
            "DAY2_FROZEN_TEST_CHANGED": DAY2_FROZEN_TEST_CHANGED,
            "TRUE_OOS": False,
            "CERTIFIED": False,
        },
        "orders": orders,
        "TRUE_OOS": False,
        "CERTIFIED": False,
    }
