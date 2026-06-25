"""
Phase494 — New Feature Discovery (research only).

Explores derived entry features across 5 categories for loser vs winner separation.
PBv2 CAP=5 replay window 20260529–20260622. No Runtime changes.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase382_capital_constrained_backtest import _parse_ts, _position_key
from research.phase451_entry_shape_tournament import _build_price_index_to, _now_iso
from research.phase463_trend_pullback_population_tournament import (
    _board_bucket,
    _fill_close_proxy_shadows,
)
from research.phase465b_trend_gate_redesign import _cohens_d, _mi_median_split
from research.phase473_trend_entry_architecture import _entry_block, _rise, pass_pbv2
from research.phase476_pre_breakout_gate_replay import _ensure_enriched, _load_replay_pool
from research.phase483_stop_low_mfe_root_cause_audit import _ks_stat
from research.phase484_stop_low_mfe_feature_discovery import _compute_base_features, _momentum_slope
from research.phase488_current_runtime_replay import (
    REPLAY_MODE,
    _filter_period,
    _filter_replay_pool_safe,
    _simulate_runtime_replay,
)
from research.phase493_global_entry_failure_audit import (
    PERIOD_END,
    PERIOD_START,
    _enrich_trade_row,
    _exit_reason,
    _is_loser,
    _is_winner,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

TOP_N = 20

EXISTING_FEATURES = (
    "r5", "r10", "r15", "r30", "r15_minus_r5", "r30_minus_r5",
    "vwap_dev_pct", "vwap_extension_rate", "vwap_structure_score",
    "momentum_continuation_score", "day_high_distance", "board_imbalance",
    "high_update_age", "high_update_count_30m",
)

NEW_FEATURE_CATALOG: dict[str, tuple[str, ...]] = {
    "pullback_quality": (
        "PBQ_pullback_depth_ratio",
        "PBQ_shallow_pullback_score",
        "PBQ_negative_r5_board_midhigh",
        "PBQ_vwap_pullback_gap",
        "PBQ_recovery_slope_15_5",
        "PBQ_pullback_efficiency",
        "PBQ_counter_pullback_long_up",
        "PBQ_board_supported_dip",
    ),
    "trend_context": (
        "TCX_trend_spread_r30_r5",
        "TCX_trend_sign_r30_r10",
        "TCX_counter_trend_bounce",
        "TCX_momentum_slope",
        "TCX_dual_window_uptrend",
        "TCX_short_neg_long_pos",
        "TCX_pm_session_momentum",
    ),
    "exhaustion": (
        "EXH_rally_decay_r15_r5",
        "EXH_vwap_extension_rate",
        "EXH_high_update_density",
        "EXH_inverse_day_high_dist",
        "EXH_chase_intensity",
        "EXH_stale_high_vwap",
        "EXH_momentum_fade_r5_r15",
        "EXH_vwap_board_extension",
    ),
    "relative_strength": (
        "RSY_r5_minus_symbol_median",
        "RSY_r10_zscore_in_day",
        "RSY_momentum_board_spread",
        "RSY_strength_index",
        "RSY_imbalance_excess",
        "RSY_vwap_dev_z_proxy",
        "RSY_composite_strength_pct",
    ),
    "market_structure": (
        "MST_structure_break_rate",
        "MST_near_day_high_flag",
        "MST_vwap_structure_score",
        "MST_board_change_delta",
        "MST_extension_near_high",
        "MST_am_structure_break",
        "MST_price_tier_vwap",
    ),
}

ALL_NEW_FEATURES = tuple(f for group in NEW_FEATURE_CATALOG.values() for f in group)
ALL_FEATURES = EXISTING_FEATURES + ALL_NEW_FEATURES

DISCOVERY_FIELDS = [
    "position_key", "symbol", "day", "cohort", "exit_reason", "pnl_yen", "mfe_pct",
    *ALL_FEATURES,
]

RANKING_FIELDS = [
    "rank", "feature_id", "category", "is_existing",
    "loser_mean", "loser_median", "winner_mean", "winner_median",
    "missing_rate_loser", "missing_rate_winner",
    "cohens_d", "ks_statistic", "mutual_information", "feature_direction",
    "loo_min_abs_d", "loo_median_abs_d", "loo_stable_days_pct", "loo_robust",
]


def _float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _sign(v: Optional[float]) -> int:
    if v is None or v == 0:
        return 0
    return 1 if v > 0 else -1


def _session_pm(entry_time: Any) -> bool:
    dt = _parse_ts(str(entry_time or ""))
    return dt is not None and dt.hour >= 12


def _high_update_density(trade: Mapping[str, Any]) -> Optional[float]:
    hu30 = _float(trade.get("high_update_count_30m"))
    age = _float(trade.get("high_update_age") or trade.get("minutes_since_day_high_update"))
    if hu30 is not None and age is not None and age > 0:
        return round(hu30 / age, 6)
    return None


def _compute_new_features(
    row: Mapping[str, Any],
    *,
    symbol_r5_median: Mapping[str, float],
    day_r10_stats: Mapping[str, tuple[float, float]],
    composite_pct: Mapping[str, float],
) -> dict[str, Optional[float]]:
    tr = row.get("_trade") or row
    r5 = _float(row.get("r5"))
    r10 = _float(row.get("r10"))
    r15 = _float(row.get("r15"))
    r30 = _float(row.get("r30"))
    vwap_dev = _float(row.get("vwap_dev_pct"))
    vwap_ext = _float(row.get("vwap_extension_rate"))
    dhd = _float(row.get("day_high_distance"))
    mom = _float(row.get("momentum_continuation_score"))
    board = _float(row.get("board_imbalance"))
    tier = str(row.get("board_tier") or "")
    age = _float(row.get("high_update_age"))
    hu30 = _float(row.get("high_update_count_30m"))
    vwap_struct = _float(row.get("vwap_structure_score"))
    bc5 = _float(row.get("board_change_5m"))
    bc10 = _float(row.get("board_change_10m"))
    sym = str(row.get("symbol") or "")
    day = str(row.get("day") or "")
    pk = str(row.get("position_key") or "")

    midhigh = tier in ("mid", "high", "board_mid", "board_high")

    out: dict[str, Optional[float]] = {}

    # Pullback Quality
    out["PBQ_pullback_depth_ratio"] = round(r5 / r30, 6) if r5 is not None and r30 and abs(r30) > 0.05 else None
    out["PBQ_shallow_pullback_score"] = round(r10 - r5, 6) if r10 is not None and r5 is not None else None
    out["PBQ_negative_r5_board_midhigh"] = 1.0 if r5 is not None and r5 < 0 and midhigh else 0.0 if r5 is not None else None
    out["PBQ_vwap_pullback_gap"] = round(vwap_dev - r5, 6) if vwap_dev is not None and r5 is not None else None
    out["PBQ_recovery_slope_15_5"] = round((r15 - r5) / 10.0, 6) if r15 is not None and r5 is not None else None
    out["PBQ_pullback_efficiency"] = round(r5 / max(r30, 0.01), 6) if r5 is not None and r30 is not None and r30 > 0 else None
    out["PBQ_counter_pullback_long_up"] = 1.0 if r5 is not None and r30 is not None and r5 < 0 and r30 > 0.5 else 0.0 if r5 is not None and r30 is not None else None
    out["PBQ_board_supported_dip"] = round(board * abs(min(r5 or 0, 0)), 6) if board is not None and r5 is not None else None

    # Trend Context
    out["TCX_trend_spread_r30_r5"] = round(r30 - r5, 6) if r30 is not None and r5 is not None else None
    out["TCX_trend_sign_r30_r10"] = 1.0 if _sign(r30) == _sign(r10) and _sign(r30) != 0 else 0.0 if r30 is not None and r10 is not None else None
    out["TCX_counter_trend_bounce"] = 1.0 if r30 is not None and r5 is not None and r30 < 0 and r5 > 0 else 0.0 if r30 is not None and r5 is not None else None
    out["TCX_momentum_slope"] = _momentum_slope(tr)
    out["TCX_dual_window_uptrend"] = 1.0 if r30 is not None and r15 is not None and r30 > 0 and r15 > 0 else 0.0 if r30 is not None and r15 is not None else None
    out["TCX_short_neg_long_pos"] = 1.0 if r5 is not None and r30 is not None and r5 < 0 and r30 > 0.3 else 0.0 if r5 is not None and r30 is not None else None
    out["TCX_pm_session_momentum"] = round(r10 * (1.0 if _session_pm(row.get("entry_time")) else 0.0), 6) if r10 is not None else None

    # Exhaustion
    out["EXH_rally_decay_r15_r5"] = _float(row.get("r15_minus_r5"))
    out["EXH_vwap_extension_rate"] = vwap_ext
    out["EXH_high_update_density"] = _high_update_density(tr)
    out["EXH_inverse_day_high_dist"] = round(1.0 / max(dhd or 0.1, 0.1), 6) if dhd is not None else None
    out["EXH_chase_intensity"] = round(r10 / max(dhd or 0.2, 0.2), 6) if r10 is not None and dhd is not None else None
    out["EXH_stale_high_vwap"] = round((age or 0) * (vwap_dev or 0), 6) if age is not None and vwap_dev is not None else None
    out["EXH_momentum_fade_r5_r15"] = round(r5 - r15, 6) if r5 is not None and r15 is not None and r15 > 0 else None
    out["EXH_vwap_board_extension"] = round((vwap_dev or 0) * (board or 0), 6) if vwap_dev is not None and board is not None else None

    # Relative Strength
    sym_med = symbol_r5_median.get(sym)
    out["RSY_r5_minus_symbol_median"] = round(r5 - sym_med, 6) if r5 is not None and sym_med is not None else None
    if r10 is not None and day in day_r10_stats:
        mu, sd = day_r10_stats[day]
        out["RSY_r10_zscore_in_day"] = round((r10 - mu) / sd, 6) if sd > 1e-9 else 0.0
    else:
        out["RSY_r10_zscore_in_day"] = None
    out["RSY_momentum_board_spread"] = round((mom or 0) - (board or 0), 6) if mom is not None and board is not None else None
    out["RSY_strength_index"] = (
        round(r10 + (vwap_dev or 0) - (dhd or 0), 6)
        if r10 is not None and vwap_dev is not None and dhd is not None
        else None
    )
    out["RSY_imbalance_excess"] = round(board - 0.5, 6) if board is not None else None
    out["RSY_vwap_dev_z_proxy"] = round(vwap_dev / max(abs(dhd or 1), 0.5), 6) if vwap_dev is not None else None
    out["RSY_composite_strength_pct"] = composite_pct.get(pk)

    # Market Structure
    out["MST_structure_break_rate"] = round(hu30 / max(age or 1, 1), 6) if hu30 is not None and age is not None else None
    out["MST_near_day_high_flag"] = 1.0 if dhd is not None and dhd < 1.0 else 0.0 if dhd is not None else None
    out["MST_vwap_structure_score"] = vwap_struct
    out["MST_board_change_delta"] = round(bc10 - bc5, 6) if bc10 is not None and bc5 is not None else None
    out["MST_extension_near_high"] = round(vwap_dev, 6) if vwap_dev is not None and dhd is not None and dhd < 1.5 else 0.0 if vwap_dev is not None and dhd is not None else None
    out["MST_am_structure_break"] = (
        round(out["MST_structure_break_rate"] or 0, 6)
        if out.get("MST_structure_break_rate") is not None and not _session_pm(row.get("entry_time"))
        else 0.0
        if out.get("MST_structure_break_rate") is not None
        else None
    )
    ep = _float(tr.get("entry_price"))
    out["MST_price_tier_vwap"] = round(math.log10(max(ep or 100, 100)) * (vwap_dev or 0), 6) if ep and vwap_dev is not None else None

    return out


def _feature_category(fid: str) -> str:
    if fid in EXISTING_FEATURES:
        return "existing"
    for cat, feats in NEW_FEATURE_CATALOG.items():
        if fid in feats:
            return cat
    return "other"


def _feature_direction(lm: Optional[float], wm: Optional[float]) -> str:
    if lm is None or wm is None:
        return "unknown"
    if lm > wm:
        return "higher_in_loser"
    if lm < wm:
        return "lower_in_loser"
    return "equal"


def _loo_stats(
    rows: Sequence[Mapping[str, Any]],
    feature: str,
    *,
    days: Sequence[str],
) -> dict[str, Any]:
    losers = [r for r in rows if r.get("cohort") == "loser"]
    winners = [r for r in rows if r.get("cohort") == "winner"]
    lv = [float(r[feature]) for r in losers if r.get(feature) is not None]
    wv = [float(r[feature]) for r in winners if r.get(feature) is not None]
    full_d = abs(float(_cohens_d(lv, wv) or 0))

    loo_ds: list[float] = []
    stable = 0
    for day in days:
        sub_l = [r for r in losers if r.get("day") != day]
        sub_w = [r for r in winners if r.get("day") != day]
        sl = [float(r[feature]) for r in sub_l if r.get(feature) is not None]
        sw = [float(r[feature]) for r in sub_w if r.get(feature) is not None]
        if len(sl) < 3 or len(sw) < 3:
            continue
        d = abs(float(_cohens_d(sl, sw) or 0))
        loo_ds.append(d)
        if d >= 0.12:
            stable += 1
    n_loo = len(loo_ds) or 1
    loo_min = min(loo_ds) if loo_ds else 0.0
    loo_med = statistics.median(loo_ds) if loo_ds else 0.0
    stable_pct = round(stable / n_loo, 4)
    robust = loo_min >= 0.12 and full_d >= 0.20 and stable_pct >= 0.6
    return {
        "loo_min_abs_d": round(loo_min, 6),
        "loo_median_abs_d": round(loo_med, 6),
        "loo_stable_days_pct": stable_pct,
        "loo_robust": robust,
    }


def _rank_features(rows: Sequence[Mapping[str, Any]], *, days: Sequence[str]) -> list[dict[str, Any]]:
    losers = [r for r in rows if r.get("cohort") == "loser"]
    winners = [r for r in rows if r.get("cohort") == "winner"]
    ranking: list[dict[str, Any]] = []

    for feat in ALL_FEATURES:
        lv = [float(r[feat]) for r in losers if r.get(feat) is not None]
        wv = [float(r[feat]) for r in winners if r.get(feat) is not None]
        if not lv and not wv:
            continue
        lm = statistics.mean(lv) if lv else None
        wm = statistics.mean(wv) if wv else None
        d = _cohens_d(lv, wv)
        ks = _ks_stat(lv, wv)
        mi = _mi_median_split(wv, lv) if lv and wv else None
        loo = _loo_stats(rows, feat, days=days)
        ranking.append(
            {
                "feature_id": feat,
                "category": _feature_category(feat),
                "is_existing": feat in EXISTING_FEATURES,
                "loser_mean": round(lm, 6) if lm is not None else None,
                "loser_median": round(statistics.median(lv), 6) if lv else None,
                "winner_mean": round(wm, 6) if wm is not None else None,
                "winner_median": round(statistics.median(wv), 6) if wv else None,
                "missing_rate_loser": round(sum(1 for r in losers if r.get(feat) is None) / max(1, len(losers)), 4),
                "missing_rate_winner": round(sum(1 for r in winners if r.get(feat) is None) / max(1, len(winners)), 4),
                "cohens_d": round(d, 6) if d is not None else None,
                "ks_statistic": ks,
                "mutual_information": round(mi, 6) if mi is not None else None,
                "feature_direction": _feature_direction(lm, wm),
                **loo,
            }
        )

    ranking.sort(key=lambda r: abs(float(r.get("cohens_d") or 0)), reverse=True)
    for i, row in enumerate(ranking, start=1):
        row["rank"] = i
    return ranking


def _load_accepted_rows(repo_root: Path) -> list[dict[str, Any]]:
    kabu = resolve_kabu_root(repo_root)
    reports = resolve_reports_dir(repo_root)
    price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)
    replay_pool, runtime_shadows = _load_replay_pool(reports)
    replay_pool = _filter_period(replay_pool, start=PERIOD_START, end=PERIOD_END)
    runtime_shadows = _fill_close_proxy_shadows(replay_pool, runtime_shadows, price_idx=price_idx)
    replay_pool = _filter_replay_pool_safe(replay_pool, runtime_shadows)
    _ensure_enriched(replay_pool, price_idx=price_idx)
    state = _simulate_runtime_replay(
        replay_pool,
        runtime_shadows,
        mode=f"{REPLAY_MODE}_phase494",
        entry_block_fn=_entry_block(pass_pbv2),
        initial_equity=1_500_000.0,
    )
    base_rows = [_enrich_trade_row(log) for log in state.trade_log]

    # Context for relative features
    sym_r5: dict[str, list[float]] = defaultdict(list)
    day_r10: dict[str, list[float]] = defaultdict(list)
    composite_raw: dict[str, float] = {}
    for r in base_rows:
        if _float(r.get("r5")) is not None:
            sym_r5[str(r["symbol"])].append(float(r["r5"]))
        if _float(r.get("r10")) is not None:
            day_r10[str(r["day"])].append(float(r["r10"]))
        v10 = _float(r.get("r10"))
        vd = _float(r.get("vwap_dev_pct"))
        if v10 is not None and vd is not None:
            composite_raw[str(r["position_key"])] = v10 + vd

    sym_median = {s: statistics.median(v) for s, v in sym_r5.items() if v}
    day_stats = {
        d: (statistics.mean(v), statistics.pstdev(v) or 1e-9)
        for d, v in day_r10.items()
        if len(v) >= 2
    }
    composite_vals = sorted(composite_raw.values())
    composite_pct: dict[str, float] = {}
    if composite_vals:
        for pk, val in composite_raw.items():
            rank = sum(1 for x in composite_vals if x <= val)
            composite_pct[pk] = round(100.0 * rank / len(composite_vals), 4)

    rows: list[dict[str, Any]] = []
    for r in base_rows:
        cohort = "loser" if _is_loser(r) else ("winner" if _is_winner(r) else "other")
        rec = {
            "position_key": r["position_key"],
            "symbol": r["symbol"],
            "day": r["day"],
            "cohort": cohort,
            "exit_reason": r.get("exit_reason"),
            "pnl_yen": r.get("pnl_yen"),
            "mfe_pct": r.get("mfe_pct"),
            **{k: r.get(k) for k in EXISTING_FEATURES},
            "_trade": r.get("_trade"),
            "entry_time": r.get("entry_time"),
            "board_tier": r.get("board_tier"),
        }
        rec.update(
            _compute_new_features(
                r,
                symbol_r5_median=sym_median,
                day_r10_stats=day_stats,
                composite_pct=composite_pct,
            )
        )
        rows.append(rec)
    return rows


def _verdict(
    *,
    ranking: Sequence[Mapping[str, Any]],
    top_new: Sequence[Mapping[str, Any]],
    best_existing: Optional[Mapping[str, Any]],
) -> str:
    if not top_new:
        return "needs_new_feature"
    best_new_d = abs(float(top_new[0].get("cohens_d") or 0))
    exist_d = abs(float((best_existing or {}).get("cohens_d") or 0))
    if best_new_d > exist_d + 0.05 and top_new[0].get("loo_robust"):
        return "new_feature_found"
    if best_new_d >= 0.28 and best_new_d > exist_d:
        return "new_feature_found"
    if best_new_d < 0.20:
        return "needs_tick_level_feature"
    return "marginal_improvement"


def run_phase494(*, repo_root: Path) -> dict[str, Any]:
    rows = _load_accepted_rows(repo_root)
    days = sorted({str(r["day"]) for r in rows if r.get("day")})
    ranking = _rank_features(rows, days=days)

    existing_ranked = [r for r in ranking if r.get("is_existing")]
    new_ranked = [r for r in ranking if not r.get("is_existing")]
    top20_new = new_ranked[:TOP_N]
    for i, row in enumerate(top20_new, start=1):
        row["rank"] = i
    best_existing = existing_ranked[0] if existing_ranked else None
    best_new = top20_new[0] if top20_new else None

    stronger = bool(
        best_new
        and best_existing
        and abs(float(best_new.get("cohens_d") or 0)) > abs(float(best_existing.get("cohens_d") or 0))
    )

    verdict = _verdict(ranking=ranking, top_new=top20_new, best_existing=best_existing)
    loser_n = sum(1 for r in rows if r.get("cohort") == "loser")
    winner_n = sum(1 for r in rows if r.get("cohort") == "winner")

    mandatory = {
        "stronger_than_existing_found": stronger,
        "top20_new_features": [r["feature_id"] for r in top20_new],
        "best_new_feature": best_new.get("feature_id") if best_new else None,
        "best_new_cohens_d": best_new.get("cohens_d") if best_new else None,
        "best_existing_feature": best_existing.get("feature_id") if best_existing else None,
        "best_existing_cohens_d": best_existing.get("cohens_d") if best_existing else None,
        "comparison": (
            f"new best {best_new.get('feature_id')} d={best_new.get('cohens_d')} "
            f"vs existing {best_existing.get('feature_id')} d={best_existing.get('cohens_d')}"
            if best_new and best_existing
            else "n/a"
        ),
        "runtime_candidate": bool(
            best_new
            and best_new.get("loo_robust")
            and abs(float(best_new.get("cohens_d") or 0)) >= 0.30
            and winner_n >= 15
            and float(best_new.get("loo_min_abs_d") or 0) >= 0.15
        ),
        "shadow_candidate": bool(
            best_new
            and abs(float(best_new.get("cohens_d") or 0)) >= 0.22
            and float(best_new.get("ks_statistic") or 0) >= 0.30
        ),
        "verdict": verdict,
        "loser_count": loser_n,
        "winner_count": winner_n,
        "trade_count": len(rows),
    }

    return {
        "generated_at": _now_iso(),
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "verdict": verdict,
        "mandatory_answers": mandatory,
        "_discovery_rows": rows,
        "_ranking": ranking,
        "_top20_new": top20_new,
    }


@dataclass
class Phase494Job:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase494(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        paths = {
            "discovery": reports / "phase494_feature_discovery.csv",
            "ranking": reports / "phase494_feature_ranking.csv",
            "summary": reports / "phase494_summary.json",
        }
        _write_csv(paths["discovery"], DISCOVERY_FIELDS, [
            {k: v for k, v in row.items() if not k.startswith("_")}
            for row in (result.get("_discovery_rows") or [])
        ])
        _write_csv(paths["ranking"], RANKING_FIELDS, list(result.get("_top20_new") or []))
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        payload["all_feature_ranking_top30"] = list(result.get("_ranking") or [])[:30]
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        return paths
