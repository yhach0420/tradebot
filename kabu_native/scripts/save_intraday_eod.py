#!/usr/bin/env python3
"""
Phase 42: EOD save — build kabu_native/data/intraday_1m from PUSH JSONL (primary) or REST snapshot.

例::
    python kabu_native/scripts/save_intraday_eod.py \\
        --universe kabu_native/data/universe/universe_intraday_full.csv \\
        --trade-date 2026-05-15
    python kabu_native/scripts/save_intraday_eod.py --trade-date 2026-05-15 --source push
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime
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


def main() -> int:
    repo_root, native_root = _bootstrap()

    from api.rest_client import KabuNativeRestClient, default_base_url, load_kabu_env
    from research.oos_data_availability import build_data_availability_for_oos
    from storage.intraday_recorder import (
        IntradayRecorder,
        build_from_push_day,
        build_snapshot_bar_from_board,
    )
    from storage.push_recorder import PushRecorder
    from storage.symbol_sources import load_symbols

    parser = argparse.ArgumentParser(description="Save kabu_native intraday 1m CSV (EOD)")
    parser.add_argument("--trade-date", default=None, help="YYYY-MM-DD (default: today JST)")
    parser.add_argument("--universe", type=Path, default=None)
    parser.add_argument("--morning-screen", type=Path, default=None)
    parser.add_argument("--watchlist", type=Path, default=None)
    parser.add_argument("--symbols", default=None, help="Comma-separated codes")
    parser.add_argument(
        "--source",
        choices=("auto", "push", "rest"),
        default="auto",
        help="auto=push if JSONL exists else skip (rest only with --allow-rest-snapshot)",
    )
    parser.add_argument(
        "--allow-rest-snapshot",
        action="store_true",
        help="If no PUSH JSONL, write single-minute REST board snapshot (not full session)",
    )
    parser.add_argument(
        "--no-update-oos-availability",
        action="store_true",
        help="Skip writing phase41_data_oos/data_availability_for_oos.json",
    )
    args = parser.parse_args()

    trade_date = args.trade_date or datetime.now(JST).date().isoformat()
    log = logging.getLogger("save_intraday_eod")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    sym_list = load_symbols(
        universe=args.universe,
        morning_screen=args.morning_screen,
        watchlist=args.watchlist,
        symbols=args.symbols.split(",") if args.symbols else None,
        native_root=native_root,
    )
    recorder = IntradayRecorder(native_root)
    push_rec = PushRecorder(native_root, trade_date)

    client: KabuNativeRestClient | None = None
    token: str | None = None
    if args.source == "rest" or args.allow_rest_snapshot:
        load_kabu_env(repo_root=repo_root)
        client = KabuNativeRestClient(default_base_url())
        token = client.issue_token_from_env()

    saved = 0
    skipped = 0
    snapshot_only = 0
    for spec in sym_list:
        push_path = push_rec.path_for_symbol(spec.symbol)
        use_push = args.source in ("auto", "push") and push_path.is_file()
        if use_push:
            path, validation = build_from_push_day(
                recorder,
                trade_date=trade_date,
                symbol=spec.symbol,
                push_jsonl_path=push_path,
            )
            if path:
                saved += 1
                log.info("%s push->csv rows=%s valid=%s", spec.symbol, validation.row_count, validation.ok)
            else:
                skipped += 1
                log.warning("%s push jsonl empty: %s", spec.symbol, validation.issues)
            continue

        if args.source == "push":
            skipped += 1
            continue

        if client and token and args.allow_rest_snapshot:
            board = client.get_board(spec.symbol_key, token=token)
            bar = build_snapshot_bar_from_board(board)
            if bar:
                recorder.write_bars(trade_date, spec.symbol, [bar], merge_existing=True)
                saved += 1
                snapshot_only += 1
                log.info("%s rest snapshot bar written", spec.symbol)
                continue
        skipped += 1

    summary = recorder.summarize_day(trade_date, [s.symbol for s in sym_list])
    out_summary = native_root / "results" / "storage" / f"intraday_eod_{trade_date.replace('-', '')}.json"
    out_summary.parent.mkdir(parents=True, exist_ok=True)
    out_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    log.info(
        "EOD done date=%s saved=%s skipped=%s snapshot_only=%s",
        trade_date,
        saved,
        skipped,
        snapshot_only,
    )

    if not args.no_update_oos_availability:
        avail = build_data_availability_for_oos(
            data_roots=[recorder.intraday_root],
            push_jsonl_paths=[native_root / "data" / "push_jsonl"],
        )
        oos_path = (
            native_root
            / "results"
            / "research"
            / "logic_lab"
            / "phase41_data_oos"
            / "data_availability_for_oos.json"
        )
        oos_path.parent.mkdir(parents=True, exist_ok=True)
        oos_path.write_text(json.dumps(avail, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("Updated %s", oos_path)

    print(f"trade_date={trade_date} saved={saved} skipped={skipped} snapshot_only={snapshot_only}")
    print(f"summary: {out_summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
