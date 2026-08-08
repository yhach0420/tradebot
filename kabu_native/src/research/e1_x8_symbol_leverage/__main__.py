"""CLI: E1_X8 Threshold Symbol Leverage Audit — load once, A/B analyze, publish, stop."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from research.e1_x6_fcrr.replay import _universe_from_manifest, load_day_events, load_source_manifest
from research.e1_x7_pfq.config import DAYS
from research.e1_x8_symbol_leverage.publish import publish
from research.e1_x8_symbol_leverage.run_audit import PUBLISH, _load_audits, run_analysis


def _run_tests() -> dict:
    native = Path(__file__).resolve().parents[3]
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{native / 'src'};{native / 'research'}"
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [
            sys.executable, "-m", "pytest", "-q", "-rA", "--tb=line",
            "-p", "no:cacheprovider",
            str(native / "tests" / "test_e1_x8_symbol_leverage.py"),
        ],
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
    print("=== E1_X8 tests ===", flush=True)
    tests = _run_tests()
    print("tests", tests["passed"], "/", tests["total"], "exit", tests["exit_code"], flush=True)
    if tests["exit_code"] != 0:
        print(tests["tail"])
        return 1

    print("=== Load events once ===", flush=True)
    sm = load_source_manifest()
    events_by_day = {}
    for day in DAYS:
        print("  preload", day, flush=True)
        events_by_day[day] = load_day_events(day, _universe_from_manifest(sm, day))
    audits, phase0 = _load_audits(events_by_day)
    print("audits", len(audits), "phase0", phase0.get("status"), flush=True)

    print("=== Run A ===", flush=True)
    report_a = run_analysis(audits, label="A")
    if str(report_a.get("verdict", "")).endswith("MISMATCH") or str(report_a.get("verdict", "")).endswith("UNRESOLVED"):
        publish(report_a, tests, {"ab_match": False, "early_stop": True}, PUBLISH)
        print("STOP", report_a.get("verdict"), flush=True)
        return 2

    print("=== Run B ===", flush=True)
    report_b = run_analysis(audits, label="B")

    keys = [
        "identity_sha", "full_threshold_sha", "symbol_profile_sha", "loso_threshold_sha",
        "membership_flip_sha", "random_deletion_sha", "frozen_signal_sensitivity_sha",
        "rederived_signal_sha", "group_classification_sha", "economic_reference_sha", "verdict",
    ]
    sa = report_a.get("determinism_shas") or {}
    sb = report_b.get("determinism_shas") or {}
    mismatches = [k for k in keys if sa.get(k) != sb.get(k)]
    det = {"ab_match": len(mismatches) == 0, "mismatches": mismatches, "A": sa, "B": sb}
    print("A/B", det["ab_match"], mismatches, flush=True)

    print("=== Publish ===", flush=True)
    shas = publish(report_a, tests, det, PUBLISH)
    print("published", PUBLISH, shas, flush=True)
    print("VERDICT", report_a.get("verdict"), flush=True)
    print("STOP", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
