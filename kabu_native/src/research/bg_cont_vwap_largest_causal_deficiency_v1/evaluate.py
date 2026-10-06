"""Compare E0/E1/E2. Design evidence only. No promotion on pooled mean alone."""
from __future__ import annotations

from typing import Any

from research.bg_cont_vwap_largest_causal_deficiency_v1 import (
    EXPECTED_E0_TRADE_N,
    EXPECTED_E0_X0,
    MATERIAL_X1_BPS,
    SYMBOLS6,
    TAIL_WORSE_PP,
)


def e0_parity(e0: dict[str, Any]) -> dict[str, Any]:
    x0 = e0.get("mean_x0_bps")
    n = int(e0.get("trade_n") or 0)
    ok = n == EXPECTED_E0_TRADE_N and x0 is not None and abs(float(x0) - EXPECTED_E0_X0) < 1e-6
    return {
        "ok": ok,
        "trade_n": n,
        "expected_trade_n": EXPECTED_E0_TRADE_N,
        "mean_x0_bps": x0,
        "expected_mean_x0_bps": EXPECTED_E0_X0,
        "abs_delta_x0": None if x0 is None else abs(float(x0) - EXPECTED_E0_X0),
    }


def _sym_map(pack: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(r.get("symbol")): r for r in list(pack.get("symbols") or [])}


def compare_to_e0(cand: dict[str, Any], e0: dict[str, Any]) -> dict[str, Any]:
    dx0 = float(cand["mean_x0_bps"]) - float(e0["mean_x0_bps"])
    dx1 = float(cand["mean_x1_bps"]) - float(e0["mean_x1_bps"])
    gb0 = (e0.get("giveback") or {}).get("average_giveback")
    gb1 = (cand.get("giveback") or {}).get("average_giveback")
    dgb = None if gb0 is None or gb1 is None else float(gb1) - float(gb0)
    tail0 = float((e0.get("tail") or {}).get("share_positive_pnl_top_5pct") or 0)
    tail1 = float((cand.get("tail") or {}).get("share_positive_pnl_top_5pct") or 0)
    ptl0 = (e0.get("giveback") or {}).get("fraction_profitable_then_loss")
    ptl1 = (cand.get("giveback") or {}).get("fraction_profitable_then_loss")
    large0 = (e0.get("giveback") or {}).get("fraction_large_winner")
    large1 = (cand.get("giveback") or {}).get("fraction_large_winner")
    s0 = _sym_map(e0)
    s1 = _sym_map(cand)
    improved = []
    worsened = []
    for sym in SYMBOLS6:
        a = (s0.get(sym) or {}).get("X0_mean")
        b = (s1.get(sym) or {}).get("X0_mean")
        if a is None or b is None:
            continue
        if float(b) > float(a):
            improved.append(sym)
        elif float(b) < float(a):
            worsened.append(sym)
    ex0 = (e0.get("exclude_8136") or {}).get("mean_x0_bps")
    ex1 = (cand.get("exclude_8136") or {}).get("mean_x0_bps")
    not_only_8136 = bool(ex0 is not None and ex1 is not None and float(ex1) > float(ex0) and len(improved) >= 2)
    b0 = dict(e0.get("block_mean_x0") or {})
    b1 = dict(cand.get("block_mean_x0") or {})
    d2 = None if b0.get("D2") is None or b1.get("D2") is None else float(b1["D2"]) - float(b0["D2"])
    d3 = None if b0.get("D3") is None or b1.get("D3") is None else float(b1["D3"]) - float(b0["D3"])
    d4 = None if b0.get("D4") is None or b1.get("D4") is None else float(b1["D4"]) - float(b0["D4"])
    d23_0 = (e0.get("d2_d3") or {}).get("mean_x0_bps")
    d23_1 = (cand.get("d2_d3") or {}).get("mean_x0_bps")
    d23 = None if d23_0 is None or d23_1 is None else float(d23_1) - float(d23_0)
    d2d3_ok = bool(
        d23 is not None
        and d23 > 0
        and (d2 is None or d2 >= -MATERIAL_X1_BPS)
        and (d3 is None or d3 >= -MATERIAL_X1_BPS)
    )
    a_x0 = dx0 > 0
    b_x1 = float(cand["mean_x1_bps"]) > 0 or dx1 >= MATERIAL_X1_BPS
    c_gb = bool(dgb is not None and dgb < 0)
    d_tail = (tail1 - tail0) < TAIL_WORSE_PP
    e_sym = not_only_8136
    f_blocks = d2d3_ok
    survive = bool(a_x0 and b_x1 and c_gb and d_tail and e_sym and f_blocks)
    return {
        "exit_id": cand.get("exit_id"),
        "delta_x0": dx0,
        "delta_x1": dx1,
        "delta_giveback_mean": dgb,
        "delta_ptl_frac": None if ptl0 is None or ptl1 is None else float(ptl1) - float(ptl0),
        "delta_large_winner_frac": None if large0 is None or large1 is None else float(large1) - float(large0),
        "delta_top5_pos_share": tail1 - tail0,
        "delta_occupancy_skips": int(cand.get("occupancy_skips") or 0) - int(e0.get("occupancy_skips") or 0),
        "delta_same_symbol_skips": int(cand.get("same_symbol_skips") or 0) - int(e0.get("same_symbol_skips") or 0),
        "delta_immediate_failure_path_n": int(cand.get("immediate_failure_path_n") or 0) - int(e0.get("immediate_failure_path_n") or 0),
        "delta_hold_min": None
        if cand.get("mean_hold_min") is None or e0.get("mean_hold_min") is None
        else float(cand["mean_hold_min"]) - float(e0["mean_hold_min"]),
        "symbols_improved": improved,
        "symbols_worsened": worsened,
        "exclude_8136_delta_x0": None if ex0 is None or ex1 is None else float(ex1) - float(ex0),
        "D2_delta_x0": d2,
        "D3_delta_x0": d3,
        "D4_delta_x0": d4,
        "D2_D3_delta_x0": d23,
        "A_x0_improves": a_x0,
        "B_x1_positive_or_material": b_x1,
        "C_giveback_decreases": c_gb,
        "D_tail_not_materially_worse": d_tail,
        "E_not_only_8136": e_sym,
        "F_d2_d3_supportive": f_blocks,
        "survives": survive,
        "pooled_mean_alone_not_sufficient": True,
        "design_evidence_not_certification": True,
        "d4_cannot_certify_group": True,
    }


def pick_winner(e0: dict[str, Any], e1: dict[str, Any], e2: dict[str, Any], c1: dict[str, Any], c2: dict[str, Any]) -> dict[str, Any]:
    survivors = []
    if c1.get("survives"):
        survivors.append("E1")
    if c2.get("survives"):
        survivors.append("E2")
    e2_beats_e1 = bool(
        e2.get("ok")
        and e1.get("ok")
        and float(e2["mean_x0_bps"]) > float(e1["mean_x0_bps"])
        and float((e2.get("giveback") or {}).get("average_giveback") or 9e9) <= float((e1.get("giveback") or {}).get("average_giveback") or 9e9)
    )
    winner = None
    if survivors == ["E1"]:
        winner = "E1"
    elif survivors == ["E2"]:
        winner = "E2"
    elif survivors == ["E1", "E2"]:
        winner = "E2" if e2_beats_e1 else "E1"
    return {
        "survivors": survivors,
        "winning_exit": winner,
        "e2_outperforms_e1": e2_beats_e1,
        "hypothesis_after_continuation_one_bar_is_noise": e2_beats_e1,
        "freeze_exactly_one": winner,
    }
