"""CLI: E1_X12 Risk Infrastructure Collection A/B + publish; stop."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from research.e1_x12_risk_history.publish import publish
from research.e1_x12_risk_history.run_audit import PUBLISH, run_once


def _run_tests() -> dict:
    native = Path(__file__).resolve().parents[3]
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{native / 'src'};{native / 'research'}"
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rA", "--tb=line", "-p", "no:cacheprovider",
         str(native / "tests" / "test_e1_x12_risk_history.py")],
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
    print("=== E1_X12 tests ===", flush=True)
    tests = _run_tests()
    print("tests", tests["passed"], "/", tests["total"], "exit", tests["exit_code"], flush=True)
    if tests["exit_code"] != 0:
        print(tests["tail"])
        return 1

    print("=== Run A ===", flush=True)
    report_a = run_once(label="A")
    print("=== Run B ===", flush=True)
    report_b = run_once(label="B")

    # A/B: registry sha may include classified_at timestamp — use structural keys excluding timestamps
    keys = ["manifest_sha", "symbol_day_risk_sha", "panel_reconciliation_sha", "policy_fractions_sha", "status"]
    sa, sb = report_a.get("determinism_shas") or {}, report_b.get("determinism_shas") or {}
    # registry: compare status map only
    def reg_map(r):
        return sorted((x["date"], x["status"]) for x in (r.get("date_registry") or {}).get("rows") or [])
    mismatches = [k for k in keys if sa.get(k) != sb.get(k)]
    if reg_map(report_a) != reg_map(report_b):
        mismatches.append("registry_status_map")
    det = {
        "ab_match": len(mismatches) == 0,
        "mismatches": mismatches,
        "A": sa,
        "B": sb,
        "registry_sha_A": sa.get("registry_sha"),
        "registry_sha_B": sb.get("registry_sha"),
        "note": "classified_at_jst may differ; status map compared separately",
    }
    print("A/B", det["ab_match"], mismatches, flush=True)

    shas = publish(report_a, tests, det, PUBLISH)
    print("published", PUBLISH, shas, flush=True)
    print("STATUS", report_a.get("status"), flush=True)
    print("NEW", report_a.get("newly_classified_date"), flush=True)
    print("VALID", report_a.get("history_coverage", {}).get("risk_history_days_valid"),
          "REMAINING", report_a.get("days_remaining_to_20"), flush=True)
    print("STOP", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
