#!/usr/bin/env python3
"""
Phase 148: Core10 + Dynamic40 AM/PM shadow daily runner.

One morning command runs preflight, AM universe + shadow, PM wait + universe regen + shadow,
then writes summary JSON under kabu_native/results/reports/.

Example (off-market validation)::

    python kabu_native/scripts/run_core10_dynamic40_am_pm_daily_runner.py \\
        --skip-kabu --skip-safety --dry-run-only --day-stamp 20260521

Production day (leave running through PM close; default = price-risk universe)::

    python kabu_native/scripts/run_core10_dynamic40_am_pm_daily_runner.py

Legacy universe (vol_liq only, no close>=300 filter)::

    python kabu_native/scripts/run_core10_dynamic40_am_pm_daily_runner.py \\
        --universe-mode core10-dynamic40
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


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

    from runner.am_pm_daily_runner import (
        ENTRY_GUARD_SHADOW_YAML,
        SHADOW_PILOT_YAML,
        UNIVERSE_MODE_DEFAULT,
        UNIVERSE_MODE_LEGACY,
        UNIVERSE_MODE_PRICE_RISK,
        DailyRunnerOptions,
        make_state,
        run_daily_runner,
    )
    from universe.day_stamp import normalize_day_stamp

    parser = argparse.ArgumentParser(
        description="Phase148 Core10+Dynamic40 AM/PM shadow daily runner"
    )
    parser.add_argument(
        "--day-stamp",
        default=None,
        help="YYYYMMDD trade date (default: today JST)",
    )
    parser.add_argument("--skip-kabu", action="store_true")
    parser.add_argument("--skip-safety", action="store_true")
    parser.add_argument("--skip-am", action="store_true")
    parser.add_argument("--skip-pm", action="store_true")
    parser.add_argument(
        "--dry-run-only",
        action="store_true",
        help="Preflight + universe build/validate only; no live pilot subprocess",
    )
    parser.add_argument("--poll-interval-sec", type=float, default=5.0)
    parser.add_argument(
        "--no-generate-features",
        action="store_true",
        help="Do not invoke phase113 when features CSV is missing",
    )
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument(
        "--universe-mode",
        default=UNIVERSE_MODE_DEFAULT,
        choices=[UNIVERSE_MODE_LEGACY, UNIVERSE_MODE_PRICE_RISK],
        help=(
            f"{UNIVERSE_MODE_PRICE_RISK} (default, Phase269) or "
            f"{UNIVERSE_MODE_LEGACY} (legacy vol_liq only)"
        ),
    )
    parser.add_argument(
        "--enable-intraday-refresh",
        action="store_true",
        help="Phase157: build 10:00/14:30 refresh CSVs and enable pilot refresh (price-risk only)",
    )
    parser.add_argument(
        "--exit-policy-shadow",
        default="",
        choices=["", "fade-hybrid", "fade-breakdown", "trailing-mfe"],
        help="Shadow-only exit policy overlay (e.g. fade-hybrid).",
    )
    parser.add_argument(
        "--startup-smoke-test",
        action="store_true",
        help="Phase552: production startup smoke test only (no AM/PM runner)",
    )
    parser.add_argument(
        "--low-liquidity-shadow",
        action="store_true",
        help="Phase179d: use trailing_mfe low-liquidity SHADOW logging YAML (non-prod).",
    )
    parser.add_argument(
        "--pre625-runtime-structure-mode",
        action="store_true",
        help="Phase612A alias for --core-runtime-mode CORE_ONLY",
    )
    parser.add_argument(
        "--core-runtime-mode",
        choices=("CORE_ONLY", "CORE_PLUS_AUDIT", "FULL_EXTENSION"),
        default=None,
        help="Phase616: Core vs Extension runtime (default FULL_EXTENSION)",
    )
    args = parser.parse_args()

    if args.startup_smoke_test:
        from small_paper.production_startup_smoke_test import run_production_startup_smoke_test

        config_rel = (
            SHADOW_PILOT_YAML
            if args.universe_mode == UNIVERSE_MODE_LEGACY
            else ENTRY_GUARD_SHADOW_YAML
        )
        if args.exit_policy_shadow == "fade-hybrid":
            from runner.am_pm_daily_runner import FADE_HYBRID_SHADOW_YAML

            config_rel = FADE_HYBRID_SHADOW_YAML
        if args.exit_policy_shadow == "fade-breakdown":
            from runner.am_pm_daily_runner import FADE_BREAKDOWN_SHADOW_YAML

            config_rel = FADE_BREAKDOWN_SHADOW_YAML
        if args.exit_policy_shadow == "trailing-mfe":
            from runner.am_pm_daily_runner import (
                TRAILING_MFE_LOW_LIQ_SHADOW_YAML,
                TRAILING_MFE_SHADOW_YAML,
            )

            config_rel = (
                TRAILING_MFE_LOW_LIQ_SHADOW_YAML
                if args.low_liquidity_shadow
                else TRAILING_MFE_SHADOW_YAML
            )
        if args.config:
            cfg = args.config if args.config.is_absolute() else repo_root / args.config
            try:
                config_rel = str(cfg.relative_to(repo_root)).replace("\\", "/")
            except ValueError:
                config_rel = str(cfg)
        report = run_production_startup_smoke_test(
            repo_root=repo_root,
            config_rel=config_rel,
        )
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return 0 if report.ready else 2

    day_stamp = (
        normalize_day_stamp(args.day_stamp)
        if args.day_stamp
        else datetime.now(JST).strftime("%Y%m%d")
    )
    config_rel = (
        SHADOW_PILOT_YAML
        if args.universe_mode == UNIVERSE_MODE_LEGACY
        else ENTRY_GUARD_SHADOW_YAML
    )
    if args.exit_policy_shadow == "fade-hybrid":
        from runner.am_pm_daily_runner import FADE_HYBRID_SHADOW_YAML

        config_rel = FADE_HYBRID_SHADOW_YAML
    if args.exit_policy_shadow == "fade-breakdown":
        from runner.am_pm_daily_runner import FADE_BREAKDOWN_SHADOW_YAML

        config_rel = FADE_BREAKDOWN_SHADOW_YAML
    if args.exit_policy_shadow == "trailing-mfe":
        from runner.am_pm_daily_runner import (
            TRAILING_MFE_LOW_LIQ_SHADOW_YAML,
            TRAILING_MFE_SHADOW_YAML,
        )

        config_rel = (
            TRAILING_MFE_LOW_LIQ_SHADOW_YAML
            if args.low_liquidity_shadow
            else TRAILING_MFE_SHADOW_YAML
        )
    if args.config:
        cfg = args.config if args.config.is_absolute() else repo_root / args.config
        try:
            config_rel = str(cfg.relative_to(repo_root)).replace("\\", "/")
        except ValueError:
            config_rel = str(cfg)

    options = DailyRunnerOptions(
        day_stamp=day_stamp,
        skip_kabu=args.skip_kabu,
        skip_safety=args.skip_safety,
        skip_am=args.skip_am,
        skip_pm=args.skip_pm,
        dry_run_only=args.dry_run_only,
        poll_interval_sec=args.poll_interval_sec,
        generate_features=not args.no_generate_features,
        config_rel=config_rel,
        universe_mode=args.universe_mode,
        enable_intraday_refresh=args.enable_intraday_refresh,
        exit_policy_shadow=args.exit_policy_shadow,
        low_liquidity_shadow=bool(args.low_liquidity_shadow),
        pre625_runtime_structure_mode=bool(args.pre625_runtime_structure_mode),
        core_runtime_mode=str(args.core_runtime_mode or ""),
    )
    state = make_state(repo_root, native_root, options)
    rc = run_daily_runner(state)
    out_paths = {
        "phase148": f"kabu_native/results/reports/phase148_am_pm_daily_runner_{day_stamp}.json",
        "summary": f"kabu_native/results/reports/daily_runner_summary_{day_stamp}.json",
        "commands": f"kabu_native/results/reports/daily_runner_commands_{day_stamp}.json",
    }
    if args.universe_mode == UNIVERSE_MODE_PRICE_RISK:
        out_paths["phase154_commands"] = (
            f"kabu_native/results/reports/phase154_daily_runner_price_risk_shadow_commands_{day_stamp}.json"
        )
    if args.enable_intraday_refresh:
        out_paths["phase157_review"] = (
            f"kabu_native/results/reports/phase157_intraday_refresh_runner_review.json"
        )
        out_paths["am_refresh_csv"] = (
            f"kabu_native/results/reports/"
            f"universe_core10_dynamic40_price_risk_am_refresh1000_{day_stamp}.csv"
        )
        out_paths["pm_refresh_csv"] = (
            f"kabu_native/results/reports/"
            f"universe_core10_dynamic40_price_risk_pm_refresh1430_{day_stamp}.csv"
        )
    print(
        json.dumps(
            {
                "verdict": state.verdict,
                "exit_code": rc,
                "universe_mode": args.universe_mode,
                "config_rel": config_rel,
                "outputs": out_paths,
            },
            ensure_ascii=True,
        )
    )
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
