"""CLI: E1_X9 Universe Regime Audit A/B + publish; stop."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from research.e1_x9_universe_regime.publish import publish
from research.e1_x9_universe_regime.run_audit import PUBLISH, run_once


def _run_tests() -> dict:
    native = Path(__file__).resolve().parents[3]
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{native / 'src'};{native / 'research'}"
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rA", "--tb=line", "-p", "no:cacheprovider",
         str(native / "tests" / "test_e1_x9_universe_regime.py")],
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
        "tail": (proc.stdout or "")[-2000:],
    }


def main() -> int:
    print("=== E1_X9 tests ===", flush=True)
    tests = _run_tests()
    print("tests", tests["passed"], "/", tests["total"], "exit", tests["exit_code"], flush=True)
    if tests["exit_code"] != 0:
        print(tests["tail"])
        return 1

    print("=== Run A ===", flush=True)
    report_a = run_once(label="A")
    print("=== Run B ===", flush=True)
    report_b = run_once(label="B")

    keys = [
        "metadata_identity_sha", "asof_validity_sha", "coverage_sha", "regime_assignment_sha",
        "path_outcome_sha", "update_sensitivity_sha", "within_symbol_reference_sha",
        "economic_reference_sha", "verdict",
    ]
    sa, sb = report_a.get("determinism_shas") or {}, report_b.get("determinism_shas") or {}
    mismatches = [k for k in keys if sa.get(k) != sb.get(k)]
    det = {"ab_match": len(mismatches) == 0, "mismatches": mismatches, "A": sa, "B": sb}
    print("A/B", det["ab_match"], mismatches, flush=True)

    shas = publish(report_a, tests, det, PUBLISH)
    print("published", PUBLISH, shas, flush=True)
    print("VERDICT", report_a.get("verdict"), flush=True)
    print("STOP", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
