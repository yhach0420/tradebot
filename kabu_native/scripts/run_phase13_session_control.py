#!/usr/bin/env python3
"""
Phase 13: market session control vs baseline / BF confirm=2.

例::
    python kabu_native/scripts/run_phase13_session_control.py \\
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


def _write_doc(path: Path, report: dict[str, Any]) -> None:
    rows = {r["sweep_id"]: r for r in report["rows"]}
    v = report["verdict"]
    meta = report["meta"]

    def row(sid: str) -> dict:
        return rows.get(sid, {})

    lines = [
        "# Phase 13: 市場セッション制御",
        "",
        "## 目的",
        "",
        "`no_entry_until`（09:30 禁止等の時間最適化）を廃止し、",
        "**市場制度・板安定化** に基づく ENTRY 枠へ整理する。",
        "",
        "正式ルール（JST）: **ENTRY 09:05 〜 14:50 未満**。14:50 以降は新規 ENTRY 不可。",
        "詳細: [market_session_control.md](market_session_control.md)",
        "",
        f"期間: {meta['start_date']} 〜 {meta['end_date']}（{meta['symbol_count']} 銘柄）",
        "",
        "## 比較結果",
        "",
        "| scenario | session | bf_confirm | trades | total_pnl | avg_pnl | PF |",
        "|----------|---------|------------|--------|-----------|---------|-----|",
    ]
    for sid, sess in (
        ("baseline", "off"),
        ("B_bf_confirm_2", "off"),
        ("market_session_plus_B", "on"),
    ):
        r = row(sid)
        lines.append(
            f"| {sid} | {sess} | {r.get('bf_confirm_count', '')} | {r.get('trades', '')} | "
            f"{r.get('total_pnl_pct', 0):.2f}% | {r.get('avg_pnl_pct', 0):.3f}% | "
            f"{r.get('profit_factor') or 0:.3f} |"
        )

    lines.extend(
        [
            "",
            "## 判定",
            "",
            f"- session+B が baseline より改善: **{v.get('session_beats_baseline')}**",
            f"- session+B が B 単独より改善: **{v.get('session_beats_B')}**",
            f"- 性能が baseline より **悪化していない**: **{v.get('not_worse_than_baseline')}**",
            f"- 旧 09:30 ゲート (Phase10 A+B 参考): total_pnl **{meta.get('phase10_A_plus_B_pnl', '—')}%**",
            f"- 推奨 shadow ルール: **{v.get('recommended_rule')}**",
            "",
            v.get("note", ""),
            "",
            "## 出力",
            "",
            f"- `{meta.get('csv_path', '')}`",
            f"- `{meta.get('json_path', '')}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _build_verdict(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by = {r["sweep_id"]: r for r in rows}
    base = by.get("baseline", {})
    b = by.get("B_bf_confirm_2", {})
    sess = by.get("market_session_plus_B", {})

    def pnl(r: dict) -> float:
        return float(r.get("total_pnl_pct") or 0)

    session_beats_baseline = pnl(sess) > pnl(base)
    session_beats_B = pnl(sess) > pnl(b)
    not_worse = pnl(sess) >= pnl(base) - 1.0

    if session_beats_B and not_worse:
        rec = "market_session_plus_B"
    elif pnl(b) >= pnl(sess):
        rec = "B_bf_confirm_2 (session optional)"
    else:
        rec = "market_session_plus_B"

    return {
        "session_beats_baseline": session_beats_baseline,
        "session_beats_B": session_beats_B,
        "not_worse_than_baseline": not_worse,
        "recommended_rule": rec,
        "note": (
            "09:30 no_entry_until は廃止。session 枠は構造理由のみ "
            "(configs/session_control.yaml)。"
        ),
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    repo_root, native_root = _bootstrap()

    from replay.combined_candidates import phase13_scenarios
    from replay.combined_candidates import summarize_phase10
    from replay.entry_quality import replay_cached_enriched
    from replay.runner import load_replay_config
    from replay.sweep_runner import build_event_cache

    parser = argparse.ArgumentParser(description="Phase 13 session control")
    parser.add_argument("--start-date", default="2026-04-10")
    parser.add_argument("--end-date", default="2026-05-15")
    parser.add_argument(
        "--universe",
        type=Path,
        default=native_root / "data" / "universe" / "universe_intraday_full.csv",
    )
    parser.add_argument("--report-date", default=None)
    parser.add_argument("--workers", type=int, default=3)
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
    csv_path = reports_dir / f"phase13_session_control_{report_date}.csv"
    json_path = reports_dir / f"phase13_session_control_{report_date}.json"
    doc_path = native_root / "docs" / "phase13_session_control.md"

    phase10_ref = None
    p10 = reports_dir / "phase10_combined_candidates_20260517.json"
    if p10.is_file():
        try:
            raw = json.loads(p10.read_text(encoding="utf-8"))
            for r in raw.get("rows", []):
                if r.get("sweep_id") == "A_plus_B":
                    phase10_ref = float(r.get("total_pnl_pct", 0))
        except Exception:
            pass

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

    scenarios = phase13_scenarios()
    rows: list[dict[str, Any]] = []
    workers = max(1, int(args.workers))
    native_src = native_root / "src"

    if workers == 1:
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
            rows.append(summarize_phase10(trades, params))
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

    verdict = _build_verdict(rows)
    report = {
        "meta": {
            "start_date": args.start_date,
            "end_date": args.end_date,
            "universe": str(args.universe.resolve()),
            "symbol_count": len(symbols),
            "session_entry_start_jst": "09:05",
            "session_entry_end_jst": "14:50",
            "no_entry_until_abolished": True,
            "phase10_A_plus_B_pnl": phase10_ref,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "csv_path": str(csv_path),
            "json_path": str(json_path),
        },
        "rows": rows,
        "verdict": verdict,
    }

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    fieldnames = [
        "sweep_id",
        "market_session_control",
        "bf_confirm_count",
        "fail_buffer_pct",
        "trades",
        "win_rate",
        "total_pnl_pct",
        "avg_pnl_pct",
        "median_pnl_pct",
        "max_loss_pct",
        "profit_factor",
        "mfe_reach_0.3pct",
        "breakout_continuation_rate",
        "median_hold_min",
        "symbols_with_trades",
        "pnl_concentration_top_symbol",
        "pnl_concentration_top_share",
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
    logging.info("verdict: %s", verdict)
    logging.info("wrote %s", doc_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
