"""First live breadth recon. Predefined V2 metrics only. No large grid. No Day1 retune."""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Optional

from research.futures_context_day1_effect_check_v1 import HORIZON_LABEL, HORIZONS_SEC
from research.futures_x_stock_state_day1_candidate_mechanism_audit_v1.stats import mean, spearman
from research.futures_x_stock_state_interaction_day1_v1.analyze import _clock_metric
from research.market_breadth_leadership_acquisition_v1 import RANKING_TYPES
from research.market_breadth_leadership_acquisition_v1.derive import derive_snapshot
from research.new_causal_information_acquisition_v1.completeness import parse_received_at
from research.run_20260914_day2_futures_plus_first_live_breadth_v1 import (
    BREADTH_METRICS,
    MATERIAL_BPS,
    MIN_SPLIT_N,
    Q1_STATE_METRIC,
    Q2_SELECTOR,
)
from research.run_20260914_day2_futures_plus_first_live_breadth_v1.transport import load_type_records

HORIZON_KEYS = tuple(HORIZON_LABEL[h] for h in HORIZONS_SEC)


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def metric_scalar(snap: dict[str, Any], name: str) -> Optional[float]:
    if name == "LEADERSHIP_DIRECTION":
        return _f(snap.get("LEADERSHIP_DIRECTION"))
    if name == "LEADERSHIP_STRENGTH":
        return _f(snap.get("LEADERSHIP_STRENGTH"))
    if name == "TICK_PRESSURE":
        return _f(snap.get("TICK_PRESSURE"))
    if name == "VOLUME_SURGE_STATE":
        return _f((snap.get("VOLUME_SURGE_STATE") or {}).get("MEDIAN_SURGE"))
    if name == "VALUE_SURGE_STATE":
        return _f((snap.get("VALUE_SURGE_STATE") or {}).get("MEDIAN_SURGE"))
    if name == "SECTOR_DIRECTION":
        return _f(snap.get("SECTOR_DIRECTION"))
    if name == "SECTOR_DISPERSION":
        return _f(snap.get("SECTOR_DISPERSION"))
    if name == "LEADERSHIP_TURNOVER":
        return _f(snap.get("LEADERSHIP_TURNOVER"))
    if name == "SECTOR.RANK_PERSISTENCE":
        return _f((snap.get("SECTOR") or {}).get("sector_leadership_persistence"))
    return None


def _sign_state(v: Optional[float]) -> Optional[int]:
    if v is None:
        return None
    if v > 0:
        return 1
    if v < 0:
        return -1
    return 0


def futures_sign(agreement: Optional[str]) -> Optional[int]:
    if agreement == "BOTH_UP":
        return 1
    if agreement == "BOTH_DOWN":
        return -1
    if agreement == "MIXED":
        return 0
    return None


def _asof_pair(records: list[dict[str, Any]], t: datetime) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    last_i: Optional[int] = None
    for i, rec in enumerate(records):
        dt = parse_received_at(rec.get("received_at"))
        if dt is None:
            continue
        if dt <= t:
            last_i = i
        else:
            break
    if last_i is None:
        return None, None
    prev_i: Optional[int] = None
    for i in range(last_i):
        if parse_received_at(records[i].get("received_at")) is not None:
            prev_i = i
    prev = records[prev_i] if prev_i is not None else None
    return records[last_i], prev


def snapshot_asof(*, tapes: dict[int, list[dict[str, Any]]], t: datetime) -> Optional[dict[str, Any]]:
    by_type: dict[int, Any] = {}
    prev_by_type: dict[int, Any] = {}
    for typ in RANKING_TYPES:
        recs = tapes.get(int(typ)) or []
        cur, prev = _asof_pair(recs, t)
        if cur is None:
            return None
        by_type[int(typ)] = cur.get("raw")
        if prev is not None:
            prev_by_type[int(typ)] = prev.get("raw")
    return derive_snapshot(by_type=by_type, prev_by_type=prev_by_type or None)


def load_tapes(day: str, *, native_root) -> dict[int, list[dict[str, Any]]]:
    return {int(t): load_type_records(day, int(t), native_root=native_root) for t in RANKING_TYPES}


def _market(clock: dict[str, Any], horizon: str, metric: str) -> Optional[float]:
    pack = ((clock.get("market") or {}).get(horizon) or {}).get(metric) or {}
    return _f(pack.get("mean"))


def _bucket_key(b: Any) -> Optional[str]:
    if b is None:
        return None
    x = _f(b)
    if x is not None and float(x) == int(x):
        return str(int(x))
    return str(b)


