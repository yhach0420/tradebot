"""
Phase456C — VWAP Structure Tournament (research only).

Tick/count-based VWAP features vs Phase456 duration guard (H_ref).
Baseline = Phase452 Runtime + NP exit.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
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
    _build_price_index_to,
    _enrich_candidates,
    _load_candidate_stream,
    _now_iso,
    _symbol_pnl_from_log,
)
from research.phase451b_entry_shape_tournament_mid_high import _runtime_entry_block_mid_high
from research.phase456c_vwap_structure_features import enrich_trade_phase456c_features
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.weak_shape_reject_entry_guard import would_block_weak_shape_reject

REPLAY_MODE = "phase456_runtime_np"
PHASE456_H_DURATION_LO = 0.69

VARIANT_FEATURE: dict[str, str] = {
    "H_ref_phase456_duration": "vwap_above_duration_min",
    "A1_recent_vwap_reclaim_guard": "recent_vwap_reclaim",
    "A2_reclaim_count_guard": "reclaim_count_30tick",
    "A3_failed_reclaim_guard": "failed_reclaim",
    "B1_vwap_above_ratio_guard": "vwap_above_ratio_20tick",
    "B2_consecutive_above_guard": "consecutive_above_ticks",
    "B3_consecutive_below_guard": "consecutive_below_ticks",
    "C1_vwap_dev_pct_guard": "vwap_dev_pct",
    "C2_vwap_dev_zscore_guard": "vwap_dev_zscore",
    "C3_vwap_acceleration_guard": "vwap_acceleration",
    "D1_structure_score_guard": "vwap_structure_score",
}

COMPARISON_FIELDS = [
    "variant",
    "feature_group",
    "uses_time",
    "total_pnl_yen",
    "delta_pnl_vs_baseline",
    "delta_pnl_vs_h_ref",
    "profit_factor",
    "delta_pf_vs_baseline",
    "delta_pf_vs_h_ref",
    "max_drawdown_yen",
    "delta_maxdd_vs_baseline",
    "delta_maxdd_vs_h_ref",
    "stop_rate",
    "accepted_count",
    "blocked_count",
    "blocked_loss_count",
    "blocked_win_count",
    "blocked_pnl_yen",
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
]

DETAIL_FIELDS = [
    "symbol",
    "entry_time",
    "pnl_yen",
    "vwap_above_duration_min",
    "recent_vwap_reclaim",
    "reclaim_count_30tick",
    "failed_reclaim",
    "vwap_above_ratio_20tick",
    "consecutive_above_ticks",
    "vwap_dev_pct",
    "vwap_dev_zscore",
    "vwap_acceleration",
    "vwap_structure_score",
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


def _weak_shape_block(trade: Mapping[str, Any]) -> bool:
    return would_block_weak_shape_reject(_map_runtime_fields(trade))


def _runtime_baseline_block(trade: Mapping[str, Any]) -> bool:
    return _runtime_entry_block_mid_high(_weak_shape_block)(trade)


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
    eligible = [t for t in trades if not _runtime_baseline_block(t)]
    wins = [t for t in eligible if _pnl_yen(t) > 0]
    losses = [t for t in eligible if _pnl_yen(t) < 0]

    def _split(key: str) -> float:
        w = [_float(t.get(key)) for t in wins if _float(t.get(key)) is not None]
        l = [_float(t.get(key)) for t in losses if _float(t.get(key)) is not None]
        if not w or not l:
            return 0.0
        return float((_median(w) + _median(l)) / 2.0)  # type: ignore[operator]

    return {
        "reclaim_count_lo": _split("reclaim_count_30tick") or 1.0,
        "vwap_above_ratio_lo": _split("vwap_above_ratio_20tick") or 0.5,
        "consecutive_above_lo": _split("consecutive_above_ticks") or 2.0,
        "consecutive_below_hi": _split("consecutive_below_ticks") or 3.0,
        "vwap_dev_pct_lo": _split("vwap_dev_pct") or 0.0,
        "vwap_dev_zscore_lo": _split("vwap_dev_zscore") or 0.0,
        "vwap_acceleration_lo": _split("vwap_acceleration") or 0.0,
        "structure_score_lo": _split("vwap_structure_score") or 1.5,
        "phase456_duration_lo": _split("vwap_above_duration_min") or PHASE456_H_DURATION_LO,
    }


def _lt(key: str, th: float) -> Callable[[Mapping[str, Any]], bool]:
    return lambda tr, k=key, t=th: (_float(tr.get(k)) or 1e18) < t


def _gt(key: str, th: float) -> Callable[[Mapping[str, Any]], bool]:
    return lambda tr, k=key, t=th: (_float(tr.get(k)) or -1e18) > t


def _build_variants(thr: Mapping[str, float]) -> dict[str, tuple[str, bool, Optional[Callable[[Mapping[str, Any]], bool]]]]:
    def _not_reclaim(tr: Mapping[str, Any]) -> bool:
        return not bool(tr.get("recent_vwap_reclaim"))

    def _failed(tr: Mapping[str, Any]) -> bool:
        return bool(tr.get("failed_reclaim"))

    def _duration(tr: Mapping[str, Any]) -> bool:
        return (_float(tr.get("vwap_above_duration_min")) or 1e18) < float(thr["phase456_duration_lo"])

    guards: dict[str, tuple[str, bool, Optional[Callable[[Mapping[str, Any]], bool]]]] = {
        "A0_runtime_baseline": ("baseline", False, None),
        "H_ref_phase456_duration": ("reference", True, _duration),
        "A1_recent_vwap_reclaim_guard": ("reclaim", False, _not_reclaim),
        "A2_reclaim_count_guard": ("reclaim", False, _lt("reclaim_count_30tick", float(thr["reclaim_count_lo"]))),
        "A3_failed_reclaim_guard": ("reclaim", False, _failed),
        "B1_vwap_above_ratio_guard": ("stability", False, _lt("vwap_above_ratio_20tick", float(thr["vwap_above_ratio_lo"]))),
        "B2_consecutive_above_guard": ("stability", False, _lt("consecutive_above_ticks", float(thr["consecutive_above_lo"]))),
        "B3_consecutive_below_guard": ("stability", False, _gt("consecutive_below_ticks", float(thr["consecutive_below_hi"]))),
        "C1_vwap_dev_pct_guard": ("distance", False, _lt("vwap_dev_pct", float(thr["vwap_dev_pct_lo"]))),
        "C2_vwap_dev_zscore_guard": ("distance", False, _lt("vwap_dev_zscore", float(thr["vwap_dev_zscore_lo"]))),
        "C3_vwap_acceleration_guard": ("distance", False, _lt("vwap_acceleration", float(thr["vwap_acceleration_lo"]))),
        "D1_structure_score_guard": ("structure", False, _lt("vwap_structure_score", float(thr["structure_score_lo"]))),
    }
    return guards


def _add_combos(
    guards: dict[str, tuple[str, bool, Optional[Callable[[Mapping[str, Any]], bool]]]],
    singles: Sequence[Mapping[str, Any]],
) -> None:
    by_group: dict[str, str] = {}
    for row in sorted(singles, key=lambda r: float(r.get("delta_pnl_vs_baseline") or 0), reverse=True):
        g = str(row.get("feature_group") or "")
        if g in ("reclaim", "stability", "distance", "structure") and g not in by_group:
            by_group[g] = str(row.get("variant") or "")

    def _g(vid: str) -> Optional[Callable[[Mapping[str, Any]], bool]]:
        tup = guards.get(vid)
        return tup[2] if tup else None

    def _combo(vid: str, v1: str, v2: str, group: str) -> None:
        a, b = _g(v1), _g(v2)
        if a is None or b is None:
            return
        guards[vid] = (group, False, lambda tr, fa=a, fb=b: fa(tr) and fb(tr))

    r, s, d = by_group.get("reclaim"), by_group.get("stability"), by_group.get("distance")
    if r and s:
        _combo("D2_reclaim_plus_stability", r, s, "structure")
    if r and d:
        _combo("D3_reclaim_plus_distance", r, d, "structure")
    if s and d:
        _combo("D4_stability_plus_distance", s, d, "structure")
    if r and s and d:
        best = max((r, s, d), key=lambda v: _delta(singles, v))
        others = [x for x in (r, s, d) if x != best]
        if len(others) >= 2:
            _combo("D5_best_pair_structure", others[0], others[1], "structure")


def _delta(singles: Sequence[Mapping[str, Any]], vid: str) -> float:
    for r in singles:
        if r.get("variant") == vid:
            return float(r.get("delta_pnl_vs_baseline") or 0)
    return 0.0


def _block_stats(enriched: Sequence[Mapping[str, Any]], *, extra: Optional[Callable[[Mapping[str, Any]], bool]]) -> dict[str, Any]:
    blocked = [t for t in enriched if not _runtime_baseline_block(t) and extra is not None and extra(t)]
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
    uses_time: bool,
    block_stats: Mapping[str, Any],
    blocked_trades: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    chron = _chronological_pnls_from_log(state.trade_log)
    sym = _symbol_pnl_from_log(state.trade_log)
    conc = _concentration(blocked_trades)
    return {
        "variant": variant,
        "feature_group": feature_group,
        "uses_time": uses_time,
        "total_pnl_yen": round(sum(chron), 2),
        "profit_factor": _pf(chron),
        "max_drawdown_yen": _max_drawdown_yen(chron) if chron else 0.0,
        "stop_rate": _stop_rate_from_log(state.trade_log),
        "accepted_count": state.accepted_trade_count,
        **block_stats,
        "daily_pnl_618": round(float(state.daily_pnls.get(DAY_618, 0.0)), 2),
        "daily_pnl_619": round(float(state.daily_pnls.get(DAY_619, 0.0)), 2),
        **{f"symbol_pnl_{k}": sym.get(k, 0.0) for k in ("6976", "6920", "4062")},
        **conc,
    }


def _verdict(best: Mapping[str, Any]) -> str:
    delta = float(best.get("delta_pnl_vs_baseline") or 0)
    if delta < 5000:
        return "no_edge"
    g = str(best.get("feature_group") or "")
    if g == "reclaim":
        return "vwap_reclaim_candidate"
    if g == "stability":
        return "vwap_stability_candidate"
    return "vwap_structure_candidate"


def run_phase456c_tournament(*, repo_root: Path) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    candidates = _load_candidate_stream(repo_root)
    enriched = _enrich_candidates(candidates, kabu=kabu)
    price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)

    for t in enriched:
        t.update(enrich_trade_phase456c_features(t, price_idx=price_idx))

    thr = _derive_thresholds(enriched)
    guards = _build_variants(thr)
    np_shadows = _precompute_np_shadows(enriched, kabu=kabu, np_policy=BEST_NP_POLICY)

    rows: list[dict[str, Any]] = []
    for vid, (group, uses_time, extra) in guards.items():
        if vid.startswith("D2_"):
            continue
        bs = _block_stats(enriched, extra=extra)
        blocked = [t for t in enriched if not _runtime_baseline_block(t) and extra is not None and extra(t)]
        state = simulate_capacity_replay(
            enriched,
            np_shadows,
            mode=REPLAY_MODE,
            entry_block_fn=_entry_block(extra),
            baseline_accepted_keys=set(),
        )
        rows.append(
            _metrics_from_state(
                state,
                variant=vid,
                feature_group=group,
                uses_time=uses_time,
                block_stats=bs,
                blocked_trades=blocked,
            )
        )

    baseline = next(r for r in rows if r["variant"] == "A0_runtime_baseline")
    h_ref = next(r for r in rows if r["variant"] == "H_ref_phase456_duration")
    base_pnl = float(baseline["total_pnl_yen"])
    base_pf = float(baseline["profit_factor"] or 0)
    base_dd = float(baseline["max_drawdown_yen"] or 0)
    h_pnl = float(h_ref["total_pnl_yen"])
    h_pf = float(h_ref["profit_factor"] or 0)
    h_dd = float(h_ref["max_drawdown_yen"] or 0)

    def _apply_deltas(m: dict[str, Any]) -> None:
        m["delta_pnl_vs_baseline"] = round(float(m["total_pnl_yen"]) - base_pnl, 2)
        m["delta_pnl_vs_h_ref"] = round(float(m["total_pnl_yen"]) - h_pnl, 2)
        m["delta_pf_vs_baseline"] = round(float(m["profit_factor"] or 0) - base_pf, 4)
        m["delta_pf_vs_h_ref"] = round(float(m["profit_factor"] or 0) - h_pf, 4)
        m["delta_maxdd_vs_baseline"] = round(float(m["max_drawdown_yen"] or 0) - base_dd, 2)
        m["delta_maxdd_vs_h_ref"] = round(float(m["max_drawdown_yen"] or 0) - h_dd, 2)
        m["delta_daily_pnl_618"] = round(float(m["daily_pnl_618"]) - float(baseline["daily_pnl_618"]), 2)
        m["delta_daily_pnl_619"] = round(float(m["daily_pnl_619"]) - float(baseline["daily_pnl_619"]), 2)
        for sym in ("6976", "6920", "4062"):
            m[f"delta_symbol_pnl_{sym}"] = round(
                float(m.get(f"symbol_pnl_{sym}") or 0) - float(baseline.get(f"symbol_pnl_{sym}") or 0),
                2,
            )

    for m in rows:
        _apply_deltas(m)

    tick_singles = [
        r
        for r in rows
        if r["variant"] not in ("A0_runtime_baseline", "H_ref_phase456_duration")
        and not str(r["variant"]).startswith("D2_")
    ]
    _add_combos(guards, tick_singles)

    for vid, (group, uses_time, extra) in guards.items():
        if not vid.startswith(("D2_", "D3_", "D4_", "D5_")):
            continue
        bs = _block_stats(enriched, extra=extra)
        blocked = [t for t in enriched if not _runtime_baseline_block(t) and extra is not None and extra(t)]
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
            uses_time=uses_time,
            block_stats=bs,
            blocked_trades=blocked,
        )
        _apply_deltas(m)
        rows.append(m)

    non_ref = [
        r
        for r in rows
        if r["variant"] not in ("A0_runtime_baseline", "H_ref_phase456_duration")
    ]
    tick_only = [r for r in non_ref if not r.get("uses_time")]
    best_tick = max(tick_only, key=lambda r: float(r.get("delta_pnl_vs_baseline") or 0)) if tick_only else baseline
    best_single = max(
        (
            r
            for r in tick_only
            if not str(r["variant"]).startswith(("D2_", "D3_", "D4_", "D5_"))
        ),
        key=lambda r: float(r.get("delta_pnl_vs_baseline") or 0),
        default=baseline,
    )
    best_combo = max(
        (r for r in tick_only if str(r["variant"]).startswith(("D2_", "D3_", "D4_", "D5_"))),
        key=lambda r: float(r.get("delta_pnl_vs_baseline") or 0),
        default=None,
    )

    h_delta = float(h_ref.get("delta_pnl_vs_baseline") or 0)
    tick_delta = float(best_tick.get("delta_pnl_vs_baseline") or 0)
    time_removed = tick_delta >= h_delta * 0.75 and float(best_tick.get("delta_pnl_vs_h_ref") or 0) >= -15000

    overfit = "high" if float(best_tick.get("top_day_share") or 0) > 0.5 else (
        "medium" if float(best_tick.get("top_symbol_share") or 0) > 0.4 else "low"
    )

    verdict = _verdict(best_tick)
    best_feat = VARIANT_FEATURE.get(str(best_single.get("variant")), str(best_single.get("variant")))
    combo_feat = str(best_combo.get("variant")) if best_combo else None

    mandatory = {
        "1_best_single_feature": best_feat,
        "2_best_composite_feature": combo_feat or best_feat,
        "3_vs_phase456_h": {
            "h_ref_pnl_delta": h_delta,
            "best_tick_variant": best_tick.get("variant"),
            "best_tick_pnl_delta": tick_delta,
            "pnl_gap_vs_h": round(tick_delta - h_delta, 2),
            "blocked_h": h_ref.get("blocked_count"),
            "blocked_best_tick": best_tick.get("blocked_count"),
        },
        "4_pnl_improvement_yen": best_tick.get("delta_pnl_vs_baseline"),
        "5_pf_improvement": best_tick.get("delta_pf_vs_baseline"),
        "6_maxdd_improvement_yen": best_tick.get("delta_maxdd_vs_baseline"),
        "7_delta_6976": best_tick.get("delta_symbol_pnl_6976"),
        "8_delta_6920": best_tick.get("delta_symbol_pnl_6920"),
        "9_delta_4062": best_tick.get("delta_symbol_pnl_4062"),
        "10_time_dependency_removed": time_removed,
        "11_runtime_candidate": float(best_tick.get("delta_pnl_vs_baseline") or 0) > 10000
        and float(best_tick.get("delta_symbol_pnl_6976") or 0) > -20000
        and not bool(best_tick.get("uses_time")),
        "12_overfit_risk": overfit,
        "verdict": verdict,
        "thresholds": thr,
    }

    detail_rows: list[dict[str, Any]] = []
    for t in enriched:
        if _runtime_baseline_block(t):
            continue
        detail_rows.append(
            {
                "symbol": t.get("symbol"),
                "entry_time": t.get("entry_time"),
                "pnl_yen": _pnl_yen(t),
                "vwap_above_duration_min": t.get("vwap_above_duration_min"),
                "recent_vwap_reclaim": t.get("recent_vwap_reclaim"),
                "reclaim_count_30tick": t.get("reclaim_count_30tick"),
                "failed_reclaim": t.get("failed_reclaim"),
                "vwap_above_ratio_20tick": t.get("vwap_above_ratio_20tick"),
                "consecutive_above_ticks": t.get("consecutive_above_ticks"),
                "vwap_dev_pct": t.get("vwap_dev_pct"),
                "vwap_dev_zscore": t.get("vwap_dev_zscore"),
                "vwap_acceleration": t.get("vwap_acceleration"),
                "vwap_structure_score": t.get("vwap_structure_score"),
            }
        )

    clean_rows = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]

    by_group: dict[str, dict[str, Any]] = {}
    for g in ("reclaim", "stability", "distance", "structure", "reference"):
        grp = [r for r in non_ref if r.get("feature_group") == g]
        if grp:
            bg = max(grp, key=lambda r: float(r.get("delta_pnl_vs_baseline") or 0))
            by_group[g] = bg

    return {
        "generated_at": _now_iso(),
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "baseline": {k: baseline.get(k) for k in COMPARISON_FIELDS if k in baseline},
        "h_ref": {k: h_ref.get(k) for k in COMPARISON_FIELDS if k in h_ref},
        "feature_group_summary": by_group,
        "mandatory_answers": mandatory,
        "verdict": verdict,
        "thresholds": thr,
        "_comparison_rows": clean_rows,
        "_detail_rows": detail_rows,
    }


@dataclass
class Phase456CJob:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase456c_tournament(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "comparison": reports / "phase456c_vwap_structure_tournament.csv",
            "detail": reports / "phase456c_vwap_structure_detail.csv",
            "summary": reports / "phase456c_vwap_structure_summary.json",
        }
        _write_csv(paths["comparison"], COMPARISON_FIELDS, list(result.get("_comparison_rows") or []))
        _write_csv(paths["detail"], DETAIL_FIELDS, list(result.get("_detail_rows") or []))
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        doc_root = self.repo_root / "kabu_native"
        if not (doc_root / "docs").is_dir():
            doc_root = self.repo_root
        report = doc_root / "docs" / "operations" / "phase456c_vwap_structure_tournament.md"
        m = result.get("mandatory_answers") or {}
        vs_h = m.get("3_vs_phase456_h") or {}
        report.write_text(
            "\n".join(
                [
                    "# Phase456C — VWAP Structure Tournament",
                    "",
                    f"Generated: {result.get('generated_at')}",
                    f"Period: {result.get('period_start')}..{result.get('period_end')}",
                    "",
                    f"**Verdict:** `{result.get('verdict')}`",
                    "",
                    "## Mandatory answers",
                    "",
                    f"1. Best single feature: **{m.get('1_best_single_feature')}**",
                    f"2. Best composite: **{m.get('2_best_composite_feature')}**",
                    f"3. vs Phase456 H: gap **{vs_h.get('pnl_gap_vs_h')}** yen (tick {vs_h.get('best_tick_pnl_delta')} vs H {vs_h.get('h_ref_pnl_delta')})",
                    f"4. PnL improvement: **{m.get('4_pnl_improvement_yen')}** yen",
                    f"5. PF improvement: **{m.get('5_pf_improvement')}**",
                    f"6. MaxDD improvement: **{m.get('6_maxdd_improvement_yen')}** yen",
                    f"7. 6976 delta: **{m.get('7_delta_6976')}**",
                    f"8. 6920 delta: **{m.get('8_delta_6920')}**",
                    f"9. 4062 delta: **{m.get('9_delta_4062')}**",
                    f"10. Time dependency removed: **{m.get('10_time_dependency_removed')}**",
                    f"11. Runtime candidate: **{m.get('11_runtime_candidate')}**",
                    f"12. Overfit risk: **{m.get('12_overfit_risk')}**",
                    "",
                    "See phase456c_vwap_structure_tournament.csv for full variant table.",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        paths["report"] = report
        return paths
