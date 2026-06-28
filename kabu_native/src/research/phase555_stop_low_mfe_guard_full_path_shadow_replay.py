"""
Phase555 — stop_low_mfe guard full-path shadow replay (research only).

Counterfactual hard-reject replay on B_current_runtime accepted trades.
No Runtime changes.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _float, _parse_ts
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase451_entry_shape_tournament import JST, _build_price_index_to, _now_iso
from research.phase484_stop_low_mfe_feature_discovery import _load_day_event_snaps
from research.phase515b_day_high_breakout_dependency_audit import SYMBOL_6976
from research.phase518_day_high_winner_loser_separation import _build_micro_lookup, _percentile
from research.phase524_live_reentry_guard_and_stop_low_mfe import (
    PERIOD_START_LIVE,
    _build_bar_cache_for_days,
    _latest_live_day,
    _num,
)
from research.phase527_entry_quality_guard import _chron_pnls
from research.phase540_no_progress_mfe0_entry_quality import _is_mfe0, _is_no_progress, _is_winner, _mfe_pct
from research.phase541_guard_v2_full_period_validation import BIG_WINNER_MFE_PCT
from research.phase551_current_runtime_full_period_replay import E4_THRESHOLD
from research.phase553_loss_day_root_cause_analysis import _load_b_runtime_accepted
from research.phase523_reentry_definition_overlay_edge_reality_audit import _is_stop_hit
from research.phase554_stop_low_mfe_entry_quality_feature_study import (
    TARGET_DAY,
    _enrich_phase554,
    _feature_value,
    _is_stop_low_mfe_554,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE555_VERDICT = "phase555_stop_low_mfe_guard_full_path_shadow_replay_done"
LIVE_END_DEFAULT = "20260625"
BIG_WINNER_MFE = BIG_WINNER_MFE_PCT
LOST_BIG_MAX = 5
RETENTION_MIN = 0.60

SUMMARY_FIELDS = [
    "variant_id",
    "label",
    "trades",
    "pnl_yen_100",
    "profit_factor",
    "max_drawdown_yen_100",
    "win_rate",
    "mfe0_count",
    "stop_low_mfe_count",
    "no_progress_count",
    "big_winner_count",
    "lost_big_winner",
    "blocked_trades",
    "blocked_losses",
    "blocked_winners",
    "blocked_big_winners",
    "net_improvement_yen_100",
    "retention",
    "success_score",
    "success_criteria_met",
    "classification",
]

DETAIL_FIELDS = SUMMARY_FIELDS + [
    "delta_pnl_vs_baseline",
    "delta_pf_vs_baseline",
    "delta_stop_low_mfe_vs_baseline",
    "delta_mfe0_vs_baseline",
    "delta_lost_big_winner_vs_baseline",
]

DEPENDENCY_FIELDS = [
    "variant_id",
    "top10_trade_exclusion_pnl_yen_100",
    "top3_symbol_exclusion_pnl_yen_100",
    "top3_day_exclusion_pnl_yen_100",
    "symbol_6976_exclusion_pnl_yen_100",
]

DAY618_FIELDS = [
    "variant_id",
    "day_pnl_baseline_yen_100",
    "day_pnl_variant_yen_100",
    "day_net_improvement_yen_100",
    "blocked_6779",
    "blocked_6976_am_loss",
    "kept_6976_pm_winner",
    "blocked_6387",
    "day_blocked_losses_yen_100",
    "day_blocked_winners_yen_100",
]


@dataclass(frozen=True)
class GuardVariant:
    variant_id: str
    label: str
    feature: str = ""
    threshold: float = 0.0
    direction: str = "le"
    rescue: Optional[str] = None


VARIANTS: tuple[GuardVariant, ...] = (
    GuardVariant("V0", "Baseline current runtime"),
    GuardVariant("V1", "G554_021 hard reject", "volume_acceleration_5m", -0.0196, "le"),
    GuardVariant("V2", "G554_031 hard reject", "board_collapse_rate", 0.077, "le"),
    GuardVariant("V3", "G554_022 hard reject", "volume_acceleration_5m", 0.009, "le"),
    GuardVariant("V4", "G554_021 + re-entry rescue", "volume_acceleration_5m", -0.0196, "le", "reentry"),
    GuardVariant("V5", "G554_021 + liquidity_burst rescue", "volume_acceleration_5m", -0.0196, "le", "liquidity_burst"),
    GuardVariant("V6", "G554_021 + high_update rescue", "volume_acceleration_5m", -0.0196, "le", "high_update"),
)


def _sym(row: Mapping[str, Any]) -> str:
    s = str(row.get("symbol") or "")
    return s if s.endswith(".T") else f"{s}.T"


def _entry_px(row: Mapping[str, Any]) -> float:
    return _float(row.get("entry_price") or row.get("current_price")) or 0.0


def _is_big_winner_row(row: Mapping[str, Any]) -> bool:
    if row.get("is_big_winner"):
        return True
    return _mfe_pct(row) >= BIG_WINNER_MFE


def _guard_would_reject(row: Mapping[str, Any], spec: GuardVariant) -> bool:
    if not spec.feature:
        return False
    v = _feature_value(row, spec.feature)
    if v is None:
        return False
    # Phase554 shadow semantics: direction "le" keeps v <= threshold (reject above).
    if spec.direction == "le":
        return v > spec.threshold
    return v < spec.threshold


def _bool_val(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).lower() in ("true", "1", "yes")


def _reentry_rescue(row: Mapping[str, Any], prior: Sequence[Mapping[str, Any]]) -> bool:
    sym = _sym(row)
    ent = _parse_ts(str(row.get("entry_time") or ""))
    if ent is None:
        return False
    px = _entry_px(row)
    prev_same = [p for p in prior if _sym(p) == sym]
    if not prev_same:
        return False
    last = prev_same[-1]
    if not _is_stop_hit(last):
        return False
    exit_ts = _parse_ts(str(last.get("exit_time") or last.get("entry_time") or ""))
    if exit_ts is None:
        return False
    mins = (ent - exit_ts).total_seconds() / 60.0
    if mins < 60:
        return False
    prev_px = _entry_px(last)
    if prev_px <= 0:
        return False
    return px >= prev_px * 0.997


def _liquidity_burst_rescue(row: Mapping[str, Any], *, lb_p75: float) -> bool:
    return (_float(row.get("liquidity_burst")) or 0) >= lb_p75


def _high_update_rescue(row: Mapping[str, Any]) -> bool:
    return _bool_val(row.get("high_update_recent"))


def _rescue_fn(
    spec: GuardVariant,
    *,
    lb_p75: float,
) -> Optional[Callable[[Mapping[str, Any], Sequence[Mapping[str, Any]]], bool]]:
    if spec.rescue == "reentry":
        return lambda r, prior: _reentry_rescue(r, prior)
    if spec.rescue == "liquidity_burst":
        return lambda r, _prior: _liquidity_burst_rescue(r, lb_p75=lb_p75)
    if spec.rescue == "high_update":
        return lambda r, _prior: _high_update_rescue(r)
    return None


def _evaluate_variant(
    trades: Sequence[Mapping[str, Any]],
    spec: GuardVariant,
    *,
    baseline_pnl: float,
    baseline_trades: int,
    lb_p75: float,
) -> dict[str, Any]:
    rescue = _rescue_fn(spec, lb_p75=lb_p75)
    accepted: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    prior: list[dict[str, Any]] = []

    ordered = sorted(
        trades,
        key=lambda t: _parse_ts(str(t.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST),
    )
    for t in ordered:
        row = dict(t)
        reject = _guard_would_reject(row, spec)
        if reject and rescue and rescue(row, prior):
            accepted.append(row)
        elif reject:
            blocked.append(row)
        else:
            accepted.append(row)
        prior.append(row)

    pnls = [_num(t.get("pnl_yen_100")) for t in accepted]
    total = round(sum(pnls), 2)
    blocked_pnls = [_num(t.get("pnl_yen_100")) for t in blocked]
    blocked_losses = sum(1 for p in blocked_pnls if p < 0)
    blocked_winners = sum(1 for p in blocked_pnls if p > 0)
    blocked_big = sum(1 for t in blocked if _is_big_winner_row(t))
    n = len(accepted)
    return {
        "trades": n,
        "pnl_yen_100": total,
        "profit_factor": _pf(pnls),
        "max_drawdown_yen_100": round(_max_drawdown_yen(_chron_pnls(accepted)) if accepted else 0.0, 2),
        "win_rate": round(sum(1 for p in pnls if p > 0) / n, 4) if n else 0.0,
        "mfe0_count": sum(1 for t in accepted if _is_mfe0(t)),
        "stop_low_mfe_count": sum(1 for t in accepted if _is_stop_low_mfe_554(t)),
        "no_progress_count": sum(1 for t in accepted if _is_no_progress(t)),
        "big_winner_count": sum(1 for t in accepted if _is_big_winner_row(t)),
        "lost_big_winner": blocked_big,
        "blocked_trades": len(blocked),
        "blocked_losses": blocked_losses,
        "blocked_winners": blocked_winners,
        "blocked_big_winners": blocked_big,
        "net_improvement_yen_100": round(total - baseline_pnl, 2),
        "retention": round(n / baseline_trades, 4) if baseline_trades else 0.0,
        "_accepted": accepted,
        "_blocked": blocked,
    }


def _success_criteria(result: Mapping[str, Any], baseline: Mapping[str, Any], *, day618: Mapping[str, Any]) -> tuple[int, list[str]]:
    met: list[str] = []
    if _num(result.get("pnl_yen_100")) > _num(baseline.get("pnl_yen_100")):
        met.append("pnl_gt_baseline")
    if _num(result.get("profit_factor")) > _num(baseline.get("profit_factor")):
        met.append("pf_gt_baseline")
    if int(result.get("stop_low_mfe_count") or 0) < int(baseline.get("stop_low_mfe_count") or 0):
        met.append("stop_low_mfe_reduced")
    if int(result.get("mfe0_count") or 0) <= int(baseline.get("mfe0_count") or 0):
        met.append("mfe0_not_worse")
    if int(result.get("lost_big_winner") or 0) <= LOST_BIG_MAX:
        met.append("lost_big_winner_ok")
    if _num(result.get("retention")) >= RETENTION_MIN:
        met.append("retention_ok")
    if _num(day618.get("day_net_improvement_yen_100")) > 0:
        met.append("day618_improved")
    if day618.get("kept_6976_pm_winner"):
        met.append("6976_pm_winner_kept")
    return len(met), met


def _classify_variant(spec: GuardVariant, result: Mapping[str, Any], *, success_score: int) -> str:
    if spec.variant_id == "V0":
        return "baseline"
    if success_score >= 7 and _num(result.get("net_improvement_yen_100")) > 0:
        return "A_runtime_candidate"
    if success_score >= 5 and _num(result.get("net_improvement_yen_100")) > 0:
        return "B_shadow_candidate"
    if _num(result.get("net_improvement_yen_100")) > 0:
        return "C_research_continue"
    return "D_reject"


def _dependency_row(
    spec: GuardVariant,
    result: Mapping[str, Any],
    *,
    baseline_pnl: float,
) -> dict[str, Any]:
    blocked = list(result.get("_blocked") or [])
    net = round(_num(result.get("pnl_yen_100")) - baseline_pnl, 2)
    sym_delta: dict[str, float] = defaultdict(float)
    day_delta: dict[str, float] = defaultdict(float)
    for t in blocked:
        pnl = _num(t.get("pnl_yen_100"))
        sym_delta[str(t.get("symbol") or "").replace(".T", "")] -= pnl
        day_delta[str(t.get("day") or "")[:8]] -= pnl
    sym_sorted = sorted(sym_delta.items(), key=lambda x: x[1], reverse=True)
    day_sorted = sorted(day_delta.items(), key=lambda x: x[1], reverse=True)
    top10 = sorted(blocked, key=lambda t: _num(t.get("pnl_yen_100")))[:10]
    return {
        "variant_id": spec.variant_id,
        "top10_trade_exclusion_pnl_yen_100": round(net + sum(_num(t.get("pnl_yen_100")) for t in top10), 2),
        "top3_symbol_exclusion_pnl_yen_100": round(net - sum(v for _, v in sym_sorted[:3]), 2),
        "top3_day_exclusion_pnl_yen_100": round(net - sum(v for _, v in day_sorted[:3]), 2),
        "symbol_6976_exclusion_pnl_yen_100": round(net - sym_delta.get(SYMBOL_6976, 0.0), 2),
    }


def _day618_eval(
    spec: GuardVariant,
    trades: Sequence[Mapping[str, Any]],
    *,
    baseline_day_pnl: float,
    lb_p75: float,
) -> dict[str, Any]:
    day_trades = [dict(t) for t in trades if str(t.get("day") or "")[:8] == TARGET_DAY]
    day_trades.sort(
        key=lambda t: _parse_ts(str(t.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST)
    )
    result = _evaluate_variant(day_trades, spec, baseline_pnl=baseline_day_pnl, baseline_trades=len(day_trades), lb_p75=lb_p75)
    blocked_keys = {(_sym(t), str(t.get("entry_time"))) for t in result.get("_blocked") or []}

    t6779 = next((t for t in day_trades if _sym(t) == "6779.T"), None)
    t6976_loss = next((t for t in day_trades if _sym(t) == "6976.T" and _num(t.get("pnl_yen_100")) < 0), None)
    t6976_win = next((t for t in day_trades if _sym(t) == "6976.T" and _num(t.get("pnl_yen_100")) > 0), None)
    t6387 = next((t for t in day_trades if _sym(t) == "6387.T"), None)

    def _is_blocked_trade(t: Optional[Mapping[str, Any]]) -> bool:
        if t is None:
            return False
        return (_sym(t), str(t.get("entry_time"))) in blocked_keys

    blocked_loss = round(sum(-_num(t.get("pnl_yen_100")) for t in result.get("_blocked") or [] if _num(t.get("pnl_yen_100")) < 0), 2)
    blocked_win = round(sum(_num(t.get("pnl_yen_100")) for t in result.get("_blocked") or [] if _num(t.get("pnl_yen_100")) > 0), 2)

    return {
        "variant_id": spec.variant_id,
        "day_pnl_baseline_yen_100": baseline_day_pnl,
        "day_pnl_variant_yen_100": result.get("pnl_yen_100"),
        "day_net_improvement_yen_100": round(_num(result.get("pnl_yen_100")) - baseline_day_pnl, 2),
        "blocked_6779": _is_blocked_trade(t6779),
        "blocked_6976_am_loss": _is_blocked_trade(t6976_loss),
        "kept_6976_pm_winner": not _is_blocked_trade(t6976_win) if t6976_win else False,
        "blocked_6387": _is_blocked_trade(t6387),
        "day_blocked_losses_yen_100": blocked_loss,
        "day_blocked_winners_yen_100": blocked_win,
    }


def _load_enriched(repo: Path, *, live_start: str, end: str) -> list[dict[str, Any]]:
    kabu = resolve_kabu_root(repo)
    accepted = _load_b_runtime_accepted(repo, live_start=live_start, end=end)
    days = sorted({str(t.get("day") or "")[:8] for t in accepted})
    symbols = sorted({str(t.get("symbol") or "").replace(".T", "") for t in accepted})
    price_idx = _build_price_index_to(kabu, period_end=end)
    bar_cache = _build_bar_cache_for_days(repo, days=days, symbols=symbols, price_idx=price_idx)
    micro = _build_micro_lookup(accepted)
    board_snaps_by_day = {day: _load_day_event_snaps(kabu, day) for day in days}
    return _enrich_phase554(
        accepted,
        bar_cache=bar_cache,
        micro_lookup=micro,
        board_snaps_by_day=board_snaps_by_day,
    )


def _mandatory_answers(
    summary: Sequence[Mapping[str, Any]],
    day618: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    by_id = {str(r["variant_id"]): r for r in summary}
    d618 = {str(r["variant_id"]): r for r in day618}
    baseline = by_id.get("V0", {})

    def _ok(vid: str) -> bool:
        r = by_id.get(vid, {})
        return _num(r.get("net_improvement_yen_100")) > 0 and int(r.get("success_score") or 0) >= 5

    shadow = [r["variant_id"] for r in summary if str(r.get("classification", "")).startswith("B_")]
    runtime = [r["variant_id"] for r in summary if str(r.get("classification", "")).startswith("A_")]
    v4 = d618.get("V4", {})
    v5 = d618.get("V5", {})
    v6 = d618.get("V6", {})

    return {
        "1_G554_021_effective": _ok("V1"),
        "2_G554_031_effective": _ok("V2"),
        "3_G554_022_effective": _ok("V3"),
        "4_618_top3_blocked": {
            "6779": d618.get("V1", {}).get("blocked_6779"),
            "6976_am": d618.get("V1", {}).get("blocked_6976_am_loss"),
            "6387": d618.get("V1", {}).get("blocked_6387"),
        },
        "5_6976_pm_winner_kept_reentry": v4.get("kept_6976_pm_winner"),
        "6_reentry_rescue_effective": _ok("V4") and bool(v4.get("kept_6976_pm_winner")),
        "7_liquidity_burst_rescue_effective": _ok("V5"),
        "8_high_update_rescue_effective": _ok("V6"),
        "9_winner_over_cut_risk": any(int(by_id.get(v, {}).get("blocked_winners") or 0) > 5 for v in ("V1", "V2", "V3")),
        "10_shadow_candidates": shadow,
        "11_runtime_candidates": runtime,
        "12_next_phase": "phase556_stop_low_mfe_guard_production_readiness",
        "best_variant": max(
            (r for r in summary if r.get("variant_id") != "V0"),
            key=lambda r: (_num(r.get("success_score")), _num(r.get("net_improvement_yen_100"))),
            default={},
        ).get("variant_id"),
        "baseline_pnl_yen_100": baseline.get("pnl_yen_100"),
        "V1_summary": {k: by_id.get("V1", {}).get(k) for k in ("pnl_yen_100", "profit_factor", "net_improvement_yen_100", "retention", "lost_big_winner")},
        "V4_day618_net": v4.get("day_net_improvement_yen_100"),
    }


@dataclass
class Phase555Job:
    repo_root: Path
    live_start: str = PERIOD_START_LIVE
    live_end: str = LIVE_END_DEFAULT

    def run(self) -> dict[str, Any]:
        repo = self.repo_root.resolve()
        end = min(self.live_end, _latest_live_day(repo))
        enriched = _load_enriched(repo, live_start=self.live_start, end=end)
        lb_vals = [_float(t.get("liquidity_burst")) for t in enriched]
        lb_p75 = _percentile([v for v in lb_vals if v is not None], 75) or E4_THRESHOLD

        baseline_spec = VARIANTS[0]
        baseline = _evaluate_variant(
            enriched, baseline_spec, baseline_pnl=0.0, baseline_trades=len(enriched), lb_p75=lb_p75
        )
        baseline_pnl = _num(baseline.get("pnl_yen_100"))
        baseline_trades = len(enriched)

        day618_baseline_pnl = round(
            sum(_num(t.get("pnl_yen_100")) for t in enriched if str(t.get("day") or "")[:8] == TARGET_DAY),
            2,
        )

        summary_rows: list[dict[str, Any]] = []
        detail_rows: list[dict[str, Any]] = []
        dependency_rows: list[dict[str, Any]] = []
        day618_rows: list[dict[str, Any]] = []

        for spec in VARIANTS:
            result = _evaluate_variant(
                enriched,
                spec,
                baseline_pnl=baseline_pnl,
                baseline_trades=baseline_trades,
                lb_p75=lb_p75,
            )
            day618 = _day618_eval(spec, enriched, baseline_day_pnl=day618_baseline_pnl, lb_p75=lb_p75)
            score, criteria = _success_criteria(result, baseline, day618=day618)
            classification = _classify_variant(spec, result, success_score=score)
            row = {
                "variant_id": spec.variant_id,
                "label": spec.label,
                "trades": result.get("trades"),
                "pnl_yen_100": result.get("pnl_yen_100"),
                "profit_factor": result.get("profit_factor"),
                "max_drawdown_yen_100": result.get("max_drawdown_yen_100"),
                "win_rate": result.get("win_rate"),
                "mfe0_count": result.get("mfe0_count"),
                "stop_low_mfe_count": result.get("stop_low_mfe_count"),
                "no_progress_count": result.get("no_progress_count"),
                "big_winner_count": result.get("big_winner_count"),
                "lost_big_winner": result.get("lost_big_winner"),
                "blocked_trades": result.get("blocked_trades"),
                "blocked_losses": result.get("blocked_losses"),
                "blocked_winners": result.get("blocked_winners"),
                "blocked_big_winners": result.get("blocked_big_winners"),
                "net_improvement_yen_100": result.get("net_improvement_yen_100"),
                "retention": result.get("retention"),
                "success_score": score,
                "success_criteria_met": "|".join(criteria),
                "classification": classification,
            }
            summary_rows.append(row)
            detail_rows.append(
                {
                    **row,
                    "delta_pnl_vs_baseline": round(_num(result.get("pnl_yen_100")) - baseline_pnl, 2),
                    "delta_pf_vs_baseline": round(_num(result.get("profit_factor")) - _num(baseline.get("profit_factor")), 4),
                    "delta_stop_low_mfe_vs_baseline": int(result.get("stop_low_mfe_count") or 0)
                    - int(baseline.get("stop_low_mfe_count") or 0),
                    "delta_mfe0_vs_baseline": int(result.get("mfe0_count") or 0) - int(baseline.get("mfe0_count") or 0),
                    "delta_lost_big_winner_vs_baseline": int(result.get("lost_big_winner") or 0),
                }
            )
            if spec.variant_id != "V0":
                dependency_rows.append(_dependency_row(spec, result, baseline_pnl=baseline_pnl))
            day618_rows.append(day618)

        answers = _mandatory_answers(summary_rows, day618_rows)
        return {
            "verdict": PHASE555_VERDICT,
            "generated_at": _now_iso(),
            "period": f"{self.live_start}-{end}",
            "trade_count": len(enriched),
            "baseline_pnl_yen_100": baseline_pnl,
            "liquidity_burst_p75": lb_p75,
            "summary": summary_rows,
            "detail": detail_rows,
            "dependency_audit": dependency_rows,
            "day618": day618_rows,
            "mandatory_answers": answers,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        paths = {
            "summary": reports / "phase555_guard_replay_summary.csv",
            "detail": reports / "phase555_guard_replay_detail.csv",
            "day618": reports / "phase555_20260618_detail.csv",
            "dependency": reports / "phase555_dependency_audit.csv",
            "report": reports / "phase555_report.json",
            "docs": kabu / "docs" / "operations" / "phase555_stop_low_mfe_guard_full_path_shadow_replay.md",
        }
        _write_csv(paths["summary"], SUMMARY_FIELDS, list(result.get("summary") or []))
        _write_csv(paths["detail"], DETAIL_FIELDS, list(result.get("detail") or []))
        _write_csv(paths["day618"], DAY618_FIELDS, list(result.get("day618") or []))
        _write_csv(paths["dependency"], DEPENDENCY_FIELDS, list(result.get("dependency_audit") or []))
        paths["report"].write_text(json.dumps(dict(result), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        self._write_docs(paths["docs"], result)
        return paths

    def _write_docs(self, path: Path, result: Mapping[str, Any]) -> None:
        ans = result.get("mandatory_answers") or {}
        lines = [
            "# Phase555 — stop_low_mfe Guard Full-Path Shadow Replay",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            f"**Period:** {result.get('period')}",
            f"**Trades:** {result.get('trade_count')} | **Baseline PnL:** {result.get('baseline_pnl_yen_100')}",
            "",
            "## Guard variants",
            "",
            "| ID | Label | PnL | PF | net_improve | retention | lost_big | score | class |",
            "|----|-------|-----|-----|-------------|-----------|----------|-------|-------|",
        ]
        for r in result.get("summary") or []:
            lines.append(
                f"| {r.get('variant_id')} | {r.get('label')} | {r.get('pnl_yen_100')} | "
                f"{r.get('profit_factor')} | {r.get('net_improvement_yen_100')} | {r.get('retention')} | "
                f"{r.get('lost_big_winner')} | {r.get('success_score')} | {r.get('classification')} |"
            )
        lines.extend(["", "## Mandatory answers", ""])
        for k, v in sorted(ans.items()):
            lines.append(f"- **{k}:** {v}")
        lines.extend(
            [
                "",
                "## Output files",
                "",
                "- `results/reports/phase555_guard_replay_summary.csv`",
                "- `results/reports/phase555_guard_replay_detail.csv`",
                "- `results/reports/phase555_20260618_detail.csv`",
                "- `results/reports/phase555_dependency_audit.csv`",
                "- `results/reports/phase555_report.json`",
            ]
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
