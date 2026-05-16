#!/usr/bin/env python3
"""
kabu_native API 接続チェック（REST board + 任意 PUSH spec）。

例::
    python kabu_native/scripts/check_api.py --symbol 9984
    python kabu_native/scripts/check_api.py --push-spec-only
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path


BOARD_SUCCESS_SCHEMA_TOP_LEVEL_KEYS: tuple[str, ...] = (
    "Symbol",
    "SymbolName",
    "Exchange",
    "ExchangeName",
    "CurrentPrice",
    "CurrentPriceTime",
    "CurrentPriceChangeStatus",
    "CurrentPriceStatus",
    "CalcPrice",
    "PreviousClose",
    "PreviousCloseTime",
    "ChangePreviousClose",
    "ChangePreviousClosePer",
    "OpeningPrice",
    "OpeningPriceTime",
    "HighPrice",
    "HighPriceTime",
    "LowPrice",
    "LowPriceTime",
    "TradingVolume",
    "TradingVolumeTime",
    "VWAP",
    "TradingValue",
    "BidQty",
    "BidPrice",
    "BidTime",
    "BidSign",
    "MarketOrderSellQty",
    "Sell1",
    "Sell2",
    "Sell3",
    "Sell4",
    "Sell5",
    "Sell6",
    "Sell7",
    "Sell8",
    "Sell9",
    "Sell10",
    "AskQty",
    "AskPrice",
    "AskTime",
    "AskSign",
    "MarketOrderBuyQty",
    "Buy1",
    "Buy2",
    "Buy3",
    "Buy4",
    "Buy5",
    "Buy6",
    "Buy7",
    "Buy8",
    "Buy9",
    "Buy10",
    "OverSellQty",
    "UnderBuyQty",
    "TotalMarketValue",
    "ClearingPrice",
    "IV",
    "Gamma",
    "Theta",
    "Vega",
    "Delta",
    "SecurityType",
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
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("kabu_native.check_api")
    log.handlers.clear()
    log.setLevel(logging.INFO)
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    log.addHandler(fh)
    log.addHandler(sh)
    log.propagate = False
    return log


def _reports_out_path(native_root: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = native_root / "results" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"api_check_{stamp}.json"


def main() -> int:
    repo_root, native_root = _bootstrap()

    from api.push_client import push_spec
    from api.rest_client import (
        KabuNativeApiError,
        KabuNativeRestClient,
        build_symbol_key,
        default_base_url,
        require_kabu_password,
        summarize_board,
    )

    parser = argparse.ArgumentParser(description="kabu_native API 接続チェック")
    parser.add_argument("--symbol", default="9984", help="銘柄コード（例: 9984）")
    parser.add_argument(
        "--exchange",
        default=os.environ.get("KABU_EXCHANGE", "1"),
        help="市場コード（デフォルト: 1 東証）",
    )
    parser.add_argument("--base-url", default=None, help="REST ベース URL（既定: KABU_API_BASE または localhost）")
    parser.add_argument("--quote-depth", type=int, default=5, help="板要約の段数（1〜10）")
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP タイムアウト秒")
    parser.add_argument("--max-retries", type=int, default=3, help="リトライ回数")
    parser.add_argument(
        "--push-spec-only",
        action="store_true",
        help="REST/PUSH に接続せず PUSH 仕様（WS URL・期待キー）のみ出力",
    )
    parser.add_argument(
        "--skip-board",
        action="store_true",
        help="board 取得をスキップ（--push-spec-only と併用可）",
    )
    args = parser.parse_args()

    base_url = (args.base_url or default_base_url()).rstrip("/")
    day_stamp = datetime.now().strftime("%Y%m%d")
    log_path = repo_root / "logs" / "runtime" / f"kabu_native_check_api_{day_stamp}.log"
    log = _setup_logging(log_path)
    outfile = _reports_out_path(native_root)

    meta: dict[str, object] = {
        "component": "kabu_native.check_api",
        "symbol_arg": args.symbol,
        "exchange": str(args.exchange),
        "base_url": base_url,
        "logged_at_local": datetime.now().isoformat(timespec="seconds"),
        "results_json": str(outfile.relative_to(repo_root)),
        "runtime_log": str(log_path.relative_to(repo_root)),
        "push_spec_only": args.push_spec_only,
        "skip_board": args.skip_board,
    }

    record: dict[str, object] = {"meta": meta, "push_spec": push_spec(base_url)}

    if args.push_spec_only:
        log.info("PUSH spec only (no REST token / board)")
        record["status"] = "push_spec_only"
        outfile.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("JSON 出力: %s", outfile.relative_to(repo_root))
        return 0

    try:
        password = require_kabu_password()
    except KabuNativeApiError as e:
        log.error("%s", e)
        _write_error(outfile, meta, str(e))
        return 2

    client = KabuNativeRestClient(
        base_url=base_url,
        timeout=args.timeout,
        max_retries=args.max_retries,
    )

    symbol_key = build_symbol_key(args.symbol, args.exchange)
    meta["symbol_key"] = symbol_key

    try:
        token = client.issue_token(password)
        log.info("token 取得成功（ログ・JSON には出力しません）")

        if not args.skip_board:
            board = client.get_board(symbol_key, token=token)
            summary = summarize_board(board, quote_depth=min(max(args.quote_depth, 1), 10))
            cp = summary["current_quote"].get("CurrentPrice")
            log.info(
                "板取得成功 CurrentPrice=%r Symbol=%r",
                cp,
                summary["current_quote"].get("Symbol") or summary["current_quote"].get("SymbolName"),
            )
            record.update(
                {
                    "status": "ok",
                    **summary,
                    "_note_response_keys": sorted(board.keys()),
                    "_board_openapi_boardsuccess_top_level_keys": list(BOARD_SUCCESS_SCHEMA_TOP_LEVEL_KEYS),
                    "_compare_schema_vs_response": {
                        "only_in_schema": sorted(set(BOARD_SUCCESS_SCHEMA_TOP_LEVEL_KEYS) - set(board.keys())),
                        "only_in_response": sorted(set(board.keys()) - set(BOARD_SUCCESS_SCHEMA_TOP_LEVEL_KEYS)),
                    },
                }
            )
        else:
            record["status"] = "token_only"
            log.info("board 取得スキップ（--skip-board）")

        outfile.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("JSON 出力: %s", outfile.relative_to(repo_root))
        return 0

    except KabuNativeApiError as e:
        log.error("kabustation API エラー: %s", e)
        _write_error(outfile, meta, str(e))
        return 1


def _write_error(outfile: Path, meta: dict[str, object], error: str) -> None:
    err_path = outfile.with_name(outfile.stem + ".error.json")
    payload = {"meta": meta, "status": "error", "error": error}
    err_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
