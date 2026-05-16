#!/usr/bin/env python3
"""
Run kabu_native morning screening from universe CSV + live /board.

例::
    python kabu_native/scripts/run_morning_screen.py \\
        --universe kabu_native/data/universe/universe_20260516.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path


_CSV_FIELDS = (
    "rank",
    "symbol",
    "symbol_name",
    "current_price",
    "change_pct",
    "trading_value",
    "trading_volume",
    "vwap",
    "vwap_distance_pct",
    "high_proximity_ratio",
    "spread_bps",
    "board_imbalance",
    "freshness_sec",
    "score",
    "pass_screen",
    "reject_reasons",
)


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
    log = logging.getLogger("kabu_native.run_morning_screen")
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


def _output_paths(native_root: Path, day_stamp: str, time_stamp: str) -> tuple[Path, Path]:
    out_dir = native_root / "results" / "morning_screen" / day_stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    base = f"morning_screen_{day_stamp}_{time_stamp}"
    return out_dir / f"{base}.csv", out_dir / f"{base}.json"


def main() -> int:
    repo_root, native_root = _bootstrap()

    from api.rest_client import (
        KabuNativeApiError,
        KabuNativeRestClient,
        default_base_url,
        require_kabu_password,
    )
    from screening.morning_screen import (
        compute_batch_stats,
        load_morning_screen_config,
        load_universe_passed,
        rank_results,
        score_symbol,
    )

    parser = argparse.ArgumentParser(description="kabu_native 朝スクリーニング")
    parser.add_argument("--universe", type=Path, required=True, help="universe_YYYYMMDD.csv（passed=true のみ使用）")
    parser.add_argument(
        "--config",
        type=Path,
        default=native_root / "configs" / "morning_screen.yaml",
        help="スコアリング設定 YAML",
    )
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--date-stamp", default=None, help="出力 YYYYMMDD（既定: 今日）")
    args = parser.parse_args()

    universe_path = args.universe if args.universe.is_absolute() else (repo_root / args.universe)
    config_path = args.config if args.config.is_absolute() else (repo_root / args.config)

    if not universe_path.is_file():
        print(f"universe not found: {universe_path}", file=sys.stderr)
        return 2
    if not config_path.is_file():
        print(f"config not found: {config_path}", file=sys.stderr)
        return 2

    entries = load_universe_passed(universe_path)
    if not entries:
        print("universe に passed=true の銘柄がありません", file=sys.stderr)
        return 2

    config = load_morning_screen_config(config_path)
    day_stamp = args.date_stamp or datetime.now().strftime("%Y%m%d")
    time_stamp = datetime.now().strftime("%H%M%S")
    csv_path, json_path = _output_paths(native_root, day_stamp, time_stamp)
    log_path = repo_root / "logs" / "runtime" / f"kabu_native_morning_screen_{day_stamp}.log"
    log = _setup_logging(log_path)

    try:
        password = require_kabu_password()
    except KabuNativeApiError as e:
        log.error("%s", e)
        return 2

    client = KabuNativeRestClient(
        base_url=(args.base_url or default_base_url()).rstrip("/"),
        timeout=args.timeout,
        max_retries=args.max_retries,
    )

    try:
        token = client.issue_token(password)
        log.info("token 取得成功")
    except KabuNativeApiError as e:
        log.error("token: %s", e)
        return 1

    boards: dict[str, dict] = {}
    errors: dict[str, str] = {}
    for entry in entries:
        try:
            boards[entry.symbol_key] = client.get_board(entry.symbol_key, token=token)
        except KabuNativeApiError as e:
            errors[entry.symbol_key] = str(e)
            boards[entry.symbol_key] = {}

    batch_stats = compute_batch_stats({k: v for k, v in boards.items() if v})

    results = []
    for entry in entries:
        board = boards.get(entry.symbol_key) or None
        if board is not None and not board:
            board = None
        row = score_symbol(
            entry,
            board,
            config,
            batch_stats=batch_stats,
            board_error=errors.get(entry.symbol_key),
        )
        results.append(row)

    results = rank_results(
        results,
        max_symbols=config.max_symbols,
        output_all_rows=config.output_all_rows,
    )
    results.sort(key=lambda r: (r.rank is None, r.rank if r.rank is not None else 9999, -r.score))

    passed_ranked = [r for r in results if r.rank is not None]
    for r in results:
        log.info(
            "%s %s score=%.2f pass=%s reasons=%s",
            f"TOP{r.rank}" if r.rank else "----",
            r.symbol_key,
            r.score,
            r.pass_screen,
            "|".join(r.reject_reasons) or "-",
        )

    payload = {
        "meta": {
            "component": "kabu_native.run_morning_screen",
            "generated_at_local": datetime.now().isoformat(timespec="seconds"),
            "universe_path": str(universe_path.relative_to(repo_root)),
            "config_path": str(config_path.relative_to(repo_root)),
            "day_stamp": day_stamp,
            "csv_path": str(csv_path.relative_to(repo_root)),
            "json_path": str(json_path.relative_to(repo_root)),
            "runtime_log": str(log_path.relative_to(repo_root)),
            "universe_passed_count": len(entries),
            "screened_count": len(results),
            "top_count": len(passed_ranked),
        },
        "config": {
            "session_mode": config.session_mode,
            "weights": config.weights,
            "gates": asdict(config.gates),
            "max_symbols": config.max_symbols,
            "output_all_rows": config.output_all_rows,
        },
        "top": [r.to_json_dict() for r in passed_ranked],
        "rows": [r.to_json_dict() for r in results],
    }

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        for row in results:
            writer.writerow(row.to_csv_dict())

    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("CSV: %s", csv_path.relative_to(repo_root))
    log.info("JSON: %s", json_path.relative_to(repo_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
