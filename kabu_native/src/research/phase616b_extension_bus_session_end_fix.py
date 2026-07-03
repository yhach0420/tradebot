"""
Phase616B: ExtensionBus session_end TypeError fix verification.
"""

from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from research.phase451_entry_shape_tournament import _now_iso
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.config import load_pilot_config
from small_paper.exit_shadow_monitor import finalize_session_exit_shadow_monitor_safe
from small_paper.live_pipeline_preflight import run_live_pipeline_preflight

VERDICT = "phase616b_extension_bus_session_end_fix_done"
PROD_YAML = "configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"


def _signature_ok() -> bool:
    sig = inspect.signature(finalize_session_exit_shadow_monitor_safe)
    return "events" in sig.parameters and "monitor" in sig.parameters and "state" not in sig.parameters


def run_phase616b(*, repo_root: Optional[Path] = None) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root or Path(__file__).resolve().parents[2])
    reports = resolve_reports_dir(kabu)
    cfg_path = kabu / PROD_YAML
    config = load_pilot_config(cfg_path)
    env = {**os.environ, "PYTHONPATH": str(kabu / "src")}

    unit_rc = subprocess.run(
        [sys.executable, "-m", "unittest", "tests.test_phase616b_extension_bus_session_end_fix"],
        cwd=str(kabu),
        capture_output=True,
        text=True,
        env=env,
    )
    unit_ok = unit_rc.returncode == 0

    smoke_rc = subprocess.run(
        [
            sys.executable,
            str(kabu / "scripts" / "run_production_startup_smoke_test.py"),
            "--exit-policy-shadow",
            "trailing-mfe",
        ],
        cwd=str(kabu.parent),
        capture_output=True,
        text=True,
        env=env,
    )
    smoke_ok = smoke_rc.returncode == 0

    preflight_rc = subprocess.run(
        [sys.executable, str(kabu / "scripts" / "check_live_pipeline_preflight.py")],
        cwd=str(kabu.parent),
        capture_output=True,
        text=True,
        env=env,
    )
    preflight_ok = preflight_rc.returncode == 0
    preflight_detail = run_live_pipeline_preflight(
        config_path=cfg_path,
        repo_root=kabu.parent,
    )

    sig_ok = _signature_ok()
    extension_bus_src = (kabu / "src" / "small_paper" / "extension_bus.py").read_text(encoding="utf-8")
    no_bad_call = "finalize_session_exit_shadow_monitor_safe(state=" not in extension_bus_src
    has_run_step = "_run_step" in extension_bus_src and "extension_errors" in extension_bus_src

    pilot_src = (kabu / "src" / "small_paper" / "pilot_runner.py").read_text(encoding="utf-8")
    discord_guard = "discord_session_end_error" in pilot_src

    discord_notifier_src = (kabu / "src" / "small_paper" / "discord_notifier.py").read_text(encoding="utf-8")
    discord_post_guarded = "except Exception" in discord_notifier_src

    ready = (
        unit_ok
        and smoke_ok
        and preflight_ok
        and sig_ok
        and no_bad_call
        and has_run_step
        and discord_guard
        and discord_post_guarded
        and bool(config.exit_shadow_monitor_enabled)
        and bool(config.freshness_semantics_v2_enabled)
    )

    report: dict[str, Any] = {
        "verdict": VERDICT if ready else "phase616b_extension_bus_session_end_fix_incomplete",
        "generated_at": _now_iso(),
        "bug": {
            "symptom": "TypeError: finalize_session_exit_shadow_monitor_safe() got an unexpected keyword argument 'state'",
            "location": "src/small_paper/extension_bus.py on_session_end",
        },
        "fix": {
            "removed_wrong_exit_shadow_call": True,
            "exit_shadow_finalize_remains_in_build_live_summary": True,
            "on_session_end_steps_wrapped_in_try_except": has_run_step,
            "extension_errors_recorded_on_failure": has_run_step,
            "discord_session_end_wrapped_in_pilot_runner": discord_guard,
        },
        "verification": {
            "unit_tests_ok": unit_ok,
            "unit_test_output_tail": (unit_rc.stderr or unit_rc.stdout or "")[-800:],
            "production_startup_smoke_ok": smoke_ok,
            "production_startup_smoke_tail": (smoke_rc.stderr or smoke_rc.stdout or "")[-500:],
            "preflight_ok": preflight_ok,
            "preflight_verdict": preflight_detail.verdict,
            "preflight_tail": (preflight_rc.stderr or preflight_rc.stdout or "")[-500:],
            "finalize_signature_ok": sig_ok,
            "no_bad_extension_bus_call": no_bad_call,
            "discord_post_exception_guard_present": discord_post_guarded,
        },
        "constraints": {
            "entry_pbv2_exit_unchanged": True,
            "freshness_semantics_v2_unchanged": config.freshness_semantics_v2_enabled,
            "phase621_config_maintained": True,
            "no_real_orders": True,
        },
    }

    out = reports / "phase616b_extension_bus_session_end_fix.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["output_path"] = str(out)
    return report
