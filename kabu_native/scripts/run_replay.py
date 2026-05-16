#!/usr/bin/env python3
"""
kabu_native batch replay — kabu_signal_v1 / kabu_exit_v1 on intraday 1m CSV.

例::
    python kabu_native/scripts/run_replay.py \\
        --start-date 2026-05-01 --end-date 2026-05-15 --symbols 9984.T,8306.T

    python kabu_native/scripts/run_replay.py \\
        --start-date 2026-05-12 --end-date 2026-05-15 \\
        --universe kabu_native/data/universe/universe_20260516.csv
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from datetime import datetime
from glob import glob
from pathlib import Path


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
    return repo_root, native_root


def _symbols_from_csv_list(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in items:
        s = raw.strip().upper()
        if not s:
            continue
        if not s.endswith(".T"):
            code = s.split("@", 1)[0]
            s = f"{code}.T"
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _symbols_from_universe(path: Path, *, passed_only: bool) -> list[str]:
    symbols: list[str] = []
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if passed_only:
                p = str(row.get("passed", "")).strip().lower()
                if p not in ("true", "1", "yes"):
                    continue
            sym = str(row.get("symbol", "")).strip()
            if sym:
                symbols.append(sym if sym.endswith(".T") else f"{sym}.T")
    return _symbols_from_csv_list(symbols)


def _symbols_from_morning_screen(
    path: Path,
    *,
    passed_only: bool,
    ranked_only: bool,
) -> list[str]:
    csv_path = _resolve_morning_screen_csv(path)
    symbols: list[str] = []
    with csv_path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if passed_only:
                p = str(row.get("pass_screen", "")).strip().lower()
                if p not in ("true", "1", "yes"):
                    continue
            if ranked_only:
                rank = str(row.get("rank", "")).strip()
                if not rank:
                    continue
            sym = str(row.get("symbol", "")).strip()
            if sym:
                symbols.append(sym if sym.endswith(".T") else f"{sym}.T")
    return _symbols_from_csv_list(symbols)


def _resolve_morning_screen_csv(path: Path) -> Path:
    if path.is_file():
        return path
    if path.is_dir():
        matches = sorted(path.glob("morning_screen_*.csv"))
        if not matches:
            matches = sorted(path.glob("*.csv"))
        if not matches:
            raise FileNotFoundError(f"no morning_screen csv in {path}")
        return matches[-1]
    patterns = sorted(glob(str(path)))
    if not patterns:
        raise FileNotFoundError(f"morning_screen path not found: {path}")
    return Path(patterns[-1])


def main() -> int:
    repo_root, native_root = _bootstrap()

    from replay.runner import ReplayRunConfig, load_replay_config, run_replay_batch

    parser = argparse.ArgumentParser(description="kabu_native 複数日リプレイ")
    parser.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--symbols", default=None, help="9984.T,8306.T")
    parser.add_argument("--universe", type=Path, default=None)
    parser.add_argument(
        "--universe-all-rows",
        action="store_true",
        help="universe CSV の passed=false 行も含める（既定: passed=true のみ）",
    )
    parser.add_argument("--morning-screen", type=Path, default=None, dest="morning_screen")
    parser.add_argument(
        "--config",
        type=Path,
        default=native_root / "configs" / "replay.yaml",
    )
    parser.add_argument("--tier", default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()

    config_path = args.config if args.config.is_absolute() else (repo_root / args.config)
    cfg_raw = load_replay_config(config_path, native_root=native_root, repo_root=repo_root)

    symbols: list[str] = []
    if args.symbols:
        symbols.extend(_symbols_from_csv_list(args.symbols.split(",")))
    if args.universe:
        up = args.universe if args.universe.is_absolute() else (repo_root / args.universe)
        passed_only = not args.universe_all_rows and bool(cfg_raw.get("universe_passed_only", True))
        symbols.extend(_symbols_from_universe(up, passed_only=passed_only))
    if args.morning_screen:
        mp = args.morning_screen if args.morning_screen.is_absolute() else (repo_root / args.morning_screen)
        symbols.extend(
            _symbols_from_morning_screen(
                mp,
                passed_only=bool(cfg_raw.get("morning_screen_passed_only", True)),
                ranked_only=bool(cfg_raw.get("morning_screen_ranked_only", False)),
            )
        )

    symbols = _symbols_from_csv_list(symbols)
    if not symbols:
        print("銘柄が指定されていません（--symbols / --universe / --morning-screen）", file=sys.stderr)
        return 2

    day_stamp = datetime.now().strftime("%Y%m%d")
    time_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = args.output_dir or (
        native_root / "results" / "replay" / day_stamp / f"replay_{time_stamp}"
    )

    run_cfg = ReplayRunConfig(
        start_date=args.start_date,
        end_date=args.end_date,
        symbols=symbols,
        data_roots=cfg_raw["data_roots"],
        output_dir=out_dir,
        tier=args.tier or str(cfg_raw.get("tier", "B")),
        entry_score_min=int(cfg_raw.get("entry_score_min", 60)),
        require_timing_ok=bool(cfg_raw.get("require_timing_ok", True)),
        relaxed_signal=bool(cfg_raw.get("relaxed_signal", False)),
        synthetic_push_keep=float(cfg_raw.get("synthetic_push_keep", 1.0)),
        synthetic_spread_bps=float(cfg_raw.get("synthetic_spread_bps", 8.0)),
        synthetic_events_per_minute=int(cfg_raw.get("synthetic_events_per_minute", 10)),
        eod_exit_reason=str(cfg_raw.get("eod_exit_reason", "eod_close")),
        repo_root=repo_root,
    )

    log_path = repo_root / "logs" / "runtime" / f"kabu_native_run_replay_{day_stamp}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("kabu_native.run_replay")
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

    log.info("symbols=%s range=%s..%s", symbols, args.start_date, args.end_date)
    result = run_replay_batch(run_cfg)
    log.info("trades=%s skipped=%s out=%s", len(result.trades), len(result.skipped), out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
