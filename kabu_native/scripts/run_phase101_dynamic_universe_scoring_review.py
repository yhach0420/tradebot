#!/usr/bin/env python3
"""
Phase 101: Review dynamic23 scoring — board errors, top-50, adopted vs rejected, growth ratio, focus ranks.
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
    return repo_root, native_root, native_root / "src"


def _bootstrap() -> tuple[Path, Path]:
    repo_root, native_root, src_root = _paths()
    src_s = str(src_root)
    if src_s not in sys.path:
        sys.path.insert(0, src_s)
    from api.rest_client import load_kabu_env

    load_kabu_env(repo_root=repo_root)
    return repo_root, native_root


def _append_phase98_rankings(csv_path: Path, p98: dict | None) -> None:
    if not p98 or not p98.get("available"):
        return
    import csv

    extra_fields = list(csv_path.read_text(encoding="utf-8").splitlines()[0].split(",")) + [
        "data_source",
        "pool_rank",
    ]
    existing = []
    if csv_path.is_file():
        with csv_path.open(encoding="utf-8", newline="") as f:
            existing = list(csv.DictReader(f))
    rows_out = [{**r, "data_source": "phase101_board_batch", "pool_rank": ""} for r in existing]
    for r in p98.get("dynamic_score_top23_from_phase98_build") or []:
        rows_out.append(
            {
                "rank": r.get("pool_rank", ""),
                "symbol": r.get("symbol", ""),
                "symbol_key": "",
                "market": r.get("market", ""),
                "dynamic_score": r.get("dynamic_score", ""),
                "trading_value_proxy": r.get("trading_value_proxy", ""),
                "change_previous_close_pct": r.get("change_previous_close_pct", ""),
                "current_price": r.get("current_price", ""),
                "spread_proxy": r.get("spread_proxy", ""),
                "board_liquidity_proxy": "",
                "passed_filter": True,
                "selected_dynamic23": r.get("selected_dynamic23", ""),
                "in_static_27": False,
                "candidate_index": "",
                "in_board_fetch_window": "",
                "reject_reasons": "",
                "board_error_class": "",
                "board_error_message": "",
                "data_source": "phase98_build_output",
                "pool_rank": r.get("pool_rank", ""),
            }
        )
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=extra_fields, extrasaction="ignore")
        w.writeheader()
        for row in rows_out:
            w.writerow(row)


def main() -> int:
    repo_root, native_root = _bootstrap()
    from universe.dynamic_build import load_dynamic_config
    from universe.dynamic_scoring_review import run_dynamic_scoring_review, write_rankings_csv

    parser = argparse.ArgumentParser(description="Phase 101 dynamic universe scoring review")
    parser.add_argument(
        "--config",
        type=Path,
        default=native_root / "configs" / "universe_dynamic_trial.yaml",
    )
    parser.add_argument("--date-stamp", default=None)
    parser.add_argument(
        "--reports-dir",
        type=Path,
        default=native_root / "results" / "reports",
    )
    parser.add_argument(
        "--phase98-json",
        type=Path,
        default=None,
        help="Optional phase98_dynamic_universe_build_YYYYMMDD.json for cross-check",
    )
    parser.add_argument(
        "--skip-kabu",
        action="store_true",
        help="Structural analysis only (no /board); verdict will be needs_revision",
    )
    args = parser.parse_args()

    cfg_path = args.config if args.config.is_absolute() else (repo_root / args.config)
    cfg = load_dynamic_config(cfg_path)
    day_stamp = args.date_stamp or datetime.now(JST).strftime("%Y%m%d")
    reports_dir = args.reports_dir if args.reports_dir.is_absolute() else (repo_root / args.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)

    phase98 = args.phase98_json
    if phase98 is None:
        candidate = reports_dir / f"phase98_dynamic_universe_build_{day_stamp}.json"
        if candidate.is_file():
            phase98 = candidate
    if phase98 is not None and not phase98.is_absolute():
        phase98 = repo_root / phase98

    log = logging.getLogger("phase101")
    log.setLevel(logging.INFO)
    if not log.handlers:
        log.addHandler(logging.StreamHandler(sys.stderr))

    payload = run_dynamic_scoring_review(
        repo_root=repo_root,
        cfg=cfg,
        day_stamp=day_stamp,
        skip_kabu=args.skip_kabu,
        phase98_json=phase98,
        log=log,
    )

    ranking_rows = payload.pop("_ranking_rows", [])
    json_path = reports_dir / f"phase101_dynamic_universe_scoring_review_{day_stamp}.json"
    csv_path = reports_dir / f"phase101_dynamic_universe_rankings_{day_stamp}.csv"
    write_rankings_csv(csv_path, ranking_rows)
    _append_phase98_rankings(csv_path, payload.get("phase98_trial_universe_analysis"))

    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "verdict": payload.get("verdict"),
                "board_fetch_error_count": payload.get("board_fetch_error_count")
                or payload.get("board_error_taxonomy", {}).get("board_fetch_error_count"),
                "json": str(json_path.relative_to(repo_root)),
                "csv": str(csv_path.relative_to(repo_root)),
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
