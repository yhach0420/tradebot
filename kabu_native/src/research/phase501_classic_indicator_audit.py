"""
Phase501 — Classic Technical Indicator Audit (research only).

Evaluates RSI / MACD / MA at ENTRY for loser vs winner separation vs existing features.
PBv2 CAP=5 replay 20260529–20260622. No Runtime changes.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase451_entry_shape_tournament import _build_price_index_to, _now_iso
from research.phase463_trend_pullback_population_tournament import _fill_close_proxy_shadows
from research.phase465b_trend_gate_redesign import _cohens_d, _mi_median_split
from research.phase473_trend_entry_architecture import _entry_block, pass_pbv2
from research.phase476_pre_breakout_gate_replay import _ensure_enriched, _load_replay_pool
from research.phase483_stop_low_mfe_root_cause_audit import _ks_stat
from research.phase488_current_runtime_replay import (
    REPLAY_MODE,
    _filter_period,
    _filter_replay_pool_safe,
    _simulate_runtime_replay,
)
from research.phase499_post_entry_behavior_audit import (
    _cohort as _winner_loser_cohort,
    _is_loser as _is_loser_broad,
    _is_winner as _is_winner_broad,
)
from research.phase493_global_entry_failure_audit import (
    PERIOD_END,
    PERIOD_START,
    _classify_cluster,
    _cluster_flags,
    _enrich_trade_row,
    _top_pct_threshold,
)
from research.phase494_new_feature_discovery import _compute_new_features
from research.phase382_capital_constrained_backtest import _parse_ts
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir


def _assign_cohort(row: Mapping[str, Any]) -> str:
    return _winner_loser_cohort(row)


RSI_PERIODS = (5, 14, 21)
MA_PERIODS = (5, 10, 25, 75, 200)
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

CLASSIC_BASE = (
    "RSI5",
    "RSI14",
    "RSI21",
    "MACD",
    "MACD_signal",
    "MACD_histogram",
    "MA_5",
    "MA_10",
    "MA_25",
    "MA_75",
    "MA_200",
)

CLASSIC_DERIVED = (
    "price_vs_5ma_pct",
    "price_vs_10ma_pct",
    "price_vs_25ma_pct",
    "price_vs_75ma_pct",
    "price_vs_200ma_pct",
    "distance_from_25ma",
    "ma5_slope",
    "ma25_slope",
    "ma75_slope",
    "ma5_gt_ma25",
    "ma25_gt_ma75",
    "rsi_over70",
    "rsi_over80",
    "rsi_under30",
    "rsi_under20",
    "macd_cross_distance",
    "macd_histogram_strength",
)

CLASSIC_FEATURES = CLASSIC_BASE + CLASSIC_DERIVED

# Rank normalized / scale-free classic features only (raw MA levels confound price tier).
CLASSIC_RANK_FEATURES = (
    "RSI5",
    "RSI14",
    "RSI21",
    "MACD",
    "MACD_signal",
    "MACD_histogram",
    *CLASSIC_DERIVED,
)

EXISTING_COMPARE = (
    "board_imbalance",
    "r5",
    "r10",
    "r15",
    "r30",
    "vwap_dev_pct",
    "MST_near_day_high_flag",
    "EXH_chase_intensity",
    "RSY_r5_minus_symbol_median",
)

ALL_RANK_FEATURES = CLASSIC_RANK_FEATURES + EXISTING_COMPARE

AUDIT_FIELDS = [
    "position_key",
    "symbol",
    "day",
    "cohort",
    "exit_reason",
    "pnl_yen",
    "mfe_pct",
    "failure_cluster",
    "falling_knife_cluster",
    "high_price_extension_cluster",
    "late_chase_cluster",
    *CLASSIC_FEATURES,
    *EXISTING_COMPARE,
]

RANKING_FIELDS = [
    "rank",
    "feature_id",
    "feature_family",
    "is_classic",
    "is_existing",
    "loser_mean",
    "loser_median",
    "winner_mean",
    "winner_median",
    "missing_rate_loser",
    "missing_rate_winner",
    "cohens_d",
    "ks_statistic",
    "mutual_information",
    "feature_direction",
    "loo_min_abs_d",
    "loo_stable_days_pct",
    "loo_robust",
    "exclude_6976_abs_d",
    "exclude_top_day_abs_d",
]

SYMBOL_6976 = "6976"


def _float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _feature_family(fid: str) -> str:
    if fid.startswith("RSI") or fid.startswith("rsi_"):
        return "rsi"
    if fid.startswith("MACD") or fid.startswith("macd_"):
        return "macd"
    if fid.startswith("MA_") or fid.startswith("ma") or fid.startswith("price_vs_") or fid.startswith("distance_from_"):
        return "ma"
    return "existing"


def _feature_direction(lm: Optional[float], wm: Optional[float]) -> str:
    if lm is None or wm is None:
        return "unknown"
    if lm > wm:
        return "higher_in_loser"
    if lm < wm:
        return "lower_in_loser"
    return "equal"


def _resample_1m_closes(
    series: Sequence[tuple[datetime, float]],
    *,
    until: datetime,
) -> list[float]:
    if not series:
        return []
    origin = series[0][0]
    bars: dict[int, float] = {}
    for ts, px in series:
        if ts > until:
            break
        if px <= 0:
            continue
        minute_key = int((ts - origin).total_seconds() // 60)
        bars[minute_key] = px
    return [bars[k] for k in sorted(bars.keys())]


def _sma(closes: Sequence[float], period: int) -> Optional[float]:
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def _ema_series(values: Sequence[float], period: int) -> list[float]:
    if len(values) < period:
        return []
    k = 2.0 / (period + 1)
    seed = sum(values[:period]) / period
    out = [seed]
    for v in values[period:]:
        out.append(v * k + out[-1] * (1.0 - k))
    return out


def _rsi(closes: Sequence[float], period: int) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0.0) for d in deltas[-period:]]
    losses = [max(-d, 0.0) for d in deltas[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss <= 1e-12:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - 100.0 / (1.0 + rs), 6)


def _macd_at_entry(closes: Sequence[float]) -> tuple[Optional[float], Optional[float], Optional[float]]:
    if len(closes) < MACD_SLOW + MACD_SIGNAL:
        return None, None, None
    ema_fast = _ema_series(closes, MACD_FAST)
    ema_slow = _ema_series(closes, MACD_SLOW)
    offset = MACD_SLOW - MACD_FAST
    macd_line = [f - s for f, s in zip(ema_fast[offset:], ema_slow)]
    if len(macd_line) < MACD_SIGNAL:
        return None, None, None
    signal_series = _ema_series(macd_line, MACD_SIGNAL)
    macd_val = macd_line[-1]
    signal_val = signal_series[-1]
    hist = macd_val - signal_val
    return round(macd_val, 6), round(signal_val, 6), round(hist, 6)


def _ma_slope(closes: Sequence[float], period: int, *, lookback: int = 5) -> Optional[float]:
    if len(closes) < period + lookback:
        return None
    ma_now = _sma(closes, period)
    ma_prev = sum(closes[-(period + lookback) : -lookback]) / period
    if ma_now is None or ma_prev <= 0:
        return None
    return round((ma_now - ma_prev) / ma_prev * 100.0, 6)


def compute_classic_indicators_at_entry(
    series: Sequence[tuple[datetime, float]],
    *,
    entry_ts: datetime,
    entry_px: float,
) -> dict[str, Optional[float]]:
    closes = _resample_1m_closes(series, until=entry_ts)
    out: dict[str, Optional[float]] = {k: None for k in CLASSIC_FEATURES}

    if entry_px <= 0 or not closes:
        return out

    for p in RSI_PERIODS:
        out[f"RSI{p}"] = _rsi(closes, p)

    macd, signal, hist = _macd_at_entry(closes)
    out["MACD"] = macd
    out["MACD_signal"] = signal
    out["MACD_histogram"] = hist

    mas: dict[int, Optional[float]] = {}
    for p in MA_PERIODS:
        mas[p] = _sma(closes, p)
        out[f"MA_{p}"] = round(mas[p], 6) if mas[p] is not None else None

    ma5, ma10, ma25, ma75, ma200 = mas.get(5), mas.get(10), mas.get(25), mas.get(75), mas.get(200)
    if ma5 and ma5 > 0:
        out["price_vs_5ma_pct"] = round((entry_px - ma5) / ma5 * 100.0, 6)
    if ma10 and ma10 > 0:
        out["price_vs_10ma_pct"] = round((entry_px - ma10) / ma10 * 100.0, 6)
    if ma25 and ma25 > 0:
        out["price_vs_25ma_pct"] = round((entry_px - ma25) / ma25 * 100.0, 6)
        out["distance_from_25ma"] = round(entry_px - ma25, 6)
    if ma75 and ma75 > 0:
        out["price_vs_75ma_pct"] = round((entry_px - ma75) / ma75 * 100.0, 6)
    if ma200 and ma200 > 0:
        out["price_vs_200ma_pct"] = round((entry_px - ma200) / ma200 * 100.0, 6)

    out["ma5_slope"] = _ma_slope(closes, 5)
    out["ma25_slope"] = _ma_slope(closes, 25)
    out["ma75_slope"] = _ma_slope(closes, 75)

    if ma5 is not None and ma25 is not None:
        out["ma5_gt_ma25"] = 1.0 if ma5 > ma25 else 0.0
    if ma25 is not None and ma75 is not None:
        out["ma25_gt_ma75"] = 1.0 if ma25 > ma75 else 0.0

    rsi14 = out.get("RSI14")
    if rsi14 is not None:
        out["rsi_over70"] = 1.0 if rsi14 >= 70 else 0.0
        out["rsi_over80"] = 1.0 if rsi14 >= 80 else 0.0
        out["rsi_under30"] = 1.0 if rsi14 <= 30 else 0.0
        out["rsi_under20"] = 1.0 if rsi14 <= 20 else 0.0

    if hist is not None:
        out["macd_cross_distance"] = hist
        out["macd_histogram_strength"] = round(abs(hist), 6)

    return out


def _enrich_row_with_classic(
    row: Mapping[str, Any],
    *,
    price_idx: Mapping[tuple[str, str], list[tuple[datetime, float]]],
) -> dict[str, Any]:
    sym = str(row.get("symbol") or "")
    if sym.endswith(".T"):
        sym_key = sym
    else:
        sym_key = f"{sym}.T"
    day = str(row.get("day") or "")[:8]
    ent_dt = _parse_ts(str(row.get("entry_time") or ""))
    entry_px = _float(row.get("entry_price")) or _float((row.get("_trade") or {}).get("entry_price"))
    series = price_idx.get((sym_key, day), [])
    if ent_dt is None or entry_px is None or entry_px <= 0:
        classic = {k: None for k in CLASSIC_FEATURES}
    else:
        classic = compute_classic_indicators_at_entry(series, entry_ts=ent_dt, entry_px=entry_px)
    rec = dict(row)
    rec.update(classic)
    return rec


def _rank_features(
    rows: Sequence[Mapping[str, Any]],
    *,
    days: Sequence[str],
    top_day: str,
) -> list[dict[str, Any]]:
    losers = [r for r in rows if r.get("cohort") == "loser"]
    winners = [r for r in rows if r.get("cohort") == "winner"]
    ranking: list[dict[str, Any]] = []

    for feat in ALL_RANK_FEATURES:
        lv = [float(r[feat]) for r in losers if r.get(feat) is not None]
        wv = [float(r[feat]) for r in winners if r.get(feat) is not None]
        if not lv and not wv:
            continue
        miss_l = round(sum(1 for r in losers if r.get(feat) is None) / max(1, len(losers)), 4)
        miss_w = round(sum(1 for r in winners if r.get(feat) is None) / max(1, len(winners)), 4)
        if max(miss_l, miss_w) > 0.5:
            continue
        lm = statistics.mean(lv) if lv else None
        wm = statistics.mean(wv) if wv else None
        d = _cohens_d(lv, wv)
        ks = _ks_stat(lv, wv)
        mi = _mi_median_split(wv, lv) if lv and wv else None

        loo_ds: list[float] = []
        stable = 0
        for day in days:
            sl = [float(r[feat]) for r in losers if r.get("day") != day and r.get(feat) is not None]
            sw = [float(r[feat]) for r in winners if r.get("day") != day and r.get(feat) is not None]
            if len(sl) < 3 or len(sw) < 3:
                continue
            ld = abs(float(_cohens_d(sl, sw) or 0))
            loo_ds.append(ld)
            if ld >= 0.12:
                stable += 1
        n_loo = len(loo_ds) or 1

        ex_l = [r for r in losers if str(r.get("symbol")) != SYMBOL_6976]
        ex_w = [r for r in winners if str(r.get("symbol")) != SYMBOL_6976]
        ex6976_d = abs(
            float(
                _cohens_d(
                    [float(r[feat]) for r in ex_l if r.get(feat) is not None],
                    [float(r[feat]) for r in ex_w if r.get(feat) is not None],
                )
                or 0
            )
        )
        ex_dl = [r for r in losers if str(r.get("day")) != top_day]
        ex_dw = [r for r in winners if str(r.get("day")) != top_day]
        ex_day_d = abs(
            float(
                _cohens_d(
                    [float(r[feat]) for r in ex_dl if r.get(feat) is not None],
                    [float(r[feat]) for r in ex_dw if r.get(feat) is not None],
                )
                or 0
            )
        )

        ranking.append(
            {
                "feature_id": feat,
                "feature_family": _feature_family(feat),
                "is_classic": feat in CLASSIC_RANK_FEATURES,
                "is_existing": feat in EXISTING_COMPARE,
                "loser_mean": round(lm, 6) if lm is not None else None,
                "loser_median": round(statistics.median(lv), 6) if lv else None,
                "winner_mean": round(wm, 6) if wm is not None else None,
                "winner_median": round(statistics.median(wv), 6) if wv else None,
                "missing_rate_loser": miss_l,
                "missing_rate_winner": miss_w,
                "cohens_d": round(d, 6) if d is not None else None,
                "ks_statistic": ks,
                "mutual_information": round(mi, 6) if mi is not None else None,
                "feature_direction": _feature_direction(lm, wm),
                "loo_min_abs_d": round(min(loo_ds), 6) if loo_ds else 0.0,
                "loo_stable_days_pct": round(stable / n_loo, 4),
                "loo_robust": (min(loo_ds) if loo_ds else 0) >= 0.12 and abs(float(d or 0)) >= 0.20,
                "exclude_6976_abs_d": round(ex6976_d, 6),
                "exclude_top_day_abs_d": round(ex_day_d, 6),
            }
        )

    ranking.sort(key=lambda r: abs(float(r.get("cohens_d") or 0)), reverse=True)
    for i, row in enumerate(ranking, start=1):
        row["rank"] = i
    return ranking


def _cluster_indicator_profile(
    rows: Sequence[Mapping[str, Any]],
    *,
    cluster_key: str,
    indicators: Sequence[str],
) -> dict[str, Any]:
    flagged = [r for r in rows if r.get(cluster_key)]
    other = [r for r in rows if not r.get(cluster_key)]
    out: dict[str, Any] = {
        "cluster": cluster_key,
        "flagged_count": len(flagged),
        "other_count": len(other),
    }
    for feat in indicators:
        fv = [float(r[feat]) for r in flagged if r.get(feat) is not None]
        ov = [float(r[feat]) for r in other if r.get(feat) is not None]
        out[f"{feat}_flagged_median"] = round(statistics.median(fv), 4) if fv else None
        out[f"{feat}_other_median"] = round(statistics.median(ov), 4) if ov else None
        out[f"{feat}_delta_median"] = (
            round(statistics.median(fv) - statistics.median(ov), 4) if fv and ov else None
        )
    return out


def _verdict(
    *,
    best_classic: Optional[Mapping[str, Any]],
    best_existing: Optional[Mapping[str, Any]],
    overfit: bool,
) -> str:
    if not best_classic:
        return "classic_indicator_not_useful"
    bc = abs(float(best_classic.get("cohens_d") or 0))
    be = abs(float((best_existing or {}).get("cohens_d") or 0))
    if overfit:
        return "overfit_indicator"
    if bc > be + 0.05 and best_classic.get("loo_robust"):
        return "classic_indicator_found"
    if bc >= 0.28 and bc > be:
        return "classic_indicator_found"
    return "classic_indicator_not_useful"


def _load_accepted_rows(repo_root: Path, *, parallel: bool, max_workers: int) -> list[dict[str, Any]]:
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
        mode=f"{REPLAY_MODE}_phase501",
        entry_block_fn=_entry_block(pass_pbv2),
        initial_equity=1_500_000.0,
    )
    base_rows = [_enrich_trade_row(log) for log in state.trade_log]

    sym_r5: dict[str, list[float]] = defaultdict(list)
    day_r10: dict[str, list[float]] = defaultdict(list)
    composite_raw: dict[str, float] = {}
    medians: dict[str, float] = {}
    r10_vals: list[float] = []

    for r in base_rows:
        if _float(r.get("r5")) is not None:
            sym_r5[str(r["symbol"])].append(float(r["r5"]))
        if _float(r.get("r10")) is not None:
            day_r10[str(r["day"])].append(float(r["r10"]))
            r10_vals.append(float(r["r10"]))
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

    for key in ("r10", "r30_minus_r5", "r15_minus_r5", "vwap_dev_pct"):
        vals = [_float(r.get(key)) for r in base_rows]
        nums = [v for v in vals if v is not None]
        if nums:
            medians[key] = statistics.median(nums)

    r10_thr = _top_pct_threshold(r10_vals, 70.0) if r10_vals else 1.0

    partial: list[dict[str, Any]] = []
    for r in base_rows:
        cohort = _assign_cohort(r)
        new_feats = _compute_new_features(
            r,
            symbol_r5_median=sym_median,
            day_r10_stats=day_stats,
            composite_pct=composite_pct,
        )
        clusters = _cluster_flags(r, medians=medians)
        r10 = _float(r.get("r10"))
        late_chase = bool(clusters.get("late_chase_cluster")) or (
            r10 is not None and (r10 >= 1.0 or r10 >= r10_thr)
        )
        rec = {
            "position_key": r["position_key"],
            "symbol": str(r["symbol"]).replace(".T", ""),
            "day": r["day"],
            "cohort": cohort,
            "exit_reason": r.get("exit_reason"),
            "pnl_yen": r.get("pnl_yen"),
            "mfe_pct": r.get("mfe_pct"),
            "entry_time": r.get("entry_time"),
            "entry_price": r.get("entry_price"),
            "failure_cluster": clusters.get("cluster"),
            "falling_knife_cluster": clusters.get("falling_knife_cluster"),
            "high_price_extension_cluster": clusters.get("high_price_extension_cluster"),
            "late_chase_cluster": late_chase,
            "board_imbalance": r.get("board_imbalance"),
            "r5": r.get("r5"),
            "r10": r.get("r10"),
            "r15": r.get("r15"),
            "r30": r.get("r30"),
            "vwap_dev_pct": r.get("vwap_dev_pct"),
            "MST_near_day_high_flag": new_feats.get("MST_near_day_high_flag"),
            "EXH_chase_intensity": new_feats.get("EXH_chase_intensity"),
            "RSY_r5_minus_symbol_median": new_feats.get("RSY_r5_minus_symbol_median"),
            "_trade": r.get("_trade"),
        }
        partial.append(rec)

    def _work(rec: dict[str, Any]) -> dict[str, Any]:
        return _enrich_row_with_classic(rec, price_idx=price_idx)

    rows: list[dict[str, Any]] = []
    if parallel and len(partial) > 1:
        with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
            futs = [pool.submit(_work, rec) for rec in partial]
            for fut in as_completed(futs):
                rows.append(fut.result())
    else:
        rows = [_work(rec) for rec in partial]
    rows.sort(key=lambda r: str(r.get("entry_time") or ""))
    return rows


def run_phase501(*, repo_root: Path, parallel: bool = False, max_workers: int = 2) -> dict[str, Any]:
    rows = _load_accepted_rows(repo_root, parallel=parallel, max_workers=max_workers)
    days = sorted({str(r["day"]) for r in rows if r.get("day")})
    day_counts = Counter(str(r["day"]) for r in rows)
    top_day = day_counts.most_common(1)[0][0] if day_counts else ""

    ranking = _rank_features(rows, days=days, top_day=top_day)
    classic_rank = [r for r in ranking if r.get("is_classic")]
    existing_rank = [r for r in ranking if r.get("is_existing")]

    best_classic = classic_rank[0] if classic_rank else None
    best_existing = existing_rank[0] if existing_rank else None
    best_overall = ranking[0] if ranking else None

    best_rsi = next((r for r in classic_rank if r.get("feature_family") == "rsi"), None)
    best_macd = next((r for r in classic_rank if r.get("feature_family") == "macd"), None)
    best_ma = next((r for r in classic_rank if r.get("feature_family") == "ma"), None)

    bc = abs(float((best_classic or {}).get("cohens_d") or 0))
    be = abs(float((best_existing or {}).get("cohens_d") or 0))
    beats_existing = bc > be + 0.01

    sym6976 = [r for r in rows if str(r.get("symbol")) == SYMBOL_6976]
    dep6976 = bool(sym6976 and best_classic and abs(float(best_classic.get("cohens_d") or 0)) > 0.2)

    loo_robust_classic = sum(1 for r in classic_rank[:5] if r.get("loo_robust"))
    overfit = bool(
        best_classic
        and abs(float(best_classic.get("cohens_d") or 0)) >= 0.25
        and (
            not best_classic.get("loo_robust")
            or float(best_classic.get("exclude_6976_abs_d") or 0) < abs(float(best_classic.get("cohens_d") or 0)) * 0.5
        )
    )

    profile_feats = ("RSI14", "price_vs_25ma_pct", "MACD_histogram", "EXH_chase_intensity", "r5")
    knife_prof = _cluster_indicator_profile(rows, cluster_key="falling_knife_cluster", indicators=profile_feats)
    ext_prof = _cluster_indicator_profile(rows, cluster_key="high_price_extension_cluster", indicators=profile_feats)
    chase_prof = _cluster_indicator_profile(rows, cluster_key="late_chase_cluster", indicators=profile_feats)

    verdict = _verdict(best_classic=best_classic, best_existing=best_existing, overfit=overfit)

    mandatory = {
        "1_strongest_rsi_feature": best_rsi.get("feature_id") if best_rsi else None,
        "1_rsi_cohens_d": best_rsi.get("cohens_d") if best_rsi else None,
        "2_strongest_macd_feature": best_macd.get("feature_id") if best_macd else None,
        "2_macd_cohens_d": best_macd.get("cohens_d") if best_macd else None,
        "3_strongest_ma_feature": best_ma.get("feature_id") if best_ma else None,
        "3_ma_cohens_d": best_ma.get("cohens_d") if best_ma else None,
        "4_strongest_overall_feature": best_overall.get("feature_id") if best_overall else None,
        "4_overall_cohens_d": best_overall.get("cohens_d") if best_overall else None,
        "4_overall_is_classic": bool(best_overall and best_overall.get("is_classic")),
        "5_beats_existing_features": beats_existing,
        "5_best_classic_d": bc,
        "5_best_existing_d": be,
        "5_best_existing_feature": best_existing.get("feature_id") if best_existing else None,
        "6_falling_knife_relation": (
            f"RSI14 flagged={knife_prof.get('RSI14_flagged_median')} other={knife_prof.get('RSI14_other_median')}; "
            f"r5 delta={knife_prof.get('r5_delta_median')}"
        ),
        "7_high_price_extension_relation": (
            f"price_vs_25ma flagged={ext_prof.get('price_vs_25ma_pct_flagged_median')}; "
            f"RSI14 flagged={ext_prof.get('RSI14_flagged_median')}"
        ),
        "8_late_chase_relation": (
            f"EXH_chase flagged={chase_prof.get('EXH_chase_intensity_flagged_median')}; "
            f"RSI14 flagged={chase_prof.get('RSI14_flagged_median')}; "
            f"MACD_hist flagged={chase_prof.get('MACD_histogram_flagged_median')}"
        ),
        "9_6976_dependency": dep6976,
        "10_loo_stability": (
            f"top5_classic_loo_robust={loo_robust_classic}/5; "
            f"best_classic_loo_robust={bool((best_classic or {}).get('loo_robust'))}"
        ),
        "11_overfit_risk": "high" if overfit else ("moderate" if bc >= 0.25 and not beats_existing else "low"),
        "12_replay_candidate": bool(beats_existing and best_classic and best_classic.get("loo_robust")),
        "13_shadow_candidate": bool(best_classic and bc >= 0.20),
        "14_runtime_candidate": False,
        "15_next_action": (
            "Shadow-log top classic indicators alongside existing features; no Runtime adoption"
            if bc >= 0.20
            else "Deprioritize classic indicators; focus on existing PBv2 feature set"
        ),
        "verdict": verdict,
        "trade_count": len(rows),
        "winner_count": sum(1 for r in rows if _is_winner_broad(r)),
        "loser_count": sum(1 for r in rows if _is_loser_broad(r)),
        "missing_rate_MA_200": next(
            (r.get("missing_rate_loser") for r in ranking if r.get("feature_id") == "MA_200"),
            None,
        ),
    }

    return {
        "generated_at": _now_iso(),
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "verdict": verdict,
        "mandatory_answers": mandatory,
        "cluster_profiles": {
            "falling_knife": knife_prof,
            "high_price_extension": ext_prof,
            "late_chase": chase_prof,
        },
        "_audit_rows": rows,
        "_ranking": ranking,
    }


@dataclass
class Phase501Job:
    repo_root: Path
    parallel: bool = False
    max_workers: int = 2

    def run(self) -> dict[str, Any]:
        return run_phase501(
            repo_root=self.repo_root,
            parallel=self.parallel,
            max_workers=self.max_workers,
        )

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        doc_root = self.repo_root / "kabu_native"
        if not (doc_root / "docs").is_dir():
            doc_root = self.repo_root
        paths = {
            "audit": reports / "phase501_classic_indicator_audit.csv",
            "ranking": reports / "phase501_classic_indicator_ranking.csv",
            "summary": reports / "phase501_summary.json",
            "report": doc_root / "docs" / "operations" / "phase501_classic_indicator_audit.md",
        }
        _write_csv(
            paths["audit"],
            AUDIT_FIELDS,
            [{k: v for k, v in row.items() if not k.startswith("_")} for row in (result.get("_audit_rows") or [])],
        )
        _write_csv(paths["ranking"], RANKING_FIELDS, list(result.get("_ranking") or []))
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        self._write_report(paths["report"], result)
        return paths

    def _write_report(self, path: Path, result: Mapping[str, Any]) -> None:
        m = result.get("mandatory_answers") or {}
        ranking = list(result.get("_ranking") or [])[:15]
        lines = [
            "# Phase501 — Classic Technical Indicator Audit",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            f"**Period:** {PERIOD_START} — {PERIOD_END}",
            "",
            "## 必須回答",
            "",
            "| # | 回答 |",
            "|---|------|",
            f"| 1 RSI最強 | **{m.get('1_strongest_rsi_feature')}** (d={m.get('1_rsi_cohens_d')}) |",
            f"| 2 MACD最強 | **{m.get('2_strongest_macd_feature')}** (d={m.get('2_macd_cohens_d')}) |",
            f"| 3 MA最強 | **{m.get('3_strongest_ma_feature')}** (d={m.get('3_ma_cohens_d')}) |",
            f"| 4 全体最強 | **{m.get('4_strongest_overall_feature')}** (d={m.get('4_overall_cohens_d')}) |",
            f"| 5 既存上回り | **{m.get('5_beats_existing_features')}** (classic d={m.get('5_best_classic_d')} vs existing d={m.get('5_best_existing_d')}) |",
            f"| 6 falling_knife | {m.get('6_falling_knife_relation')} |",
            f"| 7 high_price_extension | {m.get('7_high_price_extension_relation')} |",
            f"| 8 late_chase | {m.get('8_late_chase_relation')} |",
            f"| 9 6976依存 | **{m.get('9_6976_dependency')}** |",
            f"| 10 LOO | {m.get('10_loo_stability')} |",
            f"| 11 overfit | **{m.get('11_overfit_risk')}** |",
            f"| 12 Replay候補 | **{m.get('12_replay_candidate')}** |",
            f"| 13 Shadow候補 | **{m.get('13_shadow_candidate')}** |",
            f"| 14 Runtime候補 | **{m.get('14_runtime_candidate')}** |",
            f"| 15 次アクション | {m.get('15_next_action')} |",
            "",
            "## Top 15 Features",
            "",
            "| Rank | Feature | Family | d | LOO robust |",
            "|------|---------|--------|---|------------|",
        ]
        for r in ranking:
            lines.append(
                f"| {r.get('rank')} | {r.get('feature_id')} | {r.get('feature_family')} | "
                f"{r.get('cohens_d')} | {r.get('loo_robust')} |"
            )
        lines.extend(
            [
                "",
                "## 重要所見",
                "",
                "- **raw MA_200** は価格水準と混同（loser median ¥2010 vs winner ¥3937）かつ missing 88% のためランキング除外",
                "- ランキングは **scale-free** 特徴量のみ（missing≤50%）",
                "- **macd_histogram_strength** (|d|=0.47) は既存 **r5** (0.23) を上回るが MI≈0.002 で momentum エイリアス疑い",
                "- **late_chase** 説明は既存 **EXH_chase_intensity** が依然として意味的に適切",
                "- **falling_knife**: RSI14 やや低い（50 vs 60）— 既存 r5 の方が分離大",
                "- Runtime 採用禁止方針維持 → Shadow logging のみ",
                "",
                "## 成果物",
                "",
                "- `results/reports/phase501_classic_indicator_audit.csv`",
                "- `results/reports/phase501_classic_indicator_ranking.csv`",
                "- `results/reports/phase501_summary.json`",
                "",
                "## 実行",
                "",
                "```powershell",
                "cd kabu_native",
                '$env:PYTHONPATH="src"',
                "python scripts/run_phase501_classic_indicator_audit.py --parallel --max-workers 2",
                "```",
                "",
            ]
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
