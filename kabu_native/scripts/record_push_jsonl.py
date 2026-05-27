#!/usr/bin/env python3
"""
Phase 42: Record kabu PUSH to kabu_native/data/push_jsonl/YYYY-MM-DD/{symbol}.jsonl.

例::
    python kabu_native/scripts/record_push_jsonl.py \\
        --universe kabu_native/data/universe/universe_intraday_full.csv \\
        --duration-sec 3600
    python kabu_native/scripts/record_push_jsonl.py --dry-run --max-messages 30 --symbols 9984
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


def _bootstrap() -> tuple[Path, Path]:
    script = Path(__file__).resolve()
    native_root = script.parents[1]
    repo_root = script.parents[2]
    for p in (native_root / "src", repo_root):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    return repo_root, native_root


def _dry_run_messages(native_root: Path, symbols: list, count: int) -> None:
    from storage.push_recorder import PushRecorder
    trade_date = datetime.now(JST).date().isoformat()
    rec = PushRecorder(native_root, trade_date)
    base_price = 1000.0
    for i in range(count):
        sym = symbols[i % len(symbols)]
        payload = {
            "Symbol": sym.replace(".T", ""),
            "CurrentPrice": base_price + i * 0.5,
            "CurrentPriceTime": datetime.now(JST).isoformat(),
            "TradingVolume": float(1000 + i * 100),
            "HighPrice": base_price + i,
            "LowPrice": base_price - 1,
            "OpeningPrice": base_price,
        }
        rec.append(sym, payload, source="dry_run")


def main() -> int:
    repo_root, native_root = _bootstrap()

    from api.push_client import KabuNativePushClient
    from api.rest_client import KabuNativeRestClient, default_base_url, load_kabu_env
    from storage.push_recorder import PushRecorder
    from storage.symbol_sources import load_symbols

    parser = argparse.ArgumentParser(description="Record kabu PUSH JSONL")
    parser.add_argument("--trade-date", default=None)
    parser.add_argument("--universe", type=Path, default=None)
    parser.add_argument("--morning-screen", type=Path, default=None)
    parser.add_argument("--symbols", default=None)
    parser.add_argument("--duration-sec", type=float, default=None)
    parser.add_argument("--max-messages", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true", help="Write synthetic JSONL without API")
    args = parser.parse_args()

    trade_date = args.trade_date or datetime.now(JST).date().isoformat()
    sym_list = load_symbols(
        universe=args.universe,
        morning_screen=args.morning_screen,
        symbols=args.symbols.split(",") if args.symbols else None,
        native_root=native_root,
    )
    symbols = [s.symbol for s in sym_list]

    log = logging.getLogger("record_push_jsonl")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.dry_run:
        n = args.max_messages or 20
        _dry_run_messages(native_root, symbols, n)
        rec = PushRecorder(native_root, trade_date)
        print(f"dry-run wrote ~{n} lines/symbol cycle to {rec.day_dir}")
        return 0

    load_kabu_env(repo_root=repo_root)
    rest = KabuNativeRestClient(default_base_url())
    token = rest.issue_token_from_env()
    push_client = KabuNativePushClient(rest, token)
    specs = [(s.code, s.exchange) for s in sym_list]
    from api.kabu_register import register_symbols_cleared

    register_symbols_cleared(push_client, specs)
    log.info("Registered %s symbols for PUSH", len(specs))

    recorder = PushRecorder(native_root, trade_date)
    code_to_symbol = {s.code: s.symbol for s in sym_list}
    started = time.monotonic()
    count = 0

    try:
        for msg in push_client.iter_messages_sync(
            recv_poll_sec=30.0,
            max_messages=args.max_messages,
        ):
            sym_code = str(msg.get("Symbol") or "")
            sym = code_to_symbol.get(sym_code) or f"{sym_code}.T"
            recorder.append(sym, msg, source="push")
            count += 1
            if args.duration_sec and (time.monotonic() - started) >= args.duration_sec:
                break
    finally:
        try:
            push_client.unregister_all()
        except Exception as e:
            log.warning("unregister_all: %s", e)

    summary = recorder.summarize(symbols)
    log.info("Recorded %s messages", count)
    import json

    print(f"push_jsonl day_dir={recorder.day_dir} messages={count}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
