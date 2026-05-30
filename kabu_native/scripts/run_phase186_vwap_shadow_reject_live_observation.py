#!/usr/bin/env python3
"""
Phase186: VWAP shadow reject logging verification + live observation.

Writes:
  kabu_native/results/reports/phase186_vwap_shadow_reject_live_observation.json
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


OUT = Path("kabu_native/results/reports/phase186_vwap_shadow_reject_live_observation.json")


def _run(cmd: list[str], *, cwd: Path) -> dict[str, Any]:
    p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
    return {
        "command": " ".join(cmd),
        "exit_code": p.returncode,
        "ok": p.returncode == 0,
        "stderr": p.stderr[-3000:],
    }


def _bootstrap() -> Path:
    script = Path(__file__).resolve()
    repo = script.parents[2]
    native = script.parents[1]
    for p in (native / "src", repo):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    return repo


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    repo = _bootstrap()

    compile_res = _run(
        [
            sys.executable,
            "-m",
            "py_compile",
            str(repo / "kabu_native/src/small_paper/vwap_shadow_reject.py"),
            str(repo / "kabu_native/src/small_paper/pilot_runner.py"),
            str(repo / "kabu_native/src/small_paper/observer_position_tracker.py"),
        ],
        cwd=repo,
    )
    t186 = _run(
        [sys.executable, "-m", "unittest", "-q", "kabu_native.tests.test_phase186_vwap_shadow_reject"],
        cwd=repo,
    )
    t183 = _run(
        [sys.executable, "-m", "unittest", "-q", "kabu_native.tests.test_phase183_extended_entry_shadow"],
        cwd=repo,
    )

    src = (repo / "kabu_native/src/small_paper/pilot_runner.py").read_text(encoding="utf-8")
    obs = (repo / "kabu_native/src/small_paper/observer_position_tracker.py").read_text(encoding="utf-8")
    vwap_mod = (repo / "kabu_native/src/small_paper/vwap_shadow_reject.py").read_text(encoding="utf-8")
    checks = {
        "vwap_shadow_module_import": "VwapShadowRejectCounters" in vwap_mod,
        "accepted_fields": "vwap_shadow_reject_candidate" in src and "compute_vwap_shadow_reject_fields" in src,
        "summary_counters": "vwap_shadow_reject_candidate_count" in vwap_mod and "_vwap_shadow_summary_fields" in src,
        "observer_exit_enrich": "enrich_exit_vwap_shadow_fields" in obs,
        "no_hard_reject_vwap": "vwap_shadow_reject_candidate" in src
        and 'if vwap_shadow.get("vwap_shadow_reject_candidate")' not in src
        and "if vwap_shadow_reject_candidate:" not in src,
        "trailing_mfe_exit_field": "trailing_mfe_exit" in obs,
    }

    from research.phase186_vwap_shadow_reject_observation import (
        evaluate_vwap_shadow_reject_observation,
        finalize_observation_report,
    )

    observation = finalize_observation_report(
        evaluate_vwap_shadow_reject_observation(repo_root=repo)
    )

    verdict = "pass"
    if not compile_res.get("ok"):
        verdict = "fail_py_compile"
    elif not t186.get("ok"):
        verdict = "fail_tests"
    elif not all(checks.values()):
        verdict = "fail_checks"

    report = {
        "phase": 186,
        "verdict": verdict,
        "checks": checks,
        "py_compile": compile_res,
        "tests": {"test_phase186": t186, "test_phase183_regression": t183},
        "observation": observation,
        "notes": {
            "shadow_only": True,
            "hard_reject": False,
            "fixed_threshold_vwap_dev_pct": 2.5,
            "offline_baseline_until_live": observation.get("mode") == "offline_baseline_phase185_sessions",
        },
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    v = observation.get("verdict") or {}
    print(
        json.dumps(
            {
                "verdict": verdict,
                "mode": observation.get("mode"),
                "candidate_pf": v.get("candidate_pf"),
                "non_candidate_pf": v.get("non_candidate_pf"),
                "false_positive_rate": v.get("false_positive_rate"),
                "output": str(OUT).replace("\\", "/"),
            },
            ensure_ascii=False,
        )
    )
    return 0 if verdict == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
