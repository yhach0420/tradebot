"""CLI: E1_X11 Policy Gate V2 A/B + publish; stop."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from research.e1_x11_policy_gate_v2.publish import publish
from research.e1_x11_policy_gate_v2.run_audit import PUBLISH, run_once


def _run_tests() -> dict:
    native = Path(__file__).resolve().parents[3]
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{native / 'src'};{native / 'research'}"
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rA", "--tb=line", "-p", "no:cacheprovider",
         str(native / "tests" / "test_e1_x11_policy_gate_v2.py")],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(native), env=env, timeout=180,
    )
    rows = []
    for line in (proc.stdout or "").splitlines():
        for st in ("PASSED", "FAILED", "ERROR"):
            if line.strip().startswith(st + " "):
                rows.append({"test": line.strip().split(" ", 1)[1].split(" - ")[0], "outcome": st})
                break
    return {
        "exit_code": proc.returncode,
        "passed": sum(1 for r in rows if r["outcome"] == "PASSED"),
        "failed": sum(1 for r in rows if r["outcome"] != "PASSED"),
        "total": len(rows),
        "rows": rows,
        "tail": (proc.stdout or "")[-3000:],
    }


def main() -> int:
    print("=== E1_X11 Gate V2 tests ===", flush=True)
    tests = _run_tests()
    print("tests", tests["passed"], "/", tests["total"], "exit", tests["exit_code"], flush=True)
    if tests["exit_code"] != 0:
        print(tests["tail"])
        return 1

    print("=== Run A ===", flush=True)
    report_a = run_once(label="A")
    if "MISMATCH" in str(report_a.get("verdict")):
        print("MISMATCH", flush=True)
        return 2

    print("=== Run B ===", flush=True)
    report_b = run_once(label="B")

    keys = [
        "source_identity_sha", "symbol_day_evaluability_sha", "policy_evaluable_day_sha",
        "asof_recurring_sha", "coverage_sha", "breadth_gate_sha", "capital_scenario_sha",
        "required_capital_sha", "kioxia_profile_sha", "blocker_matrix_sha", "verdict",
    ]
    sa, sb = report_a.get("determinism_shas") or {}, report_b.get("determinism_shas") or {}
    mismatches = [k for k in keys if sa.get(k) != sb.get(k)]
    det = {"ab_match": len(mismatches) == 0, "mismatches": mismatches, "A": sa, "B": sb}
    print("A/B", det["ab_match"], mismatches, flush=True)

    shas = publish(report_a, tests, det, PUBLISH)
    print("published", PUBLISH, shas, flush=True)
    print("VERDICT", report_a.get("verdict"), flush=True)
    print("BLOCKERS", report_a.get("all_blockers"), flush=True)
    print("STOP", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
