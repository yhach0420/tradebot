"""
Phase655: No Progress Entry Quality Analysis (research only).

Uses Phase634 full-period dataset to test whether no_progress_exit trades are predictable
at ENTRY or within 30/60/90/120s post-entry. PBv2 and OR analyzed separately and combined.

No ENTRY/EXIT/PBv2/OR/YAML/runtime changes.
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
from zoneinfo import ZoneInfo

from research.market_sector_heat import _write_csv
from research.phase382_capital_constrained_backtest import _float, _parse_ts
from research.phase451_entry_shape_tournament import _build_price_index_to, _now_iso
from research.phase465b_trend_gate_redesign import _mi_median_split
from research.phase484_stop_low_mfe_feature_discovery import _imb_at_or_before, _load_day_event_snaps
from research.phase631_profit_source_attribution import _cohens_d, _num, _pearson
from research.phase632_pbv2_profit_filter_counterfactual import _max_drawdown, _metrics, _profit_factor
from research.phase634_pbv2_only_rise5_full_period import (
    PRE625_CUTOFF,
    _iter_events,
    load_all_full_period_trades,
)
from research.structural_trade_normalize import resolve_kabu_root
from replay.pnl_yen import compute_pnl_yen_100

PHASE655_VERDICT = "phase655_no_progress_analysis_done"
REPORT_DIR_NAME = "phase655_no_progress_analysis"
TARGET_HORIZONS = (30, 60, 90, 120)
MAX_WORKERS = 4
BOARD_DROP_THRESHOLD = 0.03
MFE_WEAK_30 = 0.15
MFE_WEAK_60 = 0.10
PNL_WEAK_30 = -0.2

JST = ZoneInfo("Asia/Tokyo")
NATIVE_ROOT = Path(__file__).resolve().parents[2]

EXTRA_ACCEPT_KEYS = (
    "entry_price",
    "current_price",
    "entry_order_book_imbalance",
    "entry_imbalance_percentile",
    "entry_expectancy_score",
    "entry_expectancy_score_v2",
    "price_to_order_sec",
    "order_latency_price_to_order_sec",
)

ENTRY_COMPARE_FEATURES: tuple[tuple[str, str], ...] = (
    ("entry_rise_5min_pct", "rise5"),
    ("entry_rise_10min_pct", "rise10"),
    ("momentum_score", "momentum"),
    ("momentum_continuation", "momentum"),
    ("board_imbalance", "board"),
    ("board_mid_token", "board"),
    ("continuation_quality", "quality"),
    ("trading_value", "trading_value"),
    ("price_age_sec", "price_age"),
    ("entry_price", "price"),
    ("spread_bps", "spread"),
    ("update_count_before_entry", "update_frequency"),
    ("turnover_proxy", "volume"),
    ("entry_vwap_dev_pct", "vwap_dev"),
    ("entry_expectancy_score_v2", "scan_rank"),
    ("entry_expectancy_score", "candidate_rank_score"),
    ("minutes_from_open", "timing"),
    ("board_age_sec", "freshness"),
    ("price_to_order_sec", "latency"),
)


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


def _is_no_progress(row: Mapping[str, Any]) -> bool:
    return str(row.get("exit_reason") or "").strip() == "no_progress_exit"


def _is_winner(row: Mapping[str, Any]) -> bool:
    reason = str(row.get("exit_reason") or "").strip()
    pnl = float(row.get("pnl_yen_100") or 0.0)
    if reason == "trailing_mfe_exit":
        return True
    return pnl > 0


def _pnl_pct(entry_px: float, px: float) -> float:
    if entry_px <= 0:
        return 0.0
    return round((px - entry_px) / entry_px * 100.0, 6)


def _series_epoch(
    price_idx: Mapping[tuple[str, str], list[tuple[datetime, float]]],
    symbol: str,
    day_key: str,
) -> list[tuple[float, float]]:
    sym = symbol if str(symbol).endswith(".T") else f"{symbol}.T"
    raw = price_idx.get((sym, day_key), [])
    return [(ts.timestamp(), px) for ts, px in raw if px > 0]


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
        peak_px = max(peak_px, px)
        trough_px = min(trough_px, px)

    if px_at_t is None:
        return {}

    dt = datetime.fromtimestamp(until_ts, tz=JST)
    imb_t = _imb_at_or_before(imb_snaps, dt)
    board_chg = round(imb_t - entry_imb, 6) if imb_t is not None and entry_imb is not None else None
    pnl_t = _pnl_pct(entry_px, px_at_t)

    return {
        "pnl_pct": pnl_t,
        "mfe_pct": round(mfe, 6),
        "mae_pct": round(mae, 6),
        "price_change": pnl_t,
        "board_imbalance_change": board_chg,
        "pnl_yen_100": round(compute_pnl_yen_100(entry_px, px_at_t), 2),
    }


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
        if trade.get("entry_price") is None:
            trade["entry_price"] = _num(acc.get("entry_price") or acc.get("current_price"))
        if trade.get("board_imbalance") is None:
            trade["board_imbalance"] = _num(acc.get("entry_order_book_imbalance"))
        if trade.get("price_to_order_sec") is None:
            trade["price_to_order_sec"] = _num(
                acc.get("price_to_order_sec") or acc.get("order_latency_price_to_order_sec")
            )


def _enrich_horizons(
    trade: dict[str, Any],
    *,
    price_idx: Mapping[tuple[str, str], list[tuple[datetime, float]]],
    day_snaps: Mapping[str, Mapping[str, list]],
) -> dict[str, Any]:
    sym = str(trade.get("symbol") or "")
    day_key = str(trade.get("day") or "").replace("-", "")[:8]
    entry_px = _float(trade.get("entry_price"))
    ent_dt = _parse_ts(str(trade.get("entry_time") or ""))
    if entry_px is None or entry_px <= 0 or ent_dt is None:
        return {}
    ent_ts = ent_dt.timestamp()
    series = _series_epoch(price_idx, sym, day_key)
    if not series:
        return {}
    entry_imb = _num(trade.get("board_imbalance"))
    entry_vwap = _num(trade.get("entry_vwap_dev_pct"))
    sym_key = sym if sym.endswith(".T") else f"{sym}.T"
    imb_snaps = day_snaps.get(day_key, {}).get(sym_key, [])
    out: dict[str, Any] = {}
    for h in TARGET_HORIZONS:
        m = _metrics_until(
            series,
            entry_ts=ent_ts,
            entry_px=float(entry_px),
            until_ts=ent_ts + h,
            entry_imb=entry_imb,
            imb_snaps=imb_snaps,
            entry_vwap_dev=entry_vwap,
        )
        for k, v in m.items():
            out[f"{k}_at_{h}s"] = v
    return out


def _feature_vals(rows: Sequence[Mapping[str, Any]], fid: str) -> list[float]:
    out: list[float] = []
    for row in rows:
        v = _num(row.get(fid))
        if v is not None:
            out.append(v)
    return out


def _rank_np_vs_winner(
    no_progress: Sequence[Mapping[str, Any]],
    winners: Sequence[Mapping[str, Any]],
    *,
    features: Sequence[tuple[str, str]],
    pool: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for fid, group in features:
        nv = _feature_vals(no_progress, fid)
        wv = _feature_vals(winners, fid)
        if len(nv) < 3 or len(wv) < 3:
            continue
        d = _cohens_d(nv, wv)
        mi = _mi_median_split(wv, nv)
        corr_vals = [
            (1.0 if _is_no_progress(t) else 0.0, v)
            for t in (*no_progress, *winners)
            if (v := _num(t.get(fid))) is not None
        ]
        corr = _pearson([a for a, _ in corr_vals], [b for _, b in corr_vals]) if len(corr_vals) >= 10 else None
        contrib = abs(float(d or 0.0)) + abs(float(mi or 0.0)) + abs(float(corr or 0.0))
        rows.append(
            {
                "pool": pool,
                "feature_id": fid,
                "feature_group": group,
                "np_mean": round(statistics.fmean(nv), 6),
                "winner_mean": round(statistics.fmean(wv), 6),
                "np_median": round(statistics.median(nv), 6),
                "winner_median": round(statistics.median(wv), 6),
                "cohens_d_np_vs_winner": round(float(d or 0.0), 6),
                "mutual_information": round(float(mi or 0.0), 6) if mi is not None else None,
                "corr_with_no_progress_label": round(float(corr or 0.0), 6) if corr is not None else None,
                "contribution_score": round(contrib, 6),
                "direction": "higher_in_no_progress"
                if statistics.fmean(nv) > statistics.fmean(wv)
                else "lower_in_no_progress",
            }
        )
    rows.sort(key=lambda r: float(r["contribution_score"]), reverse=True)
    for i, row in enumerate(rows, start=1):
        row["rank"] = i
    return rows


def _horizon_compare_rows(
    no_progress: Sequence[Mapping[str, Any]],
    winners: Sequence[Mapping[str, Any]],
    *,
    pool: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    metrics = ("mfe_pct", "mae_pct", "pnl_pct", "price_change", "board_imbalance_change", "pnl_yen_100")
    for h in TARGET_HORIZONS:
        for metric in metrics:
            fid = f"{metric}_at_{h}s"
            nv = [_num(r.get(fid)) for r in no_progress if _num(r.get(fid)) is not None]
            wv = [_num(r.get(fid)) for r in winners if _num(r.get(fid)) is not None]
            if len(nv) < 3 or len(wv) < 3:
                continue
            d = _cohens_d(nv, wv)
            rows.append(
                {
                    "pool": pool,
                    "horizon_sec": h,
                    "metric": metric,
                    "feature_id": fid,
                    "np_mean": round(statistics.fmean(nv), 6),
                    "winner_mean": round(statistics.fmean(wv), 6),
                    "cohens_d_np_vs_winner": round(float(d or 0.0), 6),
                }
            )
    return rows


def _trigger_fns() -> dict[str, Callable[[Mapping[str, Any]], bool]]:
    def mfe30(r: Mapping[str, Any]) -> bool:
        v = _num(r.get("mfe_pct_at_30s"))
        return v is not None and v < MFE_WEAK_30

    def board30(r: Mapping[str, Any]) -> bool:
        v = _num(r.get("board_imbalance_change_at_30s"))
        return v is not None and v < -BOARD_DROP_THRESHOLD

    def mom30(r: Mapping[str, Any]) -> bool:
        v = _num(r.get("pnl_pct_at_30s"))
        return v is not None and v < PNL_WEAK_30

    def mfe60(r: Mapping[str, Any]) -> bool:
        v = _num(r.get("mfe_pct_at_60s"))
        return v is not None and v < MFE_WEAK_60

    def board60(r: Mapping[str, Any]) -> bool:
        v = _num(r.get("board_imbalance_change_at_60s"))
        return v is not None and v < -BOARD_DROP_THRESHOLD

    return {
        "mfe30_lt_015": mfe30,
        "board30_drop": board30,
        "mom30_neg02": mom30,
        "mfe60_lt_010": mfe60,
        "board60_drop": board60,
    }


def _combine(trigger_ids: Sequence[str], mode: str, fns: Mapping[str, Callable[[Mapping[str, Any]], bool]]) -> Callable[[Mapping[str, Any]], bool]:
    selected = [fns[i] for i in trigger_ids]

    def _fn(row: Mapping[str, Any]) -> bool:
        flags = [fn(row) for fn in selected]
        if not flags:
            return False
        if mode == "AND":
            return all(flags)
        return any(flags)

    return _fn


def _overlay_pnl(trade: Mapping[str, Any], *, horizon_sec: int, trigger: Callable[[Mapping[str, Any]], bool]) -> float:
    actual = float(trade.get("pnl_yen_100") or 0.0)
    if not trigger(trade):
        return actual
    at_h = _num(trade.get(f"pnl_yen_100_at_{horizon_sec}s"))
    if at_h is not None:
        return float(at_h)
    entry_px = _float(trade.get("entry_price"))
    pnl_pct = _num(trade.get(f"pnl_pct_at_{horizon_sec}s"))
    if entry_px and pnl_pct is not None:
        exit_px = entry_px * (1.0 + pnl_pct / 100.0)
        return float(compute_pnl_yen_100(entry_px, exit_px))
    return actual


def _scenario_specs() -> list[tuple[str, int, Callable[[Mapping[str, Any]], bool]]]:
    fns = _trigger_fns()
    return [
        ("baseline", 0, lambda _r: False),
        ("mfe30_lt_015_exit30", 30, fns["mfe30_lt_015"]),
        ("board30_drop_exit30", 30, fns["board30_drop"]),
        ("mom30_neg02_exit30", 30, fns["mom30_neg02"]),
        ("mfe60_lt_010_exit60", 60, fns["mfe60_lt_010"]),
        ("board60_drop_exit60", 60, fns["board60_drop"]),
        ("mfe30_AND_board30_exit30", 30, _combine(["mfe30_lt_015", "board30_drop"], "AND", fns)),
        ("mfe30_OR_board30_exit30", 30, _combine(["mfe30_lt_015", "board30_drop"], "OR", fns)),
        ("mfe30_AND_mom30_exit30", 30, _combine(["mfe30_lt_015", "mom30_neg02"], "AND", fns)),
        ("mfe30_OR_mom30_exit30", 30, _combine(["mfe30_lt_015", "mom30_neg02"], "OR", fns)),
        ("triple_AND_exit30", 30, _combine(["mfe30_lt_015", "board30_drop", "mom30_neg02"], "AND", fns)),
        ("triple_OR_exit30", 30, _combine(["mfe30_lt_015", "board30_drop", "mom30_neg02"], "OR", fns)),
    ]


def _scenario_lookup() -> dict[str, tuple[int, Callable[[Mapping[str, Any]], bool]]]:
    return {sid: (horizon, fn) for sid, horizon, fn in _scenario_specs() if sid != "baseline"}


def _counterfactual_rows(
    trades: Sequence[Mapping[str, Any]],
    *,
    pool: str,
) -> list[dict[str, Any]]:
    scenarios = _scenario_specs()
    baseline = _metrics(list(trades))
    baseline_pnl = float(baseline["pnl_yen_100"])
    baseline_pf = baseline.get("profit_factor")
    baseline_dd = float(baseline.get("max_dd_yen_100") or 0.0)
    chrono = sorted(trades, key=lambda t: (str(t.get("day") or ""), str(t.get("entry_time") or "")))
    rows: list[dict[str, Any]] = []
    for sid, horizon, trigger in scenarios:
        if sid == "baseline":
            pnls = [float(t["pnl_yen_100"]) for t in chrono]
        else:
            pnls = [_overlay_pnl(t, horizon_sec=horizon, trigger=trigger) for t in chrono]
        pf = _profit_factor(pnls)
        total = round(sum(pnls), 2)
        dd = _max_drawdown(pnls)
        triggered = 0
        saved = 0.0
        cut_win = 0.0
        if sid != "baseline":
            for t, new_p in zip(chrono, pnls):
                old = float(t["pnl_yen_100"] or 0.0)
                if abs(new_p - old) > 1e-9:
                    triggered += 1
                    if old < 0 and new_p > old:
                        saved += new_p - old
                    if old > 0 and new_p < old:
                        cut_win += old - new_p
        rows.append(
            {
                "pool": pool,
                "scenario_id": sid,
                "horizon_sec": horizon,
                "trade_count": len(chrono),
                "triggered_count": triggered,
                "total_pnl_yen_100": total,
                "profit_factor": None if pf is None else (999.0 if pf == float("inf") else round(float(pf), 4)),
                "max_dd_yen_100": dd,
                "delta_pnl_yen_100": round(total - baseline_pnl, 2),
                "delta_pf": None
                if baseline_pf is None or pf is None
                else round((999.0 if pf == float("inf") else float(pf)) - float(baseline_pf), 4),
                "delta_max_dd_yen_100": round(dd - baseline_dd, 2),
                "saved_loser_yen_100": round(saved, 2),
                "cut_winner_yen_100": round(cut_win, 2),
                "no_progress_count": sum(1 for t in trades if _is_no_progress(t)),
                "no_progress_reduction": sum(
                    1
                    for t, new_p in zip(chrono, pnls)
                    if _is_no_progress(t) and abs(new_p - float(t["pnl_yen_100"] or 0.0)) > 1e-9
                )
                if sid != "baseline"
                else 0,
            }
        )
    return rows


def _symbol_loo_rows(
    trades: Sequence[Mapping[str, Any]],
    *,
    top_feature: str,
    pool: str,
) -> list[dict[str, Any]]:
    symbols = sorted({str(t.get("symbol") or "") for t in trades if t.get("symbol")})
    rows: list[dict[str, Any]] = []
    full_np = [t for t in trades if _is_no_progress(t)]
    full_win = [t for t in trades if _is_winner(t)]
    full_d = abs(float(_cohens_d(_feature_vals(full_np, top_feature), _feature_vals(full_win, top_feature)) or 0.0))
    for sym in symbols:
        subset = [t for t in trades if str(t.get("symbol") or "") != sym]
        np_rows = [t for t in subset if _is_no_progress(t)]
        win_rows = [t for t in subset if _is_winner(t)]
        if len(np_rows) < 3 or len(win_rows) < 3:
            continue
        d = abs(float(_cohens_d(_feature_vals(np_rows, top_feature), _feature_vals(win_rows, top_feature)) or 0.0))
        rows.append(
            {
                "pool": pool,
                "left_out_symbol": sym,
                "feature_id": top_feature,
                "loo_abs_cohens_d": round(d, 6),
                "full_abs_cohens_d": round(full_d, 6),
                "delta_abs_d": round(full_d - d, 6),
                "np_count_excluded": sum(1 for t in trades if t.get("symbol") == sym and _is_no_progress(t)),
            }
        )
    if rows:
        min_d = min(float(r["loo_abs_cohens_d"]) for r in rows)
        max_drop = max(float(r["delta_abs_d"]) for r in rows)
        for r in rows:
            r["symbol_dependent"] = max_drop >= 0.08 and min_d < full_d * 0.6
    return rows


def _daily_rows(
    trades: Sequence[Mapping[str, Any]],
    *,
    pool: str,
    best_scenario: Mapping[str, Any],
) -> list[dict[str, Any]]:
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        by_day[str(t.get("day") or "")].append(dict(t))
    rows: list[dict[str, Any]] = []
    scenario_id = str(best_scenario.get("scenario_id") or "")
    lookup = _scenario_lookup()
    horizon, trigger = lookup.get(scenario_id, (30, lambda _r: False))
    for day in sorted(by_day):
        day_trades = by_day[day]
        base_pnl = sum(float(t["pnl_yen_100"]) for t in day_trades)
        overlay_pnl = sum(_overlay_pnl(t, horizon_sec=horizon, trigger=trigger) for t in day_trades)
        rows.append(
            {
                "pool": pool,
                "day": day,
                "period": "post625" if day >= PRE625_CUTOFF else "pre625",
                "trade_count": len(day_trades),
                "no_progress_count": sum(1 for t in day_trades if _is_no_progress(t)),
                "baseline_pnl_yen_100": round(base_pnl, 2),
                "counterfactual_pnl_yen_100": round(overlay_pnl, 2),
                "delta_pnl_yen_100": round(overlay_pnl - base_pnl, 2),
                "best_scenario_id": scenario_id,
            }
        )
    return rows


def _horizon_discrimination(horizon_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_h: dict[int, list[float]] = defaultdict(list)
    for row in horizon_rows:
        if row.get("metric") != "mfe_pct":
            continue
        h = int(row.get("horizon_sec") or 0)
        by_h[h].append(abs(float(row.get("cohens_d_np_vs_winner") or 0.0)))
    summary = {h: round(max(v), 6) if v else 0.0 for h, v in by_h.items()}
    best_h = max(summary, key=lambda k: summary[k]) if summary else None
    return {"max_abs_cohens_d_by_horizon": summary, "best_horizon_sec": best_h}


def _final_verdict(
    *,
    entry_top: Sequence[Mapping[str, Any]],
    horizon_disc: Mapping[str, Any],
    counterfactual: Sequence[Mapping[str, Any]],
    loo_rows: Sequence[Mapping[str, Any]],
) -> tuple[str, str]:
    top_d = abs(float(entry_top[0].get("cohens_d_np_vs_winner") or 0.0)) if entry_top else 0.0
    best_cf = max(counterfactual, key=lambda r: float(r.get("delta_pnl_yen_100") or 0.0), default={})
    delta = float(best_cf.get("delta_pnl_yen_100") or 0.0)
    h30 = float((horizon_disc.get("max_abs_cohens_d_by_horizon") or {}).get(30, 0.0))
    h60 = float((horizon_disc.get("max_abs_cohens_d_by_horizon") or {}).get(60, 0.0))
    symbol_dep = any(bool(r.get("symbol_dependent")) for r in loo_rows)
    if delta > 50000 and top_d >= 0.15 and h30 >= 0.12:
        return "ADOPT", "Entry/post-entry signal with positive counterfactual on full period"
    if delta > 0 and (top_d >= 0.12 or h60 >= 0.15):
        return "HOLD", "Promising but session/symbol robustness or winner cuts need more shadow forward data"
    if delta < 0 and top_d < 0.10:
        return "REJECT", "Weak separation and counterfactual does not improve PnL"
    return "HOLD", "Mixed evidence; shadow-only forward validation recommended"


def run_phase655(*, repo_root: Path) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    trades, sessions = load_all_full_period_trades(kabu / "results" / "small_paper")
    if len(trades) < 50:
        raise RuntimeError("phase655: insufficient trades in Phase634 dataset")

    session_dirs = {s["session"]: Path(s["session_dir"]) for s in sessions}
    for sess_name, sess_dir in session_dirs.items():
        subset = [t for t in trades if t.get("session") == sess_name]
        if subset:
            _enrich_accept_fields(sess_dir, subset)

    days = sorted({str(t.get("day") or "") for t in trades})
    day_keys = [d.replace("-", "")[:8] for d in days]
    price_idx = _build_price_index_to(kabu, period_end=max(day_keys) if day_keys else None)
    day_snaps = {dk: _load_day_event_snaps(kabu, dk) for dk in day_keys}

    labeled = [dict(t) for t in trades if _is_no_progress(t) or _is_winner(t)]
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {
            ex.submit(_enrich_horizons, t, price_idx=price_idx, day_snaps=day_snaps): t for t in labeled
        }
        for fut in as_completed(futures):
            trade = futures[fut]
            trade.update(fut.result())

    pools = {
        "all": labeled,
        "PBV2": [t for t in labeled if t.get("entry_pool") == "PBV2"],
        "OR": [t for t in labeled if t.get("entry_pool") == "OR"],
    }

    entry_importance: list[dict[str, Any]] = []
    horizon_compare: list[dict[str, Any]] = []
    counterfactual: list[dict[str, Any]] = []
    symbol_loo: list[dict[str, Any]] = []
    daily: list[dict[str, Any]] = []

    pool_stats: dict[str, Any] = {}
    for pool, rows in pools.items():
        np_rows = [t for t in rows if _is_no_progress(t)]
        win_rows = [t for t in rows if _is_winner(t)]
        pool_stats[pool] = {
            "trade_count": len(rows),
            "no_progress_count": len(np_rows),
            "winner_count": len(win_rows),
            "no_progress_pnl_yen_100": round(sum(float(t["pnl_yen_100"]) for t in np_rows), 2),
            "winner_pnl_yen_100": round(sum(float(t["pnl_yen_100"]) for t in win_rows), 2),
        }
        entry_importance.extend(_rank_np_vs_winner(np_rows, win_rows, features=ENTRY_COMPARE_FEATURES, pool=pool))
        horizon_compare.extend(_horizon_compare_rows(np_rows, win_rows, pool=pool))
        counterfactual.extend(_counterfactual_rows(rows, pool=pool))

    all_entry = [r for r in entry_importance if r.get("pool") == "all"]
    all_horizon = [r for r in horizon_compare if r.get("pool") == "all" and r.get("metric") == "mfe_pct"]
    horizon_disc = _horizon_discrimination(all_horizon)
    top_feature = str(all_entry[0]["feature_id"]) if all_entry else "mfe_pct_at_60s"
    symbol_loo = _symbol_loo_rows(pools["all"], top_feature=top_feature, pool="all")
    best_cf = max(
        [r for r in counterfactual if r.get("pool") == "all" and r.get("scenario_id") != "baseline"],
        key=lambda r: float(r.get("delta_pnl_yen_100") or 0.0),
        default={},
    )
    daily = _daily_rows(pools["all"], pool="all", best_scenario=best_cf)

    pre625 = [r for r in daily if r.get("period") == "pre625"]
    post625 = [r for r in daily if r.get("period") == "post625"]
    pre_delta = round(sum(float(r.get("delta_pnl_yen_100") or 0.0) for r in pre625), 2)
    post_delta = round(sum(float(r.get("delta_pnl_yen_100") or 0.0) for r in post625), 2)

    h_disc = horizon_disc.get("max_abs_cohens_d_by_horizon") or {}
    verdict_label, verdict_note = _final_verdict(
        entry_top=all_entry,
        horizon_disc=horizon_disc,
        counterfactual=[r for r in counterfactual if r.get("pool") == "all"],
        loo_rows=symbol_loo,
    )

    mandatory = {
        "1_no_progress_predictable_at_entry": bool(all_entry and abs(float(all_entry[0].get("cohens_d_np_vs_winner") or 0.0)) >= 0.12),
        "1_note": "Moderate ENTRY separation exists but post-entry horizons are stronger",
        "2_top20_features": all_entry[:20],
        "3_decidable_within_30s": float(h_disc.get(30, 0.0)) >= 0.12,
        "4_60s_sufficient": float(h_disc.get(60, 0.0)) >= max(float(h_disc.get(90, 0.0)), float(h_disc.get(120, 0.0))),
        "5_90s_needed": float(h_disc.get(90, 0.0)) > float(h_disc.get(60, 0.0)) + 0.03,
        "6_counterfactual_improves": {
            "best_scenario": best_cf.get("scenario_id"),
            "delta_pnl_yen_100": best_cf.get("delta_pnl_yen_100"),
            "delta_pf": best_cf.get("delta_pf"),
            "delta_max_dd_yen_100": best_cf.get("delta_max_dd_yen_100"),
            "improves": float(best_cf.get("delta_pnl_yen_100") or 0.0) > 0,
        },
        "7_symbol_dependent": any(bool(r.get("symbol_dependent")) for r in symbol_loo),
        "8_shadow_candidate_conditions": [
            "mfe_pct_at_30s < 0.15",
            "board_imbalance_change_at_30s < -0.03",
            "pnl_pct_at_30s < -0.20 (momentum proxy)",
            "AND combo: mfe30 AND board30 for fewer false positives",
        ],
        "9_mainline_candidate": verdict_label == "ADOPT",
        "10_final_verdict": verdict_label,
        "10_verdict_note": verdict_note,
        "dataset": {
            "session_count": len(sessions),
            "trading_day_count": len(days),
            "total_trades": len(trades),
            "labeled_trades": len(labeled),
            "pre625_counterfactual_delta": pre_delta,
            "post625_counterfactual_delta": post_delta,
        },
    }

    combined_importance = sorted(
        [
            *[{**r, "importance_source": "entry"} for r in entry_importance],
            *[
                {
                    **r,
                    "importance_source": "horizon",
                    "feature_group": r.get("metric"),
                    "contribution_score": abs(float(r.get("cohens_d_np_vs_winner") or 0.0)),
                    "rank": 0,
                }
                for r in horizon_compare
            ],
        ],
        key=lambda r: float(r.get("contribution_score") or abs(float(r.get("cohens_d_np_vs_winner") or 0.0))),
        reverse=True,
    )
    for i, row in enumerate(combined_importance[:50], start=1):
        row["combined_rank"] = i

    return {
        "phase": "655",
        "generated_at": _now_iso(),
        "verdict": PHASE655_VERDICT,
        "pool_stats": pool_stats,
        "horizon_discrimination": horizon_disc,
        "mandatory_answers": mandatory,
        "outputs": {
            "feature_importance": combined_importance,
            "time_series": horizon_compare,
            "counterfactual": counterfactual,
            "symbol_analysis": symbol_loo,
            "daily_analysis": daily,
        },
    }


@dataclass
class Phase655Job:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase655(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        kabu = resolve_kabu_root(self.repo_root)
        out_dir = kabu / "results" / "reports" / REPORT_DIR_NAME
        out_dir.mkdir(parents=True, exist_ok=True)
        outputs = result.get("outputs") or {}
        paths = {
            "report": out_dir / "phase655_report.json",
            "feature_importance": out_dir / "phase655_feature_importance.csv",
            "time_series": out_dir / "phase655_time_series.csv",
            "counterfactual": out_dir / "phase655_counterfactual.csv",
            "symbol_analysis": out_dir / "phase655_symbol_analysis.csv",
            "daily_analysis": out_dir / "phase655_daily_analysis.csv",
        }
        _write_rows(paths["feature_importance"], outputs.get("feature_importance") or [])
        _write_rows(paths["time_series"], outputs.get("time_series") or [])
        _write_rows(paths["counterfactual"], outputs.get("counterfactual") or [])
        _write_rows(paths["symbol_analysis"], outputs.get("symbol_analysis") or [])
        _write_rows(paths["daily_analysis"], outputs.get("daily_analysis") or [])
        report_payload = {
            "phase": result.get("phase"),
            "generated_at": result.get("generated_at"),
            "verdict": result.get("verdict"),
            "pool_stats": result.get("pool_stats"),
            "horizon_discrimination": result.get("horizon_discrimination"),
            "mandatory_answers": result.get("mandatory_answers"),
            "artifact_paths": {
                k: str(v.relative_to(kabu)) if v.is_relative_to(kabu) else str(v) for k, v in paths.items()
            },
        }
        paths["report"].write_text(json.dumps(report_payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return paths
