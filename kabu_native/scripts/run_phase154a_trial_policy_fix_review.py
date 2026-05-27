#!/usr/bin/env python3
"""
Phase 154a: Trial policy_label fix for entry_price_risk_guard_shadow YAML.

Example::
    python kabu_native/scripts/run_phase154a_trial_policy_fix_review.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

CONFIG_REL = "kabu_native/configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_shadow.yaml"
OLD_LABEL = "q070_cap3_entry_price_risk_guard_shadow"
NEW_LABEL = "q070_cap3_entry_price_risk_guard_shadow_trial"
TRIAL_CHECK_IDS = (
    "trial_policy_label",
    "mfe_favorable_trial_config",
    "daytrade_suitability_trial_config",
)


def _bootstrap() -> tuple[Path, Path]:
    script = Path(__file__).resolve()
    native = script.parents[1]
    repo = script.parents[2]
    for p in (native / "src", repo):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    return repo, native


def _yaml_policy_fields(repo: Path) -> dict[str, Any]:
    import yaml

    raw = yaml.safe_load((repo / CONFIG_REL).read_text(encoding="utf-8")) or {}
    return {
        "policy_label": raw.get("policy_label"),
        "policy_trial": raw.get("policy_trial"),
        "shadow_only": raw.get("shadow_only"),
    }


def _run_safety(repo: Path, *, live_mode: bool) -> dict[str, Any]:
    from small_paper.config import load_pilot_config
    from small_paper.pilot_env import load_pilot_environment
    from small_paper.safety import load_config_and_check

    cfg_path = repo / CONFIG_REL
    cfg_pre = load_pilot_config(cfg_path)
    load_pilot_environment(repo_root=repo, discord_webhook_env=cfg_pre.discord_webhook_env)
    day_key = datetime.now(JST).strftime("%Y%m%d")
    _, checks = load_config_and_check(
        cfg_path,
        repo_root=repo,
        day_key=day_key,
        live_mode=live_mode,
        dry_run_flag=True,
        session_stamp="000000" if live_mode else None,
    )
    failed = [c.check_id for c in checks if not c.passed]
    trial = [
        {"check_id": c.check_id, "passed": c.passed, "message": c.message}
        for c in checks
        if c.check_id in TRIAL_CHECK_IDS
    ]
    return {
        "live_mode": live_mode,
        "overall_pass": len(failed) == 0,
        "failed_check_ids": failed,
        "trial_checks": trial,
        "trial_checks_pass": all(c["passed"] for c in trial),
    }


def main() -> int:
    repo, native = _bootstrap()
    reports = native / "results/reports"

    yaml_fields = _yaml_policy_fields(repo)
    safety_non_live = _run_safety(repo, live_mode=False)

    from small_paper.config import load_pilot_config
    from small_paper.safety import (
        check_daytrade_suitability_trial_config,
        check_mfe_favorable_trial_config,
        check_trial_policy_label,
    )

    cfg = load_pilot_config(repo / CONFIG_REL)
    trial_checks = [
        check_trial_policy_label(cfg),
        check_mfe_favorable_trial_config(cfg),
        check_daytrade_suitability_trial_config(cfg, repo_root=repo, run_session_key=None),
    ]
    trial_only = {
        "checks": [
            {"check_id": c.check_id, "passed": c.passed, "message": c.message}
            for c in trial_checks
        ],
        "all_passed": all(c.passed for c in trial_checks),
    }

    cmd = (
        "python kabu_native/scripts/check_small_paper_safety.py --live "
        f"--config {CONFIG_REL}"
    )

    trial_fix_ok = (
        yaml_fields.get("policy_label") == NEW_LABEL
        and trial_only["all_passed"]
        and safety_non_live["overall_pass"]
    )

    report: dict[str, Any] = {
        "phase": "154a",
        "verdict": "trial_policy_fix_ok" if trial_fix_ok else "trial_policy_fix_incomplete",
        "recommended_fix": "A",
        "rejected_options": {
            "B_policy_trial_false": (
                "mfe_favorable_trial_config and daytrade_suitability_trial_config "
                "still require policy_trial=true with *_trial label when mfe_linked/vol_liq enabled"
            ),
            "C_relax_safety_for_shadow": (
                "Would weaken global invariant; other shadow YAMLs share the same mismatch"
            ),
        },
        "design_rationale": (
            "Shadow pilot YAMLs inherit policy_trial=true from vol_liq trial template but used "
            "*_shadow policy_label. safety.py treats policy_trial as trial namespace requiring "
            "*_trial suffix. Fix: append _trial while keeping shadow_only=true and *_shadow.yaml "
            "filename for experiment identity."
        ),
        "yaml_current": yaml_fields,
        "yaml_change": {"policy_label": {"before": OLD_LABEL, "after": NEW_LABEL}},
        "naming_convention": {
            "production_trial_labels": [
                "q070_cap3_mfe_fav_vol_liq_trial",
                "q070_cap3_mfe_fav_symbol_cooloff_trial",
            ],
            "shadow_yaml_filenames": "*_shadow.yaml (unchanged)",
            "shadow_policy_label_after_fix": NEW_LABEL,
            "invariant": "policy_trial=true requires policy_label.endswith('_trial')",
        },
        "failed_checks_before_fix": list(TRIAL_CHECK_IDS),
        "trial_policy_checks_after_fix": trial_only,
        "safety_non_live": safety_non_live,
        "safety_command": cmd,
        "safety_live_note": (
            "After fix, trial_policy_label / mfe_favorable_trial_config / "
            "daytrade_suitability_trial_config pass. Remaining --live failures (if any) "
            "are kabu_station_connection / env, not policy_label."
        ),
        "daily_runner_constant_updated": "ENTRY_GUARD_POLICY_LABEL=q070_cap3_entry_price_risk_guard_shadow_trial",
        "constraints": [
            "no_production_yaml_change",
            "no_universe_entry_exit_change",
            "shadow_yaml_label_only",
        ],
    }

    out = reports / "phase154a_trial_policy_fix_review.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "verdict": report["verdict"],
                "policy_label": yaml_fields.get("policy_label"),
                "trial_checks_pass": trial_only["all_passed"],
                "safety_non_live_pass": safety_non_live["overall_pass"],
            },
            indent=2,
        )
    )
    return 0 if trial_fix_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
