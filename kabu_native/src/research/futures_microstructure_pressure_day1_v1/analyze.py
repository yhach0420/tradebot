"""Assemble Day1 futures microstructure pressure test. No ENTRY/EXIT. Day2 freeze unchanged."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.futures_context_day1_effect_check_v1.engine import mean as _mean
from research.futures_microstructure_pressure_day1_v1 import (
    ANALYSIS_ID,
    CASE_A,
    CASE_B,
    CASE_C,
    DAY2_FROZEN_PRICE_RETURN_TEST_CHANGED,
    ENTRY,
    EXIT,
    KIND,
    MATERIAL_BPS,
    NEXT_A,
    NEXT_B,
    NEXT_C,
    PARENT_ID,
    PLACEBO_SHIFT_SEC,
    SIGNED_FEATURES,
    TRADING_DATE,
)
from research.futures_microstructure_pressure_day1_v1.engine import (
    bucket,
    build_minute_table,
    four_state,
    sgn,
)
from research.futures_microstructure_pressure_day1_v1.isolation import NATIVE
from research.new_causal_information_acquisition_v1.launcher import live_order_counts

JST = ZoneInfo("Asia/Tokyo")

FUT_PX_PREFIX = {
    "NK_L1_IMB": "NK",
    "NK_DEPTH10_IMB": "NK",
    "NK_QUOTE_PRESSURE_60S": "NK",
    "TOPIX_L1_IMB": "TOPIX",
    "TOPIX_DEPTH10_IMB": "TOPIX",
    "TOPIX_QUOTE_PRESSURE_60S": "TOPIX",
    "CROSS_L1_AGREEMENT": "NK",
}


def _bps(x: Optional[float]) -> Optional[float]:
    return None if x is None else 10000.0 * float(x)


def _abs(x: Optional[float]) -> float:
    return abs(float(x)) if x is not None else -1.0


def run_pressure_check(*, native_root: Optional[Path] = None, trading_date: str = TRADING_DATE) -> dict[str, Any]:
    root = Path(native_root) if native_root else NATIVE
    day = str(trading_date)
    table = build_minute_table(native_root=root, day=day)
    clocks = table["clocks"]
    early = [c for c in clocks if c["block"] == "EARLY"]
    late = [c for c in clocks if c["block"] == "LATE"]

    signed_rows: list[dict[str, Any]] = []
    for feat in SIGNED_FEATURES:
        px = FUT_PX_PREFIX[feat]
        s1_30 = bucket(clocks, feat, f"{px}_RET_30S")
        s1_60 = bucket(clocks, feat, f"{px}_RET_60S")
        s1_30["shift_bps"] = _bps(s1_30["shift"])
        s1_60["shift_bps"] = _bps(s1_60["shift"])
        s1_dir_30 = sgn(s1_30["shift"])
        s1_dir_60 = sgn(s1_60["shift"])
        stage1_pass = s1_dir_30 == 1 and s1_dir_60 == 1
        s2 = {}
        for lab in ("1m", "3m", "5m"):
            b = bucket(clocks, feat, f"STK_MID_{lab}_mean")
            s2[lab] = b
        s2_dirs = [s2[lab]["direction"] for lab in ("1m", "3m", "5m") if s2[lab]["direction"] != 0]
        stage2_same = bool(s2_dirs) and len(set(s2_dirs)) == 1
        stage2_dir = s2_dirs[0] if stage2_same else 0
        stage2_pass = stage2_same and stage1_pass and stage2_dir == s1_dir_30
        seq_ok = bool(stage1_pass and s2["1m"]["direction"] == s1_dir_30 and s1_dir_30 != 0)
        e5 = bucket(early, feat, "STK_MID_5m_mean")
        l5 = bucket(late, feat, "STK_MID_5m_mean")
        all5 = s2["5m"]
        hold = (
            all5["direction"] != 0
            and e5["direction"] == all5["direction"]
            and l5["direction"] == all5["direction"]
        )
        plc_clocks = []
        for c in clocks:
            pc = dict(c)
            pc[feat] = (c.get("placebo") or {}).get(feat)
            plc_clocks.append(pc)
        plc5 = bucket(plc_clocks, feat, "STK_MID_5m_mean")
        causal5 = all5["shift"]
        beats_plc = _abs(causal5) > _abs(plc5["shift"])
        long_pos = bucket(clocks, feat, "STK_LONG_5m_mean")
        short_neg_clocks = [c for c in clocks if isinstance(c.get(feat), (int, float)) and c[feat] < 0]
        short_xs = [float(c["STK_SHORT_5m_mean"]) for c in short_neg_clocks if c.get("STK_SHORT_5m_mean") is not None]
        pop_long = [float(c["STK_LONG_5m_mean"]) for c in clocks if c.get("STK_LONG_5m_mean") is not None]
        pop_short = [float(c["STK_SHORT_5m_mean"]) for c in clocks if c.get("STK_SHORT_5m_mean") is not None]
        pos_long_mean = long_pos["pos_mean"]
        pop_long_mean = _mean(pop_long)
        pop_short_mean = _mean(pop_short)
        short_mean = _mean(short_xs)
        long_impr = (pos_long_mean - pop_long_mean) if pos_long_mean is not None and pop_long_mean is not None else None
        short_impr = (short_mean - pop_short_mean) if short_mean is not None and pop_short_mean is not None else None
        abs_long_pos = bool(pos_long_mean is not None and pos_long_mean > 0)
        abs_short_pos = bool(short_mean is not None and short_mean > 0)
        material = bool(all5["shift"] is not None and abs(float(all5["shift"])) >= MATERIAL_BPS)
        exec_impr = bool(
            (long_impr is not None and long_impr > 0) or (short_impr is not None and short_impr > 0)
        )
        candidate = bool(stage1_pass and stage2_pass and hold and beats_plc and material and exec_impr)
        strong = bool(candidate and (abs_long_pos or abs_short_pos))
        prior_stk = four_state(clocks, feat, "STOCK_PAST_RET_180S", "STK_MID_5m_mean")
        prior_nk = four_state(clocks, feat, "NK_PAST_RET_180S", "STK_MID_5m_mean")
        signed_rows.append(
            {
                "feature": feat,
                "fut_px": px,
                "stage1_30": {**s1_30, "shift_bps": _bps(s1_30["shift"])},
                "stage1_60": {**s1_60, "shift_bps": _bps(s1_60["shift"])},
                "stage1_pass": stage1_pass,
                "stage1_direction": s1_dir_30 if stage1_pass else 0,
                "stage2": s2,
                "stage2_pass": stage2_pass,
                "sequence_ok": seq_ok,
                "early_5m": e5,
                "late_5m": l5,
                "internal_hold": hold,
                "placebo_5m": plc5,
                "causal_beats_placebo": beats_plc,
                "long_impr_bps": long_impr,
                "short_impr_bps": short_impr,
                "pos_long_abs_mean": pos_long_mean,
                "neg_short_abs_mean": short_mean,
                "absolute_LONG_positive": abs_long_pos,
                "absolute_SHORT_positive": abs_short_pos,
                "material_5m": material,
                "exec_improved": exec_impr,
                "day1_candidate": candidate,
                "executable_microstructure_candidate": strong,
                "prior_stock": prior_stk,
                "prior_nk": prior_nk,
            }
        )

    stage1_hits = [r for r in signed_rows if r["stage1_pass"]]
    stage2_hits = [r for r in signed_rows if r["day1_candidate"]]
    ranked_s1 = sorted(signed_rows, key=lambda r: _abs((r["stage1_30"] or {}).get("shift_bps")), reverse=True)
    best = ranked_s1[0] if ranked_s1 else {}
    if any(r["day1_candidate"] for r in signed_rows):
        case, verdict, nxt = "A", CASE_A, NEXT_A
        best = max(signed_rows, key=lambda r: (int(r["day1_candidate"]), _abs((r["stage1_30"] or {}).get("shift_bps"))))
    elif stage1_hits and not any(r["stage2_pass"] for r in signed_rows):
        case, verdict, nxt = "B", CASE_B, NEXT_B
        best = max(stage1_hits, key=lambda r: _abs((r["stage1_30"] or {}).get("shift_bps")))
    else:
        case, verdict, nxt = "C", CASE_C, NEXT_C

    usable = bool(case == "A")
    seq = (
        f"On 20260911 minute clocks, strongest Stage1 feature {best.get('feature')} "
        f"has NK/TOPIX 30s shift {_bps((best.get('stage1_30') or {}).get('shift'))} bps "
        f"(60s {_bps((best.get('stage1_60') or {}).get('shift'))} bps), "
        f"stock MID 1/3/5m "
        f"{(best.get('stage2') or {}).get('1m', {}).get('shift')}/"
        f"{(best.get('stage2') or {}).get('3m', {}).get('shift')}/"
        f"{(best.get('stage2') or {}).get('5m', {}).get('shift')} bps, "
        f"sequence_ok={best.get('sequence_ok')}, hold={best.get('internal_hold')}, "
        f"placebo_beats={not best.get('causal_beats_placebo')}."
    )
    def compact_s1(name: str) -> dict[str, Any]:
        row = next((r for r in signed_rows if r["feature"] == name), {})
        return {
            "30s_shift_bps": (row.get("stage1_30") or {}).get("shift_bps"),
            "60s_shift_bps": (row.get("stage1_60") or {}).get("shift_bps"),
            "stage1_pass": row.get("stage1_pass"),
            "direction": row.get("stage1_direction"),
        }

    orders = live_order_counts()
    s2 = best.get("stage2") or {}
    answers = {
        "1_minute_clock_n": table["minute_clock_n"],
        "2_leakage_n": table["future_leakage_n"],
        "3_NK_L1_imbalance_NK_30s": compact_s1("NK_L1_IMB").get("30s_shift_bps"),
        "4_NK_L1_imbalance_NK_60s": compact_s1("NK_L1_IMB").get("60s_shift_bps"),
        "5_NK_depth10_NK_30s": compact_s1("NK_DEPTH10_IMB").get("30s_shift_bps"),
        "6_NK_depth10_NK_60s": compact_s1("NK_DEPTH10_IMB").get("60s_shift_bps"),
        "7_NK_quote_pressure_NK_price": compact_s1("NK_QUOTE_PRESSURE_60S"),
        "8_TOPIX_equivalents": {
            "L1_30s": compact_s1("TOPIX_L1_IMB").get("30s_shift_bps"),
            "L1_60s": compact_s1("TOPIX_L1_IMB").get("60s_shift_bps"),
            "DEPTH10_30s": compact_s1("TOPIX_DEPTH10_IMB").get("30s_shift_bps"),
            "DEPTH10_60s": compact_s1("TOPIX_DEPTH10_IMB").get("60s_shift_bps"),
            "QUOTE": compact_s1("TOPIX_QUOTE_PRESSURE_60S"),
        },
        "9_strongest_Stage1_feature": best.get("feature"),
        "10_strongest_Stage1_direction": best.get("stage1_direction"),
        "11_same_feature_stock_1m": (s2.get("1m") or {}).get("shift"),
        "12_same_feature_stock_3m": (s2.get("3m") or {}).get("shift"),
        "13_same_feature_stock_5m": (s2.get("5m") or {}).get("shift"),
        "14_strongest_stock_MID_shift": max(
            ((s2.get(lab) or {}).get("shift") for lab in ("1m", "3m", "5m")),
            key=lambda x: _abs(x),
            default=None,
        ),
        "15_LONG_improvement": best.get("long_impr_bps"),
        "16_SHORT_improvement": best.get("short_impr_bps"),
        "17_absolute_LONG_positive": best.get("absolute_LONG_positive"),
        "18_absolute_SHORT_positive": best.get("absolute_SHORT_positive"),
        "19_EARLY_direction": (best.get("early_5m") or {}).get("direction"),
        "20_LATE_direction": (best.get("late_5m") or {}).get("direction"),
        "21_internal_hold": best.get("internal_hold"),
        "22_Tplus2m_placebo": (best.get("placebo_5m") or {}).get("shift"),
        "23_causal_beats_placebo": best.get("causal_beats_placebo"),
        "24_prior_price_explains": {
            "stock_past_180s": (best.get("prior_stock") or {}).get("prior_explains"),
            "nk_past_180s": (best.get("prior_nk") or {}).get("prior_explains"),
            "stock_states": (best.get("prior_stock") or {}).get("states"),
            "nk_states": (best.get("prior_nk") or {}).get("states"),
        },
        "25_exact_causal_sequence": seq,
        "26_usable_mechanism_exists": usable,
        "27_ENTRY_built": False,
        "28_EXIT_built": False,
        "29_Day2_frozen_price_return_test_changed": DAY2_FROZEN_PRICE_RETURN_TEST_CHANGED,
        "30_Runtime_changed": False,
        "31_Paper_changed": False,
        "32_submit_cancel_live": f"{orders['submit']}/{orders['cancel']}/{orders['live']}",
        "33_VERDICT": verdict,
        "34_NEXT": nxt,
    }
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "PARENT_ID": PARENT_ID,
        "KIND": KIND,
        "trading_date": day,
        "clock": datetime.now(JST).isoformat(timespec="seconds"),
        "frozen_features": list(SIGNED_FEATURES),
        "price_return_as_primary": False,
        "statistical_unit": "MINUTE_CLOCK",
        "imb_median_window_sec": 30,
        "placebo_shift_sec": PLACEBO_SHIFT_SEC,
        "minute_clock_n": table["minute_clock_n"],
        "stock_n": table["symbol_n"],
        "future_leakage_n": table["future_leakage_n"],
        "nk_event_n": table["nk_event_n"],
        "tx_event_n": table["tx_event_n"],
        "signed_rows": signed_rows,
        "best": best,
        "stage1_hit_n": len(stage1_hits),
        "day1_candidate_n": len(stage2_hits),
        "answers": answers,
        "decision": {
            "CASE": case,
            "VERDICT": verdict,
            "NEXT": nxt,
            "ENTRY": ENTRY,
            "EXIT": EXIT,
            "STRATEGY_BUILT": False,
            "DAY2_FROZEN_PRICE_RETURN_TEST_CHANGED": DAY2_FROZEN_PRICE_RETURN_TEST_CHANGED,
            "TRUE_OOS": False,
            "CERTIFIED": False,
        },
        "orders": orders,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "clocks_slim": [
            {
                "clock": c["clock"],
                "block": c["block"],
                **{k: c.get(k) for k in SIGNED_FEATURES},
                "NK_RET_30S_bps": _bps(c.get("NK_RET_30S")),
                "NK_RET_60S_bps": _bps(c.get("NK_RET_60S")),
                "STK_MID_1m": c.get("STK_MID_1m_mean"),
                "STK_MID_5m": c.get("STK_MID_5m_mean"),
                "STK_LONG_5m": c.get("STK_LONG_5m_mean"),
                "STK_SHORT_5m": c.get("STK_SHORT_5m_mean"),
            }
            for c in clocks
        ],
    }
