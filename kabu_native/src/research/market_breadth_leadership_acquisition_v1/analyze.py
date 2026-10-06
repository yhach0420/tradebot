"""Assemble ranking acquisition probe report. No ENTRY/EXIT. Day2 freeze unchanged."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.futures_context_day1_effect_check_v1 import ANCHOR_HM, FEATURE_NAMES, PLACEBO_SHIFT_SEC
from research.market_breadth_leadership_acquisition_v1 import (
    ANALYSIS_ID,
    DAY2_FROZEN_PRICE_RETURN_TEST_CHANGED,
    KIND,
    PARENT_ID,
    RANKING_TYPES,
)
from research.market_breadth_leadership_acquisition_v1.client import mutation_counts
from research.market_breadth_leadership_acquisition_v1.isolation import NATIVE, OUT, RAW_ROOT
from research.market_breadth_leadership_acquisition_v1.probe import run_probe
from research.market_breadth_leadership_acquisition_v1.safety import scan_package_source
from research.new_causal_information_acquisition_v1.launcher import live_order_counts

JST = ZoneInfo("Asia/Tokyo")
COLLECTOR_PATH = "python kabu_native\\scripts\\run_market_breadth_leadership_capture.py --probe"
LIVE_PATH = "python kabu_native\\scripts\\run_market_breadth_leadership_capture.py --live"


def schema_answer(probe: dict[str, Any], typ: int) -> dict[str, Any]:
    return dict((probe.get("schemas") or {}).get(str(typ)) or {})


def run_acquisition_report(
    *,
    native_root: Optional[Path] = None,
    probe: Optional[dict[str, Any]] = None,
    tests_passed: Optional[bool] = None,
) -> dict[str, Any]:
    root = Path(native_root) if native_root else NATIVE
    body = probe if probe is not None else run_probe(native_root=root)
    d = dict(body.get("decision") or {})
    orders = live_order_counts()
    mut = dict(body.get("mutations") or mutation_counts())
    src = dict(body.get("source_scan") or scan_package_source())
    soft = dict(body.get("apisoftlimit") or {})
    cadence = dict(body.get("cadence") or {})
    std = dict(body.get("standard_paper") or {})
    answers = {
        "1_apisoftlimit_reachable": bool(body.get("soft_ok")),
        "2_soft_limit_response": {
            "http_status": soft.get("http_status"),
            "raw": soft.get("raw"),
            "error": soft.get("error"),
            "note": "official apisoftlimit is order-qty soft limit, not REST QPS",
        },
        "3_ranking_reachable": bool(body.get("ranking_ok")),
        "4_Type1_schema": schema_answer(body, 1),
        "5_Type2_schema": schema_answer(body, 2),
        "6_Type5_schema": schema_answer(body, 5),
        "7_Type6_schema": schema_answer(body, 6),
        "8_Type7_schema": schema_answer(body, 7),
        "9_Type14_schema": schema_answer(body, 14),
        "10_Type15_schema": schema_answer(body, 15),
        "11_registration_mutation_n": int(mut.get("register_mutation_n") or 0) + int(src.get("register_mutation_n") or 0),
        "12_unregister_n": int(mut.get("unregister_n") or 0) + int(src.get("unregister_n") or 0),
        "13_sendorder_n": int(mut.get("sendorder_n") or 0) + int(src.get("sendorder_n") or 0),
        "14_standard_Paper_config_changed": not bool(std.get("ok")),
        "15_proposed_safe_cadence": cadence,
        "16_collector_path": COLLECTOR_PATH,
        "17_raw_root": str(RAW_ROOT / "YYYYMMDD"),
        "18_tests_passed": tests_passed,
        "19_VERDICT": d.get("VERDICT"),
        "20_NEXT": d.get("NEXT"),
    }
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "PARENT_ID": PARENT_ID,
        "KIND": KIND,
        "clock": datetime.now(JST).isoformat(timespec="seconds"),
        "frozen_types": list(RANKING_TYPES),
        "day2_price_return_freeze": {
            "changed": DAY2_FROZEN_PRICE_RETURN_TEST_CHANGED,
            "FEATURE_NAMES": list(FEATURE_NAMES),
            "PLACEBO_SHIFT_SEC": PLACEBO_SHIFT_SEC,
            "ANCHOR_CLOCK_N": len(ANCHOR_HM),
        },
        "submit_cancel_live": f"{orders.get('submit', 0)}/{orders.get('cancel', 0)}/{orders.get('live', 0)}",
        "source_scan": src,
        "probe": body,
        "answers": answers,
        "decision": {
            **d,
            "ENTRY": False,
            "EXIT": False,
            "STRATEGY_BUILT": False,
            "DAY2_FROZEN_PRICE_RETURN_TEST_CHANGED": False,
            "TRUE_OOS": False,
            "CERTIFIED": False,
        },
        "orders": orders,
        "collector_live_command": LIVE_PATH,
        "out_dir": str(OUT),
        "TRUE_OOS": False,
        "CERTIFIED": False,
    }
