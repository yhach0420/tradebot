#!/usr/bin/env python3
"""Phase687W36: stall monitor accuracy fix — artifacts + regression summary."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

NATIVE = Path(__file__).resolve().parents[1]
OUT = NATIVE / "results" / "reports" / "phase687w36_stall_monitor_accuracy_fix"
JST = ZoneInfo("Asia/Tokyo")
sys.path.insert(0, str(NATIVE / "src"))

from small_paper.data_path_stall_monitor import (  # noqa: E402
    DataPathMonitorState,
    DataPathStallMonitor,
    StallMonitorConfig,
    format_stall_discord_message,
)


def _wj(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _wc(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    cols: list[str] = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _wm(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build_state_matrix() -> list[dict]:
    rows = []
    cases = [
        ("STARTING", True, False, True, True, False, "HB未発生でもPUSH/gate増加"),
        ("RUNNING", True, True, True, True, False, "HB正常または増分あり"),
        ("PUSH_ONLY", True, True, True, False, False, "ENTRY外は正常 / ENTRY内は警告候補"),
        ("STALLED", True, True, False, False, True, "HB異常 AND PUSHΔ0 AND gateΔ0"),
        ("PROCESS_DEAD", True, False, False, False, True, "即時通知"),
        ("OFF_HOURS", False, False, False, False, False, "市場外は監視対象外"),
    ]
    for state, market, hb_ok, push_d, gate_d, notify, note in cases:
        rows.append(
            {
                "state": state,
                "in_market_hours": market,
                "heartbeat_ok": hb_ok,
                "push_delta_gt0": push_d,
                "gate_delta_gt0": gate_d,
                "notify_stalled_or_dead": notify,
                "note": note,
            }
        )
    return rows


def false_positive_replay() -> dict:
    m = DataPathStallMonitor(StallMonitorConfig(heartbeat_sec=300.0))
    m.reset(start_mono=0.0)
    timeline = []
    for t, push, gate, hb in (
        (0, 0, 0, 0),
        (30, 2500, 200, 0),
        (60, 5326, 438, 0),
        (120, 12000, 900, 0),
        (300, 40000, 2500, 1),
    ):
        if hb > 0:
            m.note_heartbeat(mono=float(t), heartbeat_count=hb)
        snap = m.evaluate(
            mono=float(t),
            push_messages=push,
            gate_evaluations=gate,
            heartbeat_count=hb,
            in_market_hours=True,
        )
        timeline.append({"t": t, "push": push, "gate": gate, "hb": hb, **snap.to_dict()})
    return {
        "scenario": "20260716 PAPER_DATA_PATH_STALLED false positive",
        "legacy_behavior": "elapsed>=60 and hb_count==0 => notify (even with push=5326 gate=438)",
        "new_behavior": "no notify while PUSH/gate growing; first HB at 300s => RUNNING",
        "timeline": timeline,
        "any_stall_notify": any(x["notify_stalled"] for x in timeline),
    }


def true_stall_fixture() -> dict:
    m = DataPathStallMonitor(StallMonitorConfig(heartbeat_sec=300.0))
    m.reset(start_mono=0.0)
    m.note_heartbeat(mono=300.0, heartbeat_count=1)
    m.evaluate(
        mono=300.0,
        push_messages=1000,
        gate_evaluations=100,
        heartbeat_count=1,
        in_market_hours=True,
    )
    snap = m.evaluate(
        mono=1000.0,
        push_messages=1000,
        gate_evaluations=100,
        heartbeat_count=1,
        in_market_hours=True,
        force_window_roll=True,
    )
    snap2 = m.evaluate(
        mono=1060.0,
        push_messages=1000,
        gate_evaluations=100,
        heartbeat_count=1,
        in_market_hours=True,
        force_window_roll=True,
    )
    return {
        "scenario": "true stall: HB age>600s, PUSH delta=0, gate delta=0",
        "first_eval": snap.to_dict(),
        "second_eval_antispam": snap2.to_dict(),
        "expects": {
            "first_notify": True,
            "second_notify": False,
            "state": DataPathMonitorState.STALLED.value,
        },
        "ok": (
            snap.notify_stalled
            and not snap2.notify_stalled
            and snap.state == DataPathMonitorState.STALLED
        ),
    }


def run_pytest() -> dict:
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        str(NATIVE / "tests" / "test_phase687w36_stall_monitor_accuracy.py"),
        "-q",
    ]
    env = {
        **dict(**{k: v for k, v in __import__("os").environ.items()}),
        "PYTHONPATH": str(NATIVE / "src") + ";" + str(NATIVE.parent),
        "PYTHONIOENCODING": "utf-8",
    }
    p = subprocess.run(cmd, cwd=str(NATIVE), capture_output=True, text=True, env=env)
    return {
        "returncode": p.returncode,
        "stdout": p.stdout,
        "stderr": p.stderr,
        "passed": p.returncode == 0,
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    matrix = build_state_matrix()
    _wc(OUT / "monitor_state_matrix.csv", matrix)
    fp = false_positive_replay()
    _wj(OUT / "false_positive_replay.json", fp)
    ts = true_stall_fixture()
    _wj(OUT / "true_stall_fixture.json", ts)
    preview = format_stall_discord_message(
        heartbeat_age_sec=700,
        push_delta=0,
        gate_delta=0,
        process_alive=True,
        capture_status="CAPTURE_WRITING",
    )
    _wm(
        OUT / "notification_preview.md",
        "# Stall notification preview\n\n```\n" + preview + "\n```\n",
    )
    pytest_res = run_pytest()
    _wj(OUT / "regression_test_results.json", pytest_res)

    changed = [
        "src/small_paper/data_path_stall_monitor.py",
        "src/small_paper/pilot_runner.py",
        "tests/test_phase687w36_stall_monitor_accuracy.py",
        "scripts/phase687w36_stall_monitor_accuracy_fix.py",
    ]
    _wj(
        OUT / "code_change_manifest.json",
        {
            "phase": "687W36",
            "mainline_strategy_changed": False,
            "entry_exit_changed": False,
            "reentry_changed": False,
            "stale_logic_changed": False,
            "cap_or_shadow_changed": False,
            "heartbeat_period_changed": False,
            "stall_monitor_changed": True,
            "files": changed,
            "submit": 0,
            "cancel": 0,
        },
    )
    _wj(
        OUT / "order_safety_audit.json",
        {
            "submit": 0,
            "cancel": 0,
            "live_order_path_touched": False,
            "note": "Monitor/notify only; no SendOrder/cancel wiring changes",
        },
    )

    verdict = "STALL_MONITOR_FALSE_POSITIVE_FIXED"
    if not pytest_res["passed"] or not ts["ok"] or fp["any_stall_notify"]:
        verdict = "STALL_MONITOR_STILL_OVER_SENSITIVE"
    if pytest_res["passed"] and not ts["ok"]:
        verdict = "TRUE_STALL_NOT_DETECTED"

    report = {
        "phase": "687W36",
        "verdict": verdict,
        "heartbeat_sec_unchanged": 300.0,
        "stall_conditions": [
            "in_market_hours",
            "startup_grace_elapsed",
            "heartbeat_age_abnormal",
            "push_delta==0",
            "gate_delta==0",
        ],
        "false_positive_replay_notifies": fp["any_stall_notify"],
        "true_stall_detected": ts["ok"],
        "pytest_passed": pytest_res["passed"],
        "generated_at": datetime.now(JST).isoformat(),
    }
    _wj(OUT / "phase687w36_report.json", report)

    decision = f"""# Phase687W36 Decision — Stall Monitor Accuracy Fix

