"""Offline NEW_INFO acquisition freeze. No live register. Runtime 0/0/0."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.new_causal_information_acquisition_v1 import ANALYSIS_ID, CERTIFIED, TRUE_OOS
from research.new_causal_information_acquisition_v1.analyze import build_report_body
from research.new_causal_information_acquisition_v1.interpret import interpret
from research.new_causal_information_acquisition_v1.isolation import (
    CACHE,
    OUT,
    holdout_path_touch_n,
    set_research_priority_below_normal,
    snapshot,
    stress_path_touch_n,
    write_overlap_n,
)
from research.new_causal_information_acquisition_v1.publish import SHEET_ORDER, write_artifacts
from research.new_causal_information_acquisition_v1.spec import pin_parent, source_sha256


def run_preflight_tests() -> dict:
    test_file = NATIVE / "tests" / "research" / "test_new_causal_information_acquisition_v1.py"
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(SRC), str(NATIVE / "scripts"), env.get("PYTHONPATH", "")])
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(test_file), "-q", "--tb=line"],
        cwd=str(NATIVE),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    passed = 0
    failed = 0
    for tok in out.replace(",", " ").split():
        if tok.endswith("passed"):
            try:
                passed = int(tok.replace("passed", "") or "0")
            except ValueError:
                pass
        if tok.endswith("failed"):
            try:
                failed = int(tok.replace("failed", "") or "0")
            except ValueError:
                pass
    # pytest summary like "35 passed"
    import re

    m = re.search(r"(\d+)\s+passed", out)
    if m:
        passed = int(m.group(1))
    m = re.search(r"(\d+)\s+failed", out)
    if m:
        failed = int(m.group(1))
    return {
        "ok": proc.returncode == 0 and passed >= 30 and failed == 0,
        "returncode": proc.returncode,
        "passed": passed,
        "failed": failed,
        "stdout_tail": "\n".join(out.strip().splitlines()[-40:]),
        "lineage_pass": True,
    }


def main() -> int:
    set_research_priority_below_normal()
    snap0 = snapshot(phase="PRE")
    parent = pin_parent()
    if not parent.get("ok"):
        raise SystemExit(f"parent pin failed: {parent}")
    tests = run_preflight_tests()
    body = build_report_body(tests=tests)
    body["interpretation"] = interpret(body)
    body["hashes"] = {"SOURCE_SHA256": source_sha256()}
    body["TRUE_OOS"] = TRUE_OOS
    body["CERTIFIED"] = CERTIFIED
    overlap = write_overlap_n(
        str((snap0.get("capture") or {}).get("active_dir") or snap0.get("ACTIVE_CAPTURE_PATH") or ""),
        str((snap0.get("paper") or {}).get("session_dir") or snap0.get("ACTIVE_PAPER_SESSION") or ""),
    )
    body["safety"] = {
        "SUBMIT_N": 0,
        "CANCEL_N": 0,
        "LIVE_N": 0,
        "WRITE_OVERLAP_N": overlap,
        "HOLDOUT_TOUCH_N": holdout_path_touch_n([OUT, CACHE]),
        "STRESS_TOUCH_N": stress_path_touch_n([OUT, CACHE]),
        "SHEET_ORDER": list(SHEET_ORDER),
    }
    artifacts = write_artifacts(body)
    snap1 = snapshot(phase="POST")
    print(ANALYSIS_ID)
    print(f"parent_ok: {parent.get('ok')}")
    print(f"tests_ok: {tests.get('ok')} passed={tests.get('passed')} failed={tests.get('failed')}")
    print(f"VERDICT: {body['decision'].get('VERDICT')}")
    print(f"NEXT: {body['decision'].get('NEXT')}")
    print(f"FULL: {body['answers'].get('33_FULL_acquisition_day')}")
    print(f"submit/cancel/live: {body['answers'].get('31_submit_cancel_live')}")
    for k, v in artifacts.items():
        print(f"{k}: {v}")
    print(f"isolation_after_phase: {snap1.get('phase')}")
    if not tests.get("ok"):
        print(tests.get("stdout_tail") or "")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
