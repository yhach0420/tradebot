"""
Phase456 — New Entry Feature Tournament (research only).

Baseline = Phase452 Runtime: Momentum:low + Board mid|high + HD + WS + NP exit.
Tests additional ENTRY guards built from new intraday features.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv, read_jpx_sector_map
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase436_pullback_guard_redesign_shadow import guard_high_drift
from research.phase441_boundary_no_progress_overlap_audit import BEST_NP_POLICY, _precompute_np_shadows
from research.phase443_full_runtime_combined_capital_sim import (
    _chronological_pnls_from_log,
    _stop_rate_from_log,
    simulate_capacity_replay,
)
from research.phase451_entry_shape_tournament import (
    DAY_618,
    DAY_619,
    PERIOD_END,
    PERIOD_START,
    TARGET_SYMBOLS,
    _build_price_index_to,
    _enrich_candidates,
    _load_candidate_stream,
    _now_iso,
    _symbol_pnl_from_log,
)
from research.phase451b_entry_shape_tournament_mid_high import (
    _passes_baseline_mid_high,
    _runtime_entry_block_mid_high,
)
from research.phase456_entry_features import enrich_trade_phase456_features
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.weak_shape_reject_entry_guard import would_block_weak_shape_reject

REPLAY_MODE = "phase456_runtime_np"

VARIANT_FEATURE: dict[str, str] = {
    "B_high_update_age_guard": "last_high_update_age_min",
    "C_high_update_count_guard": "high_update_count_30m",
    "D_high_update_density_guard": "high_update_density_30m",
    "E_up_tick_ratio_guard": "up_tick_ratio_15m",
    "F_positive_bar_ratio_guard": "positive_bar_ratio_15m",
    "G_trend_consistency_guard": "trend_consistency_score",
    "H_vwap_duration_guard": "vwap_above_duration_min",
    "I_vwap_failed_reclaim_guard": "vwap_failed_reclaim_flag",
    "J_vwap_stability_guard": "vwap_position_stability",
    "K_sector_follow_guard": "sector_return_15m",
    "L_relative_strength_guard": "relative_strength_vs_sector",
}

COMPARISON_FIELDS = [
    "variant",
    "feature_group",
    "total_pnl_yen",
    "delta_pnl_vs_baseline",
    "profit_factor",
    "delta_pf_vs_baseline",
    "max_drawdown_yen",
    "delta_maxdd_vs_baseline",
    "stop_rate",
    "accepted_count",
    "blocked_count",
    "blocked_loss_count",
    "blocked_win_count",
    "blocked_pnl_yen",
    "remaining_pnl_yen",
    "daily_pnl_618",
    "delta_daily_pnl_618",
    "daily_pnl_619",
    "delta_daily_pnl_619",
    "symbol_pnl_6976",
    "delta_symbol_pnl_6976",
    "symbol_pnl_6920",
    "delta_symbol_pnl_6920",
    "symbol_pnl_4062",
    "delta_symbol_pnl_4062",
    "top_day_share",
    "top_symbol_share",
    "sector_subset",
]

DETAIL_FIELDS = [
    "variant",
    "symbol",
    "entry_time",
    "pnl_yen",
    "blocked_by_variant",
    "last_high_update_age_min",
    "high_update_count_30m",
    "up_tick_ratio_15m",
    "trend_consistency_score",
    "vwap_position_stability",
    "sector_return_15m",
    "relative_strength_vs_sector",
]


def _float(val: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if val is None or val == "":
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


def _pnl_yen(trade: Mapping[str, Any]) -> float:
    raw = trade.get("pnl_yen")
    if raw not in (None, ""):
        return float(raw)
    y100 = _float(trade.get("pnl_yen_100_float")) or _float(trade.get("pnl_yen_100"))
    return round(float(y100), 2) if y100 is not None else 0.0


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


def _runtime_baseline_block(trade: Mapping[str, Any]) -> bool:
    return _runtime_entry_block_mid_high(_weak_shape_block)(trade)


def _weak_shape_block(trade: Mapping[str, Any]) -> bool:
    return would_block_weak_shape_reject(_map_runtime_fields(trade))


def _entry_block(extra: Optional[Callable[[Mapping[str, Any]], bool]] = None):
    def block(trade: Mapping[str, Any]) -> bool:
        if _runtime_entry_block_mid_high(_weak_shape_block)(trade):
            return True
        if extra is not None and extra(trade):
            return True
        return False

    return block


def _median(vals: Sequence[float]) -> Optional[float]:
    return round(statistics.median(vals), 4) if vals else None


def _derive_thresholds(trades: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    """Loss-heavy thresholds from runtime-eligible trades."""
    eligible = [t for t in trades if not _runtime_baseline_block(t)]
    wins = [t for t in eligible if _pnl_yen(t) > 0]
    losses = [t for t in eligible if _pnl_yen(t) < 0]

    def _split(key: str, *, block_low: bool = True) -> float:
        w = [_float(t.get(key)) for t in wins if _float(t.get(key)) is not None]
        l = [_float(t.get(key)) for t in losses if _float(t.get(key)) is not None]
        if not w or not l:
            return 0.0
        if block_low:
            return (_median(w) + _median(l)) / 2.0  # type: ignore[operator]
        return (_median(w) + _median(l)) / 2.0  # type: ignore[operator]

    return {
        "last_high_update_age_min_hi": _split("last_high_update_age_min", block_low=False) or 60.0,
        "high_update_count_30m_lo": _split("high_update_count_30m") or 1.0,
        "high_update_density_30m_lo": _split("high_update_density_30m") or 0.03,
        "up_tick_ratio_15m_lo": _split("up_tick_ratio_15m") or 0.45,
        "up_tick_ratio_30m_lo": _split("up_tick_ratio_30m") or 0.45,
        "positive_bar_ratio_15m_lo": _split("positive_bar_ratio_15m") or 0.45,
        "trend_consistency_lo": _split("trend_consistency_score") or 0.8,
        "vwap_duration_lo": _split("vwap_above_duration_min") or 5.0,
        "vwap_stability_lo": _split("vwap_position_stability") or 0.45,
        "sector_return_15m_lo": 0.0,
        "relative_strength_lo": 0.0,
    }


def _build_variants(thr: Mapping[str, float]) -> dict[str, tuple[str, Optional[Callable[[Mapping[str, Any]], bool]]]]:
    def _lt(key: str, t: float) -> Callable[[Mapping[str, Any]], bool]:
        return lambda tr, k=key, th=t: (_float(tr.get(k)) or 1e18) < th

    def _gt(key: str, t: float) -> Callable[[Mapping[str, Any]], bool]:
        return lambda tr, k=key, th=t: (_float(tr.get(k)) or -1e18) > th

    def _flag(key: str) -> Callable[[Mapping[str, Any]], bool]:
        return lambda tr, k=key: bool(tr.get(k))

    def _and(*fns: Callable[[Mapping[str, Any]], bool]) -> Callable[[Mapping[str, Any]], bool]:
        return lambda tr: all(fn(tr) for fn in fns)

    guards: dict[str, tuple[str, Optional[Callable[[Mapping[str, Any]], bool]]]] = {
        "A_baseline": ("baseline", None),
        "B_high_update_age_guard": (
            "high_update",
            _gt("last_high_update_age_min", float(thr["last_high_update_age_min_hi"])),
        ),
        "C_high_update_count_guard": (
            "high_update",
            _lt("high_update_count_30m", float(thr["high_update_count_30m_lo"])),
        ),
        "D_high_update_density_guard": (
            "high_update",
            _lt("high_update_density_30m", float(thr["high_update_density_30m_lo"])),
        ),
        "E_up_tick_ratio_guard": ("trend", _lt("up_tick_ratio_15m", float(thr["up_tick_ratio_15m_lo"]))),
        "F_positive_bar_ratio_guard": (
            "trend",
            _lt("positive_bar_ratio_15m", float(thr["positive_bar_ratio_15m_lo"])),
        ),
        "G_trend_consistency_guard": ("trend", _lt("trend_consistency_score", float(thr["trend_consistency_lo"]))),
        "H_vwap_duration_guard": ("vwap", _lt("vwap_above_duration_min", float(thr["vwap_duration_lo"]))),
        "I_vwap_failed_reclaim_guard": ("vwap", _flag("vwap_failed_reclaim_flag")),
        "J_vwap_stability_guard": ("vwap", _lt("vwap_position_stability", float(thr["vwap_stability_lo"]))),
        "K_sector_follow_guard": (
            "sector",
            lambda tr: (_float(tr.get("sector_return_15m")) or 1e18)
            < float(thr["sector_return_15m_lo"])
            and bool(tr.get("sector_available")),
        ),
        "L_relative_strength_guard": (
            "sector",
            lambda tr: (_float(tr.get("relative_strength_vs_sector")) or 1e18)
            < float(thr["relative_strength_lo"])
            and bool(tr.get("sector_available")),
        ),
    }
    return guards


def _block_stats(
    enriched: Sequence[Mapping[str, Any]],
    *,
    extra: Optional[Callable[[Mapping[str, Any]], bool]],
) -> dict[str, Any]:
    blocked = [
        t
        for t in enriched
        if not _runtime_baseline_block(t) and extra is not None and extra(t)
    ]
    pnls = [_pnl_yen(t) for t in blocked]
    return {
        "blocked_count": len(blocked),
        "blocked_loss_count": sum(1 for t in blocked if _pnl_yen(t) < 0),
        "blocked_win_count": sum(1 for t in blocked if _pnl_yen(t) > 0),
        "blocked_pnl_yen": round(sum(pnls), 2),
    }


def _concentration(blocked: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    if not blocked:
        return {"top_day_share": 0.0, "top_symbol_share": 0.0}
    days = Counter(str(t.get("day") or "")[:8] for t in blocked)
    syms = Counter(str(t.get("symbol") or "") for t in blocked)
    return {
        "top_day_share": round(max(days.values()) / len(blocked), 4),
        "top_symbol_share": round(max(syms.values()) / len(blocked), 4),
    }


def _metrics_from_state(
    state: Any,
    *,
    variant: str,
    feature_group: str,
    block_stats: Mapping[str, Any],
    blocked_trades: Sequence[Mapping[str, Any]],
    sector_subset: bool = False,
) -> dict[str, Any]:
    chron = _chronological_pnls_from_log(state.trade_log)
    sym = _symbol_pnl_from_log(state.trade_log)
    conc = _concentration(blocked_trades)
    return {
        "variant": variant,
        "feature_group": feature_group,
        "total_pnl_yen": round(sum(chron), 2),
        "profit_factor": _pf(chron),
        "max_drawdown_yen": _max_drawdown_yen(chron) if chron else 0.0,
        "stop_rate": _stop_rate_from_log(state.trade_log),
        "accepted_count": state.accepted_trade_count,
        "remaining_pnl_yen": round(sum(chron), 2),
        **block_stats,
        "daily_pnl_618": round(float(state.daily_pnls.get(DAY_618, 0.0)), 2),
        "daily_pnl_619": round(float(state.daily_pnls.get(DAY_619, 0.0)), 2),
        **{f"symbol_pnl_{k}": sym.get(k, 0.0) for k in ("6976", "6920", "4062")},
        **conc,
        "sector_subset": sector_subset,
        "_state": state,
    }


def _add_combos(
    guards: dict[str, tuple[str, Optional[Callable[[Mapping[str, Any]], bool]]]],
    singles: Sequence[Mapping[str, Any]],
) -> None:
    by_group: dict[str, str] = {}
    for row in sorted(singles, key=lambda r: float(r.get("delta_pnl_vs_baseline") or 0), reverse=True):
        g = str(row.get("feature_group") or "")
        if g not in by_group and g not in ("baseline", "combined"):
            by_group[g] = str(row.get("variant") or "")

    def _g(name: str) -> Optional[Callable[[Mapping[str, Any]], bool]]:
        tup = guards.get(name)
        return tup[1] if tup else None

    def _combo(vid: str, g1: str, g2: str, f1: str, f2: str) -> None:
        a, b = _g(g1), _g(g2)
        if a is None or b is None:
            return
        guards[vid] = (
            "combined",
            lambda tr, fa=a, fb=b: fa(tr) and fb(tr),
        )

    hu = by_group.get("high_update")
    tr = by_group.get("trend")
    vw = by_group.get("vwap")
    sec = by_group.get("sector")
    if hu and tr:
        _combo("M_best_high_update_plus_trend", hu, tr, "high_update", "trend")
    if hu and vw:
        _combo("N_best_high_update_plus_vwap", hu, vw, "high_update", "vwap")
    if tr and vw:
        _combo("O_best_trend_plus_vwap", tr, vw, "trend", "vwap")
    # P: best two among hu/tr/vw (no sector)
    ranked = [(g, by_group[g]) for g in ("high_update", "trend", "vwap") if g in by_group]
    def _single_delta(vid: str) -> float:
        for r in singles:
            if r.get("variant") == vid:
                return float(r.get("delta_pnl_vs_baseline") or 0)
        return 0.0

    ranked.sort(key=lambda x: _single_delta(x[1]), reverse=True)
    if len(ranked) >= 2:
        _combo(f"P_{ranked[0][0]}_plus_{ranked[1][0]}", ranked[0][1], ranked[1][1], ranked[0][0], ranked[1][0])
    if ranked and sec:
        _combo("Q_best_no_sector_plus_sector", ranked[0][1], sec, ranked[0][0], "sector")


def _verdict(best: Mapping[str, Any], baseline: Mapping[str, Any]) -> str:
    delta = float(best.get("delta_pnl_vs_baseline") or 0)
    if delta < 5000:
        return "no_new_feature_edge"
    g = str(best.get("feature_group") or "")
    mapping = {
        "high_update": "high_update_feature_candidate",
        "trend": "trend_continuity_candidate",
        "vwap": "vwap_reclaim_candidate",
        "sector": "sector_follow_candidate",
        "combined": "combined_feature_candidate",
    }
    if float(best.get("top_symbol_share") or 0) > 0.6:
        return "no_new_feature_edge"
    return mapping.get(g, "combined_feature_candidate")


def run_phase456_tournament(*, repo_root: Path) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    candidates = _load_candidate_stream(repo_root)
    enriched = _enrich_candidates(candidates, kabu=kabu)
    price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)
    sector_map = read_jpx_sector_map(kabu)

    for t in enriched:
        t.update(enrich_trade_phase456_features(t, price_idx=price_idx, sector_map=sector_map))

    thr = _derive_thresholds(enriched)
    guards = _build_variants(thr)
    np_shadows = _precompute_np_shadows(enriched, kabu=kabu, np_policy=BEST_NP_POLICY)

    rows: list[dict[str, Any]] = []
    blocked_by_variant: dict[str, list[dict[str, Any]]] = {}

    for vid, (group, extra) in guards.items():
        if vid.startswith(("M_", "N_", "O_", "P_", "Q_")):
            continue
        bs = _block_stats(enriched, extra=extra)
        blocked = [
            t
            for t in enriched
            if not _runtime_baseline_block(t) and extra is not None and extra(t)
        ]
        blocked_by_variant[vid] = blocked
        state = simulate_capacity_replay(
            enriched,
            np_shadows,
            mode=REPLAY_MODE,
            entry_block_fn=_entry_block(extra),
            baseline_accepted_keys=set(),
        )
        rows.append(_metrics_from_state(state, variant=vid, feature_group=group, block_stats=bs, blocked_trades=blocked))

    baseline = next(r for r in rows if r["variant"] == "A_baseline")
    base_pnl = float(baseline["total_pnl_yen"])
    base_pf = float(baseline["profit_factor"] or 0)
    base_dd = float(baseline["max_drawdown_yen"] or 0)
    for m in rows:
        m["delta_pnl_vs_baseline"] = round(float(m["total_pnl_yen"]) - base_pnl, 2)
        m["delta_pf_vs_baseline"] = round(float(m["profit_factor"] or 0) - base_pf, 4)
        m["delta_maxdd_vs_baseline"] = round(float(m["max_drawdown_yen"] or 0) - base_dd, 2)
        m["delta_daily_pnl_618"] = round(float(m["daily_pnl_618"]) - float(baseline["daily_pnl_618"]), 2)
        m["delta_daily_pnl_619"] = round(float(m["daily_pnl_619"]) - float(baseline["daily_pnl_619"]), 2)
        for sym in ("6976", "6920", "4062"):
            m[f"delta_symbol_pnl_{sym}"] = round(
                float(m.get(f"symbol_pnl_{sym}") or 0) - float(baseline.get(f"symbol_pnl_{sym}") or 0),
                2,
            )

    singles = [r for r in rows if r["variant"] not in ("A_baseline",) and not r["variant"].startswith(("M_", "N_"))]
    _add_combos(guards, singles)

    for vid, (group, extra) in guards.items():
        if not vid.startswith(("M_", "N_", "O_", "P_", "Q_")):
            continue
        bs = _block_stats(enriched, extra=extra)
        blocked = [
            t
            for t in enriched
            if not _runtime_baseline_block(t) and extra is not None and extra(t)
        ]
        blocked_by_variant[vid] = blocked
        state = simulate_capacity_replay(
            enriched,
            np_shadows,
            mode=REPLAY_MODE,
            entry_block_fn=_entry_block(extra),
            baseline_accepted_keys=set(),
        )
        m = _metrics_from_state(
            state,
            variant=vid,
            feature_group=group,
            block_stats=bs,
            blocked_trades=blocked,
            sector_subset="sector" in vid,
        )
        m["delta_pnl_vs_baseline"] = round(float(m["total_pnl_yen"]) - base_pnl, 2)
        m["delta_pf_vs_baseline"] = round(float(m["profit_factor"] or 0) - base_pf, 4)
        m["delta_maxdd_vs_baseline"] = round(float(m["max_drawdown_yen"] or 0) - base_dd, 2)
        m["delta_daily_pnl_618"] = round(float(m["daily_pnl_618"]) - float(baseline["daily_pnl_618"]), 2)
        m["delta_daily_pnl_619"] = round(float(m["daily_pnl_619"]) - float(baseline["daily_pnl_619"]), 2)
        for sym in ("6976", "6920", "4062"):
            m[f"delta_symbol_pnl_{sym}"] = round(
                float(m.get(f"symbol_pnl_{sym}") or 0) - float(baseline.get(f"symbol_pnl_{sym}") or 0),
                2,
            )
        rows.append(m)

    non_base = [r for r in rows if r["variant"] != "A_baseline"]
    best = max(non_base, key=lambda r: float(r.get("delta_pnl_vs_baseline") or 0)) if non_base else baseline
    best_single = max(
        (r for r in non_base if not str(r["variant"]).startswith(("M_", "N_", "O_", "P_", "Q_"))),
        key=lambda r: float(r.get("delta_pnl_vs_baseline") or 0),
        default=baseline,
    )
    best_combo = max(
        (r for r in non_base if str(r["variant"]).startswith(("M_", "N_", "O_", "P_", "Q_"))),
        key=lambda r: float(r.get("delta_pnl_vs_baseline") or 0),
        default=None,
    )

    by_group: dict[str, dict[str, Any]] = {}
    for g in ("high_update", "trend", "vwap", "sector", "combined"):
        grp_rows = [r for r in non_base if r.get("feature_group") == g]
        if grp_rows:
            best_g = max(grp_rows, key=lambda r: float(r.get("delta_pnl_vs_baseline") or 0))
            by_group[g] = {k: v for k, v in best_g.items() if not k.startswith("_")}

    verdict = _verdict(best, baseline)
    overfit = "high" if float(best.get("top_day_share") or 0) > 0.5 else (
        "medium" if float(best.get("top_symbol_share") or 0) > 0.4 else "low"
    )

    mandatory = {
        "1_best_feature_group": best.get("feature_group"),
        "2_best_single_feature": VARIANT_FEATURE.get(str(best_single.get("variant")), best_single.get("variant")),
        "3_best_single_variant": best_single.get("variant"),
        "4_best_combo_variant": best_combo.get("variant") if best_combo else None,
        "5_pnl_improvement_yen": best.get("delta_pnl_vs_baseline"),
        "6_pf_improvement": best.get("delta_pf_vs_baseline"),
        "7_maxdd_improvement_yen": best.get("delta_maxdd_vs_baseline"),
        "8_delta_618": best.get("delta_daily_pnl_618"),
        "9_delta_619": best.get("delta_daily_pnl_619"),
        "10_delta_6976": best.get("delta_symbol_pnl_6976"),
        "11_delta_6920": best.get("delta_symbol_pnl_6920"),
        "12_delta_4062": best.get("delta_symbol_pnl_4062"),
        "13_overfit_risk": overfit,
        "14_runtime_candidate": float(best.get("delta_pnl_vs_baseline") or 0) > 10000
        and float(best.get("delta_symbol_pnl_6976") or 0) > -20000,
        "15_next_actions": [
            f"Shadow-eval {best.get('variant')}" if verdict != "no_new_feature_edge" else "No new guard — monitor D leakage",
            "Phase456B walk-forward on top variant",
        ],
        "verdict": verdict,
        "thresholds": thr,
        "baseline_pnl": base_pnl,
    }

    detail_rows: list[dict[str, Any]] = []
    best_extra = guards.get(str(best.get("variant")), (None, None))[1]
    for t in enriched:
        if _runtime_baseline_block(t):
            continue
        detail_rows.append(
            {
                "variant": best.get("variant"),
                "symbol": t.get("symbol"),
                "entry_time": t.get("entry_time"),
                "pnl_yen": _pnl_yen(t),
                "blocked_by_variant": bool(best_extra and best_extra(t)),
                "last_high_update_age_min": t.get("last_high_update_age_min"),
                "high_update_count_30m": t.get("high_update_count_30m"),
                "up_tick_ratio_15m": t.get("up_tick_ratio_15m"),
                "trend_consistency_score": t.get("trend_consistency_score"),
                "vwap_position_stability": t.get("vwap_position_stability"),
                "sector_return_15m": t.get("sector_return_15m"),
                "relative_strength_vs_sector": t.get("relative_strength_vs_sector"),
            }
        )

    clean_rows = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]

    return {
        "generated_at": _now_iso(),
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "baseline": {k: baseline.get(k) for k in COMPARISON_FIELDS if k in baseline},
        "feature_group_summary": by_group,
        "mandatory_answers": mandatory,
        "verdict": verdict,
        "thresholds": thr,
        "_comparison_rows": clean_rows,
        "_detail_rows": detail_rows,
    }


@dataclass
class Phase456Job:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase456_tournament(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "comparison": reports / "phase456_new_entry_feature_tournament.csv",
            "detail": reports / "phase456_new_entry_feature_detail.csv",
            "summary": reports / "phase456_new_entry_feature_summary.json",
        }
        _write_csv(paths["comparison"], COMPARISON_FIELDS, list(result.get("_comparison_rows") or []))
        _write_csv(paths["detail"], DETAIL_FIELDS, list(result.get("_detail_rows") or []))
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        doc_root = self.repo_root / "kabu_native"
        if not (doc_root / "docs").is_dir():
            doc_root = self.repo_root
        report = doc_root / "docs" / "operations" / "phase456_new_entry_feature_tournament.md"
        m = result.get("mandatory_answers") or {}
        report.write_text(
            "\n".join(
                [
                    "# Phase456 — New Entry Feature Tournament",
                    "",
                    f"Generated: {result.get('generated_at')}",
                    f"Period: {result.get('period_start')}..{result.get('period_end')}",
                    "",
                    f"**Verdict:** `{result.get('verdict')}`",
                    "",
                    "## Mandatory answers",
                    "",
                    f"1. Best feature group: **{m.get('1_best_feature_group')}**",
                    f"2. Best single feature variant: **{m.get('2_best_single_feature')}**",
                    f"3. Best single variant: **{m.get('3_best_single_variant')}**",
                    f"4. Best combo: **{m.get('4_best_combo_variant')}**",
                    f"5. PnL improvement: **{m.get('5_pnl_improvement_yen')}** yen",
                    f"6. PF improvement: **{m.get('6_pf_improvement')}**",
                    f"7. MaxDD improvement: **{m.get('7_maxdd_improvement_yen')}** yen",
                    f"8. 6/18 delta: **{m.get('8_delta_618')}**",
                    f"9. 6/19 delta: **{m.get('9_delta_619')}**",
                    f"10. 6976 delta: **{m.get('10_delta_6976')}**",
                    f"11. 6920 delta: **{m.get('11_delta_6920')}**",
                    f"12. 4062 delta: **{m.get('12_delta_4062')}**",
                    f"13. Overfit risk: **{m.get('13_overfit_risk')}**",
                    f"14. Runtime candidate: **{m.get('14_runtime_candidate')}**",
                    f"15. Next: {m.get('15_next_actions')}",
                    "",
                    "See phase456_new_entry_feature_tournament.csv for full variant table.",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        paths["report"] = report
        return paths
