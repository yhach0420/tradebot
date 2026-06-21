"""
Phase468 — Frozen Trend Exit Audit (research only).

Exit-only comparison on Phase465B T4 accepted trades (frozen set).
No new entries, no CAP recalculation, no additional candidates.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _parse_ts, _position_key
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase428_no_progress_tightening_sweep import simulate_tightening_no_progress_exit
from research.phase441_boundary_no_progress_overlap_audit import BEST_NP_POLICY
from research.phase443_full_runtime_combined_capital_sim import simulate_capacity_replay
from research.phase451_entry_shape_tournament import (
    PERIOD_END,
    PERIOD_START,
    _build_price_index_to,
    _now_iso,
)
from research.phase459_winner_pattern_audit import _stop_rate_from_log
from research.phase463_trend_pullback_population_tournament import (
    _fill_close_proxy_shadows,
    _filter_replay_pool,
)
from research.phase465b_trend_gate_redesign import _gate_t4, _make_trend_only
from research.phase467_trend_exit_audit import (
    _entry_block,
    _prepare_forward_context_price_idx,
    _simulate_hard_stop_only,
    _simulate_high_update_stall,
    _simulate_mfe_giveback,
    _simulate_vwap_break,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

JST = ZoneInfo("Asia/Tokyo")
TREND_ENTRY_FN = _make_trend_only(_gate_t4)

EXIT_SPECS: dict[str, tuple[str, str]] = {
    "A": ("runtime", "Hard Stop → No Progress → Board Dynamic Trailing"),
    "B": ("session_hold", "Hard Stop only → session close"),
    "C": ("trend_hold", "High-update stall exit"),
    "D": ("vwap_break", "Price < VWAP exit"),
    "E": ("trend_trailing", "MFE giveback 20%"),
}

SUMMARY_FIELDS = [
    "exit_variant",
    "exit_label",
    "trade_count",
    "total_pnl_yen",
    "profit_factor",
    "max_drawdown_yen",
    "stop_rate",
    "avg_hold_sec",
    "delta_pnl_vs_A",
    "symbol_pnl_6976",
    "symbol_pnl_4062",
]

TRADE_FIELDS = [
    "exit_variant",
    "symbol",
    "entry_time",
    "exit_time",
    "exit_reason",
    "pnl_yen",
    "hold_sec",
    "delta_pnl_vs_A_trade",
]


def _float(val: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if val is None or val == "":
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


def _simulate_frozen_exit(ctx: Mapping[str, Any], variant: str) -> dict[str, Any]:
    states = ctx["tick_states"]
    entry_price = float(ctx["entry_price"])
    entry_ts = float(ctx["entry_ts"])
    if variant == "A":
        return simulate_tightening_no_progress_exit(
            states,
            entry_price=entry_price,
            entry_ts=entry_ts,
            imb_pct=ctx.get("imb_pct"),
            policy=BEST_NP_POLICY,
        )
    if variant == "B":
        return _simulate_hard_stop_only(states, entry_price=entry_price, entry_ts=entry_ts)
    if variant == "C":
        return _simulate_high_update_stall(states, entry_price=entry_price, entry_ts=entry_ts)
    if variant == "D":
        return _simulate_vwap_break(states, entry_price=entry_price, entry_ts=entry_ts)
    if variant == "E":
        return _simulate_mfe_giveback(states, entry_price=entry_price, entry_ts=entry_ts, giveback_frac=0.20)
    raise ValueError(f"unknown variant {variant}")


def _load_replay_pool(reports: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import pickle

    path = reports / ".phase463_cache" / "population.pkl"
    with path.open("rb") as fh:
        payload = pickle.load(fh)
    return list(payload["replay_pool"]), dict(payload.get("np_shadows") or {})


def _frozen_t4_trades(
    replay_pool: Sequence[Mapping[str, Any]],
    runtime_shadows: Mapping[str, Any],
) -> list[dict[str, Any]]:
    state = simulate_capacity_replay(
        replay_pool,
        runtime_shadows,
        mode="phase468_frozen_t4",
        entry_block_fn=_entry_block(TREND_ENTRY_FN),
        baseline_accepted_keys=set(),
    )
    return [dict(r.get("trade") or r) for r in state.trade_log]


def _chronological_pnls(rows: Sequence[Mapping[str, Any]]) -> list[float]:
    ordered = sorted(
        rows,
        key=lambda r: (
            _parse_ts(str(r.get("exit_time") or "")) or datetime.min.replace(tzinfo=JST),
            str(r.get("symbol") or ""),
        ),
    )
    return [float(r.get("pnl_yen") or 0) for r in ordered]


def _symbol_pnl(rows: Sequence[Mapping[str, Any]], sym: str) -> float:
    code = sym.replace(".T", "")
    total = 0.0
    for r in rows:
        s = str(r.get("symbol") or "")
        if s.replace(".T", "") == code or s == sym:
            total += float(r.get("pnl_yen") or 0)
    return round(total, 2)


def _exit_only_rows(
    frozen: Sequence[Mapping[str, Any]],
    *,
    price_idx: Mapping[tuple[str, str], list[tuple[datetime, float]]],
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    by_variant: dict[str, list[dict[str, Any]]] = {v: [] for v in EXIT_SPECS}
    skipped = 0
    for trade in frozen:
        ctx = _prepare_forward_context_price_idx(dict(trade), price_idx=price_idx)
        if ctx is None:
            skipped += 1
            continue
        ent = _parse_ts(str(trade.get("entry_time") or ""))
        for variant in EXIT_SPECS:
            sim = _simulate_frozen_exit(ctx, variant)
            exit_ts = float(sim.get("shadow_exit_ts") or ctx["entry_ts"])
            ex_dt = datetime.fromtimestamp(exit_ts, tz=JST)
            hold = (ex_dt - ent).total_seconds() if ent else 0.0
            pnl = float(sim.get("shadow_pnl_yen_100") or 0)
            by_variant[variant].append(
                {
                    "exit_variant": variant,
                    "symbol": trade.get("symbol"),
                    "entry_time": trade.get("entry_time"),
                    "exit_time": ex_dt.isoformat(),
                    "exit_reason": sim.get("shadow_exit_reason"),
                    "pnl_yen": round(pnl, 2),
                    "hold_sec": round(hold, 2),
                }
            )
    if skipped:
        print(f"phase468 skipped (no ticks): {skipped}", flush=True)
    return [], by_variant


def _summary_row(
    variant: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    baseline_rows: Optional[Sequence[Mapping[str, Any]]] = None,
) -> dict[str, Any]:
    label, desc = EXIT_SPECS[variant]
    chron = _chronological_pnls(rows)
    holds = [_float(r.get("hold_sec")) or 0.0 for r in rows]
    base_pnl = float(sum(_chronological_pnls(baseline_rows))) if baseline_rows else 0.0
    return {
        "exit_variant": variant,
        "exit_label": desc,
        "trade_count": len(rows),
        "total_pnl_yen": round(sum(chron), 2),
        "profit_factor": _pf(chron),
        "max_drawdown_yen": _max_drawdown_yen(chron) if chron else 0.0,
        "stop_rate": _stop_rate_from_log([{"exit_reason": r.get("exit_reason")} for r in rows]),
        "avg_hold_sec": round(statistics.mean(holds), 2) if holds else 0.0,
        "delta_pnl_vs_A": round(sum(chron) - base_pnl, 2),
        "symbol_pnl_6976": _symbol_pnl(rows, "6976"),
        "symbol_pnl_4062": _symbol_pnl(rows, "4062"),
    }


def _verdict(
    *,
    row_a: Mapping[str, Any],
    best_row: Mapping[str, Any],
) -> str:
    a_pnl = float(row_a.get("total_pnl_yen") or 0)
    best_pnl = float(best_row.get("total_pnl_yen") or 0)
    best_pf = float(best_row.get("profit_factor") or 0)
    best_var = str(best_row.get("exit_variant") or "A")
    improved = best_pnl - a_pnl

    if best_pnl > 0 and best_pf >= 1.0 and best_var != "A":
        return "trend_exit_problem"
    if improved > 5000 and best_var != "A":
        return "trend_exit_problem"
    if best_pnl <= 0 and a_pnl <= 0 and abs(improved) < 3000:
        return "trend_no_edge"
    if best_var == "A" and best_pnl <= 0:
        return "trend_entry_problem"
    if best_pnl <= 0:
        return "trend_no_edge"
    return "trend_exit_problem"


def run_phase468(*, repo_root: Path) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    reports = resolve_reports_dir(repo_root)
    price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)

    replay_pool, runtime_shadows = _load_replay_pool(reports)
    runtime_shadows = _fill_close_proxy_shadows(replay_pool, runtime_shadows, price_idx=price_idx)
    replay_pool = _filter_replay_pool(replay_pool, runtime_shadows)

    frozen = _frozen_t4_trades(replay_pool, runtime_shadows)
    print(f"phase468 frozen T4 trades: {len(frozen)}", flush=True)

    _, by_variant = _exit_only_rows(frozen, price_idx=price_idx)
    rows_a = by_variant.get("A") or []
    summary_rows: list[dict[str, Any]] = []
    for variant in EXIT_SPECS:
        rows = by_variant.get(variant) or []
        summary_rows.append(_summary_row(variant, rows, baseline_rows=rows_a))

    summary_rows.sort(key=lambda r: float(r.get("total_pnl_yen") or 0), reverse=True)
    row_a = next(r for r in summary_rows if r["exit_variant"] == "A")
    best_row = summary_rows[0]

    trade_rows: list[dict[str, Any]] = []
    pnl_a_by_key = {
        (_position_key({"symbol": r["symbol"], "entry_time": r["entry_time"]})): float(r["pnl_yen"])
        for r in rows_a
    }
    for variant, rows in by_variant.items():
        for r in rows:
            key = _position_key({"symbol": r["symbol"], "entry_time": r["entry_time"]})
            trade_rows.append(
                {
                    **r,
                    "delta_pnl_vs_A_trade": round(float(r["pnl_yen"]) - pnl_a_by_key.get(key, 0.0), 2),
                }
            )

    verdict = _verdict(row_a=row_a, best_row=best_row)
    a_pnl = float(row_a.get("total_pnl_yen") or 0)
    best_pnl = float(best_row.get("total_pnl_yen") or 0)
    exit_problem = best_pnl > a_pnl + 3000 and str(best_row.get("exit_variant")) != "A"
    entry_problem = best_pnl <= 0 and a_pnl <= 0

    mandatory = {
        "1_best_exit": f"{best_row.get('exit_variant')} ({EXIT_SPECS[best_row['exit_variant']][1]})",
        "2_pnl": best_row.get("total_pnl_yen"),
        "3_pf": best_row.get("profit_factor"),
        "4_maxdd": best_row.get("max_drawdown_yen"),
        "5_runtime_delta": round(best_pnl - a_pnl, 2),
        "6_6976_impact": {"A": row_a.get("symbol_pnl_6976"), "best": best_row.get("symbol_pnl_6976")},
        "7_4062_impact": {"A": row_a.get("symbol_pnl_4062"), "best": best_row.get("symbol_pnl_4062")},
        "8_trend_edge_is_exit_problem": exit_problem,
        "9_trend_edge_is_entry_problem": entry_problem and not exit_problem,
        "10_runtime_candidate": exit_problem and float(best_row.get("profit_factor") or 0) >= 1.0,
        "verdict": verdict,
        "frozen_trade_count": len(frozen),
        "tick_sim_trade_count": len(rows_a),
    }

    return {
        "generated_at": _now_iso(),
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "verdict": verdict,
        "mandatory_answers": mandatory,
        "frozen_trades": [
            {"symbol": t.get("symbol"), "entry_time": t.get("entry_time"), "day": t.get("day")} for t in frozen
        ],
        "_summary_rows": summary_rows,
        "_trade_rows": trade_rows,
    }


@dataclass
class Phase468Job:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase468(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        base = reports / "phase468_frozen_trend_exit_audit"
        paths = {
            "summary_csv": Path(f"{base}.csv"),
            "trades_csv": Path(f"{base}_trades.csv"),
            "summary": Path(f"{base}.json"),
        }
        _write_csv(paths["summary_csv"], SUMMARY_FIELDS, list(result.get("_summary_rows") or []))
        _write_csv(paths["trades_csv"], TRADE_FIELDS, list(result.get("_trade_rows") or []))
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        doc_root = self.repo_root / "kabu_native"
        if not (doc_root / "docs").is_dir():
            doc_root = self.repo_root
        report = doc_root / "docs" / "operations" / "phase468_frozen_trend_exit_audit.md"
        m = result.get("mandatory_answers") or {}
        summaries = list(result.get("_summary_rows") or [])
        lines = [
            "# Phase468 — Frozen Trend Exit Audit",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            f"Frozen T4 accepted: **{m.get('frozen_trade_count')}** trades (exit-only, no CAP)",
            "",
            "## Comparison",
            "",
            "| var | label | PnL | PF | maxDD | Δ vs A |",
            "|---|---|---:|---:|---:|---:|",
        ]
        for r in sorted(summaries, key=lambda x: x.get("exit_variant", "")):
            lines.append(
                f"| {r.get('exit_variant')} | {r.get('exit_label')} | {r.get('total_pnl_yen')} "
                f"| {r.get('profit_factor')} | {r.get('max_drawdown_yen')} | {r.get('delta_pnl_vs_A')} |"
            )
        lines.extend(
            [
                "",
                f"Best: **{m.get('1_best_exit')}**",
                f"Exit problem: **{m.get('8_trend_edge_is_exit_problem')}**",
                f"Entry problem: **{m.get('9_trend_edge_is_entry_problem')}**",
            ]
        )
        report.write_text("\n".join(lines), encoding="utf-8")
        paths["report"] = report
        return paths
