#!/usr/bin/env python3
"""Phase 148c: Recover and validate 20260525 AM session outputs."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parents[2]
NATIVE = ROOT / "kabu_native"
REPORTS = NATIVE / "results" / "reports"
SESSION_REL = "kabu_native/results/small_paper/20260525/live_session_075733"
CONFIG_REL = "kabu_native/configs/small_paper_pilot_q070_cap3_mfe_fav_vol_liq.yaml"
POLICY = "combined_structural_exit_v1"


def _bootstrap() -> None:
    for p in (NATIVE / "src", ROOT):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def main() -> int:
    _bootstrap()

    report: dict[str, Any] = {
        "phase": 148,
        "phase_label": "148c",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "day_stamp": "20260525",
        "session_dir": SESSION_REL,
        "structural_exit_policy": POLICY,
    }

    # 1) Try standalone review command
    review_proc = subprocess.run(
        [
            sys.executable,
            str(NATIVE / "scripts" / "review_structural_observer.py"),
            "--session-dir",
            str(ROOT / SESSION_REL),
            "--config",
            str(ROOT / CONFIG_REL),
            "--structural-exit-policy",
            POLICY,
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    report["review_structural_observer"] = {
        "command": (
            "python kabu_native/scripts/review_structural_observer.py "
            f"--session-dir {SESSION_REL} --structural-exit-policy {POLICY}"
        ),
        "exit_code": review_proc.returncode,
        "stderr_tail": (review_proc.stderr or "")[-800:],
    }

    # 2) Recovery script (idempotent; rebuilds only if missing unless forced)
    recover_proc = subprocess.run(
        [
            sys.executable,
            str(NATIVE / "scripts" / "recover_small_paper_session_outputs.py"),
            "--session-dir",
            SESSION_REL,
            "--structural-exit-policy",
            POLICY,
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    report["recover_script_exit_code"] = recover_proc.returncode
    try:
        recover_payload = json.loads(recover_proc.stdout.strip())
    except json.JSONDecodeError:
        recover_payload = {"raw_stdout": recover_proc.stdout[-2000:]}
    report["recovery"] = recover_payload.get("recovery", recover_payload)
    report["validation"] = recover_payload.get("validation", {})
    report["verdict"] = recover_payload.get("verdict", "recovery_failed")
    report["verdict_options"] = {
        "A": "am_session_outputs_recovered",
        "B": "partial_recovery",
        "C": "recovery_failed",
    }

    if report["verdict"] == "am_session_outputs_recovered":
        report["verdict_notes"] = [
            "All required artifacts present; counts match 20260525 AM reference.",
        ]
    elif report["verdict"] == "partial_recovery":
        report["verdict_notes"] = [
            "Artifacts present but validation mismatch (see validation.checks).",
        ]
    else:
        report["verdict_notes"] = ["Recovery or validation failed."]

    REPORTS.mkdir(parents=True, exist_ok=True)
    out = REPORTS / "phase148c_session_recovery_20260525.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"verdict": report["verdict"], "path": str(out.relative_to(ROOT))}, ensure_ascii=True))
    return 0 if report["verdict"] == "am_session_outputs_recovered" else 1


if __name__ == "__main__":
    raise SystemExit(main())
