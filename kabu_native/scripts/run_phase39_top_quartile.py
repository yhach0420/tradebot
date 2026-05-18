#!/usr/bin/env python3
"""
Phase 39: Top-quartile small paper exposure gate (simulation only).

例::
    python kabu_native/scripts/run_phase39_top_quartile.py \\
        --reference-run-dir kabu_native/results/research/logic_lab/phase38_full_20260518 \\
        --config kabu_native/configs/small_paper_top_quartile.yaml
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


def main() -> int:
    repo_root, native_root = _bootstrap()

    from research.small_scale_paper_validation import run_phase39_top_quartile_validation

    parser = argparse.ArgumentParser(
        description="Phase 39 top-quartile exposure gate (no live/shadow)"
    )
    parser.add_argument("--reference-run-dir", type=Path, required=True)
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

    universe_n: int | None = None
    if args.universe:
        import csv

        up = args.universe if args.universe.is_absolute() else (repo_root / args.universe)
        seen: set[str] = set()
        with up.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                p = str(row.get("passed", "")).strip().lower()
                if p not in ("true", "1", "yes"):
                    continue
                sym = str(row.get("symbol", "")).strip()
                if sym:
                    seen.add(sym)
        universe_n = len(seen) or None
    if universe_n is None:
        ps_path = ref / "profile_summary.json"
        if ps_path.is_file():
            ps = json.loads(ps_path.read_text(encoding="utf-8"))
            syms = ps.get("symbols") or []
            universe_n = len(syms) if syms else None

    out = args.output_dir or ref
    if out and not out.is_absolute():
        out = repo_root / out

    log = logging.getLogger("run_phase39")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    result = run_phase39_top_quartile_validation(
        reference_run_dir=ref.resolve(),
        config_path=cfg.resolve(),
        output_dir=out.resolve(),
        universe_symbol_count=universe_n,
    )

    report = json.loads(
        (result / "small_paper_top_quartile_report.json").read_text(encoding="utf-8")
    )
    acc = report.get("accepted_metrics") or {}
    cand = report.get("candidate_evaluation") or {}
    log.info(
        "Phase39 done accepted=%s pf=%s candidate=%s",
        acc.get("trade_count"),
        acc.get("profit_factor"),
        cand.get("move_to_small_paper_candidate"),
    )
    print(f"Results: {result}")
    print(f"  move_to_small_paper_candidate: {cand.get('move_to_small_paper_candidate')}")
    print(f"  accepted trades: {acc.get('trade_count')} pf={acc.get('profit_factor')}")
    gs = report.get("gate_summary") or {}
    print(
        f"  rejects: low_quality={gs.get('rejected_low_quality')} "
        f"max_concurrent={gs.get('rejected_max_concurrent')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
