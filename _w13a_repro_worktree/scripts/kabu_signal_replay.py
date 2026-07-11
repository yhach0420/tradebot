#!/usr/bin/env python3
"""
kabu_signal_v1 / kabu_exit_v1 リプレイ検証（paper_trade 非接続）。

原則、パラメータ調整・戦略検証は本スクリプトで行い、
paper_trade 実運用を待たない。

入力:
  - Yahoo 1分足: data/intraday_1m/YYYY-MM-DD/<symbol>.csv
  - kabu REST: results/kabu_api/YYYYMMDD/*.json（板補強・任意）
  - kabu PUSH JSONL: results/kabu_push_probe/YYYYMMDD/*.jsonl
  - 合成 PUSH: Yahoo 1分足から生成（--synthetic-push-keep、検証用）

例::
    python scripts/kabu_signal_replay.py --day 2026-05-15 --symbols 9984.T,1321.T --tier B

    python scripts/kabu_signal_replay.py \\
        --yahoo-csv data/intraday_1m/2026-05-15/9984.T.csv --tier A

    python scripts/kabu_signal_replay.py --day 2026-05-15 \\
        --synthetic-push-keep 0.35 --tier B

    python scripts/kabu_signal_replay.py --day 2026-05-15 \\
        --yahoo-replay-signals-csv results/20260516/replay_1d_20260516_070634/replay_*_signals.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from glob import glob
from pathlib import Path
from typing import Any, Optional

import pandas as pd


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


_ROOT = _project_root()
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.kabu_signal_replay import (  # noqa: E402
    DATA_SOURCE_PUSH_JSONL,
    DATA_SOURCE_YAHOO_SYNTHETIC,
    ClosedTrade,
    compare_with_yahoo_replay_signals,
    events_from_push_messages,
    load_push_jsonl_events,
    merge_rest_board_template,
    push_messages_from_yahoo_df,
    replay_signal_config,
    replay_symbol_events,
    summarize_trades,
    yahoo_symbol_code,
)
from src.signal_engine import normalize_ohlcv_dataframe  # noqa: E402


def _load_yahoo_csv(path: Path) -> pd.DataFrame:
    return normalize_ohlcv_dataframe(pd.read_csv(path))


def _find_rest_json(symbol: str, api_glob: Optional[str], day: str) -> Optional[Path]:
    if api_glob:
        patterns = [api_glob]
    else:
        day_key = day.replace("-", "")
        patterns = [
            str(_ROOT / "results" / "kabu_api" / day_key / "kabu_api_check_*.json"),
            str(_ROOT / "results" / "kabu_api" / day_key / "*.json"),
        ]
    code = yahoo_symbol_code(symbol)
    for pat in patterns:
        for p in sorted(glob(pat)):
            path = Path(p)
            name = path.name.lower()
            if code.lower() in name or symbol.replace(".T", "").lower() in name:
                return path
    return None


def _resolve_yahoo_replay_csv(pattern: str) -> Path:
    matches = sorted(glob(pattern))
    if not matches:
        raise FileNotFoundError(f"yahoo replay signals not found: {pattern}")
    return Path(matches[-1])


def replay_one_symbol(
    *,
    symbol: str,
    yahoo_csv: Path,
    tier: str,
    push_jsonl: Optional[Path],
    rest_json: Optional[Path],
    synthetic_keep: float,
    synthetic_seed: int,
    synthetic_spread_bps: float,
    synthetic_events_per_minute: int,
    replay_relaxed: bool,
    entry_score_min: int,
    require_timing_ok: bool,
) -> tuple[list[ClosedTrade], dict[str, Any]]:
    df = _load_yahoo_csv(yahoo_csv)
    meta: dict[str, Any] = {
        "symbol": symbol,
        "yahoo_csv": str(yahoo_csv),
        "yahoo_rows": int(len(df)),
        "tier": tier,
    }

    if push_jsonl is not None and push_jsonl.is_file():
        events = load_push_jsonl_events(push_jsonl)
        data_source = DATA_SOURCE_PUSH_JSONL
        meta["push_jsonl"] = str(push_jsonl)
        meta["event_count"] = len(events)
        if rest_json is not None and rest_json.is_file():
            rest_payload = json.loads(rest_json.read_text(encoding="utf-8"))
            events = merge_rest_board_template(events, rest_payload)
            meta["rest_json"] = str(rest_json)
            data_source = "hybrid_push_jsonl_plus_rest"
    else:
        msgs = push_messages_from_yahoo_df(
            df,
            symbol=symbol,
            keep_fraction=synthetic_keep,
            seed=synthetic_seed,
            spread_bps=synthetic_spread_bps,
            events_per_minute=synthetic_events_per_minute,
        )
        events = events_from_push_messages(msgs, source=DATA_SOURCE_YAHOO_SYNTHETIC)
        data_source = DATA_SOURCE_YAHOO_SYNTHETIC
        meta["synthetic_push_keep"] = synthetic_keep
        meta["synthetic_push_note"] = (
            "Yahoo 1分足からの合成 kabu イベント。実 PUSH 品質・タイミングとは別。"
        )
        if rest_json is not None and rest_json.is_file():
            rest_payload = json.loads(rest_json.read_text(encoding="utf-8"))
            events = merge_rest_board_template(events, rest_payload)
            meta["rest_json"] = str(rest_json)

    sig_cfg = replay_signal_config(relaxed=replay_relaxed and push_jsonl is None)
    result = replay_symbol_events(
        symbol,
        events,
        tier=tier,
        entry_score_min=entry_score_min,
        require_timing_ok=require_timing_ok,
        data_source=data_source,
        signal_cfg=sig_cfg,
    )
    meta["eval_count"] = result.eval_count
    meta["entry_signals"] = result.entry_signals
    meta["virtual_trades"] = len(result.trades)
    return result.trades, meta


def _write_outputs(
    out_dir: Path,
    *,
    all_trades: list[ClosedTrade],
    per_symbol_meta: list[dict[str, Any]],
    run_meta: dict[str, Any],
    yahoo_compare: Optional[dict[str, Any]],
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    trades_path = out_dir / "kabu_replay_trades.csv"
    with trades_path.open("w", encoding="utf-8", newline="") as f:
        fields = [
            "symbol",
            "entry_time",
            "entry_price",
            "exit_time",
            "exit_price",
            "pnl_pct",
            "exit_reason",
            "max_favorable_excursion_pct",
            "max_adverse_excursion_pct",
            "elapsed_min",
            "signal_score_at_entry",
            "data_source",
        ]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for t in all_trades:
            w.writerow(t.to_row())

    summary = summarize_trades(all_trades)
    summary_path = out_dir / "kabu_replay_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    by_reason_path = out_dir / "kabu_replay_by_exit_reason.csv"
    with by_reason_path.open("w", encoding="utf-8", newline="") as f:
        fields = ["exit_reason", "count", "win_rate", "avg_pnl_pct", "median_pnl_pct", "max_loss_pct"]
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in summary.get("by_exit_reason") or []:
            w.writerow(row)

    trades_json_path = out_dir / "kabu_replay_trades.json"
    trades_json_path.write_text(
        json.dumps([t.to_row() for t in all_trades], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    run_meta_path = out_dir / "kabu_replay_run_meta.json"
    run_meta["per_symbol"] = per_symbol_meta
    run_meta_path.write_text(json.dumps(run_meta, ensure_ascii=False, indent=2), encoding="utf-8")

    if yahoo_compare is not None:
        (out_dir / "yahoo_replay_compare.json").write_text(
            json.dumps(yahoo_compare, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="kabu_signal_v1 / kabu_exit_v1 replay")
    ap.add_argument("--day", help="YYYY-MM-DD（data/intraday_1m/<day>/ を走査）")
    ap.add_argument("--symbols", help="カンマ区切り。未指定なら当日ディレクトリ全銘柄")
    ap.add_argument("--yahoo-csv", help="単一銘柄 Yahoo 1分足 CSV")
    ap.add_argument("--tier", default="B", choices=["A", "B", "C"])
    ap.add_argument("--entry-score-min", type=int, default=60)
    ap.add_argument("--no-require-timing-ok", action="store_true")
    ap.add_argument("--push-jsonl", help="kabu PUSH JSONL（実 PUSH リプレイ）")
    ap.add_argument("--api-check-json", help="kabu REST スナップショット（板補強）")
    ap.add_argument("--api-check-glob", help="REST JSON 探索 glob")
    ap.add_argument(
        "--synthetic-push-keep",
        type=float,
        default=1.0,
        help="Yahoo→合成PUSH の保持率 0〜1（検証用）",
    )
    ap.add_argument("--synthetic-push-seed", type=int, default=0)
    ap.add_argument("--synthetic-spread-bps", type=float, default=8.0)
    ap.add_argument(
        "--synthetic-events-per-minute",
        type=int,
        default=10,
        help="合成PUSHの1分あたりイベント数（G8 push密度用）",
    )
    ap.add_argument(
        "--replay-relaxed-gates",
        action="store_true",
        help="合成PUSHリプレイ時のみ G8/G7 を緩和（本番ゲートとは別）",
    )
    ap.add_argument("--yahoo-replay-signals-csv", help="Yahoo リプレイ signals.csv（比較用 glob 可）")
    ap.add_argument(
        "--out-dir",
        help="出力先（既定: results/kabu_signal_replay/YYYYMMDD/kabu_replay_<stamp>）",
    )
    args = ap.parse_args(argv)

    if not args.day and not args.yahoo_csv:
        ap.error("--day または --yahoo-csv が必要です")

    day = args.day or ""
    if args.yahoo_csv:
        jobs = [(Path(args.yahoo_csv).stem, Path(args.yahoo_csv))]
        if not day:
            parts = Path(args.yahoo_csv).parts
            for i, p in enumerate(parts):
                if p == "intraday_1m" and i + 1 < len(parts):
                    day = parts[i + 1]
                    break
    else:
        day_dir = _ROOT / "data" / "intraday_1m" / day
        if not day_dir.is_dir():
            print(f"day directory not found: {day_dir}", file=sys.stderr)
            return 1
        if args.symbols:
            syms = [s.strip() for s in args.symbols.split(",") if s.strip()]
            jobs = [(s, day_dir / f"{s}.csv") for s in syms]
        else:
            jobs = [(p.stem, p) for p in sorted(day_dir.glob("*.csv"))]

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    day_key = day.replace("-", "") if day else datetime.now(timezone.utc).strftime("%Y%m%d")
    out_dir = (
        Path(args.out_dir)
        if args.out_dir
        else _ROOT / "results" / "kabu_signal_replay" / day_key / f"kabu_replay_{stamp}"
    )

    all_trades: list[ClosedTrade] = []
    per_symbol_meta: list[dict[str, Any]] = []

    for symbol, csv_path in jobs:
        if not csv_path.is_file():
            print(f"[skip] missing csv: {csv_path}", file=sys.stderr)
            continue
        push_path = Path(args.push_jsonl) if args.push_jsonl else None
        rest_path = Path(args.api_check_json) if args.api_check_json else _find_rest_json(
            symbol, args.api_check_glob, day
        )
        try:
            trades, meta = replay_one_symbol(
                symbol=symbol,
                yahoo_csv=csv_path,
                tier=args.tier,
                push_jsonl=push_path,
                rest_json=rest_path,
                synthetic_keep=args.synthetic_push_keep,
                synthetic_seed=args.synthetic_push_seed,
                synthetic_spread_bps=args.synthetic_spread_bps,
                synthetic_events_per_minute=args.synthetic_events_per_minute,
                replay_relaxed=args.replay_relaxed_gates,
                entry_score_min=args.entry_score_min,
                require_timing_ok=not args.no_require_timing_ok,
            )
            all_trades.extend(trades)
            per_symbol_meta.append(meta)
            print(
                f"[ok] {symbol} trades={len(trades)} "
                f"eval={meta.get('eval_count')} entry_signals={meta.get('entry_signals')}"
            )
        except Exception as e:
            print(f"[error] {symbol} err={e!r}", file=sys.stderr)

    yahoo_compare = None
    if args.yahoo_replay_signals_csv:
        ypath = _resolve_yahoo_replay_csv(args.yahoo_replay_signals_csv)
        yahoo_compare = compare_with_yahoo_replay_signals(all_trades, ypath)

    run_meta = {
        "logged_at_utc": datetime.now(timezone.utc).isoformat(),
        "day": day,
        "tier": args.tier,
        "entry_score_min": args.entry_score_min,
        "require_timing_ok": not args.no_require_timing_ok,
        "synthetic_push_keep": args.synthetic_push_keep,
        "synthetic_events_per_minute": args.synthetic_events_per_minute,
        "replay_relaxed_gates": args.replay_relaxed_gates,
        "policy": "replay_first_not_paper_trade",
        "symbols_run": [m["symbol"] for m in per_symbol_meta],
    }

    _write_outputs(
        out_dir,
        all_trades=all_trades,
        per_symbol_meta=per_symbol_meta,
        run_meta=run_meta,
        yahoo_compare=yahoo_compare,
    )

    summary = summarize_trades(all_trades)
    print(f"output: {out_dir}")
    print(
        f"trades={summary['trades']} win_rate={summary.get('win_rate')} "
        f"avg_pnl_pct={summary.get('avg_pnl_pct')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
