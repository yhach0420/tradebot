#!/usr/bin/env python3
"""
Phase 103: Sampling revision review — hybrid_stride_plus_rotation, no kabu /board.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


def _paths() -> tuple[Path, Path, Path]:
    script = Path(__file__).resolve()
    native_root = script.parents[1]
    repo_root = script.parents[2]
    return repo_root, native_root, native_root / "src"


def _bootstrap() -> tuple[Path, Path]:
    repo_root, native_root, src_root = _paths()
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))
    from api.rest_client import load_kabu_env

    load_kabu_env(repo_root=repo_root)
    return repo_root, native_root


def main() -> int:
    repo_root, native_root = _bootstrap()
    from universe.dynamic_build import load_dynamic_config, run_phase103_sampling_revision

    parser = argparse.ArgumentParser(description="Phase103 candidate sampling revision (no board fetch)")
    parser.add_argument(
        "--config",
        type=Path,
        default=native_root / "configs" / "universe_dynamic_trial.yaml",
    )
    parser.add_argument("--date-stamp", default=None, help="YYYYMMDD (default: today JST)")
    parser.add_argument(
        "--reports-dir",
        type=Path,
        default=native_root / "results" / "reports",
    )
    parser.add_argument("--symbol-master", type=Path, default=None)
    args = parser.parse_args()

    cfg_path = args.config if args.config.is_absolute() else (repo_root / args.config)
    if not cfg_path.is_file():
        print(f"config not found: {cfg_path}", file=sys.stderr)
        return 2

    cfg = load_dynamic_config(cfg_path)
    day_stamp = args.date_stamp or datetime.now(JST).strftime("%Y%m%d")
    reports_dir = args.reports_dir if args.reports_dir.is_absolute() else (repo_root / args.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)

    master_override = None
    if args.symbol_master:
        master_override = (
            args.symbol_master if args.symbol_master.is_absolute() else (repo_root / args.symbol_master)
        )

    payload = run_phase103_sampling_revision(
        repo_root=repo_root,
        cfg=cfg,
        day_stamp=day_stamp,
        reports_dir=reports_dir,
        symbol_master_override=master_override,
    )
    payload["generated_at"] = datetime.now(JST).isoformat(timespec="seconds")
    payload["config_path"] = str(cfg_path.relative_to(repo_root))

    print(
        json.dumps(
            {
                "verdict": payload.get("verdict"),
                "focus": payload.get("focus_diagnostics"),
                "coverage": payload.get("dispersion_diagnostics", {}).get(
                    "candidate_market_position_coverage_pct"
                ),
                "json": payload.get("output_json"),
                "candidates_csv": payload.get("output_candidates_csv"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
