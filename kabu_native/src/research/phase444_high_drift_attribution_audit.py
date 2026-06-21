"""
Phase444 — High Drift attribution audit.

Decomposes Phase443 B−A (+75,899 yen) into:
  A) loss avoidance (blocked baseline-accepted losers)
  B) additional profit (CAP / buying-power freed accepts)

Research only — no Runtime/YAML/Entry/Exit/Order/Discord changes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _pf, _win_rate, _write_csv
from research.phase271_leverage_attribution_and_robustness import simulate_audited
from research.phase382_capital_constrained_backtest import _day_from_ts, _parse_ts, _position_key
from research.phase436_pullback_guard_redesign_shadow import guard_high_drift
from research.phase443_full_runtime_combined_capital_sim import (
    CAP,
    HIGH_DRIFT_REJECT_REASON,
    LEVERAGE,
    PERIOD_END,
    PERIOD_START,
    STARTING_EQUITY,
    STOP_POLICY,
    TARGET_LOSS_DAY,
    _candidate_pnl_yen,
    _enrich_candidates,
    _load_candidate_stream,
    simulate_capacity_replay,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

JST = ZoneInfo("Asia/Tokyo")

TARGET_SYMBOLS_618 = ("6976.T", "3110.T", "6981.T")

REJECT_FIELDS = [
    "symbol",
    "entry_time",
    "day",
    "would_pnl",
    "actual_reject_reason",
    "baseline_accepted",
    "baseline_pnl_yen",
]

ADDED_FIELDS = [
    "symbol",
    "entry_time",
    "day",
    "exit_time",
    "pnl_yen",
    "exit_reason",
    "reject_reason_baseline",
    "from_cap_reject",
    "from_buying_power_reject",
]

def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _trade_by_key(candidates: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    return {_position_key(t): dict(t) for t in candidates}


def _reject_rows(
    hd_state: Any,
    *,
    trade_by_key: Mapping[str, Mapping[str, Any]],
    baseline_keys: set[str],
    baseline_pnls: Mapping[str, float],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rej in hd_state.reject_log or []:
        if str(rej.get("reason") or "") != HIGH_DRIFT_REJECT_REASON:
            continue
        key = str(rej.get("key") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        trade = trade_by_key.get(key, {})
        would = _candidate_pnl_yen(trade)
        entry_time = str(trade.get("entry_time") or "")
        rows.append(
            {
                "symbol": trade.get("symbol"),
                "entry_time": entry_time,
                "day": _day_from_ts(entry_time) if entry_time else "",
                "would_pnl": round(would, 2),
                "actual_reject_reason": HIGH_DRIFT_REJECT_REASON,
                "baseline_accepted": key in baseline_keys,
                "baseline_pnl_yen": round(float(baseline_pnls.get(key, 0.0)), 2) if key in baseline_keys else "",
            }
        )
    rows.sort(key=lambda r: (str(r.get("entry_time") or ""), str(r.get("symbol") or "")))
    return rows


def _reject_aggregates(reject_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    pnls = [float(r.get("would_pnl") or 0.0) for r in reject_rows]
    return {
        "reject_count": len(reject_rows),
        "reject_pnl_sum": round(sum(pnls), 2),
        "reject_win_rate": _win_rate(pnls),
        "reject_pf": _pf(pnls),
    }


def _added_rows(
    hd_state: Any,
    *,
    added_keys: set[str],
    baseline_reject_log: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in sorted(added_keys):
        row = next(r for r in hd_state.trade_log if _position_key(r.get("trade") or {}) == key)
        trade_obj = row.get("trade") or {}
        entry_time = str(row.get("entry_time") or "")
        rej = baseline_reject_log.get(key, {})
        reason = str(rej.get("reason") or "")
        rows.append(
            {
                "symbol": row.get("symbol"),
                "entry_time": entry_time,
                "day": _day_from_ts(entry_time) if entry_time else "",
                "exit_time": row.get("exit_time"),
                "pnl_yen": row.get("pnl_yen"),
                "exit_reason": row.get("exit_reason"),
                "reject_reason_baseline": reason,
                "from_cap_reject": reason == "max_concurrent_positions",
                "from_buying_power_reject": reason == "insufficient_buying_power",
            }
        )
    return rows


def _decompose_improvement(
    *,
    baseline_keys: set[str],
    hd_keys: set[str],
    baseline_pnls: Mapping[str, float],
    hd_pnls: Mapping[str, float],
    total_delta: float,
) -> dict[str, Any]:
    only_baseline = baseline_keys - hd_keys
    only_hd = hd_keys - baseline_keys
    both = baseline_keys & hd_keys

    removed_pnl = sum(float(baseline_pnls.get(k, 0.0)) for k in only_baseline)
    added_pnl = sum(float(hd_pnls.get(k, 0.0)) for k in only_hd)
    sizing_delta = sum(float(hd_pnls.get(k, 0.0)) - float(baseline_pnls.get(k, 0.0)) for k in both)

    loss_avoided = round(-removed_pnl, 2)
    additional_profit = round(added_pnl, 2)
    path_interaction = round(total_delta - loss_avoided - additional_profit, 2)

    if abs(total_delta) < 1e-6:
        loss_ratio = 0.0
        add_ratio = 0.0
    else:
        loss_ratio = round(loss_avoided / total_delta, 4)
        add_ratio = round(additional_profit / total_delta, 4)

    return {
        "only_baseline_count": len(only_baseline),
        "only_hd_count": len(only_hd),
        "both_count": len(both),
        "removed_baseline_pnl_sum": round(removed_pnl, 2),
        "added_hd_pnl_sum": round(added_pnl, 2),
        "sizing_path_delta": sizing_delta,
        "loss_avoided": loss_avoided,
        "additional_profit": additional_profit,
        "path_interaction": path_interaction,
        "loss_avoided_ratio": loss_ratio,
        "additional_profit_ratio": add_ratio,
        "reconciliation_delta": round(loss_avoided + additional_profit + path_interaction, 2),
    }


def _day_symbol_analysis(
    *,
    day: str,
    baseline_keys: set[str],
    hd_keys: set[str],
    baseline_pnls: Mapping[str, float],
    hd_pnls: Mapping[str, float],
    trade_by_key: Mapping[str, Mapping[str, Any]],
    added_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    only_baseline = baseline_keys - hd_keys
    only_hd = hd_keys - baseline_keys

    baseline_day_pnl = sum(
        float(baseline_pnls.get(k, 0.0))
        for k in baseline_keys
        if _day_from_ts(str((trade_by_key.get(k) or {}).get("entry_time") or "")) == day
    )
    hd_day_pnl = sum(
        float(hd_pnls.get(k, 0.0))
        for k in hd_keys
        if _day_from_ts(str((trade_by_key.get(k) or {}).get("entry_time") or "")) == day
    )
    day_delta = round(hd_day_pnl - baseline_day_pnl, 2)

    symbol_rows: list[dict[str, Any]] = []
    for sym in TARGET_SYMBOLS_618:
        removed = [
            k
            for k in only_baseline
            if str((trade_by_key.get(k) or {}).get("symbol") or "") == sym
            and _day_from_ts(str((trade_by_key.get(k) or {}).get("entry_time") or "")) == day
        ]
        added = [
            k
            for k in only_hd
            if str((trade_by_key.get(k) or {}).get("symbol") or "") == sym
            and _day_from_ts(str((trade_by_key.get(k) or {}).get("entry_time") or "")) == day
        ]
        loss_avoid = round(-sum(float(baseline_pnls.get(k, 0.0)) for k in removed), 2)
        add_profit = round(sum(float(hd_pnls.get(k, 0.0)) for k in added), 2)
        sym_delta = round(loss_avoid + add_profit, 2)
        share = round(sym_delta / day_delta, 4) if abs(day_delta) > 1e-6 else 0.0
        symbol_rows.append(
            {
                "symbol": sym,
                "removed_count": len(removed),
                "added_count": len(added),
                "loss_avoided_yen": loss_avoid,
                "additional_profit_yen": add_profit,
                "net_contribution_yen": sym_delta,
                "contribution_share_of_day_delta": share,
            }
        )

    added_day = [r for r in added_rows if str(r.get("day") or "") == day]
    added_day_pnl = round(sum(float(r.get("pnl_yen") or 0.0) for r in added_day), 2)
    removed_day = [
        k
        for k in only_baseline
        if _day_from_ts(str((trade_by_key.get(k) or {}).get("entry_time") or "")) == day
    ]
    loss_avoid_day = round(-sum(float(baseline_pnls.get(k, 0.0)) for k in removed_day), 2)

    return {
        "day": day,
        "baseline_day_pnl": round(baseline_day_pnl, 2),
        "high_drift_day_pnl": round(hd_day_pnl, 2),
        "day_delta_yen": day_delta,
        "loss_avoided_day": loss_avoid_day,
        "additional_profit_day": added_day_pnl,
        "added_trade_count_day": len(added_day),
        "symbol_contributions": symbol_rows,
    }


def _verdict(*, loss_avoided: float, additional_profit: float, loss_ratio: float, add_ratio: float) -> str:
    if loss_ratio >= 0.6 and loss_avoided > additional_profit:
        return "high_drift_loss_avoidance"
    if add_ratio >= 0.6 and additional_profit > loss_avoided:
        return "high_drift_capacity_positive"
    return "high_drift_mixed"


def _essence_verdict(loss_ratio: float, add_ratio: float) -> str:
    if loss_ratio >= 0.55:
        return "損失回避型"
    if add_ratio >= 0.55:
        return "資金効率型"
    return "混合型"


def _next_research_theme(verdict: str, day_analysis: Mapping[str, Any]) -> str:
    sym = day_analysis.get("symbol_contributions") or []
    top = max(sym, key=lambda r: abs(float(r.get("net_contribution_yen") or 0.0)), default={})
    top_sym = top.get("symbol") or "6976.T"
    if verdict == "high_drift_loss_avoidance":
        return f"{top_sym} 型パターンの guard 閾値感度と forward 再現性"
    if verdict == "high_drift_capacity_positive":
        return "CAP解放で入った追加案件の entry 品質監査（sector / time-of-day）"
    return "損失回避とCAP解放の相互作用（NP併用時の再配分）"


def run_phase444_audit(*, repo_root: Path) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    candidates = _load_candidate_stream(repo_root)
    enriched = _enrich_candidates(candidates, kabu=kabu)
    trade_by_key = _trade_by_key(enriched)

    baseline_sim = simulate_audited(
        enriched,
        starting_equity=STARTING_EQUITY,
        leverage=LEVERAGE,
        cap=CAP,
        stop_policy=STOP_POLICY,
    )
    baseline_keys = set((baseline_sim.get("accepted_pnls") or {}).keys())
    baseline_pnls = {k: float(v) for k, v in (baseline_sim.get("accepted_pnls") or {}).items()}
    baseline_reject_log = {r["key"]: r for r in (baseline_sim.get("reject_log") or [])}

    hd_state = simulate_capacity_replay(
        enriched,
        {},
        mode="B_high_drift_only",
        entry_block_fn=guard_high_drift,
        baseline_accepted_keys=baseline_keys,
    )
    hd_keys = set(hd_state.accepted_pnls.keys())
    hd_pnls = {k: float(v) for k, v in hd_state.accepted_pnls.items()}

    baseline_total = round(sum(baseline_pnls.values()), 2)
    hd_total = round(sum(hd_pnls.values()), 2)
    total_delta = round(hd_total - baseline_total, 2)

    reject_rows = _reject_rows(
        hd_state,
        trade_by_key=trade_by_key,
        baseline_keys=baseline_keys,
        baseline_pnls=baseline_pnls,
    )
    reject_agg = _reject_aggregates(reject_rows)

    added_keys = hd_keys - baseline_keys
    added_trade_rows = _added_rows(hd_state, added_keys=added_keys, baseline_reject_log=baseline_reject_log)
    added_pnls = [float(r.get("pnl_yen") or 0.0) for r in added_trade_rows]

    decomp = _decompose_improvement(
        baseline_keys=baseline_keys,
        hd_keys=hd_keys,
        baseline_pnls=baseline_pnls,
        hd_pnls=hd_pnls,
        total_delta=total_delta,
    )

    day_618 = _day_symbol_analysis(
        day=TARGET_LOSS_DAY,
        baseline_keys=baseline_keys,
        hd_keys=hd_keys,
        baseline_pnls=baseline_pnls,
        hd_pnls=hd_pnls,
        trade_by_key=trade_by_key,
        added_rows=added_trade_rows,
    )

    verdict = _verdict(
        loss_avoided=float(decomp["loss_avoided"]),
        additional_profit=float(decomp["additional_profit"]),
        loss_ratio=float(decomp["loss_avoided_ratio"]),
        add_ratio=float(decomp["additional_profit_ratio"]),
    )
    essence = _essence_verdict(float(decomp["loss_avoided_ratio"]), float(decomp["additional_profit_ratio"]))

    baseline_accepted_rejects = [r for r in reject_rows if r.get("baseline_accepted")]
    ba_pnls = [float(r.get("baseline_pnl_yen") or 0.0) for r in baseline_accepted_rejects]

    day_primary = (
        "損失回避（ブロック）"
        if float(day_618.get("loss_avoided_day") or 0.0) >= float(day_618.get("additional_profit_day") or 0.0)
        else "追加採用"
    )

    summary = {
        "phase": "444-High-Drift-Attribution-Audit",
        "generated_at": _now_iso(),
        "verdict": verdict,
        "period": f"{PERIOD_START}..{PERIOD_END}",
        "reference": {
            "phase443_B_minus_A_yen": 75899.42,
            "computed_B_minus_A_yen": total_delta,
        },
        "part_a_reject_analysis": {
            **reject_agg,
            "baseline_accepted_reject_count": len(baseline_accepted_rejects),
            "baseline_accepted_reject_pnl_sum": round(sum(ba_pnls), 2),
            "baseline_accepted_reject_pf": _pf(ba_pnls),
        },
        "part_b_added_analysis": {
            "added_count": len(added_trade_rows),
            "added_pnl_sum": round(sum(added_pnls), 2),
            "added_pf": _pf(added_pnls) if added_pnls else None,
            "from_cap_reject_count": sum(1 for r in added_trade_rows if r.get("from_cap_reject")),
            "from_buying_power_reject_count": sum(1 for r in added_trade_rows if r.get("from_buying_power_reject")),
        },
        "part_c_decomposition": decomp,
        "part_d_618_analysis": day_618,
        "mandatory_answers": {
            "1_high_drift_reject_count": reject_agg["reject_count"],
            "2_reject_trade_pnl_sum": reject_agg["reject_pnl_sum"],
            "3_reject_pf": reject_agg["reject_pf"],
            "4_added_accept_count": len(added_trade_rows),
            "5_added_accept_pnl": round(sum(added_pnls), 2),
            "6_loss_avoided_share": decomp["loss_avoided_ratio"],
            "7_additional_profit_share": decomp["additional_profit_ratio"],
            "8_618_improvement_primary_cause": day_primary,
            "9_high_drift_essence": essence,
            "10_next_research_theme": _next_research_theme(verdict, day_618),
        },
    }

    attribution_metric_rows = [
        {"metric": "reject_count", "value": reject_agg["reject_count"]},
        {"metric": "reject_pnl_sum", "value": reject_agg["reject_pnl_sum"]},
        {"metric": "reject_win_rate", "value": reject_agg["reject_win_rate"]},
        {"metric": "reject_pf", "value": reject_agg["reject_pf"]},
        {"metric": "loss_avoided_yen", "value": decomp["loss_avoided"]},
        {"metric": "additional_profit_yen", "value": decomp["additional_profit"]},
        {"metric": "loss_avoided_ratio", "value": decomp["loss_avoided_ratio"]},
        {"metric": "additional_profit_ratio", "value": decomp["additional_profit_ratio"]},
        {"metric": "total_improvement_yen", "value": total_delta},
        {"metric": "path_interaction_yen", "value": decomp["path_interaction"]},
    ]

    return {
        "summary": summary,
        "_reject_rows": reject_rows,
        "_added_rows": added_trade_rows,
        "_attribution_metric_rows": attribution_metric_rows,
    }


def render_report_md(payload: Mapping[str, Any]) -> str:
    s = payload.get("summary") or {}
    m = s.get("mandatory_answers") or {}
    pa = s.get("part_a_reject_analysis") or {}
    pb = s.get("part_b_added_analysis") or {}
    pc = s.get("part_c_decomposition") or {}
    pd_ = s.get("part_d_618_analysis") or {}
    lines = [
        "# Phase444 — High Drift Attribution Audit",
        "",
        f"Generated: {s.get('generated_at')}",
        f"Verdict: **{s.get('verdict')}**",
        f"Period: {s.get('period')}",
        "",
        "## Part A — High Drift rejects",
        "",
        f"- reject_count: {pa.get('reject_count')}",
        f"- reject_pnl_sum (would_pnl): {pa.get('reject_pnl_sum')}",
        f"- reject_win_rate: {pa.get('reject_win_rate')}",
        f"- reject_pf: {pa.get('reject_pf')}",
        f"- baseline_accepted subset: {pa.get('baseline_accepted_reject_count')} trades, "
        f"pnl {pa.get('baseline_accepted_reject_pnl_sum')}, pf {pa.get('baseline_accepted_reject_pf')}",
        "",
        "## Part B — Added accepts (CAP / buying power freed)",
        "",
        f"- added_count: {pb.get('added_count')}",
        f"- added_pnl_sum: {pb.get('added_pnl_sum')}",
        f"- from_cap_reject: {pb.get('from_cap_reject_count')}",
        f"- from_buying_power_reject: {pb.get('from_buying_power_reject_count')}",
        "",
        "## Part C — Improvement decomposition",
        "",
        f"- total improvement (B−A): {pc.get('reconciliation_delta')} yen (ref {s.get('reference', {}).get('phase443_B_minus_A_yen')})",
        f"- loss_avoided: {pc.get('loss_avoided')} yen ({pc.get('loss_avoided_ratio')})",
        f"- additional_profit: {pc.get('additional_profit')} yen ({pc.get('additional_profit_ratio')})",
        f"- path_interaction (sizing): {pc.get('path_interaction')} yen",
        "",
        "## Part D — 6/18 analysis",
        "",
        f"- baseline day pnl: {pd_.get('baseline_day_pnl')}",
        f"- high drift day pnl: {pd_.get('high_drift_day_pnl')}",
        f"- day delta: {pd_.get('day_delta_yen')}",
        f"- loss_avoided (day): {pd_.get('loss_avoided_day')}",
        f"- additional_profit (day): {pd_.get('additional_profit_day')}",
        "",
        "### Symbol contributions (6976 / 3110 / 6981)",
        "",
        "| Symbol | Removed | Added | Loss avoided | Add profit | Net | Share |",
        "|--------|---------|-------|--------------|------------|-----|-------|",
    ]
    for row in pd_.get("symbol_contributions") or []:
        lines.append(
            f"| {row.get('symbol')} | {row.get('removed_count')} | {row.get('added_count')} | "
            f"{row.get('loss_avoided_yen')} | {row.get('additional_profit_yen')} | "
            f"{row.get('net_contribution_yen')} | {row.get('contribution_share_of_day_delta')} |"
        )
    lines.extend(
        [
            "",
            "## Mandatory answers",
            "",
            f"1. reject件数: {m.get('1_high_drift_reject_count')}",
            f"2. reject PnL合計: {m.get('2_reject_trade_pnl_sum')}",
            f"3. reject PF: {m.get('3_reject_pf')}",
            f"4. 追加採用件数: {m.get('4_added_accept_count')}",
            f"5. 追加採用PnL: {m.get('5_added_accept_pnl')}",
            f"6. 損失回避割合: {m.get('6_loss_avoided_share')}",
            f"7. 追加利益割合: {m.get('7_additional_profit_share')}",
            f"8. 6/18改善主因: {m.get('8_618_improvement_primary_cause')}",
            f"9. High Drift本質: {m.get('9_high_drift_essence')}",
            f"10. 次テーマ: {m.get('10_next_research_theme')}",
        ]
    )
    return "\n".join(lines) + "\n"


@dataclass
class Phase444Job:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase444_audit(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "attribution": reports / "phase444_high_drift_attribution.csv",
            "added": reports / "phase444_high_drift_added_trades.csv",
            "summary": reports / "phase444_high_drift_summary.json",
            "report": kabu / "docs" / "operations" / "phase444_high_drift_attribution_report.md",
        }
        reject_rows = list(result.get("_reject_rows") or [])
        metric_rows = result.get("_attribution_metric_rows") or []
        attr_lines: list[dict[str, Any]] = [{k: r.get(k, "") for k in REJECT_FIELDS} for r in reject_rows]
        for r in metric_rows:
            attr_lines.append(
                {
                    "symbol": "",
                    "entry_time": "",
                    "day": "",
                    "would_pnl": "",
                    "actual_reject_reason": str(r.get("metric") or ""),
                    "baseline_accepted": "",
                    "baseline_pnl_yen": r.get("value"),
                }
            )
        _write_csv(paths["attribution"], REJECT_FIELDS, attr_lines)
        _write_csv(paths["added"], ADDED_FIELDS, result.get("_added_rows") or [])
        paths["summary"].write_text(
            json.dumps(result.get("summary") or {}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        paths["report"].parent.mkdir(parents=True, exist_ok=True)
        paths["report"].write_text(render_report_md(result), encoding="utf-8")
        return paths