## Verdict: `{verdict}`

### Cause (20260716)
Legacy monitor fired `PAPER_DATA_PATH_STALLED` when `elapsed>=60` and `heartbeat_count==0`,
even with PUSH=5326 / gate=438. Heartbeat period is 300s, so first HB is not due yet.

### Fix
New state machine in `small_paper/data_path_stall_monitor.py`, wired into `pilot_runner.py`.

STALLED notify only when **all** hold:
1. market hours
2. startup grace elapsed (60s)
3. heartbeat age abnormal (no HB yet: age>=300s; after HB: age>=600s)
4. observation-window PUSH delta == 0
5. observation-window gate delta == 0

States: STARTING / RUNNING / PUSH_ONLY / STALLED / PROCESS_DEAD / OFF_HOURS

### Evidence
- FP replay any_stall_notify: **{fp['any_stall_notify']}**
- True stall fixture ok: **{ts['ok']}**
- Pytest passed: **{pytest_res['passed']}**
- Heartbeat period unchanged: **300s**
- submit/cancel: **0/0**

### Notification preview
See `notification_preview.md`. Recovery notify only after PUSH or gate increment resumes.
"""
    _wm(OUT / "phase687w36_decision.md", decision)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if verdict == "STALL_MONITOR_FALSE_POSITIVE_FIXED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
