"""Combined 20260914 EOD. No live start. No 20260911 mining. No ENTRY."""
from __future__ import annotations

from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.current_day1_information_close_v1 import FEATURE_MINING_CLOSED, VERDICT as DAY1_CLOSE_VERDICT
from research.day2_futures_plus_market_breadth_acquisition_v1.coexistence import coexistence_preflight
from research.futures_x_stock_state_day2_confirmation_v1 import TRADING_DATE as DAY2_DATE
from research.futures_x_stock_state_day2_confirmation_v1.analyze import run_day2_confirmation
from research.futures_x_stock_state_interaction_day1_v1.engine import build_interaction_table
from research.market_breadth_leadership_acquisition_v1 import RANKING_TYPES
from research.market_breadth_leadership_acquisition_v1.client import mutation_counts
from research.market_breadth_leadership_acquisition_v1.safety import scan_package_source
from research.new_causal_information_acquisition_v1.launcher import live_order_counts
from research.run_20260914_day2_futures_plus_first_live_breadth_v1 import (
    ANALYSIS_ID,
    BREADTH_LIVE_COMMAND_EXPLICIT,
    DAY1_TRADING_DATE,
    EOD_COMMAND,
    ENTRY,
    EXIT,
    FUTURES_LIVE_COMMAND_EXPLICIT,
    FUTURES_PREPARE_COMMAND,
    KIND,
    NEXT_AWAITING,
    NEXT_MECHANISM,
    NEXT_NO_INFO,
    NEXT_TRANSPORT,
    PAPER_CHANGED,
    PARENT_ID,
    RUNTIME_CHANGED,
    TRADING_DATE,
    VERDICT_A,
    VERDICT_AWAITING,
    VERDICT_B,
    VERDICT_C,
    VERDICT_TRANSPORT_FAIL,
)
from research.run_20260914_day2_futures_plus_first_live_breadth_v1.isolation import NATIVE
from research.run_20260914_day2_futures_plus_first_live_breadth_v1.recon import run_recon
from research.run_20260914_day2_futures_plus_first_live_breadth_v1.transport import evaluate_transport
from research.market_breadth_leadership_acquisition_v1.writer import refuse_historical_pseudosync

JST = ZoneInfo("Asia/Tokyo")


def refuse_day1_mining(day: str) -> None:
    d = str(day or "").replace("-", "")
    if d == DAY1_TRADING_DATE:
        raise ValueError("20260911 existing raw mining is CLOSED")


def _live_refused(*, today: str, now: datetime) -> dict[str, Any]:
    blocked = []
    if today != TRADING_DATE:
        blocked.append(f"today={today} target={TRADING_DATE}")
    try:
        refuse_historical_pseudosync(TRADING_DATE, now=now)
    except ValueError as exc:
        blocked.append(str(exc))
    return {
        "would_start_live": False,
        "blocked": blocked,
        "reason": "this package does not start live capture; operators run existing scripts on 20260914",
    }


def _strongest_observation(*, q1: dict[str, Any], q2: dict[str, Any], day2: dict[str, Any]) -> str:
    if q1.get("incremental"):
        return str(q1.get("reason") or "breadth incremental market-state")
    if q2.get("changed"):
        return str(q2.get("reason") or "breadth changes activity selection")
    d2 = dict(day2.get("decision") or {})
    if d2.get("PASS"):
        return "Day2 primary replicated; breadth added no incremental state on this first live day"
    if d2.get("VERDICT") and "FAIL" in str(d2.get("VERDICT")):
        return "Day2 primary did not replicate; breadth added no incremental state"
    return "live tapes not yet available for causal recon"


