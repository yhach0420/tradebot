#!/usr/bin/env python3
"""
Phase 10: verify combined opening gate + BF confirm=2 vs baseline and B alone.

例::
    python kabu_native/scripts/run_phase10_combined_candidates.py \\
        --start-date 2026-04-10 --end-date 2026-05-15
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
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


def _run_scenario(task: dict[str, Any]) -> dict[str, Any]:
    repo_root = Path(task["repo_root"])
    native_src = Path(task["native_src"])
    for p in (native_src, repo_root):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)

    from replay.combined_candidates import summarize_phase10
    from replay.entry_quality import replay_cached_enriched
    from replay.sweep_runner import SweepParams

    params = SweepParams(
        sweep_id=task["sweep_id"],
        sweep_group=task["sweep_group"],
        fail_window_min=float(task["fail_window_min"]),
        fail_buffer_pct=float(task["fail_buffer_pct"]),
        bf_confirm_count=int(task["bf_confirm_count"]),
        market_session_control=bool(task["market_session_control"]),
        hard_stop_pct=float(task["hard_stop_pct"]),
    )
    trades = replay_cached_enriched(
        task["cache"],
        params,
        repo_root=repo_root,
        tier=str(task["tier"]),
        entry_score_min=int(task["entry_score_min"]),
        require_timing_ok=bool(task["require_timing_ok"]),
        relaxed_signal=bool(task["relaxed_signal"]),
    )
    return summarize_phase10(trades, params)


def _pct(v: Any) -> str:
    if v is None:
        return "—"
    return f"{100 * float(v):.1f}%"


def _write_doc(path: Path, report: dict[str, Any]) -> None:
    rows = {r["sweep_id"]: r for r in report["rows"]}
    cmp_ = report["comparison"]
    sh = report["shadow_selection"]
    meta = report["meta"]

    lines = [
        "# Phase 10: 組み合わせ候補検証（A/C + B）",
        "",
        "## 目的",
        "",
        "寄り後ゲートで悪い ENTRY を削り、BF confirm=2 で過剰早逃げを抑えたとき、",
        "全体成績が baseline / B 単独より改善するかを確認する。",
        "",
        f"期間: {meta['start_date']} 〜 {meta['end_date']}（{meta['symbol_count']} 銘柄）",
        "",
        "## シナリオ",
        "",
        "| ID | session | bf_confirm | fail_buffer |",
        "|----|---------|------------|-------------|",
        "| baseline | off | 1 | 0.10 |",
        "| B | off | 2 | 0.12 |",
        "| A_plus_B | **on (09:05-14:50)** | 2 | 0.12 |",
        "| C_plus_B | **on (09:05-14:50)** | 2 | 0.12 |",
        "",
        "## 結果サマリー",
        "",
        "| シナリオ | trades | win% | total_pnl | avg_pnl | PF | MFE≥0.3% | 継続率 | med hold | top sym | 採用 |",
        "|----------|--------|------|-----------|---------|-----|-----------|--------|----------|---------|------|",
    ]

    shadow_id = sh.get("shadow_candidate_id")
    for sid in ("baseline", "B", "A_plus_B", "C_plus_B"):
        r = rows[sid]
        adopt = "★ shadow" if sid == shadow_id else ("除外" if r.get("excluded_low_trades") else "")
        lines.append(
            f"| {sid} | {r['trades']} | {_pct(r.get('win_rate'))} | "
            f"{r.get('total_pnl_pct', 0):.2f}% | {r.get('avg_pnl_pct', 0):.3f}% | "
            f"{r.get('profit_factor') or 0:.3f} | {_pct(r.get('mfe_reach_0.3pct'))} | "
            f"{_pct(r.get('breakout_continuation_rate'))} | {r.get('median_hold_min', 0):.2f}m | "
            f"{r.get('pnl_concentration_top_symbol')} | {adopt} |"
        )

    lines.extend(
        [
            "",
            "## EXIT理由分布",
            "",
        ]
    )
    for sid in ("baseline", "B", "A_plus_B", "C_plus_B"):
        ex = rows[sid].get("exit_reason_counts") or {}
        parts = ", ".join(f"{k}={v}" for k, v in sorted(ex.items(), key=lambda x: -x[1]))
        lines.append(f"- **{sid}**: {parts}")

    lines.extend(
        [
            "",
            "## baseline / B との比較",
            "",
            f"- A+B が baseline より良い: **{cmp_['A_plus_B_beats_baseline']}**",
            f"- A+B が B より良い: **{cmp_['A_plus_B_beats_B']}**",
            f"- C+B が baseline より良い: **{cmp_['C_plus_B_beats_baseline']}**",
            f"- C+B が B より良い: **{cmp_['C_plus_B_beats_B']}**",
            f"- 寄りゲートを入れるべきか: **{cmp_['use_opening_gate']}**",
            "",
            "## paper_trade shadow 推奨",
            "",
            f"**{shadow_id or '—'}**",
            "",
            f"- 理由: {sh.get('reason', '—')}",
            f"- trade フロア: {sh.get('trade_floor')}",
            f"- baseline より total_pnl 改善: {sh.get('improves_total_pnl_vs_baseline')}",
            f"- B 単独より total_pnl 改善: {sh.get('improves_total_pnl_vs_B')}",
            "",
            "### 判断メモ",
            "",
            "- trade 数がフロア未満の設定は採用候補外",
            "- 09:30 で trade が少なすぎる場合は 09:15（C+B）を優先",
            "- 個別銘柄最適化は行わず、上記は全27銘柄共通ルール",
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

    from replay.combined_candidates import (
        build_phase10_report,
        phase10_scenarios,
        pick_shadow_candidate,
    )
    from replay.entry_quality import replay_cached_enriched
    from replay.runner import load_replay_config
    from replay.sweep_runner import apply_trade_floor, build_event_cache

    parser = argparse.ArgumentParser(description="Phase 10 combined candidates")
    parser.add_argument("--start-date", default="2026-04-10")
    parser.add_argument("--end-date", default="2026-05-15")
    parser.add_argument(
        "--universe",
        type=Path,
        default=native_root / "data" / "universe" / "universe_intraday_full.csv",
    )
    parser.add_argument("--report-date", default=None)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    cfg_raw = load_replay_config(
        native_root / "configs" / "replay.yaml",
        native_root=native_root,
        repo_root=repo_root,
    )
    symbols = _symbols_from_universe(args.universe.resolve())
    report_date = args.report_date or datetime.now().strftime("%Y%m%d")
    reports_dir = native_root / "results" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    csv_path = reports_dir / f"phase10_combined_candidates_{report_date}.csv"
    json_path = reports_dir / f"phase10_combined_candidates_{report_date}.json"
    doc_path = native_root / "docs" / "phase10_combined_candidates.md"

    logging.info("building cache (%d symbols)...", len(symbols))
    cache = build_event_cache(
        repo_root=repo_root,
        symbols=symbols,
        start_date=args.start_date,
        end_date=args.end_date,
        data_roots=cfg_raw["data_roots"],
        synthetic_push_keep=float(cfg_raw.get("synthetic_push_keep", 1.0)),
        synthetic_spread_bps=float(cfg_raw.get("synthetic_spread_bps", 8.0)),
        synthetic_events_per_minute=int(cfg_raw.get("synthetic_events_per_minute", 10)),
    )

    scenarios = phase10_scenarios()
    native_src = native_root / "src"
    rows: list[dict[str, Any]] = []
    workers = max(1, int(args.workers))

    if workers == 1:
        from replay.combined_candidates import summarize_phase10 as _summ

        for params in scenarios:
            logging.info("replay %s", params.sweep_id)
            trades = replay_cached_enriched(
                cache,
                params,
                repo_root=repo_root,
                tier=str(cfg_raw.get("tier", "B")),
                entry_score_min=int(cfg_raw.get("entry_score_min", 60)),
                require_timing_ok=bool(cfg_raw.get("require_timing_ok", True)),
                relaxed_signal=bool(cfg_raw.get("relaxed_signal", False)),
            )
            rows.append(_summ(trades, params))
    else:
        tasks = [
            {
                "repo_root": str(repo_root),
                "native_src": str(native_src),
                "cache": cache,
                "tier": str(cfg_raw.get("tier", "B")),
                "entry_score_min": int(cfg_raw.get("entry_score_min", 60)),
                "require_timing_ok": bool(cfg_raw.get("require_timing_ok", True)),
                "relaxed_signal": bool(cfg_raw.get("relaxed_signal", False)),
                **p.to_dict(),
            }
            for p in scenarios
        ]
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_run_scenario, t) for t in tasks]
            for fut in as_completed(futures):
                row = fut.result()
                logging.info(
                    "done %s trades=%s pnl=%.2f",
                    row.get("sweep_id"),
                    row.get("trades"),
                    float(row.get("total_pnl_pct") or 0),
                )
                rows.append(row)
        order = {p.sweep_id: i for i, p in enumerate(scenarios)}
        rows.sort(key=lambda r: order.get(r.get("sweep_id", ""), 99))

    baseline_trades = int(next(r["trades"] for r in rows if r.get("sweep_id") == "baseline"))
    rows = apply_trade_floor(rows, baseline_trades=baseline_trades, min_ratio=0.50, min_absolute=40)
    shadow = pick_shadow_candidate(rows, baseline_trades=baseline_trades)
    report = build_phase10_report(
        rows,
        meta={
            "start_date": args.start_date,
            "end_date": args.end_date,
            "universe": str(args.universe.resolve()),
            "symbol_count": len(symbols),
            "cached_symbol_days": len(cache),
            "baseline_trades": baseline_trades,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "csv_path": str(csv_path),
            "json_path": str(json_path),
        },
        shadow=shadow,
    )

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    fieldnames = [
        "sweep_id",
        "market_session_control",
        "bf_confirm_count",
        "fail_buffer_pct",
        "trades",
        "symbols_with_trades",
        "win_rate",
        "total_pnl_pct",
        "avg_pnl_pct",
        "median_pnl_pct",
        "max_loss_pct",
        "profit_factor",
        "mfe_reach_0.3pct",
        "breakout_continuation_rate",
        "median_hold_min",
        "pnl_concentration_top_symbol",
        "pnl_concentration_top_share",
        "excluded_low_trades",
        "exit_reason_counts",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            out = dict(row)
            if isinstance(out.get("exit_reason_counts"), dict):
                out["exit_reason_counts"] = json.dumps(
                    out["exit_reason_counts"], ensure_ascii=False
                )
            w.writerow(out)

    _write_doc(doc_path, report)
    logging.info("shadow candidate: %s", shadow.get("shadow_candidate_id"))
    logging.info("wrote %s", csv_path)
    logging.info("wrote %s", json_path)
    logging.info("wrote %s", doc_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