def _group_mean(rows: list[dict[str, Any]], key: str, y: str) -> dict[str, Any]:
    buckets: dict[str, list[float]] = {}
    for r in rows:
        b = _bucket_key(r.get(key)) if key in ("LEADERSHIP_DIRECTION", "futures_sign") else r.get(key)
        if key not in ("LEADERSHIP_DIRECTION", "futures_sign") and b is not None:
            b = str(b)
        v = _f(r.get(y))
        if b is None or v is None:
            continue
        buckets.setdefault(str(b), []).append(v)
    out = {k: mean(vs) for k, vs in buckets.items()}
    ns = {k: len(vs) for k, vs in buckets.items()}
    return {"means": out, "n": ns}


def _ternary_spread(grouped: dict[str, Any]) -> dict[str, Any]:
    means = dict(grouped.get("means") or {})
    ns = dict(grouped.get("n") or {})
    up = means.get("1")
    down = means.get("-1")
    n_up = int(ns.get("1") or 0)
    n_down = int(ns.get("-1") or 0)
    spread = None
    if up is not None and down is not None:
        spread = float(up) - float(down)
    return {
        "up": up,
        "down": down,
        "zero": means.get("0"),
        "n_up": n_up,
        "n_down": n_down,
        "n_zero": int(ns.get("0") or 0),
        "spread_up_minus_down": spread,
        "usable": bool(n_up >= MIN_SPLIT_N and n_down >= MIN_SPLIT_N and spread is not None),
        "material": bool(
            n_up >= MIN_SPLIT_N
            and n_down >= MIN_SPLIT_N
            and spread is not None
            and abs(float(spread)) > MATERIAL_BPS
        ),
    }


def join_clocks(
    *,
    clocks: list[dict[str, Any]],
    tapes: dict[int, list[dict[str, Any]]],
    day: str,
) -> list[dict[str, Any]]:
    from research.futures_context_day1_effect_check_v1.engine import clock_dt
    from zoneinfo import ZoneInfo

    jst = ZoneInfo("Asia/Tokyo")
    out: list[dict[str, Any]] = []
    for c in clocks:
        hm = c.get("hm")
        if not hm:
            continue
        t = clock_dt(day, tuple(hm)).astimezone(jst)
        snap = snapshot_asof(tapes=tapes, t=t)
        if snap is None:
            continue
        row: dict[str, Any] = {
            "clock": c.get("clock"),
            "agreement_180": c.get("agreement_180"),
            "futures_sign": futures_sign(c.get("agreement_180")),
            "LEADERSHIP_DIRECTION": metric_scalar(snap, "LEADERSHIP_DIRECTION"),
        }
        for name in BREADTH_METRICS:
            row[name] = metric_scalar(snap, name)
        for h in HORIZON_KEYS:
            row[f"EW_MID_{h}"] = _market(c, h, "MID_RETURN_BPS")
            row[f"EW_LONG_{h}"] = _market(c, h, "LONG_EXEC_MARKOUT_BPS")
        row["TOP_BOTTOM_MID_10m"] = _clock_metric(c, Q2_SELECTOR, "10m", "MID_RETURN_BPS")
        row["TOP_BOTTOM_LONG_10m"] = _clock_metric(c, Q2_SELECTOR, "10m", "LONG_EXEC_MARKOUT_BPS")
        out.append(row)
    return out


