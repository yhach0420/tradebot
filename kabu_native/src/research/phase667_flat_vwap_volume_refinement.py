"""Phase667 — Flat VWAP / volume refinement (research only)."""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase382_capital_constrained_backtest import _parse_ts
from research.phase632_pbv2_profit_filter_counterfactual import _max_drawdown, _metrics
from research.phase634_pbv2_only_rise5_full_period import _disk_usage_pct
from research.phase663_price_age_freshness_analysis import CANONICAL_DAYS
from research.phase665_pretrend_shape_analysis import (
    _build_price_index_canonical,
    _vwap_dev_pct,
    _vwap_slope_pct_per_min,
    load_canonical_trades,
)
from research.phase666_breakout_initiation_analysis import (
    BIG_LOSER_YEN,
    BIG_WINNER_YEN,
    _build_accept_index,
    _class_metrics,
    _enrich_breakout_trade,
    _is_mfe0,
    _is_no_progress,
    _is_stop_hit,
    _is_trailing_mfe_exit,
)
from research.phase631_profit_source_attribution import _num
from research.structural_trade_normalize import resolve_kabu_root

PHASE667_VERDICT = "phase667_flat_vwap_volume_refinement_done"
REPORT_DIR_NAME = "phase667_flat_vwap_volume"
NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = NATIVE_ROOT / "results" / "reports" / REPORT_DIR_NAME
MAX_WORKERS = 4
DISK_USAGE_MAX_PCT = 75.0

VWAP_DEV_BAND_PCT = 0.3
VOLUME_THRESHOLDS: tuple[float, ...] = (1.1, 1.2, 1.5, 2.0)
VOLUME_WINDOWS: tuple[tuple[str, str], ...] = (
    ("1min", "volume_ratio_1min"),
    ("3min", "volume_ratio_3min"),
    ("5min", "volume_ratio_5min"),
)

COUNTERFACTUAL_SCENARIOS: tuple[tuple[str, str, Callable[[Mapping[str, Any]], bool]], ...] = (
    ("keep_flat_vwap_refined", "Flat + refined VWAP breakout only", lambda t: _keep_flat_if(t, _refined_vwap_breakout)),
    (
        "keep_flat_volume_refined_1.2",
        "Flat + volume spike 1.2x + price up only",
        lambda t: _keep_flat_if(t, lambda x: _refined_volume_spike(x, threshold=1.2)),
    ),
    (
        "keep_flat_vwap_volume_refined",
        "Flat + refined VWAP + volume spike only",
        lambda t: _keep_flat_if(t, lambda x: _refined_vwap_breakout(x) and _refined_volume_spike(x, threshold=1.2)),
    ),
    ("exclude_flat_weak_refined", "Exclude flat weak refined", lambda t: not _flat_weak_refined(t)),
    (
        "exclude_flat_range_breakout",
        "Exclude flat range breakout",
        lambda t: not (str(t.get("pretrend_shape") or "") == "E" and str(t.get("breakout_class") or "") == "A"),
    ),
    (
        "exclude_flat_no_signal",
        "Exclude flat no signal",
        lambda t: not (str(t.get("pretrend_shape") or "") == "E" and str(t.get("breakout_class") or "") == "E"),
    ),
    (
        "exclude_flat_weak_and_range",
        "Exclude flat weak + range breakout",
        lambda t: not (
            str(t.get("pretrend_shape") or "") == "E"
            and (str(t.get("breakout_class") or "") == "A" or _flat_weak_refined(t))
        ),
    ),
)


def _is_flat(trade: Mapping[str, Any]) -> bool:
    return str(trade.get("pretrend_shape") or "") == "E"


def _vwap_hold_above(
    series: Sequence[tuple[datetime, float]],
    *,
    entry_ts: datetime,
    entry_px: float,
    hold_sec: float = 90.0,
) -> bool:
    if entry_px <= 0 or not series:
        return False
    checks = 0
    above = 0
    for offset in (0.0, 30.0, 60.0, hold_sec):
        ts = entry_ts - timedelta(seconds=offset)
        px = entry_px if offset == 0 else None
        if px is None:
            from research.phase436_pullback_guard_redesign_shadow import _price_at_or_before

            px = _price_at_or_before(series, ts)
        if px is None or px <= 0:
            continue
        dev = _vwap_dev_pct(series, entry_ts=ts, entry_px=float(px), lookback_min=30.0)
        if dev is None:
            continue
        checks += 1
        if float(dev) > 0:
            above += 1
    return checks >= 2 and above >= checks - 1


