"""V22 capacity distributions and CASE A/B/C/D. 1M feasible is diagnostic only."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.simple_tech_entry_family.v13_analyze import fill_tuples, set_hash
from research.simple_tech_exit_family.v14_analyze import _finite
from research.simple_tech_exit_family.v19_analyze import identity_hashes
from research.simple_tech_strategy.v22_spec import (
    ACTUAL_EXIT_SET_HASH_EXPECTED,
    B1_EXECUTABLE_N_EXPECTED,
    E4_FILL_SET_HASH_EXPECTED,
    E4_FILLED_N_EXPECTED,
    EXIT_INPUT_FILL_SET_HASH_EXPECTED,
    LOT_SHARES,
    MULTILOT_MIN_DAY_N,
    MULTILOT_MIN_TRADE_N,
    SCHEDULED_EXIT_SET_HASH_EXPECTED,
    SHARES_100,
)


def _close(a: Any, b: Any, tol: float) -> bool:
    try:
        return abs(float(a) - float(b)) <= float(tol)
    except (TypeError, ValueError):
        return False


def dist_pack(xs: list[Any]) -> dict[str, Any]:
    vals = [float(x) for x in xs if _finite(x)]
    if not vals:
        return {"N": 0}
    arr = np.asarray(vals, dtype=float)
    def _pct(p: float) -> float:
        try:
            return float(np.percentile(arr, p, method="closest_observation"))
        except TypeError:
            return float(np.percentile(arr, p, interpolation="nearest"))
    return {
        "N": len(vals),
        "min": float(np.min(arr)),
        "p25": _pct(25),
        "median": float(np.median(arr)),
        "p75": _pct(75),
        "max": float(np.max(arr)),
        "mean": float(np.mean(arr)),
    }


def count_ge(xs: list[Any], thresh: int) -> int:
    return sum(1 for x in xs if _finite(x) and int(x) >= int(thresh))


def count_eq(xs: list[Any], thresh: int) -> int:
    return sum(1 for x in xs if _finite(x) and int(x) == int(thresh))


def eligible_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if r.get("executable_signal")]


def filled_100_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if r.get("filled_100") or ((r.get("exec") or {}).get("E4_INSIDE1_W5") or {}).get("filled")]


def identity_trade_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in filled_100_rows(rows):
        rec = dict(r)
        rec["symbol"] = str(rec.get("symbol") or "").replace(".T", "")
        rec["fill_t"] = rec.get("fill_t_100") if _finite(rec.get("fill_t_100")) else rec.get("fill_t")
        rec["fill_price"] = rec.get("fill_price_100") if _finite(rec.get("fill_price_100")) else rec.get("fill_price")
        out.append(rec)
    return out


def parity_pack(rows: list[dict[str, Any]]) -> dict[str, Any]:
    exe = eligible_rows(rows)
    fills = identity_trade_rows(rows)
    fill_hash = set_hash(fill_tuples(exe))
    hashes = identity_hashes(fills)
    filled_n = len(fills)
    ok = bool(
        len(exe) == int(B1_EXECUTABLE_N_EXPECTED)
        and filled_n == int(E4_FILLED_N_EXPECTED)
        and fill_hash == E4_FILL_SET_HASH_EXPECTED
        and str(hashes.get("EXIT_INPUT_FILL_SET_HASH") or "") == EXIT_INPUT_FILL_SET_HASH_EXPECTED
        and str(hashes.get("SCHEDULED_EXIT_SET_HASH") or "") == SCHEDULED_EXIT_SET_HASH_EXPECTED
        and str(hashes.get("ACTUAL_EXIT_SET_HASH") or "") == ACTUAL_EXIT_SET_HASH_EXPECTED
    )
    return {
        "ELIGIBLE_N": len(exe),
        "FILLED_N_100": filled_n,
        "E4_FILL_SET_HASH": fill_hash,
        "E4_100SHARE_FILL_PARITY": bool(fill_hash == E4_FILL_SET_HASH_EXPECTED and filled_n == int(E4_FILLED_N_EXPECTED)),
        "EXIT_INPUT_FILL_SET_HASH": hashes.get("EXIT_INPUT_FILL_SET_HASH"),
        "SCHEDULED_EXIT_SET_HASH": hashes.get("SCHEDULED_EXIT_SET_HASH"),
        "ACTUAL_EXIT_SET_HASH": hashes.get("ACTUAL_EXIT_SET_HASH"),
        "ACTUAL_EXIT_SET_HASH_PARITY": str(hashes.get("ACTUAL_EXIT_SET_HASH") or "") == ACTUAL_EXIT_SET_HASH_EXPECTED,
        "PARITY_OK": ok,
    }


def capability_pack(rows: list[dict[str, Any]], leak: dict[str, Any]) -> dict[str, Any]:
    exe = eligible_rows(rows)
    fills = identity_trade_rows(rows)
    entry_qty_ok = all(_finite(r.get("max_ask_cross_qty")) for r in fills)
    exit_qty_ok = all(_finite(r.get("exit_bid_qty")) for r in fills)
    entry_det = all(r.get("ENTRY_MAX_FULL_FILL_SHARES") is not None for r in exe)
    exit_det = all(r.get("EXIT_MAX_FULL_SHARES") is not None for r in fills)
    miss_entry = int(leak.get("MISSING_ENTRY_QTY_N") or 0)
    miss_exit = int(leak.get("MISSING_EXIT_QTY_N") or 0)
    miss_exit_bid = int(leak.get("EXIT_BID_MISS_N") or 0)
    entry_cap = bool(
        entry_det
        and entry_qty_ok
        and miss_entry == 0
        and int(leak.get("ENTRY_CAPACITY_LT_100_N") or 0) == 0
        and len(exe) == int(B1_EXECUTABLE_N_EXPECTED)
    )
    exit_cap = bool(
        exit_det
        and exit_qty_ok
        and miss_exit == 0
        and miss_exit_bid == 0
        and int(leak.get("EXIT_CAPACITY_LT_100_N") or 0) == 0
        and len(fills) == int(E4_FILLED_N_EXPECTED)
    )
    return {
        "ENTRY_SIZE_REPLAY_CAPABILITY": entry_cap,
        "EXIT_SIZE_REPLAY_CAPABILITY": exit_cap,
        "EVIDENCE_COMPLETE": bool(entry_cap and exit_cap),
        "MISSING_ENTRY_QTY_N": miss_entry,
        "MISSING_EXIT_QTY_N": miss_exit,
        "EXIT_BID_MISS_N": miss_exit_bid,
        "ENTRY_CAPACITY_LT_100_N": int(leak.get("ENTRY_CAPACITY_LT_100_N") or 0),
        "EXIT_CAPACITY_LT_100_N": int(leak.get("EXIT_CAPACITY_LT_100_N") or 0),
    }


def capacity_counts(xs: list[Any]) -> dict[str, Any]:
    return {
        "CAPACITY_EQ_100_N": count_eq(xs, 100),
        "CAPACITY_GE_200_N": count_ge(xs, 200),
        "CAPACITY_GE_300_N": count_ge(xs, 300),
        "CAPACITY_GE_500_N": count_ge(xs, 500),
        "CAPACITY_GE_1000_N": count_ge(xs, 1000),
    }


def day_multilot_n(rows: list[dict[str, Any]], thresh: int = 200) -> int:
    days = set()
    for r in identity_trade_rows(rows):
        if _finite(r.get("ROUNDTRIP_MAX_FULL_SHARES")) and int(r["ROUNDTRIP_MAX_FULL_SHARES"]) >= int(thresh):
            days.add(str(r.get("date") or ""))
    days.discard("")
    return len(days)


def analyze_capacity(rows: list[dict[str, Any]]) -> dict[str, Any]:
    exe = eligible_rows(rows)
    fills = identity_trade_rows(rows)
    entry_xs = [r.get("ENTRY_MAX_FULL_FILL_SHARES") for r in exe]
    exit_xs = [r.get("EXIT_MAX_FULL_SHARES") for r in fills]
    rt_xs = [r.get("ROUNDTRIP_MAX_FULL_SHARES") for r in fills]
    feas = [r for r in fills if r.get("NORMALIZED_1M_EXECUTION_FEASIBLE")]
    snap_gt_window = 0
    window_gt_snap = 0
    for r in fills:
        a = int(r.get("FILL_SNAPSHOT_SHARES") or 0)
        b = int(r.get("ENTRY_MAX_FULL_FILL_SHARES") or 0)
        if b > a:
            window_gt_snap += 1
        if a > b:
            snap_gt_window += 1
    counts = capacity_counts(rt_xs)
    ge200_days = day_multilot_n(rows, 200)
    return {
        "ENTRY_CAPACITY_DISTRIBUTION": dist_pack(entry_xs),
        "EXIT_CAPACITY_DISTRIBUTION": dist_pack(exit_xs),
        "ROUNDTRIP_CAPACITY_DISTRIBUTION": dist_pack(rt_xs),
        **counts,
        "MULTILOT_DAY_N_GE_200": ge200_days,
        "NORMALIZED_1M_EXECUTION_FEASIBLE_N": len(feas),
        "WINDOW_GT_FILL_SNAPSHOT_N": window_gt_snap,
        "SNAPSHOT_GT_WINDOW_N": snap_gt_window,
        "ENTRY_COUNTS_ELIGIBLE": capacity_counts(entry_xs),
        "LOT_SHARES": int(LOT_SHARES),
        "SHARES_100": int(SHARES_100),
    }


def flatten_capacity_trade(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "date": r.get("date"),
        "symbol": str(r.get("symbol") or "").replace(".T", ""),
        "t0": r.get("t0"),
        "executable_signal": bool(r.get("executable_signal")),
        "filled_100": bool(r.get("filled_100")),
        "ENTRY_ORDER_PRICE_CAUSAL": r.get("ENTRY_ORDER_PRICE_CAUSAL"),
        "fill_t_100": r.get("fill_t_100"),
        "fill_price_100": r.get("fill_price_100"),
        "fill_ask_qty_100": r.get("fill_ask_qty_100"),
        "FILL_SNAPSHOT_SHARES": r.get("FILL_SNAPSHOT_SHARES"),
        "max_ask_cross_qty": r.get("max_ask_cross_qty"),
        "ENTRY_MAX_FULL_FILL_SHARES": r.get("ENTRY_MAX_FULL_FILL_SHARES"),
        "scheduled_exit_time": r.get("scheduled_exit_time"),
        "actual_exit_quote_time": r.get("actual_exit_quote_time"),
        "exit_bid": r.get("exit_bid"),
        "exit_bid_qty": r.get("exit_bid_qty"),
        "EXIT_MAX_FULL_SHARES": r.get("EXIT_MAX_FULL_SHARES"),
        "ROUNDTRIP_MAX_FULL_SHARES": r.get("ROUNDTRIP_MAX_FULL_SHARES"),
        "ROUNDTRIP_CAPACITY_NOTIONAL_YEN": r.get("ROUNDTRIP_CAPACITY_NOTIONAL_YEN"),
        "THEORETICAL_1M_SHARES": r.get("THEORETICAL_1M_SHARES"),
        "NORMALIZED_1M_EXECUTION_FEASIBLE": r.get("NORMALIZED_1M_EXECUTION_FEASIBLE"),
        "normalized_is_policy": False,
    }


def decision_case(
    *,
    integrity_ok: bool,
    parity_ok: bool,
    cap: dict[str, Any],
    stats: dict[str, Any],
) -> dict[str, Any]:
    if (not integrity_ok) or (not parity_ok):
        return {
            "CASE": "D",
            "VERDICT": "SIMPLE_TECH_V22_INVALID",
            "NEXT": "STOP. 100-share parity or integrity failed. Do not size. Do not mutate V20/V21.",
        }
    entry_ok = bool(cap.get("ENTRY_SIZE_REPLAY_CAPABILITY"))
    exit_ok = bool(cap.get("EXIT_SIZE_REPLAY_CAPABILITY"))
    complete = bool(cap.get("EVIDENCE_COMPLETE"))
    if (not entry_ok) or (not exit_ok) or (not complete):
        return {
            "CASE": "C",
            "VERDICT": "SIMPLE_TECH_V22_SIZING_EXECUTION_EVIDENCE_UNRESOLVED",
            "NEXT": "STOP. Quantity-aware replay evidence incomplete. Do not guess multi-lot fills. Do not adopt sizing.",
        }
    ge200 = int(stats.get("CAPACITY_GE_200_N") or 0)
    days = int(stats.get("MULTILOT_DAY_N_GE_200") or 0)
    if ge200 >= int(MULTILOT_MIN_TRADE_N) and days >= int(MULTILOT_MIN_DAY_N):
        return {
            "CASE": "A",
            "VERDICT": "SIMPLE_TECH_V22_MULTILOT_EXECUTION_CAPACITY_CONFIRMED",
            "NEXT": "STOP. Position sizing policy may be precommitted on a later run. Do not adopt a target notional here. 1M is not a strategy. V20/V21 official unchanged. TRUE_OOS=false.",
        }
    return {
        "CASE": "B",
        "VERDICT": "SIMPLE_TECH_V22_MULTILOT_CAPACITY_INSUFFICIENT",
        "NEXT": "STOP. Do not proceed to sizing economics. Frozen 100-share execution remains the only proven size. V20/V21 official unchanged.",
    }