def evaluate_q1(rows: list[dict[str, Any]]) -> dict[str, Any]:
    y = "EW_MID_10m"
    y_long = "EW_LONG_10m"
    futures = _group_mean(rows, "agreement_180", y)
    breadth = _group_mean(rows, "LEADERSHIP_DIRECTION", y)
    both = _group_mean(
        [
            {**r, "combo": f"{r.get('agreement_180')}|{r.get('LEADERSHIP_DIRECTION')}"}
            for r in rows
        ],
        "combo",
        y,
    )
    ld_all = _ternary_spread(_group_mean(rows, "LEADERSHIP_DIRECTION", y))
    ld_long = _ternary_spread(_group_mean(rows, "LEADERSHIP_DIRECTION", y_long))
    both_down = [r for r in rows if r.get("agreement_180") == "BOTH_DOWN"]
    ld_in_down = _ternary_spread(_group_mean(both_down, "LEADERSHIP_DIRECTION", y))
    match_n = 0
    denom = 0
    for r in rows:
        fs = r.get("futures_sign")
        ld = _sign_state(_f(r.get("LEADERSHIP_DIRECTION")))
        if fs is None or ld is None:
            continue
        denom += 1
        if int(fs) == int(ld):
            match_n += 1
    collinear = (float(match_n) / float(denom)) if denom else None
    residual = bool(ld_in_down.get("material"))
    overall = bool(ld_all.get("material")) and (collinear is None or float(collinear) < 0.80)
    incremental = bool(residual or overall) and len(rows) >= 5
    horizons = {}
    for h in HORIZON_KEYS:
        horizons[h] = {
            "futures_agreement": _group_mean(rows, "agreement_180", f"EW_MID_{h}"),
            "breadth_leadership": _ternary_spread(_group_mean(rows, "LEADERSHIP_DIRECTION", f"EW_MID_{h}")),
            "executable_mean_long": _group_mean(rows, "agreement_180", f"EW_LONG_{h}"),
        }
    corrs = []
    ys = [_f(r.get(y)) for r in rows]
    for name in BREADTH_METRICS:
        xs = [_f(r.get(name)) for r in rows]
        pairs = [(a, b) for a, b in zip(xs, ys) if a is not None and b is not None]
        rho = spearman([p[0] for p in pairs], [p[1] for p in pairs]) if len(pairs) >= 5 else None
        corrs.append({"metric": name, "spearman_vs_EW_MID_10m": rho, "n": len(pairs)})
    return {
        "question": "Does MARKET_BREADTH_LEADERSHIP add market-state information beyond futures?",
        "state_metric": Q1_STATE_METRIC,
        "common_clock_n": len(rows),
        "BOTH_DOWN_clock_n": len(both_down),
        "futures_alone": futures,
        "breadth_alone": breadth,
        "futures_plus_breadth": both,
        "leadership_vs_10m_EW_MID": ld_all,
        "leadership_vs_10m_EW_LONG": ld_long,
        "leadership_within_BOTH_DOWN": ld_in_down,
        "sign_agreement_with_futures": collinear,
        "sign_match_n": match_n,
        "sign_denom": denom,
        "horizons": horizons,
        "metric_spearman_vs_10m_EW_MID": corrs,
        "incremental": incremental,
        "reason": (
            "LEADERSHIP_DIRECTION splits 10m EW MID inside BOTH_DOWN"
            if residual
            else (
                "LEADERSHIP_DIRECTION splits 10m EW MID and is not collinear with AGREEMENT_180S"
                if overall
                else "no material LEADERSHIP_DIRECTION split beyond futures"
            )
        ),
    }


def evaluate_q2(rows: list[dict[str, Any]]) -> dict[str, Any]:
    mid = _ternary_spread(_group_mean(rows, "LEADERSHIP_DIRECTION", "TOP_BOTTOM_MID_10m"))
    lng = _ternary_spread(_group_mean(rows, "LEADERSHIP_DIRECTION", "TOP_BOTTOM_LONG_10m"))
    both_down = [r for r in rows if r.get("agreement_180") == "BOTH_DOWN"]
    mid_down = _ternary_spread(_group_mean(both_down, "LEADERSHIP_DIRECTION", "TOP_BOTTOM_MID_10m"))
    lng_down = _ternary_spread(_group_mean(both_down, "LEADERSHIP_DIRECTION", "TOP_BOTTOM_LONG_10m"))
    changed = bool(mid.get("material") or lng.get("material") or mid_down.get("material") or lng_down.get("material"))
    return {
        "question": "Does breadth state change the OBSERVED_TRADE_N_180S TOP-BOTTOM effect?",
        "selector": Q2_SELECTOR,
        "rank": "TOP16 / BOTTOM16",
        "primary_metric": "TOP-BOTTOM MID / LONG 10m",
        "state_metric": Q1_STATE_METRIC,
        "all_clocks_MID": mid,
        "all_clocks_LONG": lng,
        "BOTH_DOWN_MID": mid_down,
        "BOTH_DOWN_LONG": lng_down,
        "changed": changed,
        "reason": (
            "LEADERSHIP_DIRECTION materially changes TOP-BOTTOM 10m MID or LONG"
            if changed
            else "no material change in TOP-BOTTOM activity edge by LEADERSHIP_DIRECTION"
        ),
    }


def run_recon(*, clocks: list[dict[str, Any]], day: str, native_root) -> dict[str, Any]:
    tapes = load_tapes(day, native_root=native_root)
    rows = join_clocks(clocks=clocks, tapes=tapes, day=day)
    q1 = evaluate_q1(rows)
    q2 = evaluate_q2(rows)
    return {
        "common_clock_n": len(rows),
        "clock_rows": rows,
        "q1": q1,
        "q2": q2,
        "no_large_grid": True,
        "metrics_used": list(BREADTH_METRICS),
        "day1_thresholds_not_reused": True,
    }
