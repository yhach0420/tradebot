#!/usr/bin/env python3
"""
Phase 97–98: Build hybrid static + dynamic universe CSV for shadow / dry-run only.

例::
    python kabu_native/scripts/build_jpx_symbol_master.py --input data/jpx/raw/listed_issues.xlsx
    python kabu_native/scripts/build_dynamic_universe.py --skip-kabu
    python kabu_native/scripts/build_dynamic_universe.py --symbol-master data/jpx/prime_symbols.csv
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


def _paths() -> tuple[Path, Path, Path]:
    script = Path(__file__).resolve()
    native_root = script.parents[1]
    repo_root = script.parents[2]
    src_root = native_root / "src"
    return repo_root, native_root, src_root


def _bootstrap() -> tuple[Path, Path]:
    repo_root, native_root, src_root = _paths()
    src_s = str(src_root)
    if src_s not in sys.path:
        sys.path.insert(0, src_s)
    from api.rest_client import load_kabu_env

    load_kabu_env(repo_root=repo_root)
    return repo_root, native_root


def _setup_logging(log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("kabu_native.build_dynamic_universe")
    log.handlers.clear()
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    log.addHandler(fh)
    log.addHandler(sh)
    log.propagate = False
    return log


def main() -> int:
    repo_root, native_root = _bootstrap()

    from universe.dynamic_build import build_dynamic_universe, load_dynamic_config

    parser = argparse.ArgumentParser(description="Build shadow hybrid dynamic universe CSV")
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
    parser.add_argument(
        "--skip-kabu",
        action="store_true",
        help="Alias for --board-mode none (no kabu /board)",
    )
    parser.add_argument(
        "--board-mode",
        choices=("none", "validate", "score"),
        default=None,
        help="none=board-free dynamic23; validate=final50 check; score=board dynamic23 only (default: config or none)",
    )
    parser.add_argument(
        "--legacy-bulk-board",
        action="store_true",
        help="Phase102 legacy: up to 400 /board fetches (not recommended)",
    )
    parser.add_argument(
        "--symbol-master",
        type=Path,
        default=None,
        help="Symbol master CSV (default: data/jpx/tradable_symbols.csv from config)",
    )
    args = parser.parse_args()

    cfg_path = args.config if args.config.is_absolute() else (repo_root / args.config)
    if not cfg_path.is_file():
        print(f"config not found: {cfg_path}", file=sys.stderr)
        return 2

    cfg = load_dynamic_config(cfg_path)
    if args.symbol_master:
        sm = args.symbol_master if args.symbol_master.is_absolute() else (repo_root / args.symbol_master)
        cfg.symbol_master_path = str(sm.relative_to(repo_root)) if sm.is_relative_to(repo_root) else str(sm)
        cfg.symbol_master_paths = [cfg.symbol_master_path]
    day_stamp = args.date_stamp or datetime.now(JST).strftime("%Y%m%d")
    reports_dir = args.reports_dir if args.reports_dir.is_absolute() else (repo_root / args.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)

    log_path = repo_root / "logs" / "runtime" / f"kabu_native_build_dynamic_universe_{day_stamp}.log"
    log = _setup_logging(log_path)

    master_override = None
    if args.symbol_master:
        master_override = args.symbol_master if args.symbol_master.is_absolute() else (repo_root / args.symbol_master)

    board_mode = args.board_mode or cfg.board_mode
    if args.skip_kabu:
        board_mode = "none"

    payload = build_dynamic_universe(
        repo_root=repo_root,
        cfg=cfg,
        day_stamp=day_stamp,
        reports_dir=reports_dir,
        skip_kabu=args.skip_kabu or board_mode == "none",
        symbol_master_override=master_override,
        log=log,
        board_mode=board_mode,
        legacy_bulk_board_fetch=args.legacy_bulk_board,
    )
    phase = int(payload.get("phase") or 105)
    payload["phase"] = phase
    payload["generated_at"] = datetime.now(JST).isoformat(timespec="seconds")
    payload["config_path"] = str(cfg_path.relative_to(repo_root))
    payload["board_mode"] = board_mode

    if phase == 105:
        phase_json = reports_dir / f"phase105_register_limit_aware_universe_{day_stamp}.json"
        if not phase_json.is_file():
            phase_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        phase102_path = reports_dir / f"phase102_dynamic_universe_fetch_revision_{day_stamp}.json"
        phase102_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    diag_path = reports_dir / f"phase98_dynamic_universe_build_{day_stamp}.json"
    legacy = {**payload, "phase": 98, "verdict": payload.get("legacy_verdict", payload.get("verdict"))}
    diag_path.write_text(json.dumps(legacy, ensure_ascii=False, indent=2), encoding="utf-8")

    log.info(
        "verdict=%s static=%s dynamic=%s total=%s board_mode=%s",
        payload.get("verdict"),
        payload.get("static_count"),
        payload.get("dynamic_count"),
        payload.get("total_count"),
        board_mode,
    )
    log.info("CSV: %s", payload.get("output_universe_csv"))

    out_msg = {
        "verdict": payload.get("verdict"),
        "output": payload.get("output_universe_csv"),
        "board_mode": board_mode,
    }
    if phase == 105:
        out_msg["phase105_json"] = payload.get("phase105_json_path")
        out_msg["dynamic_pool_csv"] = payload.get("phase105_dynamic_candidate_pool_csv")
    else:
        out_msg["phase102_json"] = payload.get("phase102_json_path")
        out_msg["candidates_csv"] = payload.get("phase103_board_fetch_candidates_csv")
    print(json.dumps(out_msg, ensure_ascii=True))

    # Safe exit for shadow tooling (never fail on missing master)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
