"""
Phase475 — Pre-Breakout Signal Audit (research only).

Investigates features present before sharp intraday moves vs matched non-runners.
No Entry replay — feature discovery only.
"""

from __future__ import annotations

import json
import math
import pickle
import statistics
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _write_csv
from research.phase382_capital_constrained_backtest import _parse_ts
from research.phase436_pullback_guard_redesign_shadow import _price_at_or_before, _stream_events
from research.phase451_entry_shape_tournament import (
    PERIOD_START,
    _build_price_index_to,
    _float,
    _now_iso,
    _optional_float,
)
from research.phase456_entry_features import (
    _window_return,
    compute_high_update_features,
    compute_trend_features,
)
from research.phase456c_vwap_structure_features import compute_vwap_structure_features
from research.phase459_winner_pattern_audit import _board_bucket
from research.phase460_entry_gate_failure_audit import _is_dynamic40, _iter_sessions
from research.phase465b_trend_gate_redesign import _cohens_d, _mi_median_split
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

JST = ZoneInfo("Asia/Tokyo")
PERIOD_END = "20260619"
FOCUS_SYMBOLS = ("3441.T", "6492.T", "7256.T", "7600.T")
RUNNER_O2C_PCT = 5.0
RUNNER_DH_PCT = 7.0
FOCUS_MIN_O2C = 3.0
BREAKOUT_THRESHOLDS = (1.5, 2.0, 3.0)
PRE_OFFSETS_SEC = (30, 60, 120)
PRIMARY_THRESHOLD = 2.0
PRIMARY_OFFSET_SEC = 60
PRICE_BAND_RATIO = (0.65, 1.55)
TIME_BAND_MIN = 20
MAX_CONTROLS_PER_SNAPSHOT = 4

NUMERIC_FEATURES = (
    "board_imbalance",
    "high_update_count_30m",
    "high_update_count_session",
    "high_update_age",
    "consecutive_above_ticks",
    "vwap_dev_pct",
    "vwap_above_ratio",
    "momentum_continuation_score",
    "day_high_distance",
    "r5",
    "r10",
    "r15",
    "r30",
    "tick_rate_60s",
    "tick_rate_5m",
    "trading_value",
    "trading_value_rate",
    "high_update_rate_30m",
    "return_from_open_pct",
    "up_tick_ratio_15m",
    "vwap_structure_score",
)

FEATURE_GROUPS = {
    "board": ("board_imbalance",),
    "vwap": ("vwap_dev_pct", "vwap_above_ratio", "consecutive_above_ticks", "vwap_structure_score"),
    "high_update": (
        "high_update_count_30m",
        "high_update_count_session",
        "high_update_age",
        "high_update_rate_30m",
    ),
    "volume": ("tick_rate_60s", "tick_rate_5m", "trading_value", "trading_value_rate"),
    "momentum": ("momentum_continuation_score", "r5", "r10", "r15", "r30", "up_tick_ratio_15m"),
}

AUDIT_FIELDS = [
    "cohort",
    "symbol",
    "day",
    "breakout_threshold_pct",
    "pre_offset_sec",
    "breakout_time",
    "snapshot_time",
    "open_px",
    "snapshot_px",
    "open_to_close_pct",
    "day_high_from_open_pct",
    "board_bucket",
    *NUMERIC_FEATURES,
]

RANKING_FIELDS = [
    "feature",
    "feature_group",
    "winner_mean",
    "winner_median",
    "loser_mean",
    "loser_median",
    "cohens_d",
    "mutual_information",
    "abs_cohens_d_rank",
    "mi_rank",
]

CANDIDATE_FIELDS = [
    "candidate_id",
    "condition_count",
    "conditions",
    "winner_coverage",
    "loser_false_positive_rate",
    "separation_score",
    "notes",
]


def _session_open(day: str) -> datetime:
    return datetime.strptime(f"{day} 09:00:00", "%Y%m%d %H:%M:%S").replace(tzinfo=JST)


def _day_stats(series: Sequence[tuple[datetime, float]]) -> Optional[dict[str, Any]]:
    if len(series) < 5:
        return None
    day = series[0][0].astimezone(JST).strftime("%Y%m%d")
    open_dt = _session_open(day)
    open_px = _price_at_or_before(series, open_dt) or series[0][1]
    close_px = series[-1][1]
    day_high = max(px for _, px in series)
    if open_px <= 0:
        return None
    o2c = round((close_px - open_px) / open_px * 100.0, 4)
    dh = round((day_high - open_px) / open_px * 100.0, 4)
    return {
        "open_px": open_px,
        "close_px": close_px,
        "day_high": day_high,
        "open_to_close_pct": o2c,
        "day_high_from_open_pct": dh,
    }


