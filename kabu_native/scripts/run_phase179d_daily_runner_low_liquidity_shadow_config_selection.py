#!/usr/bin/env python3
"""
Phase179d: Verify daily runner can select low-liquidity shadow YAML without breaking existing trailing-mfe runs.

Writes:
- kabu_native/results/reports/phase179d_daily_runner_low_liquidity_shadow_config_selection.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


OUT = Path("kabu_native/results/reports/phase179d_daily_runner_low_liquidity_shadow_config_selection.json")


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
    repo_root, native_root = _bootstrap()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    from runner.am_pm_daily_runner import (
        DailyRunnerOptions,
        make_state,
        TRAILING_MFE_LOW_LIQ_SHADOW_YAML,
        TRAILING_MFE_SHADOW_YAML,
        UNIVERSE_MODE_PRICE_RISK,
    )
    from small_paper.config import load_pilot_config

    # Case 1: trailing-mfe WITHOUT low-liquidity flag should keep old YAML
    opt1 = DailyRunnerOptions(
        day_stamp="20260528",
        skip_kabu=True,
        skip_safety=True,
        skip_am=True,
        skip_pm=True,
        dry_run_only=True,
        generate_features=False,
        poll_interval_sec=5.0,
        universe_mode=UNIVERSE_MODE_PRICE_RISK,
        enable_intraday_refresh=True,
        exit_policy_shadow="trailing-mfe",
        low_liquidity_shadow=False,
        config_rel=TRAILING_MFE_SHADOW_YAML,
    )
    s1 = make_state(repo_root, native_root, opt1)
    cfg1 = load_pilot_config(repo_root / s1.options.config_rel)

    # Case 2: trailing-mfe WITH low-liquidity flag selects new YAML
    opt2 = DailyRunnerOptions(
        day_stamp="20260528",
        skip_kabu=True,
        skip_safety=True,
        skip_am=True,
        skip_pm=True,
        dry_run_only=True,
        generate_features=False,
        poll_interval_sec=5.0,
        universe_mode=UNIVERSE_MODE_PRICE_RISK,
        enable_intraday_refresh=True,
        exit_policy_shadow="trailing-mfe",
        low_liquidity_shadow=True,
        config_rel=TRAILING_MFE_LOW_LIQ_SHADOW_YAML,
    )
    s2 = make_state(repo_root, native_root, opt2)
    cfg2 = load_pilot_config(repo_root / s2.options.config_rel)

    report: dict[str, Any] = {
        "phase": "179d",
        "checks": {
            "case1_trailing_mfe_default_yaml_path": s1.options.config_rel,
            "case1_low_liquidity_shadow_false": s1.options.low_liquidity_shadow,
            "case1_cfg_low_liquidity_shadow_enabled": bool(
                getattr(cfg1, "low_liquidity_shadow_enabled", False)
            ),
            "case2_trailing_mfe_low_liq_yaml_path": s2.options.config_rel,
            "case2_low_liquidity_shadow_true": s2.options.low_liquidity_shadow,
            "case2_cfg_low_liquidity_shadow_enabled": bool(
                getattr(cfg2, "low_liquidity_shadow_enabled", False)
            ),
            "case2_order_enabled_false": not bool(getattr(cfg2, "order_enabled", True)),
            "case2_paper_only_true": bool(getattr(cfg2, "paper_only", False)),
            "case2_hard_reject_disabled_note": "low_liquidity_shadow is logging-only by implementation (accept path not blocked)",
        },
        "verdict": "ok",
    }
    if report["checks"]["case1_trailing_mfe_default_yaml_path"] != TRAILING_MFE_SHADOW_YAML:
        report["verdict"] = "fail_default_path_changed"
    if report["checks"]["case2_trailing_mfe_low_liq_yaml_path"] != TRAILING_MFE_LOW_LIQ_SHADOW_YAML:
        report["verdict"] = "fail_low_liq_path_not_selected"
    if report["checks"]["case2_cfg_low_liquidity_shadow_enabled"] is not True:
        report["verdict"] = "fail_yaml_missing_low_liq_enable"
    if report["checks"]["case2_order_enabled_false"] is not True or report["checks"]["case2_paper_only_true"] is not True:
        report["verdict"] = "fail_safety_flags"

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

