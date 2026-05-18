#!/usr/bin/env python3
"""
Phase 40: Top-quartile exposure gate — OOS / extended validation.

例::
    python kabu_native/scripts/run_phase40_top_quartile_oos.py \\
        --reference-run-dir kabu_native/results/research/logic_lab/20260517/run_225513 \\
        --extended-oos-json kabu_native/results/research/logic_lab/phase38_full_20260518/extended_oos_validation.json \\
        --config kabu_native/configs/small_paper_top_quartile.yaml \\
        --universe kabu_native/data/universe/universe_intraday_full.csv
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


def _universe_count(repo_root: Path, universe: Path | None) -> int | None:
    if not universe:
        return None
    import csv

    up = universe if universe.is_absolute() else (repo_root / universe)
    seen: set[str] = set()
    with up.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            p = str(row.get("passed", "")).strip().lower()
            if p not in ("true", "1", "yes"):
                continue
            sym = str(row.get("symbol", "")).strip()
            if sym:
                seen.add(sym)
    return len(seen) or None


def main() -> int:
    repo_root, native_root = _bootstrap()

    from research.top_quartile_oos_validation import run_phase40_top_quartile_oos_validation

    parser = argparse.ArgumentParser(
        description="Phase 40 top-quartile gate OOS validation (no live/shadow)"
    )
    parser.add_argument("--reference-run-dir", type=Path, required=True)
    parser.add_argument(
        "--extended-oos-json",
        type=Path,
        default=None,
        help="Phase38 extended_oos_validation.json for window run dirs",
    )
    parser.add_argument(
        "--latest-oos-json",
        type=Path,
        default=None,
        help="Phase41 latest_oos_window.json (preferred over extended-oos-json)",
    )
    parser.add_argument(
        "--window-run",
        action="append",
        default=[],
        help="Extra window id=path (e.g. oos_latest=.../run_xxx)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=native_root / "configs" / "small_paper_top_quartile.yaml",
    )
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--universe", type=Path, default=None)
    args = parser.parse_args()

    ref = (
        args.reference_run_dir
        if args.reference_run_dir.is_absolute()
        else (repo_root / args.reference_run_dir)
    )
    if not ref.is_dir():
        print(f"reference-run-dir not found: {ref}", file=sys.stderr)
        return 2

    cfg = args.config if args.config.is_absolute() else (repo_root / args.config)
    if not cfg.is_file():
        print(f"config not found: {cfg}", file=sys.stderr)
        return 2

    latest_json = None
    if args.latest_oos_json:
        latest_json = (
            args.latest_oos_json
            if args.latest_oos_json.is_absolute()
            else (repo_root / args.latest_oos_json)
        )
    ext_json = None
    if args.extended_oos_json:
        ext_json = (
            args.extended_oos_json
            if args.extended_oos_json.is_absolute()
            else (repo_root / args.extended_oos_json)
        )

    universe_n = _universe_count(repo_root, args.universe)
    if universe_n is None:
        ps_path = ref / "profile_summary.json"
        if ps_path.is_file():
            ps = json.loads(ps_path.read_text(encoding="utf-8"))
            syms = ps.get("symbols") or []
            universe_n = len(syms) if syms else 27
        else:
            universe_n = 27

    window_runs: list[dict[str, str]] = []
    for item in args.window_run:
        if "=" not in item:
            continue
        wid, path = item.split("=", 1)
        p = Path(path)
        if not p.is_absolute():
            p = repo_root / p
        window_runs.append({"window_id": wid, "run_dir": str(p.resolve())})

    out = args.output_dir or (
        repo_root
        / "kabu_native"
        / "results"
        / "research"
        / "logic_lab"
        / "phase40_top_quartile_oos"
    )
    if out and not out.is_absolute():
        out = repo_root / out

    log = logging.getLogger("run_phase40")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    result = run_phase40_top_quartile_oos_validation(
        reference_run_dir=ref.resolve(),
        output_dir=out.resolve(),
        config_path=cfg.resolve(),
        universe_symbol_count=universe_n,
        extended_oos_json=ext_json.resolve() if ext_json else None,
        latest_oos_json=latest_json.resolve() if latest_json else None,
        window_runs=window_runs or None,
    )

    report = json.loads(
        (result / "top_quartile_oos_validation.json").read_text(encoding="utf-8")
    )
    comb = report.get("combined_is_oos") or {}
    gate = comb.get("gate_accepted") or {}
    cand = report.get("candidate_evaluation") or {}
    log.info(
        "Phase40 done combined_trades=%s pf=%s candidate=%s",
        gate.get("trade_count"),
        gate.get("profit_factor"),
        cand.get("move_to_small_paper_candidate"),
    )
    print(f"Results: {result}")
    print(f"  move_to_small_paper_candidate: {cand.get('move_to_small_paper_candidate')}")
    print(
        f"  combined IS+OOS: trades={gate.get('trade_count')} "
        f"pf={gate.get('profit_factor')} avg={gate.get('avg_pnl_pct')}"
    )
    print(f"  OOS deterioration vs IS gate PF: {report.get('oos_deterioration_vs_in_sample_gate_pf_pct')}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
