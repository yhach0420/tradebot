"""Phase685 — No-progress ENTRY root cause discovery (research only)."""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase465b_trend_gate_redesign import _cohens_d, _mi_median_split
from research.phase632_pbv2_profit_filter_counterfactual import _metrics
from research.phase634_pbv2_only_rise5_full_period import _disk_usage_pct
from research.phase671_early_stop_feature_discovery import _is_leaky_feature
from research.phase672_pre_entry_microsequence import (
    BIG_WINNER_YEN,
    MICROSEQ_FEATURES,
    SMALL_PAPER_ROOT,
    _attach_microsequence,
    _build_price_index_canonical,
    _day_iso,
    _day_key,
    _enrich_trade_labels,
    _load_canonical_trades_with_session,
    _load_signal_index,
    _sym_t,
)
from research.phase674_microsequence_candidate_robustness import (
    SUPPLEMENTAL_DAYS,
    _extend_price_index,
    _load_trades_for_days,
)
from research.phase634_pbv2_only_rise5_full_period import _iter_events
from research.phase631_profit_source_attribution import _num, _parse_iso
from research.structural_trade_normalize import resolve_kabu_root
from small_paper.ihc_shadow_counterfactual import (
    DEFAULT_SHADOW_CFG,
    build_session_price_index,
    enrich_trades_with_shadow,
    evaluate_trade_shadow_fields,
    load_session_canonical_trades,
)

VERDICT_FOUND = "FOUND_NO_PROGRESS_SIGNAL"
VERDICT_RANKING = "RANKING_SHADOW_CANDIDATE"
VERDICT_REJECT = "REJECT_SHADOW_CANDIDATE"
VERDICT_WEAK = "WEAK_SIGNAL"
VERDICT_NO_ROBUST = "NO_ROBUST_SIGNAL"
VERDICT_DATA_GAP = "DATA_INSUFFICIENT"

REPORT_DIR = Path(__file__).resolve().parents[2] / "results" / "reports" / "phase685_no_progress_entry_discovery"
NATIVE_ROOT = Path(__file__).resolve().parents[2]
PM_DIR = NATIVE_ROOT / "results" / "small_paper" / "20260710" / "live_session_122525"
AM_DIR = NATIVE_ROOT / "results" / "small_paper" / "20260710" / "live_session_084821"
DAY_710 = "2026-07-10"
RECENT_DAYS = ("2026-07-07", "2026-07-08", "2026-07-09")
ALL_710_DAYS = RECENT_DAYS + (DAY_710,)

ACCEPT_ALIAS = {
    "r5": "entry_rise_5min_pct",
    "r10": "entry_rise_10min_pct",
    "r15": "entry_rise_15min_pct",
    "r30": "r30_sec",
    "r60": "r60_sec",
    "r120": "r120_sec",
    "continuation_quality": "continuation_quality_score",
    "entry_score_v2": "entry_expectancy_score_v2",
    "distance_from_day_high": "day_high_distance_pct",
    "distance_from_vwap": "entry_vwap_dev_pct",
    "distance_from_recent_high": "entry_near_day_high_pct",
    "spread_bps": "spread_bps",
    "entry_imbalance_percentile": "entry_imbalance_percentile",
    "momentum_continuation_score": "momentum_continuation_score",
    "push_pre_entry_sec": "push_pre_entry_sec",
    "price_age_sec": "price_age_sec",
    "board_age_sec": "board_age_sec",
    "update_count_before_entry": "update_count_before_entry",
    "live_feature_complete": "live_feature_complete",
    "quality_fallback_path": "quality_fallback_path",
    "position_slot_before": "position_slot_before",
    "max_concurrent_positions": "max_concurrent_positions",
}

FEATURE_CATALOG: tuple[str, ...] = (
    *(f"alias_{k}" for k in ACCEPT_ALIAS),
    *MICROSEQ_FEATURES,
    "readiness_bounce_from_recent_low_accept",
    "microseq_bounce_from_recent_low",
    "microseq_fall_from_recent_high",
    "microseq_slope_5min",
    "slope_5min",
    "entry_expectancy_score_v2",
    "continuation_quality_score",
    "momentum_continuation_score",
    "entry_rise_5min_pct",
    "entry_rise_10min_pct",
    "entry_rise_15min_pct",
    "entry_rise_30min_pct",
    "r30_sec",
    "r60_sec",
    "r120_sec",
    "day_high_distance_pct",
    "entry_vwap_dev_pct",
    "entry_near_day_high_pct",
    "entry_imbalance_percentile",
    "spread_bps",
    "price_age_sec",
    "board_age_sec",
    "update_count_before_entry",
    "trading_value",
    "liquidity_burst",
    "push_pre_entry_sec",
    "price_history_point_count",
    "price_history_span_sec",
)


def _pnl(t: Mapping[str, Any]) -> float:
    return float(_num(t.get("pnl_yen_100")) or 0)


def _is_winner(t: Mapping[str, Any]) -> bool:
    return _pnl(t) > 0


def _is_loser(t: Mapping[str, Any]) -> bool:
    return _pnl(t) < 0


def _is_big_winner(t: Mapping[str, Any]) -> bool:
    return _pnl(t) >= BIG_WINNER_YEN


def _outcome_label(t: Mapping[str, Any]) -> str:
    reason = str(t.get("exit_reason") or "")
    pnl = _pnl(t)
    if reason == "no_progress_exit":
        return "B_benign_no_progress" if pnl > 0 else "A_harmful_no_progress"
    if pnl > 0 and reason in ("trailing_mfe_exit", "morning_session_close", "afternoon_session_close"):
        return "C_successful_continuation"
    if reason == "stop_hit":
        return "D_stop_loss"
    return "E_other"


def _session_kind(t: Mapping[str, Any]) -> str:
    if str(t.get("session") or "").upper() in ("AM", "PM"):
        return str(t["session"]).upper()
    sess = str(t.get("session") or t.get("session_dir") or "")
    return "PM" if "122525" in sess else "AM"


def _percentile(vals: Sequence[float], p: float) -> Optional[float]:
    if not vals:
        return None
    s = sorted(vals)
    idx = min(len(s) - 1, max(0, int(round((len(s) - 1) * p))))
    return s[idx]


def _rank_biserial(pos: Sequence[float], neg: Sequence[float]) -> Optional[float]:
    if not pos or not neg:
        return None
    combined = [(v, 1) for v in pos] + [(v, 0) for v in neg]
    combined.sort(key=lambda x: x[0])
    n1, n0 = len(pos), len(neg)
    r1 = sum(i + 1 for i, (_, y) in enumerate(combined) if y == 1)
    u1 = r1 - n1 * (n1 + 1) / 2
    return round(2 * u1 / (n1 * n0) - 1, 4)


