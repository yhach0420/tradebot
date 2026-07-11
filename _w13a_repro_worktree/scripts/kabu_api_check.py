#!/usr/bin/env python3
"""
kabuステーション® API への接続チェック。

前提: Kabuステーションが起動し、API が有効。.env に KABU_API_PASSWORD を設定。

例::
    python scripts/kabu_api_check.py --symbol 9984
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path


# OpenAPI `BoardSuccess` トップレベル（対応表 docs/kabu_response_mapping.md と同期）
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


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _ensure_env_loaded() -> None:
    root = _project_root()
    try:
        from dotenv import load_dotenv

        load_dotenv(dotenv_path=root / ".env", override=False)
    except ImportError:
        pass


def _setup_logging(log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("kabu_api_check")
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


def main() -> int:
    _ensure_env_loaded()
    root = _project_root()
    sys.path.insert(0, str(root))

    from src.kabu_api_client import KabuApiClient, KabuApiError, build_symbol_key, summarize_board

    parser = argparse.ArgumentParser(description="kabuステーション API 接続・板確認")
    parser.add_argument("--symbol", required=True, help="銘柄コード（例: 9984）")
    parser.add_argument("--exchange", default=os.environ.get("KABU_EXCHANGE", "1"), help="市場コード（デフォルト: 1 東証）")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("KABU_API_BASE", "http://localhost:18080/kabusapi"),
        help="kabustation API のベースURL",
    )
    parser.add_argument("--quote-depth", type=int, default=5, help="板の要約で何段まで残すか（1〜10）")
    args = parser.parse_args()

    day_stamp = datetime.now().strftime("%Y%m%d")
    time_stamp = datetime.now().strftime("%H%M%S")
    log_path = root / "logs" / "runtime" / f"kabu_api_check_{day_stamp}.log"
    log = _setup_logging(log_path)

    out_dir = root / "results" / "kabu_api" / day_stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    outfile = out_dir / f"kabu_api_check_{args.symbol}_{args.exchange}_{time_stamp}.json"

    password = os.environ.get("KABU_API_PASSWORD", "").strip()
    if not password:
        log.error("KABU_API_PASSWORD が .env に未設定です。（プロジェクト直下の .env を使用）")
        return 2

    symbol_key = build_symbol_key(args.symbol, args.exchange)
    meta = {
        "symbol_arg": args.symbol,
        "exchange": args.exchange,
        "symbol_key": symbol_key,
        "base_url": args.base_url.rstrip("/"),
        "logged_at_local": datetime.now().isoformat(timespec="seconds"),
        "results_log": str(outfile.relative_to(root)),
        "runtime_log": str(log_path.relative_to(root)),
    }

    client = KabuApiClient(base_url=args.base_url)

    try:
        token = client.issue_token(password)
        log.info("token 取得成功")
        board = client.get_board(symbol_key, token=token)
        summary = summarize_board(board, quote_depth=min(max(args.quote_depth, 1), 10))

        cp = summary["current_quote"].get("CurrentPrice")
        log.info(
            "板取得成功 CurrentPrice=%r Symbol=%r",
            cp,
            summary["current_quote"].get("Symbol") or summary["current_quote"].get("SymbolName"),
        )

        excerpt_keys = sorted(summary["board_excerpt"])
        log.info("板要約キー (%d): %s", len(excerpt_keys), excerpt_keys[:12])

        record = {
            "meta": meta,
            **summary,
            "_note_response_keys": sorted(board.keys()),
            "_board_openapi_boardsuccess_top_level_keys": list(BOARD_SUCCESS_SCHEMA_TOP_LEVEL_KEYS),
            "_compare_schema_vs_response": {
                "only_in_schema": sorted(set(BOARD_SUCCESS_SCHEMA_TOP_LEVEL_KEYS) - set(board.keys())),
                "only_in_response": sorted(set(board.keys()) - set(BOARD_SUCCESS_SCHEMA_TOP_LEVEL_KEYS)),
            },
        }

        outfile.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("JSON 出力: %s", outfile)
        return 0

    except KabuApiError as e:
        log.error("kabustation API エラー: %s", e)
        err_file = outfile.with_suffix(".error.json")
        err_file.write_text(
            json.dumps({"meta": meta, "error": str(e)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log.error("エラー内容を書き込みました: %s", err_file)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
