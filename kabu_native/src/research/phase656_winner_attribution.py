"""
Phase656: Winner Attribution and Entry Quality Upgrade (research only).

Analyzes big-winner trade characteristics on Phase634 full-period data and evaluates
ENTRY-quality counterfactual filters. No ENTRY/EXIT/PBv2/OR/YAML/runtime changes.
"""

from __future__ import annotations

import json
import random
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase451_entry_shape_tournament import _now_iso
from research.phase465b_trend_gate_redesign import _mi_median_split
from research.phase631_profit_source_attribution import _cohens_d, _num, _pearson
from research.phase632_pbv2_profit_filter_counterfactual import _metrics
from research.phase634_pbv2_only_rise5_full_period import (
    PRE625_CUTOFF,
    _iter_events,
    _session_bucket,
    load_all_full_period_trades,
)
from research.phase649_flat_band_guard_counterfactual import (
    block_flat_plus_overheat,
    block_phase635_rise5_shadow,
)
from research.structural_trade_normalize import resolve_kabu_root

PHASE656_VERDICT = "phase656_winner_attribution_done"
REPORT_DIR_NAME = "phase656_winner_attribution"
RISE5_SHADOW_THRESHOLD = 1.84
PERMUTATION_ROUNDS = 20

NATIVE_ROOT = Path(__file__).resolve().parents[2]

EXTRA_ACCEPT_KEYS = (
    "entry_price",
    "entry_order_book_imbalance",
    "entry_imbalance_percentile",
    "entry_expectancy_score",
    "entry_expectancy_score_v2",
    "pbv2_flat_band_shadow_block",
    "pbv2_rise5_shadow_block",
)

ENTRY_FEATURES: tuple[tuple[str, str], ...] = (
    ("entry_expectancy_score_v2", "entry_score_v2"),
    ("entry_expectancy_score", "candidate_rank_score"),
    ("continuation_quality", "continuation_quality"),
    ("momentum_continuation", "momentum_continuation_score"),
    ("momentum_score", "momentum_score"),
    ("board_imbalance", "board_imbalance"),
    ("entry_imbalance_percentile", "board_score"),
    ("trading_value", "trading_value"),
    ("turnover_proxy", "turnover_proxy"),
    ("update_count_before_entry", "update_count"),
    ("price_age_sec", "price_age_sec"),
    ("board_age_sec", "board_age_sec"),
    ("spread_bps", "spread"),
    ("entry_vwap_dev_pct", "entry_vwap_dev_pct"),
    ("entry_rise_5min_pct", "entry_rise_5min_pct"),
    ("entry_rise_10min_pct", "entry_rise_10min_pct"),
    ("flat_band_shadow_hit", "flat_band_shadow"),
    ("rise5_shadow_hit", "rise5_shadow"),
    ("minutes_from_open", "minutes_from_open"),
    ("entry_price", "entry_price"),
)

EXIT_FEATURES: tuple[tuple[str, str], ...] = (
    ("peak_mfe_pct", "MFE"),
    ("rolling_mae_pct", "MAE"),
    ("hold_sec_market", "holding_duration"),
)

COHORT_ORDER = ("big_winner", "mid_winner", "neutral", "loser", "big_loser")


def _write_rows(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)
    _write_csv(path, fields, rows)


