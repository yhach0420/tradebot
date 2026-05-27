#!/usr/bin/env python3
"""
kabu_native shadow — live kabu_signal_v1 / kabu_exit_v1 evaluation (no orders).

Optional [KABU_PAPER] Discord virtual ENTRY/EXIT via KABU_SHADOW_DISCORD_WEBHOOK_URL (default OFF).

例::
    python kabu_native/scripts/run_shadow.py --max-polls 2
    python kabu_native/scripts/run_shadow.py --watchlist-source universe --top-n 5
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


def _paths() -> tuple[Path, Path, Path]:
    script = Path(__file__).resolve()
    native_root = script.parents[1]
    repo_root = script.parents[2]
    return repo_root, native_root, native_root / "src"


def _bootstrap() -> tuple[Path, Path]:
    repo_root, native_root, src_root = _paths()
    for p in (src_root, repo_root):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    from api.rest_client import load_kabu_env

    load_kabu_env(repo_root=repo_root)
    return repo_root, native_root


def main() -> int:
    repo_root, native_root = _bootstrap()

    from shadow.config import load_shadow_config
    from shadow.runner import ShadowRunner
    from shadow.watchlist import build_watchlist

    parser = argparse.ArgumentParser(description="kabu_native shadow runner")
    parser.add_argument(
        "--config",
        type=Path,
        default=native_root / "configs" / "shadow.yaml",
    )
    parser.add_argument("--watchlist-source", choices=("morning_screen", "universe"), default=None)
    parser.add_argument("--watchlist-path", type=Path, default=None)
    parser.add_argument(
        "--universe-csv",
        type=Path,
        default=None,
        help="Phase105/106 trial CSV (sets watchlist-source=universe)",
    )
    parser.add_argument("--top-n", type=int, default=None)
    parser.add_argument("--max-polls", type=int, default=None, help="終了までのポール回数（未指定=無限）")
    parser.add_argument("--poll-interval-sec", type=float, default=None)
    parser.add_argument("--use-push", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="watchlist のみ表示して終了")
    args = parser.parse_args()

    cfg_path = args.config if args.config.is_absolute() else (repo_root / args.config)
    config = load_shadow_config(cfg_path)

    if args.universe_csv is not None:
        config.watchlist.source = "universe"
        up = args.universe_csv if args.universe_csv.is_absolute() else (repo_root / args.universe_csv)
        config.watchlist.universe_path = str(up.relative_to(repo_root)) if up.is_relative_to(repo_root) else str(up)
    if args.watchlist_source:
        config.watchlist.source = args.watchlist_source
    if args.top_n is not None:
        config.watchlist.top_n = args.top_n
    if args.max_polls is not None:
        config.runtime.max_polls = args.max_polls
    if args.poll_interval_sec is not None:
        config.runtime.poll_interval_sec = args.poll_interval_sec
    if args.use_push:
        config.runtime.use_push = True

    wl_path = args.watchlist_path
    if wl_path is not None and not wl_path.is_absolute():
        wl_path = (repo_root / wl_path).resolve()

    watchlist = build_watchlist(
        source=config.watchlist.source,
        native_root=native_root,
        repo_root=repo_root,
        path=wl_path,
        universe_path=config.watchlist.universe_path,
        top_n=config.watchlist.top_n,
        passed_only=config.watchlist.passed_only,
    )
    if not watchlist:
        print("watchlist is empty", file=sys.stderr)
        return 2

    log_path = repo_root / "logs" / "runtime" / f"kabu_native_shadow_{__import__('datetime').datetime.now().strftime('%Y%m%d')}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("kabu_native.shadow")
    log.handlers.clear()
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    for h in (logging.FileHandler(log_path, encoding="utf-8"), logging.StreamHandler(sys.stderr)):
        h.setFormatter(fmt)
        log.addHandler(h)

    log.info("rules: session_control=%s bf_confirm=%d", config.rules.market_session_control, config.rules.bf_confirm_count)
    log.info("watchlist (%d): %s", len(watchlist), [w.symbol for w in watchlist])

    if args.dry_run:
        return 0

    runner = ShadowRunner(
        repo_root=repo_root,
        native_root=native_root,
        config=config,
        watchlist=watchlist,
    )
    try:
        runner.run_loop()
    except KeyboardInterrupt:
        log.info("stopped by user")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
