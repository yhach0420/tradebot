#!/usr/bin/env python3
"""
kabu_signal_v1 / kabu_exit_v1 パラメータ横比較（リプレイ）。

大損削減（hard_stop / breakout_failure）と過剰早逃げのバランスを
1 日分データで高速に比較する。

例::
    python scripts/kabu_signal_param_sweep.py --day 2026-05-15 --symbols 9984.T --tier B

    python scripts/kabu_signal_param_sweep.py --day 2026-05-15 --mode grid --max-combos 200

    python scripts/kabu_signal_param_sweep.py --yahoo-csv data/intraday_1m/2026-05-15/9984.T.csv
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

import pandas as pd


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


_ROOT = _project_root()
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.kabu_signal_replay import (  # noqa: E402
    build_symbol_replay_events,
    exit_config_from_sweep,
    replay_signal_config,
    replay_symbol_events,
    summarize_trades_for_sweep,
)

# --- スイープ軸（Phase 5H 既定） ---
ENTRY_SCORE_MIN_VALUES = (50, 60, 70, 80)
BREAKOUT_FAILURE_MINUTES_VALUES = (1, 2, 3)
BREAKOUT_FAILURE_BUFFER_PCT_VALUES = (0.05, 0.12, 0.20)
HARD_STOP_PCT_VALUES = (-0.8, -1.0, -1.2, -1.35)
TIME_STOP_MIN_VALUES = (5, 9, 12, 15)
VWAP_EXIT_BUFFER_PCT_VALUES = (-0.03, -0.05, -0.10)

SWEEP_PARAM_NAMES = (
    "entry_score_min",
    "breakout_failure_minutes",
    "breakout_failure_buffer_pct",
    "hard_stop_pct",
    "time_stop_min",
    "vwap_exit_buffer_pct",
)


@dataclass(frozen=True)
class SweepParams:
    entry_score_min: int = 60
    breakout_failure_minutes: float = 2.0
    breakout_failure_buffer_pct: float = 0.12
    hard_stop_pct: float = -1.2
    time_stop_min: float = 9.0
    vwap_exit_buffer_pct: float = -0.05
    tier: str = "B"

    def combo_id(self) -> str:
        return (
            f"es{self.entry_score_min}"
            f"_bfm{self.breakout_failure_minutes:g}"
            f"_bfb{self.breakout_failure_buffer_pct:g}"
            f"_hs{self.hard_stop_pct:g}"
            f"_ts{self.time_stop_min:g}"
            f"_vw{self.vwap_exit_buffer_pct:g}"
        )

    def to_row_prefix(self) -> dict[str, Any]:
        return asdict(self)


def baseline_params(tier: str = "B") -> SweepParams:
    hard = -1.35 if tier.upper() == "A" else -1.2
    t_stop = 12.0 if tier.upper() == "A" else 9.0
    vwap = -0.05 if tier.upper() == "A" else -0.03
    return SweepParams(
        entry_score_min=60,
        breakout_failure_minutes=2.0,
        breakout_failure_buffer_pct=0.12,
        hard_stop_pct=hard,
        time_stop_min=t_stop,
        vwap_exit_buffer_pct=vwap,
        tier=tier.upper(),
    )


def iter_oaat_combos(baseline: SweepParams) -> Iterator[tuple[str, SweepParams]]:
    """1 軸ずつ変化（他は baseline）。"""
    yield "baseline", baseline
    for v in ENTRY_SCORE_MIN_VALUES:
        if v != baseline.entry_score_min:
            yield "entry_score_min", SweepParams(**{**asdict(baseline), "entry_score_min": v})
    for v in BREAKOUT_FAILURE_MINUTES_VALUES:
        if v != baseline.breakout_failure_minutes:
            yield "breakout_failure_minutes", SweepParams(
                **{**asdict(baseline), "breakout_failure_minutes": float(v)}
            )
    for v in BREAKOUT_FAILURE_BUFFER_PCT_VALUES:
        if v != baseline.breakout_failure_buffer_pct:
            yield "breakout_failure_buffer_pct", SweepParams(
                **{**asdict(baseline), "breakout_failure_buffer_pct": float(v)}
            )
    for v in HARD_STOP_PCT_VALUES:
        if v != baseline.hard_stop_pct:
            yield "hard_stop_pct", SweepParams(**{**asdict(baseline), "hard_stop_pct": float(v)})
    for v in TIME_STOP_MIN_VALUES:
        if v != baseline.time_stop_min:
            yield "time_stop_min", SweepParams(**{**asdict(baseline), "time_stop_min": float(v)})
    for v in VWAP_EXIT_BUFFER_PCT_VALUES:
        if v != baseline.vwap_exit_buffer_pct:
            yield "vwap_exit_buffer_pct", SweepParams(
                **{**asdict(baseline), "vwap_exit_buffer_pct": float(v)}
            )


def iter_grid_combos(baseline: SweepParams) -> Iterator[SweepParams]:
    for es, bfm, bfb, hs, ts, vw in itertools.product(
        ENTRY_SCORE_MIN_VALUES,
        BREAKOUT_FAILURE_MINUTES_VALUES,
        BREAKOUT_FAILURE_BUFFER_PCT_VALUES,
        HARD_STOP_PCT_VALUES,
        TIME_STOP_MIN_VALUES,
        VWAP_EXIT_BUFFER_PCT_VALUES,
    ):
        yield SweepParams(
            entry_score_min=int(es),
            breakout_failure_minutes=float(bfm),
            breakout_failure_buffer_pct=float(bfb),
            hard_stop_pct=float(hs),
            time_stop_min=float(ts),
            vwap_exit_buffer_pct=float(vw),
            tier=baseline.tier,
        )


def run_combo(
    *,
    symbols_events: dict[str, tuple[list, str]],
    params: SweepParams,
    replay_relaxed: bool,
    require_timing_ok: bool,
) -> list:
    exit_cfg = exit_config_from_sweep(
        tier=params.tier,
        breakout_failure_minutes=params.breakout_failure_minutes,
        breakout_failure_buffer_pct=params.breakout_failure_buffer_pct,
        hard_stop_pct=params.hard_stop_pct,
        time_stop_min=params.time_stop_min,
        vwap_exit_buffer_pct=params.vwap_exit_buffer_pct,
    )
    sig_cfg = replay_signal_config(relaxed=replay_relaxed)
    all_trades = []
    for symbol, (events, data_source) in symbols_events.items():
        res = replay_symbol_events(
            symbol,
            events,
            tier=params.tier,
            entry_score_min=params.entry_score_min,
            require_timing_ok=require_timing_ok,
            data_source=data_source,
            signal_cfg=sig_cfg,
            exit_cfg=exit_cfg,
        )
        all_trades.extend(res.trades)
    return all_trades


def _result_row(
    *,
    sweep_mode: str,
    varied_param: str,
    params: SweepParams,
    symbols: list[str],
    metrics: dict[str, Any],
) -> dict[str, Any]:
    row = {
        "sweep_mode": sweep_mode,
        "varied_param": varied_param,
        "combo_id": params.combo_id(),
        "symbols": ",".join(symbols),
        **params.to_row_prefix(),
        **metrics,
    }
    return row


def _write_ranked_csv(path: Path, rows: list[dict[str, Any]], sort_key: str, *, reverse: bool) -> None:
    valid = [r for r in rows if r.get(sort_key) is not None]
    ranked = sorted(valid, key=lambda r: r[sort_key], reverse=reverse)
    if not ranked:
        return
    fields = list(ranked[0].keys())
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for i, row in enumerate(ranked, start=1):
            out = dict(row)
            out["rank"] = i
            w.writerow(out)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="kabu signal/exit parameter sweep (replay)")
    ap.add_argument("--day", help="YYYY-MM-DD")
    ap.add_argument("--symbols", help="カンマ区切り")
    ap.add_argument("--yahoo-csv", help="単一銘柄 CSV")
    ap.add_argument("--tier", default="B", choices=["A", "B"])
    ap.add_argument(
        "--mode",
        choices=["oaat", "grid"],
        default="oaat",
        help="oaat=1軸ずつ比較（既定）, grid=全組合せ",
    )
    ap.add_argument("--max-combos", type=int, default=0, help="grid 時の上限（0=無制限）")
    ap.add_argument("--replay-relaxed-gates", dest="replay_relaxed", action="store_true", default=True)
    ap.add_argument("--no-replay-relaxed-gates", dest="replay_relaxed", action="store_false")
    ap.add_argument("--no-require-timing-ok", action="store_true")
    ap.add_argument("--synthetic-push-keep", type=float, default=1.0)
    ap.add_argument("--synthetic-events-per-minute", type=int, default=10)
    ap.add_argument("--out-dir", help="出力ディレクトリ")
    args = ap.parse_args(argv)

    relaxed = bool(args.replay_relaxed)
    require_timing_ok = not args.no_require_timing_ok

    if args.yahoo_csv:
        jobs = [(Path(args.yahoo_csv).stem, Path(args.yahoo_csv))]
        day = args.day or ""
    elif args.day:
        day_dir = _ROOT / "data" / "intraday_1m" / args.day
        if not day_dir.is_dir():
            print(f"not found: {day_dir}", file=sys.stderr)
            return 1
        if args.symbols:
            syms = [s.strip() for s in args.symbols.split(",") if s.strip()]
            jobs = [(s, day_dir / f"{s}.csv") for s in syms]
        else:
            jobs = [(p.stem, p) for p in sorted(day_dir.glob("*.csv"))]
        day = args.day
    else:
        ap.error("--day または --yahoo-csv が必要です")

    symbols_events: dict[str, tuple[list, str]] = {}
    for symbol, csv_path in jobs:
        if not csv_path.is_file():
            print(f"[skip] {csv_path}", file=sys.stderr)
            continue
        events, ds = build_symbol_replay_events(
            symbol=symbol,
            yahoo_csv=csv_path,
            synthetic_keep=args.synthetic_push_keep,
            synthetic_events_per_minute=args.synthetic_events_per_minute,
        )
        symbols_events[symbol] = (events, ds)
        print(f"[load] {symbol} events={len(events)}")

    if not symbols_events:
        print("no symbols loaded", file=sys.stderr)
        return 1

    symbols = list(symbols_events.keys())
    baseline = baseline_params(args.tier)
    rows: list[dict[str, Any]] = []

    if args.mode == "oaat":
        combo_list = list(iter_oaat_combos(baseline))
    else:
        combo_list = [("grid", p) for p in iter_grid_combos(baseline)]
        if args.max_combos > 0:
            combo_list = combo_list[: args.max_combos]
        print(f"[grid] combos={len(combo_list)} (full product up to {4*3*3*4*4*3})")

    for varied_param, params in combo_list:
        trades = run_combo(
            symbols_events=symbols_events,
            params=params,
            replay_relaxed=relaxed,
            require_timing_ok=require_timing_ok,
        )
        metrics = summarize_trades_for_sweep(trades)
        rows.append(
            _result_row(
                sweep_mode=args.mode,
                varied_param=varied_param if args.mode == "oaat" else "grid",
                params=params,
                symbols=symbols,
                metrics=metrics,
            )
        )
        print(
            f"[{params.combo_id()}] trades={metrics.get('trades')} "
            f"total_pnl={metrics.get('total_pnl_pct'):.3f} "
            f"max_loss={metrics.get('max_loss_pct')} "
            f"bf_exit={metrics.get('breakout_failure_exit_count')}"
        )

    day_key = (day or datetime.now(timezone.utc).strftime("%Y-%m-%d")).replace("-", "")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = (
        Path(args.out_dir)
        if args.out_dir
        else _ROOT / "results" / "kabu_signal_param_sweep" / day_key / f"sweep_{stamp}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    df = pd.DataFrame(rows)
    csv_path = out_dir / "sweep_results.csv"
    df.to_csv(csv_path, index=False, encoding="utf-8")
    json_path = out_dir / "sweep_results.json"
    json_path.write_text(
        json.dumps(
            {
                "logged_at_utc": datetime.now(timezone.utc).isoformat(),
                "sweep_mode": args.mode,
                "tier": args.tier,
                "symbols": symbols,
                "baseline": asdict(baseline),
                "replay_relaxed_gates": relaxed,
                "synthetic_note": "合成PUSH時は本番PUSH品質と別",
                "rows": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    _write_ranked_csv(out_dir / "best_by_max_loss.csv", rows, "max_loss_pct", reverse=True)
    _write_ranked_csv(out_dir / "best_by_total_pnl.csv", rows, "total_pnl_pct", reverse=True)
    _write_ranked_csv(out_dir / "best_by_profit_factor.csv", rows, "profit_factor", reverse=True)

    print(f"output: {out_dir}")
    print(f"rows: {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