def run_eod(
    *,
    native_root: Optional[Path] = None,
    trading_date: str = TRADING_DATE,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    root = Path(native_root) if native_root else NATIVE
    day = str(trading_date or TRADING_DATE).replace("-", "")
    refuse_day1_mining(day)
    clock = now or datetime.now(JST)
    today = clock.astimezone(JST).strftime("%Y%m%d")
    orders = live_order_counts()
    mut = mutation_counts()
    scan = scan_package_source()
    coex = coexistence_preflight(native_root=root, trading_date=day)
    live_gate = _live_refused(today=today, now=clock)
    day2 = run_day2_confirmation(native_root=root, trading_date=day)
    transport = evaluate_transport(native_root=root, trading_date=day)
    recon: dict[str, Any] = {
        "ran": False,
        "reason": "transport not FULL" if not transport.get("FULL") else "futures interaction table unavailable",
    }
    if transport.get("FULL") and bool((day2.get("completeness") or {}).get("FULL")):
        try:
            table = build_interaction_table(native_root=root, day=day)
            recon = run_recon(clocks=list(table.get("clocks") or []), day=day, native_root=root)
            recon["ran"] = True
        except Exception as exc:
            recon = {"ran": False, "reason": f"{type(exc).__name__}:{exc}"}
    q1 = dict(recon.get("q1") or {})
    q2 = dict(recon.get("q2") or {})
    d2 = dict(day2.get("decision") or {})
    a2 = dict(day2.get("answers") or {})
    session_over = clock.astimezone(JST).time() >= dtime(11, 30)
    day2_full = bool((day2.get("completeness") or {}).get("FULL"))
    transport_full = bool(transport.get("FULL"))
    if today < TRADING_DATE:
        awaiting = True
    elif today == TRADING_DATE and (not session_over) and (not transport_full) and (not day2_full):
        awaiting = True
    else:
        awaiting = False
    if awaiting:
        verdict = VERDICT_AWAITING
        nxt = NEXT_AWAITING
    elif not transport_full:
        verdict = VERDICT_TRANSPORT_FAIL
        nxt = NEXT_TRANSPORT
    elif bool(q1.get("incremental")):
        verdict = VERDICT_A
        nxt = NEXT_MECHANISM
    elif bool(q2.get("changed")):
        verdict = VERDICT_B
        nxt = NEXT_MECHANISM
    else:
        verdict = VERDICT_C
        nxt = NEXT_NO_INFO
    strongest = _strongest_observation(q1=q1, q2=q2, day2=day2)
    answers = {
        "1_FULL": a2.get("1_FULL"),
        "2_stock_N": a2.get("2_stock_N"),
        "3_NK_present": a2.get("3_NK_present"),
        "4_TOPIX_present": a2.get("4_TOPIX_present"),
        "5_Day2_BOTH_DOWN_clock_N": a2.get("5_Day2_BOTH_DOWN_clock_N"),
        "6_TOP_MID": a2.get("6_TOP_MID"),
        "7_BOTTOM_MID": a2.get("7_BOTTOM_MID"),
        "8_TOP_BOTTOM": a2.get("8_TOP_BOTTOM"),
        "9_BASE_A": a2.get("9_BASE_A"),
        "10_incremental_lift": a2.get("10_incremental_lift"),
        "11_TOP_LONG_mean": a2.get("11_TOP_LONG_mean"),
        "12_TOP_LONG_median": a2.get("12_TOP_LONG_median"),
        "13_primary_PASS_FAIL": a2.get("13_primary_PASS_FAIL"),
        "14_secondary_rescue_used": False,
        "15_transport_FULL": transport_full,
        "16_seven_types_present": transport.get("seven_types_present"),
        "17_snapshot_N_by_type": transport.get("snapshot_n_by_type"),
        "18_empty_rate": transport.get("empty_rate"),
        "19_429_N": transport.get("http_429_n"),
        "20_HTTP_error_N": transport.get("http_error_n"),
        "21_schema_drift_N": transport.get("schema_drift_n"),
        "22_received_at_continuity": {
            "advancing": transport.get("received_at_advancing"),
            "first": transport.get("first_received_at"),
            "last": transport.get("last_received_at"),
            "coverage_0905_1125": transport.get("coverage_0905_1125"),
        },
        "23_Q1_incremental_breadth_evidence": bool(q1.get("incremental")) if recon.get("ran") else None,
        "24_Q2_activity_effect_change": bool(q2.get("changed")) if recon.get("ran") else None,
        "25_strongest_explainable_observation": strongest,
        "26_ENTRY_built": False,
        "27_EXIT_built": False,
        "28_Runtime_changed": False,
        "29_Paper_changed": False,
        "30_submit_cancel_live": f"{orders.get('submit', 0)}/{orders.get('cancel', 0)}/{orders.get('live', 0)}",
        "31_VERDICT": verdict,
        "32_NEXT": nxt,
    }
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "PARENT_ID": PARENT_ID,
        "KIND": KIND,
        "clock": clock.isoformat(timespec="seconds"),
        "trading_date": day,
        "today_jst": today,
        "day1_mining_closed": True,
        "day1_close_verdict": DAY1_CLOSE_VERDICT,
        "FEATURE_MINING_CLOSED": FEATURE_MINING_CLOSED,
        "live_start": live_gate,
        "operator_monday": {
            "prepare": FUTURES_PREPARE_COMMAND,
            "futures_live": FUTURES_LIVE_COMMAND_EXPLICIT,
            "breadth_live": BREADTH_LIVE_COMMAND_EXPLICIT,
            "eod": EOD_COMMAND,
            "futures_window": "07:55-11:30 JST",
            "breadth_window": "09:05-11:25 JST",
            "types": list(RANKING_TYPES),
            "cadence_sec": 60,
            "fail_soft_sec": 120,
            "universe": "Core10+Dynamic38+NK225mini+TOPIX=50",
        },
        "coexistence": coex,
        "day2": {
            "ANALYSIS_ID": day2.get("ANALYSIS_ID"),
            "status": day2.get("status"),
            "answers": a2,
            "decision": d2,
            "primary_gates": day2.get("primary_gates"),
            "completeness": day2.get("completeness"),
        },
        "transport": transport,
        "recon": recon,
        "answers": answers,
        "decision": {
            "CASE": verdict,
            "VERDICT": verdict,
            "NEXT": nxt,
            "ENTRY": ENTRY,
            "EXIT": EXIT,
            "RUNTIME_CHANGED": RUNTIME_CHANGED,
            "PAPER_CHANGED": PAPER_CHANGED,
            "secondary_rescue_used": False,
            "substitutions_allowed": False,
            "day1_mining_reopened": False,
            "TRUE_OOS": False,
            "CERTIFIED": False,
        },
        "mutations": mut,
        "source_scan": {k: scan.get(k) for k in ("ok", "register_mutation_n", "unregister_n", "sendorder_n")},
        "orders": orders,
        "submit_cancel_live": f"{orders.get('submit', 0)}/{orders.get('cancel', 0)}/{orders.get('live', 0)}",
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "DAY2_DATE": DAY2_DATE,
        "_day2_full": day2,
    }
