"""
Phase499 — Post Entry Behavior Audit (research only).

Post-entry 30–180s feature analysis and exit-overlay counterfactuals on PBv2 accepted trades.
No Runtime / YAML / Entry / Exit / Order / Discord changes.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _float, _parse_ts, _position_key
from research.phase402_time_decay_exit_shadow import HARD_STOP_PCT, _max_drawdown_yen
from research.phase404_no_progress_exit_shadow import build_tick_states
from research.phase451_entry_shape_tournament import _build_price_index_to, _now_iso
from research.phase463_trend_pullback_population_tournament import _fill_close_proxy_shadows
from research.phase465b_trend_gate_redesign import _cohens_d, _mi_median_split
from research.phase473_trend_entry_architecture import _entry_block, pass_pbv2
from research.phase476_pre_breakout_gate_replay import _ensure_enriched, _load_replay_pool
from research.phase483_stop_low_mfe_root_cause_audit import _ks_stat
from research.phase484_stop_low_mfe_feature_discovery import _imb_at_or_before, _load_day_event_snaps
from research.phase488_current_runtime_replay import (
    REPLAY_MODE,
    _filter_period,
    _filter_replay_pool_safe,
    _simulate_runtime_replay,
)
from research.phase493_global_entry_failure_audit import (
    DAY_622,
    PERIOD_END,
    PERIOD_START,
    _enrich_trade_row,
    _exit_reason,
)
from replay.pnl_yen import compute_pnl_yen_100
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

HORIZONS = (30, 60, 120, 180)
E6_BOARD_X = 0.03
E7_VWAP_X = 0.2

BEHAVIOR_FIELDS = [
    "position_key", "symbol", "day", "cohort", "exit_reason", "pnl_yen_100",
    "baseline_hold_sec", "mfe_pct", "mae_pct",
    "E1_30s_pnl_lt_neg02", "E2_60s_mfe_lt_01", "E3_120s_stall", "E4_120s_no_reclaim",
    "E5_60s_new_low", "E6_board_drop_60s", "E7_vwap_drop_60s",
    *[
        f
        for h in HORIZONS
        for f in (
            f"pnl_pct_at_{h}s",
            f"mfe_pct_at_{h}s",
            f"mae_pct_at_{h}s",
            f"price_change_at_{h}s",
            f"board_imbalance_change_at_{h}s",
            f"vwap_dev_change_at_{h}s",
            f"high_update_after_entry_count_{h}s",
            f"new_low_after_entry_flag_{h}s",
            f"reclaim_entry_price_flag_{h}s",
            f"failed_reclaim_flag_{h}s",
        )
    ],
]

RANKING_FIELDS = [
    "rank", "feature_id", "w_mean", "w_median", "l_mean", "l_median",
    "missing_rate_w", "missing_rate_l", "cohens_d", "ks_statistic", "mutual_information",
    "feature_direction", "loo_min_abs_d", "loo_stable_days_pct", "loo_robust",
]

OVERLAY_FIELDS = [
    "scenario", "total_pnl_yen_100", "profit_factor", "maxDD_yen_100", "delta_maxDD_yen_100",
    "delta_pnl_yen_100", "baseline_pnl_yen_100", "baseline_PF", "baseline_maxDD_yen_100",
    "early_exit_count", "cut_winners", "saved_losers", "stop_hit_reduction", "no_progress_reduction",
    "avg_hold_sec_baseline", "avg_hold_sec_overlay", "impact_6976", "impact_4062", "impact_6522",
    "impact_20260622", "impact_AM", "impact_PM",
]

ROBUSTNESS_FIELDS = [
    "test", "scenario", "delta_pnl_vs_baseline", "profit_factor", "cut_winners",
]


def _pnl_pct(entry_px: float, px: float) -> float:
    if entry_px <= 0:
        return 0.0
    return round((px - entry_px) / entry_px * 100.0, 6)


def _is_winner(row: Mapping[str, Any]) -> bool:
    pnl = float(row.get("pnl_yen_100") or row.get("pnl_yen") or 0)
    reason = _exit_reason(row)
    if reason == "trailing_mfe_exit":
        return True
    return pnl > 0


def _is_loser(row: Mapping[str, Any]) -> bool:
    pnl = float(row.get("pnl_yen_100") or row.get("pnl_yen") or 0)
    reason = _exit_reason(row)
    if reason in ("stop_hit", "no_progress_exit"):
        return True
    return pnl < 0


def _cohort(row: Mapping[str, Any]) -> str:
    if _is_loser(row):
        return "loser"
    if _is_winner(row):
        return "winner"
    return "other"


def _series_epoch(
    price_idx: Mapping[tuple[str, str], list[tuple[datetime, float]]],
    symbol: str,
    day: str,
) -> list[tuple[float, float]]:
    sym = symbol if symbol.endswith(".T") else f"{symbol}.T"
    raw = price_idx.get((sym, day), [])
    return [(ts.timestamp(), px) for ts, px in raw if px > 0]


def _imb_at(
    snaps: Sequence[tuple[Any, float]],
    ts: datetime,
) -> Optional[float]:
    return _imb_at_or_before(snaps, ts)


def _metrics_until(
    series: Sequence[tuple[float, float]],
    *,
    entry_ts: float,
    entry_px: float,
    until_ts: float,
    entry_imb: Optional[float],
    imb_snaps: Sequence[tuple[Any, float]],
    entry_vwap_dev: Optional[float],
) -> dict[str, Any]:
    mfe = 0.0
    mae = 0.0
    peak_px = entry_px
    trough_px = entry_px
    below = False
    reclaimed = False
    high_updates = 0
    session_high: Optional[float] = None
    px_at_t: Optional[float] = None

    for ts, px in series:
        if ts < entry_ts:
            continue
        if ts > until_ts:
            break
        px_at_t = px
        pnl = _pnl_pct(entry_px, px)
        mfe = max(mfe, pnl)
        mae = min(mae, pnl)
        if px > peak_px:
            peak_px = px
        if px < trough_px:
            trough_px = px
        if px < entry_px:
            below = True
        if below and px >= entry_px:
            reclaimed = True
        if session_high is None:
            session_high = px
        elif px > session_high:
            high_updates += 1
            session_high = px

    if px_at_t is None:
        return {}

    dt = datetime.fromtimestamp(until_ts, tz=JST)
    imb_t = _imb_at(imb_snaps, dt)
    board_chg = round(imb_t - entry_imb, 6) if imb_t is not None and entry_imb is not None else None
    pnl_t = _pnl_pct(entry_px, px_at_t)
    vwap_chg = round(pnl_t - (entry_vwap_dev or 0), 6) if entry_vwap_dev is not None else None

    return {
        "pnl_pct": pnl_t,
        "mfe_pct": round(mfe, 6),
        "mae_pct": round(mae, 6),
        "price_change": pnl_t,
        "board_imbalance_change": board_chg,
        "vwap_dev_change": vwap_chg,
        "high_update_count": high_updates,
        "new_low_flag": 1.0 if trough_px < entry_px * 0.999 else 0.0,
        "reclaim_flag": 1.0 if reclaimed else 0.0,
        "failed_reclaim_flag": 1.0 if below and not reclaimed else 0.0,
    }


def _enrich_post_entry(
    log: Mapping[str, Any],
    *,
    price_idx: Mapping[tuple[str, str], list[tuple[datetime, float]]],
    day_snaps: Mapping[str, Mapping[str, list]],
) -> Optional[dict[str, Any]]:
    base = _enrich_trade_row(log)
    tr = dict(base.get("_trade") or {})
    sym = str(base["symbol"])
    day = str(base["day"])[:8]
    entry_px = _float(tr.get("entry_price")) or _float(base.get("entry_price"))
    ent_dt = _parse_ts(str(tr.get("entry_time") or base.get("entry_time") or ""))
    ex_dt = _parse_ts(str(log.get("exit_time") or tr.get("exit_time") or ""))
    if entry_px is None or entry_px <= 0 or ent_dt is None:
        return None

    ent_ts = ent_dt.timestamp()
    ex_ts = ex_dt.timestamp() if ex_dt else ent_ts + 600.0
    series = _series_epoch(price_idx, sym, day)
    if not series:
        return None

    entry_imb = _float(tr.get("board_imbalance") or tr.get("entry_order_book_imbalance"))
    entry_vwap_dev = _float(tr.get("entry_vwap_dev_pct") or tr.get("vwap_dev_pct"))
    imb_snaps = day_snaps.get(day, {}).get(sym if sym.endswith(".T") else f"{sym}.T", [])

    rec: dict[str, Any] = {
        "position_key": base["position_key"],
        "symbol": sym,
        "day": day,
        "cohort": _cohort(base),
        "exit_reason": base.get("exit_reason"),
        "pnl_yen_100": base.get("pnl_yen"),
        "baseline_hold_sec": round(max(0.0, ex_ts - ent_ts), 1),
        "mfe_pct": base.get("mfe_pct"),
        "mae_pct": base.get("mae_pct"),
        "entry_ts": ent_ts,
        "exit_ts": ex_ts,
        "entry_px": entry_px,
        "series": series,
        "entry_imb": entry_imb,
        "entry_vwap_dev": entry_vwap_dev,
    }

    horizon_metrics: dict[int, dict[str, Any]] = {}
    for h in HORIZONS:
        m = _metrics_until(
            series,
            entry_ts=ent_ts,
            entry_px=entry_px,
            until_ts=ent_ts + h,
            entry_imb=entry_imb,
            imb_snaps=imb_snaps,
            entry_vwap_dev=entry_vwap_dev,
        )
        horizon_metrics[h] = m
        for k, v in m.items():
            suffix = {
                "pnl_pct": f"pnl_pct_at_{h}s",
                "mfe_pct": f"mfe_pct_at_{h}s",
                "mae_pct": f"mae_pct_at_{h}s",
                "price_change": f"price_change_at_{h}s",
                "board_imbalance_change": f"board_imbalance_change_at_{h}s",
                "vwap_dev_change": f"vwap_dev_change_at_{h}s",
                "high_update_count": f"high_update_after_entry_count_{h}s",
                "new_low_flag": f"new_low_after_entry_flag_{h}s",
                "reclaim_flag": f"reclaim_entry_price_flag_{h}s",
                "failed_reclaim_flag": f"failed_reclaim_flag_{h}s",
            }.get(k)
            if suffix:
                rec[suffix] = v

    m30 = horizon_metrics.get(30, {})
    m60 = horizon_metrics.get(60, {})
    m120 = horizon_metrics.get(120, {})
    rec["E1_30s_pnl_lt_neg02"] = bool(m30.get("pnl_pct") is not None and float(m30["pnl_pct"]) < -0.2)
    rec["E2_60s_mfe_lt_01"] = bool(m60.get("mfe_pct") is not None and float(m60["mfe_pct"]) < 0.1)
    rec["E3_120s_stall"] = bool(
        m120.get("mfe_pct") is not None
        and m120.get("pnl_pct") is not None
        and float(m120["mfe_pct"]) < 0.2
        and float(m120["pnl_pct"]) < 0
    )
    rec["E4_120s_no_reclaim"] = bool(m120.get("failed_reclaim_flag") == 1.0)
    rec["E5_60s_new_low"] = bool(m60.get("new_low_flag") == 1.0)
    rec["E6_board_drop_60s"] = bool(
        m60.get("board_imbalance_change") is not None and float(m60["board_imbalance_change"]) < -E6_BOARD_X
    )
    rec["E7_vwap_drop_60s"] = bool(
        m60.get("vwap_dev_change") is not None and float(m60["vwap_dev_change"]) < -E7_VWAP_X
    )
    return rec


def _rankable_features() -> list[str]:
    feats: list[str] = []
    for h in HORIZONS:
        feats.extend(
            [
                f"pnl_pct_at_{h}s",
                f"mfe_pct_at_{h}s",
                f"mae_pct_at_{h}s",
                f"price_change_at_{h}s",
                f"board_imbalance_change_at_{h}s",
                f"vwap_dev_change_at_{h}s",
                f"high_update_after_entry_count_{h}s",
                f"new_low_after_entry_flag_{h}s",
                f"reclaim_entry_price_flag_{h}s",
                f"failed_reclaim_flag_{h}s",
            ]
        )
    feats.extend(
        [
            "E1_30s_pnl_lt_neg02", "E2_60s_mfe_lt_01", "E3_120s_stall", "E4_120s_no_reclaim",
            "E5_60s_new_low", "E6_board_drop_60s", "E7_vwap_drop_60s",
        ]
    )
    return feats


def _rank_features(rows: Sequence[Mapping[str, Any]], *, days: Sequence[str]) -> list[dict[str, Any]]:
    w_rows = [r for r in rows if r.get("cohort") == "winner"]
    l_rows = [r for r in rows if r.get("cohort") == "loser"]
    feats = _rankable_features()
    ranking: list[dict[str, Any]] = []

    for feat in feats:
        if feat.startswith("E"):
            wv = [1.0 if r.get(feat) else 0.0 for r in w_rows]
            lv = [1.0 if r.get(feat) else 0.0 for r in l_rows]
        else:
            wv = [float(r[feat]) for r in w_rows if r.get(feat) is not None]
            lv = [float(r[feat]) for r in l_rows if r.get(feat) is not None]
        if not wv and not lv:
            continue
        wm = statistics.mean(wv) if wv else None
        lm = statistics.mean(lv) if lv else None
        d = _cohens_d(lv, wv)
        ks = _ks_stat(lv, wv)
        mi = _mi_median_split(wv, lv) if wv and lv and not feat.startswith("E") else None

        loo_ds: list[float] = []
        stable = 0
        for day in days:
            sw = [float(r[feat]) for r in w_rows if r.get("day") != day and r.get(feat) is not None] if not feat.startswith("E") else [
                1.0 if r.get(feat) else 0.0 for r in w_rows if r.get("day") != day
            ]
            sl = [float(r[feat]) for r in l_rows if r.get("day") != day and r.get(feat) is not None] if not feat.startswith("E") else [
                1.0 if r.get(feat) else 0.0 for r in l_rows if r.get("day") != day
            ]
            if len(sw) < 2 or len(sl) < 2:
                continue
            ld = abs(float(_cohens_d(sl, sw) or 0))
            loo_ds.append(ld)
            if ld >= 0.12:
                stable += 1
        n_loo = len(loo_ds) or 1

        ranking.append(
            {
                "feature_id": feat,
                "w_mean": round(wm, 6) if wm is not None else None,
                "w_median": round(statistics.median(wv), 6) if wv else None,
                "l_mean": round(lm, 6) if lm is not None else None,
                "l_median": round(statistics.median(lv), 6) if lv else None,
                "missing_rate_w": round(sum(1 for r in w_rows if r.get(feat) is None and not feat.startswith("E")) / max(1, len(w_rows)), 4),
                "missing_rate_l": round(sum(1 for r in l_rows if r.get(feat) is None and not feat.startswith("E")) / max(1, len(l_rows)), 4),
                "cohens_d": d,
                "ks_statistic": ks,
                "mutual_information": mi,
                "feature_direction": "higher_in_loser" if lm is not None and wm is not None and lm > wm else (
                    "lower_in_loser" if lm is not None and wm is not None and lm < wm else "unknown"
                ),
                "loo_min_abs_d": round(min(loo_ds), 6) if loo_ds else 0.0,
                "loo_stable_days_pct": round(stable / n_loo, 4),
                "loo_robust": (min(loo_ds) if loo_ds else 0) >= 0.12 and abs(float(d or 0)) >= 0.20,
            }
        )

    ranking.sort(key=lambda r: abs(float(r.get("cohens_d") or 0)), reverse=True)
    for i, row in enumerate(ranking, start=1):
        row["rank"] = i
    return ranking


def _hard_stop_px(entry_px: float) -> float:
    return entry_px * (1.0 - HARD_STOP_PCT / 100.0)


def _simulate_overlay(
    rec: Mapping[str, Any],
    *,
    check_fn: Callable[[Mapping[str, Any], float, float, float], bool],
    exit_at_sec: Optional[float] = None,
) -> dict[str, Any]:
    entry_px = float(rec["entry_px"])
    entry_ts = float(rec["entry_ts"])
    exit_ts = float(rec["exit_ts"])
    series = rec["series"]
    entry_vwap = rec.get("entry_vwap_dev")
    session_end = max(ts for ts, _ in series) if series else exit_ts

    states = build_tick_states(
        series,
        entry_ts=entry_ts,
        entry_price=entry_px,
        session_end_ts=session_end,
        entry_vwap_dev_pct=_float(entry_vwap),
    )
    baseline_pnl = float(rec.get("pnl_yen_100") or 0)
    baseline_reason = str(rec.get("exit_reason") or "")
    hard_px = _hard_stop_px(entry_px)

    below_entry = False
    reclaimed = False
    triggered = False
    overlay_pnl = baseline_pnl
    overlay_hold = float(rec.get("baseline_hold_sec") or 0)
    overlay_reason = baseline_reason

    for st in states:
        ts = float(st["ts"])
        if ts > exit_ts:
            break
        px = float(st["px"])
        elapsed = float(st["elapsed"])
        pnl = float(st["pnl"])

        if px <= hard_px:
            overlay_pnl = round(compute_pnl_yen_100(entry_px, px), 2)
            overlay_hold = elapsed
            overlay_reason = "stop_hit"
            triggered = baseline_reason != "stop_hit" or overlay_pnl != baseline_pnl
            break

        if px < entry_px:
            below_entry = True
        if below_entry and px >= entry_px:
            reclaimed = True

        ctx = {
            "elapsed": elapsed,
            "pnl": pnl,
            "peak_mfe": float(st["peak_mfe"]),
            "below_entry": below_entry,
            "reclaimed": reclaimed,
            "board_chg": _float(rec.get("board_imbalance_change_at_60s")),
            "vwap_chg": _float(rec.get("vwap_dev_change_at_60s")),
        }
        if exit_at_sec is not None and elapsed < exit_at_sec:
            continue
        if check_fn(ctx, entry_ts, entry_px, ts):
            overlay_pnl = round(compute_pnl_yen_100(entry_px, px), 2)
            overlay_hold = elapsed
            overlay_reason = "early_overlay"
            triggered = True
            break

    return {
        "overlay_pnl": overlay_pnl,
        "overlay_hold": overlay_hold,
        "overlay_reason": overlay_reason,
        "triggered": triggered,
        "baseline_pnl": baseline_pnl,
        "baseline_reason": baseline_reason,
        "is_winner": rec.get("cohort") == "winner",
        "is_loser": rec.get("cohort") == "loser",
        "symbol": rec.get("symbol"),
        "day": rec.get("day"),
        "session_bucket": _session_bucket_from_rec(rec),
    }


def _session_bucket_from_rec(rec: Mapping[str, Any]) -> str:
    # infer from entry_ts hour
    from research.phase495_new_feature_guard_replay import _session_bucket
    return _session_bucket(datetime.fromtimestamp(float(rec["entry_ts"]), tz=JST))


def _overlay_check_e1(ctx: Mapping[str, Any], *_: Any) -> bool:
    return float(ctx["elapsed"]) <= 30 and float(ctx["pnl"]) < -0.2


def _overlay_check_e2(ctx: Mapping[str, Any], *_: Any) -> bool:
    return float(ctx["elapsed"]) >= 60 and float(ctx["peak_mfe"]) < 0.1


def _overlay_check_e3(ctx: Mapping[str, Any], *_: Any) -> bool:
    return float(ctx["elapsed"]) >= 120 and float(ctx["peak_mfe"]) < 0.2 and float(ctx["pnl"]) < 0


def _overlay_check_e4(ctx: Mapping[str, Any], *_: Any) -> bool:
    return float(ctx["elapsed"]) >= 120 and bool(ctx["below_entry"]) and not bool(ctx["reclaimed"])


def _overlay_check_e5(ctx: Mapping[str, Any], entry_ts: float, entry_px: float, ts: float) -> bool:
    return float(ctx["elapsed"]) <= 60 and float(ctx["pnl"]) < -0.05


def _overlay_check_e6(ctx: Mapping[str, Any], *_: Any) -> bool:
    return float(ctx["elapsed"]) >= 60 and _float(ctx.get("board_chg")) is not None and float(ctx["board_chg"]) < -E6_BOARD_X


def _overlay_check_e7(ctx: Mapping[str, Any], *_: Any) -> bool:
    return float(ctx["elapsed"]) >= 60 and _float(ctx.get("vwap_chg")) is not None and float(ctx["vwap_chg"]) < -E7_VWAP_X


def _aggregate_overlay(
    results: Sequence[Mapping[str, Any]],
    *,
    scenario: str,
    baseline_pnls: Sequence[float],
    baseline_max_dd: float,
) -> dict[str, Any]:
    pnls = [float(r["overlay_pnl"]) for r in results]
    base_total = sum(baseline_pnls)
    total = sum(pnls)
    delta = total - base_total
    early = sum(1 for r in results if r.get("triggered"))
    cut_w = sum(
        1 for r in results
        if r.get("is_winner") and r.get("triggered") and float(r["overlay_pnl"]) < float(r["baseline_pnl"]) - 1
    )
    saved_l = sum(
        1 for r in results
        if r.get("is_loser") and float(r["overlay_pnl"]) > float(r["baseline_pnl"]) + 1
    )
    stop_b = sum(1 for r in results if r.get("baseline_reason") == "stop_hit")
    stop_o = sum(1 for r in results if r.get("overlay_reason") == "stop_hit")
    np_b = sum(1 for r in results if r.get("baseline_reason") == "no_progress_exit")
    np_o = sum(1 for r in results if "no_progress" in str(r.get("overlay_reason") or ""))

    def _sym_delta(sym: str) -> float:
        return sum(float(r["overlay_pnl"]) - float(r["baseline_pnl"]) for r in results if str(r.get("symbol")) == sym)

    am_block = sum(
        float(r["overlay_pnl"]) - float(r["baseline_pnl"])
        for r in results
        if r.get("session_bucket") == "AM" and r.get("triggered")
    )
    pm_block = sum(
        float(r["overlay_pnl"]) - float(r["baseline_pnl"])
        for r in results
        if r.get("session_bucket") == "PM" and r.get("triggered")
    )
    holds_b = [float(r.get("baseline_hold_sec") or 0) for r in results if "baseline_hold_sec" in r]
    holds_o = [float(r.get("overlay_hold") or 0) for r in results]

    max_dd = _max_drawdown_yen(pnls)
    return {
        "scenario": scenario,
        "total_pnl_yen_100": round(total, 2),
        "profit_factor": _pf(pnls),
        "maxDD_yen_100": round(max_dd, 2),
        "delta_maxDD_yen_100": round(max_dd - baseline_max_dd, 2),
        "delta_pnl_yen_100": round(delta, 2),
        "baseline_pnl_yen_100": round(base_total, 2),
        "baseline_PF": _pf(list(baseline_pnls)),
        "baseline_maxDD_yen_100": round(baseline_max_dd, 2),
        "early_exit_count": early,
        "cut_winners": cut_w,
        "saved_losers": saved_l,
        "stop_hit_reduction": stop_b - stop_o,
        "no_progress_reduction": np_b - np_o,
        "avg_hold_sec_baseline": round(statistics.mean(holds_b), 1) if holds_b else 0,
        "avg_hold_sec_overlay": round(statistics.mean(holds_o), 1) if holds_o else 0,
        "impact_6976": round(_sym_delta("6976"), 2),
        "impact_4062": round(_sym_delta("4062"), 2),
        "impact_6522": round(_sym_delta("6522"), 2),
        "impact_20260622": round(
            sum(float(r["overlay_pnl"]) - float(r["baseline_pnl"]) for r in results if r.get("day") == DAY_622),
            2,
        ),
        "impact_AM": round(am_block, 2),
        "impact_PM": round(pm_block, 2),
    }


def _verdict(
    *,
    best: Mapping[str, Any],
    top_feat: Mapping[str, Any],
    overfit: bool,
    overlay_positive: bool,
) -> str:
    delta = float(best.get("delta_pnl_yen_100") or 0)
    cut_w = int(best.get("cut_winners") or 0)
    saved = int(best.get("saved_losers") or 0)
    feat_d = abs(float(top_feat.get("cohens_d") or 0))
    if overfit and not overlay_positive:
        return "overfit_post_entry"
    if overlay_positive and delta >= 15000 and cut_w <= 10 and saved >= 8:
        return "post_entry_exit_candidate"
    if overlay_positive and delta >= 5000 and saved >= 5:
        return "post_entry_exit_candidate" if cut_w <= 12 else "post_entry_feature_found"
    if feat_d >= 0.35:
        return "post_entry_feature_found"
    if delta < 2000 and not overlay_positive:
        return "no_post_entry_edge" if feat_d < 0.25 else "post_entry_feature_found"
    return "no_post_entry_edge"


def run_phase499(*, repo_root: Path, parallel: bool = False, max_workers: int = 2) -> dict[str, Any]:
    max_workers = min(max(2, max_workers), 4)
    kabu = resolve_kabu_root(repo_root)
    reports = resolve_reports_dir(repo_root)
    price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)

    replay_pool, runtime_shadows = _load_replay_pool(reports)
    replay_pool = _filter_period(replay_pool, start=PERIOD_START, end=PERIOD_END)
    runtime_shadows = _fill_close_proxy_shadows(replay_pool, runtime_shadows, price_idx=price_idx)
    replay_pool = _filter_replay_pool_safe(replay_pool, runtime_shadows)
    _ensure_enriched(replay_pool, price_idx=price_idx)

    baseline_state = _simulate_runtime_replay(
        replay_pool,
        runtime_shadows,
        mode=f"{REPLAY_MODE}_phase499",
        entry_block_fn=_entry_block(pass_pbv2),
        initial_equity=1_500_000.0,
    )

    days_needed = sorted({str(log.get("day") or "")[:8] for log in baseline_state.trade_log})
    day_snaps: dict[str, dict[str, list]] = {}
    for day in days_needed:
        snaps = _load_day_event_snaps(kabu, day)
        day_snaps[day] = snaps

    rows: list[dict[str, Any]] = []
    for log in baseline_state.trade_log:
        rec = _enrich_post_entry(log, price_idx=price_idx, day_snaps=day_snaps)
        if rec:
            rows.append(rec)

    days = sorted({str(r["day"]) for r in rows})
    ranking = _rank_features(rows, days=days)
    top_feat = ranking[0] if ranking else {}

    baseline_pnls = [float(r.get("pnl_yen_100") or 0) for r in rows]
    baseline_max_dd = _max_drawdown_yen(baseline_pnls)

    overlay_specs: list[tuple[str, Callable[..., bool], Optional[float]]] = [
        ("A_baseline", lambda *_: False, None),
        ("B_E1_30s_pnl_lt_neg02", _overlay_check_e1, None),
        ("C_E2_60s_mfe_lt_01", _overlay_check_e2, None),
        ("D_E3_120s_stall", _overlay_check_e3, None),
        ("E_E4_120s_no_reclaim", _overlay_check_e4, None),
        ("F_E5_60s_new_low", _overlay_check_e5, None),
        ("G1_E6_board_drop_60s", _overlay_check_e6, None),
        ("G2_E7_vwap_drop_60s", _overlay_check_e7, None),
    ]

    def _run_overlay(name: str, fn: Callable[..., bool], exit_at: Optional[float]) -> dict[str, Any]:
        if name == "A_baseline":
            return _aggregate_overlay(
                [
                    {
                        "overlay_pnl": float(r.get("pnl_yen_100") or 0),
                        "overlay_hold": r.get("baseline_hold_sec"),
                        "overlay_reason": r.get("exit_reason"),
                        "triggered": False,
                        "baseline_pnl": float(r.get("pnl_yen_100") or 0),
                        "baseline_reason": r.get("exit_reason"),
                        "is_winner": r.get("cohort") == "winner",
                        "is_loser": r.get("cohort") == "loser",
                        "symbol": r.get("symbol"),
                        "day": r.get("day"),
                        "session_bucket": _session_bucket_from_rec(r),
                        "baseline_hold_sec": r.get("baseline_hold_sec"),
                    }
                    for r in rows
                ],
                scenario=name,
                baseline_pnls=baseline_pnls,
                baseline_max_dd=baseline_max_dd,
            )
        results = [_simulate_overlay(r, check_fn=fn, exit_at_sec=exit_at) for r in rows]
        for r, res in zip(rows, results):
            res["baseline_hold_sec"] = r.get("baseline_hold_sec")
        return _aggregate_overlay(results, scenario=name, baseline_pnls=baseline_pnls, baseline_max_dd=baseline_max_dd)

    cf_rows: list[dict[str, Any]] = []
    if parallel and len(overlay_specs) > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = {ex.submit(_run_overlay, n, f, e): n for n, f, e in overlay_specs}
            for fut in as_completed(futs):
                cf_rows.append(fut.result())
    else:
        for name, fn, exit_at in overlay_specs:
            cf_rows.append(_run_overlay(name, fn, exit_at))

    non_base = [r for r in cf_rows if r.get("scenario") != "A_baseline"]
    non_base.sort(key=lambda r: float(r.get("delta_pnl_yen_100") or 0), reverse=True)
    best_overlay = non_base[0] if non_base and float(non_base[0].get("delta_pnl_yen_100") or 0) > 0 else None
    baseline_row = next((r for r in cf_rows if r.get("scenario") == "A_baseline"), {})
    best_cf = best_overlay if best_overlay else baseline_row

    # G conservative: combine top 2 overlays by delta (exclude baseline)
    top2 = non_base[:2]
    if best_overlay and len(top2) == 2:
        n1, n2 = top2[0]["scenario"], top2[1]["scenario"]
        fn_map = {n: f for n, f, _ in overlay_specs if n not in ("A_baseline",)}
        f1 = fn_map.get(n1, _overlay_check_e2)
        f2 = fn_map.get(n2, _overlay_check_e1)

        def _combo(ctx, *a):
            return f1(ctx, *a) or f2(ctx, *a)

        g_res = [_simulate_overlay(r, check_fn=_combo, exit_at_sec=None) for r in rows]
        for r, res in zip(rows, g_res):
            res["baseline_hold_sec"] = r.get("baseline_hold_sec")
        g_row = _aggregate_overlay(
            g_res,
            scenario=f"G_conservative_{n1}_{n2}",
            baseline_pnls=baseline_pnls,
            baseline_max_dd=baseline_max_dd,
        )
        cf_rows.append(g_row)
        cf_rows.sort(key=lambda r: float(r.get("delta_pnl_yen_100") or 0), reverse=True)
        if float(g_row.get("delta_pnl_yen_100") or 0) > float(best_cf.get("delta_pnl_yen_100") or 0):
            best_cf = g_row
            best_overlay = g_row

    # Pattern rates
    w_rows = [r for r in rows if r.get("cohort") == "winner"]
    l_rows = [r for r in rows if r.get("cohort") == "loser"]

    def _rate(key: str, grp: Sequence[Mapping[str, Any]]) -> float:
        if not grp:
            return 0.0
        return round(sum(1 for r in grp if r.get(key)) / len(grp), 4)

    day622_pnl = sum(float(r.get("pnl_yen_100") or 0) for r in rows if r.get("day") == DAY_622)
    day622_share = abs(day622_pnl / sum(baseline_pnls)) if baseline_pnls else 0.0

    robustness_rows: list[dict[str, Any]] = []
    best_name = str(best_cf.get("scenario") or "")

    def _rob_subset(test: str, subset: Sequence[Mapping[str, Any]]) -> None:
        if len(subset) < 20:
            return
        sub_pnls = [float(r.get("pnl_yen_100") or 0) for r in subset]
        base_total = sum(sub_pnls)
        fn_map = {n: f for n, f, _ in overlay_specs}
        if best_name == "A_baseline":
            return
        if best_name.startswith("G_conservative"):
            fns = [_overlay_check_e2, _overlay_check_e1]
            check = lambda ctx, *a: any(f(ctx, *a) for f in fns)
        else:
            check = fn_map.get(best_name, _overlay_check_e2)
        results = [_simulate_overlay(r, check_fn=check, exit_at_sec=None) for r in subset]
        total = sum(float(x["overlay_pnl"]) for x in results)
        cut_w = sum(1 for x in results if x.get("is_winner") and x.get("triggered"))
        robustness_rows.append(
            {
                "test": test,
                "scenario": best_name,
                "delta_pnl_vs_baseline": round(total - base_total, 2),
                "profit_factor": _pf([float(x["overlay_pnl"]) for x in results]),
                "cut_winners": cut_w,
            }
        )

    for day in days:
        _rob_subset(f"LOO_day_{day}", [r for r in rows if r.get("day") != day])
    _rob_subset("exclude_6976", [r for r in rows if str(r.get("symbol")) != "6976"])
    _rob_subset("exclude_6_22", [r for r in rows if r.get("day") != DAY_622])
    sym_counts = Counter(str(r["symbol"]) for r in rows)
    top_sym = sym_counts.most_common(1)[0][0] if sym_counts else ""
    _rob_subset("exclude_top_symbol", [r for r in rows if str(r.get("symbol")) != top_sym])
    _rob_subset("AM_only", [r for r in rows if r.get("cohort") and _session_bucket_from_rec(r) == "AM"])
    _rob_subset("PM_only", [r for r in rows if r.get("cohort") and _session_bucket_from_rec(r) == "PM"])

    loo_pos = sum(1 for r in robustness_rows if str(r.get("test", "")).startswith("LOO_day_") and float(r.get("delta_pnl_vs_baseline") or 0) > 0)
    loo_n = sum(1 for r in robustness_rows if str(r.get("test", "")).startswith("LOO_day_"))
    overfit = loo_n > 0 and loo_pos < loo_n * 0.5 and float(best_cf.get("delta_pnl_yen_100") or 0) > 0

    verdict = _verdict(
        best=best_cf,
        top_feat=top_feat,
        overfit=overfit,
        overlay_positive=best_overlay is not None,
    )

    chase_w = _pattern_medians_simple(w_rows)
    chase_l = _pattern_medians_simple(l_rows)

    pattern_keys = [
        ("E1_30s_pnl_lt_neg02", "E1"),
        ("E2_60s_mfe_lt_01", "E2"),
        ("E3_120s_stall", "E3"),
        ("E4_120s_no_reclaim", "E4"),
        ("E5_60s_new_low", "E5"),
    ]
    best_pat = "n/a"
    best_sep = -1.0
    for key, label in pattern_keys:
        sep = _rate(key, l_rows) - _rate(key, w_rows)
        if sep > best_sep:
            best_sep = sep
            best_pat = label

    mandatory = {
        "1_strongest_post_entry_feature": top_feat.get("feature_id"),
        "1_cohens_d": top_feat.get("cohens_d"),
        "2_loser_visible_at_30s": f"E1 rate W={_rate('E1_30s_pnl_lt_neg02', w_rows)} L={_rate('E1_30s_pnl_lt_neg02', l_rows)}",
        "3_loser_visible_at_60s": f"E2 rate W={_rate('E2_60s_mfe_lt_01', w_rows)} L={_rate('E2_60s_mfe_lt_01', l_rows)}",
        "4_loser_visible_at_120s": (
            f"E3 W={_rate('E3_120s_stall', w_rows)} L={_rate('E3_120s_stall', l_rows)}; "
            f"E4 W={_rate('E4_120s_no_reclaim', w_rows)} L={_rate('E4_120s_no_reclaim', l_rows)}"
        ),
        "5_best_early_failure_pattern": best_pat,
        "6_best_exit_overlay": best_cf.get("scenario"),
        "6_overlay_improves_pnl": best_overlay is not None,
        "6_best_overlay_delta_if_any": best_overlay.get("delta_pnl_yen_100") if best_overlay else 0,
        "6_worst_overlay": non_base[-1].get("scenario") if non_base else None,
        "7_delta_pnl": best_cf.get("delta_pnl_yen_100"),
        "8_pf_improvement": round(float(best_cf.get("profit_factor") or 0) - float(best_cf.get("baseline_PF") or 0), 4),
        "9_maxDD_change": best_cf.get("delta_maxDD_yen_100"),
        "10_early_exit_count": best_cf.get("early_exit_count"),
        "11_cut_winners": best_cf.get("cut_winners"),
        "12_saved_losers": best_cf.get("saved_losers"),
        "13_impact_6976": best_cf.get("impact_6976"),
        "14_day622_dependent": day622_share > 0.3 and abs(float(best_cf.get("impact_20260622") or 0)) > 5000,
        "15_hurts_am": float(best_cf.get("impact_AM") or 0) < -3000,
        "16_improves_pm": float(best_cf.get("impact_PM") or 0) > 0,
        "17_overfit_risk": "high" if overfit else "moderate" if int(best_cf.get("cut_winners") or 0) > 15 else "low",
        "18_runtime_candidate": verdict == "post_entry_exit_candidate" and int(best_cf.get("cut_winners") or 0) <= 5,
        "19_shadow_candidate": verdict in ("post_entry_exit_candidate", "post_entry_feature_found"),
        "20_next_action": (
            "Shadow-log 30/60/120s checkpoints (mfe/pnl/reclaim); no exit overlay until cut_winners < 10"
            if verdict == "post_entry_feature_found"
            else "Continue entry guards only; post-entry signal too weak for overlay"
        ),
        "verdict": verdict,
        "trade_count": len(rows),
        "winner_count": len(w_rows),
        "loser_count": len(l_rows),
        "winning_chase_traits": chase_w,
        "losing_chase_traits": chase_l,
        "missing_price_rate": round(1 - len(rows) / max(1, len(baseline_state.trade_log)), 4),
    }

    export_rows = [{k: r.get(k) for k in BEHAVIOR_FIELDS} for r in rows]

    return {
        "generated_at": _now_iso(),
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "verdict": verdict,
        "mandatory_answers": mandatory,
        "_behavior": export_rows,
        "_ranking": ranking,
        "_counterfactual": cf_rows,
        "_robustness": robustness_rows,
    }


def _pattern_medians_simple(grp: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not grp:
        return {"count": 0}
    out: dict[str, Any] = {"count": len(grp)}
    for f in ("pnl_pct_at_30s", "mfe_pct_at_60s", "mfe_pct_at_120s", "mae_pct_at_30s"):
        vals = [_float(r.get(f)) for r in grp]
        vals_n = [v for v in vals if v is not None]
        out[f"median_{f}"] = round(statistics.median(vals_n), 4) if vals_n else None
    for e in ("E1_30s_pnl_lt_neg02", "E2_60s_mfe_lt_01", "E3_120s_stall", "E4_120s_no_reclaim"):
        out[f"rate_{e}"] = round(sum(1 for r in grp if r.get(e)) / len(grp), 4)
    return out


@dataclass
class Phase499Job:
    repo_root: Path
    parallel: bool = False
    max_workers: int = 2

    def run(self) -> dict[str, Any]:
        return run_phase499(repo_root=self.repo_root, parallel=self.parallel, max_workers=self.max_workers)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        doc_root = self.repo_root / "kabu_native"
        if not (doc_root / "docs").is_dir():
            doc_root = self.repo_root
        paths = {
            "behavior": reports / "phase499_post_entry_behavior.csv",
            "ranking": reports / "phase499_post_entry_feature_ranking.csv",
            "counterfactual": reports / "phase499_post_entry_exit_overlay.csv",
            "robustness": reports / "phase499_post_entry_robustness.csv",
            "summary": reports / "phase499_summary.json",
            "report": doc_root / "docs" / "operations" / "phase499_post_entry_behavior_audit.md",
        }
        _write_csv(paths["behavior"], BEHAVIOR_FIELDS, list(result.get("_behavior") or []))
        _write_csv(paths["ranking"], RANKING_FIELDS, list(result.get("_ranking") or []))
        _write_csv(paths["counterfactual"], OVERLAY_FIELDS, list(result.get("_counterfactual") or []))
        _write_csv(paths["robustness"], ROBUSTNESS_FIELDS, list(result.get("_robustness") or []))
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        self._write_report(paths["report"], result)
        return paths

    def _write_report(self, report: Path, result: Mapping[str, Any]) -> None:
        m = result.get("mandatory_answers") or {}
        lines = ["# Phase499 — Post Entry Behavior Audit", "", f"**Verdict:** `{result.get('verdict')}`", "", "## 必須回答", ""]
        for k in range(1, 21):
            key = f"{k}_" + {
                1: "strongest_post_entry_feature", 2: "loser_visible_at_30s", 3: "loser_visible_at_60s",
                4: "loser_visible_at_120s", 5: "best_early_failure_pattern", 6: "best_exit_overlay",
                7: "delta_pnl", 8: "pf_improvement", 9: "maxDD_change", 10: "early_exit_count",
                11: "cut_winners", 12: "saved_losers", 13: "impact_6976", 14: "day622_dependent",
                15: "hurts_am", 16: "improves_pm", 17: "overfit_risk", 18: "runtime_candidate",
                19: "shadow_candidate", 20: "next_action",
            }[k]
            if k == 1:
                lines.append(f"- **{key}:** {m.get('1_strongest_post_entry_feature')} (d={m.get('1_cohens_d')})")
            elif key in m:
                lines.append(f"- **{key}:** {m.get(key)}")
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("\n".join(lines), encoding="utf-8")
