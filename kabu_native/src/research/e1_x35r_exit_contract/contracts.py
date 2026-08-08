"""Contract extraction + episode-level PATH vs FIXED identity."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Optional

import numpy as np

from research.e1_x28_executable_joint.board import BOARD_FRESHNESS_SEC, MIN_QTY
from research.e1_x35_passive_exit.exits import simulate_exit

from . import HORIZONS


def contract_table() -> list[dict[str, Any]]:
    """Side-by-side PATH_EXEC_H vs E0_FIXED_H from X35 source semantics."""
    rows = []
    fields = [
        ("entry_timestamp", "fill_time (conservative passive fill)", "fill_time (same path.entry_t)"),
        ("entry_price", "passive limit fill_price", "passive limit fill_price"),
        ("horizon_origin", "fill_time", "fill_time"),
        ("target_timestamp", "fill_time + H (mark)", "fill_time + H (trigger)"),
        (
            "quote_lookup_direction",
            "BACKWARD: last valid bid with offs <= H",
            "FORWARD: first valid bid with offs >= H",
        ),
        (
            "lookup_method",
            "np.searchsorted(offs, H, side='right') - 1",
            "path walk until o >= H (simulate_exit FIXED_HOLD)",
        ),
        ("lookup_tolerance_window", "none (discrete board ticks only)", "none (discrete board ticks only)"),
        ("first_or_last_quote", "LAST at-or-before target", "FIRST at-or-after target"),
        ("freshness_requirement", f"<= {BOARD_FRESHNESS_SEC}s (path filter)", f"<= {BOARD_FRESHNESS_SEC}s (path filter)"),
        ("buy1_qty_requirement", f">= {MIN_QTY}", f">= {MIN_QTY}"),
        ("special_quote", "excluded", "excluded"),
        ("same_session", "path truncated at session_end", "path truncated at session_end"),
        (
            "session_close_handling",
            "if path ends before H, _at(H) still returns last available bid (<=H) without SESSION_CLOSE label",
            "if no tick reaches H, explicit SESSION_CLOSE at last available bid",
        ),
        (
            "missing_target_handling",
            "returns last available <=H (may be early); None only if no quotes at all",
            "SESSION_CLOSE at last bid; never synthetic",
        ),
        ("early_late_quote_allowance", "allows early (offs < H)", "allows late (offs > H) first tick"),
        ("return_denominator", "fill_price (bps)", "fill_price (bps)"),
        ("price_side", "Buy1.Price (bid) only", "Buy1.Price (bid) only"),
        ("mid_or_synthetic", "mid diagnostic only; not used for EXIT ret", "no mid/synthetic"),
        ("code_locus", "paths.path_metrics._at", "exits.simulate_exit fixed_hold_sec"),
    ]
    for name, path_v, fixed_v in fields:
        rows.append({"field": name, "PATH_EXEC_H": path_v, "E0_FIXED_H": fixed_v})
    return rows


def path_exit_at(path: dict[str, Any], H: float) -> dict[str, Any]:
    """Reproduce PATH_EXEC_H: last valid bid with offs <= H."""
    if not path.get("ok") or path["offs"].size == 0:
        return {"ok": False, "reason": "NO_PATH"}
    offs, rets, times = path["offs"], path["rets"], path["times"]
    j = int(np.searchsorted(offs, H, side="right") - 1)
    if j < 0:
        return {"ok": False, "reason": "NO_QUOTE_BEFORE_TARGET"}
    o = float(offs[j])
    # classify
    if o + 1e-12 < H and abs(o - float(offs[-1])) < 1e-12 and float(offs[-1]) + 1e-12 < H:
        reason = "SESSION_CLOSE_AS_MARK"  # last path point before H used as mark
    elif abs(o - H) < 1e-9:
        reason = "EXACT_TARGET"
    else:
        reason = "LAST_AT_OR_BEFORE"
    return {
        "ok": True,
        "exit_time": float(times[j]),
        "exit_off": o,
        "exit_ret_bps": float(rets[j]),
        "exit_bid": float(path["entry_price"]) * (1.0 + float(rets[j]) / 10000.0),
        "reason": reason,
    }


def fixed_exit_at(path: dict[str, Any], H: float) -> dict[str, Any]:
    """Reproduce E0_FIXED_H via simulate_exit."""
    r = simulate_exit(path, fixed_hold_sec=float(H))
    if not r.get("ok"):
        return {"ok": False, "reason": "NO_PATH"}
    entry_px = float(path["entry_price"])
    ret = float(r["exit_ret_bps"])
    return {
        "ok": True,
        "exit_time": float(r["exit_time"]),
        "exit_off": float(r["hold_sec"]),
        "exit_ret_bps": ret,
        "exit_bid": entry_px * (1.0 + ret / 10000.0),
        "reason": str(r["reason"]),
    }


def classify_mismatch(path_r: dict, fixed_r: dict, H: float) -> str:
    """Classify PATH vs FIXED mismatch using code-supported reasons only."""
    if not path_r.get("ok") or not fixed_r.get("ok"):
        return "MISSING_QUOTE_POLICY"
    pt, ft = path_r["exit_time"], fixed_r["exit_time"]
    pr, fr = path_r["exit_ret_bps"], fixed_r["exit_ret_bps"]
    if abs(pt - ft) < 1e-9 and abs(pr - fr) < 1e-9:
        return "IDENTICAL"

    # session close cases
    if fixed_r.get("reason") == "SESSION_CLOSE":
        if abs(pr - fr) < 1e-9 and abs(pt - ft) < 1e-9:
            return "SESSION_CLOSE"  # same quote, label differs only in path mark semantics
        return "SESSION_CLOSE"

    # different ticks around horizon → forward vs backward lookup
    po, fo = path_r["exit_off"], fixed_r["exit_off"]
    if po + 1e-12 <= H <= fo + 1e-12 or (po < H and fo >= H):
        return "TARGET_QUOTE_MAPPING"

    if abs(po - fo) > 1e-6:
        return "DIFFERENT_LOOKUP_WINDOW"

    return "OTHER"


def episode_compare(eps: list[dict], H: float) -> list[dict[str, Any]]:
    rows = []
    for e in eps:
        path = e["path"]
        pr = path_exit_at(path, H)
        fr = fixed_exit_at(path, H)
        reason = classify_mismatch(pr, fr, H)
        delta = None
        if pr.get("ok") and fr.get("ok"):
            delta = float(fr["exit_ret_bps"] - pr["exit_ret_bps"])
        rows.append({
            "date": e["date"],
            "symbol": e["symbol"],
            "session": e["session"],
            "entry_time": e["entry_time"],
            "entry_price": e["entry_price"],
            "H": H,
            "path_exit_time": pr.get("exit_time"),
            "path_exit_off": pr.get("exit_off"),
            "path_exit_bid": pr.get("exit_bid"),
            "path_ret_bps": pr.get("exit_ret_bps"),
            "path_reason": pr.get("reason"),
            "fixed_exit_time": fr.get("exit_time"),
            "fixed_exit_off": fr.get("exit_off"),
            "fixed_exit_bid": fr.get("exit_bid"),
            "fixed_ret_bps": fr.get("exit_ret_bps"),
            "fixed_reason": fr.get("reason"),
            "return_delta_bps": delta,
            "mismatch_reason": reason,
        })
    return rows


def summarize_horizon(rows: list[dict]) -> dict[str, Any]:
    path_rets = [r["path_ret_bps"] for r in rows if r.get("path_ret_bps") is not None]
    fixed_rets = [r["fixed_ret_bps"] for r in rows if r.get("fixed_ret_bps") is not None]
    deltas = [r["return_delta_bps"] for r in rows if r.get("return_delta_bps") is not None]
    reasons = Counter(r["mismatch_reason"] for r in rows)
    identical = reasons.get("IDENTICAL", 0)
    mismatch = len(rows) - identical
    mm_deltas = [
        r["return_delta_bps"] for r in rows
        if r["mismatch_reason"] != "IDENTICAL" and r.get("return_delta_bps") is not None
    ]
    by_day_delta: dict[str, float] = defaultdict(float)
    for r in rows:
        if r.get("return_delta_bps") is not None and r["mismatch_reason"] != "IDENTICAL":
            by_day_delta[r["date"]] += float(r["return_delta_bps"])

    def _m(xs):
        return float(np.mean(xs)) if xs else None

    def _med(xs):
        return float(np.median(xs)) if xs else None

    return {
        "n": len(rows),
        "path_mean": _m(path_rets),
        "fixed_mean": _m(fixed_rets),
        "identical_count": identical,
        "mismatch_count": mismatch,
        "reason_breakdown": dict(reasons),
        "mismatch_delta_sum": float(np.sum(mm_deltas)) if mm_deltas else 0.0,
        "mismatch_delta_mean": _m(mm_deltas),
        "mismatch_delta_median": _med(mm_deltas),
        "mismatch_delta_max_pos": float(np.max(mm_deltas)) if mm_deltas else None,
        "mismatch_delta_max_neg": float(np.min(mm_deltas)) if mm_deltas else None,
        "day_level_mismatch_delta_sum": dict(sorted(by_day_delta.items())),
        "all_delta_mean": _m(deltas),
    }


def canonical_fixed_exit(path: dict[str, Any], H: float) -> dict[str, Any]:
    """
    Canonical X36 fixed-horizon EXIT:
    target = entry_t + H; first valid Buy1 at-or-after target;
    if none before session end → SESSION_CLOSE at last valid bid.
    Identical to E0_FIXED_H / simulate_exit(fixed_hold_sec=H).
    """
    return fixed_exit_at(path, H)
