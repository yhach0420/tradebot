#!/usr/bin/env python3
"""
Phase 17: kabu_native logic lab — multi-profile replay diagnostics.

paper_trade / realtime Discord より前に、全銘柄・複数日でロジックを横比較する。

例::
    python kabu_native/scripts/run_logic_lab.py \\
        --start-date 2026-05-01 --end-date 2026-05-15 \\
        --universe kabu_native/data/universe/universe_intraday_full.csv

    python kabu_native/scripts/run_logic_lab.py \\
        --start-date 2026-05-12 --end-date 2026-05-15 \\
        --symbols 9984.T,8306.T --profiles baseline,relaxed_entry,continuation_v1
"""

from __future__ import annotations

import argparse
import csv
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
    roots = [
        (native_root / "data" / "intraday_1m").resolve(),
        (repo_root / "data" / "intraday_1m").resolve(),
    ]
    return roots


def main() -> int:
    repo_root, native_root = _bootstrap()

    from research.entry_v2 import (
        ENTRY_V2_PHASE24_PROFILES,
        ENTRY_V2_PHASE25_PROFILES,
        ENTRY_V2_PHASE26_PROFILES,
        ENTRY_V2_PHASE27_PROFILES,
        ENTRY_V2_PHASE28_PROFILES,
        ENTRY_V2_PHASE29_PROFILES,
        ENTRY_V2_PHASE30_PROFILES,
        ENTRY_V2_PHASE31_PROFILES,
        ENTRY_V2_PHASE32_PROFILES,
        ENTRY_V2_PHASE33_PROFILES,
        ENTRY_V2_PHASE34_PROFILES,
        ENTRY_V2_PHASE35_PROFILES,
    )
    from research.logic_lab import (
        ALL_PROFILE_NAMES,
        ENTRY_V2_COMPARISON_PROFILES,
        LogicLabConfig,
        PROFILE_NAMES,
        run_logic_lab,
    )

    parser = argparse.ArgumentParser(description="kabu_native logic lab (Phase 17)")
    parser.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--symbols", default=None, help="9984.T,8306.T")
    parser.add_argument("--universe", type=Path, default=None)
    parser.add_argument(
        "--universe-all-rows",
        action="store_true",
        help="universe の passed=false も含める",
    )
    parser.add_argument(
        "--profiles",
        default="all",
        help=f"カンマ区切りプロファイル名（既定: all = legacy）",
    )
    parser.add_argument(
        "--entry-v2-comparison",
        action="store_true",
        help=f"baseline + ENTRY v2 Phase23 ({','.join(ENTRY_V2_COMPARISON_PROFILES[1:6])}…) を実行",
    )
    parser.add_argument(
        "--entry-v2-phase24",
        action="store_true",
        help=f"Phase24: v1+v2+hybrid ({','.join(ENTRY_V2_PHASE24_PROFILES)})",
    )
    parser.add_argument(
        "--momentum-v3-phase25",
        action="store_true",
        help=f"Phase25: v2 loss analysis + v3 guards ({','.join(ENTRY_V2_PHASE25_PROFILES)})",
    )
    parser.add_argument(
        "--momentum-v4-phase26",
        action="store_true",
        help=f"Phase26: early adverse move + v4 protection ({','.join(ENTRY_V2_PHASE26_PROFILES)})",
    )
    parser.add_argument(
        "--momentum-v5-phase27",
        action="store_true",
        help=f"Phase27: recovery-based exit v5 ({','.join(ENTRY_V2_PHASE27_PROFILES)})",
    )
    parser.add_argument(
        "--momentum-v6-phase28",
        action="store_true",
        help=f"Phase28: microstructure-adaptive exit v6 ({','.join(ENTRY_V2_PHASE28_PROFILES)})",
    )
    parser.add_argument(
        "--momentum-v7-phase29",
        action="store_true",
        help=f"Phase29: noise-tolerant exit v7 vs v2/v5/v6 ({','.join(ENTRY_V2_PHASE29_PROFILES)})",
    )
    parser.add_argument(
        "--momentum-v8-phase30",
        action="store_true",
        help=f"Phase30: recovery persistence exit v8 ({','.join(ENTRY_V2_PHASE30_PROFILES)})",
    )
    parser.add_argument(
        "--momentum-v9-phase31",
        action="store_true",
        help=f"Phase31: state persistence exit v9 ({','.join(ENTRY_V2_PHASE31_PROFILES)})",
    )
    parser.add_argument(
        "--momentum-v10-phase32",
        action="store_true",
        help=f"Phase32: state transition exit v10 ({','.join(ENTRY_V2_PHASE32_PROFILES)})",
    )
    parser.add_argument(
        "--momentum-v11-phase33",
        action="store_true",
        help=f"Phase33: duration weighted exit v11 ({','.join(ENTRY_V2_PHASE33_PROFILES)})",
    )
    parser.add_argument(
        "--momentum-v12-phase34",
        action="store_true",
        help=f"Phase34: bullish continuation exit v12 ({','.join(ENTRY_V2_PHASE34_PROFILES)})",
    )
    parser.add_argument(
        "--momentum-v13-phase35",
        action="store_true",
        help=f"Phase35: momentum continuation priority v13 ({','.join(ENTRY_V2_PHASE35_PROFILES)})",
    )
    parser.add_argument(
        "--research-exit-phase36",
        action="store_true",
        help="Phase36: write research exit criteria / validation freeze report after run",
    )
    parser.add_argument(
        "--validation-phase37",
        action="store_true",
        help="Phase37: OOS + regime validation + paper trade gate (frozen v10–v13 profiles)",
    )
    parser.add_argument(
        "--validation-phase38",
        action="store_true",
        help="Phase38: extended OOS + quality ranking + small-scale paper (validation only)",
    )
    parser.add_argument("--tier", default="B")
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()

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
        symbols.extend(_symbols_from_universe(up, passed_only=not args.universe_all_rows))

    seen: set[str] = set()
    uniq: list[str] = []
    for s in symbols:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    symbols = uniq

    if not symbols:
        print("銘柄未指定: --symbols または --universe", file=sys.stderr)
        return 2

    if args.momentum_v13_phase35:
        profile_list = list(ENTRY_V2_PHASE35_PROFILES)
    elif args.momentum_v12_phase34:
        profile_list = list(ENTRY_V2_PHASE34_PROFILES)
    elif args.momentum_v11_phase33:
        profile_list = list(ENTRY_V2_PHASE33_PROFILES)
    elif args.momentum_v10_phase32:
        profile_list = list(ENTRY_V2_PHASE32_PROFILES)
    elif args.momentum_v9_phase31:
        profile_list = list(ENTRY_V2_PHASE31_PROFILES)
    elif args.momentum_v8_phase30:
        profile_list = list(ENTRY_V2_PHASE30_PROFILES)
    elif args.momentum_v7_phase29:
        profile_list = list(ENTRY_V2_PHASE29_PROFILES)
    elif args.momentum_v6_phase28:
        profile_list = list(ENTRY_V2_PHASE28_PROFILES)
    elif args.momentum_v5_phase27:
        profile_list = list(ENTRY_V2_PHASE27_PROFILES)
    elif args.momentum_v4_phase26:
        profile_list = list(ENTRY_V2_PHASE26_PROFILES)
    elif args.momentum_v3_phase25:
        profile_list = list(ENTRY_V2_PHASE25_PROFILES)
    elif args.entry_v2_phase24:
        profile_list = list(ENTRY_V2_PHASE24_PROFILES)
    elif args.entry_v2_comparison:
        profile_list = list(ENTRY_V2_COMPARISON_PROFILES)
    elif args.profiles.strip().lower() == "all":
        profile_list = list(ALL_PROFILE_NAMES)
    else:
        profile_list = [p.strip() for p in args.profiles.split(",") if p.strip()]

    day_key = datetime.now().strftime("%Y%m%d")
    time_key = datetime.now().strftime("%H%M%S")
    out_dir = args.output_dir or (
        native_root / "results" / "research" / "logic_lab" / day_key / f"run_{time_key}"
    )

    log_path = repo_root / "logs" / "runtime" / f"kabu_native_logic_lab_{day_key}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("kabu_native.run_logic_lab")
    log.handlers.clear()
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    for h in (
        logging.FileHandler(log_path, encoding="utf-8"),
        logging.StreamHandler(sys.stderr),
    ):
        h.setFormatter(fmt)
        log.addHandler(h)

    cfg = LogicLabConfig(
        start_date=args.start_date,
        end_date=args.end_date,
        symbols=symbols,
        data_roots=_load_data_roots(repo_root, native_root),
        output_dir=out_dir,
        profiles=profile_list,
        tier=args.tier,
        repo_root=repo_root,
        research_exit_phase36=args.research_exit_phase36,
        validation_phase37=args.validation_phase37,
        validation_phase38=args.validation_phase38,
    )

    log.info(
        "logic_lab start symbols=%d days=%s..%s profiles=%s",
        len(symbols),
        args.start_date,
        args.end_date,
        profile_list,
    )
    log.info("paper_trade: STOPPED (logic validation phase)")
    log.info("discord: diagnostics/replay only — not production notify")

    out = run_logic_lab(cfg)
    log.info("logic_lab done out=%s", out)
    print(f"Results: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
