"""Assemble Day1 effect check. No threshold search. No ENTRY/EXIT."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.futures_context_day1_effect_check_v1 import (
    ANALYSIS_ID,
    CASE_A,
    CASE_B,
    CASE_C,
    FEATURE_NAMES,
    HORIZON_LABEL,
    HORIZONS_SEC,
    KIND,
    MATERIAL_BPS,
    NEXT_A,
    NEXT_B,
    NEXT_C,
    PARENT_ID,
    SIGNED_FEATURES,
    TRADING_DATE,
)
from research.futures_context_day1_effect_check_v1.engine import (
    bucket_agreement,
    bucket_signed,
    build_clock_table,
    decay_pattern,
    placebo_clocks,
    preopen_test,
    relation_sentence,
    top_symbol_exclusion,
)
from research.futures_context_day1_effect_check_v1.isolation import NATIVE
from research.new_causal_information_acquisition_v1.launcher import live_order_counts

JST = ZoneInfo("Asia/Tokyo")


def _abs(x: Optional[float]) -> float:
    return abs(float(x)) if x is not None else -1.0


def _material(row: dict[str, Any]) -> bool:
    mid = row.get("mid_shift_bps")
    long_i = row.get("long_impr_bps")
    short_i = row.get("short_impr_bps")
    return bool(
        (mid is not None and abs(float(mid)) >= MATERIAL_BPS)
        or (long_i is not None and float(long_i) >= MATERIAL_BPS)
        or (short_i is not None and float(short_i) >= MATERIAL_BPS)
    )


def run_effect_check(*, native_root: Optional[Path] = None, trading_date: str = TRADING_DATE) -> dict[str, Any]:
    root = Path(native_root) if native_root else NATIVE
    day = str(trading_date)
    table = build_clock_table(native_root=root, day=day)
    clocks = table["clocks"]
    stock_rows = table["stock_rows"]
    symbols = table["symbols"]
    nk = table["nk_series"]
    tx = table["tx_series"]
    stocks = table["stock_series"]
    placebo, _p_leak = placebo_clocks(clocks, nk, tx, day)

    signed_rows: list[dict[str, Any]] = []
    for feat in SIGNED_FEATURES:
        shifts = {}
        holds = {}
        for h in HORIZONS_SEC:
            hl = HORIZON_LABEL[h]
            all_b = bucket_signed(clocks, feat, hl)
            early_b = bucket_signed([c for c in clocks if c["block"] == "EARLY"], feat, hl)
            late_b = bucket_signed([c for c in clocks if c["block"] == "LATE"], feat, hl)
            plc = bucket_signed(placebo, feat, hl)
            hold = (
                all_b["direction"] != 0
                and early_b["direction"] == all_b["direction"]
                and late_b["direction"] == all_b["direction"]
            )
            shifts[hl] = all_b["mid_shift_bps"]
            holds[hl] = hold
            signed_rows.append(
                {
                    **all_b,
                    "early_direction": early_b["direction"],
                    "late_direction": late_b["direction"],
                    "all_direction": all_b["direction"],
                    "internal_hold": hold,
                    "placebo_mid_shift_bps": plc["mid_shift_bps"],
                    "placebo_long_impr_bps": plc["long_impr_bps"],
                    "placebo_short_impr_bps": plc["short_impr_bps"],
                    "causal_beats_placebo": _abs(all_b["mid_shift_bps"]) > _abs(plc["mid_shift_bps"]),
                    "material": _material(all_b),
                    "exec_positive": bool(
                        (all_b.get("long_impr_bps") is not None and float(all_b["long_impr_bps"]) > 0)
                        or (all_b.get("short_impr_bps") is not None and float(all_b["short_impr_bps"]) > 0)
                    ),
                }
            )
        for row in signed_rows:
            if row["feature"] == feat:
                row["decay"] = decay_pattern(shifts)
                row["feature_internal_hold_any_horizon"] = any(holds.values())

    agreement_rows = [bucket_agreement(clocks, HORIZON_LABEL[h]) for h in HORIZONS_SEC]

    ranked = sorted(
        signed_rows,
        key=lambda r: (
            abs(float(r["mid_shift_bps"] or 0)),
            abs(float(r["long_impr_bps"] or 0)),
            abs(float(r["short_impr_bps"] or 0)),
        ),
        reverse=True,
    )
    best = ranked[0] if ranked else {}
    excl1 = top_symbol_exclusion(
        stock_rows=stock_rows,
        clocks=clocks,
        feature=str(best.get("feature") or "NK_RET_60S"),
        horizon=str(best.get("horizon") or "5m"),
        drop_n=1,
    )
    excl2 = top_symbol_exclusion(
        stock_rows=stock_rows,
        clocks=clocks,
        feature=str(best.get("feature") or "NK_RET_60S"),
        horizon=str(best.get("horizon") or "5m"),
        drop_n=2,
    )
    concentrated = bool(not excl1.get("hold") or not excl2.get("hold"))
    exclusion_hold = bool(excl1.get("hold") and excl2.get("hold"))

    material_any = any(r.get("material") for r in signed_rows)
    hold_best = bool(best.get("internal_hold"))
    beats_placebo = bool(best.get("causal_beats_placebo"))
    exec_pos = any(r.get("exec_positive") for r in signed_rows)
    stronger = bool(
        best.get("material")
        and best.get("internal_hold")
        and best.get("causal_beats_placebo")
        and exclusion_hold
        and (
            (best.get("pos_mid_median") is not None and best.get("pos_mid") and (best["pos_mid"]["mean"] or 0) > 0 and (best["pos_mid_median"] or 0) > 0)
            or (best.get("neg_mid_median") is not None and best.get("neg_mid") and (best["neg_mid"]["mean"] or 0) < 0 and (best["neg_mid_median"] or 0) < 0)
        )
        and best.get("decay")
        and (
            (best.get("long_impr_bps") is not None and float(best["long_impr_bps"]) >= MATERIAL_BPS)
            or (best.get("short_impr_bps") is not None and float(best["short_impr_bps"]) >= MATERIAL_BPS)
        )
    )

    if material_any and hold_best and beats_placebo and exclusion_hold and not concentrated:
        case = "A"
        verdict = CASE_A
        nxt = NEXT_A
    elif not material_any:
        case = "C"
        verdict = CASE_C
        nxt = NEXT_C
    else:
        case = "B"
        verdict = CASE_B
        nxt = NEXT_B

    if concentrated and case == "A":
        case = "B"
        verdict = CASE_B
        nxt = NEXT_B

    pre = preopen_test(native_root=root, day=day, stocks=stocks, nk=nk, tx=tx, symbols=symbols)

    def feat_summary(name: str) -> dict[str, Any]:
        rows = [r for r in signed_rows if r["feature"] == name]
        best_h = max(rows, key=lambda r: abs(float(r.get("mid_shift_bps") or 0))) if rows else {}
        return {
            "best_horizon": best_h.get("horizon"),
            "mid_shift_bps": best_h.get("mid_shift_bps"),
            "long_impr_bps": best_h.get("long_impr_bps"),
            "short_impr_bps": best_h.get("short_impr_bps"),
            "internal_hold": best_h.get("internal_hold"),
            "early_direction": best_h.get("early_direction"),
            "late_direction": best_h.get("late_direction"),
        }

    orders = live_order_counts()
    answers = {
        "1_anchor_clock_n": table["anchor_clock_n"],
        "2_stock_anchor_n": table["stock_anchor_n"],
        "3_future_leakage_n": table["future_leakage_n"],
        "4_NK30_relation": feat_summary("NK_RET_30S"),
        "5_NK60_relation": feat_summary("NK_RET_60S"),
        "6_NK180_relation": feat_summary("NK_RET_180S"),
        "7_TOPIX30_relation": feat_summary("TOPIX_RET_30S"),
        "8_TOPIX60_relation": feat_summary("TOPIX_RET_60S"),
        "9_TOPIX180_relation": feat_summary("TOPIX_RET_180S"),
        "10_agreement_effect": agreement_rows,
        "11_strongest_horizon": best.get("horizon"),
        "12_strongest_MID_shift_bps": best.get("mid_shift_bps"),
        "13_strongest_LONG_improvement_bps": best.get("long_impr_bps"),
        "14_strongest_SHORT_improvement_bps": best.get("short_impr_bps"),
        "15_executable_markout_positive_anywhere": exec_pos,
        "16_EARLY_direction": best.get("early_direction"),
        "17_LATE_direction": best.get("late_direction"),
        "18_internal_direction_hold": hold_best,
        "19_placebo_comparison": {
            "causal_mid_shift_bps": best.get("mid_shift_bps"),
            "placebo_mid_shift_bps": best.get("placebo_mid_shift_bps"),
            "causal_beats_placebo": beats_placebo,
            "shift_sec": 300,
        },
        "20_top_symbol_exclusion_hold": exclusion_hold,
        "21_preopen_relation": pre,
        "22_exact_mechanism_candidate_sentence": relation_sentence(best),
        "23_CASE": case,
        "24_ENTRY_built": False,
        "25_EXIT_built": False,
        "26_Runtime_changed": False,
        "27_Paper_changed": False,
        "28_submit_cancel_live": f"{orders['submit']}/{orders['cancel']}/{orders['live']}",
        "29_VERDICT": verdict,
        "30_NEXT": nxt,
    }

    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "PARENT_ID": PARENT_ID,
        "KIND": KIND,
        "trading_date": day,
        "clock": datetime.now(JST).isoformat(timespec="seconds"),
        "frozen_features": list(FEATURE_NAMES),
        "statistical_unit": "CLOCK",
        "stock_n": table["symbol_n"],
        "anchor_clock_n": table["anchor_clock_n"],
        "stock_anchor_n": table["stock_anchor_n"],
        "future_leakage_n": table["future_leakage_n"],
        "clocks": [{k: v for k, v in c.items() if k != "features" or True} for c in clocks],
        "signed_rows": signed_rows,
        "agreement_rows": agreement_rows,
        "best": best,
        "exclusion_drop1": excl1,
        "exclusion_drop2": excl2,
        "concentrated_only": concentrated,
        "stronger_day1_candidate": stronger,
        "preopen": pre,
        "answers": answers,
        "decision": {
            "CASE": case,
            "VERDICT": verdict,
            "NEXT": nxt,
            "TRUE_OOS": False,
            "CERTIFIED": False,
            "ENTRY": False,
            "EXIT": False,
            "STRATEGY_BUILT": False,
        },
        "orders": orders,
        "TRUE_OOS": False,
        "CERTIFIED": False,
    }
