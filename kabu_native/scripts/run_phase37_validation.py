#!/usr/bin/env python3
"""
Phase 37: Validation freeze + OOS + regime validation + paper trade gate.

No new EXIT logic. Runs frozen profiles (baseline, v2, v10–v13 combined) on OOS windows.

例::
    # Use existing IS run + run OOS replays
    python kabu_native/scripts/run_phase37_validation.py \\
        --is-run-dir kabu_native/results/research/logic_lab/20260517/run_HHMMSS \\
        --universe kabu_native/data/universe/universe_intraday_full.csv \\
        --run-oos

    # Evaluate only (pre-computed OOS run dirs)
    python kabu_native/scripts/run_phase37_validation.py \\
        --is-run-dir .../run_IS \\
        --oos-run-dir-april .../run_april \\
        --oos-run-dir-may .../run_may
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
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


def _symbols_from_universe(path: Path, *, passed_only: bool) -> list[str]:
    import csv

    out: list[str] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if passed_only:
                p = str(row.get("passed", "")).strip().lower()
                if p not in ("true", "1", "yes"):
                    continue
            sym = str(row.get("symbol", "")).strip()
            if not sym:
                continue
            if not sym.endswith(".T"):
                sym = f"{sym.split('@')[0].replace('.T', '')}.T"
            if sym not in seen:
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

    from research.phase37_validation import (
        DEFAULT_IS_WINDOW,
        DEFAULT_OOS_WINDOWS,
        MOMENTUM_V13_COMBINED_REFERENCE,
        Phase37Input,
        _latest_trading_date,
        _trading_days_between,
        run_logic_lab_for_window,
        run_phase37_validation,
    )

    parser = argparse.ArgumentParser(description="Phase 37 validation freeze + OOS")
    parser.add_argument(
        "--is-run-dir",
        type=Path,
        required=True,
        help="In-sample Logic Lab run (e.g. 2026-05-01..2026-05-15)",
    )
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--universe", type=Path, default=None)
    parser.add_argument("--symbols", default=None)
    parser.add_argument("--focus-profile", default=MOMENTUM_V13_COMBINED_REFERENCE)
    parser.add_argument(
        "--oos-run-dir-april",
        type=Path,
        default=None,
        help="Existing OOS run for 2026-04-01..2026-04-30",
    )
    parser.add_argument(
        "--oos-run-dir-may",
        type=Path,
        default=None,
        help="Existing OOS run for 2026-05-16..latest",
    )
    parser.add_argument(
        "--run-oos",
        action="store_true",
        help="Run Logic Lab for OOS windows (slow; uses frozen profiles only)",
    )
    parser.add_argument("--tier", default="B")
    args = parser.parse_args()

    is_run = args.is_run_dir if args.is_run_dir.is_absolute() else (repo_root / args.is_run_dir)
    if not is_run.is_dir():
        print(f"is-run-dir not found: {is_run}", file=sys.stderr)
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
        symbols.extend(_symbols_from_universe(up, passed_only=True))
    if not symbols:
        ps_path = is_run / "profile_summary.json"
        if ps_path.is_file():
            import json

            ps = json.loads(ps_path.read_text(encoding="utf-8"))
            symbols = list(ps.get("symbols") or [])
    if not symbols:
        print("銘柄未指定: --universe / --symbols または IS run に symbols", file=sys.stderr)
        return 2

    log = logging.getLogger("kabu_native.run_phase37_validation")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    oos_windows = []
    for spec in DEFAULT_OOS_WINDOWS:
        start = spec["start"]
        end = spec["end"] or _latest_trading_date(data_roots) or "2026-05-15"
        wid = spec["id"]
        existing = None
        if wid == "oos_april" and args.oos_run_dir_april:
            existing = args.oos_run_dir_april
        if wid == "oos_may_forward" and args.oos_run_dir_may:
            existing = args.oos_run_dir_may

        if existing is not None:
            run_path = existing if existing.is_absolute() else (repo_root / existing)
            log.info("OOS %s: using existing %s", wid, run_path)
        elif args.run_oos:
            days = _trading_days_between(start, end, data_roots)
            if not days:
                log.warning("OOS %s: no trading days in %s..%s — skip", wid, start, end)
                continue
            day_key = datetime.now().strftime("%Y%m%d")
            time_key = datetime.now().strftime("%H%M%S")
            run_path = (
                native_root
                / "results"
                / "research"
                / "logic_lab"
                / "phase37_oos"
                / day_key
                / f"{wid}_{time_key}"
            )
            log.info("OOS %s: running logic_lab %s..%s -> %s", wid, start, end, run_path)
            run_path = run_logic_lab_for_window(
                start=start,
                end=end,
                symbols=symbols,
                data_roots=data_roots,
                output_dir=run_path,
                repo_root=repo_root,
                tier=args.tier,
            )
        else:
            log.warning(
                "OOS %s: no run dir (use --run-oos or --oos-run-dir-*)", wid
            )
            continue

        oos_windows.append(
            {"id": wid, "start": start, "end": end, "run_dir": str(run_path.resolve())}
        )

    out_dir = args.output_dir or is_run
    if out_dir and not out_dir.is_absolute():
        out_dir = repo_root / out_dir

    result = run_phase37_validation(
        Phase37Input(
            is_run_dir=is_run.resolve(),
            oos_runs=oos_windows,
            data_roots=data_roots,
            universe_symbol_count=len(symbols),
            focus_profile=args.focus_profile,
            output_dir=out_dir.resolve() if out_dir else None,
        )
    )

    import json

    freeze = json.loads((result / "validation_freeze_report.json").read_text(encoding="utf-8"))
    decision = freeze.get("research_decision", {}).get("research_decision", "?")
    log.info("Phase37 complete out=%s decision=%s", result, decision)
    print(f"Results: {result}")
    print(f"  research_decision: {decision}")
    print("  validation_freeze_report.json")
    print("  oos_validation_report.json")
    print("  regime_validation.json")
    print("  paper_trade_readiness.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
