"""Exact frozen Day2 confirmation. No substitutions. No 20260911 mining."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.futures_x_stock_state_day1_candidate_mechanism_audit_v1 import DAY1_SECONDARY_ROWS
from research.futures_x_stock_state_day2_confirmation_v1 import (
    ANALYSIS_ID,
    DAY1_TRADING_DATE,
    ENTRY,
    EXIT,
    KIND,
    NEXT_FAIL,
    NEXT_PASS,
    NEXT_WAIT,
    PAPER_CHANGED,
    PARENT_ID,
    PRIMARY_BUCKET,
    PRIMARY_DIRECTION,
    PRIMARY_FAMILY,
    PRIMARY_HORIZON,
    PRIMARY_SELECTOR,
    RUNTIME_CHANGED,
    SECONDARY_MAY_RESCUE,
    SUBSTITUTIONS_ALLOWED,
    TRADING_DATE,
    VERDICT_FAIL,
    VERDICT_NOT_RUN,
    VERDICT_PASS,
)
from research.futures_x_stock_state_day2_confirmation_v1.isolation import FREEZE_PARENT, NATIVE
from research.futures_x_stock_state_interaction_day1_v1 import MATERIAL_BPS, MIN_CONTEXT_CLOCK_N
from research.futures_x_stock_state_interaction_day1_v1.analyze import _bucket_clocks, _eval_row
from research.futures_x_stock_state_interaction_day1_v1.engine import build_interaction_table
from research.new_causal_information_acquisition_v1.completeness import evaluate_day
from research.new_causal_information_acquisition_v1.launcher import live_order_counts

JST = ZoneInfo("Asia/Tokyo")
GATE_KEYS = (
    "C1_BOTH_DOWN_clock_n_ge_3",
    "C2_TOP_BOTTOM_MID_gt_5bps",
    "C3_incremental_lift_vs_BASE_A_gt_5bps",
    "C4_TOP_BOTTOM_LONG_spread_gt_0",
    "C5_TOP_LONG_absolute_mean_gt_0",
    "C6_drop_top_clock_TOP_BOTTOM_MID_gt_0",
    "C7_drop_top_symbol_TOP_BOTTOM_MID_gt_0",
)


def _gt(v: Any, thresh: float) -> bool:
    try:
        return v is not None and float(v) > float(thresh)
    except (TypeError, ValueError):
        return False


def not_evaluated_gates() -> dict[str, Any]:
    """NOT_FULL / table-error: unevaluated, not a strategy FAIL."""
    return {
        "evaluated": False,
        "gate_status": "NOT_EVALUATED",
        "gates": {k: None for k in GATE_KEYS},
        "all_c1_c7": None,
        "strong_confirmation": None,
        "PASS": None,
        "FAIL": False,
    }


def evaluate_primary_gates(row: dict[str, Any]) -> dict[str, Any]:
    """Frozen C1-C7 on a FULL tape only. Secondary rows are not inputs."""
    c1 = int(row.get("context_clock_n") or 0) >= MIN_CONTEXT_CLOCK_N and int(row.get("ranking_clock_n") or 0) >= MIN_CONTEXT_CLOCK_N
    c2 = _gt(row.get("interaction_mid_spread"), MATERIAL_BPS)
    c3 = _gt(row.get("lift_mid"), MATERIAL_BPS)
    c4 = _gt(row.get("interaction_long_spread"), 0.0)
    c5 = _gt(row.get("top_long_mean"), 0.0)
    c6 = _gt(row.get("drop_top_clock_spread"), 0.0)
    c7 = _gt(row.get("drop_top_symbol_spread"), 0.0)
    gates = {
        "C1_BOTH_DOWN_clock_n_ge_3": bool(c1),
        "C2_TOP_BOTTOM_MID_gt_5bps": bool(c2),
        "C3_incremental_lift_vs_BASE_A_gt_5bps": bool(c3),
        "C4_TOP_BOTTOM_LONG_spread_gt_0": bool(c4),
        "C5_TOP_LONG_absolute_mean_gt_0": bool(c5),
        "C6_drop_top_clock_TOP_BOTTOM_MID_gt_0": bool(c6),
        "C7_drop_top_symbol_TOP_BOTTOM_MID_gt_0": bool(c7),
    }
    strong = _gt(row.get("top_long_median"), 0.0)
    passed = all(gates.values())
    return {
        "evaluated": True,
        "gate_status": "EVALUATED",
        "gates": gates,
        "all_c1_c7": bool(passed),
        "strong_confirmation": {"TOP_LONG_median_gt_0": bool(strong)},
        "PASS": bool(passed),
        "FAIL": (not passed),
    }


def resolve_primary_gates(*, full: bool, table_error: str = "", row: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Boolean C1-C7 only after FULL capture. Missing tape is NOT_EVALUATED."""
    if (not full) or table_error:
        return not_evaluated_gates()
    return evaluate_primary_gates(row or {})


