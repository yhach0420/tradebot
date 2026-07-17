#!/usr/bin/env python3
"""Phase687W38: Multi-day board dataset auto-append — bootstrap + report.

Research-only. Does not change ENTRY/EXIT/CAP/OR/Shadow/orders.
Does not copy or auto-delete raw Capture.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

NATIVE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NATIVE / "src"))
JST = ZoneInfo("Asia/Tokyo")
OUT = NATIVE / "results" / "reports" / "phase687w38_multi_day_board_dataset"

from research.board_entry_dataset_append import (  # noqa: E402
    DISK_OPS_WARN_PCT,
    DISK_WARN_PCT,
    append_session,
    dataset_root,
    detect_session_meta,
    import_existing_dataframe,
    is_eligible,
    load_manifest,
    maybe_append_session_board_dataset,
)


def _wj(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _wm(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def discover_sessions(native: Path) -> list[Path]:
    root = native / "results" / "small_paper"
    if not root.is_dir():
        return []
    out = []
    for day in sorted(root.iterdir()):
        if not day.is_dir() or not day.name.isdigit() or len(day.name) != 8:
            continue
        # skip synthetic test dates in 2099*
        if day.name.startswith("2099"):
            continue
        for sess in sorted(day.glob("live_session_*")):
            if sess.is_dir() and not sess.name.endswith("_abort"):
                out.append(sess)
    return out


def bootstrap_from_w37() -> dict:
    w37 = (
        NATIVE
        / "results"
        / "reports"
        / "phase687w37_live_board_entry_quality"
        / "board_entry_dataset_20260716.parquet"
    )
    if not w37.is_file():
        return {"status": "W37_DATASET_MISSING"}
    df = pd.read_parquet(w37)
    # quality from W37
    qpath = NATIVE / "results" / "reports" / "phase687w37_live_board_entry_quality" / "board_data_quality.json"
    q = json.loads(qpath.read_text(encoding="utf-8")) if qpath.is_file() else {}
    return import_existing_dataframe(
        native_root=NATIVE,
        df=df,
        trading_date="20260716",
        session_kind="am",
        session_id="live_session_073602",
        quality={
            "board_level_missing_rate": q.get("board_level_missing_rate_slim", 0.0),
            "capture_events": q.get("event_count"),
            "dups_skipped": None,
            "source": "phase687w37_parquet",
        },
    )


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    results = []
    boot = bootstrap_from_w37()
    results.append({"action": "bootstrap_w37_20260716_am", **boot})

    # Scan other eligible sessions (idempotent skip if already ingested)
    for sess in discover_sessions(NATIVE):
        meta = detect_session_meta(sess)
        ok, reason = is_eligible(meta)
        if not ok:
            results.append(
                {
                    "action": "scan",
                    "session": str(sess),
                    "status": "SKIPPED_INELIGIBLE",
                    "reason": reason,
                    "session_kind": meta.get("session_kind"),
                    "trading_date": meta.get("trading_date"),
                }
            )
            continue
        # Skip re-build of 20260716 AM if bootstrap already ingested
        if (
            meta["trading_date"] == "20260716"
            and meta["session_kind"] == "am"
            and meta["session_id"] == "live_session_073602"
            and boot.get("status") in ("INGESTED", "SKIPPED_ALREADY_INGESTED")
        ):
            results.append(
                {
                    "action": "scan",
                    "session": str(sess),
                    "status": "SKIPPED_BOOTSTRAPPED",
                }
            )
            continue
        r = append_session(native_root=NATIVE, session_dir=sess)
        results.append({"action": "append", "session": str(sess), **r})

    # Idempotency smoke: second append of same session must skip
    am = NATIVE / "results" / "small_paper" / "20260716" / "live_session_073602"
    if am.is_dir():
        again = maybe_append_session_board_dataset(native_root=NATIVE, session_dir=am)
        results.append({"action": "idempotency_check", **again})

    man = load_manifest(dataset_root(NATIVE))
    root = dataset_root(NATIVE)
    part = root / "trading_date=20260716"
    schema_ok = (root / "feature_schema.json").is_file()
    part_ok = (part / "entries.parquet").is_file() and (part / "data_quality.json").is_file()

    statuses = {r.get("status") for r in results}
    if "DATASET_SCHEMA_MISMATCH" in statuses:
        verdict = "DATASET_SCHEMA_MISMATCH"
    elif "CAPTURE_SYNC_QUALITY_FAILED" in statuses:
        verdict = "CAPTURE_SYNC_QUALITY_FAILED"
    elif "DISK_CAPACITY_BLOCKED" in statuses:
        verdict = "DISK_CAPACITY_BLOCKED"
    elif part_ok and schema_ok and man.get("n_trading_days", 0) >= 1:
        verdict = "MULTI_DAY_BOARD_DATASET_READY"
    else:
        verdict = "CAPTURE_SYNC_QUALITY_FAILED"

    # Daily report snapshot for latest ingested
    latest = None
    for sk, info in sorted((man.get("sessions") or {}).items()):
        latest = info
    daily_report = {
        "valid_entries": latest.get("n_entries") if latest else 0,
        "sync_ok": latest.get("sync_ok") if latest else 0,
        "missing_rate": latest.get("board_level_missing_rate") if latest else None,
        "capture_events": latest.get("capture_events") if latest else None,
        "duplicates": latest.get("dups_skipped") if latest else None,
        "dataset_days": man.get("n_trading_days"),
        "dataset_entries": man.get("n_entries_total"),
        "disk_used_pct": man.get("disk_used_pct"),
        "disk_warning_ge_75": bool(man.get("disk_warning")),
        "ops_warn_ge_85": bool(man.get("ops_warn_before_next_capture")),
        "reanalysis_gate": man.get("reanalysis_gate"),
        "thresholds": {"warn": DISK_WARN_PCT, "ops_warn": DISK_OPS_WARN_PCT},
    }

    report = {
        "phase": "687W38",
        "verdict": verdict,
        "dataset_root": str(root),
        "manifest": {
            "n_trading_days": man.get("n_trading_days"),
            "n_sessions": man.get("n_sessions"),
            "n_entries_total": man.get("n_entries_total"),
            "trading_dates": man.get("trading_dates"),
            "reanalysis_gate": man.get("reanalysis_gate"),
            "disk_used_pct": man.get("disk_used_pct"),
            "ops_warn_before_next_capture": man.get("ops_warn_before_next_capture"),
        },
        "daily_report": daily_report,
        "actions": results,
        "hook": "pilot_runner post-seal maybe_append_session_board_dataset (fail-open)",
        "generated_at": datetime.now(JST).isoformat(),
    }
    _wj(OUT / "phase687w38_report.json", report)
    _wj(
        OUT / "code_change_manifest.json",
        {
            "phase": "687W38",
            "mainline_strategy_changed": False,
            "entry_exit_cap_or_shadow_changed": False,
            "board_rules_adopted": False,
            "orders_changed": False,
            "capture_auto_delete": False,
            "capture_copied": False,
            "files": [
                "src/research/board_entry_features.py",
                "src/research/board_entry_dataset_append.py",
                "src/small_paper/pilot_runner.py",
                "scripts/phase687w38_multi_day_board_dataset_auto_append.py",
                "tests/test_phase687w38_multi_day_board_dataset.py",
            ],
            "submit": 0,
            "cancel": 0,
        },
    )
    _wj(
        OUT / "order_safety_audit.json",
        {"submit": 0, "cancel": 0, "live_order_path_touched": False},
    )
    decision = f"""# Phase687W38 Decision — Multi-Day Board Dataset Auto Append

