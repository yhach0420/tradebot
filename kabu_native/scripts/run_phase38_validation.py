#!/usr/bin/env python3
"""
Phase 38: Extended OOS + small-scale paper validation (frozen v10–v13).

例::
    python kabu_native/scripts/run_phase38_validation.py \\
        --reference-run-dir kabu_native/results/research/logic_lab/YYYYMMDD/run_HHMMSS \\
        --universe kabu_native/data/universe/universe_intraday_full.csv \\
        --run-extended-oos
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any


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


def _symbols_from_universe(path: Path) -> list[str]:
    import csv

    out: list[str] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            p = str(row.get("passed", "")).strip().lower()
            if p not in ("true", "1", "yes"):
                continue
            sym = str(row.get("symbol", "")).strip()
            if not sym.endswith(".T"):
                sym = f"{sym.split('@')[0].replace('.T', '')}.T"
            if sym and sym not in seen:
                seen.add(sym)
                out.append(sym)
    return out


def _load_data_roots(repo_root: Path, native_root: Path) -> list[Path]:
    return [
        (native_root / "data" / "intraday_1m").resolve(),
        (repo_root / "data" / "intraday_1m").resolve(),
    ]


def main() -> int:
    repo_root, native_root = _bootstrap()

    from research.entry_v2 import MOMENTUM_V13_COMBINED_REFERENCE
    from research.extended_oos_validation import resolve_extended_windows
    from research.phase38_validation import Phase38Input, run_extended_oos_replays, run_phase38_validation

    parser = argparse.ArgumentParser(description="Phase 38 extended OOS + small-scale paper")
    parser.add_argument("--reference-run-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--universe", type=Path, default=None)
    parser.add_argument("--symbols", default=None)
    parser.add_argument("--focus-profile", default=MOMENTUM_V13_COMBINED_REFERENCE)
    parser.add_argument(
        "--run-extended-oos",
        action="store_true",
        help="Replay frozen profiles on March/April/May-late/latest windows",
    )
    parser.add_argument(
        "--window-run",
        action="append",
        default=[],
        help="id=path for precomputed window (e.g. oos_march=.../run_xxx)",
    )
    parser.add_argument("--tier", default="B")
    args = parser.parse_args()

    ref = args.reference_run_dir if args.reference_run_dir.is_absolute() else (repo_root / args.reference_run_dir)
    if not ref.is_dir():
        print(f"reference-run-dir not found: {ref}", file=sys.stderr)
        return 2

    data_roots = _load_data_roots(repo_root, native_root)
    symbols: list[str] = []
    if args.symbols:
        for raw in args.symbols.split(","):
            s = raw.strip().upper()
            if s and not s.endswith(".T"):
                s = f"{s}.T"
            if s:
                symbols.append(s)
    if args.universe:
        up = args.universe if args.universe.is_absolute() else (repo_root / args.universe)
        symbols.extend(_symbols_from_universe(up))
    if not symbols:
        ps = json.loads((ref / "profile_summary.json").read_text(encoding="utf-8"))
        symbols = list(ps.get("symbols") or [])
    if not symbols:
        print("symbols required", file=sys.stderr)
        return 2

    log = logging.getLogger("run_phase38")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    window_runs: list[dict[str, Any]] = []
    for item in args.window_run:
        if "=" not in item:
            continue
        wid, path = item.split("=", 1)
        p = Path(path)
        if not p.is_absolute():
            p = repo_root / p
        window_runs.append({"id": wid, "run_dir": str(p.resolve())})

    if args.run_extended_oos:
        log.info("Running extended OOS replays (frozen profiles)...")
        prebuilt_ids = {w["id"] for w in window_runs}
        new_runs = run_extended_oos_replays(
            symbols=symbols,
            data_roots=data_roots,
            repo_root=repo_root,
            reference_run_dir=ref,
            tier=args.tier,
        )
        window_runs = window_runs + [w for w in new_runs if w["id"] not in prebuilt_ids]
    elif not window_runs:
        for spec in resolve_extended_windows(data_roots):
            log.warning(
                "No window run for %s — use --run-extended-oos or --window-run %s=PATH",
                spec["id"],
                spec["id"],
            )

    out_dir = args.output_dir or ref
    if out_dir and not out_dir.is_absolute():
        out_dir = repo_root / out_dir

    result = run_phase38_validation(
        Phase38Input(
            reference_run_dir=ref.resolve(),
            window_runs=window_runs,
            data_roots=data_roots,
            universe_symbol_count=len(symbols),
            focus_profile=args.focus_profile,
            output_dir=out_dir.resolve() if out_dir else None,
            repo_root=repo_root,
            tier=args.tier,
        )
    )

    decision = json.loads((result / "paper_trade_readiness_v2.json").read_text(encoding="utf-8"))
    log.info("Phase38 done decision=%s", decision.get("research_decision"))
    print(f"Results: {result}")
    print(f"  research_decision: {decision.get('research_decision')}")
    print(f"  root_cause: {decision.get('root_cause_split')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
