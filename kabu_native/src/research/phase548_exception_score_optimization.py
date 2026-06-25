"""
Phase548 — Exception score optimization for V6 reject rescue (research only).

Multi-feature exception score vs single-feature rules (E4/E1/E5/E10).
No Runtime changes. No adoption.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase451_entry_shape_tournament import _now_iso
from research.phase515b_day_high_breakout_dependency_audit import SYMBOL_6976
from research.phase518_day_high_winner_loser_separation import _percentile
from research.phase524_live_reentry_guard_and_stop_low_mfe import _num
from research.phase540_no_progress_mfe0_entry_quality import _is_mfe0, _is_winner
from research.phase545b_recursive_cluster_refinement import _as_bool
from research.phase546_entry_cluster_shadow_replay import _is_big_winner_row, _is_rejected
from research.phase547_reject_cluster_winner_rescue import (
    V6_SPEC,
    _build_exception_fns,
    _enrich_trades,
    _evaluate_variant,
    _open_strength_proxy,
    _period_thresholds,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE548_VERDICT = "phase548_exception_score_optimization_done"

SCORE_THRESHOLDS: tuple[int, ...] = (2, 3, 4, 5)
SINGLE_COMPARE: tuple[str, ...] = ("E4", "E1", "E5", "E10")

SCORE_COMPONENTS: tuple[tuple[str, str, int], ...] = (
    ("liquidity_burst_high", "liquidity_burst >= p75", 2),
    ("vwap_recovery_fast", "vwap_recovery_min <= median", 2),
    ("update_count_high", "update_count_before_entry >= median", 1),
    ("relative_volume_high", "relative_volume >= p75", 1),
    ("day_leader", "day_return_rank <= 20", 1),
    ("board_strong", "board_imbalance >= 0.60", 1),
    ("open_strength", "open_strength == true", 1),
)

SUMMARY_FIELDS = [
    "variant_id",
    "label",
    "score_threshold",
    "rescued_trades",
    "rescued_winners",
    "rescued_big_winners",
    "reintroduced_losers",
    "reintroduced_mfe0",
    "pnl_yen_100",
    "profit_factor",
    "max_drawdown_yen_100",
    "win_rate",
    "trade_retention_rate",
    "net_improvement_vs_v6_yen_100",
    "beats_e4_pnl",
    "beats_e4_pf",
    "beats_e4_rec_big",
    "success_score",
    "shadow_candidate",
    "runtime_candidate",
]

DETAIL_FIELDS = SUMMARY_FIELDS + [
    "mfe0_count",
    "stop_low_mfe_count",
    "big_winner_count",
    "reintroduced_loss_pnl_yen_100",
    "recovered_winner_pnl_yen_100",
    "net_improvement_vs_baseline_yen_100",
    "mfe0_within_e4_budget",
    "retention_beats_e4",
]

DEPENDENCY_FIELDS = [
    "variant_id",
    "score_threshold",
    "top10_trade_exclusion_pnl_yen_100",
    "top3_symbol_exclusion_pnl_yen_100",
    "top3_day_exclusion_pnl_yen_100",
    "symbol_6976_exclusion_pnl_yen_100",
    "exception_rescue_top_symbol",
    "exception_rescue_top_share_pct",
]

RANKING_FIELDS = [
    "rank",
    "variant_id",
    "label",
    "score_threshold",
    "pnl_yen_100",
    "profit_factor",
    "rescued_big_winners",
    "reintroduced_mfe0",
    "trade_retention_rate",
    "net_improvement_vs_v6_yen_100",
    "priority_score",
    "shadow_candidate",
]


def _float(v: Any) -> Optional[float]:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _compute_score_thresholds(trades: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    base = _period_thresholds(trades)
    lb = [_float(t.get("liquidity_burst")) for t in trades]
    rv = [_float(t.get("relative_volume")) for t in trades]
    vr = [_float(t.get("vwap_recovery_min")) for t in trades]
    uc = [_float(t.get("update_count_before_entry")) for t in trades]
    lb_n = [v for v in lb if v is not None]
    rv_n = [v for v in rv if v is not None]
    vr_n = [v for v in vr if v is not None]
    uc_n = [v for v in uc if v is not None]
    return {
        **base,
        "liquidity_burst_p75": base.get("liquidity_burst_p75") or (_percentile(lb_n, 75) or 0.0),
        "relative_volume_p75": _percentile(rv_n, 75) or 0.0,
        "vwap_recovery_min_median": statistics.median(vr_n) if vr_n else 999.0,
        "update_count_median": statistics.median(uc_n) if uc_n else 0.0,
    }


def _score_components(row: Mapping[str, Any], thr: Mapping[str, float]) -> dict[str, int]:
    pts: dict[str, int] = {}
    lb = _float(row.get("liquidity_burst")) or 0.0
    vr = _float(row.get("vwap_recovery_min"))
    uc = _float(row.get("update_count_before_entry"))
    rel = _float(row.get("relative_volume")) or 0.0
    rank = _float(row.get("day_return_rank")) or 999.0
    board = _float(row.get("board_imbalance")) or 0.0

    pts["liquidity_burst_high"] = 2 if lb >= float(thr.get("liquidity_burst_p75") or 0) else 0
    pts["vwap_recovery_fast"] = 2 if vr is not None and vr <= float(thr.get("vwap_recovery_min_median") or 999) else 0
    pts["update_count_high"] = 1 if uc is not None and uc >= float(thr.get("update_count_median") or 0) else 0
    pts["relative_volume_high"] = 1 if rel >= float(thr.get("relative_volume_p75") or 0) else 0
    pts["day_leader"] = 1 if rank <= 20.0 else 0
    pts["board_strong"] = 1 if board >= 0.60 else 0
    pts["open_strength"] = 1 if _open_strength_proxy(row) else 0
    return pts


def _exception_score(row: Mapping[str, Any], thr: Mapping[str, float]) -> int:
    return sum(_score_components(row, thr).values())


def _score_exception_fn(thr: Mapping[str, float], min_score: int) -> Callable[[Mapping[str, Any]], bool]:
    return lambda r: _exception_score(r, thr) >= min_score


def _dependency_row(
    variant_id: str,
    score_threshold: Any,
    result: Mapping[str, Any],
    *,
    baseline_net: float,
    recovered: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    blocked = list(result.get("_blocked") or [])
    net = round(_num(result.get("net_improvement_vs_baseline_yen_100")), 2)
    sym_delta: dict[str, float] = defaultdict(float)
    day_delta: dict[str, float] = defaultdict(float)
    for t in blocked:
        pnl = _num(t.get("pnl_yen_100"))
        sym_delta[str(t.get("symbol") or "").replace(".T", "")] -= pnl
        day_delta[str(t.get("day") or "")[:8]] -= pnl
    sym_sorted = sorted(sym_delta.items(), key=lambda x: x[1], reverse=True)
    day_sorted = sorted(day_delta.items(), key=lambda x: x[1], reverse=True)
    top3_sym = round(sum(v for _, v in sym_sorted[:3]), 2)
    top3_day = round(sum(v for _, v in day_sorted[:3]), 2)
    top10 = sorted(blocked, key=lambda t: _num(t.get("pnl_yen_100")))[:10]
    sym6976 = sym_delta.get(SYMBOL_6976, 0.0)
    rescue_sym = Counter(str(t.get("symbol") or "").replace(".T", "") for t in recovered)
    top_rescue = rescue_sym.most_common(1)
    rescue_total = len(recovered) or 1
    return {
        "variant_id": variant_id,
        "score_threshold": score_threshold,
        "top10_trade_exclusion_pnl_yen_100": round(net + sum(_num(t.get("pnl_yen_100")) for t in top10), 2),
        "top3_symbol_exclusion_pnl_yen_100": round(net - top3_sym, 2),
        "top3_day_exclusion_pnl_yen_100": round(net - top3_day, 2),
        "symbol_6976_exclusion_pnl_yen_100": round(net - sym6976, 2),
        "exception_rescue_top_symbol": top_rescue[0][0] if top_rescue else "",
        "exception_rescue_top_share_pct": round(top_rescue[0][1] / rescue_total * 100.0, 2) if top_rescue else 0.0,
    }


def _variant_row(
    variant_id: str,
    label: str,
    ev: Mapping[str, Any],
    *,
    score_threshold: Any,
    e4_ref: Mapping[str, Any],
) -> dict[str, Any]:
    rescued = int(ev.get("recovered_winner_count") or 0) + int(ev.get("reintroduced_loser_count") or 0)
    beats_pnl = _num(ev.get("pnl_yen_100")) >= _num(e4_ref.get("pnl_yen_100"))
    beats_pf = _num(ev.get("profit_factor")) >= _num(e4_ref.get("profit_factor"))
    beats_big = int(ev.get("recovered_big_winner_count") or 0) > int(e4_ref.get("recovered_big_winner_count") or 0)
    mfe0_ok = int(ev.get("reintroduced_mfe0_count") or 0) <= int(e4_ref.get("reintroduced_mfe0_count") or 0) + 10
    ret_ok = _num(ev.get("trade_retention_rate")) > _num(e4_ref.get("trade_retention_rate"))
    success = sum([beats_pnl, beats_pf, beats_big, mfe0_ok, ret_ok])
    shadow = beats_pnl and beats_pf and mfe0_ok and int(ev.get("recovered_big_winner_count") or 0) >= int(
        e4_ref.get("recovered_big_winner_count") or 0
    )
    return {
        "variant_id": variant_id,
        "label": label,
        "score_threshold": score_threshold,
        "rescued_trades": rescued,
        "rescued_winners": ev.get("recovered_winner_count"),
        "rescued_big_winners": ev.get("recovered_big_winner_count"),
        "reintroduced_losers": ev.get("reintroduced_loser_count"),
        "reintroduced_mfe0": ev.get("reintroduced_mfe0_count"),
        "pnl_yen_100": ev.get("pnl_yen_100"),
        "profit_factor": ev.get("profit_factor"),
        "max_drawdown_yen_100": ev.get("max_drawdown_yen_100"),
        "win_rate": ev.get("win_rate"),
        "trade_retention_rate": ev.get("trade_retention_rate"),
        "net_improvement_vs_v6_yen_100": ev.get("net_improvement_vs_v6_yen_100"),
        "beats_e4_pnl": beats_pnl,
        "beats_e4_pf": beats_pf,
        "beats_e4_rec_big": beats_big,
        "success_score": success,
        "shadow_candidate": shadow,
        "runtime_candidate": False,
        "mfe0_count": ev.get("mfe0_count"),
        "stop_low_mfe_count": ev.get("stop_low_mfe_count"),
        "big_winner_count": ev.get("big_winner_count"),
        "reintroduced_loss_pnl_yen_100": ev.get("reintroduced_loss_pnl_yen_100"),
        "recovered_winner_pnl_yen_100": ev.get("recovered_winner_pnl_yen_100"),
        "net_improvement_vs_baseline_yen_100": ev.get("net_improvement_vs_baseline_yen_100"),
        "mfe0_within_e4_budget": mfe0_ok,
        "retention_beats_e4": ret_ok,
        "_ev": ev,
    }


def _priority_score(row: Mapping[str, Any]) -> float:
    return (
        _num(row.get("pnl_yen_100")) * 0.4
        + _num(row.get("profit_factor")) * 50000.0
        + int(row.get("rescued_big_winners") or 0) * 8000.0
        - int(row.get("reintroduced_mfe0") or 0) * 2000.0
        + _num(row.get("trade_retention_rate")) * 10000.0
    )


def _mandatory_answers(
    rows: Sequence[Mapping[str, Any]],
    *,
    e4_ref: Mapping[str, Any],
    score_thr: Mapping[str, float],
    best: Mapping[str, Any],
) -> dict[str, Any]:
    score_rows = [r for r in rows if str(r.get("variant_id", "")).startswith("SCORE")]
    beats_e4 = [
        r
        for r in score_rows
        if _num(r.get("pnl_yen_100")) >= _num(e4_ref.get("pnl_yen_100"))
        and _num(r.get("profit_factor")) >= _num(e4_ref.get("profit_factor"))
    ]
    return {
        "1_best_score_composition": {c[0]: c[2] for c in SCORE_COMPONENTS},
        "2_best_score_threshold": best.get("score_threshold"),
        "3_rescued_winner_count": best.get("rescued_winners"),
        "4_rescued_big_winner_count": best.get("rescued_big_winners"),
        "5_reintroduced_loser_count": best.get("reintroduced_losers"),
        "6_reintroduced_mfe0_count": best.get("reintroduced_mfe0"),
        "7_pnl_yen_100": best.get("pnl_yen_100"),
        "8_profit_factor": best.get("profit_factor"),
        "9_retention": best.get("trade_retention_rate"),
        "10_beats_e4": _num(best.get("pnl_yen_100")) >= _num(e4_ref.get("pnl_yen_100"))
        and _num(best.get("profit_factor")) >= _num(e4_ref.get("profit_factor")),
        "11_shadow_candidates": [r.get("variant_id") for r in rows if r.get("shadow_candidate")],
        "12_runtime_candidate": False,
        "13_next_phase": "phase549_entry_cluster_shadow_monitor",
        "score_thresholds_used": score_thr,
        "score_beats_e4_count": len(beats_e4),
    }


def _render_docs(result: Mapping[str, Any]) -> str:
    ma = result.get("mandatory_answers") or {}
    ranking = list(result.get("ranking") or [])[:8]
    lines = [
        "# Phase548 — Exception Score Optimization",
        "",
        f"**Verdict:** `{result.get('verdict')}`",
        "**Runtime変更:** なし / **採用:** なし",
        "",
        "## Score components (max 9)",
        "",
    ]
    for key, rule, pts in SCORE_COMPONENTS:
        lines.append(f"- `{key}` ({rule}): +{pts}")
    lines.extend(["", "## Ranking", ""])
    for r in ranking:
        lines.append(
            f"- #{r.get('rank')} {r.get('variant_id')}: PnL={r.get('pnl_yen_100')} "
            f"PF={r.get('profit_factor')} rec_big={r.get('rescued_big_winners')}"
        )
    lines.extend(["", "## Mandatory answers", ""])
    for k, v in ma.items():
        lines.append(f"- **{k}:** {v}")
    return "\n".join(lines) + "\n"


@dataclass
class Phase548Job:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        repo = self.repo_root.resolve()
        reports = resolve_reports_dir(repo)
        trades = _enrich_trades(reports)
        rejected = [t for t in trades if _is_rejected(t, V6_SPEC)]
        baseline_pnl = round(sum(_num(t.get("pnl_yen_100")) for t in trades), 2)
        total_trades = len(trades)

        score_thr = _compute_score_thresholds(trades)
        exception_defs = _build_exception_fns(score_thr)

        v6_ev = _evaluate_variant(trades, exception_fn=None, baseline_pnl=baseline_pnl, v6_pnl=0.0, total_trades=total_trades)
        v6_pnl = _num(v6_ev.get("pnl_yen_100"))
        v6_ev["net_improvement_vs_v6_yen_100"] = 0.0

        evals: dict[str, dict[str, Any]] = {"V6": v6_ev}
        for eid in SINGLE_COMPARE:
            _, _, fn = exception_defs[eid]
            evals[eid] = _evaluate_variant(
                trades, exception_fn=fn, baseline_pnl=baseline_pnl, v6_pnl=v6_pnl, total_trades=total_trades
            )

        e4_ref_ev = evals["E4"]
        for t in SCORE_THRESHOLDS:
            vid = f"SCORE>={t}"
            evals[vid] = _evaluate_variant(
                trades,
                exception_fn=_score_exception_fn(score_thr, t),
                baseline_pnl=baseline_pnl,
                v6_pnl=v6_pnl,
                total_trades=total_trades,
            )

        summary_rows: list[dict[str, Any]] = []
        summary_rows.append(
            _variant_row("V6", "Balanced Reject (baseline)", v6_ev, score_threshold="", e4_ref=e4_ref_ev)
        )
        for eid in SINGLE_COMPARE:
            label, rule, _ = exception_defs[eid]
            summary_rows.append(_variant_row(eid, label, evals[eid], score_threshold=eid, e4_ref=e4_ref_ev))
        for t in SCORE_THRESHOLDS:
            vid = f"SCORE>={t}"
            summary_rows.append(
                _variant_row(vid, f"Exception Score >= {t}", evals[vid], score_threshold=t, e4_ref=e4_ref_ev)
            )

        detail_rows = [{k: v for k, v in r.items() if k != "_ev"} for r in summary_rows]

        dependency_rows: list[dict[str, Any]] = []
        for r in summary_rows:
            ev = r.get("_ev") or {}
            dependency_rows.append(
                _dependency_row(
                    str(r.get("variant_id")),
                    r.get("score_threshold"),
                    ev,
                    baseline_net=_num(ev.get("net_improvement_vs_baseline_yen_100")),
                    recovered=list(ev.get("_recovered") or []),
                )
            )

        score_only = [r for r in summary_rows if str(r.get("variant_id", "")).startswith("SCORE")]
        best = max(
            score_only,
            key=lambda r: (
                int(r.get("success_score") or 0),
                _num(r.get("pnl_yen_100")),
                int(r.get("rescued_big_winners") or 0),
                -int(r.get("reintroduced_mfe0") or 0),
            ),
            default={},
        )

        ranking_rows: list[dict[str, Any]] = []
        for r in summary_rows:
            clean = {k: v for k, v in r.items() if k != "_ev"}
            clean["priority_score"] = round(_priority_score(clean), 2)
            ranking_rows.append(clean)
        ranking_rows.sort(key=lambda x: x.get("priority_score") or 0, reverse=True)
        for i, row in enumerate(ranking_rows, start=1):
            row["rank"] = i
        ranking = [{k: r.get(k) for k in RANKING_FIELDS} for r in ranking_rows]

        reject_score_rows: list[dict[str, Any]] = []
        for t in rejected:
            comps = _score_components(t, score_thr)
            reject_score_rows.append(
                {
                    "symbol": t.get("symbol"),
                    "entry_time": t.get("entry_time"),
                    "pnl_yen_100": t.get("pnl_yen_100"),
                    "is_winner": _is_winner(t),
                    "is_big_winner": _is_big_winner_row(t),
                    "exception_score": sum(comps.values()),
                    **comps,
                }
            )

        answers = _mandatory_answers(summary_rows, e4_ref=e4_ref_ev, score_thr=score_thr, best=best)

        return {
            "verdict": PHASE548_VERDICT,
            "generated_at": _now_iso(),
            "trade_count": total_trades,
            "v6_rejected_count": len(rejected),
            "score_thresholds": score_thr,
            "e4_reference": {k: e4_ref_ev.get(k) for k in (
                "pnl_yen_100", "profit_factor", "recovered_big_winner_count",
                "reintroduced_mfe0_count", "trade_retention_rate",
            )},
            "v6_reference": {k: v6_ev.get(k) for k in ("pnl_yen_100", "profit_factor", "trade_retention_rate")},
            "summary": detail_rows,
            "dependency": dependency_rows,
            "ranking": [{k: r.get(k) for k in RANKING_FIELDS if k in r or k == "rank"} for r in ranking],
            "reject_score_distribution": reject_score_rows,
            "best_variant": {k: best.get(k) for k in SUMMARY_FIELDS if k in best},
            "mandatory_answers": answers,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        paths = {
            "summary": reports / "phase548_exception_score_summary.csv",
            "detail": reports / "phase548_exception_score_detail.csv",
            "dependency": reports / "phase548_exception_score_dependency.csv",
            "ranking": reports / "phase548_exception_score_ranking.csv",
            "report": reports / "phase548_report.json",
            "docs": kabu / "docs" / "operations" / "phase548_exception_score_optimization.md",
        }
        rows = list(result.get("summary") or [])
        _write_csv(paths["summary"], SUMMARY_FIELDS, rows)
        _write_csv(paths["detail"], DETAIL_FIELDS, rows)
        _write_csv(paths["dependency"], DEPENDENCY_FIELDS, list(result.get("dependency") or []))
        _write_csv(paths["ranking"], RANKING_FIELDS, list(result.get("ranking") or []))
        public = {
            k: v
            for k, v in result.items()
            if k not in ("summary", "dependency", "ranking", "reject_score_distribution")
        }
        public["reject_score_sample_count"] = len(result.get("reject_score_distribution") or [])
        paths["report"].write_text(json.dumps(public, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        paths["docs"].write_text(_render_docs(result), encoding="utf-8")
        return paths
