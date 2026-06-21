"""
Phase452A — Weak Shape Fidelity Audit (research only).

Compare Phase451B EOD E_weak_shape_reject vs Phase452 Runtime weak_shape_reject
on 20260529–20260619, Board mid+high + High Drift eval pool.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _pf, _write_csv
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase436_pullback_guard_redesign_shadow import guard_high_drift
from research.phase441_boundary_no_progress_overlap_audit import BEST_NP_POLICY, _precompute_np_shadows
from research.phase443_full_runtime_combined_capital_sim import (
    _chronological_pnls_from_log,
    simulate_capacity_replay,
)
from research.phase451_entry_shape_tournament import (
    DAY_618,
    DAY_619,
    PERIOD_END,
    PERIOD_START,
    TARGET_SYMBOLS,
    _enrich_candidates,
    _guard_e_weak_shape,
    _load_candidate_stream,
    _metrics_from_state,
    _now_iso,
    _symbol_pnl_from_log,
)
from research.phase451b_entry_shape_tournament_mid_high import (
    _passes_baseline_mid_high,
    _runtime_entry_block_mid_high,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.weak_shape_reject_entry_guard import (
    classify_intraday_weak_shape,
    would_block_weak_shape_reject,
)

JST = ZoneInfo("Asia/Tokyo")

AUDIT_FIELDS = [
    "symbol",
    "day",
    "entry_time",
    "eod_shape_class",
    "eod_reject",
    "runtime_shape_class",
    "runtime_reject",
    "agreement",
    "day_high_minutes_from_open",
    "minutes_since_day_high_update",
    "day_high_distance_pct",
    "entry_rise_15min_pct",
    "pnl_yen",
]


def _trade_key(trade: Mapping[str, Any]) -> tuple[str, str]:
    return (str(trade.get("symbol") or ""), str(trade.get("entry_time") or ""))


def _map_runtime_fields(trade: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(trade)
    for src, dst in (
        ("return_5min_pct", "entry_rise_5min_pct"),
        ("return_10min_pct", "entry_rise_10min_pct"),
        ("return_15min_pct", "entry_rise_15min_pct"),
        ("return_30min_pct", "entry_rise_30min_pct"),
    ):
        if out.get(dst) is None and out.get(src) is not None:
            out[dst] = out[src]
    return out


def _runtime_weak_shape_reject(trade: Mapping[str, Any]) -> bool:
    return would_block_weak_shape_reject(_map_runtime_fields(trade))


def _runtime_shape_class(trade: Mapping[str, Any]) -> str:
    return classify_intraday_weak_shape(_map_runtime_fields(trade)) or ""


def _eval_pool(enriched: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        dict(t)
        for t in enriched
        if _passes_baseline_mid_high(t) and not guard_high_drift(t)
    ]


def _confusion(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    eod_rejects = [r for r in rows if r.get("eod_reject")]
    rt_rejects = [r for r in rows if r.get("runtime_reject")]
    both = [r for r in rows if r.get("eod_reject") and r.get("runtime_reject")]
    eod_only = [r for r in rows if r.get("eod_reject") and not r.get("runtime_reject")]
    rt_only = [r for r in rows if r.get("runtime_reject") and not r.get("eod_reject")]
    eod_n = len(eod_rejects)
    rt_n = len(rt_rejects)
    agree_n = len(both)
    precision = round(agree_n / rt_n, 4) if rt_n else None
    recall = round(agree_n / eod_n, 4) if eod_n else None
    return {
        "eval_pool_count": len(rows),
        "eod_reject_count": eod_n,
        "runtime_reject_count": rt_n,
        "agreement_count": agree_n,
        "precision": precision,
        "recall": recall,
        "eod_only_reject_count": len(eod_only),
        "runtime_only_reject_count": len(rt_only),
        "eod_only_reject_pnl_yen": round(sum(float(r.get("pnl_yen") or 0) for r in eod_only), 2),
        "runtime_only_reject_pnl_yen": round(sum(float(r.get("pnl_yen") or 0) for r in rt_only), 2),
    }


def _fidelity_verdict(
    *,
    precision: Optional[float],
    recall: Optional[float],
    eod_only: int,
    runtime_only: int,
) -> str:
    if precision is None or recall is None:
        return "runtime_different"
    if precision >= 0.85 and recall >= 0.85 and eod_only <= 5 and runtime_only <= 5:
        return "runtime_equivalent"
    if recall < 0.70 or (runtime_only < eod_only and recall < 0.85):
        return "runtime_weaker"
    return "runtime_different"


def _replay_metrics(
    enriched: Sequence[Mapping[str, Any]],
    np_shadows: Mapping[str, Any],
    *,
    shape_guard: Optional[Callable[[Mapping[str, Any]], bool]],
    variant: str,
) -> dict[str, Any]:
    state = simulate_capacity_replay(
        enriched,
        np_shadows,
        mode=variant,
        entry_block_fn=_runtime_entry_block_mid_high(shape_guard),
        baseline_accepted_keys=set(),
    )
    chron = _chronological_pnls_from_log(state.trade_log)
    sym = _symbol_pnl_from_log(state.trade_log)
    return {
        "variant": variant,
        "total_pnl_yen": round(sum(chron), 2),
        "profit_factor": _pf(chron),
        "max_drawdown_yen": _max_drawdown_yen(chron) if chron else 0.0,
        "accepted_count": state.accepted_trade_count,
        "symbol_pnl_6976": sym.get("6976", 0.0),
        "symbol_pnl_6920": sym.get("6920", 0.0),
        "symbol_pnl_4062": sym.get("4062", 0.0),
        "daily_pnl_618": round(float(state.daily_pnls.get(DAY_618, 0.0)), 2),
        "daily_pnl_619": round(float(state.daily_pnls.get(DAY_619, 0.0)), 2),
    }


def run_phase452a_audit(*, repo_root: Path) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    candidates = _load_candidate_stream(repo_root)
    enriched = _enrich_candidates(candidates, kabu=kabu)
    np_shadows = _precompute_np_shadows(enriched, kabu=kabu, np_policy=BEST_NP_POLICY)
    pool = _eval_pool(enriched)

    pnl_by_key: dict[tuple[str, str], float] = {}
    for t in enriched:
        pnl_by_key[_trade_key(t)] = float(t.get("pnl_yen") or 0.0)

    audit_rows: list[dict[str, Any]] = []
    for t in pool:
        eod_rej = _guard_e_weak_shape(t)
        rt_rej = _runtime_weak_shape_reject(t)
        audit_rows.append(
            {
                "symbol": t.get("symbol"),
                "day": t.get("day"),
                "entry_time": t.get("entry_time"),
                "eod_shape_class": t.get("eod_shape_class"),
                "eod_reject": eod_rej,
                "runtime_shape_class": _runtime_shape_class(t),
                "runtime_reject": rt_rej,
                "agreement": eod_rej == rt_rej,
                "day_high_minutes_from_open": t.get("day_high_minutes_from_open"),
                "minutes_since_day_high_update": t.get("minutes_since_day_high_update"),
                "day_high_distance_pct": t.get("day_high_distance_pct"),
                "entry_rise_15min_pct": _map_runtime_fields(t).get("entry_rise_15min_pct"),
                "pnl_yen": pnl_by_key.get(_trade_key(t), 0.0),
            }
        )

    confusion = _confusion(audit_rows)

    baseline = _replay_metrics(enriched, np_shadows, shape_guard=None, variant="baseline_mid_high_hd")
    eod_e = _replay_metrics(enriched, np_shadows, shape_guard=_guard_e_weak_shape, variant="eod_weak_shape")
    runtime_e = _replay_metrics(
        enriched,
        np_shadows,
        shape_guard=_runtime_weak_shape_reject,
        variant="runtime_weak_shape",
    )

    pnl_delta = round(float(runtime_e["total_pnl_yen"]) - float(eod_e["total_pnl_yen"]), 2)
    p6976_delta = round(float(runtime_e["symbol_pnl_6976"]) - float(eod_e["symbol_pnl_6976"]), 2)

    verdict = _fidelity_verdict(
        precision=confusion.get("precision"),
        recall=confusion.get("recall"),
        eod_only=int(confusion.get("eod_only_reject_count") or 0),
        runtime_only=int(confusion.get("runtime_only_reject_count") or 0),
    )

    mandatory = {
        "1_eod_reject_count": confusion["eod_reject_count"],
        "2_runtime_reject_count": confusion["runtime_reject_count"],
        "3_agreement_count": confusion["agreement_count"],
        "4_precision": confusion["precision"],
        "5_recall": confusion["recall"],
        "6_eod_only_reject_count": confusion["eod_only_reject_count"],
        "7_runtime_only_reject_count": confusion["runtime_only_reject_count"],
        "8_pnl_delta_runtime_minus_eod_yen": pnl_delta,
        "9_symbol_6976_delta_runtime_minus_eod_yen": p6976_delta,
        "10_equivalent_verdict": verdict,
    }

    return {
        "generated_at": _now_iso(),
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "eval_pool_definition": "Momentum:low + (Board:mid OR Board:high) + NOT high_drift",
        "confusion": confusion,
        "replay": {
            "baseline_mid_high_hd": baseline,
            "eod_weak_shape": eod_e,
            "runtime_weak_shape": runtime_e,
            "pnl_delta_runtime_minus_eod_yen": pnl_delta,
            "symbol_6976_delta_runtime_minus_eod_yen": p6976_delta,
        },
        "verdict": verdict,
        "mandatory_answers": mandatory,
        "audit_rows": audit_rows,
        "eod_only_rows": [r for r in audit_rows if r["eod_reject"] and not r["runtime_reject"]],
        "runtime_only_rows": [r for r in audit_rows if r["runtime_reject"] and not r["eod_reject"]],
    }


@dataclass
class Phase452AJob:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase452a_audit(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        summary_path = reports / "phase452a_weak_shape_fidelity_summary.json"
        detail_path = reports / "phase452a_weak_shape_fidelity_detail.csv"
        eod_only_path = reports / "phase452a_weak_shape_eod_only.csv"
        rt_only_path = reports / "phase452a_weak_shape_runtime_only.csv"

        payload = {k: v for k, v in result.items() if k not in ("audit_rows", "eod_only_rows", "runtime_only_rows")}
        summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        _write_csv(detail_path, AUDIT_FIELDS, list(result.get("audit_rows") or []))
        _write_csv(eod_only_path, AUDIT_FIELDS, list(result.get("eod_only_rows") or []))
        _write_csv(rt_only_path, AUDIT_FIELDS, list(result.get("runtime_only_rows") or []))

        doc = self.repo_root / "kabu_native" / "docs" / "operations" / "phase452a_weak_shape_fidelity_report.md"
        if not doc.parent.is_dir():
            doc = self.repo_root / "docs" / "operations" / "phase452a_weak_shape_fidelity_report.md"
        doc.parent.mkdir(parents=True, exist_ok=True)
        m = result.get("mandatory_answers") or {}
        c = result.get("confusion") or {}
        r = result.get("replay") or {}
        doc.write_text(
            "\n".join(
                [
                    "# Phase452A — Weak Shape Fidelity Audit",
                    "",
                    f"Generated: {result.get('generated_at')}",
                    f"Period: {result.get('period_start')}..{result.get('period_end')}",
                    f"Eval pool: {result.get('eval_pool_definition')}",
                    "",
                    "## Mandatory answers",
                    "",
                    f"1. EOD reject count: **{m.get('1_eod_reject_count')}**",
                    f"2. Runtime reject count: **{m.get('2_runtime_reject_count')}**",
                    f"3. Agreement count: **{m.get('3_agreement_count')}**",
                    f"4. Precision: **{m.get('4_precision')}**",
                    f"5. Recall: **{m.get('5_recall')}**",
                    f"6. EOD-only rejects: **{m.get('6_eod_only_reject_count')}**",
                    f"7. Runtime-only rejects: **{m.get('7_runtime_only_reject_count')}**",
                    f"8. PnL delta (Runtime − EOD): **{m.get('8_pnl_delta_runtime_minus_eod_yen')}** yen",
                    f"9. 6976 delta (Runtime − EOD): **{m.get('9_symbol_6976_delta_runtime_minus_eod_yen')}** yen",
                    f"10. Verdict: **`{m.get('10_equivalent_verdict')}`**",
                    "",
                    "## Confusion (eval pool n={})".format(c.get("eval_pool_count")),
                    "",
                    "| Metric | Value |",
                    "|--------|-------|",
                    f"| EOD reject | {c.get('eod_reject_count')} |",
                    f"| Runtime reject | {c.get('runtime_reject_count')} |",
                    f"| Agreement | {c.get('agreement_count')} |",
                    f"| Precision | {c.get('precision')} |",
                    f"| Recall | {c.get('recall')} |",
                    f"| EOD-only | {c.get('eod_only_reject_count')} (PnL {c.get('eod_only_reject_pnl_yen')} yen) |",
                    f"| Runtime-only | {c.get('runtime_only_reject_count')} (PnL {c.get('runtime_only_reject_pnl_yen')} yen) |",
                    "",
                    "## CAP5 replay",
                    "",
                    f"| Variant | PnL | PF | Accepted | 6976 |",
                    f"|---------|-----|-----|----------|------|",
                    f"| Baseline (no weak shape) | {r.get('baseline_mid_high_hd', {}).get('total_pnl_yen')} | {r.get('baseline_mid_high_hd', {}).get('profit_factor')} | {r.get('baseline_mid_high_hd', {}).get('accepted_count')} | {r.get('baseline_mid_high_hd', {}).get('symbol_pnl_6976')} |",
                    f"| EOD E_weak_shape | {r.get('eod_weak_shape', {}).get('total_pnl_yen')} | {r.get('eod_weak_shape', {}).get('profit_factor')} | {r.get('eod_weak_shape', {}).get('accepted_count')} | {r.get('eod_weak_shape', {}).get('symbol_pnl_6976')} |",
                    f"| Runtime weak_shape | {r.get('runtime_weak_shape', {}).get('total_pnl_yen')} | {r.get('runtime_weak_shape', {}).get('profit_factor')} | {r.get('runtime_weak_shape', {}).get('accepted_count')} | {r.get('runtime_weak_shape', {}).get('symbol_pnl_6976')} |",
                    "",
                    "## EOD vs Runtime definition delta",
                    "",
                    "- **EOD (451B E):** `eod_shape_class` in (`opening_peak`, `slow_opening_peak`); `uptrend` passes.",
                    "- **Runtime (452):** Intraday timing + pullback at ENTRY; uptrend pass via recent high update or r10/r15/r30.",
                    "",
                    "Outputs:",
                    f"- `{summary_path.name}`",
                    f"- `{detail_path.name}`",
                    f"- `{eod_only_path.name}`",
                    f"- `{rt_only_path.name}`",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return {
            "summary": summary_path,
            "detail": detail_path,
            "eod_only": eod_only_path,
            "runtime_only": rt_only_path,
            "report": doc,
        }
