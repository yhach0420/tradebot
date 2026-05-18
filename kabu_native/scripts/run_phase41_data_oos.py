#!/usr/bin/env python3
"""
Phase 41: Data accumulation / latest OOS window fix + Phase40 re-validation.

例::
    python kabu_native/scripts/run_phase41_data_oos.py \\
        --reference-run-dir kabu_native/results/research/logic_lab/20260517/run_225513 \\
        --run-latest-replay \\
        --revalidate-phase40
"""

from __future__ import annotations

import argparse
import json
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
    return repo_root, native_root


def _data_roots(repo_root: Path, native_root: Path) -> list[Path]:
    return [
        (repo_root / "data" / "intraday_1m").resolve(),
        (native_root / "data" / "intraday_1m").resolve(),
    ]


def _push_paths(repo_root: Path, native_root: Path) -> list[Path]:
    return [
        (repo_root / "data" / "push_jsonl").resolve(),
        (native_root / "data" / "push_jsonl").resolve(),
    ]


def _universe_symbols(repo_root: Path, universe: Path | None, ref: Path) -> tuple[list[str], int]:
    symbols: list[str] = []
    if universe:
        import csv

        up = universe if universe.is_absolute() else (repo_root / universe)
        seen: set[str] = set()
        with up.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                p = str(row.get("passed", "")).strip().lower()
                if p not in ("true", "1", "yes"):
                    continue
                sym = str(row.get("symbol", "")).strip()
                if sym and sym not in seen:
                    seen.add(sym)
                    symbols.append(sym)
    if not symbols:
        ps = ref / "profile_summary.json"
        if ps.is_file():
            psj = json.loads(ps.read_text(encoding="utf-8"))
            symbols = list(psj.get("symbols") or [])
    return symbols, len(symbols) if symbols else 27


def main() -> int:
    repo_root, native_root = _bootstrap()

    from research.oos_data_availability import (
        build_data_availability_for_oos,
        build_latest_oos_window_report,
        run_valid_oos_replays,
    )
    from research.top_quartile_oos_validation import run_phase40_top_quartile_oos_validation

    parser = argparse.ArgumentParser(description="Phase41 data OOS fix + optional replay")
    parser.add_argument("--reference-run-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--universe", type=Path, default=None)
    parser.add_argument(
        "--config",
        type=Path,
        default=native_root / "configs" / "small_paper_top_quartile.yaml",
    )
    parser.add_argument(
        "--run-latest-replay",
        action="store_true",
        help="Replay oos_latest (and other valid windows with --replay-all-valid)",
    )
    parser.add_argument(
        "--replay-all-valid",
        action="store_true",
        help="Replay all valid_window specs (skip no_data)",
    )
    parser.add_argument(
        "--reuse-run",
        action="append",
        default=[],
        help="window_id=path to reuse existing replay (e.g. oos_april=.../oos_april)",
    )
    parser.add_argument("--revalidate-phase40", action="store_true")
    parser.add_argument("--latest-days", type=int, default=10)
    parser.add_argument("--tier", default="B")
    args = parser.parse_args()

    ref = (
        args.reference_run_dir
        if args.reference_run_dir.is_absolute()
        else (repo_root / args.reference_run_dir)
    )
    if not ref.is_dir():
        print(f"reference-run-dir not found: {ref}", file=sys.stderr)
        return 2

    out = args.output_dir or (
        repo_root / "kabu_native" / "results" / "research" / "logic_lab" / "phase41_data_oos"
    )
    if not out.is_absolute():
        out = repo_root / out
    out.mkdir(parents=True, exist_ok=True)

    data_roots = _data_roots(repo_root, native_root)
    push_paths = _push_paths(repo_root, native_root)

    log = logging.getLogger("run_phase41")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    availability = build_data_availability_for_oos(
        data_roots=data_roots,
        push_jsonl_paths=push_paths,
    )
    avail_path = out / "data_availability_for_oos.json"
    avail_path.write_text(
        json.dumps(availability, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info(
        "Data inventory: %s days, latest=%s",
        availability.get("merged_trading_day_count"),
        availability.get("latest_trading_date"),
    )

    reuse_runs: list[dict[str, str]] = []
    for item in args.reuse_run:
        if "=" not in item:
            continue
        wid, path = item.split("=", 1)
        p = Path(path)
        if not p.is_absolute():
            p = repo_root / p
        reuse_runs.append({"window_id": wid, "run_dir": str(p.resolve())})

    replay_results: list[dict] = list(reuse_runs)
    if args.run_latest_replay or args.replay_all_valid:
        symbols, _ = _universe_symbols(
            repo_root,
            args.universe,
            ref,
        )
        if not symbols:
            print("symbols required for replay", file=sys.stderr)
            return 2
        only = None if args.replay_all_valid else ["oos_latest"]
        base = repo_root / "kabu_native" / "results" / "research" / "logic_lab" / "phase41_oos"
        log.info("Running OOS replays (only=%s)...", only)
        replay_results = reuse_runs + run_valid_oos_replays(
            symbols=symbols,
            data_roots=data_roots,
            repo_root=repo_root,
            output_base=base,
            tier=args.tier,
            only_window_ids=only,
        )

    latest_report = build_latest_oos_window_report(
        data_roots=data_roots,
        window_runs=replay_results,
        latest_days=args.latest_days,
    )
    latest_path = out / "latest_oos_window.json"
    latest_path.write_text(
        json.dumps(latest_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("Wrote %s", latest_path)

    valid = [w for w in latest_report.get("windows", []) if w.get("status") == "valid_window"]
    nodata = [w for w in latest_report.get("windows", []) if w.get("status") == "no_data"]
    print(f"Windows valid={len(valid)} no_data={len(nodata)}")
    for w in nodata:
        print(f"  no_data {w.get('window_id')}: {w.get('reason')}")
    for w in valid:
        print(
            f"  valid {w.get('window_id')}: {w.get('start')}..{w.get('end')} "
            f"days={w.get('trading_day_count')} run={w.get('run_dir')}"
        )

    if args.revalidate_phase40:
        cfg = args.config if args.config.is_absolute() else (repo_root / args.config)
        _, universe_n = _universe_symbols(repo_root, args.universe, ref)
        p40_out = out / "phase40_top_quartile_oos"
        window_runs = [
            {"window_id": r["window_id"], "run_dir": r["run_dir"]}
            for r in replay_results
            if r.get("run_dir")
        ]
        run_phase40_top_quartile_oos_validation(
            reference_run_dir=ref.resolve(),
            output_dir=p40_out.resolve(),
            config_path=cfg.resolve(),
            universe_symbol_count=universe_n,
            latest_oos_json=latest_path.resolve(),
            window_runs=window_runs,
        )
        report = json.loads(
            (p40_out / "top_quartile_oos_validation.json").read_text(encoding="utf-8")
        )
        cand = report.get("candidate_evaluation") or {}
        comb = (report.get("combined_is_oos") or {}).get("gate_accepted") or {}
        log.info(
            "Phase40 revalidation candidate=%s trades=%s pf=%s",
            cand.get("move_to_small_paper_candidate"),
            comb.get("trade_count"),
            comb.get("profit_factor"),
        )
        print(f"Phase40 output: {p40_out}")
        print(f"  move_to_small_paper_candidate: {cand.get('move_to_small_paper_candidate')}")
        print(f"  combined deduped trades: {comb.get('trade_count')} pf={comb.get('profit_factor')}")

    print(f"Phase41 output: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
