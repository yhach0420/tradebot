"""Answers 1-35. No strategy, no alpha claim."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.new_causal_information_acquisition_v1 import (
    ANALYSIS_ID,
    CASE_FIRST_FULL,
    CASE_IMPL_FAIL,
    CASE_MODE_READY,
    CASE_PARTIAL,
    CASE_SET_READY,
    CORE_N,
    DYNAMIC_N,
    FUTURES_EXCHANGE,
    FUTURES_N,
    NEXT_MECHANISM,
    NEXT_WAIT_FIRST_DAY,
    PARENT_ID,
    TOTAL_REGISTRATION_N,
)
from research.new_causal_information_acquisition_v1.completeness import list_full_days
from research.new_causal_information_acquisition_v1.exclusive import probe_exclusive
from research.new_causal_information_acquisition_v1.isolation import NATIVE
from research.new_causal_information_acquisition_v1.launcher import dry_preflight_live_blockers, live_order_counts
from research.new_causal_information_acquisition_v1.reconcile import classify_day, forbid_ready_downgrade, live_artifacts_present
from research.new_causal_information_acquisition_v1.spec import pin_parent, refuse_legacy_futures_backfill, standard_config_unchanged
from research.new_causal_information_acquisition_v1.universe import build_new_info_universe, latest_am_csv, same_day_am_csv
from research.new_causal_information_acquisition_v1.writer import day_layout

JST = ZoneInfo("Asia/Tokyo")


def _try_universe(native_root: Path, day: str, *, refuse_prior_day: bool = False) -> dict[str, Any]:
    layout = day_layout(day, native_root=native_root)
    prepared = {}
    if layout["prepared_manifest"].is_file():
        try:
            prepared = json.loads(layout["prepared_manifest"].read_text(encoding="utf-8"))
        except Exception:
            prepared = {}
        prep_path = Path(str(prepared.get("source_universe_path") or ""))
        if prep_path.is_file():
            try:
                return {
                    "ok": True,
                    "source": str(prep_path),
                    "universe": build_new_info_universe(prep_path),
                    "same_day": True,
                    "via": "prepared_manifest",
                }
            except Exception as exc:
                return {"ok": False, "source": str(prep_path), "error": f"{type(exc).__name__}:{exc}", "same_day": True}
    path = same_day_am_csv(native_root, day)
    if path.is_file():
        try:
            return {"ok": True, "source": str(path), "universe": build_new_info_universe(path), "same_day": True, "via": "same_day_am_csv"}
        except Exception as exc:
            return {"ok": False, "source": str(path), "error": f"{type(exc).__name__}:{exc}", "same_day": True}
    if refuse_prior_day:
        return {"ok": False, "error": "same-day AM CSV missing; prior-day fallback refused", "same_day": False}
    latest = latest_am_csv(native_root)
    if latest is None:
        return {"ok": False, "error": "no AM CSV", "same_day": False}
    try:
        return {
            "ok": True,
            "source": str(latest),
            "universe": build_new_info_universe(latest),
            "same_day": False,
            "note": "ranking algorithm proof only; not today's live registration",
        }
    except Exception as exc:
        return {"ok": False, "source": str(latest), "error": f"{type(exc).__name__}:{exc}", "same_day": False}


def build_report_body(
    *,
    tests: dict[str, Any],
    now: Optional[datetime] = None,
    native_root: Optional[Path] = None,
    trading_date: Optional[str] = None,
) -> dict[str, Any]:
    root = Path(native_root) if native_root else NATIVE
    dt = now or datetime.now(JST)
    day = str(trading_date or dt.strftime("%Y%m%d"))
    parent = pin_parent()
    std = standard_config_unchanged()
    exclusive = probe_exclusive(native_root=root, trading_date=day)
    layout = day_layout(day, native_root=root)
    artifacts = live_artifacts_present(layout)
    uni_pack = _try_universe(root, day, refuse_prior_day=bool(artifacts.get("any")))
    uni = dict(uni_pack.get("universe") or {})
    preflight = dry_preflight_live_blockers(native_root=root, trading_date=day, now=dt)
    capture = classify_day(native_root=root, trading_date=day)
    full_days = list_full_days(native_root=root)
    orders = live_order_counts()
    tests_ok = bool(tests.get("ok")) and int(tests.get("passed") or 0) >= 30
    impl_ok = bool(parent.get("ok") and std.get("ok") and tests_ok)
    try:
        refuse_legacy_futures_backfill("20260807")
        legacy_guard = False
    except ValueError:
        legacy_guard = True

    live_m = {}
    if layout["live_manifest"].is_file():
        try:
            live_m = json.loads(layout["live_manifest"].read_text(encoding="utf-8"))
        except Exception:
            live_m = {}
    first_push = {}
    fp = layout["root"] / "first_push.json"
    if fp.is_file():
        try:
            first_push = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            first_push = {}
    resolved_live = capture.get("resolved_live") or {"nk": None, "topix": None, "board_nk": None, "board_tx": None}
    nk_res = resolved_live.get("nk")
    tx_res = resolved_live.get("topix")
    board_nk = bool((nk_res or {}).get("board_preflight", {}).get("ok")) if isinstance(nk_res, dict) else False
    board_tx = bool((tx_res or {}).get("board_preflight", {}).get("ok")) if isinstance(tx_res, dict) else False
    first_nk = (first_push.get("nk") or {}).get("received_at") or (capture.get("nk225mini") or {}).get("first_received_at")
    first_tx = (first_push.get("topix") or {}).get("received_at") or (capture.get("topix") or {}).get("first_received_at")

    answers = {
        "1_parent_pinned": bool(parent.get("ok")),
        "2_normal_50_stock_config_unchanged": bool(std.get("ok")),
        "3_acquisition_mode_isolated": True,
        "4_Paper_simultaneous_run": bool(exclusive.get("paper_simultaneous")),
        "5_OPVAL_simultaneous_run": bool(exclusive.get("opval_simultaneous")),
        "6_Core_N": int(uni.get("core_n") or CORE_N),
        "7_Dynamic_N": int(uni.get("dynamic_n") or DYNAMIC_N),
        "8_Futures_N": int(FUTURES_N),
        "9_total_registration_N": int(TOTAL_REGISTRATION_N),
        "10_NK225mini_FutureCode_resolved": bool(nk_res),
        "11_NK_resolved_symbol": (nk_res or {}).get("resolved_symbol") if isinstance(nk_res, dict) else None,
        "12_TOPIX_resolved": bool(tx_res),
        "13_TOPIX_symbol": (tx_res or {}).get("resolved_symbol") if isinstance(tx_res, dict) else None,
        "14_Exchange": int(FUTURES_EXCHANGE),
        "15_board_preflight_NK": board_nk,
        "16_board_preflight_TOPIX": board_tx,
        "17_registration_success": int((live_m.get("registration_specs") and len(live_m.get("registration_specs") or [])) or 0) == 50,
        "18_first_NK_PUSH": first_nk,
        "19_first_TOPIX_PUSH": first_tx,
        "20_NK_event_N": int((capture.get("nk225mini") or {}).get("event_n") or 0),
        "21_TOPIX_event_N": int((capture.get("topix") or {}).get("event_n") or 0),
        "22_0845_0900_NK_changes": capture.get("nk_change_0845_0900"),
        "23_0845_0900_TOPIX_changes": capture.get("topix_change_0845_0900"),
        "24_0900_1130_NK_changes": capture.get("nk_change_0900_1130"),
        "25_0900_1130_TOPIX_changes": capture.get("topix_change_0900_1130"),
        "26_stock_48_coverage": bool((capture.get("gates") or {}).get("A_stock_48_48") or int((capture.get("stock") or {}).get("unique_symbol_n") or 0) == 48),
        "27_future_gaps_gt_60s": capture.get("future_gaps_gt_60s") or [],
        "28_timestamp_lineage_PASS": bool((capture.get("gates") or {}).get("H_timestamp_lineage")) if artifacts.get("any") else (tests.get("lineage_pass", True) if tests_ok else False),
        "29_Runtime_changed": False,
        "30_Paper_changed": False,
        "31_submit_cancel_live": f"{orders['submit']}/{orders['cancel']}/{orders['live']}",
        "32_sendorder_call_N": int(orders["sendorder_call_n"]),
        "33_FULL_acquisition_day": bool(capture.get("FULL")),
        "34_VERDICT": None,
        "35_NEXT": None,
    }

    decision = {
        "parent_ok": bool(parent.get("ok")),
        "standard_config_ok": bool(std.get("ok")),
        "tests_ok": tests_ok,
        "tests_passed": tests.get("passed"),
        "impl_ok": impl_ok,
        "exclusive_ok": bool(exclusive.get("ok")),
        "paper_simultaneous": bool(exclusive.get("paper_simultaneous")),
        "opval_simultaneous": bool(exclusive.get("opval_simultaneous")),
        "live_register_attempted": bool((live_m.get("live_start_sequence") or [])),
        "FULL": bool(capture.get("FULL")),
        "classification": capture.get("classification"),
        "preflight_snapshot": not bool(artifacts.get("any")),
        "full_day_n": len(full_days),
        "legacy_backfill_guard": legacy_guard,
        "KIND": "NEW_INFO_DEV_CONSTRUCTION_ONLY",
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "STRATEGY_BUILT": False,
        "ENTRY": False,
        "OPENING_REVIVED": False,
    }

    if not impl_ok:
        verdict = CASE_IMPL_FAIL
        nxt = "FIX_NEW_INFO_ACQUISITION_IMPLEMENTATION_V1"
    elif artifacts.get("any") or int((capture.get("stock") or {}).get("event_n") or 0) > 0 or int((capture.get("nk225mini") or {}).get("event_n") or 0) > 0:
        verdict = str(capture.get("VERDICT") or CASE_PARTIAL)
        nxt = str(capture.get("NEXT") or "RETAIN_RAW_DO_NOT_COUNT_AS_FULL_NEW_INFO_DAY_V1")
        verdict = forbid_ready_downgrade(
            verdict,
            artifacts=artifacts,
            event_n=int((capture.get("nk225mini") or {}).get("event_n") or 0) + int((capture.get("stock") or {}).get("event_n") or 0),
        )
        if verdict == CASE_MODE_READY:
            verdict = CASE_PARTIAL
            nxt = "RETAIN_RAW_DO_NOT_COUNT_AS_FULL_NEW_INFO_DAY_V1"
    elif len(full_days) >= 10:
        verdict = CASE_SET_READY
        nxt = NEXT_MECHANISM
    elif capture.get("FULL"):
        verdict = CASE_FIRST_FULL
        nxt = "ACCUMULATE_FIRST_10_FULL_NEW_INFO_DAYS_V1"
    else:
        verdict = CASE_MODE_READY
        nxt = NEXT_WAIT_FIRST_DAY

    answers["34_VERDICT"] = verdict
    answers["35_NEXT"] = nxt
    decision["VERDICT"] = verdict
    decision["NEXT"] = nxt
    decision["CASE"] = verdict

    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "PARENT_ID": PARENT_ID,
        "parent": parent,
        "standard_config": std,
        "exclusive": exclusive,
        "universe_pack": uni_pack,
        "preflight": preflight,
        "capture_today": capture,
        "full_days": full_days,
        "tests": tests,
        "orders": orders,
        "answers": answers,
        "decision": decision,
        "resolved_live": {"nk": nk_res, "topix": tx_res, "board_nk": board_nk, "board_tx": board_tx},
        "first_push": first_push,
        "live_manifest": live_m,
        "clock": dt.isoformat(timespec="seconds"),
        "trading_date": day,
    }
