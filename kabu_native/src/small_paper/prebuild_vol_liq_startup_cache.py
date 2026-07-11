"""
Phase687W1: Prebuild vol_liq startup cache for a target session key.

Usage:
  python -m small_paper.prebuild_vol_liq_startup_cache --date 20260711
  python -m small_paper.prebuild_vol_liq_startup_cache --date 20260711 --session AM
  python -m small_paper.prebuild_vol_liq_startup_cache --run-session-key 20260711/live_session_084500
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


def _repo_root() -> Path:
    # kabu_native/src/small_paper/thisfile → parents[3] = tradebotfile
    return Path(__file__).resolve().parents[3]


def _default_config_path(repo: Path) -> Path:
    return (
        repo
        / "kabu_native"
        / "configs"
        / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
    )


def _guess_session_stamp(*, session: str) -> str:
    # Typical AM/PM start stamps used by daily runner (approximate; key only needs
    # lexicographic position after prior sessions for prior_only lookback).
    return "084500" if session.upper() == "AM" else "122500"


def build_run_session_key(*, date: str, session: str) -> str:
    day = date.replace("-", "")
    if len(day) != 8 or not day.isdigit():
        raise ValueError(f"invalid date: {date}")
    stamp = _guess_session_stamp(session=session)
    return f"{day}/live_session_{stamp}"


def prebuild_vol_liq_startup_cache(
    *,
    run_session_key: str,
    config_path: Optional[Path] = None,
    repo_root: Optional[Path] = None,
) -> dict[str, Any]:
    from small_paper.config import load_pilot_config
    from small_paper.vol_liq_session_key import normalize_vol_liq_run_session_key
    from small_paper.vol_liq_startup_cache import (
        build_vol_liq_threshold_with_startup_cache,
        get_vol_liq_cache_metrics,
    )

    repo = repo_root or _repo_root()
    cfg_path = config_path or _default_config_path(repo)
    cfg = load_pilot_config(cfg_path)
    key = normalize_vol_liq_run_session_key(run_session_key)
    state = build_vol_liq_threshold_with_startup_cache(
        cfg, repo_root=repo, run_session_key=key
    )
    metrics = get_vol_liq_cache_metrics(key)
    return {
        "run_session_key": key,
        "vol_liq_threshold": None if state is None else state.vol_liq_threshold,
        "prior_quality_trade_count": None if state is None else state.prior_quality_trade_count,
        "metrics": None if metrics is None else metrics.summary_fields(),
        "built_at": datetime.now(JST).isoformat(timespec="seconds"),
        "config_path": str(cfg_path),
    }


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Prebuild vol_liq startup cache")
    p.add_argument("--date", help="YYYYMMDD or YYYY-MM-DD")
    p.add_argument("--session", choices=("AM", "PM", "am", "pm"), default="AM")
    p.add_argument("--run-session-key", help="Explicit key YYYYMMDD/live_session_HHMMSS")
    p.add_argument("--config", type=Path, default=None)
    p.add_argument("--repo-root", type=Path, default=None)
    args = p.parse_args(argv)

    if args.run_session_key:
        key = args.run_session_key
    elif args.date:
        key = build_run_session_key(date=args.date, session=args.session)
    else:
        p.error("provide --date or --run-session-key")
        return 2

    out = prebuild_vol_liq_startup_cache(
        run_session_key=key,
        config_path=args.config,
        repo_root=args.repo_root,
    )
    print(json.dumps(out, ensure_ascii=False, indent=2))
    m = out.get("metrics") or {}
    if m.get("vol_liq_cache_hit") or m.get("vol_liq_cache_status") in (
        "cache_hit",
        "baseline_fallback",
    ):
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