def _is_runner(sym: str, stats: Mapping[str, Any]) -> bool:
    o2c = float(stats["open_to_close_pct"])
    dh = float(stats["day_high_from_open_pct"])
    if sym in FOCUS_SYMBOLS and (o2c >= FOCUS_MIN_O2C or dh >= 5.0):
        return True
    return o2c >= RUNNER_O2C_PCT or dh >= RUNNER_DH_PCT


def _find_breakout_ts(
    series: Sequence[tuple[datetime, float]],
    *,
    open_px: float,
    threshold_pct: float,
) -> Optional[datetime]:
    target = open_px * (1.0 + threshold_pct / 100.0)
    for ts, px in series:
        if px >= target:
            return ts
    return None


def _tick_count(series: Sequence[tuple[datetime, float]], *, end_ts: datetime, window_sec: float) -> int:
    start = end_ts - timedelta(seconds=window_sec)
    return sum(1 for ts, _ in series if start <= ts <= end_ts)


def _running_day_high(series: Sequence[tuple[datetime, float]], *, end_ts: datetime) -> float:
    high = 0.0
    for ts, px in series:
        if ts > end_ts:
            break
        high = max(high, px)
    return high


def _build_event_snapshots(
    kabu: Path,
    *,
    symbols: set[str],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    out: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for day, sess in _iter_sessions(kabu):
        if day < PERIOD_START or day > PERIOD_END:
            continue
        path = sess / "small_paper_events.csv"
        for row in _stream_events(path):
            if not _is_dynamic40(row):
                continue
            sym = str(row.get("symbol") or "")
            if sym not in symbols:
                continue
            ts = _parse_ts(str(row.get("event_time") or row.get("entry_time") or ""))
            if ts is None:
                continue
            out[(sym, day)].append(
                {
                    "ts": ts,
                    "trading_value": _optional_float(row.get("trading_value")),
                    "board_imbalance": _optional_float(row.get("entry_order_book_imbalance")),
                    "momentum_continuation_score": _optional_float(
                        row.get("momentum_continuation_score") or row.get("entry_momentum_continuation_score")
                    ),
                    "board_bucket": _board_bucket(row),
                    "return_5min_pct": _optional_float(row.get("entry_rise_5min_pct") or row.get("return_5min_pct")),
                    "return_10min_pct": _optional_float(row.get("entry_rise_10min_pct")),
                    "return_15min_pct": _optional_float(row.get("entry_rise_15min_pct")),
                    "return_30min_pct": _optional_float(row.get("return_30min_pct")),
                }
            )
    for key in out:
        out[key].sort(key=lambda r: r["ts"])
    return dict(out)


def _nearest_snapshot(snaps: Sequence[Mapping[str, Any]], ts: datetime, *, max_sec: float = 120.0) -> Optional[dict[str, Any]]:
    best: Optional[dict[str, Any]] = None
    best_d = max_sec + 1
    for s in snaps:
        d = abs((s["ts"] - ts).total_seconds())
        if d <= max_sec and d < best_d:
            best_d = d
            best = dict(s)
    return best


def _extract_features_at(
    series: Sequence[tuple[datetime, float]],
    *,
    ts: datetime,
    open_px: float,
    event_snaps: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    px = _price_at_or_before(series, ts)
    if px is None or px <= 0:
        return {}
    hu = compute_high_update_features(series, entry_ts=ts)
    trend = compute_trend_features(series, entry_ts=ts, entry_px=px)
    vwap = compute_vwap_structure_features(series, entry_ts=ts, entry_px=px)
    day_high = _running_day_high(series, end_ts=ts)
    dh_dist = round((day_high - px) / px * 100.0, 4) if day_high > 0 else None
    ret_open = round((px - open_px) / open_px * 100.0, 4) if open_px > 0 else None

    tick_60 = _tick_count(series, end_ts=ts, window_sec=60)
    tick_5m = _tick_count(series, end_ts=ts, window_sec=300)
    tick_rate_5m = round(tick_5m / 5.0, 4) if tick_5m else 0.0
    hu30 = float(hu.get("high_update_count_30m") or 0)
    hu_rate = round(hu30 / 30.0, 4)

    snap = _nearest_snapshot(event_snaps, ts)
    tv = snap.get("trading_value") if snap else None
    tv_rate = None
    if snap and tv is not None:
        prior = _nearest_snapshot([s for s in event_snaps if s["ts"] < ts - timedelta(seconds=30)], ts - timedelta(seconds=60), max_sec=90)
        if prior and prior.get("trading_value") is not None:
            dt = max((snap["ts"] - prior["ts"]).total_seconds(), 1.0)
            tv_rate = round((float(tv) - float(prior["trading_value"])) / dt, 2)

    r5 = _window_return(series, entry_ts=ts, minutes=5, entry_px=px)
    r10 = _window_return(series, entry_ts=ts, minutes=10, entry_px=px)
    r15 = _window_return(series, entry_ts=ts, minutes=15, entry_px=px)
    r30 = _window_return(series, entry_ts=ts, minutes=30, entry_px=px)

    return {
        "board_imbalance": snap.get("board_imbalance") if snap else None,
        "board_bucket": snap.get("board_bucket") if snap else "unknown",
        "high_update_count_30m": hu.get("high_update_count_30m"),
        "high_update_count_session": hu.get("high_update_count_session"),
        "high_update_age": hu.get("last_high_update_age_min"),
        "consecutive_above_ticks": vwap.get("consecutive_above_ticks"),
        "vwap_dev_pct": vwap.get("vwap_dev_pct"),
        "vwap_above_ratio": vwap.get("vwap_above_ratio_20tick"),
        "momentum_continuation_score": (
            snap.get("momentum_continuation_score") if snap else None
        ),
        "day_high_distance": dh_dist,
        "r5": r5 if r5 is not None else (snap.get("return_5min_pct") if snap else None),
        "r10": r10 if r10 is not None else (snap.get("return_10min_pct") if snap else None),
        "r15": r15 if r15 is not None else (snap.get("return_15min_pct") if snap else None),
        "r30": r30 if r30 is not None else (snap.get("return_30min_pct") if snap else None),
        "tick_rate_60s": float(tick_60),
        "tick_rate_5m": tick_rate_5m,
        "trading_value": tv,
        "trading_value_rate": tv_rate,
        "high_update_rate_30m": hu_rate,
        "return_from_open_pct": ret_open,
        "up_tick_ratio_15m": trend.get("up_tick_ratio_15m"),
        "vwap_structure_score": vwap.get("vwap_structure_score"),
        "snapshot_px": px,
    }


def _classify_all_days(
    price_idx: Mapping[tuple[str, str], list[tuple[datetime, float]]],
) -> tuple[dict[tuple[str, str], dict[str, Any]], set[tuple[str, str]]]:
    stats_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    runners: set[tuple[str, str]] = set()
    for key, series in price_idx.items():
        sym, day = key
        if day < PERIOD_START or day > PERIOD_END:
            continue
        st = _day_stats(series)
        if st is None:
            continue
        stats_by_key[key] = st
        if _is_runner(sym, st):
            runners.add(key)
    return stats_by_key, runners


def _process_runner_day_inline(
    sym: str,
    day: str,
    threshold: float,
    *,
    price_idx: Mapping[tuple[str, str], list[tuple[datetime, float]]],
    event_snaps: Mapping[tuple[str, str], list[dict[str, Any]]],
    stats_by_key: Mapping[tuple[str, str], Mapping[str, Any]],
) -> list[dict[str, Any]]:
    key = (sym, day)
    series = price_idx.get(key)
    stats = stats_by_key.get(key)
    if not series or not stats:
        return []

    open_px = float(stats["open_px"])
    breakout_ts = _find_breakout_ts(series, open_px=open_px, threshold_pct=threshold)
    if breakout_ts is None:
        return []

    rows: list[dict[str, Any]] = []
    snaps = event_snaps.get(key, [])

    for offset in PRE_OFFSETS_SEC:
        snap_ts = breakout_ts - timedelta(seconds=offset)
        if snap_ts < _session_open(day):
            continue
        feats = _extract_features_at(series, ts=snap_ts, open_px=open_px, event_snaps=snaps)
        if not feats:
            continue
        rows.append(
            {
                "cohort": "winner",
                "symbol": sym,
                "day": day,
                "breakout_threshold_pct": threshold,
                "pre_offset_sec": offset,
                "breakout_time": breakout_ts.isoformat(),
                "snapshot_time": snap_ts.isoformat(),
                "open_px": open_px,
                "open_to_close_pct": stats["open_to_close_pct"],
                "day_high_from_open_pct": stats["day_high_from_open_pct"],
                **feats,
            }
        )
    return rows


def _process_runner_day(args: tuple) -> list[dict[str, Any]]:
    sym, day, cache_path, threshold = args
    with Path(cache_path).open("rb") as fh:
        payload = pickle.load(fh)
    return _process_runner_day_inline(
        sym,
        day,
        threshold,
        price_idx=payload["price_idx"],
        event_snaps=payload["event_snaps"],
        stats_by_key=payload["stats_by_key"],
    )


MAX_CONTROL_SCAN = 48


def _attach_controls(
    winner_rows: Sequence[Mapping[str, Any]],
    *,
    price_idx: Mapping[tuple[str, str], list[tuple[datetime, float]]],
    event_snaps: Mapping[tuple[str, str], list[dict[str, Any]]],
    stats_by_key: Mapping[tuple[str, str], Mapping[str, Any]],
    runners: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    by_day_controls: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for (csym, cday), cstats in stats_by_key.items():
        if (csym, cday) in runners:
            continue
        by_day_controls[cday].append((csym, dict(cstats)))

    feat_cache: dict[tuple[str, str, str], dict[str, Any]] = {}
    out = [dict(r) for r in winner_rows]
    seen_control: set[tuple[str, str, str, float, int]] = set()

    def _cached_feats(csym: str, cday: str, snap_ts: datetime, copen: float) -> Optional[dict[str, Any]]:
        ck = (csym, cday, snap_ts.isoformat())
        if ck not in feat_cache:
            cseries = price_idx.get((csym, cday))
            if not cseries:
                return None
            feats = _extract_features_at(
                cseries,
                ts=snap_ts,
                open_px=copen,
                event_snaps=event_snaps.get((csym, cday), []),
            )
            if not feats:
                return None
            feat_cache[ck] = feats
        return feat_cache[ck]

    primary_winners = [
        r
        for r in winner_rows
        if float(r.get("breakout_threshold_pct") or 0) == PRIMARY_THRESHOLD
        and int(r.get("pre_offset_sec") or 0) == PRIMARY_OFFSET_SEC
    ]
    groups: dict[tuple[str, str, float, int], list[Mapping[str, Any]]] = defaultdict(list)
    for wr in primary_winners:
        groups[(str(wr.get("day")), str(wr.get("snapshot_time")), float(wr.get("breakout_threshold_pct") or 0), int(wr.get("pre_offset_sec") or 0))].append(wr)

    for (day, snap_iso, threshold, pre_off), group in groups.items():
        wr = group[0]
        snap_ts = _parse_ts(snap_iso)
        if snap_ts is None:
            continue
        px = _float(wr.get("snapshot_px"))
        if px <= 0:
            continue
        bb = str(wr.get("board_bucket") or "unknown")
        added = 0
        for csym, cstats in by_day_controls.get(day, [])[:MAX_CONTROL_SCAN]:
            if added >= MAX_CONTROLS_PER_SNAPSHOT:
                break
            ck = (csym, day, snap_iso, threshold, pre_off)
            if ck in seen_control:
                continue
            copen = float(cstats["open_px"])
            cfeats = _cached_feats(csym, day, snap_ts, copen)
            if not cfeats:
                continue
            cpx = float(cfeats["snapshot_px"])
            if not (PRICE_BAND_RATIO[0] * px <= cpx <= PRICE_BAND_RATIO[1] * px):
                continue
            cbb = str(cfeats.get("board_bucket") or "unknown")
            if bb != "unknown" and cbb != "unknown" and bb != cbb:
                continue
            ret = cfeats.get("return_from_open_pct")
            if ret is not None and float(ret) >= threshold:
                continue
            seen_control.add(ck)
            out.append(
                {
                    "cohort": "loser",
                    "symbol": csym,
                    "day": day,
                    "breakout_threshold_pct": threshold,
                    "pre_offset_sec": pre_off,
                    "breakout_time": "",
                    "snapshot_time": snap_iso,
                    "open_px": copen,
                    "open_to_close_pct": cstats["open_to_close_pct"],
                    "day_high_from_open_pct": cstats["day_high_from_open_pct"],
                    **cfeats,
                }
            )
            added += 1
    return out


def _feature_values(rows: Sequence[Mapping[str, Any]], feat: str) -> list[float]:
    out: list[float] = []
    for r in rows:
        v = _optional_float(r.get(feat))
        if v is not None:
            out.append(float(v))
    return out


def _compare_rows(
    winners: Sequence[Mapping[str, Any]],
    losers: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for feat in NUMERIC_FEATURES:
        wv = _feature_values(winners, feat)
        lv = _feature_values(losers, feat)
        if len(wv) < 3 or len(lv) < 3:
            continue
        group = "other"
        for gname, gfeats in FEATURE_GROUPS.items():
            if feat in gfeats:
                group = gname
                break
        rows.append(
            {
                "feature": feat,
                "feature_group": group,
                "winner_mean": round(statistics.mean(wv), 4),
                "winner_median": round(statistics.median(wv), 4),
                "loser_mean": round(statistics.mean(lv), 4),
                "loser_median": round(statistics.median(lv), 4),
                "cohens_d": _cohens_d(wv, lv),
                "mutual_information": _mi_median_split(wv, lv),
            }
        )
    rows = [r for r in rows if r.get("cohens_d") is not None]
    rows.sort(key=lambda r: abs(float(r["cohens_d"])), reverse=True)
    for i, r in enumerate(rows, start=1):
        r["abs_cohens_d_rank"] = i
    mi_sorted = sorted(rows, key=lambda r: float(r.get("mutual_information") or 0), reverse=True)
    mi_rank = {r["feature"]: i + 1 for i, r in enumerate(mi_sorted)}
    for r in rows:
        r["mi_rank"] = mi_rank.get(r["feature"])
    return rows


def _group_contribution(ranking: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    out = {g: 0.0 for g in FEATURE_GROUPS}
    out["other"] = 0.0
    for r in ranking:
        g = str(r.get("feature_group") or "other")
        out[g] = out.get(g, 0.0) + abs(float(r.get("cohens_d") or 0))
    return {k: round(v, 4) for k, v in out.items()}


def _build_gate_candidates(
    ranking: Sequence[Mapping[str, Any]],
    winners: Sequence[Mapping[str, Any]],
    losers: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    top = list(ranking[:6])
    if not top:
        return []

    def _eval(conditions: list[tuple[str, str, float]]) -> dict[str, Any]:
        w_hit = 0
        l_hit = 0
        for r in winners:
            ok = True
            for feat, op, thr in conditions:
                v = _optional_float(r.get(feat))
                if v is None:
                    ok = False
                    break
                if op == "lt" and not (v < thr):
                    ok = False
                if op == "gt" and not (v > thr):
                    ok = False
                if op == "lte" and not (v <= thr):
                    ok = False
                if op == "gte" and not (v >= thr):
                    ok = False
            if ok:
                w_hit += 1
        for r in losers:
            ok = True
            for feat, op, thr in conditions:
                v = _optional_float(r.get(feat))
                if v is None:
                    ok = False
                    break
                if op == "lt" and not (v < thr):
                    ok = False
                if op == "gt" and not (v > thr):
                    ok = False
                if op == "lte" and not (v <= thr):
                    ok = False
                if op == "gte" and not (v >= thr):
                    ok = False
            if ok:
                l_hit += 1
        w_cov = round(w_hit / len(winners), 4) if winners else 0.0
        l_fp = round(l_hit / len(losers), 4) if losers else 0.0
        sep = round(w_cov - l_fp, 4)
        return {"winner_coverage": w_cov, "loser_false_positive_rate": l_fp, "separation_score": sep}

    candidates: list[dict[str, Any]] = []

    def _pick_op(feat: str, w_med: float, l_med: float) -> tuple[str, float]:
        wv = _feature_values(winners, feat)
        if not wv:
            return "gt", w_med
        thr = round(statistics.median(wv), 4)
        if w_med < l_med:
            return "lt", thr
        return "gt", thr

    if len(top) >= 2:
        f1, f2 = top[0]["feature"], top[1]["feature"]
        op1, t1 = _pick_op(f1, float(top[0]["winner_median"]), float(top[0]["loser_median"]))
        op2, t2 = _pick_op(f2, float(top[1]["winner_median"]), float(top[1]["loser_median"]))
        conds = [(f1, op1, t1), (f2, op2, t2)]
        ev = _eval(conds)
        candidates.append(
            {
                "candidate_id": "PB2-top2",
                "condition_count": 2,
                "conditions": f"{f1} {op1} {t1} AND {f2} {op2} {t2}",
                "notes": "Top-2 |Cohen d| features, winner-median split",
                **ev,
            }
        )

    if len(top) >= 3:
        f1, f2, f3 = top[0]["feature"], top[1]["feature"], top[2]["feature"]
        specs = []
        for i, f in enumerate((f1, f2, f3)):
            op, t = _pick_op(f, float(top[i]["winner_median"]), float(top[i]["loser_median"]))
            specs.append((f, op, t))
        ev = _eval(specs)
        cond_str = " AND ".join(f"{a} {b} {c}" for a, b, c in specs)
        candidates.append(
            {
                "candidate_id": "PB3-top3",
                "condition_count": 3,
                "conditions": cond_str,
                "notes": "Top-3 |Cohen d| features, winner-median split",
                **ev,
            }
        )

    for r in top[:4]:
        feat = str(r["feature"])
        op, thr = _pick_op(feat, float(r["winner_median"]), float(r["loser_median"]))
        ev = _eval([(feat, op, thr)])
        candidates.append(
            {
                "candidate_id": f"PB1-{feat}",
                "condition_count": 1,
                "conditions": f"{feat} {op} {thr}",
                "notes": "Single-feature gate from ranking",
                **ev,
            }
        )

    candidates.sort(key=lambda r: float(r.get("separation_score") or 0), reverse=True)
    return candidates


def _verdict(
    *,
    ranking: Sequence[Mapping[str, Any]],
    winner_count: int,
    loser_count: int,
    best_candidate: Optional[Mapping[str, Any]],
) -> str:
    if winner_count < 10 or loser_count < 20:
        return "needs_more_data"
    if not ranking:
        return "no_predictive_signal"
    top_d = abs(float(ranking[0].get("cohens_d") or 0))
    top_mi = float(ranking[0].get("mutual_information") or 0)
    sep = float((best_candidate or {}).get("separation_score") or 0)
    if top_d >= 0.45 and top_mi >= 0.02 and sep >= 0.15:
        return "pre_breakout_signal_found"
    if top_d < 0.25 and sep < 0.08:
        return "no_predictive_signal"
    if top_d >= 0.35 or sep >= 0.12:
        return "pre_breakout_signal_found"
    return "needs_more_data"


def run_phase475(
    *,
    repo_root: Path,
    parallel: bool = False,
    max_workers: int = 4,
) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)
    print(f"phase475 price_idx keys: {len(price_idx)}", flush=True)

    symbols = {sym for sym, _ in price_idx}
    print("phase475 building event snapshots...", flush=True)
    event_snaps = _build_event_snapshots(kabu, symbols=symbols)
    print(f"phase475 event snapshot keys: {len(event_snaps)}", flush=True)
    stats_by_key, runners = _classify_all_days(price_idx)
    print(f"phase475 runners: {len(runners)} / days: {len(stats_by_key)}", flush=True)

    winner_only: list[dict[str, Any]] = []
    tasks = [(sym, day, th) for (sym, day) in sorted(runners) for th in BREAKOUT_THRESHOLDS]
    total = len(tasks)

    if parallel and tasks:
        cache_path = resolve_reports_dir(repo_root) / ".phase475_cache" / "payload.pkl"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with cache_path.open("wb") as fh:
            pickle.dump(
                {"price_idx": price_idx, "event_snaps": event_snaps, "stats_by_key": stats_by_key},
                fh,
                protocol=pickle.HIGHEST_PROTOCOL,
            )
        ptasks = [(sym, day, str(cache_path), th) for sym, day, th in tasks]
        with ProcessPoolExecutor(max_workers=max_workers) as ex:
            futs = [ex.submit(_process_runner_day, t) for t in ptasks]
            for i, fut in enumerate(as_completed(futs), start=1):
                winner_only.extend(fut.result())
                if i % 50 == 0 or i == total:
                    print(f"phase475 winner extract {i}/{total}", flush=True)
    else:
        for i, (sym, day, th) in enumerate(tasks, start=1):
            winner_only.extend(
                _process_runner_day_inline(
                    sym,
                    day,
                    th,
                    price_idx=price_idx,
                    event_snaps=event_snaps,
                    stats_by_key=stats_by_key,
                )
            )
            if i % 50 == 0 or i == total:
                print(f"phase475 winner extract {i}/{total}", flush=True)

    print(f"phase475 winner snapshots: {len(winner_only)}", flush=True)
    audit_rows = _attach_controls(
        winner_only,
        price_idx=price_idx,
        event_snaps=event_snaps,
        stats_by_key=stats_by_key,
        runners=runners,
    )
    print(f"phase475 audit rows (with controls): {len(audit_rows)}", flush=True)

    primary = [
        r
        for r in audit_rows
        if float(r.get("breakout_threshold_pct") or 0) == PRIMARY_THRESHOLD
        and int(r.get("pre_offset_sec") or 0) == PRIMARY_OFFSET_SEC
    ]
    winners = [r for r in primary if r.get("cohort") == "winner"]
    losers = [r for r in primary if r.get("cohort") == "loser"]
    print(f"phase475 primary snapshots: winners={len(winners)} losers={len(losers)}", flush=True)

    ranking = _compare_rows(winners, losers)
    group_contrib = _group_contribution(ranking)
    candidates = _build_gate_candidates(ranking, winners, losers)
    best_cand = candidates[0] if candidates else None

    top_d = ranking[:10]
    top_mi = sorted(ranking, key=lambda r: float(r.get("mutual_information") or 0), reverse=True)[:10]

    trend_lag = (
        "Current T-B (consecutive_above_ticks>=20 AND vwap_dev_pct>0) requires extended price>VWAP "
        "and many consecutive up-ticks — states only reachable after initial breakout acceleration, "
        "not at pre-breakout compression/coiling phase."
    )

    verdict = _verdict(
        ranking=ranking,
        winner_count=len(winners),
        loser_count=len(losers),
        best_candidate=best_cand,
    )

    overfit = (
        len(winners) < 25
        or float(group_contrib.get("board", 0)) > 2.0 * float(group_contrib.get("momentum", 0) or 1)
        or any(sym in str(r.get("symbol")) for r in winners for sym in FOCUS_SYMBOLS)
        and len({r.get("day") for r in winners}) <= 4
    )

    replay_ready = (
        verdict == "pre_breakout_signal_found"
        and best_cand is not None
        and float(best_cand.get("separation_score") or 0) >= 0.15
        and float(best_cand.get("winner_coverage") or 0) >= 0.35
        and not overfit
    )

    mandatory = {
        "1_top_changing_feature": ranking[0]["feature"] if ranking else None,
        "2_cohens_d_top10": [{"feature": r["feature"], "d": r["cohens_d"]} for r in top_d],
        "3_mi_top10": [{"feature": r["feature"], "mi": r["mutual_information"]} for r in top_mi],
        "4_board_contribution": group_contrib.get("board"),
        "5_vwap_contribution": group_contrib.get("vwap"),
        "6_high_update_contribution": group_contrib.get("high_update"),
        "7_volume_contribution": group_contrib.get("volume"),
        "8_momentum_contribution": group_contrib.get("momentum"),
        "9_trend_conditions_lag_reason": trend_lag,
        "10_best_pre_breakout_candidate": best_cand,
        "11_two_condition_candidate": next((c for c in candidates if c.get("condition_count") == 2), None),
        "12_three_condition_candidate": next((c for c in candidates if c.get("condition_count") == 3), None),
        "13_overfit_risk": overfit,
        "14_replay_candidate": replay_ready,
        "15_next_actions": _next_actions(verdict, replay_ready, best_cand, overfit),
        "verdict": verdict,
        "runner_symbol_days": len(runners),
        "primary_winner_snapshots": len(winners),
        "primary_loser_snapshots": len(losers),
        "focus_symbol_runner_days": [
            {"symbol": s, "day": d, **stats_by_key[(s, d)]}
            for (s, d) in sorted(runners)
            if s in FOCUS_SYMBOLS
        ],
        "group_contribution_abs_cohens_d_sum": group_contrib,
    }

    return {
        "generated_at": _now_iso(),
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "verdict": verdict,
        "mandatory_answers": mandatory,
        "_audit_rows": audit_rows,
        "_ranking_rows": ranking,
        "_candidate_rows": candidates,
    }


def _next_actions(
    verdict: str,
    replay_ready: bool,
    best_cand: Optional[Mapping[str, Any]],
    overfit: bool,
) -> list[str]:
    actions = [f"Verdict: {verdict}"]
    if verdict == "pre_breakout_signal_found":
        actions.append("Design Phase476 frozen pre-breakout gate audit (no runtime yet)")
        if best_cand:
            actions.append(f"Lead candidate: {best_cand.get('candidate_id')} — {best_cand.get('conditions')}")
    elif verdict == "no_predictive_signal":
        actions.append("Do not pursue Trend entry redesign on current feature set")
        actions.append("Keep Pullback v2 primary; revisit with richer board/tick features")
    else:
        actions.append("Extend sample window or add board tick archive before gate design")
    actions.append(f"Replay candidate: {replay_ready}")
    actions.append(f"Overfit risk: {overfit}")
    return actions


@dataclass
class Phase475Job:
    repo_root: Path
    parallel: bool = False
    max_workers: int = 4

    def run(self) -> dict[str, Any]:
        return run_phase475(
            repo_root=self.repo_root,
            parallel=self.parallel,
            max_workers=self.max_workers,
        )

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "audit_csv": reports / "phase475_pre_breakout_signal_audit.csv",
            "ranking_csv": reports / "phase475_pre_breakout_feature_ranking.csv",
            "candidates_csv": reports / "phase475_pre_breakout_candidates.csv",
            "summary": reports / "phase475_summary.json",
        }
        _write_csv(paths["audit_csv"], AUDIT_FIELDS, list(result.get("_audit_rows") or []))
        _write_csv(paths["ranking_csv"], RANKING_FIELDS, list(result.get("_ranking_rows") or []))
        _write_csv(paths["candidates_csv"], CANDIDATE_FIELDS, list(result.get("_candidate_rows") or []))
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        doc_root = self.repo_root / "kabu_native"
        if not (doc_root / "docs").is_dir():
            doc_root = self.repo_root
        report = doc_root / "docs" / "operations" / "phase475_pre_breakout_signal_audit.md"
        self._write_report(report, result)
        paths["report"] = report
        return paths

    def _write_report(self, report: Path, result: Mapping[str, Any]) -> None:
        m = result.get("mandatory_answers") or {}
        ranking = list(result.get("_ranking_rows") or [])
        candidates = list(result.get("_candidate_rows") or [])
        lines = [
            "# Phase475 — Pre-Breakout Signal Audit",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            f"**Period:** {result.get('period_start')}–{result.get('period_end')}",
            f"**Runners (symbol-days):** {m.get('runner_symbol_days')}",
            f"**Primary analysis:** +{PRIMARY_THRESHOLD}% breakout, {PRIMARY_OFFSET_SEC}s pre-window",
            "",
            "## 必須回答",
            "",
            "| # | 項目 | 結果 |",
            "|---|------|------|",
            f"| 1 | 急騰前に最も変化する特徴量 | **{m.get('1_top_changing_feature')}** |",
            f"| 2 | Cohen's d 上位 | 下表参照 |",
            f"| 3 | 情報利得上位 | 下表参照 |",
            f"| 4 | board寄与 | **{m.get('4_board_contribution')}** |",
            f"| 5 | VWAP寄与 | **{m.get('5_vwap_contribution')}** |",
            f"| 6 | 高値更新寄与 | **{m.get('6_high_update_contribution')}** |",
            f"| 7 | 出来高急増寄与 | **{m.get('7_volume_contribution')}** |",
            f"| 8 | Momentum寄与 | **{m.get('8_momentum_contribution')}** |",
            f"| 9 | Trend後追い理由 | {m.get('9_trend_conditions_lag_reason')} |",
            f"| 10 | 最有力Pre-Breakout候補 | **{(m.get('10_best_pre_breakout_candidate') or {}).get('conditions')}** |",
            f"| 11 | 2条件候補 | **{(m.get('11_two_condition_candidate') or {}).get('conditions')}** |",
            f"| 12 | 3条件候補 | **{(m.get('12_three_condition_candidate') or {}).get('conditions')}** |",
            f"| 13 | 過学習リスク | **{m.get('13_overfit_risk')}** |",
            f"| 14 | Replay候補 | **{m.get('14_replay_candidate')}** |",
            f"| 15 | 次アクション | {'; '.join(m.get('15_next_actions') or [])} |",
            "",
            "## Cohen's d Ranking (primary window)",
            "",
            "| rank | feature | group | d | winner_med | loser_med | MI |",
            "|---:|---|---|---:|---:|---:|---:|",
        ]
        for r in ranking[:15]:
            lines.append(
                f"| {r.get('abs_cohens_d_rank')} | {r.get('feature')} | {r.get('feature_group')} "
                f"| {r.get('cohens_d')} | {r.get('winner_median')} | {r.get('loser_median')} | {r.get('mutual_information')} |"
            )
        lines.extend(["", "## Gate Candidates (discovery only — no replay)", ""])
        for c in candidates[:8]:
            lines.append(
                f"- **{c.get('candidate_id')}** ({c.get('condition_count')} cond): `{c.get('conditions')}` "
                f"— coverage={c.get('winner_coverage')} fp={c.get('loser_false_positive_rate')} sep={c.get('separation_score')}"
            )
        lines.extend(
            [
                "",
                "## Focus symbols (runner days)",
                "",
            ]
        )
        for row in m.get("focus_symbol_runner_days") or []:
            lines.append(
                f"- {row.get('symbol')} {row.get('day')}: o2c={row.get('open_to_close_pct')}% "
                f"dh={row.get('day_high_from_open_pct')}%"
            )
        lines.extend(["", f"**判定:** `{result.get('verdict')}`"])
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("\n".join(lines), encoding="utf-8")
