#!/usr/bin/env python3
"""Phase552: production startup smoke test (same path as run_paper_trade.bat)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
KABU = REPO / "kabu_native"


def _bootstrap() -> Path:
    for p in (KABU / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    return REPO


def _resolve_config_rel(
    *,
    repo_root: Path,
    universe_mode: str,
    exit_policy_shadow: str,
    config: Path | None,
) -> str:
    from runner.am_pm_daily_runner import (
        ENTRY_GUARD_SHADOW_YAML,
        SHADOW_PILOT_YAML,
        TRAILING_MFE_SHADOW_YAML,
        UNIVERSE_MODE_LEGACY,
        UNIVERSE_MODE_PRICE_RISK,
    )

    if config is not None:
        cfg = config if config.is_absolute() else repo_root / config
        try:
            return str(cfg.relative_to(repo_root)).replace("\\", "/")
        except ValueError:
            return str(cfg)

    config_rel = (
        SHADOW_PILOT_YAML
        if universe_mode == UNIVERSE_MODE_LEGACY
        else ENTRY_GUARD_SHADOW_YAML
    )
    if exit_policy_shadow == "trailing-mfe":
        config_rel = TRAILING_MFE_SHADOW_YAML
    return config_rel


def main() -> int:
    repo_root = _bootstrap()
    parser = argparse.ArgumentParser(description="Production startup smoke test")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument(
        "--universe-mode",
        default="core10-dynamic40-price-risk-filter-shadow",
    )
    parser.add_argument(
        "--exit-policy-shadow",
        default="trailing-mfe",
        choices=["", "fade-hybrid", "fade-breakdown", "trailing-mfe"],
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    from small_paper.production_startup_smoke_test import run_production_startup_smoke_test

    config_rel = _resolve_config_rel(
        repo_root=repo_root,
        universe_mode=args.universe_mode,
        exit_policy_shadow=args.exit_policy_shadow,
        config=args.config,
    )
    report = run_production_startup_smoke_test(
        repo_root=repo_root,
        config_rel=config_rel,
    )
    payload = report.to_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif report.ready:
        print(f"[SMOKE] production startup ok ({report.config_rel})")
    else:
        print("[SMOKE] production startup failed", file=sys.stderr)
        for err in report.errors:
            print(f"  - {err}", file=sys.stderr)
    return 0 if report.ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
