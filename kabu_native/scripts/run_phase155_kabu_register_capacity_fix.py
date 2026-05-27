#!/usr/bin/env python3
"""
Phase 155: Review kabu register capacity fix (unregister/all + 4002006 retry).

Example::
    python kabu_native/scripts/run_phase155_kabu_register_capacity_fix.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def _bootstrap() -> tuple[Path, Path]:
    script = Path(__file__).resolve()
    native = script.parents[1]
    repo = script.parents[2]
    for p in (native / "src", repo):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    return repo, native


def main() -> int:
    repo, native = _bootstrap()
    reports = native / "results/reports"

    from api.kabu_register import (
        KABU_PUSH_REGISTER_LIMIT,
        assess_register_capacity,
        clear_register_before_session,
        is_register_limit_error,
        parse_kabu_error_code,
    )
    from api.push_client import push_spec
    from api.rest_client import KabuNativeApiError

    # Offline unit tests
    test_proc = subprocess.run(
        [sys.executable, str(native / "tests/test_kabu_register.py")],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )

    recovery_test: dict[str, Any] = {
        "parse_4002006": parse_kabu_error_code(
            KabuNativeApiError('register HTTP 400: {"Code":4002006,"Message":"レジスト数エラー"}')
        )
        == 4002006,
        "is_register_limit_error": is_register_limit_error(
            KabuNativeApiError('register HTTP 400: {"Code":4002006}')
        ),
        "pytest_exit_code": test_proc.returncode,
        "pytest_stdout": (test_proc.stdout or "")[-1500:],
        "pytest_stderr": (test_proc.stderr or "")[-500:],
    }

    cap = assess_register_capacity(universe_symbol_count=50)
    api_spec = push_spec()
    clear_live: dict[str, Any] = {"skipped": True, "reason": "no_password_or_off_hours"}
    try:
        clear_live = clear_register_before_session(repo)
    except Exception as e:
        clear_live = {"ok": False, "error": str(e)}

    fix_report: dict[str, Any] = {
        "phase": "155",
        "verdict": "register_capacity_fix_ready",
        "verdict_options": {
            "A": "register_capacity_fix_ready",
            "B": "unregister_api_missing",
            "C": "retry_logic_added_but_needs_live_test",
            "D": "capacity_limit_unknown",
        },
        "root_cause": (
            "2026-05-26 AM: register HTTP 400 Code 4002006 without prior unregister/all. "
            "Stale registrations + 50 new symbols exceeded kabu shared register cap."
        ),
        "api_survey": {
            "register_endpoint": api_spec.get("register_endpoint"),
            "unregister_all_endpoint": api_spec.get("unregister_all_endpoint"),
            "per_symbol_unregister": False,
            "list_registered_symbols_api": False,
            "register_limit": KABU_PUSH_REGISTER_LIMIT,
        },
        "code_changes": {
            "api/kabu_register.py": "register_symbols_cleared + clear_register_before_session",
            "pilot_runner.py": "unregister before register; 4002006 retry; Discord fatal message",
            "am_pm_daily_runner.py": "preflight/AM/PM unregister/all between sessions",
            "safety.py": "check_kabu_register_capacity with optional pre_clear",
            "record_push_jsonl.py": "register_symbols_cleared",
        },
        "capacity_assessment": cap,
        "live_clear_probe": clear_live,
        "pytest_offline": recovery_test["pytest_exit_code"] == 0,
        "constraints": [
            "no_production_yaml_change",
            "no_universe_entry_exit_change",
            "kabu_register_layer_only",
        ],
        "live_test_note": (
            "Next AM shadow session should show unregister_all_before_register in errors.jsonl "
            "only on recovery; ENTRY_count>0 if register succeeds."
        ),
    }

    if not api_spec.get("unregister_all_endpoint"):
        fix_report["verdict"] = "unregister_api_missing"
    elif recovery_test["pytest_exit_code"] != 0:
        fix_report["verdict"] = "retry_logic_added_but_needs_live_test"
    elif not clear_live.get("ok") and not clear_live.get("skipped"):
        fix_report["verdict"] = "retry_logic_added_but_needs_live_test"
        fix_report["verdict_notes"] = ["offline retry tests pass; live unregister probe failed"]
    else:
        fix_report["verdict_notes"] = [
            "unregister/all available; pilot clears before register; 4002006 retry once",
        ]
        if not clear_live.get("cleared"):
            fix_report["verdict"] = "retry_logic_added_but_needs_live_test"

    out_fix = reports / "phase155_kabu_register_capacity_fix.json"
    out_recovery = reports / "phase155_register_error_recovery_test.json"
    out_fix.write_text(json.dumps(fix_report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_recovery.write_text(
        json.dumps(
            {
                "phase": "155",
                "recovery_scenarios": recovery_test,
                "simulated_flow": [
                    "1. unregister_all_before_register",
                    "2. register (50 symbols)",
                    "3. on 4002006: unregister_all_retry + register_retry",
                    "4. fatal + Discord if still failing",
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(json.dumps({"verdict": fix_report["verdict"], "outputs": str(out_fix)}, indent=2))
    return 0 if fix_report["verdict"] in (
        "register_capacity_fix_ready",
        "retry_logic_added_but_needs_live_test",
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