def _roc_auc(pos: Sequence[float], neg: Sequence[float], *, higher_is_positive: bool) -> Optional[float]:
    if len(pos) < 2 or len(neg) < 2:
        return None
    if not higher_is_positive:
        pos = [-x for x in pos]
        neg = [-x for x in neg]
    combined = [(v, 1) for v in pos] + [(v, 0) for v in neg]
    combined.sort(key=lambda x: x[0])
    n1, n0 = len(pos), len(neg)
    r1 = sum(i + 1 for i, (_, y) in enumerate(combined) if y == 1)
    u1 = r1 - n1 * (n1 + 1) / 2
    return round(u1 / (n1 * n0), 4)


def _load_710_sessions() -> list[dict[str, Any]]:
    am, _ = load_session_canonical_trades(AM_DIR, session_label="AM", expected_count=36, expected_pnl=8600.0)
    pm, _ = load_session_canonical_trades(PM_DIR, session_label="PM", expected_count=38, expected_pnl=-36900.0)
    for t in am + pm:
        t["day"] = DAY_710
        t["dataset"] = "forward_710"
        t["session_kind"] = _session_kind(t)
        t["session_bucket"] = t["session_kind"]
    return am + pm


def _iter_events_local(sess_dir: Path):
    yield from _iter_events(sess_dir)


def _signal_index_for_dirs(day_dirs: Sequence[Path]) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    out: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for day_dir in day_dirs:
        if not day_dir.is_dir():
            continue
        day_iso = _day_iso(day_dir.name)
        for sess_dir in sorted(day_dir.glob("live_session_*")):
            path = sess_dir / "entry_scan_audit.jsonl"
            if not path.is_file():
                continue
            with path.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if row.get("audit_type") != "entry_notify":
                        continue
                    if str(row.get("entry_decision") or "").lower() not in ("true", "1"):
                        continue
                    sym = _sym_t(str(row.get("symbol") or ""))
                    out[(day_iso, sess_dir.name, sym)].append(row)
    for key in out:
        out[key].sort(key=lambda r: str(r.get("entry_signal_ts") or ""))
    return dict(out)


def _accept_index_for_dirs(day_dirs: Sequence[Path]) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    out: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for day_dir in day_dirs:
        if not day_dir.is_dir():
            continue
        day_iso = _day_iso(day_dir.name)
        for sess_dir in sorted(day_dir.glob("live_session_*")):
            if not sess_dir.is_dir():
                continue
            session = sess_dir.name
            for e in _iter_events_local(sess_dir):
                if e.get("event_type") != "accepted":
                    continue
                sym = _sym_t(str(e.get("symbol") or ""))
                et = str(e.get("entry_time") or "")
                out[(day_iso, session, sym, et)] = dict(e)
    return out