def _add_refinement_features(
    trade: dict[str, Any],
    *,
    series: Sequence[tuple[datetime, float]],
    entry_ts: datetime,
    entry_px: float,
) -> dict[str, Any]:
    row = dict(trade)
    dev = _num(row.get("vwap_dev_pct"))
    slope = _vwap_slope_pct_per_min(series, entry_ts=entry_ts, minutes=10.0)
    row["vwap_slope"] = slope
    row["vwap_above"] = bool(dev is not None and float(dev) > 0)
    row["vwap_dev_near_flat"] = bool(dev is not None and -VWAP_DEV_BAND_PCT <= float(dev) <= VWAP_DEV_BAND_PCT)
    row["vwap_cross_recent"] = bool(row.get("vwap_cross_up"))
    row["vwap_hold_above"] = _vwap_hold_above(series, entry_ts=entry_ts, entry_px=entry_px)
    row["refined_vwap_breakout"] = _refined_vwap_breakout(row)

    for _label, key in VOLUME_WINDOWS:
        vol = _num(row.get(key))
        price_up = float(row.get("r60_sec") or 0) > 0 or float(row.get("r120_sec") or 0) > 0.03
        row[f"volume_price_up_{_label}"] = bool(
            vol is not None and float(vol) >= 1.2 and price_up
        )
        row[f"volume_only_no_price_{_label}"] = bool(
            vol is not None and float(vol) >= 1.2 and not price_up
        )
    row["refined_volume_spike_1.2"] = _refined_volume_spike(row, threshold=1.2)
    row["flat_weak_refined"] = _flat_weak_refined(row)
    return row


def _refined_vwap_breakout(trade: Mapping[str, Any]) -> bool:
    if not trade.get("vwap_cross_up"):
        return False
    dev = _num(trade.get("vwap_dev_pct"))
    if dev is None:
        return False
    dev_f = float(dev)
    if dev_f <= 0 or dev_f > VWAP_DEV_BAND_PCT:
        return False
    slope = _num(trade.get("vwap_slope"))
    if slope is not None and float(slope) <= 0:
        return False
    if not trade.get("vwap_above"):
        return False
    if trade.get("vwap_hold_above") is False:
        return False
    return True


def _refined_volume_spike(trade: Mapping[str, Any], *, threshold: float = 1.2) -> bool:
    vols = [
        _num(trade.get("volume_ratio_1min")),
        _num(trade.get("volume_ratio_3min")),
        _num(trade.get("volume_ratio_5min")),
    ]
    if not any(v is not None and float(v) >= threshold for v in vols):
        return False
    price_up = float(trade.get("r60_sec") or 0) > 0 or float(trade.get("r120_sec") or 0) > 0.03
    return price_up


def _flat_weak_refined(trade: Mapping[str, Any]) -> bool:
    if not _is_flat(trade):
        return False
    dev = _num(trade.get("vwap_dev_pct"))
    r60 = float(trade.get("r60_sec") or 0)
    r300 = _num(trade.get("r300_sec"))
    r600 = _num(trade.get("r600_sec"))
    weak_price = (
        bool(trade.get("recent_low_break"))
        or bool(trade.get("vwap_cross_down"))
        or (dev is not None and float(dev) < -0.05)
        or r60 < -0.05
        or (r300 is not None and float(r300) < -0.08)
        or (r600 is not None and float(r600) < -0.08)
    )
    if not weak_price:
        return False
    no_board = not bool(trade.get("board_improvement"))
    mfe0 = _is_mfe0(trade) if trade.get("peak_mfe_pct") is not None else False
    no_prog = _is_no_progress(trade)
    return no_board or mfe0 or no_prog or bool(trade.get("recent_low_break"))


def _keep_flat_if(trade: Mapping[str, Any], flat_ok: Callable[[Mapping[str, Any]], bool]) -> bool:
    if not _is_flat(trade):
        return True
    return flat_ok(trade)


