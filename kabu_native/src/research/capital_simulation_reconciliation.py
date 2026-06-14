"""
Phase268-Capital-Simulation-Reconciliation.

Explain the gap between past CAP=2 positive results and Phase267 1.5M negative equity curve.
Research only — no runtime / universe / entry / exit / YAML changes.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.equity_curve_shadow import (
    PERIOD_START,
    STARTING_EQUITY,
    EQUITY_FLOOR,
    POSITION_CAP,
    load_period_trades,
    normalize_structural_trade,
)
from research.phase382_capital_constrained_backtest import (
    _parse_ts,
    _pf,
    _position_key,
    _trade_pnl_yen,
    _write_csv,
    dedupe_trades,
)
from research.phase388_cap1500k_live_candidate_validation import (
    CANDIDATE_CAP,
    CANDIDATE_EQUITY,
    REFERENCE_EQUITY,
    build_daily_equity_rows,
    simulate_detailed,
)
from research.research_output_layers import (
    COMMON_RESEARCH_CONSTRAINTS,
    build_adoption_verdict,
    build_dual_layer_bundle,
    build_live_simulation_layer,
    build_live_simulation_layer_from_cap_result,
    build_research_layer,
    format_dual_layer_markdown,
)

JST = ZoneInfo("Asia/Tokyo")

REJECT_CATEGORY_MAP: dict[str, str] = {
    "max_concurrent_positions": "CAP reached",
    "insufficient_buying_power": "buying power",
    "invalid_size": "buying power",
    "invalid_price": "buying power",
    "maintenance_ratio_stop": "leverage",
    "maintenance_ratio_force_exit": "leverage",
    "equity_floor_breach": "other",
}

ACCEPTED_VS_REJECTED_FIELDS = [
    "cohort",
    "trade_count",
    "total_pnl_yen",
    "avg_pnl_yen",
    "profit_factor",
    "win_rate",
    "win_count",
    "loss_count",
    "note",
]

REJECT_BREAKDOWN_FIELDS = [
    "reject_category",
    "raw_reason",
    "trade_count",
    "share_of_rejects",
    "counterfactual_pnl_yen",
    "counterfactual_profit_factor",
    "counterfactual_win_rate",
]


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _counterfactual_pnl(trade: Mapping[str, Any], *, shares: int = 100) -> float:
    return float(_trade_pnl_yen(trade, shares))


def _win_rate(pnls: Sequence[float]) -> float:
    if not pnls:
        return 0.0
    return round(sum(1 for p in pnls if p > 0) / len(pnls), 4)


def _cohort_stats(pnls: Sequence[float], *, note: str = "") -> dict[str, Any]:
    total = round(sum(pnls), 2)
    count = len(pnls)
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    return {
        "trade_count": count,
        "total_pnl_yen": total,
        "avg_pnl_yen": round(total / count, 2) if count else 0.0,
        "profit_factor": _pf(list(pnls)),
        "win_rate": _win_rate(pnls),
        "win_count": wins,
        "loss_count": losses,
        "note": note,
    }


def categorize_reject_reason(raw_reason: str) -> str:
    return REJECT_CATEGORY_MAP.get(raw_reason, "other")


def collect_duplicate_trades(all_rows: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    by_key: dict[str, dict[str, Any]] = {}
    duplicates: list[dict[str, Any]] = []
    for trade in sorted(all_rows, key=lambda t: str(t.get("entry_time") or "")):
        key = _position_key(trade)
        row = dict(trade)
        if key in by_key:
            row["reject_reason"] = "duplicate"
            row["reject_category"] = "duplicate"
            duplicates.append(row)
            continue
        by_key[key] = row
    return list(by_key.values()), duplicates, len(duplicates)


def analyze_simulation(
    sim: Mapping[str, Any],
    trades: Sequence[Mapping[str, Any]],
    *,
    duplicate_trades: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    trade_lookup = {(t.get("symbol"), t.get("entry_time")): t for t in trades}
    accepted_pnls: list[float] = []
    rejected_pnls: list[float] = []
    raw_reason_rows: list[tuple[str, float]] = []
    category_pnls: dict[str, list[float]] = defaultdict(list)

    for row in sim.get("_trade_log") or []:
        key = (row.get("symbol"), row.get("entry_time"))
        trade = trade_lookup.get(key) or {}
        if row.get("accepted_or_rejected") == "accepted":
            if row.get("pnl_yen") in ("", None):
                continue
            pnl = float(row.get("pnl_yen") or 0.0)
            accepted_pnls.append(pnl)
            continue
        raw_reason = str(row.get("reject_reason") or "other")
        cf = _counterfactual_pnl(trade)
        rejected_pnls.append(cf)
        raw_reason_rows.append((raw_reason, cf))
        category = categorize_reject_reason(raw_reason)
        category_pnls[category].append(cf)

    for dup in duplicate_trades:
        cf = _counterfactual_pnl(dup)
        category_pnls["duplicate"].append(cf)

    accepted_stats = _cohort_stats(
        accepted_pnls,
        note="Realized PnL from capital simulation (483 trades in Phase267).",
    )
    rejected_stats = _cohort_stats(
        rejected_pnls,
        note="Counterfactual 100-share PnL for CAP-simulation rejects (not realized).",
    )
    duplicate_stats = _cohort_stats(
        [_counterfactual_pnl(t) for t in duplicate_trades],
        note="Counterfactual PnL for trades removed in dedupe before simulation.",
    )

    raw_counts = Counter(r for r, _ in raw_reason_rows)
    total_rejects = len(rejected_pnls)
    breakdown_rows: list[dict[str, Any]] = []
    for raw_reason, count in sorted(raw_counts.items(), key=lambda x: (-x[1], x[0])):
        pnls = [cf for r, cf in raw_reason_rows if r == raw_reason]
        breakdown_rows.append(
            {
                "reject_category": categorize_reject_reason(raw_reason),
                "raw_reason": raw_reason,
                "trade_count": count,
                "share_of_rejects": round(count / total_rejects, 4) if total_rejects else 0.0,
                "counterfactual_pnl_yen": round(sum(pnls), 2),
                "counterfactual_profit_factor": _pf(pnls),
                "counterfactual_win_rate": _win_rate(pnls),
            }
        )

    if duplicate_trades:
        dup_pnls = [_counterfactual_pnl(t) for t in duplicate_trades]
        breakdown_rows.append(
            {
                "reject_category": "duplicate",
                "raw_reason": "duplicate",
                "trade_count": len(duplicate_trades),
                "share_of_rejects": round(len(duplicate_trades) / (total_rejects + len(duplicate_trades)), 4)
                if (total_rejects + len(duplicate_trades))
                else 0.0,
                "counterfactual_pnl_yen": round(sum(dup_pnls), 2),
                "counterfactual_profit_factor": _pf(dup_pnls),
                "counterfactual_win_rate": _win_rate(dup_pnls),
            }
        )

    category_summary: list[dict[str, Any]] = []
    for category in ("CAP reached", "buying power", "leverage", "duplicate", "other"):
        pnls = category_pnls.get(category) or []
        if not pnls:
            continue
        category_summary.append(
            {
                "reject_category": category,
                "raw_reason": "",
                "trade_count": len(pnls),
                "share_of_rejects": round(len(pnls) / (total_rejects + len(duplicate_trades)), 4)
                if (total_rejects + len(duplicate_trades))
                else 0.0,
                "counterfactual_pnl_yen": round(sum(pnls), 2),
                "counterfactual_profit_factor": _pf(pnls),
                "counterfactual_win_rate": _win_rate(pnls),
            }
        )

    return {
        "accepted": accepted_stats,
        "rejected": rejected_stats,
        "duplicate_dedupe": duplicate_stats,
        "raw_reject_breakdown": breakdown_rows,
        "category_reject_breakdown": category_summary,
        "sim_realized_pnl_yen": round(float(sim.get("total_pnl_yen") or 0.0), 2),
        "sim_accepted_count": int(sim.get("accepted_trade_count") or 0),
        "sim_rejected_count": int(sim.get("rejected_trade_count") or 0),
    }


def build_accepted_vs_rejected_rows(analysis: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for cohort in ("accepted", "rejected", "duplicate_dedupe"):
        stats = analysis.get(cohort) or {}
        rows.append({"cohort": cohort, **{k: stats.get(k, "") for k in ACCEPTED_VS_REJECTED_FIELDS if k != "cohort"}})
    return rows


def load_json_snapshot(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"loaded": False, "path": str(path)}
    return {"loaded": True, "path": str(path), "data": json.loads(path.read_text(encoding="utf-8"))}


def build_premise_comparison(
    *,
    phase267_pop: Mapping[str, Any],
    phase267_sim: Mapping[str, Any],
    phase267_analysis: Mapping[str, Any],
    phase388_summary: Mapping[str, Any],
    phase387_summary: Mapping[str, Any],
    phase261_summary: Mapping[str, Any],
    structural_repro: Mapping[str, Any],
    phase381_repro: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    p388 = phase388_summary.get("data") or {}
    p387 = phase387_summary.get("data") or {}
    p261 = phase261_summary.get("data") or {}

    cand388 = p388.get("candidate") or {}
    ref388 = p388.get("reference_2000k_cap2_sim") or {}
    p385_ref = p388.get("phase385_reference_cap2_2m") or {}
    shadow387 = p387.get("shadow_cap2") or {}

    p267_scenario = ((phase267_sim.get("data") or {}).get("scenarios") or {}).get("actual_fixed_stop") or {}

    rows = [
        {
            "label": "Phase267 (structural_trades, capital sim)",
            "trade_source": "structural_trades.csv via load_trades_by_day",
            "period": phase267_pop.get("period_days"),
            "initial_equity": STARTING_EQUITY,
            "position_cap": POSITION_CAP,
            "methodology": "sequential_capital_simulation",
            "input_trades": phase267_pop.get("input_trade_count"),
            "accepted": phase267_analysis.get("sim_accepted_count"),
            "rejected": phase267_analysis.get("sim_rejected_count"),
            "total_pnl_yen": phase267_analysis.get("sim_realized_pnl_yen"),
            "profit_factor": p267_scenario.get("profit_factor"),
            "note": "Phase267 equity curve (actual fixed stop).",
        },
        {
            "label": "Phase268 repro (structural, simulate_detailed)",
            "trade_source": "same as Phase267",
            "period": phase267_pop.get("period_days"),
            "initial_equity": STARTING_EQUITY,
            "position_cap": POSITION_CAP,
            "methodology": "sequential_capital_simulation",
            "input_trades": phase267_pop.get("input_trade_count"),
            "accepted": structural_repro.get("accepted_trade_count"),
            "rejected": structural_repro.get("rejected_trade_count"),
            "total_pnl_yen": structural_repro.get("total_pnl_yen"),
            "profit_factor": structural_repro.get("profit_factor"),
            "note": "Cross-check Phase267 via phase388 simulate_detailed engine.",
        },
        {
            "label": "Phase388 candidate (phase381 trades, 1.5M)",
            "trade_source": "phase381 winner profile / production stack",
            "period": f"{(p388.get('population') or {}).get('min_day')}–{(p388.get('population') or {}).get('max_day')}",
            "initial_equity": CANDIDATE_EQUITY,
            "position_cap": CANDIDATE_CAP,
            "methodology": "sequential_capital_simulation",
            "input_trades": (p388.get("population") or {}).get("input_trade_count"),
            "accepted": cand388.get("accepted_trade_count"),
            "rejected": cand388.get("rejected_trade_count"),
            "total_pnl_yen": cand388.get("total_pnl_yen"),
            "profit_factor": cand388.get("profit_factor"),
            "note": "Past positive CAP=2 validation at 150万.",
        },
        {
            "label": "Phase388 reference (phase381 trades, 2.0M)",
            "trade_source": "phase381 winner profile / production stack",
            "period": f"{(p388.get('population') or {}).get('min_day')}–{(p388.get('population') or {}).get('max_day')}",
            "initial_equity": REFERENCE_EQUITY,
            "position_cap": CANDIDATE_CAP,
            "methodology": "sequential_capital_simulation",
            "input_trades": (p388.get("population") or {}).get("input_trade_count"),
            "accepted": ref388.get("accepted_trade_count"),
            "rejected": ref388.get("rejected_trade_count"),
            "total_pnl_yen": ref388.get("total_pnl_yen"),
            "profit_factor": ref388.get("profit_factor"),
            "note": "Phase385 CAP_2 capital sim match.",
        },
        {
            "label": "Phase385 CAP_2 shadow (2.0M, summary)",
            "trade_source": "phase381 (via phase385 runner)",
            "period": "20260529–20260612",
            "initial_equity": p385_ref.get("initial_equity"),
            "position_cap": 2,
            "methodology": "sequential_capital_simulation",
            "input_trades": None,
            "accepted": p385_ref.get("accepted_trade_count"),
            "rejected": p385_ref.get("rejected_trade_count"),
            "total_pnl_yen": p385_ref.get("total_pnl_yen_100"),
            "profit_factor": p385_ref.get("profit_factor"),
            "note": "Historical CAP=2 positive reference.",
        },
        {
            "label": "Phase387 shadow_cap2 (accepted-only static PnL)",
            "trade_source": "phase381 winner profile",
            "period": f"{(p387.get('population') or {}).get('min_day')}–{(p387.get('population') or {}).get('max_day')}",
            "initial_equity": p387.get("initial_equity"),
            "position_cap": p387.get("shadow_cap"),
            "methodology": "accepted_subset_pnl_yen_100_sum",
            "input_trades": (p387.get("population") or {}).get("input_trade_count"),
            "accepted": shadow387.get("trade_count"),
            "rejected": (p387.get("acceptance") or {}).get("shadow_cap2_rejected"),
            "total_pnl_yen": shadow387.get("total_pnl_yen_100"),
            "profit_factor": shadow387.get("profit_factor"),
            "note": "NOT capital-path PnL — sum of pnl_yen_100 for CAP2-accepted subset.",
        },
        {
            "label": "Phase261 fixed_100_shares (overlap days, no CAP sim)",
            "trade_source": "structural_trades overlap 20260520–25",
            "period": (p261.get("summary") or {}).get("trade_overlap_days"),
            "initial_equity": 1_000_000,
            "position_cap": None,
            "methodology": "sizing_shadow_no_capital_constraint",
            "input_trades": (p261.get("summary") or {}).get("base_entry_count"),
            "accepted": (p261.get("summary") or {}).get("base_entry_count"),
            "rejected": 0,
            "total_pnl_yen": next(
                (
                    row.get("total_pnl_yen_scaled")
                    for row in (p261.get("policy_by_equity") or [])
                    if row.get("equity_yen") == 1_000_000 and row.get("sizing_policy") == "fixed_100_shares"
                ),
                None,
            ),
            "profit_factor": next(
                (
                    row.get("profit_factor")
                    for row in (p261.get("policy_by_equity") or [])
                    if row.get("equity_yen") == 1_000_000 and row.get("sizing_policy") == "fixed_100_shares"
                ),
                None,
            ),
            "note": "Different period and no CAP=2 sequential simulation.",
        },
    ]

    if phase381_repro:
        rows.append(
            {
                "label": "Phase268 repro (phase381 trades, 1.5M)",
                "trade_source": "phase381 winner profile (reloaded)",
                "period": phase267_pop.get("period_days"),
                "initial_equity": CANDIDATE_EQUITY,
                "position_cap": CANDIDATE_CAP,
                "methodology": "sequential_capital_simulation",
                "input_trades": phase381_repro.get("input_trade_count"),
                "accepted": phase381_repro.get("accepted_trade_count"),
                "rejected": phase381_repro.get("rejected_trade_count"),
                "total_pnl_yen": phase381_repro.get("total_pnl_yen"),
                "profit_factor": phase381_repro.get("profit_factor"),
                "note": "Same capital engine as Phase388, re-run for decomposition.",
            }
        )

    return {"rows": rows}


def build_pnl_delta_decomposition(
    *,
    phase267_analysis: Mapping[str, Any],
    structural_repro: Mapping[str, Any],
    phase381_repro: Optional[Mapping[str, Any]],
    phase388_candidate_pnl: float,
) -> dict[str, Any]:
    p267_pnl = float(structural_repro.get("total_pnl_yen") or 0.0)
    p388_pnl = float(phase388_candidate_pnl)
    total_gap = round(p267_pnl - p388_pnl, 2)

    accepted = phase267_analysis.get("accepted") or {}
    rejected = phase267_analysis.get("rejected") or {}

    unconstrained_all = round(
        float(accepted.get("total_pnl_yen") or 0.0) + float(rejected.get("total_pnl_yen") or 0.0),
        2,
    )

    universe_gap = None
    if phase381_repro:
        universe_gap = round(p267_pnl - float(phase381_repro.get("total_pnl_yen") or 0.0), 2)

    return {
        "config": {
            "initial_equity_yen": STARTING_EQUITY,
            "leverage_limit": 2.0,
            "shares": 100,
            "position_cap": POSITION_CAP,
        },
        "phase267_capital_sim_pnl_yen": p267_pnl,
        "phase388_capital_sim_pnl_yen": p388_pnl,
        "total_gap_yen": total_gap,
        "factors": {
            "accepted_realized_pnl_yen": float(accepted.get("total_pnl_yen") or 0.0),
            "accepted_profit_factor": accepted.get("profit_factor"),
            "rejected_counterfactual_pnl_yen": float(rejected.get("total_pnl_yen") or 0.0),
            "rejected_counterfactual_profit_factor": rejected.get("profit_factor"),
            "rejected_opportunity_cost_yen": round(float(rejected.get("total_pnl_yen") or 0.0), 2),
            "accepted_plus_rejected_counterfactual_yen": unconstrained_all,
            "selection_effect_note": (
                "Realized accepted PnL is path-dependent; rejected counterfactual sums static "
                "100-share PnL and overstates what sequential execution would have earned."
            ),
            "trade_universe_gap_vs_phase381_yen": universe_gap,
            "trade_universe_note": (
                "structural_trades.csv vs phase381 winner profile — different entry/exit timing "
                "and population size drive accept counts (483 vs ~119)."
            ),
            "methodology_gap_note": (
                "Phase387 shadow_cap2 reports accepted-only pnl_yen_100 sum (+162,700 at 2M), "
                "not sequential capital-path PnL; Phase388/385 use full capital simulation."
            ),
        },
    }


def build_report(summary: Mapping[str, Any]) -> str:
    avr = summary.get("accepted_vs_rejected") or {}
    accepted = avr.get("accepted") or {}
    rejected = avr.get("rejected") or {}
    decomp = summary.get("pnl_delta_decomposition") or {}
    factors = decomp.get("factors") or {}
    premise = summary.get("premise_comparison") or {}
    cat_rows = summary.get("reject_category_breakdown") or []
    dual = summary.get("dual_layer") or {}

    lines = [
        "# Phase268 Capital Simulation Reconciliation",
        "",
        f"**生成:** {summary.get('generated_at')}",
        "",
        "## 結論",
        "",
        "過去の「CAP=2でプラス」と Phase267「150万スタートでマイナス」の差は、",
        "**同一の資本制約シミュレーションでもトレード母集団が異なる**ことと、",
        "**Phase267 accepted 483件の実現PnLがマイナス（PF<1）**であることが主因。",
        "",
        f"- Phase267 資本シミュ realized PnL: **{decomp.get('phase267_capital_sim_pnl_yen')}円**",
        f"- Phase388 (phase381母集団) realized PnL: **{decomp.get('phase388_capital_sim_pnl_yen')}円**",
        f"- 差分: **{decomp.get('total_gap_yen')}円**",
        "",
    ]
    lines.extend(format_dual_layer_markdown(dual, title="Phase267 structural_trades @ CAP=2"))
    lines.extend(
        [
        "",
        "### Phase267 内訳",
        "",
        f"| コホート | 件数 | 総PnL | PF |",
        f"|---|---:|---:|---:|",
        f"| accepted (実現) | {accepted.get('trade_count')} | {accepted.get('total_pnl_yen')} | {accepted.get('profit_factor')} |",
        f"| rejected (反実仮想100株) | {rejected.get('trade_count')} | {rejected.get('total_pnl_yen')} | {rejected.get('profit_factor')} |",
        "",
        f"rejected は CAP到達等で取れなかったトレードの静的PnL（反実仮想合計 **+{rejected.get('total_pnl_yen')}円**）。",
        f"機会損失目安: **{factors.get('rejected_opportunity_cost_yen')}円**（sequential実行とは一致しない参考値）",
        "",
        "### reject reason 別（カテゴリ）",
        "",
        ]
    )
    for row in cat_rows:
        lines.append(
            f"- **{row.get('reject_category')}**: {row.get('trade_count')}件, "
            f"反実仮想PnL={row.get('counterfactual_pnl_yen')}円, PF={row.get('counterfactual_profit_factor')}"
        )

    lines.extend(
        [
        "",
        "### なぜ Phase267 だけマイナスか（要因分解）",
        "",
        f"1. **accepted 実現PnL {accepted.get('total_pnl_yen')}円 (PF={accepted.get('profit_factor')})** — 483件を実行しても損失",
        f"2. **rejected 反実仮想 {rejected.get('total_pnl_yen')}円 (PF={rejected.get('profit_factor')})** — CAPで取り逃がした静的勝ち筋",
        f"3. **母集団差** — Phase388(phase381)は119 accept / +148,200円 vs Phase267(structural)は483 accept / -62,520円",
        "4. **Phase387 +162,700円** — accepted 131件の pnl_yen_100 合計であり、150万資本パスシミュ結果ではない",
        "5. **Phase261 +31,830円** — 20260520–25・CAP制約なし fixed_100 shadow（別期間・別手法）",
        "",
            "## 禁止事項",
            "",
            "- Runtime / Universe / Entry / Exit / YAML 変更なし",
            "- 採用判定は Research PF ではなく Live Simulation final_equity を主指標とする",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


@dataclass
class CapitalSimulationReconciliation:
    repo_root: Path
    reports_dir: Path
    period_start: str = PERIOD_START
    phase381_trades: list[dict[str, Any]] = field(default_factory=list)

    def paths(self) -> dict[str, Path]:
        return {
            "accepted_vs_rejected": self.reports_dir / "accepted_vs_rejected.csv",
            "reject_reason_breakdown": self.reports_dir / "reject_reason_breakdown.csv",
            "summary": self.reports_dir / "phase268_reconciliation_summary.json",
            "report": self.reports_dir / "phase268_report.md",
        }

    def run(self) -> dict[str, Any]:
        trades, pop_meta = load_period_trades(self.repo_root, period_start=self.period_start)

        raw_rows: list[dict[str, Any]] = []
        from research.market_sector_heat import load_trades_by_day

        by_day = load_trades_by_day(self.repo_root)
        for day in sorted(by_day.keys()):
            if day < self.period_start:
                continue
            for row in by_day.get(day) or []:
                trade = normalize_structural_trade(row)
                if _parse_ts(trade.get("entry_time")) is None or _parse_ts(trade.get("exit_time")) is None:
                    continue
                if float(trade.get("entry_price") or 0.0) <= 0:
                    continue
                raw_rows.append(trade)

        _, duplicate_trades, _ = collect_duplicate_trades(raw_rows)

        structural_sim = simulate_detailed(
            trades,
            scenario_id="phase268_structural_1500k_cap2",
            cap=POSITION_CAP,
            initial_equity=STARTING_EQUITY,
            equity_floor=EQUITY_FLOOR,
        )
        analysis = analyze_simulation(structural_sim, trades, duplicate_trades=duplicate_trades)

        phase381_repro = None
        if self.phase381_trades:
            p381_deduped, _ = dedupe_trades(self.phase381_trades)
            p381_deduped.sort(
                key=lambda t: (
                    _parse_ts(t.get("entry_time")) or datetime.min.replace(tzinfo=JST),
                    str(t.get("symbol") or ""),
                )
            )
            phase381_repro = simulate_detailed(
                p381_deduped,
                scenario_id="phase268_phase381_1500k_cap2",
                cap=POSITION_CAP,
                initial_equity=STARTING_EQUITY,
                equity_floor=EQUITY_FLOOR,
            )
            phase381_repro = {
                k: v for k, v in {**phase381_repro, "input_trade_count": len(p381_deduped)}.items() if not str(k).startswith("_")
            }

        p267_snap = load_json_snapshot(self.reports_dir / "phase267_equity_curve_summary.json")
        p388_snap = load_json_snapshot(self.reports_dir / "phase388_cap1500k_validation_summary.json")
        p387_snap = load_json_snapshot(self.reports_dir / "phase387_cap2_shadow_summary.json")
        p261_snap = load_json_snapshot(self.reports_dir / "phase261_risk_aware_sizing_summary.json")

        p388_data = p388_snap.get("data") or {}
        p388_pnl = float((p388_data.get("candidate") or {}).get("total_pnl_yen") or 0.0)

        premise = build_premise_comparison(
            phase267_pop=pop_meta,
            phase267_sim=p267_snap,
            phase267_analysis=analysis,
            phase388_summary=p388_snap,
            phase387_summary=p387_snap,
            phase261_summary=p261_snap,
            structural_repro={k: v for k, v in structural_sim.items() if not str(k).startswith("_")},
            phase381_repro=phase381_repro,
        )

        decomp = build_pnl_delta_decomposition(
            phase267_analysis=analysis,
            structural_repro={k: v for k, v in structural_sim.items() if not str(k).startswith("_")},
            phase381_repro=phase381_repro,
            phase388_candidate_pnl=p388_pnl,
        )

        p267_actual = ((p267_snap.get("data") or {}).get("scenarios") or {}).get("actual_fixed_stop") or {}
        p267_accepted = int(p267_actual.get("accepted_trade_count") or 0)
        crosscheck_match = int(structural_sim.get("accepted_trade_count") or 0) == p267_accepted

        all_static_pnls = [_counterfactual_pnl(t) for t in trades]
        research_layer = build_research_layer(
            all_static_pnls,
            label="all_trades_static_100_shares",
        )
        structural_daily = build_daily_equity_rows(structural_sim)
        live_layer = build_live_simulation_layer_from_cap_result(
            structural_sim,
            cap=POSITION_CAP,
            daily_rows=structural_daily,
            starting_equity=STARTING_EQUITY,
        )
        dual_layer = build_dual_layer_bundle(
            research_layer=research_layer,
            live_simulation_layer=live_layer,
        )

        p388_candidate = (p388_data.get("candidate") or {})
        comparison_dual_layer = {
            "phase267_structural": dual_layer,
            "phase388_phase381": {
                "research_layer": {
                    "label": "phase388_accepted_static_reference",
                    "note": "Phase388 summary; research metrics from accepted capital-path trades.",
                    "profit_factor": p388_candidate.get("profit_factor"),
                    "total_pnl_yen": p388_candidate.get("total_pnl_yen"),
                    "win_rate": p388_candidate.get("win_rate"),
                    "trade_count": p388_candidate.get("accepted_trade_count"),
                },
                "live_simulation_layer": build_live_simulation_layer(
                    cap=POSITION_CAP,
                    starting_equity=CANDIDATE_EQUITY,
                    leverage=2.0,
                    shares=100,
                    final_equity=float(p388_candidate.get("final_equity") or 0.0),
                    total_return_pct=float(p388_candidate.get("return_pct") or 0.0),
                    max_drawdown_pct=float(p388_candidate.get("max_drawdown_pct") or 0.0),
                    days_below_50pct=0,
                    accepted_count=int(p388_candidate.get("accepted_trade_count") or 0),
                    rejected_count=int(p388_candidate.get("rejected_trade_count") or 0),
                    total_pnl_yen=float(p388_candidate.get("total_pnl_yen") or 0.0),
                    profit_factor=p388_candidate.get("profit_factor"),
                    win_rate=p388_candidate.get("win_rate"),
                ),
                "adoption_verdict": build_adoption_verdict(
                    live_simulation_layer=build_live_simulation_layer(
                        cap=POSITION_CAP,
                        starting_equity=CANDIDATE_EQUITY,
                        leverage=2.0,
                        shares=100,
                        final_equity=float(p388_candidate.get("final_equity") or 0.0),
                        total_return_pct=float(p388_candidate.get("return_pct") or 0.0),
                        max_drawdown_pct=float(p388_candidate.get("max_drawdown_pct") or 0.0),
                        days_below_50pct=0,
                        accepted_count=int(p388_candidate.get("accepted_trade_count") or 0),
                        rejected_count=int(p388_candidate.get("rejected_trade_count") or 0),
                    ),
                    research_layer={
                        "profit_factor": p388_candidate.get("profit_factor"),
                    },
                ),
            },
        }

        return {
            "phase": "268-Capital-Simulation-Reconciliation",
            "title": "Capital simulation reconciliation",
            "generated_at": _now_iso(),
            "purpose": "Explain CAP=2 positive (past) vs Phase267 1.5M negative",
            "constraints": dict(COMMON_RESEARCH_CONSTRAINTS),
            "output_standard": {
                "research_layer_fields": ["profit_factor", "total_pnl_yen", "win_rate"],
                "live_simulation_layer_fields": [
                    "starting_equity",
                    "leverage",
                    "shares",
                    "cap",
                    "final_equity",
                    "total_return_pct",
                    "max_drawdown_pct",
                    "days_below_50pct",
                    "accepted_count",
                    "rejected_count",
                ],
                "adoption_primary_metric": "final_equity",
            },
            "config": {
                "starting_equity": STARTING_EQUITY,
                "leverage_limit": 2.0,
                "shares": 100,
                "position_cap": POSITION_CAP,
                "equity_floor": EQUITY_FLOOR,
            },
            "population": pop_meta,
            "accepted_vs_rejected": {
                "accepted": analysis["accepted"],
                "rejected": analysis["rejected"],
                "duplicate_dedupe": analysis["duplicate_dedupe"],
            },
            "reject_reason_breakdown_raw": analysis["raw_reject_breakdown"],
            "reject_category_breakdown": analysis["category_reject_breakdown"],
            "phase267_crosscheck": {
                "simulate_detailed_matches_phase267": crosscheck_match,
                "accepted_count": structural_sim.get("accepted_trade_count"),
                "rejected_count": structural_sim.get("rejected_trade_count"),
                "realized_pnl_yen": structural_sim.get("total_pnl_yen"),
                "phase267_accepted_count": p267_accepted,
            },
            "premise_comparison": premise,
            "pnl_delta_decomposition": decomp,
            "dual_layer": dual_layer,
            "comparison_dual_layer": comparison_dual_layer,
            "_accepted_vs_rejected_rows": build_accepted_vs_rejected_rows(analysis),
            "_reject_breakdown_rows": analysis["category_reject_breakdown"],
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        paths = self.paths()
        paths["accepted_vs_rejected"].parent.mkdir(parents=True, exist_ok=True)
        _write_csv(paths["accepted_vs_rejected"], result.get("_accepted_vs_rejected_rows") or [], ACCEPTED_VS_REJECTED_FIELDS)
        _write_csv(
            paths["reject_reason_breakdown"],
            result.get("_reject_breakdown_rows") or [],
            REJECT_BREAKDOWN_FIELDS,
        )
        public = {k: v for k, v in result.items() if not str(k).startswith("_")}
        paths["summary"].write_text(json.dumps(public, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        paths["report"].write_text(build_report(public), encoding="utf-8")
        return paths


def run_capital_simulation_reconciliation(
    *,
    repo_root: Path,
    reports_dir: Path,
    period_start: str = PERIOD_START,
    phase381_trades: Optional[Sequence[Mapping[str, Any]]] = None,
) -> dict[str, Any]:
    job = CapitalSimulationReconciliation(
        repo_root=repo_root,
        reports_dir=reports_dir,
        period_start=period_start,
        phase381_trades=list(phase381_trades or []),
    )
    result = job.run()
    job.write_outputs(result)
    return {k: v for k, v in result.items() if not str(k).startswith("_")}