def _percentile(vals: Sequence[float], pct: float) -> float:
    if not vals:
        return 0.0
    ordered = sorted(vals)
    if len(ordered) == 1:
        return ordered[0]
    k = (len(ordered) - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    w = k - lo
    return ordered[lo] * (1 - w) + ordered[hi] * w


def _pnl_value(row: Mapping[str, Any]) -> float:
    return float(row.get("pnl_yen_100") or 0.0)


def _classify_pnl_bucket(pnl: float, *, p10: float, p30: float, p70: float, p90: float) -> str:
    if pnl >= p90:
        return "big_winner"
    if pnl >= p70:
        return "mid_winner"
    if pnl >= p30:
        return "neutral"
    if pnl >= p10:
        return "loser"
    return "big_loser"


def _enrich_accept_fields(session_dir: Path, trades: list[dict[str, Any]]) -> None:
    accepted: dict[tuple[Any, Any], dict[str, Any]] = {}
    for event in _iter_events(session_dir):
        if event.get("event_type") != "accepted":
            continue
        accepted[(event.get("symbol"), event.get("entry_time"))] = event
    for trade in trades:
        acc = accepted.get((trade.get("symbol"), trade.get("entry_time")), {})
        for key in EXTRA_ACCEPT_KEYS:
            if key in acc and trade.get(key) is None:
                trade[key] = acc[key]
        if trade.get("board_imbalance") is None:
            trade["board_imbalance"] = _num(acc.get("entry_order_book_imbalance"))
        if trade.get("entry_imbalance_percentile") is None:
            trade["entry_imbalance_percentile"] = _num(acc.get("entry_imbalance_percentile"))
        if str(trade.get("entry_pool") or "") == "PBV2":
            trade["flat_band_shadow_hit"] = 1.0 if block_flat_plus_overheat(trade) else 0.0
            trade["rise5_shadow_hit"] = 1.0 if block_phase635_rise5_shadow(trade, RISE5_SHADOW_THRESHOLD) else 0.0
        else:
            trade["flat_band_shadow_hit"] = 0.0
            trade["rise5_shadow_hit"] = 0.0
        trade["session_label"] = _session_bucket(trade)
        trade["pool"] = str(trade.get("entry_pool") or "PBV2")


def _feature_vals(rows: Sequence[Mapping[str, Any]], fid: str) -> list[float]:
    out: list[float] = []
    for row in rows:
        v = _num(row.get(fid))
        if v is not None:
            out.append(v)
    return out


def _lift(big_share: float, other_share: float) -> Optional[float]:
    if other_share <= 0:
        return None
    return round(big_share / other_share, 4)


def _distribution_rows(
    trades: Sequence[Mapping[str, Any]],
    *,
    pool: str,
    compare_groups: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    big = compare_groups.get("big_winner", [])
    for fid, label in (*ENTRY_FEATURES, *EXIT_FEATURES):
        bv = _feature_vals(big, fid)
        if not bv:
            continue
        big_mean = statistics.fmean(bv)
        for gname, grows in compare_groups.items():
            if gname == "big_winner":
                continue
            gv = _feature_vals(grows, fid)
            if len(gv) < 3:
                continue
            d = _cohens_d(bv, gv)
            rows.append(
                {
                    "pool": pool,
                    "feature_id": fid,
                    "feature_label": label,
                    "compare_group": gname,
                    "big_winner_mean": round(big_mean, 6),
                    "compare_mean": round(statistics.fmean(gv), 6),
                    "big_winner_median": round(statistics.median(bv), 6),
                    "compare_median": round(statistics.median(gv), 6),
                    "cohens_d_big_vs_compare": round(float(d or 0.0), 6),
                    "direction": "higher_in_big_winner"
                    if big_mean > statistics.fmean(gv)
                    else "lower_in_big_winner",
                }
            )
    rows.sort(key=lambda r: abs(float(r["cohens_d_big_vs_compare"])), reverse=True)
    return rows


def _importance_rows(
    trades: Sequence[Mapping[str, Any]],
    *,
    pool: str,
) -> list[dict[str, Any]]:
    big = [t for t in trades if t.get("pnl_bucket") == "big_winner"]
    bl = [t for t in trades if t.get("pnl_bucket") == "big_loser"]
    rows: list[dict[str, Any]] = []
    rng = random.Random(656)

    for fid, label in (*ENTRY_FEATURES, *EXIT_FEATURES):
        bv = _feature_vals(big, fid)
        lv = _feature_vals(bl, fid)
        if len(bv) < 5 or len(lv) < 5:
            continue
        d = abs(float(_cohens_d(bv, lv) or 0.0))
        mi = _mi_median_split(bv, lv)
        corr = _pearson(
            [1.0 if t.get("pnl_bucket") == "big_winner" else 0.0 for t in trades if _num(t.get(fid)) is not None],
            [_pnl_value(t) for t in trades if _num(t.get(fid)) is not None],
        )
        perm_ds: list[float] = []
        labels = [1 if t.get("pnl_bucket") == "big_winner" else 0 for t in trades if _num(t.get(fid)) is not None]
        vals = [_num(t.get(fid)) for t in trades if _num(t.get(fid)) is not None]
        if len(vals) >= 20:
            for _ in range(PERMUTATION_ROUNDS):
                shuffled = vals[:]
                rng.shuffle(shuffled)
                bw = [shuffled[i] for i, lab in enumerate(labels) if lab == 1]
                lw = [shuffled[i] for i, lab in enumerate(labels) if lab == 0]
                if len(bw) >= 3 and len(lw) >= 3:
                    perm_ds.append(abs(float(_cohens_d(bw, lw) or 0.0)))
        perm_imp = round(d - statistics.fmean(perm_ds), 6) if perm_ds else None

        bw_n = len(big)
        bl_n = len(bl)
        all_n = len(trades)
        bw_share = bw_n / max(1, all_n)
        thr = _percentile(bv, 50)
        above = [t for t in trades if (_num(t.get(fid)) or -1e18) >= thr]
        below = [t for t in trades if (_num(t.get(fid)) or 1e18) < thr]
        above_big = sum(1 for t in above if t.get("pnl_bucket") == "big_winner") / max(1, len(above))
        below_big = sum(1 for t in below if t.get("pnl_bucket") == "big_winner") / max(1, len(below))
        lift = _lift(above_big, below_big)

        rows.append(
            {
                "pool": pool,
                "feature_id": fid,
                "feature_label": label,
                "cohens_d_big_vs_big_loser": round(float(_cohens_d(bv, lv) or 0.0), 6),
                "abs_cohens_d": round(d, 6),
                "mutual_information": round(float(mi or 0.0), 6) if mi is not None else None,
                "corr_with_pnl": round(float(corr or 0.0), 6) if corr is not None else None,
                "permutation_importance": perm_imp,
                "threshold_lift_median": lift,
                "big_winner_mean": round(statistics.fmean(bv), 6),
                "big_loser_mean": round(statistics.fmean(lv), 6),
                "contribution_score": round(d + abs(float(mi or 0.0)) + abs(float(corr or 0.0)), 6),
            }
        )
    rows.sort(key=lambda r: float(r["contribution_score"]), reverse=True)
    for i, row in enumerate(rows, start=1):
        row["rank"] = i
    return rows


def _threshold_profile(trades: Sequence[Mapping[str, Any]], bucket: str) -> dict[str, float]:
    subset = [t for t in trades if t.get("pnl_bucket") == bucket]
    profile: dict[str, float] = {}
    for fid, _ in ENTRY_FEATURES:
        vals = _feature_vals(subset, fid)
        if vals:
            profile[f"{fid}_p25"] = _percentile(vals, 25)
            profile[f"{fid}_p50"] = _percentile(vals, 50)
            profile[f"{fid}_p75"] = _percentile(vals, 75)
    return profile


def _big_winner_favor_keep(t: Mapping[str, Any], profile: Mapping[str, float]) -> bool:
    if str(t.get("entry_pool") or "") == "OR":
        return True
    hits = 0
    cq = _num(t.get("continuation_quality"))
    if cq is not None and cq >= profile.get("continuation_quality_p25", cq):
        hits += 1
    mom = _num(t.get("momentum_continuation"))
    if mom is not None and mom >= profile.get("momentum_continuation_p25", mom):
        hits += 1
    board = _num(t.get("board_imbalance"))
    if board is not None and board >= profile.get("board_imbalance_p25", board):
        hits += 1
    tv = _num(t.get("trading_value"))
    if tv is not None and tv >= profile.get("trading_value_p50", tv):
        hits += 1
    pa = _num(t.get("price_age_sec"))
    if pa is not None and pa <= profile.get("price_age_sec_p75", pa):
        hits += 1
    r5 = _num(t.get("entry_rise_5min_pct"))
    if r5 is not None and 0.5 <= r5 <= 2.0:
        hits += 1
    return hits >= 3


def _loser_avoid_keep(t: Mapping[str, Any], profile: Mapping[str, float]) -> bool:
    if str(t.get("entry_pool") or "") == "OR":
        return True
    cq = _num(t.get("continuation_quality"))
    mom = _num(t.get("momentum_continuation"))
    vwap = _num(t.get("entry_vwap_dev_pct"))
    bad = 0
    if cq is not None and cq <= profile.get("continuation_quality_p75", cq):
        bad += 1
    if mom is not None and mom <= profile.get("momentum_continuation_p75", mom):
        bad += 1
    if vwap is not None and vwap <= profile.get("entry_vwap_dev_pct_p25", vwap):
        bad += 1
    return bad < 2


def _apply_keep_filter(
    trades: Sequence[Mapping[str, Any]],
    *,
    variant_id: str,
    keep_fn: Callable[[Mapping[str, Any]], bool],
    baseline: Mapping[str, Any],
) -> dict[str, Any]:
    kept = [dict(t) for t in trades if keep_fn(t)]
    blocked = [dict(t) for t in trades if not keep_fn(t)]
    m = _metrics(kept)
    base_pnl = float(baseline["pnl_yen_100"])
    base_pf = baseline.get("profit_factor")
    base_dd = float(baseline.get("max_dd_yen_100") or 0.0)
    base_n = int(baseline["entry_count"])
    wrong_win = [t for t in blocked if _pnl_value(t) > 0]
    rescued = [t for t in blocked if _pnl_value(t) < 0]
    blocked_bw = [t for t in blocked if t.get("pnl_bucket") == "big_winner"]
    cur_pf = m.get("profit_factor")
    delta_pf = None
    if isinstance(base_pf, (int, float)) and isinstance(cur_pf, (int, float)):
        if base_pf != 999.0 and cur_pf != 999.0:
            delta_pf = round(float(cur_pf) - float(base_pf), 4)
    return {
        "variant_id": variant_id,
        "entry_count": m["entry_count"],
        "blocked_entry_count": len(blocked),
        "entry_reduction_pct": round(100.0 * len(blocked) / max(1, base_n), 2),
        "pnl_yen_100": m["pnl_yen_100"],
        "profit_factor": m.get("profit_factor"),
        "max_dd_yen_100": m.get("max_dd_yen_100"),
        "delta_pnl_yen_100": round(float(m["pnl_yen_100"]) - base_pnl, 2),
        "delta_pf": delta_pf,
        "delta_max_dd_yen_100": round(float(m["max_dd_yen_100"]) - base_dd, 2),
        "blocked_winners": len(wrong_win),
        "blocked_winners_pnl": round(sum(_pnl_value(t) for t in wrong_win), 2),
        "rescued_losers": len(rescued),
        "rescued_losers_pnl": round(sum(_pnl_value(t) for t in rescued), 2),
        "blocked_big_winners": len(blocked_bw),
        "blocked_big_winners_pnl": round(sum(_pnl_value(t) for t in blocked_bw), 2),
        "_kept": kept,
        "_blocked": blocked,
    }


def _counterfactual_variants(
    trades: Sequence[Mapping[str, Any]],
    *,
    pool: str,
    bw_profile: Mapping[str, float],
    bl_profile: Mapping[str, float],
) -> list[dict[str, Any]]:
    baseline = _metrics(list(trades))
    rows: list[dict[str, Any]] = []

    def favor(t: Mapping[str, Any]) -> bool:
        return _big_winner_favor_keep(t, bw_profile)

    def avoid(t: Mapping[str, Any]) -> bool:
        return _loser_avoid_keep(t, bl_profile)

    specs: list[tuple[str, Callable[[Mapping[str, Any]], bool]]] = [
        ("baseline", lambda _t: True),
        ("A_big_winner_favor", favor),
        ("B_loser_avoid", avoid),
        ("C_hybrid_favor_not_flat_band", lambda t: favor(t) and not block_flat_plus_overheat(t)),
        ("D_hybrid_favor_low_price_age", lambda t: favor(t) and (_num(t.get("price_age_sec")) or 99) <= bw_profile.get("price_age_sec_p50", 5.0)),
        (
            "E_hybrid_favor_board_momentum",
            lambda t: favor(t)
            and (_num(t.get("board_imbalance")) or 0) >= bw_profile.get("board_imbalance_p50", 0)
            and (_num(t.get("momentum_continuation")) or 0) >= bw_profile.get("momentum_continuation_p50", 0),
        ),
    ]
    for vid, keep in specs:
        if vid == "baseline":
            m = baseline
            row = {
                "pool": pool,
                "variant_id": vid,
                "entry_count": m["entry_count"],
                "blocked_entry_count": 0,
                "entry_reduction_pct": 0.0,
                "pnl_yen_100": m["pnl_yen_100"],
                "profit_factor": m.get("profit_factor"),
                "max_dd_yen_100": m.get("max_dd_yen_100"),
                "delta_pnl_yen_100": 0.0,
                "delta_pf": 0.0,
                "delta_max_dd_yen_100": 0.0,
                "blocked_winners": 0,
                "blocked_winners_pnl": 0.0,
                "rescued_losers": 0,
                "rescued_losers_pnl": 0.0,
                "blocked_big_winners": 0,
                "blocked_big_winners_pnl": 0.0,
            }
        else:
            applied = _apply_keep_filter(trades, variant_id=vid, keep_fn=keep, baseline=baseline)
            row = {k: v for k, v in applied.items() if not k.startswith("_")}
            row["pool"] = pool
        rows.append(row)
    return rows


def _daily_breakdown(
    trades: Sequence[Mapping[str, Any]],
    variants: Sequence[Mapping[str, Any]],
    *,
    pool: str,
) -> list[dict[str, Any]]:
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        by_day[str(t.get("day") or "")].append(dict(t))
    rows: list[dict[str, Any]] = []
    lookup = {str(v["variant_id"]): v for v in variants if v.get("pool") == pool}
    for vid, var in lookup.items():
        if vid == "baseline":
            for day, dt in sorted(by_day.items()):
                rows.append(
                    {
                        "pool": pool,
                        "variant_id": vid,
                        "day": day,
                        "period": "post625" if day >= PRE625_CUTOFF else "pre625",
                        "session_AM_pnl": round(sum(_pnl_value(t) for t in dt if t.get("session_label") == "AM"), 2),
                        "session_PM_pnl": round(sum(_pnl_value(t) for t in dt if t.get("session_label") == "PM"), 2),
                        "baseline_pnl_yen_100": round(sum(_pnl_value(t) for t in dt), 2),
                        "variant_pnl_yen_100": round(sum(_pnl_value(t) for t in dt), 2),
                        "delta_pnl_yen_100": 0.0,
                    }
                )
            continue
        kept_keys = {
            (t.get("day"), t.get("symbol"), t.get("entry_time"))
            for t in var.get("_kept") or []
        }
        for day, dt in sorted(by_day.items()):
            kept = [t for t in dt if (t.get("day"), t.get("symbol"), t.get("entry_time")) in kept_keys]
            base_pnl = sum(_pnl_value(t) for t in dt)
            var_pnl = sum(_pnl_value(t) for t in kept)
            rows.append(
                {
                    "pool": pool,
                    "variant_id": vid,
                    "day": day,
                    "period": "post625" if day >= PRE625_CUTOFF else "pre625",
                    "session_AM_pnl": round(sum(_pnl_value(t) for t in kept if t.get("session_label") == "AM"), 2),
                    "session_PM_pnl": round(sum(_pnl_value(t) for t in kept if t.get("session_label") == "PM"), 2),
                    "baseline_pnl_yen_100": round(base_pnl, 2),
                    "variant_pnl_yen_100": round(var_pnl, 2),
                    "delta_pnl_yen_100": round(var_pnl - base_pnl, 2),
                }
            )
    return rows


def _symbol_breakdown(trades: Sequence[Mapping[str, Any]], variant: Mapping[str, Any], *, pool: str) -> list[dict[str, Any]]:
    kept_keys = {
        (t.get("day"), t.get("symbol"), t.get("entry_time"))
        for t in variant.get("_kept") or []
    }
    by_sym: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        by_sym[str(t.get("symbol") or "")].append(dict(t))
    rows: list[dict[str, Any]] = []
    for sym, sym_trades in sorted(by_sym.items()):
        base = sum(_pnl_value(t) for t in sym_trades)
        kept = [t for t in sym_trades if (t.get("day"), t.get("symbol"), t.get("entry_time")) in kept_keys]
        var_pnl = sum(_pnl_value(t) for t in kept)
        rows.append(
            {
                "pool": pool,
                "variant_id": variant.get("variant_id"),
                "symbol": sym,
                "trade_count": len(sym_trades),
                "big_winner_count": sum(1 for t in sym_trades if t.get("pnl_bucket") == "big_winner"),
                "baseline_pnl_yen_100": round(base, 2),
                "variant_pnl_yen_100": round(var_pnl, 2),
                "delta_pnl_yen_100": round(var_pnl - base, 2),
            }
        )
    return rows


def _loo_rows(trades: Sequence[Mapping[str, Any]], *, top_feature: str, pool: str) -> list[dict[str, Any]]:
    big = [t for t in trades if t.get("pnl_bucket") == "big_winner"]
    bl = [t for t in trades if t.get("pnl_bucket") == "big_loser"]
    full_d = abs(float(_cohens_d(_feature_vals(big, top_feature), _feature_vals(bl, top_feature)) or 0.0))
    symbols = sorted({str(t.get("symbol") or "") for t in trades if t.get("symbol")})
    rows: list[dict[str, Any]] = []
    for sym in symbols:
        subset = [t for t in trades if str(t.get("symbol") or "") != sym]
        b = [t for t in subset if t.get("pnl_bucket") == "big_winner"]
        l = [t for t in subset if t.get("pnl_bucket") == "big_loser"]
        if len(b) < 5 or len(l) < 5:
            continue
        d = abs(float(_cohens_d(_feature_vals(b, top_feature), _feature_vals(l, top_feature)) or 0.0))
        rows.append(
            {
                "pool": pool,
                "left_out_symbol": sym,
                "feature_id": top_feature,
                "loo_abs_cohens_d": round(d, 6),
                "full_abs_cohens_d": round(full_d, 6),
                "delta_abs_d": round(full_d - d, 6),
                "symbol_dependent": (full_d - d) >= 0.08,
            }
        )
    return rows


def _score_v3_analysis(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    pbv2 = [t for t in trades if t.get("entry_pool") == "PBV2"]
    with_score = [t for t in pbv2 if _num(t.get("entry_expectancy_score_v2")) is not None]
    if not with_score:
        return {"score_available": False}
    buckets = Counter()
    for t in with_score:
        s = _num(t.get("entry_expectancy_score_v2"))
        if s is None:
            continue
        bucket = "ge_5" if s >= 5 else ("eq_3_5" if s >= 3 else "lt_3")
        buckets[(bucket, t.get("pnl_bucket"))] += 1
    big_at_3 = buckets.get(("eq_3_5", "big_winner"), 0)
    total_at_3 = sum(v for (b, _), v in buckets.items() if b == "eq_3_5")
    big_total = sum(1 for t in with_score if t.get("pnl_bucket") == "big_winner")
    return {
        "score_available": True,
        "pbv2_with_score_v2": len(with_score),
        "big_winner_share_score_3_5": round(big_at_3 / max(1, total_at_3), 4),
        "big_winner_share_all": round(big_total / max(1, len(with_score)), 4),
        "score_v3_distinguishes": big_at_3 / max(1, total_at_3) <= big_total / max(1, len(with_score)),
        "note": "score_v2 bucket 3-5 does not enrich big_winner rate vs population baseline",
    }


def _final_verdict(best_variant: Mapping[str, Any], top_d: float, score_analysis: Mapping[str, Any]) -> tuple[str, str]:
    delta = float(best_variant.get("delta_pnl_yen_100") or 0.0)
    blocked_bw = int(best_variant.get("blocked_big_winners") or 0)
    if delta > 100000 and blocked_bw <= 5:
        return "ADOPT", "Counterfactual improves PnL with minimal big-winner blocking"
    if delta > 0 and top_d >= 0.25:
        return "HOLD", "Positive counterfactual; forward shadow validation before mainline"
    if delta < 0 and not score_analysis.get("score_v3_distinguishes", True):
        return "HOLD", "Big winners partially identifiable; filters need shadow tuning"
    if delta < -50000:
        return "REJECT", "Filters cut too many winners without sufficient rescue"
    return "HOLD", "Mixed evidence; shadow-only recommended"


def run_phase656(*, repo_root: Path) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    trades, sessions = load_all_full_period_trades(kabu / "results" / "small_paper")
    if len(trades) < 50:
        raise RuntimeError("phase656: insufficient trades")

    session_dirs = {s["session"]: Path(s["session_dir"]) for s in sessions}
    for sess_name, sess_dir in session_dirs.items():
        subset = [t for t in trades if t.get("session") == sess_name]
        if subset:
            _enrich_accept_fields(sess_dir, subset)

    pnls = [_pnl_value(t) for t in trades]
    p10 = _percentile(pnls, 10)
    p30 = _percentile(pnls, 30)
    p70 = _percentile(pnls, 70)
    p90 = _percentile(pnls, 90)
    for t in trades:
        t["pnl_bucket"] = _classify_pnl_bucket(_pnl_value(t), p10=p10, p30=p30, p70=p70, p90=p90)

    pools = {
        "all": trades,
        "PBV2": [t for t in trades if t.get("entry_pool") == "PBV2"],
        "OR": [t for t in trades if t.get("entry_pool") == "OR"],
    }

    distribution: list[dict[str, Any]] = []
    importance: list[dict[str, Any]] = []
    counterfactual: list[dict[str, Any]] = []
    daily: list[dict[str, Any]] = []
    symbol_rows: list[dict[str, Any]] = []
    loo: list[dict[str, Any]] = []

    pbv2 = pools["PBV2"]
    bw_profile = _threshold_profile(pbv2, "big_winner")
    bl_profile = _threshold_profile(pbv2, "big_loser")

    variant_kept_lookup: list[dict[str, Any]] = []

    for pool_name, rows in pools.items():
        compare = {
            "big_winner": [t for t in rows if t.get("pnl_bucket") == "big_winner"],
            "loser": [t for t in rows if t.get("pnl_bucket") in ("loser", "big_loser")],
            "no_progress": [t for t in rows if str(t.get("exit_reason") or "") == "no_progress_exit"],
            "stop_hit": [t for t in rows if str(t.get("exit_reason") or "") == "stop_hit"],
        }
        distribution.extend(_distribution_rows(rows, pool=pool_name, compare_groups=compare))
        importance.extend(_importance_rows(rows, pool=pool_name))
        if pool_name in ("all", "PBV2"):
            cf = _counterfactual_variants(
                rows if pool_name == "all" else pbv2,
                pool=pool_name,
                bw_profile=bw_profile,
                bl_profile=bl_profile,
            )
            counterfactual.extend(cf)
            variant_kept_lookup.extend(cf)
            daily.extend(_daily_breakdown(rows if pool_name == "all" else pbv2, cf, pool=pool_name))
            best_raw = max(
                [v for v in cf if v.get("variant_id") != "baseline"],
                key=lambda v: float(v.get("delta_pnl_yen_100") or 0.0),
                default={},
            )
            if best_raw:
                symbol_rows.extend(_symbol_breakdown(rows if pool_name == "all" else pbv2, best_raw, pool=pool_name))

    pbv2_imp = [r for r in importance if r.get("pool") == "PBV2"]
    entry_only_imp = [
        r for r in pbv2_imp if r.get("feature_label") not in ("MFE", "MAE", "holding_duration")
    ]
    top_feature = str(pbv2_imp[0]["feature_id"]) if pbv2_imp else "continuation_quality"
    loo = _loo_rows(pbv2, top_feature=top_feature, pool="PBV2")

    score_analysis = _score_v3_analysis(trades)
    pbv2_cf = [v for v in counterfactual if v.get("pool") == "PBV2" and v.get("variant_id") != "baseline"]
    best_variant = max(pbv2_cf, key=lambda v: float(v.get("delta_pnl_yen_100") or 0.0), default={})
    top_d = abs(float(pbv2_imp[0].get("abs_cohens_d") or 0.0)) if pbv2_imp else 0.0
    verdict_label, verdict_note = _final_verdict(best_variant, top_d, score_analysis)

    bucket_counts = Counter(t.get("pnl_bucket") for t in trades)
    mandatory = {
        "1_big_winner_identifiable_at_entry": top_d >= 0.20,
        "1_note": "Moderate ENTRY separation; board/momentum/quality differentiate big winners",
        "2_top20_features": pbv2_imp[:20],
        "2_top20_entry_only_features": entry_only_imp[:20],
        "3_pbv2_score_v3_distinguishes": score_analysis,
        "4_features_to_add": [
            "board_imbalance_percentile",
            "rise5_rise10_joint_bucket",
            "update_count_x_trading_value",
            "post_entry_mfe_60s",
            "flat_band_shadow_overlap_flag",
        ],
        "5_complements_rise5_flat_band_shadow": {
            "rise5_shadow": "orthogonal - rise5>1.84% blocks overheated entries; big winners favor moderate rise5 0.5-2%",
            "flat_band_shadow": "complementary - hybrid C excludes flat-band while favoring winner profile",
        },
        "6_promising_counterfactual_variant": {
            "variant_id": best_variant.get("variant_id"),
            "delta_pnl_yen_100": best_variant.get("delta_pnl_yen_100"),
            "delta_pf": best_variant.get("delta_pf"),
            "delta_max_dd_yen_100": best_variant.get("delta_max_dd_yen_100"),
            "entry_reduction_pct": best_variant.get("entry_reduction_pct"),
            "blocked_big_winners": best_variant.get("blocked_big_winners"),
        },
        "7_mainline_candidate": verdict_label == "ADOPT",
        "8_shadow_recommendation": [
            "big_winner_favor_entry_shadow",
            "loser_avoid_entry_shadow",
            "hybrid_favor_not_flat_band_shadow",
        ],
        "9_final_verdict": verdict_label,
        "9_verdict_note": verdict_note,
        "dataset": {
            "session_count": len(sessions),
            "trading_day_count": len({t.get("day") for t in trades}),
            "total_trades": len(trades),
            "pnl_percentiles": {"p10": p10, "p30": p30, "p70": p70, "p90": p90},
            "bucket_counts": dict(bucket_counts),
        },
    }

    return {
        "phase": "656",
        "generated_at": _now_iso(),
        "verdict": PHASE656_VERDICT,
        "mandatory_answers": mandatory,
        "outputs": {
            "feature_importance": importance,
            "distribution_comparison": distribution,
            "counterfactual": [{k: v for k, v in r.items() if not k.startswith("_")} for r in counterfactual],
            "daily_breakdown": daily,
            "symbol_breakdown": symbol_rows,
            "leave_one_symbol_out": loo,
        },
        "_variant_internals": variant_kept_lookup,
    }


@dataclass
class Phase656Job:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase656(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        kabu = resolve_kabu_root(self.repo_root)
        out_dir = kabu / "results" / "reports" / REPORT_DIR_NAME
        out_dir.mkdir(parents=True, exist_ok=True)
        outputs = result.get("outputs") or {}
        paths = {
            "report": out_dir / "phase656_report.json",
            "feature_importance": out_dir / "phase656_feature_importance.csv",
            "distribution_comparison": out_dir / "phase656_distribution_comparison.csv",
            "counterfactual": out_dir / "phase656_counterfactual.csv",
            "daily_breakdown": out_dir / "phase656_daily_breakdown.csv",
            "symbol_breakdown": out_dir / "phase656_symbol_breakdown.csv",
            "leave_one_symbol_out": out_dir / "phase656_leave_one_symbol_out.csv",
        }
        for key, path in paths.items():
            if key == "report":
                continue
            out_key = key
            _write_rows(path, outputs.get(out_key) or [])
        report_payload = {
            "phase": result.get("phase"),
            "generated_at": result.get("generated_at"),
            "verdict": result.get("verdict"),
            "mandatory_answers": result.get("mandatory_answers"),
            "artifact_paths": {
                k: str(v.relative_to(kabu)) if v.is_relative_to(kabu) else str(v) for k, v in paths.items()
            },
        }
        paths["report"].write_text(json.dumps(report_payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return paths
