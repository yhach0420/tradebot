#!/usr/bin/env python3
"""
Phase 9: ENTRY quality analysis for baseline vs Phase 8 candidates A/B/C.

例::
    python kabu_native/scripts/run_phase9_entry_quality.py \\
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


def _run_scenario(task: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    repo_root = Path(task["repo_root"])
    native_src = Path(task["native_src"])
    for p in (native_src, repo_root):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)

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
    return params.sweep_id, [t.to_dict() for t in trades]


def _write_doc(path: Path, report: dict[str, Any]) -> None:
    m = report["scenario_metrics"]
    base = m["baseline"]
    v = report["verdict"]
    lines = [
        "# Phase 9: ENTRY品質分析",
        "",
        "## 目的",
        "",
        "Phase 8 の損失改善が **trade数減少のみ** か **ENTRY品質改善** かを切り分ける。",
        "",
        "## 対象シナリオ",
        "",
        "| ID | 設定 |",
        "|----|------|",
        "| baseline | fail_window 2m, buffer 0.10, bf_confirm 1, entry from 09:00 |",
        "| candidate_a | no_entry_until **09:30** |",
        "| candidate_b | bf_confirm **2**, fail_buffer **0.12** |",
        "| candidate_c | no_entry_until **09:15** |",
        "",
        f"期間: {report['meta']['start_date']} 〜 {report['meta']['end_date']}  ",
        f"（{report['meta']['symbol_count']} symbols, {report['meta']['cached_symbol_days']} symbol-days）",
        "",
        "## 1. MFE到達率",
        "",
        "| シナリオ | trades | +0.1% | +0.3% | +0.5% | +1.0% | avg MFE |",
        "|----------|--------|-------|-------|-------|-------|---------|",
    ]

    def pct(x: Any) -> str:
        if x is None:
            return "—"
        return f"{100 * float(x):.1f}%"

    for sid in ("baseline", "candidate_a", "candidate_b", "candidate_c"):
        r = m[sid]
        lines.append(
            f"| {sid} | {r['trades']} | {pct(r.get('mfe_reach_0.1pct'))} | "
            f"{pct(r.get('mfe_reach_0.3pct'))} | {pct(r.get('mfe_reach_0.5pct'))} | "
            f"{pct(r.get('mfe_reach_1pct'))} | {r.get('avg_mfe_pct', 0):.3f}% |"
        )

    lines.extend(
        [
            "",
            "## 2. ENTRY直後逆行（MAE）",
            "",
            "| シナリオ | 1分 avg | 1分 adverse率 | 3分 avg | 5分 avg |",
            "|----------|---------|---------------|---------|---------|",
        ]
    )
    for sid in ("baseline", "candidate_a", "candidate_b", "candidate_c"):
        r = m[sid]
        lines.append(
            f"| {sid} | {r.get('early_mae_1m_avg_pct', 0):.3f}% | "
            f"{pct(r.get('early_adverse_1m_rate'))} | "
            f"{r.get('early_mae_3m_avg_pct', 0):.3f}% | "
            f"{r.get('early_mae_5m_avg_pct', 0):.3f}% |"
        )

    lines.extend(
        [
            "",
            "## 3. breakout継続率",
            "",
            "| シナリオ | 高値更新率 |",
            "|----------|------------|",
        ]
    )
    for sid in ("baseline", "candidate_a", "candidate_b", "candidate_c"):
        lines.append(f"| {sid} | {pct(m[sid].get('breakout_continuation_rate'))} |")

    lines.extend(
        [
            "",
            "## 4. HOLD時間",
            "",
            "| シナリオ | avg (min) | median (min) |",
            "|----------|-----------|--------------|",
        ]
    )
    for sid in ("baseline", "candidate_a", "candidate_b", "candidate_c"):
        r = m[sid]
        lines.append(
            f"| {sid} | {r.get('avg_hold_min', 0):.2f} | {r.get('median_hold_min', 0):.2f} |"
        )

    lines.extend(
        [
            "",
            "## 5. EXIT理由別 MFE（平均）",
            "",
            "| シナリオ | BF | hard_stop | time_stop | vwap |",
            "|----------|-----|-----------|-----------|------|",
        ]
    )
    def _f(v: Any) -> str:
        if v is None:
            return "—"
        return f"{float(v):.3f}%"

    for sid in ("baseline", "candidate_a", "candidate_b", "candidate_c"):
        r = m[sid]
        lines.append(
            f"| {sid} | {_f(r.get('mfe_avg_exit_breakout_failure'))} | "
            f"{_f(r.get('mfe_avg_exit_hard_stop'))} | "
            f"{_f(r.get('mfe_avg_exit_time_stop'))} | "
            f"{_f(r.get('mfe_avg_exit_vwap_reclaim_failure'))} |"
        )

    lines.extend(["", "## 6. 削除された trade（baseline 比）", ""])
    for comp in report["removal_comparisons"]:
        cid = comp["candidate_id"]
        lines.append(f"### vs {cid}")
        lines.append("")
        lines.append(f"- 削除: **{comp['removed_count']}** / 維持: {comp['kept_count']} / 追加: {comp['added_count']}")
        lines.append(f"- 解釈: `{comp['interpretation_hint']}`")
        rw = comp.get("removed_wins", {})
        rl = comp.get("removed_losses", {})
        lines.append(
            f"- 削除内訳: 勝ち {rw.get('count', 0)}件 (pnl {rw.get('total_pnl_pct', 0):.2f}%), "
            f"負け {rl.get('count', 0)}件 (pnl {rl.get('total_pnl_pct', 0):.2f}%)"
        )
        lines.append(
            f"- 削除のノイズ proxy (MFE<0.1% & 負け): {pct(comp['removed'].get('noise_proxy_rate'))}"
        )
        smfe = comp.get("same_entry_avg_mfe_delta_pct")
        if smfe is not None:
            lines.append(
                f"- 同一ENTRYの成績変化（維持 {comp.get('same_entry_paired_count')}件）: "
                f"avg MFE Δ {smfe:+.3f}%, avg PnL Δ {comp.get('same_entry_avg_pnl_delta_pct', 0):+.3f}%"
            )
        lines.append("")

    notes = v.get("notes") or {}
    if notes:
        lines.extend(["", "### シナリオ別メモ", ""])
        for sid, note in notes.items():
            lines.append(f"- **{sid}**: {note}")

    lines.extend(
        [
            "",
            "## 結論",
            "",
            f"- EXIT/HOLD改善（MFE・継続率）: **{', '.join(v.get('exit_hold_improved_scenarios') or []) or 'なし'}**",
            f"- ENTRY品質（到達率）改善: **{', '.join(v.get('entry_quality_improved_scenarios') or []) or 'なし'}**",
            f"- **trade数削減が主因**: **{', '.join(v.get('trade_reduction_only_scenarios') or []) or 'なし'}**",
            f"- **次に触るべき層: {v.get('recommended_next_focus', '—')}**",
            "",
            "### 読み方",
            "",
            "- MFE到達率・breakout継続率が baseline より上 → ENTRY品質向上の証拠",
            "- total_pnl 改善は主に trade 削減で、MFE/継続率が横ばい → EXIT/ゲート側の効果",
            "- 削除 trade の勝ちが多い → 利益機会も捨てている",
            "",
            "## 出力ファイル",
            "",
            f"- `{report['meta'].get('csv_path', '')}`",
            f"- `{report['meta'].get('json_path', '')}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    repo_root, native_root = _bootstrap()

    from replay.entry_quality import (
        analyze_scenario,
        build_phase9_report,
        compare_removed_trades,
        metrics_to_csv_rows,
        phase9_scenarios,
        replay_cached_enriched,
    )
    from replay.runner import load_replay_config
    from replay.sweep_runner import build_event_cache

    parser = argparse.ArgumentParser(description="Phase 9 ENTRY quality analysis")
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
    csv_path = reports_dir / f"phase9_entry_quality_{report_date}.csv"
    json_path = reports_dir / f"phase9_entry_quality_{report_date}.json"
    doc_path = native_root / "docs" / "phase9_entry_quality.md"

    logging.info("building cache...")
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

    scenarios = phase9_scenarios()
    trade_lists: dict[str, list] = {}
    workers = max(1, int(args.workers))
    native_src = native_root / "src"

    if workers == 1:
        for params in scenarios:
            logging.info("replay %s", params.sweep_id)
            trade_lists[params.sweep_id] = replay_cached_enriched(
                cache,
                params,
                repo_root=repo_root,
                tier=str(cfg_raw.get("tier", "B")),
                entry_score_min=int(cfg_raw.get("entry_score_min", 60)),
                require_timing_ok=bool(cfg_raw.get("require_timing_ok", True)),
                relaxed_signal=bool(cfg_raw.get("relaxed_signal", False)),
            )
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
            futures = {pool.submit(_run_scenario, t): t["sweep_id"] for t in tasks}
            for fut in as_completed(futures):
                sid, rows = fut.result()
                logging.info("done %s (%d trades)", sid, len(rows))
                from replay.entry_quality import EnrichedTrade

                trade_lists[sid] = [
                    EnrichedTrade(
                        trade_date=r["trade_date"],
                        symbol=r["symbol"],
                        entry_time=datetime.fromisoformat(r["entry_time"]),
                        entry_price=float(r["entry_price"]),
                        exit_time=datetime.fromisoformat(r["exit_time"]),
                        exit_price=float(r["exit_price"]),
                        pnl_pct=float(r["pnl_pct"]),
                        exit_reason=r["exit_reason"],
                        mfe_pct=float(r["mfe_pct"]),
                        mae_pct=float(r["mae_pct"]),
                        elapsed_min=float(r["elapsed_min"]),
                        signal_score_at_entry=int(r["signal_score_at_entry"]),
                        mae_1m_pct=float(r["mae_1m_pct"]),
                        mae_3m_pct=float(r["mae_3m_pct"]),
                        mae_5m_pct=float(r["mae_5m_pct"]),
                        breakout_continued=bool(r["breakout_continued"]),
                        session_high_at_entry=0.0,
                        session_high_max=0.0,
                    )
                    for r in rows
                ]

    scenario_metrics = {
        sid: analyze_scenario(trades, sid) for sid, trades in trade_lists.items()
    }
    baseline_trades = trade_lists["baseline"]
    removal_comparisons = [
        compare_removed_trades(baseline_trades, trade_lists[cid], cid)
        for cid in ("candidate_a", "candidate_b", "candidate_c")
    ]

    report = build_phase9_report(
        scenario_metrics,
        removal_comparisons,
        meta={
            "start_date": args.start_date,
            "end_date": args.end_date,
            "universe": str(args.universe.resolve()),
            "symbol_count": len(symbols),
            "cached_symbol_days": len(cache),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "csv_path": str(csv_path),
            "json_path": str(json_path),
        },
    )
    report["trade_details"] = {
        sid: [t.to_dict() for t in trades] for sid, trades in trade_lists.items()
    }

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    csv_rows = metrics_to_csv_rows(scenario_metrics)
    for comp in removal_comparisons:
        csv_rows.append({"row_type": "removal", **comp})

    if csv_rows:
        keys: list[str] = []
        for row in csv_rows:
            for k in row:
                if k not in keys:
                    keys.append(k)
        with csv_path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            w.writerows(csv_rows)

    _write_doc(doc_path, report)
    logging.info("wrote %s", csv_path)
    logging.info("wrote %s", json_path)
    logging.info("wrote %s", doc_path)
    logging.info("verdict: %s", report["verdict"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