def _merge_accept_fields(trades: Sequence[Mapping[str, Any]], accept_idx: Mapping[tuple, Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in trades:
        row = dict(t)
        day = str(row.get("day") or "")
        session = str(row.get("session_dir") or row.get("session") or "")
        if "live_session_" not in session:
            session = f"live_session_{'122525' if _session_kind(row) == 'PM' else '084821'}"
        sym = _sym_t(str(row.get("symbol") or ""))
        et = str(row.get("entry_time") or "")
        acc = accept_idx.get((day, session, sym, et))
        if acc is None:
            for k, v in accept_idx.items():
                if k[0] == day and k[2] == sym and k[3] == et:
                    acc = v
                    break
        if acc:
            for k, v in acc.items():
                if k not in row or row.get(k) in (None, ""):
                    row[k] = v
        for alias, src in ACCEPT_ALIAS.items():
            if row.get(src) not in (None, ""):
                row.setdefault(alias, row.get(src))
        out.append(row)
    return out


def _add_price_history_meta(trades: list[dict[str, Any]], price_idx: Mapping[str, list[tuple[float, float]]]) -> None:
    for t in trades:
        sym = str(t.get("symbol") or "")
        et = _parse_iso(str(t.get("entry_time") or ""))
        if et is None:
            continue
        ts = et.timestamp()
        ring = price_idx.get(sym, [])
        pre = [(a, b) for a, b in ring if ts - 900 <= a <= ts]
        t["price_history_point_count"] = len(pre)
        t["price_history_span_sec"] = round(ts - pre[0][0], 2) if len(pre) >= 2 else 0.0


from small_paper.microsequence_pre_entry import compute_microsequence_pre_entry_features
from small_paper.pbv2_flat_band_entry_guard import would_block_flat_band_mainline
from types import SimpleNamespace

MAINLINE_CFG = SimpleNamespace(
    pbv2_flat_band_mainline_enabled=True,
    pbv2_flat_band_shadow_enabled=False,
    pbv2_flat_band_shadow_apply_pool="PBV2_ONLY",
    pbv2_flat_band_shadow_rise5_flat_min_pct=0.0,
    pbv2_flat_band_shadow_rise5_flat_max_pct=0.5,
    pbv2_flat_band_shadow_rise10_flat_min_pct=-0.5,
    pbv2_flat_band_shadow_rise10_flat_max_pct=0.5,
    pbv2_flat_band_shadow_overheat_rise5_pct=2.0,
)


def _price_at_or_before(ring: Sequence[tuple[float, float]], ts: float) -> Optional[float]:
    best: Optional[float] = None
    for t, px in ring:
        if t <= ts:
            best = px
        else:
            break
    return best


def _return_from_ring(ring: Sequence[tuple[float, float]], *, entry_ts: float, entry_px: float, sec: float) -> Optional[float]:
    px0 = _price_at_or_before(ring, entry_ts - sec)
    if px0 is None or px0 <= 0 or entry_px <= 0:
        return None
    return round((entry_px - px0) / px0 * 100.0, 4)


def _high_update_failure_from_ring(ring: Sequence[tuple[float, float]], *, entry_ts: float, window_sec: float = 120.0) -> Optional[int]:
    pts = [(t, px) for t, px in ring if entry_ts - window_sec <= t <= entry_ts]
    if len(pts) < 3:
        return None
    running_high = pts[0][1]
    failures = 0
    for _, px in pts[1:]:
        if px >= running_high:
            running_high = px
        else:
            failures += 1
    return failures


def _ring_features(ring: Sequence[tuple[float, float]], *, entry_ts: float, entry_px: float) -> dict[str, Any]:
    micro = compute_microsequence_pre_entry_features(ring, entry_ts=entry_ts, entry_px=entry_px)
    hi_fail = _high_update_failure_from_ring(ring, entry_ts=entry_ts)
    pts = [px for t, px in ring if entry_ts - 120 <= t <= entry_ts]
    range_pct = None
    if len(pts) >= 2 and entry_px > 0:
        range_pct = round((max(pts) - min(pts)) / entry_px * 100.0, 4)
    return {
        **micro,
        "microseq_bounce_from_recent_low": micro.get("bounce_from_recent_low"),
        "microseq_fall_from_recent_high": micro.get("fall_from_recent_high"),
        "microseq_slope_5min": micro.get("slope_5min"),
        "slope_5min": micro.get("slope_5min"),
        "bounce_from_recent_low": micro.get("bounce_from_recent_low"),
        "fall_from_recent_high": micro.get("fall_from_recent_high"),
        "high_update_failure_count": hi_fail,
        "price_return_120s": _return_from_ring(ring, entry_ts=entry_ts, entry_px=entry_px, sec=120),
        "price_return_60s": _return_from_ring(ring, entry_ts=entry_ts, entry_px=entry_px, sec=60),
        "price_return_30s": _return_from_ring(ring, entry_ts=entry_ts, entry_px=entry_px, sec=30),
        "price_return_10s": _return_from_ring(ring, entry_ts=entry_ts, entry_px=entry_px, sec=10),
        "pre30_price_return": _return_from_ring(ring, entry_ts=entry_ts, entry_px=entry_px, sec=30),
        "pre10_price_return": _return_from_ring(ring, entry_ts=entry_ts, entry_px=entry_px, sec=10),
        "range_5min_pct": range_pct,
        "microsequence_ok": bool(micro.get("microsequence_pre_entry_ok")),
    }


def _enrich_710_only(
    trades_710: list[dict[str, Any]],
    *,
    repo_root: Path,
) -> list[dict[str, Any]]:
    day_dir = SMALL_PAPER_ROOT / "20260710"
    accept_idx = _accept_index_for_dirs([day_dir])
    ring_by_session: dict[str, dict[str, list[tuple[float, float]]]] = {}
    for sess in ("live_session_084821", "live_session_122525"):
        sess_dir = day_dir / sess
        if sess_dir.is_dir():
            ring_by_session[sess] = build_session_price_index(sess_dir)

    out: list[dict[str, Any]] = []
    for t in trades_710:
        row = dict(t)
        sess_dir_name = Path(str(row.get("session_dir") or "")).name
        ring_idx = ring_by_session.get(sess_dir_name, {})
        row = _merge_accept_fields([row], accept_idx)[0]
        sym = str(row.get("symbol") or "")
        ring = list(ring_idx.get(sym, []))
        et = _parse_iso(str(row.get("entry_time") or ""))
        entry_px = float(_num(row.get("entry_price")) or _num(row.get("current_price")) or 0)
        if et is not None and entry_px > 0:
            entry_ts = et.timestamp()
            ring.append((entry_ts, entry_px))
            ring.sort(key=lambda x: x[0])
            row.update(_ring_features(ring, entry_ts=entry_ts, entry_px=entry_px))
            pre = [(a, b) for a, b in ring if entry_ts - 900 <= a <= entry_ts]
            row["price_history_point_count"] = len(pre)
            row["price_history_span_sec"] = round(entry_ts - pre[0][0], 2) if len(pre) >= 2 else 0.0
        blocked, _reason = would_block_flat_band_mainline(MAINLINE_CFG, row)
        row["flat_band_mainline_would_block"] = blocked
        row["post_flat_band_entry"] = not blocked
        shadow = evaluate_trade_shadow_fields(row, config=DEFAULT_SHADOW_CFG, price_idx=ring_idx, saved_flags=row)
        row.update(shadow)
        row["outcome_label"] = _outcome_label(row)
        row["session_kind"] = _session_kind(row)
        out.append(row)
    return out


def _load_historical_light(repo_root: Path) -> list[dict[str, Any]]:
    canonical = _load_canonical_trades_with_session(repo_root)
    for t in canonical:
        t["dataset"] = "canonical_22"
    supplemental = _load_trades_for_days(repo_root, SUPPLEMENTAL_DAYS)
    price_idx = _build_price_index_canonical(repo_root)
    price_idx = _extend_price_index(price_idx, SUPPLEMENTAL_DAYS)
    trades = _enrich_trade_labels(canonical + supplemental, repo_root=repo_root, price_idx=price_idx)
    out: list[dict[str, Any]] = []
    for t in trades:
        row = dict(t)
        shadow = evaluate_trade_shadow_fields(row, config=DEFAULT_SHADOW_CFG, price_idx={}, saved_flags=row)
        row.update(shadow)
        row["outcome_label"] = _outcome_label(row)
        row["session_kind"] = _session_kind(row)
        out.append(row)
    return out


def load_dataset(*, include_historical: bool = False) -> list[dict[str, Any]]:
    repo_root = resolve_kabu_root(NATIVE_ROOT)
    trades_710 = _enrich_710_only(_load_710_sessions(), repo_root=repo_root)
    if not include_historical:
        return trades_710
    hist = _load_historical_light(repo_root)
    hist = [t for t in hist if str(t.get("day") or "") != DAY_710]
    return hist + trades_710


def _feature_value(t: Mapping[str, Any], feat: str) -> Optional[float]:
    if feat.startswith("alias_"):
        feat = feat[6:]
    v = t.get(feat)
    if v is None or v == "":
        if feat == "slope_5min":
            v = t.get("microseq_slope_5min")
        elif feat == "bounce_from_recent_low":
            v = t.get("microseq_bounce_from_recent_low")
        elif feat == "fall_from_recent_high":
            v = t.get("microseq_fall_from_recent_high")
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    return _num(v)


def _feature_keys(trades: Sequence[Mapping[str, Any]]) -> list[str]:
    banned = {
        "is_loser",
        "is_winner",
        "is_big_winner",
        "is_early_stop_300s",
        "is_stop_hit",
        "outcome_label",
        "winner",
        "early_stop",
        "no_progress_exit",
        "normal_stop",
    }
    keys: set[str] = set(FEATURE_CATALOG)
    for t in trades:
        for k, v in t.items():
            if k in banned or _is_leaky_feature(k):
                continue
            if k.startswith("hour_") or "symbol" in k.lower():
                continue
            if k in ("pnl_yen_100", "exit_reason", "hold_sec"):
                continue
            if isinstance(v, bool) or _num(v) is not None:
                keys.add(k)
    return sorted(keys)


def _dist_stats(vals: Sequence[float]) -> dict[str, Any]:
    if not vals:
        return {}
    s = sorted(vals)
    return {
        "count": len(vals),
        "mean": round(statistics.mean(vals), 4),
        "median": round(statistics.median(vals), 4),
        "p10": round(_percentile(s, 0.10) or 0, 4),
        "p25": round(_percentile(s, 0.25) or 0, 4),
        "p50": round(_percentile(s, 0.50) or 0, 4),
        "p75": round(_percentile(s, 0.75) or 0, 4),
        "p90": round(_percentile(s, 0.90) or 0, 4),
    }


def _univariate_row(
    feat: str,
    pos: Sequence[Mapping[str, Any]],
    neg: Sequence[Mapping[str, Any]],
    *,
    pool: str,
) -> Optional[dict[str, Any]]:
    pv = [_feature_value(t, feat) for t in pos]
    nv = [_feature_value(t, feat) for t in neg]
    pv = [x for x in pv if x is not None]
    nv = [x for x in nv if x is not None]
    if len(pv) < 2 or len(nv) < 2:
        return None
    d = _cohens_d(pv, nv)
    mi = _mi_median_split(pv, nv)
    higher_pos = statistics.mean(pv) > statistics.mean(nv)
    auc = _roc_auc(pv, nv, higher_is_positive=higher_pos)
    rb = _rank_biserial(pv, nv)
    return {
        "pool": pool,
        "feature": feat,
        "comparison": "A_harmful_no_progress_vs_C_successful_continuation",
        "pos_n": len(pv),
        "neg_n": len(nv),
        "missing_rate_pos": round(1 - len(pv) / max(1, len(pos)), 4),
        "missing_rate_neg": round(1 - len(nv) / max(1, len(neg)), 4),
        "pos_stats": _dist_stats(pv),
        "neg_stats": _dist_stats(nv),
        "cohens_d": round(d, 4) if d is not None else None,
        "rank_biserial": rb,
        "roc_auc": auc,
        "useful_direction": "higher_in_harmful" if higher_pos else "lower_in_harmful",
        "mutual_information": round(mi, 6) if mi is not None else None,
    }


def _coverage_audit(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    pm = [t for t in trades if str(t.get("day") or "") == DAY_710 and _session_kind(t) == "PM"]
    rows: list[dict[str, Any]] = []
    for feat in _feature_keys(trades):
        present = sum(1 for t in pm if _feature_value(t, feat) is not None)
        rows.append(
            {
                "feature": feat,
                "pm_coverage": round(present / max(1, len(pm)), 4),
                "pm_missing": len(pm) - present,
                "source": "accept" if feat in ACCEPT_ALIAS.values() or feat in ACCEPT_ALIAS else (
                    "microseq" if feat in MICROSEQ_FEATURES else "derived"
                ),
                "entry_live_computable": feat not in ("pnl_yen_100", "hold_sec"),
                "future_leakage": _is_leaky_feature(feat),
                "phase683_namespace": feat in (
                    "readiness_bounce_from_recent_low_accept",
                    "microseq_bounce_from_recent_low",
                    "microseq_fall_from_recent_high",
                    "microseq_slope_5min",
                ),
            }
        )
    rows.sort(key=lambda r: float(r.get("pm_coverage") or 0), reverse=True)
    return rows


def _median_threshold(pos: Sequence[float], neg: Sequence[float]) -> float:
    if not pos and not neg:
        return 0.0
    return statistics.median(pos + neg)


def _build_candidate_rules(pm_a: Sequence[Mapping[str, Any]], pm_c: Sequence[Mapping[str, Any]]) -> list[tuple[str, str, Callable[[Mapping[str, Any]], bool], Callable[[Mapping[str, Any]], float]]]:
    def thr_feat(feat: str, *, harmful_high: bool) -> float:
        pv = [_feature_value(t, feat) for t in pm_a if _feature_value(t, feat) is not None]
        nv = [_feature_value(t, feat) for t in pm_c if _feature_value(t, feat) is not None]
        return _median_threshold(pv, nv)

    huf = thr_feat("high_update_failure_count", harmful_high=True)
    slope = thr_feat("slope_5min", harmful_high=False)
    ret60 = thr_feat("price_return_60s", harmful_high=False)
    ret10 = thr_feat("price_return_10s", harmful_high=False)
    mom = thr_feat("momentum_continuation_score", harmful_high=False)
    imb_div = thr_feat("imbalance_price_divergence", harmful_high=True)
    board_nf = 0.5
    fall = thr_feat("fall_from_recent_high", harmful_high=False)
    push_sec = thr_feat("push_pre_entry_sec", harmful_high=False)
    hist_pts = thr_feat("price_history_point_count", harmful_high=False)

    def _stall(t: Mapping[str, Any]) -> bool:
        h = _feature_value(t, "high_update_failure_count")
        s = _feature_value(t, "slope_5min") or _feature_value(t, "microseq_slope_5min")
        return h is not None and s is not None and h >= huf and s <= slope

    def _board_div(t: Mapping[str, Any]) -> bool:
        nf = _feature_value(t, "price_up_with_board_not_following")
        div = _feature_value(t, "imbalance_price_divergence")
        return (nf is not None and nf >= board_nf) or (div is not None and div >= imb_div)

    def _vol_price(t: Mapping[str, Any]) -> bool:
        r60 = _feature_value(t, "price_return_60s")
        r10 = _feature_value(t, "price_return_10s")
        return r60 is not None and r10 is not None and r60 <= ret60 and r10 <= ret10

    def _range_comp(t: Mapping[str, Any]) -> bool:
        shape = str(t.get("pretrend_shape") or "")
        r5 = _feature_value(t, "entry_rise_5min_pct") or _feature_value(t, "alias_r5")
        return shape in ("C", "D", "flat") and r5 is not None and r5 <= 0.3

    def _exhaust(t: Mapping[str, Any]) -> bool:
        f = _feature_value(t, "fall_from_recent_high") or _feature_value(t, "microseq_fall_from_recent_high")
        s = _feature_value(t, "slope_5min") or _feature_value(t, "microseq_slope_5min")
        return f is not None and s is not None and f <= fall and s <= slope

    def _startup_thin(t: Mapping[str, Any]) -> bool:
        pps = _feature_value(t, "push_pre_entry_sec")
        pts = _feature_value(t, "price_history_point_count")
        s = _feature_value(t, "slope_5min") or _feature_value(t, "microseq_slope_5min")
        return (
            pps is not None and pts is not None and s is not None
            and pps <= push_sec and pts <= hist_pts and s <= slope
        )

    def _combo_stall_board(t: Mapping[str, Any]) -> bool:
        return _stall(t) and _board_div(t)

    def _combo_stall_vol(t: Mapping[str, Any]) -> bool:
        return _stall(t) and _vol_price(t)

    def _score(t: Mapping[str, Any]) -> float:
        s = 0.0
        h = _feature_value(t, "high_update_failure_count")
        sl = _feature_value(t, "slope_5min") or _feature_value(t, "microseq_slope_5min")
        r60v = _feature_value(t, "price_return_60s")
        momv = _feature_value(t, "momentum_continuation_score")
        if h is not None:
            s += (h - huf) * 0.4
        if sl is not None:
            s -= (sl - slope) * 2.0
        if r60v is not None:
            s -= r60v * 0.5
        if momv is not None:
            s += (momv - mom) * 0.3
        return round(s, 4)

    def _score_block(t: Mapping[str, Any]) -> bool:
        return _score(t) >= 0.0

    return [
        ("NP_STALL", f"high_update_failure>={huf:.4f} AND slope_5min<={slope:.4f}", _stall, _score),
        ("NP_BOARD_DIV", "price_up_board_not_following OR imbalance_divergence", _board_div, _score),
        ("NP_VOL_PRICE", f"ret60<={ret60:.4f} AND ret10<={ret10:.4f}", _vol_price, _score),
        ("NP_RANGE_COMP", "flat_shape AND low rise5", _range_comp, _score),
        ("NP_EXHAUST", f"fall<={fall:.4f} AND slope<={slope:.4f}", _exhaust, _score),
        ("NP_STARTUP_THIN", "thin_history AND low_slope", _startup_thin, _score),
        ("NP_STALL_BOARD", "stall AND board_div", _combo_stall_board, _score),
        ("NP_STALL_VOL", "stall AND vol_price", _combo_stall_vol, _score),
        ("NP_SCORE_REJECT", "continuation_score>=0", _score_block, _score),
    ]


def _day_pnl(trades: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for t in trades:
        out[str(t.get("day") or "")] += _pnl(t)
    return dict(out)


def _eval_rule(
    trades: Sequence[Mapping[str, Any]],
    *,
    rule_id: str,
    rule_label: str,
    block_pred: Callable[[Mapping[str, Any]], bool],
    slice_id: str,
) -> dict[str, Any]:
    chron = sorted(trades, key=lambda t: (str(t.get("day") or ""), str(t.get("entry_time") or "")))
    if not chron:
        return {"rule_id": rule_id, "slice_id": slice_id, "entry_count": 0}
    base_pnl = round(sum(_pnl(t) for t in chron), 2)
    blocked = [t for t in chron if block_pred(t)]
    kept = [t for t in chron if not block_pred(t)]
    kept_pnl = round(sum(_pnl(t) for t in kept), 2)
    harmful = [t for t in chron if t.get("outcome_label") == "A_harmful_no_progress"]
    benign_np = [t for t in chron if t.get("outcome_label") == "B_benign_no_progress"]
    succ = [t for t in chron if t.get("outcome_label") == "C_successful_continuation"]
    np_all = [t for t in chron if str(t.get("exit_reason") or "") == "no_progress_exit"]

    def _cap(sub: Sequence[Mapping[str, Any]]) -> int:
        return sum(1 for t in sub if block_pred(t))

    base_day = _day_pnl(chron)
    kept_day = _day_pnl(kept)
    improved = sum(1 for d, v in base_day.items() if kept_day.get(d, 0) > v)
    worsened = sum(1 for d, v in base_day.items() if kept_day.get(d, 0) < v)

    sym_blk: dict[str, int] = defaultdict(int)
    for t in blocked:
        sym_blk[str(t.get("symbol") or "")] += 1
    top_sym = max(sym_blk.items(), key=lambda x: x[1]) if sym_blk else ("", 0)

    evaluable = sum(1 for t in chron if block_pred(t) is not None)
    not_eval = len(chron) - evaluable

    return {
        "rule_id": rule_id,
        "rule_label": rule_label,
        "slice_id": slice_id,
        "entry_count": len(chron),
        "blocked_count": len(blocked),
        "blocked_harmful_no_progress": _cap(harmful),
        "blocked_benign_no_progress": _cap(benign_np),
        "blocked_successful_continuation": _cap(succ),
        "blocked_no_progress_all": _cap(np_all),
        "blocked_stop_hit": sum(1 for t in blocked if t.get("outcome_label") == "D_stop_loss"),
        "blocked_winners": sum(1 for t in blocked if _is_winner(t)),
        "blocked_big_winners": sum(1 for t in blocked if _is_big_winner(t)),
        "blocked_losers": sum(1 for t in blocked if _is_loser(t)),
        "avoided_loss_yen": round(-sum(_pnl(t) for t in blocked if _is_loser(t)), 2),
        "lost_profit_yen": round(sum(_pnl(t) for t in blocked if _is_winner(t)), 2),
        "net_delta_yen": round(kept_pnl - base_pnl, 2),
        "counterfactual_total_pnl_yen": kept_pnl,
        "baseline_total_pnl_yen": base_pnl,
        "capture_rate_harmful": round(_cap(harmful) / max(1, len(harmful)), 4),
        "precision_harmful": round(
            sum(1 for t in blocked if t.get("outcome_label") == "A_harmful_no_progress") / max(1, len(blocked)),
            4,
        ),
        "false_positive_rate_succ": round(_cap(succ) / max(1, len(succ)), 4),
        "improved_days": improved,
        "worsened_days": worsened,
        "top_symbol": top_sym[0],
        "top_symbol_blocks": top_sym[1],
        "feature_coverage": round(evaluable / max(1, len(chron)), 4),
        "not_evaluable_count": not_eval,
    }


def _ranking_sim(
    trades: Sequence[Mapping[str, Any]],
    *,
    score_fn: Callable[[Mapping[str, Any]], float],
    deprioritize_pct: float = 0.35,
) -> dict[str, Any]:
    chron = sorted(trades, key=lambda t: str(t.get("entry_time") or ""))
    if not chron:
        return {"kept_count": 0}
    scores = [score_fn(t) for t in chron]
    thr = _percentile(scores, deprioritize_pct) or min(scores)
    open_until: list[float] = []
    kept: list[dict[str, Any]] = []
    cap = 5
    for t in chron:
        et = _parse_iso(str(t.get("entry_time") or ""))
        xt = _parse_iso(str(t.get("exit_time") or ""))
        if et is None:
            continue
        entry_ts = et.timestamp()
        open_until = [u for u in open_until if u > entry_ts]
        sc = score_fn(t)
        if sc <= thr:
            continue
        if len(open_until) >= cap:
            continue
        kept.append(t)
        if xt is not None:
            open_until.append(xt.timestamp())
    base_pnl = round(sum(_pnl(t) for t in chron), 2)
    kept_pnl = round(sum(_pnl(t) for t in kept), 2)
    harmful = [t for t in chron if t.get("outcome_label") == "A_harmful_no_progress"]
    dropped = [t for t in chron if t not in kept]
    return {
        "kept_count": len(kept),
        "dropped_count": len(dropped),
        "baseline_pnl": base_pnl,
        "scenario_pnl": kept_pnl,
        "net_delta_yen": round(kept_pnl - base_pnl, 2),
        "dropped_harmful_no_progress": sum(1 for t in dropped if t.get("outcome_label") == "A_harmful_no_progress"),
        "dropped_successful_continuation": sum(1 for t in dropped if t.get("outcome_label") == "C_successful_continuation"),
        "dropped_winners": sum(1 for t in dropped if _is_winner(t)),
        "dropped_big_winners": sum(1 for t in dropped if _is_big_winner(t)),
        "capture_rate_harmful": round(
            sum(1 for t in dropped if t.get("outcome_label") == "A_harmful_no_progress") / max(1, len(harmful)),
            4,
        ),
    }


def _ihc_overlap_eval(trades: Sequence[Mapping[str, Any]], new_pred: Callable[[Mapping[str, Any]], bool]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    combos = (
        ("I_only", lambda t: bool(t.get("I_block"))),
        ("H_only", lambda t: bool(t.get("H_block"))),
        ("C_only", lambda t: bool(t.get("C_block"))),
        ("new_only", new_pred),
        ("I_OR_new", lambda t: bool(t.get("I_block")) or new_pred(t)),
        ("H_OR_new", lambda t: bool(t.get("H_block")) or new_pred(t)),
        ("C_OR_new", lambda t: bool(t.get("C_block")) or new_pred(t)),
        ("I_OR_H_OR_new", lambda t: bool(t.get("I_block") or t.get("H_block")) or new_pred(t)),
        ("I_OR_H_OR_C_OR_new", lambda t: bool(t.get("IHC_union_block")) or new_pred(t)),
        ("new_excl_IHC", lambda t: new_pred(t) and not bool(t.get("IHC_union_block"))),
        ("IHC_excl_new", lambda t: bool(t.get("IHC_union_block")) and not new_pred(t)),
    )
    for label, pred in combos:
        rows.append(_eval_rule(trades, rule_id=label, rule_label=label, block_pred=pred, slice_id="post_flat_band"))
    return rows


def _label_summary(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        groups[str(t.get("outcome_label") or "E_other")].append(t)
    out: dict[str, Any] = {}
    for label, rows in sorted(groups.items()):
        pnls = [_pnl(t) for t in rows]
        out[label] = {
            "count": len(rows),
            "total_pnl_yen_100": round(sum(pnls), 2),
            "mean_pnl": round(statistics.mean(pnls), 2) if pnls else 0,
            "median_pnl": round(statistics.median(pnls), 2) if pnls else 0,
        }
    return out


def _verify_pm_canonical(pm: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    pnl = round(sum(_pnl(t) for t in pm), 2)
    reasons: dict[str, int] = defaultdict(int)
    for t in pm:
        reasons[str(t.get("exit_reason") or "")] += 1
    meta = {
        "trade_count": len(pm),
        "total_pnl_yen_100": pnl,
        "exit_reason_counts": dict(reasons),
        "count_ok": len(pm) == 38,
        "pnl_ok": pnl == -36900.0,
        "no_progress_ok": reasons.get("no_progress_exit", 0) == 24,
        "stop_hit_ok": reasons.get("stop_hit", 0) == 5,
        "trailing_ok": reasons.get("trailing_mfe_exit", 0) == 5,
        "close_ok": reasons.get("afternoon_session_close", 0) == 4,
    }
    if not (meta["count_ok"] and meta["pnl_ok"]):
        raise ValueError(f"PM canonical mismatch: {meta}")
    return meta


def _pool_slices(trades: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    post = [t for t in trades if t.get("post_flat_band_entry")]
    return {
        "710_pm": [t for t in trades if str(t.get("day") or "") == DAY_710 and _session_kind(t) == "PM"],
        "710_am": [t for t in trades if str(t.get("day") or "") == DAY_710 and _session_kind(t) == "AM"],
        "recent_707_710": [t for t in trades if str(t.get("day") or "") in ALL_710_DAYS],
        "post_flat_band": post,
        "canonical_22": [t for t in trades if t.get("dataset") == "canonical_22"],
    }


def _pick_verdict(best: Mapping[str, Any], robust: Sequence[Mapping[str, Any]]) -> str:
    pm = next((r for r in robust if r.get("rule_id") == best.get("rule_id") and r.get("slice_id") == "710_pm"), {})
    post = next((r for r in robust if r.get("rule_id") == best.get("rule_id") and r.get("slice_id") == "post_flat_band"), {})
    if not pm:
        return VERDICT_DATA_GAP
    if post.get("entry_count", 0) < 50:
        if pm.get("blocked_harmful_no_progress", 0) >= 3 and pm.get("net_delta_yen", 0) > 0:
            return VERDICT_WEAK
        if pm.get("blocked_harmful_no_progress", 0) >= 1:
            return VERDICT_WEAK
        return VERDICT_NO_ROBUST
    if (
        pm.get("blocked_harmful_no_progress", 0) >= 2
        and pm.get("net_delta_yen", 0) > 0
        and post.get("net_delta_yen", 0) > 0
        and pm.get("blocked_big_winners", 0) == 0
        and pm.get("precision_harmful", 0) >= 0.35
    ):
        return VERDICT_REJECT
    if pm.get("blocked_harmful_no_progress", 0) >= 2 and pm.get("net_delta_yen", 0) > 0:
        return VERDICT_WEAK
    return VERDICT_NO_ROBUST


def run_audit(*, write_outputs: bool = True) -> dict[str, Any]:
    disk_pct = _disk_usage_pct(NATIVE_ROOT)
    if disk_pct >= 98:
        raise RuntimeError(f"Disk usage {disk_pct:.1f}% >= 98%; aborting phase685")
    trades = load_dataset(include_historical=False)
    pools = _pool_slices(trades)
    pm = pools["710_pm"]
    pm_meta = _verify_pm_canonical(pm)
    label_summary = _label_summary(pm)

    pm_a = [t for t in pm if t.get("outcome_label") == "A_harmful_no_progress"]
    pm_b = [t for t in pm if t.get("outcome_label") == "B_benign_no_progress"]
    pm_c = [t for t in pm if t.get("outcome_label") == "C_successful_continuation"]

    coverage = _coverage_audit(trades)
    features = _feature_keys(trades)

    banned = {
        "is_loser",
        "is_winner",
        "is_big_winner",
        "is_early_stop_300s",
        "is_stop_hit",
        "outcome_label",
        "winner",
        "early_stop",
        "no_progress_exit",
        "normal_stop",
        "accept_time",
        "entry_time",
        "exit_time",
        "event_time",
    }
    uni_rows: list[dict[str, Any]] = []
    for pool_name, pool_trades in (
        ("710_pm", pm),
        ("recent_707_710", pools["recent_707_710"]),
        ("post_flat_band", pools["post_flat_band"]),
    ):
        a = [t for t in pool_trades if t.get("outcome_label") == "A_harmful_no_progress"]
        c = [t for t in pool_trades if t.get("outcome_label") == "C_successful_continuation"]
        for feat in features:
            if feat in banned:
                continue
            row = _univariate_row(feat, a, c, pool=pool_name)
            if row:
                uni_rows.append(row)
    uni_rows.sort(key=lambda r: abs(float(r.get("cohens_d") or 0)), reverse=True)

    rules = _build_candidate_rules(pm_a, pm_c)
    cand_rows: list[dict[str, Any]] = []
    daily_rows: list[dict[str, Any]] = []
    trade_detail_rows: list[dict[str, Any]] = []
    ranking_rows: list[dict[str, Any]] = []

    for rule_id, rule_label, pred, score_fn in rules:
        slice_results: list[dict[str, Any]] = []
        for slice_id, slice_trades in pools.items():
            row = _eval_rule(slice_trades, rule_id=rule_id, rule_label=rule_label, block_pred=pred, slice_id=slice_id)
            daily_rows.append(row)
            slice_results.append(row)
        pm_row = next((r for r in slice_results if r.get("slice_id") == "710_pm"), {})
        cand_rows.append({"rule_id": rule_id, "rule_label": rule_label, **pm_row})
        rank = _ranking_sim(pm, score_fn=score_fn)
        ranking_rows.append({"rule_id": rule_id, "mode": "reject", **pm_row})
        ranking_rows.append({"rule_id": rule_id, "mode": "ranking", **rank})
        for t in pm:
            if pred(t):
                trade_detail_rows.append(
                    {
                        "rule_id": rule_id,
                        "symbol": t.get("symbol"),
                        "entry_time": t.get("entry_time"),
                        "outcome_label": t.get("outcome_label"),
                        "exit_reason": t.get("exit_reason"),
                        "pnl_yen_100": t.get("pnl_yen_100"),
                        "continuation_score": score_fn(t),
                    }
                )

    best_rule = max(
        [r for r in daily_rows if r.get("slice_id") == "710_pm" and r.get("blocked_count", 0) > 0],
        key=lambda r: (
            float(r.get("blocked_harmful_no_progress") or 0),
            float(r.get("net_delta_yen") or -1e18),
            -float(r.get("blocked_winners") or 0),
        ),
        default={},
    )
    best_pred = next((p for rid, _, p, _ in rules if rid == best_rule.get("rule_id")), lambda _t: False)
    ihc_rows = _ihc_overlap_eval(pools["post_flat_band"], best_pred)

    top_feat = uni_rows[0] if uni_rows else {}
    best_2cond = next((r for r in daily_rows if r.get("rule_id") == "NP_STALL_BOARD" and r.get("slice_id") == "710_pm"), {})

    startup_audit = {
        "pm_live_feature_incomplete_rate": round(
            sum(1 for t in pm if not t.get("live_feature_complete")) / max(1, len(pm)),
            4,
        ),
        "pm_quality_fallback_rate": round(
            sum(1 for t in pm if t.get("quality_fallback_path")) / max(1, len(pm)),
            4,
        ),
        "pm_mean_push_pre_entry_sec": round(
            statistics.mean([float(_feature_value(t, "push_pre_entry_sec") or 0) for t in pm]),
            2,
        ),
        "pm_mean_price_history_points": round(
            statistics.mean([float(_feature_value(t, "price_history_point_count") or 0) for t in pm]),
            2,
        ),
        "A_mean_push_pre_entry_sec": round(
            statistics.mean([float(_feature_value(t, "push_pre_entry_sec") or 0) for t in pm_a]) if pm_a else 0,
            2,
        ),
        "C_mean_push_pre_entry_sec": round(
            statistics.mean([float(_feature_value(t, "push_pre_entry_sec") or 0) for t in pm_c]) if pm_c else 0,
            2,
        ),
    }

    verdict = _pick_verdict(best_rule, daily_rows)
    ranking_best = next((r for r in ranking_rows if r.get("rule_id") == best_rule.get("rule_id") and r.get("mode") == "ranking"), {})
    reject_best = next((r for r in ranking_rows if r.get("rule_id") == best_rule.get("rule_id") and r.get("mode") == "reject"), {})
    ranking_better = float(ranking_best.get("net_delta_yen") or 0) > float(reject_best.get("net_delta_yen") or 0)

    report: dict[str, Any] = {
        "phase": 685,
        "verdict": verdict,
        "note_historical_pools": (
            "Historical canonical/post_flat_band pools skipped in this run (disk/runtime); "
            "robustness limited to 7/10 AM+PM slice."
        ),
        "pm_canonical": pm_meta,
        "pm_label_summary": label_summary,
        "startup_audit": startup_audit,
        "ihc_pm_no_progress_capture": {
            "I": sum(1 for t in pm if t.get("I_block") and str(t.get("exit_reason") or "") == "no_progress_exit"),
            "H": sum(1 for t in pm if t.get("H_block") and str(t.get("exit_reason") or "") == "no_progress_exit"),
            "C": sum(1 for t in pm if t.get("C_block") and str(t.get("exit_reason") or "") == "no_progress_exit"),
        },
        "top_univariate_710_pm": top_feat,
        "best_rule_710_pm": best_rule,
        "best_2cond_710_pm": best_2cond,
        "ranking_vs_reject": {
            "best_rule_id": best_rule.get("rule_id"),
            "reject_delta": reject_best.get("net_delta_yen"),
            "ranking_delta": ranking_best.get("net_delta_yen"),
            "ranking_better": ranking_better,
            "recommendation": "ranking" if ranking_better else "reject",
        },
        "required_answers": {
            "1_np24_breakdown": {
                "harmful": label_summary.get("A_harmful_no_progress", {}).get("count"),
                "benign": label_summary.get("B_benign_no_progress", {}).get("count"),
                "total_no_progress": pm_meta["exit_reason_counts"].get("no_progress_exit"),
            },
            "3_top_single_feature": top_feat.get("feature"),
            "4_best_2cond": "NP_STALL_BOARD",
            "5_board_price_divergence": any(r.get("rule_id") == "NP_BOARD_DIV" and r.get("blocked_harmful_no_progress", 0) > 0 for r in daily_rows if r.get("slice_id") == "710_pm"),
            "6_volume_price_divergence": any(r.get("rule_id") == "NP_VOL_PRICE" and r.get("blocked_harmful_no_progress", 0) > 0 for r in daily_rows if r.get("slice_id") == "710_pm"),
            "7_high_update_stop_effective": any(r.get("rule_id") == "NP_STALL" and r.get("blocked_harmful_no_progress", 0) > 0 for r in daily_rows if r.get("slice_id") == "710_pm"),
            "8_range_compression_effective": any(r.get("rule_id") == "NP_RANGE_COMP" and r.get("blocked_harmful_no_progress", 0) > 0 for r in daily_rows if r.get("slice_id") == "710_pm"),
            "9_np_reduction_best": best_rule.get("blocked_no_progress_all"),
            "10_pm_pnl_improvement": best_rule.get("counterfactual_total_pnl_yen"),
            "11_winner_sacrifice": {
                "count": best_rule.get("blocked_winners"),
                "yen": best_rule.get("lost_profit_yen"),
            },
            "12_big_winner_sacrifice": best_rule.get("blocked_big_winners"),
            "13_improves_outside_710": next(
                (r.get("net_delta_yen") for r in daily_rows if r.get("rule_id") == best_rule.get("rule_id") and r.get("slice_id") == "post_flat_band"),
                None,
            ),
            "15_complements_ihc": any(r.get("rule_id") == "new_excl_IHC" and r.get("blocked_count", 0) > 0 for r in ihc_rows),
            "16_reject_vs_ranking": "ranking" if ranking_better else "reject",
            "17_runtime_shadow_candidate": verdict in (VERDICT_REJECT, VERDICT_RANKING, VERDICT_FOUND),
            "18_if_no_candidate": "Need board push history for PM; I/H/C do not tag no_progress on 7/10",
        },
    }

    if write_outputs:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_DIR / "phase685_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        label_fields = ["position_id", "symbol", "entry_time", "exit_time", "exit_reason", "pnl_yen_100", "outcome_label", "hold_sec"]
        _write_csv(REPORT_DIR / "phase685_20260710_pm_trade_labels.csv", label_fields, [{k: t.get(k) for k in label_fields} for t in pm])
        _write_csv(REPORT_DIR / "phase685_feature_coverage.csv", list(coverage[0].keys()) if coverage else [], coverage)
        uni_flat = []
        for r in uni_rows:
            uni_flat.append({k: v for k, v in r.items() if k not in ("pos_stats", "neg_stats")})
        _write_csv(REPORT_DIR / "phase685_univariate_features.csv", list(uni_flat[0].keys()) if uni_flat else [], uni_flat[:200])
        _write_csv(REPORT_DIR / "phase685_candidate_rules.csv", list(cand_rows[0].keys()) if cand_rows else [], cand_rows)
        _write_csv(REPORT_DIR / "phase685_candidate_daily_results.csv", list(daily_rows[0].keys()) if daily_rows else [], daily_rows)
        _write_csv(REPORT_DIR / "phase685_candidate_trade_details.csv", list(trade_detail_rows[0].keys()) if trade_detail_rows else [], trade_detail_rows)
        _write_csv(REPORT_DIR / "phase685_ranking_vs_reject.csv", list(ranking_rows[0].keys()) if ranking_rows else [], ranking_rows)
        _write_csv(REPORT_DIR / "phase685_ihc_overlap.csv", list(ihc_rows[0].keys()) if ihc_rows else [], ihc_rows)
        rob_fields = ["rule_id", "slice_id", "net_delta_yen", "blocked_harmful_no_progress", "blocked_big_winners", "improved_days"]
        _write_csv(REPORT_DIR / "phase685_robustness.csv", rob_fields, [{k: r.get(k) for k in rob_fields} for r in daily_rows])
        _write_decision_md(report, label_summary, best_rule, top_feat)

    return report


def _write_decision_md(
    report: Mapping[str, Any],
    labels: Mapping[str, Any],
    best: Mapping[str, Any],
    top_feat: Mapping[str, Any],
) -> None:
    ans = report.get("required_answers") or {}
    lines = [
        "# Phase685 Decision — No-Progress ENTRY Discovery",
        "",
        f"**Verdict:** `{report.get('verdict')}`",
        "",
        "## 7/10 PM Label Summary",
        "",
        f"- A harmful no_progress: {labels.get('A_harmful_no_progress', {}).get('count')} trades / {labels.get('A_harmful_no_progress', {}).get('total_pnl_yen_100'):+,}円",
        f"- B benign no_progress: {labels.get('B_benign_no_progress', {}).get('count')} trades / {labels.get('B_benign_no_progress', {}).get('total_pnl_yen_100'):+,}円",
        f"- C successful continuation: {labels.get('C_successful_continuation', {}).get('count')} trades",
        "",
        "## Key Findings",
        "",
        f"- Top univariate feature (A vs C, 7/10 PM): **{top_feat.get('feature')}** (d={top_feat.get('cohens_d')}, AUC={top_feat.get('roc_auc')})",
        f"- Best reject rule on 7/10 PM: **{best.get('rule_id')}** → Δ{best.get('net_delta_yen'):+,}円, harmful capture {best.get('blocked_harmful_no_progress')}",
        f"- I/H/C captured PM no_progress: {json.dumps(report.get('ihc_pm_no_progress_capture'))}",
        f"- Reject vs Ranking: **{ans.get('16_reject_vs_ranking')}**",
        "",
        "## Caution",
        "",
        "Research only. No mainline promotion. 7/10 PM startup delay may thin pre-entry history; do not use clock-time rules.",
    ]
    (REPORT_DIR / "phase685_decision.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    out = run_audit()
    print(json.dumps({"verdict": out["verdict"], "best_rule": out.get("best_rule_710_pm")}, ensure_ascii=False, indent=2))
