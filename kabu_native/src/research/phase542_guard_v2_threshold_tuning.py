"""
Phase542 — Guard v2 threshold tuning (research only).

Tunes G13-family guards for MFE0 reduction vs winner retention balance.
No Runtime changes. No adoption.
"""

from __future__ import annotations

import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase451_entry_shape_tournament import _build_price_index_to, _now_iso
from research.phase518_day_high_winner_loser_separation import _build_micro_lookup
from research.phase524_live_reentry_guard_and_stop_low_mfe import (
    _build_bar_cache_for_days,
    _is_stop_low_mfe,
    _latest_live_day,
    _num,
)
from research.phase527_entry_quality_guard import _chron_pnls
from research.phase541_guard_v2_full_period_validation import (
    BIG_WINNER_MFE_PCT,
    MAX_WORKERS,
    PERIOD_START,
    _discover_live_days,
    _enrich_trades_phase541,
    _is_mfe0,
    _is_no_progress,
    _is_winner,
    _load_canonical_trades_for_day,
    _mfe_pct,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE542_VERDICT = "phase542_guard_v2_threshold_tuning_done"
G13_GUARD_ID = "ADX30_FIVE33"

SUMMARY_FIELDS = [
    "guard_id",
    "group",
    "adx_max",
    "five_min_max",
    "ma_max",
    "total_pnl_yen_100",
    "profit_factor",
    "max_drawdown_yen_100",
    "trade_count",
    "trade_retention_rate",
    "win_rate",
    "avg_pnl_yen_100",
    "mfe0_count",
    "mfe0_reduction_rate",
    "no_progress_count",
    "no_progress_reduction_rate",
    "stop_low_mfe_count",
    "stop_low_mfe_reduction_rate",
    "blocked_trade_count",
    "blocked_future_pnl_yen_100",
    "lost_winner_count",
    "lost_big_winner_count",
    "lost_big_winner_rate",
    "prevented_mfe0_count",
    "prevented_no_progress_count",
    "net_improvement_yen_100",
    "composite_score",
    "success_count",
    "all_success",
]

DAILY_FIELDS = [
    "day",
    "guard_id",
    "daily_pnl_yen_100",
    "daily_delta_vs_baseline_yen_100",
    "daily_pf",
    "daily_trade_count",
    "daily_mfe0_count",
    "daily_lost_big_winner_count",
]

DEPENDENCY_FIELDS = [
    "guard_id",
    "top1_symbol_contribution_yen_100",
    "top3_symbol_contribution_yen_100",
    "top1_day_contribution_yen_100",
    "top3_day_contribution_yen_100",
    "top10_trade_exclusion_net_yen_100",
    "top3_symbol_exclusion_net_yen_100",
    "top3_day_exclusion_net_yen_100",
]

RANKING_FIELDS = [
    "rank",
    "guard_id",
    "group",
    "composite_score",
    "total_pnl_yen_100",
    "trade_retention_rate",
    "mfe0_reduction_rate",
    "lost_big_winner_count",
    "net_improvement_yen_100",
    "all_success",
]


def _five_label(v: float) -> str:
    if abs(v - 33.3333) < 0.01:
        return "33"
    if abs(v - 50.0) < 0.01:
        return "50"
    return "66"


def _ma_label(v: float) -> str:
    if abs(v - 0.13) < 0.01:
        return "013"
    if abs(v - 0.25) < 0.01:
        return "025"
    return "050"


def build_guard_specs() -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = [
        {"guard_id": "A_baseline", "group": "baseline"},
    ]
    for adx in (30, 35, 40, 45):
        specs.append({"guard_id": f"ADX{adx}", "group": "A", "adx_max": float(adx)})
    for adx in (30, 35, 40, 45):
        for five in (33.3333, 50.0, 66.67):
            specs.append(
                {
                    "guard_id": f"ADX{adx}_FIVE{_five_label(five)}",
                    "group": "B",
                    "adx_max": float(adx),
                    "five_min_max": five,
                }
            )
    for adx in (30, 35, 40, 45):
        for ma in (0.13, 0.25, 0.50):
            specs.append(
                {
                    "guard_id": f"ADX{adx}_MA{_ma_label(ma)}",
                    "group": "C",
                    "adx_max": float(adx),
                    "ma_max": ma,
                }
            )
    for adx, five, ma in (
        (35, 50.0, 0.25),
        (40, 50.0, 0.25),
        (40, 66.67, 0.50),
        (45, 66.67, 0.50),
    ):
        specs.append(
            {
                "guard_id": f"ADX{adx}_FIVE{_five_label(five)}_MA{_ma_label(ma)}",
                "group": "D",
                "adx_max": float(adx),
                "five_min_max": five,
                "ma_max": ma,
            }
        )
    return specs


GUARD_SPECS = build_guard_specs()
GUARD_IDS: tuple[str, ...] = tuple(s["guard_id"] for s in GUARD_SPECS)


def _spec_by_id(guard_id: str) -> dict[str, Any]:
    for s in GUARD_SPECS:
        if s["guard_id"] == guard_id:
            return s
    return {"guard_id": guard_id}


def _guard_allows(feats: Mapping[str, Any], spec: Mapping[str, Any]) -> bool:
    if spec.get("guard_id") == "A_baseline" or spec.get("group") == "baseline":
        return True
    adx_max = spec.get("adx_max")
    five_max = spec.get("five_min_max")
    ma_max = spec.get("ma_max")
    adx = feats.get("adx14")
    fmp = feats.get("five_min_position")
    ma = feats.get("moving_average_position")
    if adx_max is not None:
        if adx is None or float(adx) > float(adx_max):
            return False
    if five_max is not None:
        if fmp is None or float(fmp) > float(five_max):
            return False
    if ma_max is not None:
        if ma is None or float(ma) > float(ma_max):
            return False
    return True


def _run_day_guard(
    day: str,
    spec: Mapping[str, Any],
    day_trades: Sequence[Mapping[str, Any]],
    baseline_day_pnl: float,
) -> dict[str, Any]:
    gid = str(spec["guard_id"])
    accepted: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for trade in day_trades:
        row = dict(trade)
        if _guard_allows(row, spec):
            accepted.append(row)
        else:
            blocked.append(row)
    pnls = [_num(t.get("pnl_yen_100")) for t in accepted]
    total = round(sum(pnls), 2)
    blocked_big = sum(
        1 for t in blocked if _is_winner(t) and _mfe_pct(t) > BIG_WINNER_MFE_PCT
    )
    return {
        "day": day,
        "guard_id": gid,
        "daily_pnl_yen_100": total,
        "daily_delta_vs_baseline_yen_100": round(total - baseline_day_pnl, 2),
        "daily_pf": _pf(pnls),
        "daily_trade_count": len(accepted),
        "daily_mfe0_count": sum(1 for t in accepted if _is_mfe0(t)),
        "daily_lost_big_winner_count": blocked_big,
        "_accepted": accepted,
        "_blocked": blocked,
    }


def _aggregate_summary(
    raw: Sequence[Mapping[str, Any]],
    *,
    baseline_pnl: float,
    baseline_trades: int,
    baseline_mfe0: int,
    baseline_np: int,
    baseline_slm: int,
) -> list[dict[str, Any]]:
    by_guard: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in raw:
        by_guard[str(row.get("guard_id") or "")].append(row)

    rows: list[dict[str, Any]] = []
    for spec in GUARD_SPECS:
        gid = str(spec["guard_id"])
        parts = by_guard.get(gid, [])
        accepted: list[dict[str, Any]] = []
        blocked: list[dict[str, Any]] = []
        for p in parts:
            accepted.extend(p.get("_accepted") or [])
            blocked.extend(p.get("_blocked") or [])
        pnls = [_num(t.get("pnl_yen_100")) for t in accepted]
        total = round(sum(pnls), 2)
        wins = sum(1 for p in pnls if p > 0)
        blocked_pnls = [_num(t.get("pnl_yen_100")) for t in blocked]
        lost_winners = [t for t in blocked if _is_winner(t)]
        lost_big = [t for t in blocked if _is_winner(t) and _mfe_pct(t) > BIG_WINNER_MFE_PCT]
        prevented_mfe0 = [t for t in blocked if _is_mfe0(t)]
        prevented_np = [t for t in blocked if _is_no_progress(t)]
        mfe0_rem = sum(1 for t in accepted if _is_mfe0(t))
        np_rem = sum(1 for t in accepted if _is_no_progress(t))
        slm_rem = sum(1 for t in accepted if _is_stop_low_mfe(t))
        tc = len(accepted)
        rows.append(
            {
                "guard_id": gid,
                "group": spec.get("group", ""),
                "adx_max": spec.get("adx_max"),
                "five_min_max": spec.get("five_min_max"),
                "ma_max": spec.get("ma_max"),
                "total_pnl_yen_100": total,
                "profit_factor": _pf(pnls),
                "max_drawdown_yen_100": round(
                    _max_drawdown_yen(_chron_pnls(accepted)) if accepted else 0.0, 2
                ),
                "trade_count": tc,
                "trade_retention_rate": round(tc / baseline_trades, 4) if baseline_trades else 0.0,
                "win_rate": round(wins / tc, 4) if tc else 0.0,
                "avg_pnl_yen_100": round(total / tc, 2) if tc else 0.0,
                "mfe0_count": mfe0_rem,
                "mfe0_reduction_rate": round((baseline_mfe0 - mfe0_rem) / baseline_mfe0, 4)
                if baseline_mfe0
                else 0.0,
                "no_progress_count": np_rem,
                "no_progress_reduction_rate": round((baseline_np - np_rem) / baseline_np, 4)
                if baseline_np
                else 0.0,
                "stop_low_mfe_count": slm_rem,
                "stop_low_mfe_reduction_rate": round((baseline_slm - slm_rem) / baseline_slm, 4)
                if baseline_slm
                else 0.0,
                "blocked_trade_count": len(blocked),
                "blocked_future_pnl_yen_100": round(sum(blocked_pnls), 2),
                "lost_winner_count": len(lost_winners),
                "lost_big_winner_count": len(lost_big),
                "lost_big_winner_rate": round(len(lost_big) / len(blocked), 4) if blocked else 0.0,
                "prevented_mfe0_count": len(prevented_mfe0),
                "prevented_no_progress_count": len(prevented_np),
                "net_improvement_yen_100": round(total - baseline_pnl, 2),
                "_accepted": accepted,
                "_blocked": blocked,
            }
        )
    return rows


def _dependency_rows(
    summary: Sequence[Mapping[str, Any]],
    *,
    baseline_pnl: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for s in summary:
        gid = str(s.get("guard_id") or "")
        if gid == "A_baseline":
            continue
        blocked = list(s.get("_blocked") or [])
        accepted = list(s.get("_accepted") or [])
        net = round(sum(_num(t.get("pnl_yen_100")) for t in accepted) - baseline_pnl, 2)
        sym_delta: dict[str, float] = defaultdict(float)
        day_delta: dict[str, float] = defaultdict(float)
        for t in blocked:
            pnl = _num(t.get("pnl_yen_100"))
            sym = str(t.get("symbol") or "").replace(".T", "")
            day = str(t.get("day") or "")[:8]
            sym_delta[sym] -= pnl
            day_delta[day] -= pnl
        sym_sorted = sorted(sym_delta.items(), key=lambda x: x[1], reverse=True)
        day_sorted = sorted(day_delta.items(), key=lambda x: x[1], reverse=True)
        top3_sym = round(sum(v for _, v in sym_sorted[:3]), 2)
        top3_day = round(sum(v for _, v in day_sorted[:3]), 2)
        top10 = sorted(blocked, key=lambda t: _num(t.get("pnl_yen_100")))[:10]
        rows.append(
            {
                "guard_id": gid,
                "top1_symbol_contribution_yen_100": round(sym_sorted[0][1], 2) if sym_sorted else 0.0,
                "top3_symbol_contribution_yen_100": top3_sym,
                "top1_day_contribution_yen_100": round(day_sorted[0][1], 2) if day_sorted else 0.0,
                "top3_day_contribution_yen_100": top3_day,
                "top10_trade_exclusion_net_yen_100": round(
                    net + sum(_num(t.get("pnl_yen_100")) for t in top10), 2
                ),
                "top3_symbol_exclusion_net_yen_100": round(net - top3_sym, 2),
                "top3_day_exclusion_net_yen_100": round(net - top3_day, 2),
            }
        )
    return rows


def _daily_stability(daily_rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    by_guard: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in daily_rows:
        by_guard[str(row.get("guard_id") or "")].append(row)
    out: dict[str, dict[str, Any]] = {}
    for gid, rows in by_guard.items():
        if gid == "A_baseline":
            continue
        deltas = [_num(r.get("daily_delta_vs_baseline_yen_100")) for r in rows]
        positive = sum(1 for r in rows if _num(r.get("daily_pnl_yen_100")) > 0)
        improve = sum(1 for d in deltas if d > 0)
        out[gid] = {
            "positive_day_rate": round(positive / len(rows), 4) if rows else 0.0,
            "improvement_day_rate": round(improve / len(rows), 4) if rows else 0.0,
            "worst_day_delta": round(min(deltas), 2) if deltas else 0.0,
            "best_day_delta": round(max(deltas), 2) if deltas else 0.0,
        }
    return out


def _success_criteria(
    summary: Sequence[Mapping[str, Any]],
    dependency: Sequence[Mapping[str, Any]],
    stability: Mapping[str, Mapping[str, Any]],
    *,
    g13_lost_big: int,
    g13_top3_sym_excl: float,
    g13_top3_day_excl: float,
) -> dict[str, dict[str, bool]]:
    baseline = next((s for s in summary if s.get("guard_id") == "A_baseline"), {})
    dep_by = {str(d.get("guard_id")): d for d in dependency}
    out: dict[str, dict[str, bool]] = {}
    for s in summary:
        gid = str(s.get("guard_id") or "")
        if gid == "A_baseline":
            continue
        dep = dep_by.get(gid, {})
        stab = stability.get(gid, {})
        top3_sym_excl = _num(dep.get("top3_symbol_exclusion_net_yen_100"))
        top3_day_excl = _num(dep.get("top3_day_exclusion_net_yen_100"))
        checks = {
            "pnl_gt_baseline": _num(s.get("total_pnl_yen_100")) > _num(baseline.get("total_pnl_yen_100")),
            "pf_gte_baseline": _num(s.get("profit_factor")) >= _num(baseline.get("profit_factor")),
            "maxdd_lte_baseline": _num(s.get("max_drawdown_yen_100")) <= _num(
                baseline.get("max_drawdown_yen_100")
            ),
            "mfe0_lte_half_baseline": int(s.get("mfe0_count") or 0)
            <= int(baseline.get("mfe0_count") or 0) * 0.5,
            "np_lte_70pct_baseline": int(s.get("no_progress_count") or 0)
            <= int(baseline.get("no_progress_count") or 0) * 0.7,
            "trade_retention_gte_30pct": _num(s.get("trade_retention_rate")) >= 0.3,
            "lost_big_winner_lte_70pct_g13": int(s.get("lost_big_winner_count") or 0)
            <= int(g13_lost_big * 0.7),
            "improvement_day_rate_gte_60": _num(stab.get("improvement_day_rate")) >= 0.6,
            "top3_symbol_exclusion_ok": top3_sym_excl > 0 or top3_sym_excl > g13_top3_sym_excl,
            "top3_day_exclusion_better_than_g13": top3_day_excl > g13_top3_day_excl,
        }
        out[gid] = checks
    return out


def _composite_score(
    s: Mapping[str, Any],
    baseline: Mapping[str, Any],
    g13: Mapping[str, Any],
    stability: Mapping[str, Any],
) -> float:
    b_pnl = _num(baseline.get("total_pnl_yen_100"))
    b_pf = max(_num(baseline.get("profit_factor")), 0.01)
    b_dd = max(_num(baseline.get("max_drawdown_yen_100")), 1.0)
    b_mfe0 = max(int(baseline.get("mfe0_count") or 0), 1)
    g13_lbw = max(int(g13.get("lost_big_winner_count") or 0), 1)

    pnl_imp = (_num(s.get("total_pnl_yen_100")) - b_pnl) / max(abs(b_pnl), 1.0)
    pf_imp = (_num(s.get("profit_factor")) - b_pf) / b_pf
    dd_red = (b_dd - _num(s.get("max_drawdown_yen_100"))) / b_dd
    mfe0_red = _num(s.get("mfe0_reduction_rate"))
    retention = _num(s.get("trade_retention_rate"))
    lbw_red = (g13_lbw - int(s.get("lost_big_winner_count") or 0)) / g13_lbw
    daily_stab = _num(stability.get("improvement_day_rate"))

    raw = (
        0.25 * pnl_imp
        + 0.15 * pf_imp
        + 0.15 * dd_red
        + 0.20 * mfe0_red
        + 0.10 * retention
        + 0.10 * lbw_red
        + 0.05 * daily_stab
    )
    return round(raw, 6)


def _apply_scores(
    summary: list[dict[str, Any]],
    stability: Mapping[str, Mapping[str, Any]],
    success: Mapping[str, Mapping[str, bool]],
) -> None:
    baseline = next((s for s in summary if s.get("guard_id") == "A_baseline"), {})
    g13 = next((s for s in summary if s.get("guard_id") == G13_GUARD_ID), baseline)
    for s in summary:
        gid = str(s.get("guard_id") or "")
        if gid == "A_baseline":
            s["composite_score"] = 0.0
            s["success_count"] = 0
            s["all_success"] = False
            continue
        checks = success.get(gid, {})
        s["composite_score"] = _composite_score(s, baseline, g13, stability.get(gid, {}))
        s["success_count"] = sum(1 for v in checks.values() if v)
        s["all_success"] = all(checks.values()) if checks else False


def _ranking_rows(summary: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    candidates = [s for s in summary if s.get("guard_id") != "A_baseline"]
    ranked = sorted(candidates, key=lambda s: _num(s.get("composite_score")), reverse=True)
    rows: list[dict[str, Any]] = []
    for i, s in enumerate(ranked, start=1):
        rows.append(
            {
                "rank": i,
                "guard_id": s.get("guard_id"),
                "group": s.get("group"),
                "composite_score": s.get("composite_score"),
                "total_pnl_yen_100": s.get("total_pnl_yen_100"),
                "trade_retention_rate": s.get("trade_retention_rate"),
                "mfe0_reduction_rate": s.get("mfe0_reduction_rate"),
                "lost_big_winner_count": s.get("lost_big_winner_count"),
                "net_improvement_yen_100": s.get("net_improvement_yen_100"),
                "all_success": s.get("all_success"),
            }
        )
    return rows


def _best_in_group(summary: Sequence[Mapping[str, Any]], group: str) -> Optional[str]:
    items = [s for s in summary if s.get("group") == group and s.get("guard_id") != "A_baseline"]
    if not items:
        return None
    return str(max(items, key=lambda s: _num(s.get("composite_score"))).get("guard_id"))


def _mandatory_answers(
    summary: Sequence[Mapping[str, Any]],
    ranking: Sequence[Mapping[str, Any]],
    success: Mapping[str, Mapping[str, bool]],
) -> dict[str, Any]:
    baseline = next((s for s in summary if s.get("guard_id") == "A_baseline"), {})
    g13 = next((s for s in summary if s.get("guard_id") == G13_GUARD_ID), {})
    best = ranking[0] if ranking else {}
    best_gid = str(best.get("guard_id") or "")

    better_than_g13 = [
        s
        for s in summary
        if s.get("guard_id") not in ("A_baseline", G13_GUARD_ID)
        and _num(s.get("total_pnl_yen_100")) >= _num(g13.get("total_pnl_yen_100"))
        and _num(s.get("trade_retention_rate")) > _num(g13.get("trade_retention_rate"))
        and int(s.get("lost_big_winner_count") or 0) < int(g13.get("lost_big_winner_count") or 0)
    ]

    retention_mfe0 = [
        s
        for s in summary
        if s.get("guard_id") != "A_baseline"
        and _num(s.get("trade_retention_rate")) >= 0.3
        and _num(s.get("mfe0_reduction_rate")) >= 0.3
    ]

    lbw_better = [
        s
        for s in summary
        if s.get("guard_id") not in ("A_baseline", G13_GUARD_ID)
        and int(s.get("lost_big_winner_count") or 0) < int(g13.get("lost_big_winner_count") or 0)
    ]

    explainable_order = ("ADX35", "ADX40", "ADX35_FIVE50", "ADX40_FIVE50", "ADX35_MA025")
    explainable = next(
        (gid for gid in explainable_order if any(s.get("guard_id") == gid for s in summary)),
        best_gid,
    )

    shadow_candidates = [
        str(s.get("guard_id"))
        for s in summary
        if str(s.get("guard_id")) in {best_gid, "ADX40_FIVE50", "ADX35_FIVE50", "ADX40"}
        and _num(s.get("total_pnl_yen_100")) > _num(baseline.get("total_pnl_yen_100"))
    ]
    shadow_candidates = list(dict.fromkeys(shadow_candidates))

    any_adopt = any(success.get(str(s.get("guard_id")), {}).get("all_success") for s in summary)

    group_d_ok = any(
        _num(s.get("total_pnl_yen_100")) > _num(baseline.get("total_pnl_yen_100"))
        for s in summary
        if s.get("group") == "D"
    )

    return {
        "1_g13_too_strong": _num(g13.get("trade_retention_rate")) < 0.2,
        "2_better_balance_than_g13_exists": len(better_than_g13) > 0,
        "2_better_balance_examples": [str(s.get("guard_id")) for s in better_than_g13[:5]],
        "3_best_adx_only": _best_in_group(summary, "A"),
        "4_best_adx_five_min": _best_in_group(summary, "B"),
        "5_best_adx_ma": _best_in_group(summary, "C"),
        "6_group_d_representatives_valid": group_d_ok,
        "7_retention_and_mfe0_balance_candidates": [
            str(s.get("guard_id")) for s in sorted(retention_mfe0, key=lambda x: -_num(x.get("composite_score")))[:5]
        ],
        "8_lost_big_winner_better_than_g13": [str(s.get("guard_id")) for s in lbw_better[:8]],
        "9_best_composite_score_guard": best_gid,
        "10_most_explainable_guard": explainable,
        "11_shadow_forward_candidates": shadow_candidates,
        "12_production_adoption_candidate": any_adopt,
        "13_next_phase": (
            "Phase543: forward-shadow top 2–3 threshold guards on new live days."
            if shadow_candidates
            else "Widen thresholds further; collect more live days."
        ),
        "g13_trade_count": g13.get("trade_count"),
        "g13_trade_retention_rate": g13.get("trade_retention_rate"),
        "g13_lost_big_winner_count": g13.get("lost_big_winner_count"),
        "baseline_trade_count": baseline.get("trade_count"),
        "best_composite_score": best.get("composite_score"),
    }


@dataclass
class Phase542Job:
    repo_root: Path
    period_start: str = PERIOD_START
    period_end: Optional[str] = None
    parallel: bool = True
    max_workers: int = MAX_WORKERS

    def run(self) -> dict[str, Any]:
        repo_root = self.repo_root.resolve()
        end = self.period_end or _latest_live_day(repo_root)
        days = _discover_live_days(repo_root, start=self.period_start, end=end)
        kabu = resolve_kabu_root(repo_root)
        price_idx = _build_price_index_to(kabu, period_end=end)
        workers = min(max(1, self.max_workers), MAX_WORKERS)

        def _load_day(day: str) -> list[dict[str, Any]]:
            return _load_canonical_trades_for_day(repo_root, day, all_sessions=True)

        all_trades: list[dict[str, Any]] = []
        if self.parallel and len(days) > 1:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(_load_day, d): d for d in days}
                for fut in as_completed(futs):
                    all_trades.extend(fut.result())
        else:
            for day in days:
                all_trades.extend(_load_day(day))

        if not all_trades:
            raise RuntimeError(f"no trades for Phase542 {self.period_start}–{end}")

        symbols = sorted({str(t.get("symbol") or "").replace(".T", "") for t in all_trades})
        bar_cache = _build_bar_cache_for_days(repo_root, days=days, symbols=symbols, price_idx=price_idx)
        micro_lookup = _build_micro_lookup(all_trades)
        enriched = _enrich_trades_phase541(all_trades, bar_cache=bar_cache, micro_lookup=micro_lookup)

        by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for t in enriched:
            by_day[str(t.get("day") or "")[:8]].append(dict(t))

        baseline_pnl = round(sum(_num(t.get("pnl_yen_100")) for t in enriched), 2)
        baseline_trades = len(enriched)
        baseline_mfe0 = sum(1 for t in enriched if _is_mfe0(t))
        baseline_np = sum(1 for t in enriched if _is_no_progress(t))
        baseline_slm = sum(1 for t in enriched if _is_stop_low_mfe(t))
        baseline_by_day = {
            day: round(sum(_num(t.get("pnl_yen_100")) for t in tr), 2) for day, tr in by_day.items()
        }

        jobs = [(day, spec) for day in sorted(by_day) for spec in GUARD_SPECS]
        raw_details: list[dict[str, Any]] = []
        if self.parallel and jobs:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {
                    ex.submit(
                        _run_day_guard,
                        day,
                        spec,
                        by_day.get(day, []),
                        baseline_by_day.get(day, 0.0),
                    ): (day, spec["guard_id"])
                    for day, spec in jobs
                }
                for fut in as_completed(futs):
                    raw_details.append(fut.result())
        else:
            for day, spec in jobs:
                raw_details.append(
                    _run_day_guard(day, spec, by_day.get(day, []), baseline_by_day.get(day, 0.0))
                )

        daily_rows = [{k: v for k, v in r.items() if not k.startswith("_")} for r in raw_details]
        summary = _aggregate_summary(
            raw_details,
            baseline_pnl=baseline_pnl,
            baseline_trades=baseline_trades,
            baseline_mfe0=baseline_mfe0,
            baseline_np=baseline_np,
            baseline_slm=baseline_slm,
        )
        dependency = _dependency_rows(summary, baseline_pnl=baseline_pnl)
        stability = _daily_stability(daily_rows)

        g13_row = next((s for s in summary if s.get("guard_id") == G13_GUARD_ID), {})
        g13_dep = next((d for d in dependency if d.get("guard_id") == G13_GUARD_ID), {})
        success = _success_criteria(
            summary,
            dependency,
            stability,
            g13_lost_big=int(g13_row.get("lost_big_winner_count") or 0),
            g13_top3_sym_excl=_num(g13_dep.get("top3_symbol_exclusion_net_yen_100")),
            g13_top3_day_excl=_num(g13_dep.get("top3_day_exclusion_net_yen_100")),
        )
        _apply_scores(summary, stability, success)
        ranking = _ranking_rows(summary)
        mandatory = _mandatory_answers(summary, ranking, success)

        public_summary = [{k: v for k, v in s.items() if not k.startswith("_")} for s in summary]

        return {
            "verdict": PHASE542_VERDICT,
            "generated_at": _now_iso(),
            "period_start": self.period_start,
            "period_end": end,
            "live_days": days,
            "all_sessions": True,
            "strategy_count": len(GUARD_SPECS),
            "trade_count": baseline_trades,
            "guard_summary": public_summary,
            "guard_daily": daily_rows,
            "dependency": dependency,
            "ranking": ranking,
            "daily_stability": stability,
            "success_criteria": {k: {**v, "all_success": all(v.values())} for k, v in success.items()},
            "mandatory_answers": mandatory,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        paths = {
            "summary": reports / "phase542_guard_v2_threshold_summary.csv",
            "daily": reports / "phase542_guard_v2_threshold_daily.csv",
            "dependency": reports / "phase542_guard_v2_threshold_dependency.csv",
            "ranking": reports / "phase542_guard_v2_threshold_ranking.csv",
            "report": reports / "phase542_report.json",
            "docs": kabu / "docs" / "operations" / "phase542_guard_v2_threshold_tuning.md",
        }
        _write_csv(paths["summary"], SUMMARY_FIELDS, list(result.get("guard_summary") or []))
        _write_csv(paths["daily"], DAILY_FIELDS, list(result.get("guard_daily") or []))
        _write_csv(paths["dependency"], DEPENDENCY_FIELDS, list(result.get("dependency") or []))
        _write_csv(paths["ranking"], RANKING_FIELDS, list(result.get("ranking") or []))
        report_payload = {k: v for k, v in result.items() if k != "guard_daily"}
        paths["report"].write_text(
            json.dumps(report_payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        paths["docs"].write_text(_render_docs(result), encoding="utf-8")
        return paths


def _render_docs(result: Mapping[str, Any]) -> str:
    ma = result.get("mandatory_answers") or {}
    top3 = (result.get("ranking") or [])[:3]
    lines = [
        "# Phase542 — Guard v2 Threshold Tuning",
        "",
        f"**Verdict:** `{result.get('verdict')}`",
        f"**Period:** {result.get('period_start')} – {result.get('period_end')} (all sessions)",
        f"**Strategies:** {result.get('strategy_count')}",
        f"**Trades:** {result.get('trade_count')}",
        "",
        "## Top 3 by composite score",
        "",
    ]
    for row in top3:
        lines.append(
            f"- #{row.get('rank')} `{row.get('guard_id')}` score={row.get('composite_score')} "
            f"PnL={row.get('total_pnl_yen_100')} retention={row.get('trade_retention_rate')}"
        )
    lines.extend(["", "## Mandatory answers", ""])
    for i in range(1, 14):
        key = [k for k in ma if k.startswith(f"{i}_")]
        for k in sorted(key):
            lines.append(f"- **{k}:** {ma.get(k)}")
    lines.extend(
        [
            "",
            "## Outputs",
            "",
            "- `results/reports/phase542_guard_v2_threshold_summary.csv`",
            "- `results/reports/phase542_guard_v2_threshold_daily.csv`",
            "- `results/reports/phase542_guard_v2_threshold_dependency.csv`",
            "- `results/reports/phase542_guard_v2_threshold_ranking.csv`",
            "- `results/reports/phase542_report.json`",
            "",
            "Research only. No Runtime / EXIT adoption.",
        ]
    )
    return "\n".join(lines) + "\n"
