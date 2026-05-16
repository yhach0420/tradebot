#!/usr/bin/env python3
"""
Phase 11: morning_screen (liquidity proxy) + A+B replay integration.

例::
    python kabu_native/scripts/run_phase11_screen_replay.py \\
        --start-date 2026-04-10 --end-date 2026-05-15
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from datetime import datetime
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
    symbols: list[str] = []
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            p = str(row.get("passed", "true")).strip().lower()
            if p not in ("true", "1", "yes", ""):
                continue
            sym = str(row.get("symbol", "")).strip()
            if sym:
                symbols.append(sym if sym.endswith(".T") else f"{sym}.T")
    return symbols


def _pct(v: Any) -> str:
    if v is None:
        return "—"
    return f"{100 * float(v):.1f}%"


def _write_doc(path: Path, report: dict[str, Any]) -> None:
    rows = report["replay_scenarios"]
    v = report["verdict"]
    meta = report["meta"]
    rank_table = report.get("screen_rank_table") or []

    lines = [
        "# Phase 11: morning_screen × replay 統合検証",
        "",
        "## 目的",
        "",
        "A+B シグナル/EXIT の上で、**監視銘柄（screen 上位 N）** が trade 品質を改善するか検証。",
        "",
        f"期間: {meta['start_date']} 〜 {meta['end_date']}（{meta['symbol_count']} 銘柄）",
        "",
        f"Screen 方式: **{meta.get('screen_mode_note', '')}**",
        "",
        "## リプレイ比較（A+B 固定）",
        "",
        "| scenario | top_n | trades | total_pnl | avg_pnl | PF | MFE≥0.3% | 9984 share | large_cap% | trades/sym |",
        "|----------|-------|--------|-----------|---------|-----|-----------|------------|------------|------------|",
    ]

    for r in rows:
        if "screen_static" in str(r.get("scenario", "")):
            continue
        lines.append(
            f"| {r.get('scenario')} | {r.get('top_n', '')} | {r.get('trades')} | "
            f"{r.get('total_pnl_pct', 0):.2f}% | {r.get('avg_pnl_pct', 0):.3f}% | "
            f"{r.get('profit_factor') or 0:.3f} | {_pct(r.get('mfe_reach_0.3pct'))} | "
            f"{r.get('bias_9984_pnl_share', '—')} | {_pct(r.get('bias_large_cap_trade_rate'))} | "
            f"{r.get('bias_trades_per_symbol_avg', 0):.2f} |"
        )

    lines.extend(["", "## Screen ランキング（期間平均 turnover プロキシ）", "", "| rank | symbol | score | avg_turnover |", "|------|--------|-------|--------------|"])
    for item in rank_table[:15]:
        lines.append(
            f"| {item.get('rank')} | {item.get('symbol')} | {item.get('score_proxy')} | {item.get('avg_daily_turnover', 0):.0f} |"
        )

    corr = report.get("correlation") or {}
    lines.extend(
        [
            "",
            "## score と pnl",
            "",
            f"- Pearson(score, pnl) universe_full: **{corr.get('universe_full_score_pnl', '—')}**",
            f"- Pearson(rank, pnl) universe_full: **{corr.get('universe_full_rank_pnl', '—')}**",
            f"- 上位N trade の質: top_rank_win_rate / pnl は各 scenario の `bias_*` 参照",
            "",
            "## 結論",
            "",
            f"- screen が universe より良い: **{v.get('screen_improves_vs_universe')}**",
            f"- walk-forward top10 が universe より PnL 改善: **{v.get('walk_forward_top_n_better_pnl')}**",
            f"- 9984 偏重が減った: **{v.get('9984_concentration_reduced')}**",
            f"- paper_trade shadow 準備: **{v.get('ready_for_paper_trade_shadow')}**",
            f"- 推奨 watchlist: **{v.get('recommended_watchlist_mode')}**",
            "",
            v.get("notes", ""),
            "",
            "## 出力",
            "",
            f"- `{meta.get('csv_path', '')}`",
            f"- `{meta.get('json_path', '')}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    repo_root, native_root = _bootstrap()

    from replay.runner import iter_trade_dates, load_replay_config
    from replay.screen_replay import (
        build_phase11_verdict,
        compute_daily_turnover,
        run_screen_replay_scenarios,
    )
    from replay.sweep_runner import build_event_cache

    parser = argparse.ArgumentParser(description="Phase 11 screen + replay")
    parser.add_argument("--start-date", default="2026-04-10")
    parser.add_argument("--end-date", default="2026-05-15")
    parser.add_argument(
        "--universe",
        type=Path,
        default=native_root / "data" / "universe" / "universe_intraday_full.csv",
    )
    parser.add_argument("--top-n", default="5,10,15", help="comma-separated top N")
    parser.add_argument("--report-date", default=None)
    args = parser.parse_args()

    cfg_raw = load_replay_config(
        native_root / "configs" / "replay.yaml",
        native_root=native_root,
        repo_root=repo_root,
    )
    symbols = _symbols_from_universe(args.universe.resolve())
    top_n_list = [int(x.strip()) for x in args.top_n.split(",") if x.strip()]
    report_date = args.report_date or datetime.now().strftime("%Y%m%d")
    reports_dir = native_root / "results" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    csv_path = reports_dir / f"phase11_screen_replay_{report_date}.csv"
    json_path = reports_dir / f"phase11_screen_replay_{report_date}.json"
    doc_path = native_root / "docs" / "phase11_screen_replay.md"

    logging.info("computing liquidity screen proxy...")
    daily_tv, meta_by_sym = compute_daily_turnover(
        repo_root=repo_root,
        data_roots=cfg_raw["data_roots"],
        symbols=symbols,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    trade_dates = iter_trade_dates(args.start_date, args.end_date)

    logging.info("building event cache...")
    cache = build_event_cache(
        repo_root=repo_root,
        symbols=list(meta_by_sym.keys()),
        start_date=args.start_date,
        end_date=args.end_date,
        data_roots=cfg_raw["data_roots"],
        synthetic_push_keep=float(cfg_raw.get("synthetic_push_keep", 1.0)),
        synthetic_spread_bps=float(cfg_raw.get("synthetic_spread_bps", 8.0)),
        synthetic_events_per_minute=int(cfg_raw.get("synthetic_events_per_minute", 10)),
    )

    rows = run_screen_replay_scenarios(
        cache=cache,
        meta_by_sym=meta_by_sym,
        daily_turnover=daily_tv,
        trade_dates=trade_dates,
        repo_root=repo_root,
        top_n_list=top_n_list,
        tier=str(cfg_raw.get("tier", "B")),
        entry_score_min=int(cfg_raw.get("entry_score_min", 60)),
        require_timing_ok=bool(cfg_raw.get("require_timing_ok", True)),
        relaxed_signal=bool(cfg_raw.get("relaxed_signal", False)),
    )

    uni_row = next((r for r in rows if r.get("scenario") == "universe_full"), {})
    verdict = build_phase11_verdict(rows, top_n=10)
    report = {
        "meta": {
            "start_date": args.start_date,
            "end_date": args.end_date,
            "universe": str(args.universe.resolve()),
            "symbol_count": len(symbols),
            "symbols_with_intraday": len(meta_by_sym),
            "screen_mode_note": (
                "Walk-forward top-N by prior-day intraday turnover (morning_screen liquidity proxy). "
                "score_proxy = normalized avg turnover."
            ),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "csv_path": str(csv_path),
            "json_path": str(json_path),
        },
        "screen_rank_table": [
            {
                "rank": m.rank,
                "symbol": m.symbol,
                "score_proxy": m.score_proxy,
                "avg_daily_turnover": m.avg_daily_turnover,
                "avg_price": m.avg_price,
            }
            for m in sorted(meta_by_sym.values(), key=lambda x: x.rank)
        ],
        "replay_scenarios": rows,
        "correlation": {
            "universe_full_score_pnl": uni_row.get("bias_score_pnl_pearson"),
            "universe_full_rank_pnl": uni_row.get("bias_rank_pnl_pearson"),
        },
        "verdict": verdict,
    }

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if rows:
        keys: list[str] = []
        for row in rows:
            for k in row:
                if k not in keys:
                    keys.append(k)
        with csv_path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            for row in rows:
                out = dict(row)
                if isinstance(out.get("exit_reason_counts"), dict):
                    out["exit_reason_counts"] = json.dumps(
                        out["exit_reason_counts"], ensure_ascii=False
                    )
                w.writerow(out)

    _write_doc(doc_path, report)
    logging.info("verdict: %s", verdict)
    logging.info("wrote %s", csv_path)
    logging.info("wrote %s", json_path)
    logging.info("wrote %s", doc_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
