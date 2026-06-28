"""
Phase554 — stop_low_mfe entry quality feature study.

Research only. Current Runtime (B) accepted trades. No Runtime changes.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _float, _parse_ts
from research.phase451_entry_shape_tournament import JST, _build_price_index_to, _now_iso
from research.phase465b_trend_gate_redesign import _cohens_d
from research.phase484_stop_low_mfe_feature_discovery import (
    _compute_base_features,
    _load_day_event_snaps,
    _board_features,
    _momentum_slope,
)
from research.phase501_classic_indicator_audit import _macd_at_entry
from research.phase515b_day_high_breakout_dependency_audit import _bar_index_at
from research.phase518_day_high_winner_loser_separation import (
    FEATURE_IDS,
    _build_micro_lookup,
    _extract_entry_features,
    _percentile,
    _separation_score,
)
from research.phase524_live_reentry_guard_and_stop_low_mfe import (
    PERIOD_START_LIVE,
    _build_bar_cache_for_days,
    _latest_live_day,
    _num,
)
from research.phase540_no_progress_mfe0_entry_quality import _is_mfe0, _is_winner, _mfe_pct
from research.phase541_guard_v2_full_period_validation import _enrich_trades_phase541
from research.phase544_entry_feature_attribution import _extend_entry_features
from research.phase551_current_runtime_full_period_replay import (
    PERIOD_EXTENDED_START,
    _cap_extension_metrics,
)
from research.phase553_loss_day_root_cause_analysis import _load_b_runtime_accepted, _iter_days
from research.phase523_reentry_definition_overlay_edge_reality_audit import _is_stop_hit
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE554_VERDICT = "phase554_stop_low_mfe_entry_quality_feature_study_done"
LIVE_END_DEFAULT = "20260625"
TARGET_DAY = "20260618"
TARGET_SYMBOLS = ("6976.T", "6387.T", "6779.T")

STOP_LOW_MFE_MFE_PCT = 0.6
NORMAL_WINNER_MFE_PCT = 0.8
BIG_WINNER_MFE_PCT = 1.5

COHORTS: tuple[str, ...] = (
    "stop_low_mfe",
    "normal_winner",
    "big_winner",
    "mfe0",
)

NUMERIC_FEATURES: tuple[str, ...] = (
    "volume_ratio",
    "rolling_volume_percentile",
    "board_imbalance",
    "spread",
    "update_count_before_entry",
    "vwap_distance_pct",
    "adx14",
    "rsi14",
    "five_min_position",
    "volume_acceleration_1m",
    "volume_acceleration_3m",
    "volume_acceleration_5m",
    "relative_volume",
    "liquidity_burst",
    "tick_speed",
    "high_update_count",
    "high_update_persistence",
    "board_consumption_speed",
    "board_collapse_rate",
    "momentum_decay",
    "price_acceleration_decay",
    "vwap_recovery_speed",
    "momentum_slope",
    "price_acceleration",
    "board_update_frequency",
)

GUARD_FEATURES: tuple[str, ...] = (
    "tick_speed",
    "liquidity_burst",
    "volume_acceleration_3m",
    "high_update_count",
    "momentum_decay",
    "board_consumption_speed",
    "volume_acceleration_5m",
    "high_update_persistence",
    "board_collapse_rate",
)

SEPARATION_FIELDS = [
    "feature",
    "cohort_a",
    "cohort_b",
    "count_a",
    "count_b",
    "mean_a",
    "mean_b",
    "median_a",
    "median_b",
    "cohens_d",
    "separation_score",
    "missing_rate_a",
    "missing_rate_b",
]

RANKING_FIELDS = [
    "rank",
    "feature",
    "cohens_d",
    "separation_score",
    "missing_rate",
    "winner_loss_risk",
    "stop_low_mfe_mean",
    "normal_winner_mean",
    "direction",
]

GUARD_FIELDS = [
    "guard_id",
    "feature",
    "threshold",
    "direction",
    "pnl_yen_100",
    "profit_factor",
    "stop_low_mfe_blocked",
    "stop_low_mfe_reduction",
    "lost_big_winner",
    "normal_winner_blocked",
    "trade_retention",
    "net_improvement_yen_100",
    "classification",
]

COUNTERFACTUAL_FIELDS = [
    "trade_id",
    "symbol",
    "day",
    "pnl_yen_100",
    "mfe_pct",
    "exit_reason",
    "cohort",
    "guard_would_block",
    "blocking_guards",
    "saved_loss_yen_100",
]


def _is_stop_low_mfe_554(row: Mapping[str, Any]) -> bool:
    return _is_stop_hit(row) and _mfe_pct(row) < STOP_LOW_MFE_MFE_PCT


def _is_normal_winner(row: Mapping[str, Any]) -> bool:
    return _num(row.get("pnl_yen_100")) > 0 and _mfe_pct(row) >= NORMAL_WINNER_MFE_PCT


def _is_big_winner_554(row: Mapping[str, Any]) -> bool:
    return _mfe_pct(row) >= BIG_WINNER_MFE_PCT


def _cohort_flags(row: Mapping[str, Any]) -> dict[str, bool]:
    return {
        "stop_low_mfe": _is_stop_low_mfe_554(row),
        "normal_winner": _is_normal_winner(row),
        "big_winner": _is_big_winner_554(row),
        "mfe0": _is_mfe0(row),
    }


def _feature_value(row: Mapping[str, Any], feat: str) -> Optional[float]:
    v = row.get(feat)
    if v is None or v == "":
        if feat == "spread":
            v = row.get("spread_bps")
        if v is None or v == "":
            return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _volume_accel(bars: Sequence[Any], ei: int, window: int) -> Optional[float]:
    if ei < window or window <= 0:
        return None
    recent = sum(float(b.volume) for b in bars[ei - window + 1 : ei + 1])
    prior = sum(float(b.volume) for b in bars[ei - 2 * window + 1 : ei - window + 1])
    if prior <= 0:
        return None
    return round((recent - prior) / prior, 6)


def _extend_phase554_features(
    row: Mapping[str, Any],
    *,
    bar_cache: Mapping,
    board_snaps: Mapping[str, list[tuple[Any, float]]],
) -> dict[str, Any]:
    out = dict(_extend_entry_features(row, bar_cache=bar_cache))
    base484 = _compute_base_features(row)
    board = _board_features(row, board_snaps)

    sym_t = f"{str(row.get('symbol') or '').replace('.T', '')}.T"
    day = str(row.get("day") or "")[:8]
    ent = _parse_ts(str(row.get("entry_time") or ""))
    cached = bar_cache.get((sym_t, day))

    out["volume_acceleration_1m"] = None
    out["volume_acceleration_3m"] = None
    out["volume_acceleration_5m"] = None
    if cached and ent is not None:
        bars, _ = cached
        ei = _bar_index_at(bars, ent)
        if ei is not None:
            out["volume_acceleration_1m"] = _volume_accel(bars, ei, 1)
            out["volume_acceleration_3m"] = _volume_accel(bars, ei, 3)
            out["volume_acceleration_5m"] = _volume_accel(bars, ei, 5)

    out["relative_volume"] = _num(row.get("relative_volume")) or _num(row.get("volume_ratio"))
    out["liquidity_burst"] = _num(row.get("liquidity_burst"))
    out["high_update_count"] = _num(
        row.get("high_update_count_30m") or row.get("high_update_count_session") or row.get("high_update_count")
    )
    hu_recent = row.get("high_update_recent")
    out["high_update_persistence"] = (
        round(float(out["high_update_count"]) * (1.0 if hu_recent in (True, "True", "true", 1, "1") else 0.5), 4)
        if out["high_update_count"] is not None
        else None
    )

    d1 = board.get("D1_board_change_5m")
    d2 = board.get("D2_board_change_10m")
    out["board_consumption_speed"] = round(-float(d1), 6) if d1 is not None and d1 < 0 else (
        round(float(d1), 6) if d1 is not None else None
    )
    out["board_collapse_rate"] = board.get("D3_board_decay_score")

    r5 = _float(row.get("entry_rise_5min_pct") or row.get("return_5min_pct"))
    r15 = _float(row.get("entry_rise_15min_pct") or row.get("return_15min_pct"))
    out["momentum_decay"] = base484.get("A2_r15_minus_r5")
    if out["momentum_decay"] is None and r5 is not None and r15 is not None:
        out["momentum_decay"] = round(r15 - r5, 6)

    pa = out.get("price_acceleration")
    if pa is not None and r5 is not None:
        out["price_acceleration_decay"] = round(float(pa) - float(r5), 6)
    else:
        out["price_acceleration_decay"] = None

    vwap_dev = _float(row.get("vwap_distance_pct"))
    reclaim = _float(row.get("reclaim_count_30tick") or row.get("vwap_reclaim_count"))
    out["vwap_recovery_speed"] = (
        round(reclaim / max(abs(vwap_dev), 0.01), 6) if reclaim is not None and vwap_dev is not None else None
    )

    out["cluster_id"] = row.get("cluster_id")
    out["cluster_guard_status"] = row.get("cluster_guard_status")
    out["spread"] = _num(row.get("spread") or row.get("spread_bps"))
    out["rolling_volume_percentile"] = row.get("rolling_volume_percentile")
    out["five_min_position"] = row.get("five_min_position")
    out["momentum_slope"] = out.get("momentum_slope") or _momentum_slope(row)
    return out


def _enrich_phase554(
    trades: Sequence[Mapping[str, Any]],
    *,
    bar_cache: Mapping,
    micro_lookup: Mapping,
    board_snaps_by_day: Mapping[str, Mapping[str, list[tuple[Any, float]]]],
) -> list[dict[str, Any]]:
    base = _enrich_trades_phase541(trades, bar_cache=bar_cache, micro_lookup=micro_lookup)
    out: list[dict[str, Any]] = []
    for row in base:
        r = dict(row)
        day = str(r.get("day") or "")[:8]
        snaps = board_snaps_by_day.get(day, {})
        r.update(_extend_phase554_features(r, bar_cache=bar_cache, board_snaps=snaps))
        flags = _cohort_flags(r)
        r.update(
            {
                "mfe_pct": round(_mfe_pct(r), 4),
                **{f"is_{k}": v for k, v in flags.items()},
            }
        )
        out.append(r)
    return out


def _cohort_vals(rows: Sequence[Mapping[str, Any]], feat: str, cohort: str) -> list[float]:
    vals: list[float] = []
    for r in rows:
        if not _cohort_flags(r).get(cohort):
            continue
        v = _feature_value(r, feat)
        if v is not None:
            vals.append(v)
    return vals


def _feature_separation_rows(enriched: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pairs = (
        ("stop_low_mfe", "normal_winner"),
        ("stop_low_mfe", "big_winner"),
        ("stop_low_mfe", "mfe0"),
        ("mfe0", "normal_winner"),
    )
    for feat in NUMERIC_FEATURES:
        for ca, cb in pairs:
            va = _cohort_vals(enriched, feat, ca)
            vb = _cohort_vals(enriched, feat, cb)
            na = sum(1 for r in enriched if _cohort_flags(r).get(ca))
            nb = sum(1 for r in enriched if _cohort_flags(r).get(cb))
            d = _cohens_d(va, vb) if len(va) >= 3 and len(vb) >= 3 else None
            sep = _separation_score(va, vb) if len(va) >= 2 and len(vb) >= 2 else None
            rows.append(
                {
                    "feature": feat,
                    "cohort_a": ca,
                    "cohort_b": cb,
                    "count_a": na,
                    "count_b": nb,
                    "mean_a": round(statistics.mean(va), 6) if va else None,
                    "mean_b": round(statistics.mean(vb), 6) if vb else None,
                    "median_a": round(statistics.median(va), 6) if va else None,
                    "median_b": round(statistics.median(vb), 6) if vb else None,
                    "cohens_d": round(d, 4) if d is not None else None,
                    "separation_score": round(sep, 4) if sep is not None else None,
                    "missing_rate_a": round(1.0 - len(va) / na, 4) if na else 1.0,
                    "missing_rate_b": round(1.0 - len(vb) / nb, 4) if nb else 1.0,
                }
            )
    return rows


def _winner_loss_risk(
    enriched: Sequence[Mapping[str, Any]],
    *,
    feat: str,
    thr: float,
    direction: str,
) -> Optional[float]:
    winners = [r for r in enriched if _cohort_flags(r)["normal_winner"]]
    if not winners:
        return None

    def blocked(r: Mapping[str, Any]) -> bool:
        v = _feature_value(r, feat)
        if v is None:
            return False
        return v >= thr if direction == "ge" else v <= thr

    blocked_w = sum(1 for r in winners if blocked(r))
    return round(blocked_w / len(winners), 4)


def _feature_ranking_rows(enriched: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    slm = [r for r in enriched if _cohort_flags(r)["stop_low_mfe"]]
    win = [r for r in enriched if _cohort_flags(r)["normal_winner"]]
    rows: list[dict[str, Any]] = []
    for feat in NUMERIC_FEATURES:
        slm_vals = [_feature_value(r, feat) for r in slm]
        slm_vals = [v for v in slm_vals if v is not None]
        win_vals = [_feature_value(r, feat) for r in win]
        win_vals = [v for v in win_vals if v is not None]
        miss = 1.0 - (len(slm_vals) / len(slm)) if slm else 1.0
        d = _cohens_d(slm_vals, win_vals) if len(slm_vals) >= 3 and len(win_vals) >= 3 else None
        sep = _separation_score(slm_vals, win_vals) if len(slm_vals) >= 2 and len(win_vals) >= 2 else None
        slm_mean = statistics.mean(slm_vals) if slm_vals else None
        win_mean = statistics.mean(win_vals) if win_vals else None
        direction = "higher_in_stop_low_mfe"
        if slm_mean is not None and win_mean is not None and slm_mean < win_mean:
            direction = "lower_in_stop_low_mfe"

        thr = _percentile(slm_vals, 50) if slm_vals else None
        wlr = None
        if thr is not None and d is not None:
            wlr = _winner_loss_risk(
                enriched,
                feat=feat,
                thr=thr,
                direction="ge" if (d or 0) > 0 else "le",
            )

        rows.append(
            {
                "feature": feat,
                "cohens_d": round(d, 4) if d is not None else None,
                "separation_score": round(sep, 4) if sep is not None else None,
                "missing_rate": round(miss, 4),
                "winner_loss_risk": wlr,
                "stop_low_mfe_mean": round(slm_mean, 6) if slm_mean is not None else None,
                "normal_winner_mean": round(win_mean, 6) if win_mean is not None else None,
                "direction": direction,
            }
        )
    rows.sort(key=lambda r: abs(_num(r.get("separation_score")) or 0), reverse=True)
    for i, row in enumerate(rows, start=1):
        row["rank"] = i
    return rows


def _eval_guard(
    enriched: Sequence[Mapping[str, Any]],
    *,
    guard_id: str,
    feat: str,
    thr: float,
    direction: str,
    baseline_pnl: float,
    baseline_trades: int,
    baseline_slm: int,
    baseline_big: int,
) -> dict[str, Any]:
    def passes(r: Mapping[str, Any]) -> bool:
        v = _feature_value(r, feat)
        if v is None:
            return True
        return v >= thr if direction == "ge" else v <= thr

    kept = [r for r in enriched if passes(r)]
    blocked = [r for r in enriched if not passes(r)]
    pnls = [_num(r.get("pnl_yen_100")) for r in kept]
    total = round(sum(pnls), 2)
    slm_blocked = sum(1 for r in blocked if _cohort_flags(r)["stop_low_mfe"])
    big_blocked = sum(1 for r in blocked if _cohort_flags(r)["big_winner"])
    win_blocked = sum(1 for r in blocked if _cohort_flags(r)["normal_winner"])
    classification = "D_reject"
    if total > baseline_pnl and slm_blocked >= 2 and big_blocked <= 2 and len(kept) >= baseline_trades * 0.5:
        classification = "B_shadow_candidate"
    if total > baseline_pnl + 10000 and slm_blocked >= 3 and big_blocked == 0:
        classification = "A_runtime_candidate"

    return {
        "guard_id": guard_id,
        "feature": feat,
        "threshold": thr,
        "direction": direction,
        "pnl_yen_100": total,
        "profit_factor": _pf(pnls),
        "stop_low_mfe_blocked": slm_blocked,
        "stop_low_mfe_reduction": round(slm_blocked / baseline_slm, 4) if baseline_slm else 0.0,
        "lost_big_winner": big_blocked,
        "normal_winner_blocked": win_blocked,
        "trade_retention": round(len(kept) / baseline_trades, 4) if baseline_trades else 0.0,
        "net_improvement_yen_100": round(total - baseline_pnl, 2),
        "classification": classification,
    }


def _guard_candidate_rows(enriched: Sequence[Mapping[str, Any]], *, baseline_pnl: float) -> list[dict[str, Any]]:
    baseline_trades = len(enriched)
    baseline_slm = sum(1 for r in enriched if _cohort_flags(r)["stop_low_mfe"])
    baseline_big = sum(1 for r in enriched if _cohort_flags(r)["big_winner"])
    rows: list[dict[str, Any]] = []
    gid = 0
    for feat in GUARD_FEATURES:
        vals = [_feature_value(r, feat) for r in enriched]
        vals = [v for v in vals if v is not None]
        if len(vals) < 20:
            continue
        slm_vals = [_feature_value(r, feat) for r in enriched if _cohort_flags(r)["stop_low_mfe"]]
        slm_vals = [v for v in slm_vals if v is not None]
        win_vals = [_feature_value(r, feat) for r in enriched if _cohort_flags(r)["normal_winner"]]
        win_vals = [v for v in win_vals if v is not None]
        d = _cohens_d(slm_vals, win_vals) if slm_vals and win_vals else None
        prefer_ge = (d or 0) >= 0
        for p in (50, 65, 75, 85):
            thr = _percentile(slm_vals if slm_vals else vals, p)
            if thr is None:
                continue
            for direction in (("ge",) if prefer_ge else ("le",)):
                gid += 1
                rows.append(
                    _eval_guard(
                        enriched,
                        guard_id=f"G554_{gid:03d}",
                        feat=feat,
                        thr=thr,
                        direction=direction,
                        baseline_pnl=baseline_pnl,
                        baseline_trades=baseline_trades,
                        baseline_slm=baseline_slm,
                        baseline_big=baseline_big,
                    )
                )
    rows.sort(key=lambda r: (_num(r.get("net_improvement_yen_100")), _num(r.get("stop_low_mfe_blocked"))), reverse=True)
    return rows


def _counterfactual_618(
    day_trades: Sequence[Mapping[str, Any]],
    guards: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    top_guards = [g for g in guards if _num(g.get("net_improvement_yen_100")) > 0][:8]
    rows: list[dict[str, Any]] = []
    for i, t in enumerate(day_trades):
        sym = str(t.get("symbol") or "")
        if not sym.endswith(".T"):
            sym = f"{sym}.T"
        blocking: list[str] = []
        for g in top_guards:
            feat = str(g.get("feature") or "")
            thr = float(g.get("threshold") or 0)
            direction = str(g.get("direction") or "ge")
            v = _feature_value(t, feat)
            if v is None:
                continue
            hit = v >= thr if direction == "ge" else v <= thr
            if not hit:
                blocking.append(str(g.get("guard_id")))
        pnl = _num(t.get("pnl_yen_100"))
        rows.append(
            {
                "trade_id": t.get("trade_id") or f"D{i+1:02d}",
                "symbol": sym,
                "day": t.get("day"),
                "pnl_yen_100": pnl,
                "mfe_pct": _mfe_pct(t),
                "exit_reason": t.get("exit_reason"),
                "cohort": "stop_low_mfe" if _cohort_flags(t)["stop_low_mfe"] else "other",
                "guard_would_block": len(blocking) > 0,
                "blocking_guards": "|".join(blocking) if blocking else "",
                "saved_loss_yen_100": round(-pnl, 2) if blocking and pnl < 0 else 0.0,
            }
        )
    return rows


def _loss_attribution(enriched: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    losers = [r for r in enriched if _num(r.get("pnl_yen_100")) < 0]
    total_loss = sum(_num(r.get("pnl_yen_100")) for r in losers)
    slm = [r for r in losers if _cohort_flags(r)["stop_low_mfe"]]
    mfe0 = [r for r in losers if _cohort_flags(r)["mfe0"]]
    slm_loss = sum(_num(r.get("pnl_yen_100")) for r in slm)
    mfe0_loss = sum(_num(r.get("pnl_yen_100")) for r in mfe0)
    return {
        "loser_count": len(losers),
        "total_loss_yen_100": round(total_loss, 2),
        "stop_low_mfe_count": len(slm),
        "stop_low_mfe_loss_yen_100": round(slm_loss, 2),
        "stop_low_mfe_share_of_loss_pct": round(-slm_loss / total_loss * 100, 2) if total_loss < 0 else 0.0,
        "mfe0_count": len(mfe0),
        "mfe0_loss_yen_100": round(mfe0_loss, 2),
        "mfe0_share_of_loss_pct": round(-mfe0_loss / total_loss * 100, 2) if total_loss < 0 else 0.0,
    }


def _mandatory_answers(
    *,
    live_attr: Mapping[str, Any],
    full_attr: Mapping[str, Any],
    ranking: Sequence[Mapping[str, Any]],
    guards: Sequence[Mapping[str, Any]],
    counterfactual: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    top = ranking[0] if ranking else {}
    top_feat = str(top.get("feature") or "")

    def _rank_feat(name: str) -> Optional[dict[str, Any]]:
        for r in ranking:
            if r.get("feature") == name:
                return dict(r)
        return None

    vol_acc = _rank_feat("volume_acceleration_3m") or _rank_feat("volume_acceleration_5m")
    tick = _rank_feat("tick_speed")
    hup = _rank_feat("high_update_persistence")
    board = _rank_feat("board_consumption_speed") or _rank_feat("board_collapse_rate")

    shadow = [g for g in guards if str(g.get("classification", "")).startswith("B_")]
    runtime = [g for g in guards if str(g.get("classification", "")).startswith("A_")]

    cf_targets = {str(r.get("symbol")): r for r in counterfactual if str(r.get("symbol")) in TARGET_SYMBOLS}
    blocked_targets = {sym: r for sym, r in cf_targets.items() if r.get("guard_would_block")}

    best_guard = guards[0] if guards else {}
    win_block = _num(best_guard.get("normal_winner_blocked"))

    return {
        "1_stop_low_mfe_main_loss_driver_full_period": _num(full_attr.get("stop_low_mfe_share_of_loss_pct")) > _num(
            full_attr.get("mfe0_share_of_loss_pct")
        ),
        "1_live_window_stop_low_mfe_share_pct": live_attr.get("stop_low_mfe_share_of_loss_pct"),
        "1_full_period_stop_low_mfe_share_pct": full_attr.get("stop_low_mfe_share_of_loss_pct"),
        "2_separable_features_exist": abs(_num(top.get("separation_score"))) >= 0.15,
        "3_most_effective_feature": top_feat,
        "3_top_cohens_d": top.get("cohens_d"),
        "4_volume_acceleration_effective": abs(_num(vol_acc.get("separation_score") if vol_acc else 0)) >= 0.12,
        "4_volume_acceleration_rank": vol_acc.get("rank") if vol_acc else None,
        "5_tick_speed_effective": abs(_num(tick.get("separation_score") if tick else 0)) >= 0.12,
        "5_tick_speed_rank": tick.get("rank") if tick else None,
        "6_high_update_persistence_effective": abs(_num(hup.get("separation_score") if hup else 0)) >= 0.12,
        "6_high_update_persistence_rank": hup.get("rank") if hup else None,
        "7_board_consumption_effective": abs(_num(board.get("separation_score") if board else 0)) >= 0.12,
        "7_board_consumption_rank": board.get("rank") if board else None,
        "8_618_6976_blocked": cf_targets.get("6976.T", {}).get("guard_would_block", False),
        "8_618_6387_blocked": cf_targets.get("6387.T", {}).get("guard_would_block", False),
        "8_618_6779_blocked": cf_targets.get("6779.T", {}).get("guard_would_block", False),
        "8_618_blocked_symbols": list(blocked_targets.keys()),
        "9_winner_over_cut_risk": win_block > 5,
        "9_best_guard_normal_winner_blocked": win_block,
        "9_best_guard_big_winner_lost": best_guard.get("lost_big_winner"),
        "10_shadow_candidates": [g.get("guard_id") for g in shadow[:5]],
        "11_runtime_candidates": [g.get("guard_id") for g in runtime[:3]],
        "12_next_phase": "phase555_stop_low_mfe_guard_shadow_replay",
        "top5_features": [r.get("feature") for r in ranking[:5]],
        "best_guard": {
            "guard_id": best_guard.get("guard_id"),
            "feature": best_guard.get("feature"),
            "threshold": best_guard.get("threshold"),
            "net_improvement_yen_100": best_guard.get("net_improvement_yen_100"),
        },
    }


@dataclass
class Phase554Job:
    repo_root: Path
    live_start: str = PERIOD_START_LIVE
    live_end: str = LIVE_END_DEFAULT
    extended_start: str = PERIOD_EXTENDED_START
    include_cap_extension: bool = True

    def run(self) -> dict[str, Any]:
        repo = self.repo_root.resolve()
        kabu = resolve_kabu_root(repo)
        end = min(self.live_end, _latest_live_day(repo))

        accepted = _load_b_runtime_accepted(repo, live_start=self.live_start, end=end)
        days = sorted({str(t.get("day") or "")[:8] for t in accepted})
        symbols = sorted({str(t.get("symbol") or "").replace(".T", "") for t in accepted})
        price_idx = _build_price_index_to(kabu, period_end=end)
        bar_cache = _build_bar_cache_for_days(repo, days=days, symbols=symbols, price_idx=price_idx)
        micro = _build_micro_lookup(accepted)

        board_snaps_by_day: dict[str, dict[str, list[tuple[Any, float]]]] = {}
        for day in days:
            board_snaps_by_day[day] = _load_day_event_snaps(kabu, day)

        enriched = _enrich_phase554(
            accepted,
            bar_cache=bar_cache,
            micro_lookup=micro,
            board_snaps_by_day=board_snaps_by_day,
        )
        baseline_pnl = round(sum(_num(t.get("pnl_yen_100")) for t in enriched), 2)
        live_attr = _loss_attribution(enriched)

        cap_trades: list[dict[str, Any]] = []
        if self.include_cap_extension:
            cap_end = (datetime.strptime(self.live_start, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
            cap = _cap_extension_metrics(
                repo,
                period_start=self.extended_start,
                period_end=cap_end,
                include_or=True,
            )
            for t in cap.get("_trades") or []:
                cap_trades.append({**dict(t), "data_source": "cap_extension"})
            cap_enriched = _enrich_phase554(
                cap_trades,
                bar_cache=bar_cache,
                micro_lookup=micro,
                board_snaps_by_day=board_snaps_by_day,
            )
            full_enriched = cap_enriched + enriched
        else:
            full_enriched = enriched

        full_attr = _loss_attribution(full_enriched)

        separation = _feature_separation_rows(enriched)
        ranking = _feature_ranking_rows(enriched)
        guards = _guard_candidate_rows(enriched, baseline_pnl=baseline_pnl)

        day_trades = [dict(t) for t in enriched if str(t.get("day") or "")[:8] == TARGET_DAY]
        day_trades.sort(
            key=lambda t: _parse_ts(str(t.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST)
        )
        for i, t in enumerate(day_trades):
            t["trade_id"] = f"D{i+1:02d}"
        counterfactual = _counterfactual_618(day_trades, guards)
        answers = _mandatory_answers(
            live_attr=live_attr,
            full_attr=full_attr,
            ranking=ranking,
            guards=guards,
            counterfactual=counterfactual,
        )

        cohort_counts = {c: sum(1 for t in enriched if _cohort_flags(t)[c]) for c in COHORTS}

        return {
            "verdict": PHASE554_VERDICT,
            "generated_at": _now_iso(),
            "period_live": f"{self.live_start}-{end}",
            "period_full": f"{self.extended_start}-{end}" if self.include_cap_extension else f"{self.live_start}-{end}",
            "trade_count_live": len(enriched),
            "trade_count_full": len(full_enriched),
            "baseline_pnl_yen_100": baseline_pnl,
            "cohort_counts": cohort_counts,
            "live_loss_attribution": live_attr,
            "full_loss_attribution": full_attr,
            "feature_separation": separation,
            "feature_ranking": ranking,
            "guard_candidates": guards,
            "counterfactual_618": counterfactual,
            "mandatory_answers": answers,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        paths = {
            "separation": reports / "phase554_feature_separation.csv",
            "ranking": reports / "phase554_feature_ranking.csv",
            "guards": reports / "phase554_guard_candidates.csv",
            "counterfactual": reports / "phase554_20260618_counterfactual.csv",
            "report": reports / "phase554_report.json",
            "docs": kabu / "docs" / "operations" / "phase554_stop_low_mfe_entry_quality_feature_study.md",
        }
        _write_csv(paths["separation"], SEPARATION_FIELDS, list(result.get("feature_separation") or []))
        _write_csv(paths["ranking"], RANKING_FIELDS, list(result.get("feature_ranking") or []))
        _write_csv(paths["guards"], GUARD_FIELDS, list(result.get("guard_candidates") or []))
        _write_csv(paths["counterfactual"], COUNTERFACTUAL_FIELDS, list(result.get("counterfactual_618") or []))
        public = {k: v for k, v in result.items() if not k.startswith("feature_")}
        paths["report"].write_text(json.dumps(public, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        self._write_docs(paths["docs"], result)
        return paths

    def _write_docs(self, path: Path, result: Mapping[str, Any]) -> None:
        ans = result.get("mandatory_answers") or {}
        live = result.get("live_loss_attribution") or {}
        full = result.get("full_loss_attribution") or {}
        cohort = result.get("cohort_counts") or {}
        lines = [
            "# Phase554 — stop_low_mfe Entry Quality Feature Study",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            f"**Live period:** {result.get('period_live')}",
            f"**Full period:** {result.get('period_full')}",
            f"**Live trades:** {result.get('trade_count_live')} | **Baseline PnL:** {result.get('baseline_pnl_yen_100')}",
            "",
            "## Cohorts",
            "",
            f"- stop_low_mfe (stop_hit + MFE<{STOP_LOW_MFE_MFE_PCT}%): {cohort.get('stop_low_mfe')}",
            f"- normal_winner (pnl>0, MFE>={NORMAL_WINNER_MFE_PCT}%): {cohort.get('normal_winner')}",
            f"- big_winner (MFE>={BIG_WINNER_MFE_PCT}%): {cohort.get('big_winner')}",
            f"- mfe0: {cohort.get('mfe0')}",
            "",
            "## Loss attribution",
            "",
            f"- Live stop_low_mfe share of loss: {live.get('stop_low_mfe_share_of_loss_pct')}%",
            f"- Full-period stop_low_mfe share of loss: {full.get('stop_low_mfe_share_of_loss_pct')}%",
            f"- Live MFE0 share of loss: {live.get('mfe0_share_of_loss_pct')}%",
            "",
            "## Mandatory answers",
            "",
        ]
        for k, v in sorted(ans.items()):
            lines.append(f"- **{k}:** {v}")
        lines.extend(
            [
                "",
                "## Output files",
                "",
                "- `results/reports/phase554_feature_separation.csv`",
                "- `results/reports/phase554_feature_ranking.csv`",
                "- `results/reports/phase554_guard_candidates.csv`",
                "- `results/reports/phase554_20260618_counterfactual.csv`",
                "- `results/reports/phase554_report.json`",
            ]
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
