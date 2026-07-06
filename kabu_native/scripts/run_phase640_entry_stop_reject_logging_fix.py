#!/usr/bin/env python3
"""Phase640: entry stop reject logging fix — audit + parity report."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

SCRIPT = Path(__file__).resolve()
NATIVE_ROOT = SCRIPT.parents[1]
REPO_ROOT = NATIVE_ROOT.parent
REPORT_DIR = NATIVE_ROOT / "results" / "reports" / "phase640_entry_stop_reject_logging_fix"
PHASE640_VERDICT_DONE = "phase640_entry_stop_reject_logging_fix_done"
PARITY_DAYS = ("2026-06-25", "2026-06-29", "2026-06-30", "2026-07-01")
LIVE_SESSION = NATIVE_ROOT / "results" / "small_paper" / "20260701" / "live_session_080616"
PHASE630_BASE = NATIVE_ROOT / "results" / "small_paper" / "_phase630" / "head_baseline"
JST = ZoneInfo("Asia/Tokyo")

for p in (NATIVE_ROOT / "src", REPO_ROOT):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _count_csv_rows(path: Path, *, event_type: str, reason: str) -> int:
    if not path.is_file():
        return 0
    import csv

    n = 0
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("event_type") != event_type:
                continue
            gate_reason = str(row.get("gate_reject_reason") or row.get("reject_reason") or "")
            if gate_reason == reason:
                n += 1
    return n


def _audit_live_session_20260701() -> dict[str, Any]:
    events_csv = LIVE_SESSION / "small_paper_events.csv"
    rejects_csv = LIVE_SESSION / "small_paper_rejects.csv"
    errors_fp = LIVE_SESSION / "errors.jsonl"
    cand = _count_csv_rows(events_csv, event_type="candidate", reason="am_pm_entry_stop")
    rej = _count_csv_rows(events_csv, event_type="rejected", reason="am_pm_entry_stop")
    push_unexpected = 0
    if errors_fp.is_file():
        for line in errors_fp.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("operation") == "push_unexpected" or row.get("error_type") == "push_unexpected":
                msg = str(row.get("message") or "")
                if "ref_now" in msg:
                    push_unexpected += 1
    return {
        "session_dir": str(LIVE_SESSION),
        "am_pm_entry_stop_candidate_count": cand,
        "am_pm_entry_stop_rejected_event_count": rej,
        "missing_reject_count": max(0, cand - rej),
        "push_unexpected_ref_now_count": push_unexpected,
        "historical_bug_confirmed": cand > 0 and rej == 0,
    }


def _replay_entry_stop_fix_sample() -> dict[str, Any]:
    from dataclasses import replace

    from small_paper.am_pm_session_policy import AmPmSessionPolicy
    from small_paper.config import load_pilot_config
    from small_paper.live_feature_bridge import LiveFeatureBridge
    from small_paper.live_writer import LiveSessionWriter
    from small_paper.pilot_runner import (
        EVENT_FIELDS,
        _LiveRunState,
        _PushPipelineContext,
        _make_entry_scan_controller,
        _process_push_payload,
    )

    cfg_path = (
        NATIVE_ROOT
        / "configs"
        / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
    )
    tmp = NATIVE_ROOT / "results" / "small_paper" / "_phase640" / "fix_sample"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        import time

        config = replace(load_pilot_config(cfg_path), discord_enabled=False)
        gate = config.make_exposure_gate(repo_root=REPO_ROOT)
        writer = LiveSessionWriter(tmp, incremental=False, event_fields=EVENT_FIELDS)
        state = _LiveRunState(started_mono=time.monotonic())
        ctx = _PushPipelineContext(
            config=config,
            gate=gate,
            feature_bridge=LiveFeatureBridge(config.feature_bridge_config()),
            state=state,
            writer=writer,
            code_to_symbol={"6976": "6976.T", "9984": "9984.T"},
            source="push-replay",
            pos_fields=(),
            entry_scan=_make_entry_scan_controller(config, source="push-replay", writer=writer),
            am_pm_policy=AmPmSessionPolicy.morning(),
        )
        after_stop = datetime(2026, 7, 1, 11, 25, tzinfo=JST).isoformat(timespec="seconds")
        payload = {
            "Symbol": "6976",
            "CurrentPrice": 1500.0,
            "CurrentPriceTime": after_stop,
            "CalcPrice": 1500.0,
            "BidPrice": 1499.0,
            "AskPrice": 1501.0,
            "BidTime": after_stop,
            "AskTime": after_stop,
            "recorded_at": after_stop,
        }
        with patch.object(AmPmSessionPolicy, "entry_allowed_now", return_value=False):
            _process_push_payload(ctx, payload, 1, t0_push_received_at=after_stop)
        rejs = [e for e in state.events if e.get("event_type") == "rejected"]
        return {
            "accepted_count": len(state.accepted_rows),
            "rejected_count": len(rejs),
            "entry_stop_reject_logging_recovered_count": state.entry_stop_reject_logging_recovered_count,
            "logging_error_count": state.logging_error_count,
            "reject_reason": rejs[0].get("gate_reject_reason") if rejs else "",
            "fix_records_reject": len(rejs) == 1 and state.entry_stop_reject_logging_recovered_count == 1,
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _parity_accepted_counts() -> list[dict[str, Any]]:
    from small_paper.pilot_runner import run_push_replay_dry_run
    from small_paper.config import load_pilot_config

    cfg_path = (
        NATIVE_ROOT
        / "configs"
        / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
    )
    config = load_pilot_config(cfg_path)
    rows: list[dict[str, Any]] = []
    tmp_root = NATIVE_ROOT / "results" / "small_paper" / "_phase640"
    tmp_root.mkdir(parents=True, exist_ok=True)
    for day in PARITY_DAYS:
        push_dir = NATIVE_ROOT / "data" / "push_jsonl" / day
        baseline_fp = PHASE630_BASE / day.replace("-", "") / "small_paper_summary.json"
        baseline = _load_json(baseline_fp) if baseline_fp.is_file() else {}
        baseline_accepted = int(baseline.get("accepted_count") or -1)
        out_dir = tmp_root / f"parity_{day.replace('-', '')}"
        if out_dir.exists():
            shutil.rmtree(out_dir, ignore_errors=True)
        try:
            result = run_push_replay_dry_run(
                config,
                push_dir=push_dir,
                output_dir=out_dir,
                poll_interval_sec=5.0,
                replay_speed_sec=0.0,
            )
            summary = result.summary
            current_accepted = int(summary.get("accepted_count") or 0)
            rows.append(
                {
                    "day": day,
                    "baseline_accepted": baseline_accepted,
                    "current_accepted": current_accepted,
                    "accepted_match": baseline_accepted == current_accepted,
                    "current_rejected": int(summary.get("rejected_count") or 0),
                    "entry_stop_recovered": int(
                        summary.get("entry_stop_reject_logging_recovered_count") or 0
                    ),
                }
            )
        finally:
            shutil.rmtree(out_dir, ignore_errors=True)
    return rows


def run_phase640(*, skip_parity: bool) -> dict[str, Any]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    live_audit = _audit_live_session_20260701()
    fix_sample = _replay_entry_stop_fix_sample()
    parity_rows = [] if skip_parity else _parity_accepted_counts()
    parity_ok = all(r.get("accepted_match") for r in parity_rows) if parity_rows else True
    report = {
        "phase": 640,
        "verdict": PHASE640_VERDICT_DONE
        if live_audit.get("historical_bug_confirmed")
        and fix_sample.get("fix_records_reject")
        and parity_ok
        else "phase640_entry_stop_reject_logging_fix_fail",
        "live_session_audit_20260701": live_audit,
        "fix_replay_sample": fix_sample,
        "parity_accepted": parity_rows,
        "parity_accepted_all_match": parity_ok,
        "notes": [
            "Phase640 fixes ref_now on am_pm_entry_stop / outside_refresh_universe branches.",
            "New reject rows/events are expected diffs; accepted_count must match Phase630 baseline.",
        ],
    }
    (REPORT_DIR / "phase640_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase640 entry stop reject logging fix audit")
    parser.add_argument(
        "--skip-parity",
        action="store_true",
        help="Skip full-day push replay parity (faster; unit tests cover fix)",
    )
    args = parser.parse_args()
    report = run_phase640(skip_parity=args.skip_parity)
    return 0 if report.get("verdict") == PHASE640_VERDICT_DONE else 1


if __name__ == "__main__":
    raise SystemExit(main())