def _load_freeze(native_root: Path) -> dict[str, Any]:
    path = Path(native_root) / "results" / "research" / "futures_x_stock_state_day1_candidate_mechanism_audit_v1" / "day2_freeze_manifest.json"
    if not path.is_file():
        path = FREEZE_PARENT / "day2_freeze_manifest.json"
    if not path.is_file():
        return {"ok": False, "path": str(path)}
    body = json.loads(path.read_text(encoding="utf-8"))
    primary = dict(body.get("primary") or {})
    ok = (
        str(body.get("analysis_id") or "") == ANALYSIS_ID
        and primary.get("selector") == PRIMARY_SELECTOR
        and primary.get("context_bucket") == PRIMARY_BUCKET
        and primary.get("horizon") == PRIMARY_HORIZON
        and body.get("substitutions_allowed") is False
    )
    return {"ok": bool(ok), "path": str(path), "manifest": body}


def _empty_primary() -> dict[str, Any]:
    return {
        "selector": PRIMARY_SELECTOR,
        "family": PRIMARY_FAMILY,
        "bucket": PRIMARY_BUCKET,
        "horizon": PRIMARY_HORIZON,
        "context_clock_n": None,
        "ranking_clock_n": None,
        "insufficient_clock_support": None,
        "base_a_mid_spread": None,
        "interaction_mid_spread": None,
        "top_mid": None,
        "bottom_mid": None,
        "lift_mid": None,
        "interaction_long_spread": None,
        "top_long_mean": None,
        "top_long_median": None,
        "drop_top_clock_spread": None,
        "drop_top_symbol_spread": None,
        "candidate": None,
        "ctx_clocks": [],
        "clock_mid_spreads": {},
    }


