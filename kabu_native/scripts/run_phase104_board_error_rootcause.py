#!/usr/bin/env python3
"""
Phase 104: Classify kabu /board HTTP 400 root causes from live probe (no PF / universe eval).
"""

from __future__ import annotations

import argparse
import json
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
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))
    from api.rest_client import load_kabu_env

    load_kabu_env(repo_root=repo_root)
    return repo_root, native_root


def main() -> int:
    repo_root, native_root = _bootstrap()
    from api.rest_client import KabuNativeApiError, KabuNativeRestClient, default_base_url, require_kabu_password
    from universe.board_error_probe import (
        determine_phase104_verdict,
        load_candidate_rows,
        run_board_error_probe,
        summarize_probe_results,
        write_error_examples_csv,
    )
    from universe.dynamic_build import (
        load_dynamic_config,
        load_static_universe,
        resolve_symbol_master,
        select_board_candidates,
        write_board_fetch_candidates_csv,
    )

    parser = argparse.ArgumentParser(description="Phase104 board /board error root-cause probe")
    parser.add_argument(
        "--config",
        type=Path,
        default=native_root / "configs" / "universe_dynamic_trial.yaml",
    )
    parser.add_argument("--date-stamp", default=None)
    parser.add_argument(
        "--candidates-csv",
        type=Path,
        default=None,
        help="phase103_board_fetch_candidates CSV (default: auto from date-stamp)",
    )
    parser.add_argument(
        "--reports-dir",
        type=Path,
        default=native_root / "results" / "reports",
    )
    parser.add_argument("--max-probe", type=int, default=None, help="Limit probes (default: all candidates)")
    parser.add_argument("--delay-sec", type=float, default=0.25)
    args = parser.parse_args()

    day_stamp = args.date_stamp or datetime.now(JST).strftime("%Y%m%d")
    reports_dir = args.reports_dir if args.reports_dir.is_absolute() else (repo_root / args.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)

    candidates_path = args.candidates_csv
    if candidates_path is None:
        candidates_path = reports_dir / f"phase103_board_fetch_candidates_{day_stamp}.csv"
    elif not candidates_path.is_absolute():
        candidates_path = repo_root / candidates_path

    if not candidates_path.is_file():
        cfg_path = args.config if args.config.is_absolute() else (repo_root / args.config)
        cfg = load_dynamic_config(cfg_path)
        master_path, master_entries = resolve_symbol_master(repo_root, cfg.symbol_master_paths)
        static_path = repo_root / cfg.static_universe_path
        if not static_path.is_file():
            static_path = repo_root / "kabu_native" / "data" / "universe" / "universe_intraday_full.csv"
        static_rows = load_static_universe(static_path, static_max=cfg.static_max)
        static_codes = {r["symbol"].replace(".T", "").upper() for r in static_rows}
        picks = select_board_candidates(master_entries, static_codes, cfg=cfg, day_stamp=day_stamp)
        write_board_fetch_candidates_csv(candidates_path, picks)
        print(f"generated candidates: {candidates_path}", file=sys.stderr)

    candidates = load_candidate_rows(candidates_path)

    try:
        password = require_kabu_password()
    except KabuNativeApiError as e:
        print(json.dumps({"verdict": "probe_failed", "error": str(e)}, ensure_ascii=False))
        return 1

    client = KabuNativeRestClient(base_url=default_base_url())
    token = client.issue_token(password)

    results = run_board_error_probe(
        candidates,
        token=token,
        base_url=client.base_url,
        delay_sec=args.delay_sec,
        max_probe=args.max_probe,
    )
    summary = summarize_probe_results(results)
    verdict, notes = determine_phase104_verdict(summary)

    examples_path = reports_dir / f"phase104_board_error_examples_{day_stamp}.csv"
    json_path = reports_dir / f"phase104_board_error_rootcause_{day_stamp}.json"
    write_error_examples_csv(examples_path, results, limit=50)

    first_errors = [
        {
            "symbol": r.symbol,
            "exchange": r.exchange,
            "market": r.market,
            "request_payload": r.request_payload,
            "response_body": r.response_body,
            "http_status": r.http_status,
            "kabu_api_code": r.kabu_api_code,
            "kabu_api_message": r.kabu_api_message,
            "root_cause": r.root_cause,
        }
        for r in results if not r.ok
    ][:50]

    payload = {
        "phase": 104,
        "day_stamp": day_stamp,
        "verdict": verdict,
        "verdict_notes": notes,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "candidates_csv": str(candidates_path.relative_to(repo_root)),
        "probe_delay_sec": args.delay_sec,
        "summary": summary,
        "first_50_errors": first_errors,
        "phase104_board_error_examples_csv": str(examples_path.relative_to(repo_root)),
        "constraints": [
            "no_pf_evaluation",
            "no_universe_evaluation",
            "diagnosis_only",
        ],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "verdict": verdict,
                "success": summary.get("board_fetch_success_count"),
                "errors": summary.get("board_fetch_error_count"),
                "root_cause_counts": summary.get("root_cause_counts"),
                "kabu_api_code_counts": summary.get("kabu_api_code_counts"),
                "json": str(json_path.relative_to(repo_root)),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
