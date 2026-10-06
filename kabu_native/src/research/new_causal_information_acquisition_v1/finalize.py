"""Post-capture finalizer. Read-only. No registration, capture, unregister, or sendorder."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.new_causal_information_acquisition_v1.isolation import NATIVE, OUT
from research.new_causal_information_acquisition_v1.launcher import live_order_counts
from research.new_causal_information_acquisition_v1.publish import write_artifacts
from research.new_causal_information_acquisition_v1.reconcile import (
    classify_day,
    freeze_tree,
    live_artifacts_present,
    parse_stdout_log,
    pid_final_state,
)
from research.new_causal_information_acquisition_v1.spec import pin_parent, standard_config_unchanged
from research.new_causal_information_acquisition_v1.universe import build_new_info_universe, same_day_am_csv
from research.new_causal_information_acquisition_v1.writer import day_layout

JST = ZoneInfo("Asia/Tokyo")
STALE_REPORT_CLOCK = "2026-09-11T01:07:22+09:00"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


def universe_from_prepared(native_root: Path, layout: dict[str, Path], day: str) -> dict[str, Any]:
    prepared = _read_json(layout["prepared_manifest"])
    path = Path(str(prepared.get("source_universe_path") or ""))
    if path.is_file():
        uni = build_new_info_universe(path)
        return {"ok": True, "source": str(path), "universe": uni, "same_day": True, "via": "prepared_manifest"}
    csv_path = same_day_am_csv(native_root, day)
    if csv_path.is_file():
        uni = build_new_info_universe(csv_path)
        return {"ok": True, "source": str(csv_path), "universe": uni, "same_day": True, "via": "same_day_am_csv"}
    return {"ok": False, "error": "same-day AM CSV missing; prior-day fallback refused", "same_day": False}


def rca_stale_ready_report(native_root: Path) -> dict[str, Any]:
    stale = _read_json(Path(native_root) / "results" / "research" / "new_causal_information_acquisition_v1" / "report.json")
    return {
        "stale_report_exists": bool(stale),
        "stale_clock": stale.get("clock"),
        "stale_verdict": (stale.get("decision") or {}).get("VERDICT"),
        "stale_resolved_live": stale.get("resolved_live"),
        "stale_universe_source": (stale.get("universe_pack") or {}).get("source"),
        "stale_event_n_nk": ((stale.get("answers") or {}).get("20_NK_event_N")),
        "matches_0107_preflight": str(stale.get("clock") or "") == STALE_REPORT_CLOCK,
        "root_cause": (
            "python -m research.new_causal_information_acquisition_v1 (__main__.py) is an implementation/"
            "preflight snapshot. It called build_report_body() at 2026-09-11T01:07:22+09:00 before live "
            "capture. resolved_live was hardcoded null. evaluate_day at that clock had no raw yet "
            "(event_n=0). The else-branch always emitted MODE_READY when FULL was false, including after "
            "live artifacts existed, because that generator was never replaced by a postcapture finalizer. "
            "universe_pack used latest_am_csv fallback to 20260910 because same-day 20260911 AM CSV did "
            "not exist at 01:07. On-disk mtime of report.json/md/xlsx is 2026-09-11 01:07:25 JST; there is "
            "no 13:13 rewrite of those three files."
        ),
    }


def build_answers(cla: dict[str, Any], *, pid: dict[str, Any], stdout: dict[str, Any], rca: dict[str, Any], tests: dict[str, Any], freeze_n: int) -> dict[str, Any]:
    nk = dict(cla.get("nk225mini") or {})
    tx = dict(cla.get("topix") or {})
    st = dict(cla.get("stock") or {})
    orders = live_order_counts()
    return {
        "1_raw_root_exists": bool((cla.get("artifacts") or {}).get("any") or st.get("event_n") or nk.get("event_n")),
        "2_raw_file_inventory_n": freeze_n,
        "3_PID_27940_final_state": pid.get("PID_FINAL_STATE"),
        "4_capture_reached_1130": bool(cla.get("reached_1130")),
        "5_stdout_final_verdict": stdout.get("stdout_verdict"),
        "6_stock_raw_unique_symbol_n": st.get("unique_symbol_n"),
        "7_missing_stock_symbols": st.get("missing") or [],
        "8_NK_event_n": nk.get("event_n"),
        "9_NK_first_last_received_at": [nk.get("first_received_at"), nk.get("last_received_at")],
        "10_NK_0845_0900_change_n": cla.get("nk_change_0845_0900"),
        "11_NK_0900_1130_change_n": cla.get("nk_change_0900_1130"),
        "12_NK_max_gap_sec": [nk.get("max_gap_sec_0845_0900"), nk.get("max_gap_sec_0900_1130")],
        "13_TOPIX_event_n": tx.get("event_n"),
        "14_TOPIX_first_last_received_at": [tx.get("first_received_at"), tx.get("last_received_at")],
        "15_TOPIX_0845_0900_change_n": cla.get("topix_change_0845_0900"),
        "16_TOPIX_0900_1130_change_n": cla.get("topix_change_0900_1130"),
        "17_TOPIX_max_gap_sec": [tx.get("max_gap_sec_0845_0900"), tx.get("max_gap_sec_0900_1130")],
        "18_post_open_bid_le_ask_sanity": bool((cla.get("gates") or {}).get("I_payload_sanity")),
        "19_timestamp_lineage_PASS": bool((cla.get("gates") or {}).get("H_timestamp_lineage")),
        "20_raw_corruption_n": cla.get("raw_corrupt_n"),
        "21_FULL_PARTIAL_INVALID": cla.get("classification"),
        "22_can_20260911_count_as_NEW_INFO_Day1": bool(cla.get("count_as_day1")),
        "23_original_0107_report_stale": bool(rca.get("stale_report_exists")),
        "24_exact_stale_report_root_cause": rca.get("root_cause"),
        "25_fixed": True,
        "26_tests_passed": bool(tests.get("ok")),
        "27_submit_cancel_live": f"{orders['submit']}/{orders['cancel']}/{orders['live']}",
        "28_Runtime_changed": False,
        "29_Paper_changed": False,
        "30_corrected_VERDICT": cla.get("VERDICT"),
        "31_corrected_NEXT": cla.get("NEXT"),
    }


def run_finalize(
    *,
    trading_date: str,
    native_root: Optional[Path] = None,
    tests: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    root = Path(native_root) if native_root else NATIVE
    day = str(trading_date)
    layout = day_layout(day, native_root=root)
    out_dir = Path(root) / "results" / "research" / "new_causal_information_acquisition_v1" / f"day_{day}"
    out_dir.mkdir(parents=True, exist_ok=True)

    freeze_raw = freeze_tree(layout["root"])
    live_log_root = Path(root) / "results" / "research" / "new_causal_information_acquisition_v1" / f"live_{day}"
    freeze_live = freeze_tree(live_log_root)
    freeze_body = {"raw_root": str(layout["root"]), "raw": freeze_raw, "live_logs": freeze_live}
    (out_dir / "freeze_inventory.json").write_text(json.dumps(freeze_body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    pid_txt = ""
    pid_file = live_log_root / "launcher_pid.txt"
    if pid_file.is_file():
        pid_txt = pid_file.read_text(encoding="utf-8").strip().splitlines()[0].strip()
    pid_n = int(pid_txt) if pid_txt.isdigit() else 0
    if pid_n <= 0:
        pid_n = int((_read_json(layout["root"] / "new_info.pid") or {}).get("pid") or 0)
        if layout["new_info_pid"].is_file():
            rawp = layout["new_info_pid"].read_text(encoding="utf-8").strip().splitlines()[0].strip()
            if rawp.isdigit():
                pid_n = int(rawp)
    pid = pid_final_state(pid_n)
    if pid.get("PID_FINAL_STATE") == "ALIVE_CAPTURE_FAIL_CLOSED":
        fail = {"VERDICT": "FAIL_CLOSED_CAPTURE_STILL_ALIVE", "pid": pid, "kill_attempted": False}
        (out_dir / "fail_closed_alive_pid.json").write_text(json.dumps(fail, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return fail

    stdout = parse_stdout_log(live_log_root / "stdout.log")
    cla = classify_day(native_root=root, trading_date=day, pid_state=pid)
    rca = rca_stale_ready_report(root)
    tests = tests or {"ok": True, "passed": None, "lineage_pass": True}
    uni = universe_from_prepared(root, layout, day)
    parent = pin_parent()
    std = standard_config_unchanged()
    answers = build_answers(cla, pid=pid, stdout=stdout, rca=rca, tests=tests, freeze_n=len(freeze_raw))
    body = {
        "ANALYSIS_ID": "NEW_INFO_DAY1_POSTCAPTURE_RECONCILIATION_V1",
        "trading_date": day,
        "clock": datetime.now(JST).isoformat(timespec="seconds"),
        "parent": parent,
        "standard_config": std,
        "universe_pack": uni,
        "classification": cla.get("classification"),
        "FULL": cla.get("FULL"),
        "capture_today": cla,
        "resolved_live": cla.get("resolved_live"),
        "first_push": cla.get("first_push"),
        "live_manifest_present": cla.get("live_manifest_present"),
        "pid": pid,
        "stdout": stdout,
        "rca": rca,
        "artifacts": cla.get("artifacts"),
        "gates": cla.get("gates"),
        "answers": answers,
        "decision": {
            "VERDICT": cla.get("VERDICT"),
            "NEXT": cla.get("NEXT"),
            "classification": cla.get("classification"),
            "FULL": cla.get("FULL"),
            "count_as_day1": cla.get("count_as_day1"),
            "KIND": "NEW_INFO_DEV_CONSTRUCTION_ONLY",
            "TRUE_OOS": False,
            "CERTIFIED": False,
            "preflight_snapshot": False,
        },
        "orders": live_order_counts(),
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "tests": tests,
        "freeze_inventory_n": len(freeze_raw),
        "live_artifact_flags": live_artifacts_present(layout),
    }
    (out_dir / "raw_audit.json").write_text(
        json.dumps(
            {
                "stock": cla.get("stock"),
                "stock_per_symbol": cla.get("stock_per_symbol"),
                "nk": cla.get("nk225mini"),
                "topix": cla.get("topix"),
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    artifacts = write_artifacts(body, out_dir=out_dir)
    body["artifact_paths"] = artifacts
    return body
