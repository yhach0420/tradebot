"""
Phase660: Rise5 Recent Regression Root Cause Analysis (research only).

Explains why rise5 shadow counterfactual delta turned negative over the
recent 5 trading days vs +112k over 22 days (Phase659).
No ENTRY/EXIT/YAML/runtime changes.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase451_entry_shape_tournament import _now_iso
from research.phase631_profit_source_attribution import _num
from research.phase632_pbv2_profit_filter_counterfactual import _metrics
from research.phase634_pbv2_only_rise5_full_period import _session_bucket, load_all_full_period_trades
from research.phase649_flat_band_guard_counterfactual import block_flat_plus_overheat, block_phase635_rise5_shadow
from research.phase657_shadow_portfolio_review import _discover_summaries_extended
from research.phase659_rise5_mainline_readiness import (
    RECENT_TRADING_DAYS,
    RISE5_THRESHOLD,
    _apply_rise5,
    _delta_metrics,
    _pnl,
    filter_pbv2_trades,
    flat_band_overlap,
    leave_one_symbol_out,
    rise5_blocks,
)

PHASE660_VERDICT = "phase660_rise5_recent_regression_done"
REPORT_DIR_NAME = "phase660_rise5_recent_regression"
NATIVE_ROOT = Path(__file__).resolve().parents[2]

RISE5_BUCKETS: list[tuple[str, float, float]] = [
    ("0_to_0.5", 0.0, 0.5),
    ("0.5_to_1", 0.5, 1.0),
    ("1_to_2", 1.0, 2.0),
    ("2_to_3", 2.0, 3.0),
    ("gt_3", 3.0, math.inf),
]

THRESHOLD_SWEEP = (1.84, 2.2, 2.5, 3.0)

MARKET_FEATURES = (
    "trading_value",
    "update_count_before_entry",
    "board_age_sec",
    "price_age_sec",
    "spread_bps",
    "turnover_proxy",
    "entry_rise_5min_pct",
    "board_imbalance",
)

QUALITY_FEATURES = (
    "entry_expectancy_score_v2",
    "entry_expectancy_score",
    "continuation_quality",
    "momentum_score",
    "momentum_continuation",
)


def _recent_days(trades: Sequence[Mapping[str, Any]], n: int = RECENT_TRADING_DAYS) -> list[str]:
    days = sorted({str(t.get("day") or "") for t in trades if t.get("day")})
    return days[-n:] if days else []


def _split_recent(trades: Sequence[Mapping[str, Any]]) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    recent_set = set(_recent_days(trades))
    recent = [dict(t) for t in trades if str(t.get("day") or "") in recent_set]
    prior = [dict(t) for t in trades if str(t.get("day") or "") not in recent_set]
    return sorted(recent_set), recent, prior


def _period_metrics(trades: Sequence[Mapping[str, Any]], *, label: str) -> dict[str, Any]:
    kept, blocked = _apply_rise5(trades)
    m = _delta_metrics(trades, kept)
    bw = sum(1 for t in blocked if _pnl(t) > 0)
    rl = sum(1 for t in blocked if _pnl(t) < 0)
    return {
        "period": label,
        "trading_days": len({str(t.get("day") or "") for t in trades}),
        **m,
        "blocked_winners": bw,
        "rescued_losers": rl,
        "blocked_winners_pnl": round(sum(_pnl(t) for t in blocked if _pnl(t) > 0), 2),
        "rescued_losers_pnl": round(sum(_pnl(t) for t in blocked if _pnl(t) < 0), 2),
    }


def daily_comparison_rows(trades: Sequence[Mapping[str, Any]], recent_days: Sequence[str]) -> list[dict[str, Any]]:
    recent_set = set(recent_days)
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        by_day[str(t.get("day") or "")].append(dict(t))
    rows: list[dict[str, Any]] = []
    for day in sorted(by_day):
        dt = by_day[day]
        kept, blocked = _apply_rise5(dt)
        m = _delta_metrics(dt, kept)
        rows.append(
            {
                "day": day,
                "cohort": "recent_5d" if day in recent_set else "prior_17d",
                "baseline_entries": m["baseline_entries"],
                "blocked_entries": m["blocked_entries"],
                "delta_pnl_yen": m["delta_pnl_yen"],
                "baseline_pf": m["baseline_pf"],
                "kept_pf": m["kept_pf"],
                "delta_pf": m["delta_pf"],
                "baseline_dd_yen": m["baseline_dd_yen"],
                "delta_dd_yen": m["delta_dd_yen"],
                "baseline_win_rate": m["baseline_win_rate"],
                "kept_win_rate": m["kept_win_rate"],
                "session_AM_delta": round(
                    sum(_pnl(t) for t in kept if _session_bucket(t) == "AM")
                    - sum(_pnl(t) for t in dt if _session_bucket(t) == "AM"),
                    2,
                ),
                "session_PM_delta": round(
                    sum(_pnl(t) for t in kept if _session_bucket(t) == "PM")
                    - sum(_pnl(t) for t in dt if _session_bucket(t) == "PM"),
                    2,
                ),
                "blocked_winners": sum(1 for t in blocked if _pnl(t) > 0),
                "rescued_losers": sum(1 for t in blocked if _pnl(t) < 0),
            }
        )
    return rows


def am_pm_comparison(trades: Sequence[Mapping[str, Any]], *, label: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for bucket in ("AM", "PM", "lunch"):
        sub = [dict(t) for t in trades if _session_bucket(t) == bucket]
        if not sub:
            continue
        kept, blocked = _apply_rise5(sub)
        m = _delta_metrics(sub, kept)
        rows.append(
            {
                "period": label,
                "session_bucket": bucket,
                "baseline_entries": m["baseline_entries"],
                "blocked_entries": m["blocked_entries"],
                "delta_pnl_yen": m["delta_pnl_yen"],
                "delta_pf": m["delta_pf"],
                "blocked_winners": sum(1 for t in blocked if _pnl(t) > 0),
                "rescued_losers": sum(1 for t in blocked if _pnl(t) < 0),
            }
        )
    return rows


def symbol_contribution_rows(trades: Sequence[Mapping[str, Any]], *, period: str, top_n: int = 20) -> list[dict[str, Any]]:
    by_sym: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        by_sym[str(t.get("symbol") or "")].append(dict(t))
    rows: list[dict[str, Any]] = []
    for sym, seq in by_sym.items():
        kept, blocked = _apply_rise5(seq)
        base_pnl = sum(_pnl(t) for t in seq)
        kept_pnl = sum(_pnl(t) for t in kept)
        rows.append(
            {
                "period": period,
                "symbol": sym,
                "trade_count": len(seq),
                "blocked_count": len(blocked),
                "baseline_pnl_yen": round(base_pnl, 2),
                "delta_pnl_yen": round(kept_pnl - base_pnl, 2),
                "blocked_winners": sum(1 for t in blocked if _pnl(t) > 0),
                "rescued_losers": sum(1 for t in blocked if _pnl(t) < 0),
            }
        )
    rows.sort(key=lambda r: float(r["delta_pnl_yen"]), reverse=True)
    top_help = rows[:top_n]
    top_hurt = sorted(rows, key=lambda r: float(r["delta_pnl_yen"]))[:top_n]
    out: list[dict[str, Any]] = []
    for r in top_help:
        out.append({**r, "rank_type": "top_help"})
    for r in top_hurt:
        if r not in top_help:
            out.append({**r, "rank_type": "top_hurt"})
    return out


def rise5_bucket_rows(trades: Sequence[Mapping[str, Any]], *, period: str) -> list[dict[str, Any]]:
    total = len(trades)
    rows: list[dict[str, Any]] = []
    for label, lo, hi in RISE5_BUCKETS:
        in_bucket = []
        for t in trades:
            r5 = _num(t.get("entry_rise_5min_pct"))
            if r5 is None:
                continue
            if lo <= r5 < hi or (hi == math.inf and r5 >= lo):
                in_bucket.append(dict(t))
        if not in_bucket:
            rows.append({"period": period, "bucket": label, "entry_count": 0, "entry_pct": 0.0, "delta_pnl_yen": 0.0})
            continue
        kept, blocked = _apply_rise5(in_bucket)
        base = sum(_pnl(t) for t in in_bucket)
        kept_pnl = sum(_pnl(t) for t in kept)
        rows.append(
            {
                "period": period,
                "bucket": label,
                "entry_count": len(in_bucket),
                "entry_pct": round(100.0 * len(in_bucket) / max(1, total), 2),
                "blocked_in_bucket": len(blocked),
                "baseline_pnl_yen": round(base, 2),
                "delta_pnl_yen": round(kept_pnl - base, 2),
            }
        )
    return rows


def overlap_period(overlap: Mapping[str, Any], *, period: str) -> dict[str, Any]:
    return {"period": period, **dict(overlap)}


def _feature_means(trades: Sequence[Mapping[str, Any]], features: Sequence[str]) -> dict[str, Optional[float]]:
    out: dict[str, Optional[float]] = {}
    for fid in features:
        vals = [_num(t.get(fid)) for t in trades]
        nums = [float(v) for v in vals if v is not None]
        out[fid] = round(statistics.fmean(nums), 4) if nums else None
    return out


def market_regime_rows(
    full_trades: Sequence[Mapping[str, Any]],
    recent_trades: Sequence[Mapping[str, Any]],
    sector_by_day: Mapping[str, float],
    recent_days: Sequence[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    full_m = _feature_means(full_trades, MARKET_FEATURES)
    recent_m = _feature_means(recent_trades, MARKET_FEATURES)
    for fid in MARKET_FEATURES:
        f = full_m.get(fid)
        r = recent_m.get(fid)
        pct = None
        if f is not None and r is not None and abs(f) > 1e-9:
            pct = round(100.0 * (r - f) / abs(f), 2)
        rows.append(
            {
                "feature": fid,
                "full_22d_mean": f,
                "recent_5d_mean": r,
                "pct_change_vs_full": pct,
            }
        )
    full_heat = [sector_by_day[d] for d in sector_by_day if d not in recent_days]
    recent_heat = [sector_by_day[d] for d in recent_days if d in sector_by_day]
    rows.append(
        {
            "feature": "sector_heat_session_proxy",
            "full_22d_mean": round(statistics.fmean(full_heat), 4) if full_heat else None,
            "recent_5d_mean": round(statistics.fmean(recent_heat), 4) if recent_heat else None,
            "pct_change_vs_full": (
                round(100.0 * (statistics.fmean(recent_heat) - statistics.fmean(full_heat)) / abs(statistics.fmean(full_heat)), 2)
                if full_heat and recent_heat and statistics.fmean(full_heat) != 0
                else None
            ),
        }
    )
    # volatility proxy: stdev of rise5
    full_r5 = [float(_num(t.get("entry_rise_5min_pct"))) for t in full_trades if _num(t.get("entry_rise_5min_pct")) is not None]
    recent_r5 = [float(_num(t.get("entry_rise_5min_pct"))) for t in recent_trades if _num(t.get("entry_rise_5min_pct")) is not None]
    rows.append(
        {
            "feature": "rise5_stdev_volatility_proxy",
            "full_22d_mean": round(statistics.pstdev(full_r5), 4) if len(full_r5) > 1 else None,
            "recent_5d_mean": round(statistics.pstdev(recent_r5), 4) if len(recent_r5) > 1 else None,
            "pct_change_vs_full": None,
        }
    )
    return rows


def pbv2_quality_rows(full_trades: Sequence[Mapping[str, Any]], recent_trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def score_bucket(t: Mapping[str, Any]) -> str:
        s = _num(t.get("entry_expectancy_score_v2"))
        if s is None:
            return "missing"
        if s >= 5:
            return "score5"
        if s >= 4:
            return "score4"
        if s >= 3:
            return "score3"
        return "below3"

    for period, trades in (("full_22d", full_trades), ("recent_5d", recent_trades)):
        n = len(trades)
        buckets = Counter(score_bucket(t) for t in trades)
        for b in ("score3", "score4", "score5", "below3", "missing"):
            rows.append(
                {
                    "period": period,
                    "metric": f"share_{b}",
                    "value": round(100.0 * buckets.get(b, 0) / max(1, n), 2),
                }
            )
        for fid in QUALITY_FEATURES:
            vals = [_num(t.get(fid)) for t in trades]
            nums = [float(v) for v in vals if v is not None]
            rows.append(
                {
                    "period": period,
                    "metric": f"mean_{fid}",
                    "value": round(statistics.fmean(nums), 4) if nums else None,
                }
            )
    return rows


def threshold_sweep_recent(recent_trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for th in THRESHOLD_SWEEP:
        kept = [t for t in recent_trades if not block_phase635_rise5_shadow(t, th)]
        blocked = [t for t in recent_trades if block_phase635_rise5_shadow(t, th)]
        m = _delta_metrics(recent_trades, kept)
        rows.append(
            {
                "threshold_pct": th,
                "blocked_entries": len(blocked),
                "entry_reduction_pct": m["entry_reduction_pct"],
                "delta_pnl_yen": m["delta_pnl_yen"],
                "delta_pf": m["delta_pf"],
                "blocked_winners": sum(1 for t in blocked if _pnl(t) > 0),
                "rescued_losers": sum(1 for t in blocked if _pnl(t) < 0),
            }
        )
    base_delta = float(rows[0]["delta_pnl_yen"]) if rows else 0.0
    for r in rows:
        r["improves_vs_baseline_184"] = float(r["delta_pnl_yen"]) > base_delta + 1e-6
    return rows


def _load_sector_heat_by_day(repo_root: Path) -> dict[str, float]:
    out: dict[str, float] = {}
    for day, _sess, sm in _discover_summaries_extended():
        day_iso = f"{day[:4]}-{day[4:6]}-{day[6:8]}" if len(day) == 8 else day
        nested = sm.get("sector_heat_forward_shadow")
        if isinstance(nested, Mapping):
            v = _num(nested.get("sector_heat_index") or nested.get("heat_index") or nested.get("delta_yen_100"))
            if v is not None:
                out[day_iso] = float(v)
    return out


def classify_root_cause(
    *,
    full_metrics: Mapping[str, Any],
    recent_metrics: Mapping[str, Any],
    am_pm: Sequence[Mapping[str, Any]],
    symbol_rows: Sequence[Mapping[str, Any]],
    threshold_rows: Sequence[Mapping[str, Any]],
    overlap_full: Mapping[str, Any],
    overlap_recent: Mapping[str, Any],
    market_rows: Sequence[Mapping[str, Any]],
    quality_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    scores: dict[str, float] = {k: 0.0 for k in ("A", "B", "C", "D", "E", "F", "G")}

    recent_delta = float(recent_metrics.get("delta_pnl_yen") or 0)
    if recent_delta >= 0:
        primary = "G"
        scores["G"] = 1.0
    else:
        # C sample
        if int(recent_metrics.get("trading_days") or 0) <= 5:
            scores["C"] += 2.0
        neg_days = sum(1 for r in am_pm if str(r.get("period")) == "recent_5d" and float(r.get("delta_pnl_yen") or 0) < 0)
        if neg_days <= 2:
            scores["C"] += 1.5

        # B symbol
        hurt = [r for r in symbol_rows if str(r.get("period")) == "recent_5d" and str(r.get("rank_type")) == "top_hurt"]
        if hurt:
            top = float(hurt[0].get("delta_pnl_yen") or 0)
            if top < 0 and abs(top) >= abs(recent_delta) * 0.4:
                scores["B"] += 3.0

        # AM weakness -> market/time not pure symbol
        am_recent = next((r for r in am_pm if r.get("period") == "recent_5d" and r.get("session_bucket") == "AM"), {})
        if float(am_recent.get("delta_pnl_yen") or 0) <= recent_delta + 1:
            scores["B"] += 1.0

        # D threshold
        best_th = max(threshold_rows, key=lambda r: float(r.get("delta_pnl_yen") or -1e18), default={})
        if float(best_th.get("delta_pnl_yen") or 0) > recent_delta + 500:
            scores["D"] += 2.5

        # E overlap
        if float(overlap_recent.get("overlap_pct_of_rise5") or 0) > float(overlap_full.get("overlap_pct_of_rise5") or 0) + 5:
            scores["E"] += 1.5

        # A market
        for row in market_rows:
            pct = row.get("pct_change_vs_full")
            if isinstance(pct, (int, float)) and abs(float(pct)) > 15:
                scores["A"] += 0.5

        # F quality
        def qshare(period: str, metric: str) -> Optional[float]:
            for r in quality_rows:
                if r.get("period") == period and r.get("metric") == metric:
                    return float(r["value"]) if r.get("value") is not None else None
            return None

        s5_full = qshare("full_22d", "share_score5") or 0
        s5_recent = qshare("recent_5d", "share_score5") or 0
        if abs(s5_recent - s5_full) > 5:
            scores["F"] += 1.5

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    primary = ranked[0][0] if ranked and ranked[0][1] > 0 else "G"
    labels = {
        "A": "market_regime_shift",
        "B": "symbol_concentration",
        "C": "small_sample_noise",
        "D": "threshold_mismatch",
        "E": "flat_band_competition",
        "F": "pbv2_quality_shift",
        "G": "other_mixed",
    }
    return {
        "primary_code": primary,
        "primary_label": labels.get(primary, "other"),
        "scores": {labels[k]: round(v, 2) for k, v in scores.items()},
        "structural": primary in ("B", "D", "F") and scores.get(primary, 0) >= 2.5,
        "accidental": primary == "C" and scores.get("C", 0) >= 3,
    }


def _final_verdict(
    classification: Mapping[str, Any],
    recent_delta: float,
    threshold_rows: Sequence[Mapping[str, Any]],
) -> tuple[str, str]:
    best_th = max(threshold_rows, key=lambda r: float(r.get("delta_pnl_yen") or -1e18), default={})
    if recent_delta < -10000 and classification.get("structural"):
        return "REJECT", "Recent regression structural and material"
    if recent_delta < 0 and classification.get("accidental"):
        return "HOLD", "Small-sample noise; maintain trial-first HOLD from Phase659"
    if float(best_th.get("delta_pnl_yen") or 0) > 0 and classification.get("primary_code") == "D":
        return "KEEP", "Threshold tuning may help recent window; do not change YAML in this phase"
    if recent_delta < 0:
        return "HOLD", "Recent 5d negative but full-period positive; paper trial with rollback"
    return "ADOPT", "Recent window no longer negative on replay"


@dataclass
class Phase660Job:
    repo_root: Path = field(default_factory=lambda: NATIVE_ROOT)

    def run(self) -> dict[str, Any]:
        trades, sessions = load_all_full_period_trades(self.repo_root / "results" / "small_paper")
        pbv2 = filter_pbv2_trades(trades)
        recent_days, recent_trades, prior_trades = _split_recent(pbv2)

        full_m = _period_metrics(pbv2, label="full_22d")
        recent_m = _period_metrics(recent_trades, label="recent_5d")
        prior_m = _period_metrics(prior_trades, label="prior_17d")

        daily = daily_comparison_rows(pbv2, recent_days)
        am_pm = am_pm_comparison(pbv2, label="full_22d")
        am_pm.extend(am_pm_comparison(recent_trades, label="recent_5d"))
        am_pm.extend(am_pm_comparison(prior_trades, label="prior_17d"))

        sym_full = symbol_contribution_rows(pbv2, period="full_22d")
        sym_recent = symbol_contribution_rows(recent_trades, period="recent_5d")
        symbol_rows = sym_full + sym_recent
        loo_sym = leave_one_symbol_out(pbv2)

        bucket_full = rise5_bucket_rows(pbv2, period="full_22d")
        bucket_recent = rise5_bucket_rows(recent_trades, period="recent_5d")

        overlap_full = flat_band_overlap(pbv2)
        overlap_recent = flat_band_overlap(recent_trades)

        sector_by_day = _load_sector_heat_by_day(self.repo_root)
        market = market_regime_rows(pbv2, recent_trades, sector_by_day, recent_days)
        quality = pbv2_quality_rows(pbv2, recent_trades)
        threshold = threshold_sweep_recent(recent_trades)

        classification = classify_root_cause(
            full_metrics=full_m,
            recent_metrics=recent_m,
            am_pm=am_pm,
            symbol_rows=symbol_rows,
            threshold_rows=threshold,
            overlap_full=overlap_full,
            overlap_recent=overlap_recent,
            market_rows=market,
            quality_rows=quality,
        )
        verdict, verdict_reason = _final_verdict(classification, float(recent_m["delta_pnl_yen"]), threshold)

        best_th = max(threshold, key=lambda r: float(r.get("delta_pnl_yen") or -1e18))
        am_recent = next((r for r in am_pm if r.get("period") == "recent_5d" and r.get("session_bucket") == "AM"), {})

        mandatory = {
            "1_primary_regression_cause": classification["primary_label"],
            "1_detail": (
                f"AM recent delta={am_recent.get('delta_pnl_yen')}; "
                f"top hurt symbol={next((r['symbol'] for r in sym_recent if r.get('rank_type')=='top_hurt'), '?')}"
            ),
            "2_structural_vs_accidental": (
                "structural" if classification.get("structural") else "accidental/likely_noise"
            ),
            "3_threshold_change_helps": float(best_th.get("delta_pnl_yen") or 0) > float(recent_m["delta_pnl_yen"]),
            "3_best_recent_threshold_pct": best_th.get("threshold_pct"),
            "3_best_recent_delta_yen": best_th.get("delta_pnl_yen"),
            "4_flat_band_competition": float(overlap_recent.get("overlap_pct_of_rise5") or 0) > 50,
            "4_overlap_pct_recent": overlap_recent.get("overlap_pct_of_rise5"),
            "5_market_regime_dependent": classification.get("primary_code") == "A",
            "6_symbol_dependent": classification.get("primary_code") == "B",
            "7_keep_until_late_july": verdict in ("HOLD", "KEEP"),
            "8_paper_trial_proceed": verdict in ("HOLD", "KEEP", "ADOPT"),
            "9_mainline_candidate_maintained": verdict != "REJECT",
            "10_final_verdict": verdict,
            "10_verdict_reason": verdict_reason,
        }

        return {
            "phase": "phase660_rise5_recent_regression",
            "verdict": PHASE660_VERDICT,
            "generated_at": _now_iso(),
            "rise5_threshold_baseline_pct": RISE5_THRESHOLD,
            "dataset": {
                "session_count": len(sessions),
                "trading_day_count": len({s["day"] for s in sessions}),
                "pbv2_trade_count": len(pbv2),
                "recent_trading_days": recent_days,
            },
            "period_comparison": {
                "full_22d": full_m,
                "recent_5d": recent_m,
                "prior_17d": prior_m,
            },
            "classification": classification,
            "am_pm_comparison": am_pm,
            "flat_band_overlap": {
                "full_22d": overlap_full,
                "recent_5d": overlap_recent,
            },
            "mandatory_answers": mandatory,
            "daily_comparison": daily,
            "symbol_analysis": symbol_rows,
            "leave_one_symbol_out": loo_sym,
            "rise5_bucket_analysis": bucket_full + bucket_recent,
            "market_regime": market,
            "pbv2_quality": quality,
            "threshold_sweep_recent": threshold,
            "overlap_analysis": [
                overlap_period(overlap_full, period="full_22d"),
                overlap_period(overlap_recent, period="recent_5d"),
            ],
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        out = self.repo_root / "results" / "reports" / REPORT_DIR_NAME
        out.mkdir(parents=True, exist_ok=True)
        paths: dict[str, Path] = {}

        compact = {k: v for k, v in result.items() if k not in (
            "daily_comparison", "symbol_analysis", "market_regime", "threshold_sweep_recent", "overlap_analysis", "leave_one_symbol_out"
        )}
        report_fp = out / "phase660_report.json"
        report_fp.write_text(json.dumps(compact, indent=2, ensure_ascii=False), encoding="utf-8")
        paths["report"] = report_fp

        _write_csv(
            out / "phase660_daily_comparison.csv",
            [
                "day", "cohort", "baseline_entries", "blocked_entries", "delta_pnl_yen",
                "baseline_pf", "kept_pf", "delta_pf", "baseline_dd_yen", "delta_dd_yen",
                "baseline_win_rate", "kept_win_rate", "session_AM_delta", "session_PM_delta",
                "blocked_winners", "rescued_losers",
            ],
            result.get("daily_comparison") or [],
        )
        paths["daily"] = out / "phase660_daily_comparison.csv"

        sym_cols = [
            "period", "rank_type", "symbol", "trade_count", "blocked_count",
            "baseline_pnl_yen", "delta_pnl_yen", "blocked_winners", "rescued_losers",
        ]
        _write_csv(out / "phase660_symbol_analysis.csv", sym_cols, result.get("symbol_analysis") or [])
        paths["symbol"] = out / "phase660_symbol_analysis.csv"

        _write_csv(
            out / "phase660_market_regime.csv",
            ["feature", "full_22d_mean", "recent_5d_mean", "pct_change_vs_full"],
            result.get("market_regime") or [],
        )
        paths["market"] = out / "phase660_market_regime.csv"

        _write_csv(
            out / "phase660_threshold_sweep.csv",
            [
                "threshold_pct", "blocked_entries", "entry_reduction_pct", "delta_pnl_yen",
                "delta_pf", "blocked_winners", "rescued_losers", "improves_vs_baseline_184",
            ],
            result.get("threshold_sweep_recent") or [],
        )
        paths["threshold"] = out / "phase660_threshold_sweep.csv"

        _write_csv(
            out / "phase660_overlap_analysis.csv",
            [
                "period", "rise5_blocked_count", "flat_band_blocked_count", "overlap_count",
                "rise5_only_count", "flat_only_count", "overlap_pct_of_rise5", "overlap_pct_of_flat",
                "overlap_blocked_pnl_yen", "rise5_only_blocked_pnl_yen",
            ],
            result.get("overlap_analysis") or [],
        )
        paths["overlap"] = out / "phase660_overlap_analysis.csv"
        return paths


def run_phase660(*, repo_root: Optional[Path] = None) -> dict[str, Any]:
    job = Phase660Job(repo_root=repo_root or NATIVE_ROOT)
    result = job.run()
    paths = job.write_outputs(result)
    result["output_paths"] = {k: str(v) for k, v in paths.items()}
    return result