def run_day2_confirmation(
    *,
    native_root: Optional[Path] = None,
    trading_date: str = TRADING_DATE,
) -> dict[str, Any]:
    root = Path(native_root) if native_root else NATIVE
    day = str(trading_date or TRADING_DATE).replace("-", "")
    if day == DAY1_TRADING_DATE:
        raise ValueError("20260911 mining is closed; Day2 confirmation cannot reuse Day1 raw as the confirmation day")
    freeze = _load_freeze(root)
    completeness = evaluate_day(day, native_root=root)
    orders = live_order_counts()
    nk = dict(completeness.get("nk225mini") or {})
    tx = dict(completeness.get("topix") or {})
    nk_present = int(nk.get("event_n") or 0) > 0
    tx_present = int(tx.get("event_n") or 0) > 0
    full = bool(completeness.get("FULL"))
    table: dict[str, Any] = {}
    primary = _empty_primary()
    secondary: list[dict[str, Any]] = []
    table_error = ""
    if full:
        try:
            table = build_interaction_table(native_root=root, day=day)
            clocks = list(table.get("clocks") or [])
            ctx = _bucket_clocks(clocks, PRIMARY_FAMILY, PRIMARY_BUCKET)
            primary = _eval_row(
                selector=PRIMARY_SELECTOR,
                family=PRIMARY_FAMILY,
                bucket=PRIMARY_BUCKET,
                horizon=PRIMARY_HORIZON,
                all_clocks=clocks,
                ctx_clocks_raw=ctx,
            )
            for sel, fam, buck, hor in DAY1_SECONDARY_ROWS:
                secondary.append(
                    _eval_row(
                        selector=sel,
                        family=fam,
                        bucket=buck,
                        horizon=hor,
                        all_clocks=clocks,
                        ctx_clocks_raw=_bucket_clocks(clocks, fam, buck),
                    )
                )
        except Exception as exc:
            table_error = f"{type(exc).__name__}:{exc}"
            primary = _empty_primary()
            secondary = []
    judged = resolve_primary_gates(full=full, table_error=table_error, row=primary)
    if not judged.get("evaluated"):
        verdict = VERDICT_NOT_RUN
        nxt = NEXT_WAIT
        primary_pass = None
        status = "NOT_FULL" if not full else "TABLE_ERROR"
    elif judged["PASS"]:
        verdict = VERDICT_PASS
        nxt = NEXT_PASS
        primary_pass = True
        status = "PASS"
    else:
        verdict = VERDICT_FAIL
        nxt = NEXT_FAIL
        primary_pass = False
        status = "FAIL"
    answers = {
        "1_FULL": full,
        "2_stock_N": completeness.get("stock_symbol_n"),
        "3_NK_present": nk_present,
        "4_TOPIX_present": tx_present,
        "5_Day2_BOTH_DOWN_clock_N": primary.get("context_clock_n"),
        "6_TOP_MID": primary.get("top_mid"),
        "7_BOTTOM_MID": primary.get("bottom_mid"),
        "8_TOP_BOTTOM": primary.get("interaction_mid_spread"),
        "9_BASE_A": primary.get("base_a_mid_spread"),
        "10_incremental_lift": primary.get("lift_mid"),
        "11_TOP_LONG_mean": primary.get("top_long_mean"),
        "12_TOP_LONG_median": primary.get("top_long_median"),
        "13_primary_PASS_FAIL": (
            "PASS" if primary_pass is True else ("FAIL" if primary_pass is False else "NOT_RUN")
        ),
        "14_secondary_rescue_used": False,
    }
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "PARENT_ID": PARENT_ID,
        "KIND": KIND,
        "clock": datetime.now(JST).isoformat(timespec="seconds"),
        "trading_date": day,
        "day1_mining_closed": True,
        "day1_trading_date": DAY1_TRADING_DATE,
        "status": status,
        "table_error": table_error or None,
        "freeze": freeze,
        "completeness": {
            "FULL": full,
            "stock_symbol_n": completeness.get("stock_symbol_n"),
            "nk_present": nk_present,
            "topix_present": tx_present,
            "nk_event_n": nk.get("event_n"),
            "topix_event_n": tx.get("event_n"),
        },
        "frozen_primary": {
            "context": f"{PRIMARY_FAMILY}_{PRIMARY_BUCKET}",
            "selector": PRIMARY_SELECTOR,
            "rank": "TOP16 / BOTTOM16",
            "horizon": PRIMARY_HORIZON,
            "direction": PRIMARY_DIRECTION,
            "substitutions_allowed": SUBSTITUTIONS_ALLOWED,
        },
        "primary_row": primary,
        "primary_gates": judged,
        "secondary_rows": [
            {
                "selector": r.get("selector"),
                "family": r.get("family"),
                "bucket": r.get("bucket"),
                "horizon": r.get("horizon"),
                "use": "ACTIVITY_FAMILY_COHERENCE_ONLY",
                "context_clock_n": r.get("context_clock_n"),
                "interaction_mid_spread": r.get("interaction_mid_spread"),
                "lift_mid": r.get("lift_mid"),
                "top_long_mean": r.get("top_long_mean"),
                "cannot_rescue_primary": True,
            }
            for r in secondary
        ],
        "future_leakage_n": table.get("future_leakage_n"),
        "anchor_clock_n": table.get("anchor_clock_n"),
        "answers": answers,
        "decision": {
            "CASE": status,
            "VERDICT": verdict,
            "NEXT": nxt,
            "PASS": primary_pass,
            "FAIL": False if primary_pass is None else (not primary_pass),
            "secondary_rescue_used": False,
            "substitutions_allowed": False,
            "ENTRY": ENTRY,
            "EXIT": EXIT,
            "RUNTIME_CHANGED": RUNTIME_CHANGED,
            "PAPER_CHANGED": PAPER_CHANGED,
            "ten_m_is_diagnostic_not_exit": True,
            "PASS_does_not_mean_ENTRY": True,
            "TRUE_OOS": False,
            "CERTIFIED": False,
        },
        "orders": orders,
        "submit_cancel_live": f"{orders.get('submit', 0)}/{orders.get('cancel', 0)}/{orders.get('live', 0)}",
        "SECONDARY_MAY_RESCUE": SECONDARY_MAY_RESCUE,
        "TRUE_OOS": False,
        "CERTIFIED": False,
    }