## Verdict: `{verdict}`

### Dataset
- root: `{root}`
- trading_dates: {man.get('trading_dates')}
- sessions: {man.get('n_sessions')}
- entries: {man.get('n_entries_total')}
- reanalysis_gate: **{man.get('reanalysis_gate')}**
  - 5d interim / 10d stability / 20d adoption review

### Daily report (latest session)
- valid ENTRY: {daily_report['valid_entries']}
- sync OK: {daily_report['sync_ok']}
- missing rate: {daily_report['missing_rate']}
- Capture events: {daily_report['capture_events']}
- duplicates skipped: {daily_report['duplicates']}
- cumulative days/entries: {daily_report['dataset_days']} / {daily_report['dataset_entries']}
- disk used: {daily_report['disk_used_pct']}% (warn≥{DISK_WARN_PCT}, ops≥{DISK_OPS_WARN_PCT})

### Guarantees
- VALID_SESSION + SEALED_VALID only
- AM/PM session keys separate; no overwrite of prior session rows
- W37 backward sync + duplicate skip
- raw Capture never copied / never auto-deleted
- post-seal hook fail-open in pilot_runner
- submit/cancel = 0
"""
    _wm(OUT / "phase687w38_decision.md", decision)
    # convenience copies of paths
    _wj(OUT / "dataset_paths.json", {
        "dataset_root": str(root),
        "manifest": str(root / "board_entry_dataset_manifest.json"),
        "summary_csv": str(root / "board_entry_dataset_summary.csv"),
        "schema": str(root / "feature_schema.json"),
    })
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if verdict == "MULTI_DAY_BOARD_DATASET_READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
