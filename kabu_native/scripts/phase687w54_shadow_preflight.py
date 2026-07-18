#!/usr/bin/env python3
"""W54 Shadow Preflight — emit SHADOW_FORWARD_READY or BLOCKED."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1]
OUT = NATIVE / "results" / "research" / "pre_entry_market_state"
JST = __import__("zoneinfo").ZoneInfo("Asia/Tokyo")
SHADOW = NATIVE / "src" / "small_paper" / "cost_aware_entry_shadow.py"
PILOT = NATIVE / "src" / "small_paper" / "pilot_runner.py"


def main() -> int:
    checks = {}
    src = SHADOW.read_text(encoding="utf-8")
    # 1 NP exclusion
    checks["np_not_in_integrated_score"] = (
        "z(pbv2_score) + 0.35 * winner_enrichment - 0.45 * z(stop_risk)" in src
        or "z(pbv2)+0.35*winner_enrichment-0.45*z(stop_risk)" in src
    )
    checks["np_reject_absent"] = "reject = \"np_risk\"" not in src and "elif use_np" not in src
    checks["np_audit_only"] = "np_risk_score_audit" in src or "np_risk_audit" in src
    # 2 selection cycle wiring
    pilot = PILOT.read_text(encoding="utf-8")
    checks["notes_every_eval"] = "note_symbol_eval" in pilot
    checks["runs_on_scan_flush"] = "_cost_aware_shadow_on_scan_flush" in pilot
    # Accept-path-only logging removed; evaluation is note_symbol_eval + scan flush
    checks["not_only_on_accept"] = (
        "cae[\"official_entry\"] = True" not in pilot
        and "evaluate_shadow_candidate(" not in pilot
        and checks["notes_every_eval"]
        and checks["runs_on_scan_flush"]
    )
    # 3 unit tests
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_phase687w54_cost_aware_entry_shadow_preflight.py", "-q"],
        cwd=str(NATIVE),
        capture_output=True,
        text=True,
    )
    checks["unit_tests_pass"] = r.returncode == 0
    checks["unit_tests_output"] = (r.stdout or "") + (r.stderr or "")
    # 4 mainline non-interference
    checks["default_off"] = "shadow_enabled" in src and 'COST_AWARE_ENTRY_SHADOW' in src
    checks["fail_open_hooks"] = "except Exception" in pilot and "cost_aware_entry_shadow" in pilot
    # 5 EXIT fields
    checks["exit_fields_present"] = all(
        k in src
        for k in (
            "fixed_30m_exit_time",
            "fixed_30m_pnl",
            "official_runtime_exit_time",
            "shadow_exit_policy",
            "shadow_exit_pnl",
        )
    )
    # 6 summary fields
    checks["summary_fields"] = all(
        k in src
        for k in (
            "selection_cycles",
            "same_snapshot_nofill",
            "later_fill",
            "never_filled",
            "pnl_after_5bps",
        )
    )

    blocked_reasons = [k for k, v in checks.items() if k != "unit_tests_output" and v is False]
    ready = len(blocked_reasons) == 0
    verdict = "SHADOW_FORWARD_READY" if ready else "SHADOW_FORWARD_BLOCKED"

    report = {
        "metadata": {
            "phase": "W54_Shadow_Preflight",
            "generated_at": datetime.now(JST).isoformat(),
        },
        "verdict": verdict,
        "checks": {k: v for k, v in checks.items() if k != "unit_tests_output"},
        "blocked_reasons": blocked_reasons,
        "paper_settings": {
            "COST_AWARE_ENTRY_SHADOW": "1",
            "pbv2_mainline": "unchanged",
            "yaml": "unchanged",
            "exit": "unchanged",
            "cap": 5,
            "parallel": ["W43F_PAPER_FORWARD", "cost_aware_entry_shadow"],
        },
        "score_formula": "integrated_score = z(pbv2_score) + 0.35*winner_enrichment - 0.45*z(stop_risk)",
        "np_policy": "audit log only; never reject / score / rank",
        "unit_test_tail": checks.get("unit_tests_output", "")[-500:],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "shadow_preflight_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md = f"""# W54 Shadow Preflight

## Verdict
`{verdict}`

## Checks
```
{json.dumps(report['checks'], ensure_ascii=False, indent=2)}
```

## Paper
- `COST_AWARE_ENTRY_SHADOW=1`
- PBv2 / YAML / EXIT / CAP unchanged
- Parallel: W43F PAPER_FORWARD + Cost-Aware ENTRY Shadow

## Score
`{report['score_formula']}`
NP: {report['np_policy']}
"""
    (OUT / "shadow_preflight_report.md").write_text(md, encoding="utf-8")
    print(json.dumps({"verdict": verdict, "blocked": blocked_reasons}, ensure_ascii=False))
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