def _enrich_trade_full(
    trade: dict[str, Any],
    *,
    price_idx: Mapping[tuple[str, str], list[tuple[datetime, float]]],
    accept_idx: Mapping[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    row = _enrich_breakout_trade(trade, price_idx=price_idx, accept_idx=accept_idx)
    ent = _parse_ts(str(row.get("entry_time") or ""))
    if ent is None:
        return row
    from research.phase665_pretrend_shape_analysis import _day_key, _sym_t

    sym_t = _sym_t(str(row.get("symbol") or ""))
    day_key = _day_key(str(row.get("day") or ""))
    series = price_idx.get((sym_t, day_key), []) or price_idx.get((str(row.get("symbol") or ""), day_key), [])
    entry_px = float(row.get("entry_price") or 0)
    return _add_refinement_features(row, series=series, entry_ts=ent, entry_px=entry_px)


def _portfolio_metrics(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    m = _class_metrics(trades)
    n = len(trades)
    if n == 0:
        return m
    big_winner_loss = sum(float(t.get("pnl_yen_100") or 0) for t in trades if float(t.get("pnl_yen_100") or 0) >= BIG_WINNER_YEN)
    return {
        **m,
        "stop_hit_rate": m.get("stop_hit_rate"),
        "no_progress_exit_rate": m.get("no_progress_exit_rate"),
        "mfe0_rate": m.get("mfe0_rate"),
        "trailing_mfe_exit_rate": m.get("trailing_mfe_exit_rate"),
        "big_winner_total_pnl": round(big_winner_loss, 2),
        "big_winner_count": sum(1 for t in trades if float(t.get("pnl_yen_100") or 0) >= BIG_WINNER_YEN),
        "big_loser_count": sum(1 for t in trades if float(t.get("pnl_yen_100") or 0) <= BIG_LOSER_YEN),
    }


def _threshold_rows(flat_trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    vwap_variants: tuple[tuple[str, Callable[[Mapping[str, Any]], bool]], ...] = (
        ("vwap_cross_up_only", lambda t: bool(t.get("vwap_cross_up"))),
        ("vwap_refined_strict", _refined_vwap_breakout),
        (
            "vwap_cross_near_above",
            lambda t: bool(t.get("vwap_cross_up"))
            and bool(t.get("vwap_dev_near_flat"))
            and bool(t.get("vwap_above")),
        ),
        (
            "vwap_cross_slope_hold",
            lambda t: bool(t.get("vwap_cross_up"))
            and (_num(t.get("vwap_slope")) is None or float(t.get("vwap_slope") or 0) > 0)
            and bool(t.get("vwap_hold_above")),
        ),
    )
    for variant_id, pred in vwap_variants:
        sub = [t for t in flat_trades if pred(t)]
        m = _class_metrics(sub)
        rows.append(
            {
                "theme": "vwap_breakout",
                "variant_id": variant_id,
                "threshold": "",
                "window": "",
                "flat_pass_count": len(sub),
                "flat_pass_share": round(len(sub) / len(flat_trades), 4) if flat_trades else 0.0,
                **{k: m.get(k) for k in ("win_rate", "profit_factor", "total_pnl_yen_100", "avg_pnl_yen_100", "stop_hit_rate", "mfe0_rate")},
            }
        )

    for thresh in VOLUME_THRESHOLDS:
        for window, key in VOLUME_WINDOWS:
            for require_price in (False, True):
                sub = [
                    t
                    for t in flat_trades
                    if (_num(t.get(key)) is not None and float(t.get(key) or 0) >= thresh)
                    and (
                        not require_price
                        or float(t.get("r60_sec") or 0) > 0
                        or float(t.get("r120_sec") or 0) > 0.03
                    )
                ]
                m = _class_metrics(sub)
                rows.append(
                    {
                        "theme": "volume_spike",
                        "variant_id": f"vol_{window}_{thresh}{'_price_up' if require_price else ''}",
                        "threshold": thresh,
                        "window": window,
                        "flat_pass_count": len(sub),
                        "flat_pass_share": round(len(sub) / len(flat_trades), 4) if flat_trades else 0.0,
                        **{k: m.get(k) for k in ("win_rate", "profit_factor", "total_pnl_yen_100", "avg_pnl_yen_100", "stop_hit_rate", "mfe0_rate")},
                    }
                )

    weak_variants: tuple[tuple[str, Callable[[Mapping[str, Any]], bool]], ...] = (
        ("weak_low_break", lambda t: bool(t.get("recent_low_break"))),
        ("weak_vwap_below", lambda t: (_num(t.get("vwap_dev_pct")) or 0) < 0),
        (
            "weak_refined_combo",
            _flat_weak_refined,
        ),
        (
            "weak_safe_low_and_vwap",
            lambda t: bool(t.get("recent_low_break")) and (_num(t.get("vwap_dev_pct")) or 0) < 0,
        ),
    )
    for variant_id, pred in weak_variants:
        sub = [t for t in flat_trades if pred(t)]
        m = _class_metrics(sub)
        rows.append(
            {
                "theme": "flat_weak",
                "variant_id": variant_id,
                "threshold": "",
                "window": "",
                "flat_pass_count": len(sub),
                "flat_pass_share": round(len(sub) / len(flat_trades), 4) if flat_trades else 0.0,
                **{k: m.get(k) for k in ("win_rate", "profit_factor", "total_pnl_yen_100", "avg_pnl_yen_100", "stop_hit_rate", "mfe0_rate")},
            }
        )
    return rows


def _counterfactual_rows(
    trades: Sequence[Mapping[str, Any]],
    *,
    pool: str,
) -> list[dict[str, Any]]:
    base_trades = list(trades) if pool == "all" else [t for t in trades if str(t.get("entry_pool") or "") == pool]
    baseline = _portfolio_metrics(base_trades)
    rows: list[dict[str, Any]] = []
    for scenario_id, description, keep_fn in COUNTERFACTUAL_SCENARIOS:
        kept = [t for t in base_trades if keep_fn(t)]
        blocked = [t for t in base_trades if t not in kept]
        km = _portfolio_metrics(kept)
        bw = sum(1 for t in blocked if float(t.get("pnl_yen_100") or 0) > 0)
        bl = sum(1 for t in blocked if float(t.get("pnl_yen_100") or 0) < 0)
        blocked_big_winners = sum(
            1 for t in blocked if float(t.get("pnl_yen_100") or 0) >= BIG_WINNER_YEN
        )
        rows.append(
            {
                "scenario_id": scenario_id,
                "description": description,
                "pool": pool,
                "baseline_entries": baseline["entry_count"],
                "kept_entries": km["entry_count"],
                "blocked_entries": len(blocked),
                "blocked_winners": bw,
                "blocked_losers": bl,
                "blocked_big_winners": blocked_big_winners,
                "delta_pnl_yen_100": round(
                    float(km.get("total_pnl_yen_100") or 0) - float(baseline.get("total_pnl_yen_100") or 0),
                    2,
                ),
                "delta_profit_factor": round(
                    float(km.get("profit_factor") or 0) - float(baseline.get("profit_factor") or 0),
                    4,
                )
                if km.get("profit_factor") is not None and baseline.get("profit_factor") is not None
                else None,
                "delta_max_dd_yen_100": round(
                    float(km.get("max_dd_yen_100") or 0) - float(baseline.get("max_dd_yen_100") or 0),
                    2,
                ),
                "kept_win_rate": km.get("win_rate"),
                "kept_profit_factor": km.get("profit_factor"),
                "kept_total_pnl_yen_100": km.get("total_pnl_yen_100"),
                "kept_max_dd_yen_100": km.get("max_dd_yen_100"),
                "kept_stop_hit_rate": km.get("stop_hit_rate"),
                "kept_no_progress_exit_rate": km.get("no_progress_exit_rate"),
                "kept_mfe0_rate": km.get("mfe0_rate"),
                "kept_trailing_mfe_exit_rate": km.get("trailing_mfe_exit_rate"),
                "kept_big_winner_total_pnl": km.get("big_winner_total_pnl"),
            }
        )
    return rows


def _daily_consistency(
    trades: Sequence[Mapping[str, Any]],
    *,
    scenario_id: str,
    pool: str,
) -> list[dict[str, Any]]:
    scenario = next(s for s in COUNTERFACTUAL_SCENARIOS if s[0] == scenario_id)
    keep_fn = scenario[2]
    base_trades = list(trades) if pool == "all" else [t for t in trades if str(t.get("entry_pool") or "") == pool]
    rows: list[dict[str, Any]] = []
    for day in CANONICAL_DAYS:
        day_trades = [t for t in base_trades if t.get("day") == day]
        if not day_trades:
            continue
        base_pnl = sum(float(t.get("pnl_yen_100") or 0) for t in day_trades)
        kept = [t for t in day_trades if keep_fn(t)]
        kept_pnl = sum(float(t.get("pnl_yen_100") or 0) for t in kept)
        rows.append(
            {
                "day": day,
                "scenario_id": scenario_id,
                "pool": pool,
                "baseline_entries": len(day_trades),
                "kept_entries": len(kept),
                "baseline_pnl_yen_100": round(base_pnl, 2),
                "kept_pnl_yen_100": round(kept_pnl, 2),
                "delta_pnl_yen_100": round(kept_pnl - base_pnl, 2),
                "improved_day": kept_pnl >= base_pnl,
            }
        )
    return rows


def _symbol_concentration(
    trades: Sequence[Mapping[str, Any]],
    *,
    scenario_id: str,
    pool: str,
) -> list[dict[str, Any]]:
    scenario = next(s for s in COUNTERFACTUAL_SCENARIOS if s[0] == scenario_id)
    keep_fn = scenario[2]
    base_trades = list(trades) if pool == "all" else [t for t in trades if str(t.get("entry_pool") or "") == pool]
    blocked_by_sym: dict[str, float] = defaultdict(float)
    blocked_count: dict[str, int] = defaultdict(int)
    for t in base_trades:
        if keep_fn(t):
            continue
        sym = str(t.get("symbol") or "")
        blocked_by_sym[sym] += float(t.get("pnl_yen_100") or 0)
        blocked_count[sym] += 1
    total_blocked_pnl = sum(blocked_by_sym.values())
    rows: list[dict[str, Any]] = []
    for sym, pnl in sorted(blocked_by_sym.items(), key=lambda x: x[1]):
        rows.append(
            {
                "scenario_id": scenario_id,
                "pool": pool,
                "symbol": sym,
                "blocked_entries": blocked_count[sym],
                "blocked_pnl_yen_100": round(pnl, 2),
                "share_of_blocked_pnl_pct": round(pnl / total_blocked_pnl * 100.0, 2) if total_blocked_pnl else 0.0,
            }
        )
    if rows:
        top3 = sorted(rows, key=lambda r: abs(float(r.get("blocked_pnl_yen_100") or 0)), reverse=True)[:3]
        top3_share = sum(abs(float(r.get("blocked_pnl_yen_100") or 0)) for r in top3)
        total_abs = sum(abs(float(r.get("blocked_pnl_yen_100") or 0)) for r in rows)
        for r in rows:
            r["top3_abs_share_pct"] = round(top3_share / total_abs * 100.0, 2) if total_abs else 0.0
    return rows


def _consistency_summary(daily_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    improved = [r for r in daily_rows if r.get("improved_day")]
    return {
        "days_with_data": len(daily_rows),
        "improved_days": len(improved),
        "improved_day_rate": round(len(improved) / len(daily_rows), 4) if daily_rows else 0.0,
        "total_delta_pnl": round(sum(float(r.get("delta_pnl_yen_100") or 0) for r in daily_rows), 2),
    }


def decide_phase667(
    *,
    counterfactual: Sequence[Mapping[str, Any]],
    daily_rows: Sequence[Mapping[str, Any]],
    threshold_rows: Sequence[Mapping[str, Any]],
    flat_count: int,
) -> tuple[str, str]:
    pbv2_rows = {r["scenario_id"]: r for r in counterfactual if r.get("pool") == "PBV2"}
    exclude_best = pbv2_rows.get("exclude_flat_weak_and_range") or {}
    keep_vwap_vol = pbv2_rows.get("keep_flat_vwap_volume_refined") or {}

    ex_pnl = float(exclude_best.get("delta_pnl_yen_100") or 0)
    ex_pf = float(exclude_best.get("delta_profit_factor") or 0)
    ex_dd = float(exclude_best.get("delta_max_dd_yen_100") or 0)
    ex_bw = int(exclude_best.get("blocked_winners") or 0)
    ex_bl = int(exclude_best.get("blocked_losers") or 0)

    daily_sub = [d for d in daily_rows if d.get("scenario_id") == "exclude_flat_weak_and_range" and d.get("pool") == "PBV2"]
    consistency = _consistency_summary(daily_sub)
    improved_days = int(consistency.get("improved_days") or 0)
    days_total = int(consistency.get("days_with_data") or 0)

    vwap_refined = next((r for r in threshold_rows if r.get("variant_id") == "vwap_refined_strict"), {})
    vwap_pf = float(vwap_refined.get("profit_factor") or 0)
    vwap_pnl = float(vwap_refined.get("total_pnl_yen_100") or 0)
    vwap_count = int(vwap_refined.get("flat_pass_count") or 0)

    vol_best = max(
        (
            r
            for r in threshold_rows
            if r.get("theme") == "volume_spike"
            and str(r.get("variant_id", "")).endswith("_price_up")
            and int(r.get("flat_pass_count") or 0) >= 10
        ),
        key=lambda r: float(r.get("total_pnl_yen_100") or -1e18),
        default={},
    )

    signal_within_flat_ok = vwap_pnl > 0 and vwap_pf >= 1.05 and vwap_count >= 20
    exclusion_ok = ex_pnl > 250000 and ex_pf > 0.07 and ex_dd >= 0 and ex_bl >= ex_bw

    if exclusion_ok and signal_within_flat_ok and improved_days >= max(14, int(days_total * 0.6)):
        return (
            "ADOPT_CANDIDATE",
            f"Exclude weak+range ΔPnL {ex_pnl:+.0f}, ΔPF {ex_pf:+.3f}; "
            f"VWAP strict flat PF={vwap_pf:.2f} ({vwap_count} entries); "
            f"daily improved {improved_days}/{days_total}.",
        )

    if exclusion_ok or float(keep_vwap_vol.get("delta_pnl_yen_100") or 0) > 400000:
        return (
            "HOLD",
            f"Exclusion counterfactuals work (weak+range ΔPnL {ex_pnl:+.0f}, blocked L/W {ex_bl}/{ex_bw}) "
            f"but refined VWAP within-flat is weak (PF={vwap_pf:.2f}, PnL={vwap_pnl:+.0f}, n={vwap_count}). "
            f"Prefer exclusion over keep-only; tune VWAP/volume gates before Shadow.",
        )

    if float(vol_best.get("total_pnl_yen_100") or 0) > 0:
        return (
            "HOLD",
            f"Volume threshold {vol_best.get('variant_id')} shows flat-subset edge; "
            f"overall exclusion still insufficient (weak+range ΔPnL {ex_pnl:+.0f}).",
        )

    return (
        "REJECT",
        f"Refined flat filters lack edge within flat and exclusion gain is modest "
        f"(weak+range ΔPnL {ex_pnl:+.0f}, VWAP strict PnL {vwap_pnl:+.0f}).",
    )


def _mandatory_answers(
    *,
    flat_trades: Sequence[Mapping[str, Any]],
    counterfactual: Sequence[Mapping[str, Any]],
    threshold_rows: Sequence[Mapping[str, Any]],
    daily_rows: Sequence[Mapping[str, Any]],
    symbol_rows: Sequence[Mapping[str, Any]],
    decision: str,
    rationale: str,
) -> dict[str, Any]:
    vwap_strict = [t for t in flat_trades if _refined_vwap_breakout(t)]
    vol_alone = [t for t in flat_trades if _refined_volume_spike(t, threshold=1.2)]
    vwap_vol = [t for t in flat_trades if _refined_vwap_breakout(t) and _refined_volume_spike(t, threshold=1.2)]
    weak = [t for t in flat_trades if _flat_weak_refined(t)]

    vol_thresh_best = max(
        (r for r in threshold_rows if r.get("theme") == "volume_spike" and r.get("flat_pass_count", 0) >= 5),
        key=lambda r: float(r.get("total_pnl_yen_100") or -1e18),
        default={},
    )

    pbv2_cf = {r["scenario_id"]: r for r in counterfactual if r.get("pool") == "PBV2"}

    return {
        "1_vwap_effective_in_flat": {
            "refined_vwap_count": len(vwap_strict),
            "metrics": _class_metrics(vwap_strict),
            "phase666_flat_B_metrics": _class_metrics([t for t in flat_trades if t.get("breakout_class") == "B"]),
        },
        "2_volume_alone_vs_combo": {
            "volume_only_1.2_price_up": _class_metrics(vol_alone),
            "vwap_plus_volume": _class_metrics(vwap_vol),
            "best_volume_threshold_row": vol_thresh_best,
        },
        "3_flat_weak_cut": {
            "weak_refined_count": len(weak),
            "weak_metrics": _class_metrics(weak),
            "exclude_weak_refined_cf": pbv2_cf.get("exclude_flat_weak_refined"),
            "exclude_weak_and_range_cf": pbv2_cf.get("exclude_flat_weak_and_range"),
        },
        "4_blocked_winner_check": {
            sid: {
                "blocked_winners": r.get("blocked_winners"),
                "blocked_losers": r.get("blocked_losers"),
                "blocked_big_winners": r.get("blocked_big_winners"),
            }
            for sid, r in pbv2_cf.items()
        },
        "5_daily_consistency": {
            sid: _consistency_summary([d for d in daily_rows if d.get("scenario_id") == sid and d.get("pool") == "PBV2"])
            for sid in (r[0] for r in COUNTERFACTUAL_SCENARIOS)
        },
        "6_symbol_concentration": {
            "exclude_flat_weak_and_range_top3": sorted(
                [r for r in symbol_rows if r.get("scenario_id") == "exclude_flat_weak_and_range"],
                key=lambda r: abs(float(r.get("blocked_pnl_yen_100") or 0)),
                reverse=True,
            )[:10],
        },
        "7_forward_shadow_value": {"decision": decision, "rationale": rationale},
    }


def _write_decision_md(*, report: Mapping[str, Any], answers: Mapping[str, Any]) -> None:
    lines = [
        "# Phase667 — Flat VWAP / Volume Refinement",
        "",
        f"**Verdict:** `{report.get('verdict')}`",
        f"**Decision:** **{report.get('decision')}**",
        "",
        "## Rationale",
        "",
        str(report.get("decision_rationale") or ""),
        "",
        "## Mandatory answers",
        "",
    ]
    for key, title in (
        ("1_vwap_effective_in_flat", "VWAP上抜けは横ばい内で有効か"),
        ("2_volume_alone_vs_combo", "出来高急増単独 vs VWAP組合せ"),
        ("3_flat_weak_cut", "横ばい弱含みの安全な切り方"),
        ("4_blocked_winner_check", "blocked winner過多か"),
        ("5_daily_consistency", "日別再現性"),
        ("6_symbol_concentration", "銘柄依存"),
        ("7_forward_shadow_value", "Forward Shadow候補"),
    ):
        lines.append(f"### {title}")
        lines.append("")
        lines.append(f"```json\n{json.dumps(answers.get(key), ensure_ascii=False, indent=2)}\n```")
        lines.append("")
    lines.extend(["## Constraints", "", "- Runtime / YAML / Shadow 変更なし", "- Counterfactualのみ", ""])
    (REPORT_ROOT / "phase667_decision.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_audit(*, max_workers: int = MAX_WORKERS) -> dict[str, Any]:
    disk_before = _disk_usage_pct(NATIVE_ROOT)
    disk_cap_exceeded_at_start = disk_before > DISK_USAGE_MAX_PCT

    repo_root = resolve_kabu_root(NATIVE_ROOT)
    price_idx = _build_price_index_canonical(repo_root)
    accept_idx = _build_accept_index()
    trades = load_canonical_trades()

    chunk_size = max(1, len(trades) // max_workers)
    chunks = [trades[i : i + chunk_size] for i in range(0, len(trades), chunk_size)]
    enriched: list[dict[str, Any]] = []

    def _worker(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [_enrich_trade_full(t, price_idx=price_idx, accept_idx=accept_idx) for t in batch]

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for batch in ex.map(_worker, chunks):
            enriched.extend(batch)
    enriched.sort(key=lambda t: (str(t.get("day") or ""), str(t.get("entry_time") or ""), str(t.get("symbol") or "")))

    flat_trades = [t for t in enriched if _is_flat(t)]
    flat_pbv2 = [t for t in flat_trades if str(t.get("entry_pool") or "") == "PBV2"]

    threshold_rows = _threshold_rows(flat_pbv2)

    counterfactual: list[dict[str, Any]] = []
    for pool in ("all", "PBV2", "OR"):
        counterfactual.extend(_counterfactual_rows(enriched, pool=pool))

    daily_rows: list[dict[str, Any]] = []
    symbol_rows: list[dict[str, Any]] = []
    for pool in ("all", "PBV2"):
        for scenario_id, _, _ in COUNTERFACTUAL_SCENARIOS:
            daily_rows.extend(_daily_consistency(enriched, scenario_id=scenario_id, pool=pool))
            symbol_rows.extend(_symbol_concentration(enriched, scenario_id=scenario_id, pool=pool))

    decision, rationale = decide_phase667(
        counterfactual=counterfactual,
        daily_rows=daily_rows,
        threshold_rows=threshold_rows,
        flat_count=len(flat_trades),
    )
    answers = _mandatory_answers(
        flat_trades=flat_pbv2,
        counterfactual=counterfactual,
        threshold_rows=threshold_rows,
        daily_rows=daily_rows,
        symbol_rows=symbol_rows,
        decision=decision,
        rationale=rationale,
    )

    disk_after = _disk_usage_pct(NATIVE_ROOT)
    report: dict[str, Any] = {
        "verdict": PHASE667_VERDICT,
        "entry_count": len(enriched),
        "flat_entry_count": len(flat_trades),
        "flat_pbv2_count": len(flat_pbv2),
        "refined_vwap_flat_count": sum(1 for t in flat_pbv2 if _refined_vwap_breakout(t)),
        "refined_volume_flat_count": sum(1 for t in flat_pbv2 if _refined_volume_spike(t, threshold=1.2)),
        "flat_weak_refined_count": sum(1 for t in flat_pbv2 if _flat_weak_refined(t)),
        "decision": decision,
        "decision_rationale": rationale,
        "disk_cap_exceeded_at_start": disk_cap_exceeded_at_start,
        "mandatory_answers": answers,
    }

    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    _write_csv(
        REPORT_ROOT / "phase667_flat_vwap_volume_thresholds.csv",
        [
            "theme",
            "variant_id",
            "threshold",
            "window",
            "flat_pass_count",
            "flat_pass_share",
            "win_rate",
            "profit_factor",
            "total_pnl_yen_100",
            "avg_pnl_yen_100",
            "stop_hit_rate",
            "mfe0_rate",
        ],
        threshold_rows,
    )
    _write_csv(
        REPORT_ROOT / "phase667_flat_vwap_volume_counterfactual.csv",
        [
            "scenario_id",
            "description",
            "pool",
            "baseline_entries",
            "kept_entries",
            "blocked_entries",
            "blocked_winners",
            "blocked_losers",
            "blocked_big_winners",
            "delta_pnl_yen_100",
            "delta_profit_factor",
            "delta_max_dd_yen_100",
            "kept_win_rate",
            "kept_profit_factor",
            "kept_total_pnl_yen_100",
            "kept_max_dd_yen_100",
            "kept_stop_hit_rate",
            "kept_no_progress_exit_rate",
            "kept_mfe0_rate",
            "kept_trailing_mfe_exit_rate",
            "kept_big_winner_total_pnl",
        ],
        counterfactual,
    )
    _write_csv(
        REPORT_ROOT / "phase667_flat_vwap_volume_daily.csv",
        [
            "day",
            "scenario_id",
            "pool",
            "baseline_entries",
            "kept_entries",
            "baseline_pnl_yen_100",
            "kept_pnl_yen_100",
            "delta_pnl_yen_100",
            "improved_day",
        ],
        daily_rows,
    )
    _write_csv(
        REPORT_ROOT / "phase667_flat_vwap_volume_symbol.csv",
        [
            "scenario_id",
            "pool",
            "symbol",
            "blocked_entries",
            "blocked_pnl_yen_100",
            "share_of_blocked_pnl_pct",
            "top3_abs_share_pct",
        ],
        symbol_rows,
    )
    (REPORT_ROOT / "phase667_flat_vwap_volume_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (REPORT_ROOT / "phase667_disk_usage_report.json").write_text(
        json.dumps(
            {
                "disk_usage_before_pct": round(disk_before, 2),
                "disk_usage_after_pct": round(disk_after, 2),
                "disk_cap_pct": DISK_USAGE_MAX_PCT,
                "disk_cap_exceeded_at_start": disk_cap_exceeded_at_start,
                "max_workers": max_workers,
                "temp_files_created": False,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _write_decision_md(report=report, answers=answers)
    return report


if __name__ == "__main__":
    result = run_audit()
    print(json.dumps({"verdict": result["verdict"], "decision": result["decision"]}, ensure_ascii=False))
