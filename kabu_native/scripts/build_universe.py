#!/usr/bin/env python3
"""
Build kabu_native universe from config + live board filters.

例::
    python kabu_native/scripts/build_universe.py --config kabu_native/configs/universe.yaml
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
    log = logging.getLogger("kabu_native.build_universe")
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


def _output_paths(native_root: Path, day_stamp: str) -> tuple[Path, Path]:
    out_dir = native_root / "data" / "universe"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"universe_{day_stamp}.csv"
    json_path = out_dir / f"universe_{day_stamp}.json"
    return csv_path, json_path


def main() -> int:
    repo_root, native_root = _bootstrap()

    from api.rest_client import (
        KabuNativeApiError,
        KabuNativeRestClient,
        default_base_url,
        require_kabu_password,
    )
    from universe.filters import apply_max_symbols, evaluate_board, load_universe_config
    from universe.symbols import ParsedSymbol

    parser = argparse.ArgumentParser(description="kabu_native universe ビルド")
    parser.add_argument(
        "--config",
        type=Path,
        default=native_root / "configs" / "universe.yaml",
        help="universe YAML 設定",
    )
    parser.add_argument("--base-url", default=None, help="REST ベース URL")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument(
        "--date-stamp",
        default=None,
        help="出力ファイル日付 YYYYMMDD（既定: 今日）",
    )
    args = parser.parse_args()

    config_path = args.config if args.config.is_absolute() else (repo_root / args.config)
    if not config_path.is_file():
        print(f"config not found: {config_path}", file=sys.stderr)
        return 2

    config = load_universe_config(config_path)
    candidates = config.parsed_include()
    if not candidates:
        print("include_symbols が空です", file=sys.stderr)
        return 2

    day_stamp = args.date_stamp or datetime.now().strftime("%Y%m%d")
    csv_path, json_path = _output_paths(native_root, day_stamp)
    log_path = repo_root / "logs" / "runtime" / f"kabu_native_build_universe_{day_stamp}.log"
    log = _setup_logging(log_path)

    try:
        password = require_kabu_password()
    except KabuNativeApiError as e:
        log.error("%s", e)
        return 2

    base_url = (args.base_url or default_base_url()).rstrip("/")
    client = KabuNativeRestClient(
        base_url=base_url,
        timeout=args.timeout,
        max_retries=args.max_retries,
    )

    try:
        token = client.issue_token(password)
        log.info("token 取得成功（ログ・JSON に出力しません）")
    except KabuNativeApiError as e:
        log.error("token: %s", e)
        return 1

    rows = []
    for parsed in candidates:
        board, err = _fetch_board(client, token, parsed)
        row = evaluate_board(parsed, board, config, board_error=err)
        rows.append(row)
        status = "PASS" if row.passed else "SKIP"
        log.info(
            "%s %s %s reasons=%s tv=%s",
            status,
            row.symbol_key,
            row.symbol_name or "",
            "|".join(row.exclude_reasons) or "-",
            row.trading_value,
        )

    rows = apply_max_symbols(rows, config.max_symbols)

    included = [r for r in rows if r.passed]
    excluded = [r for r in rows if not r.passed]

    payload = {
        "meta": {
            "component": "kabu_native.build_universe",
            "generated_at_local": datetime.now().isoformat(timespec="seconds"),
            "config_path": str(config_path.relative_to(repo_root)),
            "base_url": base_url,
            "day_stamp": day_stamp,
            "csv_path": str(csv_path.relative_to(repo_root)),
            "json_path": str(json_path.relative_to(repo_root)),
            "runtime_log": str(log_path.relative_to(repo_root)),
            "candidate_count": len(rows),
            "included_count": len(included),
            "excluded_count": len(excluded),
        },
        "config": asdict(config),
        "included": [r.to_json_dict() for r in included],
        "excluded": [r.to_json_dict() for r in excluded],
        "rows": [r.to_json_dict() for r in rows],
    }

    _write_csv(csv_path, rows)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    log.info("included=%s excluded=%s", len(included), len(excluded))
    log.info("CSV: %s", csv_path.relative_to(repo_root))
    log.info("JSON: %s", json_path.relative_to(repo_root))
    return 0


def _fetch_board(
    client: object,
    token: str,
    parsed: ParsedSymbol,
) -> tuple[dict | None, str | None]:
    from api.rest_client import KabuNativeApiError, KabuNativeRestClient

    assert isinstance(client, KabuNativeRestClient)
    try:
        return client.get_board(parsed.symbol_key, token=token), None
    except KabuNativeApiError as e:
        return None, str(e)


_CSV_FIELDS = (
    "symbol",
    "exchange",
    "symbol_key",
    "symbol_name",
    "passed",
    "exclude_reasons",
    "current_price",
    "trading_value",
    "trading_volume",
    "spread_bps",
    "security_type",
    "exchange_name",
    "board_error",
)


def _write_csv(path: Path, rows: list) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.to_csv_dict())


if __name__ == "__main__":
    raise SystemExit(main())
