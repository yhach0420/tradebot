"""Phase671 — Early STOP / same-symbol churn / exploratory feature discovery (research only)."""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase632_pbv2_profit_filter_counterfactual import _metrics, _profit_factor
from research.phase634_pbv2_only_rise5_full_period import (
    _disk_usage_pct,
    _is_push_replay_session,
    _iter_events,
    load_all_full_period_trades,
    load_trades_for_session,
)
from research.phase663_price_age_freshness_analysis import CANONICAL_DAYS
from research.phase665_pretrend_shape_analysis import _build_price_index_canonical
from research.phase666_breakout_initiation_analysis import _build_accept_index
from research.phase667_flat_vwap_volume_refinement import _enrich_trade_full
from research.phase668_existing_shadow_adoption_review import _filter_canonical
from research.phase465b_trend_gate_redesign import _cohens_d, _mi_median_split
from research.phase631_profit_source_attribution import _num, _parse_iso
from research.structural_trade_normalize import resolve_kabu_root
from small_paper.flat_weak_range_forward_shadow import evaluate_flat_weak_range_shadow
from small_paper.pbv2_flat_band_entry_guard import would_block_flat_band_mainline

PHASE671_VERDICT_FOUND_SIGNAL = "FOUND_SIGNAL"
PHASE671_VERDICT_FOUND_CHURN_BUG = "FOUND_CHURN_BUG"
PHASE671_VERDICT_HOLD = "HOLD"
PHASE671_VERDICT_REJECT = "REJECT"
REPORT_DIR_NAME = "phase671_early_stop"
NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = NATIVE_ROOT / "results" / "reports" / REPORT_DIR_NAME
SMALL_PAPER_ROOT = NATIVE_ROOT / "results" / "small_paper"
DISK_USAGE_MAX_PCT = 75.0
EARLY_STOP_SEC = 300.0
BIG_WINNER_YEN = 5000.0

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

NON_FEATURE_KEYS = frozenset(
    {
        "day",
        "session",
        "symbol",
        "entry_time",
        "exit_time",
        "entry_type",
        "entry_pool",
        "exit_reason",
        "event_type",
        "message_index",
        "session_id",
        "session_kind",
        "event_time",
        "accepted_at",
        "accepted_event_time",
        "market_entry_time",
        "current_price_time",
        "minutes_from_open",
        "hold_sec",
        "hold_sec_market",
        "pnl_yen_100",
        "pnl_pct",
        "peak_mfe_pct",
        "rolling_mfe_pct",
        "rolling_mae_pct",
        "mfe_pct",
        "mae_pct",
        "exit_price",
        "entry_price",
        "current_price",
        "structural_exit_policy",
        "source",
        "profile",
        "reject_reason",
        "pretrend_shape",
        "breakout_class",
        "flat_subclass",
    }
)

TIME_PREFIXES = ("hour_", "minute_", "session_", "am_", "pm_", "time_")


def _hold_sec(row: Mapping[str, Any]) -> Optional[float]:
    for key in ("hold_sec", "hold_sec_market"):
        v = _num(row.get(key))
        if v is not None:
            return float(v)
    et = _parse_iso(row.get("entry_time"))
    xt = _parse_iso(row.get("exit_time"))
    if et is not None and xt is not None:
        return max(0.0, (xt - et).total_seconds())
    return None


def _is_stop_hit(row: Mapping[str, Any]) -> bool:
    return str(row.get("exit_reason") or "") == "stop_hit" or bool(row.get("stop_hit"))


def _is_early_stop(row: Mapping[str, Any], *, sec: float = EARLY_STOP_SEC) -> bool:
    hs = _hold_sec(row)
    return _is_stop_hit(row) and hs is not None and hs <= sec


def _session_bucket(row: Mapping[str, Any]) -> str:
    mins = _num(row.get("minutes_from_open"))
    if mins is None:
        return str(row.get("session_kind") or "unknown")
    if mins < 150:
        return "AM"
    if mins >= 210:
        return "PM"
    return "lunch"


def _pf(yens: Sequence[float]) -> Optional[float]:
    gp = sum(max(y, 0) for y in yens)
    gl = abs(sum(min(y, 0) for y in yens))
    if gl <= 0:
        return None if gp <= 0 else 999.0
    return round(gp / gl, 4)


LEAKY_SUBSTRINGS = (
    "stop_hit",
    "pnl",
    "mfe",
    "mae",
    "exit_",
    "shadow_pnl",
    "overlap_replaced",
    "blocked_pnl",
    "delta_yen",
    "actual_pnl",
    "hold_sec",
    "no_progress",
    "trailing_mfe",
    "fwd_pct",
    "_fwd_",
    "return_5min_fwd",
    "return_10min_fwd",
    "return_15min_fwd",
    "scan_id",
)


def _is_leaky_feature(key: str) -> bool:
    k = str(key).lower()
    if k in NON_FEATURE_KEYS:
        return True
    if any(p in k for p in LEAKY_SUBSTRINGS):
        return True
    if any(k.startswith(p) for p in TIME_PREFIXES):
        return True
    return False


def _merge_numeric_features(src: Mapping[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for key, val in src.items():
        if _is_leaky_feature(key):
            continue
        if isinstance(val, bool):
            out[f"num_{key}"] = 1.0 if val else 0.0
            continue
        if val in ("True", "true", "1", 1):
            out[f"num_{key}"] = 1.0
            continue
        if val in ("False", "false", "0", 0):
            out[f"num_{key}"] = 0.0
            continue
        num = _num(val)
        if num is not None:
            out[f"num_{key}"] = float(num)
    return out


def _load_trade_row_extended(session_dir: Path, day: str) -> list[dict[str, Any]]:
    base = load_trades_for_session(session_dir, day)
    if not base:
        return []

    accepted_by_key: dict[tuple[Any, Any], dict[str, Any]] = {}
    for e in _iter_events(session_dir):
        if e.get("event_type") != "accepted":
            continue
        key = (e.get("symbol"), e.get("entry_time") or e.get("message_index"))
        accepted_by_key[key] = e

    exit_by_key: dict[tuple[Any, Any], dict[str, Any]] = {}
    for e in _iter_events(session_dir):
        if e.get("event_type") != "observer_exit":
            continue
        key = (e.get("symbol"), e.get("entry_time"))
        exit_by_key[key] = e

    out: list[dict[str, Any]] = []
    for row in base:
        key = (row.get("symbol"), row.get("entry_time"))
        acc = accepted_by_key.get(key) or {}
        ex = exit_by_key.get(key) or {}
        src = {**ex, **acc, **row}
        merged = dict(row)
        merged.update(_merge_numeric_features(acc))
        hs = _num(ex.get("hold_sec")) or row.get("hold_sec_market")
        if hs is not None:
            merged["hold_sec"] = float(hs)
        merged["exit_time"] = ex.get("exit_time") or merged.get("exit_time")
        merged["stop_hit"] = _is_stop_hit(merged)
        merged["early_stop"] = _is_early_stop(merged)
        merged["session_bucket"] = _session_bucket(merged)
        out.append(merged)
    return out


def _load_canonical_trades_extended(repo_root: Path) -> list[dict[str, Any]]:
    days = set(CANONICAL_DAYS)
    trades: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for day_dir in sorted(SMALL_PAPER_ROOT.iterdir()):
        if not day_dir.is_dir() or len(day_dir.name) != 8:
            continue
        day_iso = f"{day_dir.name[:4]}-{day_dir.name[4:6]}-{day_dir.name[6:8]}"
        if day_iso not in days:
            continue
        for sess_dir in sorted(day_dir.glob("live_session_*")):
            if not sess_dir.is_dir() or _is_push_replay_session(sess_dir):
                continue
            for t in _load_trade_row_extended(sess_dir, day_iso):
                key = (day_iso, str(t.get("session") or ""), str(t.get("symbol") or ""), str(t.get("entry_time") or ""))
                if key in seen:
                    continue
                seen.add(key)
                trades.append(t)
    trades.sort(key=lambda t: (str(t.get("day") or ""), str(t.get("entry_time") or ""), str(t.get("symbol") or "")))
    return trades


def _enrich_shadow_flags(trades: list[dict[str, Any]], *, repo_root: Path) -> list[dict[str, Any]]:
    price_idx = _build_price_index_canonical(repo_root)
    accept_idx = _build_accept_index()
    out: list[dict[str, Any]] = []
    for t in trades:
        row = _enrich_trade_full(dict(t), price_idx=price_idx, accept_idx=accept_idx)
        row["flat_band_mainline_would_block"] = would_block_flat_band_mainline(MAINLINE_CFG, row)[0]
        blocked, reason = evaluate_flat_weak_range_shadow(row)
        row["flat_weak_range_shadow_block"] = blocked
        row["flat_weak_range_shadow_reason"] = reason
        row["early_stop"] = _is_early_stop(row)
        row["hold_sec"] = _hold_sec(row)
        out.append(row)
    return out


def _early_stop_summary(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    stops = [t for t in trades if _is_stop_hit(t)]
    early = [t for t in trades if t.get("early_stop")]
    early_60 = [t for t in stops if (_hold_sec(t) or 9999) <= 60]
    early_180 = [t for t in stops if (_hold_sec(t) or 9999) <= 180]
    early_300 = early
    return {
        "entry_count": len(trades),
        "stop_hit_count": len(stops),
        "early_stop_count": len(early),
        "early_stop_share_of_all_entries": round(len(early) / len(trades), 4) if trades else 0.0,
        "early_stop_share_of_stops": round(len(early) / len(stops), 4) if stops else 0.0,
        "stop_within_60s": len(early_60),
        "stop_within_180s": len(early_180),
        "stop_within_300s": len(early_300),
        "early_stop_total_pnl_yen": round(sum(float(t.get("pnl_yen_100") or 0) for t in early), 2),
    }


def _group_counts(trades: Sequence[Mapping[str, Any]], key_fn: Callable[[Mapping[str, Any]], str]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        buckets[key_fn(t)].append(dict(t))
    rows: list[dict[str, Any]] = []
    for k in sorted(buckets):
        sub = buckets[k]
        early = [t for t in sub if t.get("early_stop")]
        stops = [t for t in sub if _is_stop_hit(t)]
        rows.append(
            {
                "bucket": k,
                "entry_count": len(sub),
                "stop_hit_count": len(stops),
                "early_stop_count": len(early),
                "early_stop_rate": round(len(early) / len(sub), 4) if sub else 0.0,
                "total_pnl_yen": round(sum(float(t.get("pnl_yen_100") or 0) for t in sub), 2),
            }
        )
    return rows


def _analyze_churn(trades: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_day_sym: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        by_day_sym[(str(t.get("day") or ""), str(t.get("symbol") or ""))].append(dict(t))

    chain_rows: list[dict[str, Any]] = []
    reentry_after_stop: list[dict[str, Any]] = []
    reentry_pnl_after_stop = 0.0
    stop_loop_loss = 0.0
    chains_ge2 = 0

    for (day, sym), seq in by_day_sym.items():
        seq.sort(key=lambda r: str(r.get("entry_time") or ""))
        stop_streak = 0
        entries_today = len(seq)
        for i, t in enumerate(seq):
            if _is_stop_hit(t):
                stop_streak += 1
            else:
                stop_streak = 0
            if i == 0:
                continue
            prev = seq[i - 1]
            if not _is_stop_hit(prev):
                continue
            et_prev = _parse_iso(prev.get("exit_time") or prev.get("entry_time"))
            et_cur = _parse_iso(t.get("entry_time"))
            if et_prev is None or et_cur is None:
                gap_sec = None
            else:
                gap_sec = max(0.0, (et_cur - et_prev).total_seconds())
            if gap_sec is not None and gap_sec <= 30 * 60:
                row = {
                    "day": day,
                    "symbol": sym,
                    "prior_stop_hold_sec": _hold_sec(prev),
                    "reentry_gap_sec": round(gap_sec, 1),
                    "reentry_pnl_yen_100": float(t.get("pnl_yen_100") or 0),
                    "reentry_stop_hit": _is_stop_hit(t),
                    "reentry_early_stop": bool(t.get("early_stop")),
                    "entries_same_symbol_day": entries_today,
                    "same_symbol_stop_chain_count": stop_streak + 1,
                }
                chain_rows.append(row)
                reentry_after_stop.append(t)
                reentry_pnl_after_stop += float(t.get("pnl_yen_100") or 0)
                if _is_stop_hit(t):
                    stop_loop_loss += float(t.get("pnl_yen_100") or 0)
        if stop_streak >= 2:
            chains_ge2 += 1

    reentry_wins = sum(1 for t in reentry_after_stop if float(t.get("pnl_yen_100") or 0) > 0)
    reentry_losses = sum(1 for t in reentry_after_stop if float(t.get("pnl_yen_100") or 0) < 0)
    summary = {
        "same_symbol_reentry_after_stop_30m_count": len(chain_rows),
        "reentry_after_stop_win_count": reentry_wins,
        "reentry_after_stop_loss_count": reentry_losses,
        "reentry_after_stop_total_pnl_yen": round(reentry_pnl_after_stop, 2),
        "reentry_after_stop_pf": _pf([float(t.get("pnl_yen_100") or 0) for t in reentry_after_stop]),
        "stop_loop_followup_loss_yen": round(stop_loop_loss, 2),
        "symbols_with_2plus_stop_streak_days": chains_ge2,
        "reentry_after_stop_is_loss_source": reentry_pnl_after_stop < 0,
    }
    return chain_rows, summary


def _feature_columns(trades: Sequence[Mapping[str, Any]]) -> list[str]:
    keys: set[str] = set()
    for t in trades:
        for k, v in t.items():
            if _is_leaky_feature(k):
                continue
            if k.startswith("num_") or _num(v) is not None:
                keys.add(k if k.startswith("num_") else k)
    # prefer ENTRY_FEATURES ids already on row
    for k in (
        "momentum_score",
        "momentum_continuation",
        "board_imbalance",
        "spread_bps",
        "entry_vwap_dev_pct",
        "entry_rise_5min_pct",
        "entry_rise_10min_pct",
        "entry_expectancy_score_v2",
        "update_count_before_entry",
        "trading_value",
        "turnover_proxy",
        "price_age_sec",
        "board_age_sec",
        "flat_weak_range_shadow_block",
    ):
        if any(k in t for t in trades) and not _is_leaky_feature(k):
            keys.add(k)
    return sorted(keys)


def _feature_ranking(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    early = [t for t in trades if t.get("early_stop")]
    non_early = [t for t in trades if not t.get("early_stop")]
    cols = _feature_columns(trades)
    rows: list[dict[str, Any]] = []
    for col in cols:
        ev = [float(_num(t.get(col)) or 0) for t in early if _num(t.get(col)) is not None]
        nv = [float(_num(t.get(col)) or 0) for t in non_early if _num(t.get(col)) is not None]
        if len(ev) < 5 or len(nv) < 5:
            continue
        d = _cohens_d(ev, nv)
        mi = _mi_median_split(ev, nv)
        rows.append(
            {
                "feature": col,
                "early_stop_mean": round(statistics.mean(ev), 4),
                "non_early_stop_mean": round(statistics.mean(nv), 4),
                "cohens_d": round(d, 4) if d is not None else None,
                "mutual_information": round(mi, 6) if mi is not None else None,
                "early_stop_n": len(ev),
                "non_early_stop_n": len(nv),
            }
        )
    rows.sort(
        key=lambda r: (
            abs(float(r.get("cohens_d") or 0)),
            float(r.get("mutual_information") or 0),
        ),
        reverse=True,
    )
    for i, r in enumerate(rows, start=1):
        r["rank"] = i
    return rows


def _tree_rules(trades: Sequence[Mapping[str, Any]], features: Sequence[str], *, max_depth: int = 3) -> list[dict[str, Any]]:
    try:
        import numpy as np
        from sklearn.tree import DecisionTreeClassifier, export_text
    except ImportError:
        return []

    y = [1 if t.get("early_stop") else 0 for t in trades]
    if sum(y) < 5 or len(y) - sum(y) < 5:
        return []

    X_cols: list[str] = []
    matrix: list[list[float]] = []
    for f in features:
        vals = [_num(t.get(f)) for t in trades]
        if sum(1 for v in vals if v is not None) < 20:
            continue
        med = statistics.median([float(v) for v in vals if v is not None])
        X_cols.append(f)
        matrix.append([float(v if v is not None else med) for v in vals])

    if not X_cols:
        return []

    import numpy as np

    X = np.array(matrix, dtype=float).T
    clf = DecisionTreeClassifier(max_depth=max_depth, min_samples_leaf=max(10, len(y) // 50), random_state=42)
    clf.fit(X, y)
    text = export_text(clf, feature_names=X_cols, max_depth=max_depth)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    rows: list[dict[str, Any]] = []
    for i, ln in enumerate(lines[:40], start=1):
        rows.append({"rule_line": i, "tree_export": ln})
    return rows


def _threshold_sweep(trades: Sequence[Mapping[str, Any]], ranking: Sequence[Mapping[str, Any]], *, top_n: int = 12) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    n = len(trades)
    if n == 0:
        return rows
    base_early_rate = sum(1 for t in trades if t.get("early_stop")) / n
    for r in list(ranking)[:top_n]:
        feat = str(r["feature"])
        vals = [(float(v), bool(t.get("early_stop"))) for t in trades if (v := _num(t.get(feat))) is not None]
        if len(vals) < 20:
            continue
        ordered = sorted({v for v, _ in vals})
        if len(ordered) > 30:
            step = max(1, len(ordered) // 30)
            ordered = ordered[::step]
        for thr in ordered:
            hi = [es for v, es in vals if v >= thr]
            lo = [es for v, es in vals if v < thr]
            if len(hi) < 5 or len(lo) < 5:
                continue
            hi_rate = sum(hi) / len(hi)
            lo_rate = sum(lo) / len(lo)
            jump = max(abs(hi_rate - base_early_rate), abs(lo_rate - base_early_rate))
            rows.append(
                {
                    "feature": feat,
                    "threshold": round(thr, 4),
                    "side": "ge",
                    "bucket_count": len(hi),
                    "early_stop_rate": round(hi_rate, 4),
                    "delta_vs_baseline": round(hi_rate - base_early_rate, 4),
                    "rate_jump": round(jump, 4),
                }
            )
    rows.sort(key=lambda r: float(r.get("rate_jump") or 0), reverse=True)
    for i, row in enumerate(rows[:200], start=1):
        row["rank"] = i
    return rows[:200]


def _combo_rules(trades: Sequence[Mapping[str, Any]], ranking: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    top_feats = [str(r["feature"]) for r in list(ranking)[:8]]
    rows: list[dict[str, Any]] = []
    n = len(trades)
    if n == 0:
        return rows

    def _rule(feat: str, op: str, thr: float) -> Callable[[Mapping[str, Any]], bool]:
        if op == "ge":
            return lambda t, f=feat, th=thr: (_num(t.get(f)) or -1e18) >= th
        return lambda t, f=feat, th=thr: (_num(t.get(f)) or 1e18) <= th

    singles: list[tuple[str, Callable[[Mapping[str, Any]], bool]]] = []
    for feat in top_feats:
        vals = [float(v) for t in trades if (v := _num(t.get(feat))) is not None]
        if len(vals) < 20:
            continue
        for pct in (0.25, 0.5, 0.75):
            thr = statistics.quantiles(vals, n=4)[int(pct * 4) - 1] if len(vals) >= 4 else statistics.median(vals)
            singles.append((f"{feat}>={thr:.4g}", _rule(feat, "ge", thr)))
            singles.append((f"{feat}<={thr:.4g}", _rule(feat, "le", thr)))

    def _eval_rule(name: str, pred: Callable[[Mapping[str, Any]], bool]) -> dict[str, Any]:
        flagged = [t for t in trades if pred(t)]
        if not flagged:
            return {}
        blocked = flagged
        bw = sum(1 for t in blocked if float(t.get("pnl_yen_100") or 0) > 0)
        bl = sum(1 for t in blocked if float(t.get("pnl_yen_100") or 0) < 0)
        early = sum(1 for t in flagged if t.get("early_stop"))
        return {
            "rule": name,
            "flagged_count": len(flagged),
            "early_stop_capture_rate": round(early / len(flagged), 4),
            "early_stop_share_of_all": round(early / n, 4),
            "blocked_winners": bw,
            "blocked_losers": bl,
            "blocked_pnl_yen": round(sum(float(t.get("pnl_yen_100") or 0) for t in flagged), 2),
        }

    for name, pred in singles:
        row = _eval_rule(name, pred)
        if row:
            rows.append(row)

    for (n1, p1), (n2, p2) in combinations(singles[:12], 2):
        combo_name = f"{n1} AND {n2}"

        def _combo(t: Mapping[str, Any], a=p1, b=p2) -> bool:
            return a(t) and b(t)

        row = _eval_rule(combo_name, _combo)
        if row and row.get("flagged_count", 0) >= 5:
            rows.append(row)

    for f1, f2, f3 in combinations(top_feats[:6], 3):
        vals1 = [float(v) for t in trades if (v := _num(t.get(f1))) is not None]
        vals2 = [float(v) for t in trades if (v := _num(t.get(f2))) is not None]
        vals3 = [float(v) for t in trades if (v := _num(t.get(f3))) is not None]
        if min(len(vals1), len(vals2), len(vals3)) < 20:
            continue
        t1 = statistics.median(vals1)
        t2 = statistics.median(vals2)
        t3 = statistics.median(vals3)
        name = f"{f1}>={t1:.4g} AND {f2}<={t2:.4g} AND {f3}>={t3:.4g}"

        def _triple(t: Mapping[str, Any]) -> bool:
            return (
                (_num(t.get(f1)) or -1e18) >= t1
                and (_num(t.get(f2)) or 1e18) <= t2
                and (_num(t.get(f3)) or -1e18) >= t3
            )

        row = _eval_rule(name, _triple)
        if row and row.get("flagged_count", 0) >= 5:
            rows.append(row)

    rows.sort(key=lambda r: (float(r.get("early_stop_capture_rate") or 0), -int(r.get("blocked_winners") or 0)), reverse=True)
    for i, row in enumerate(rows[:100], start=1):
        row["rank"] = i
    return rows[:100]


def _counterfactual_rows(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    chron = sorted(trades, key=lambda t: (str(t.get("day") or ""), str(t.get("entry_time") or ""), str(t.get("symbol") or "")))
    base_m = _metrics(list(chron))
    rows: list[dict[str, Any]] = []

    scenario_ids = (
        "baseline",
        "ban_reentry_30m_after_stop",
        "ban_reentry_60m_after_stop",
        "ban_after_1_stop_same_day",
        "ban_after_2_stops_same_day",
        "reentry_requires_board_improvement",
    )

    for sid in scenario_ids:
        state: dict[str, Any] = {}
        stop_counts: dict[tuple[str, str], int] = defaultdict(int)
        kept: list[dict[str, Any]] = []
        for t in chron:
            day = str(t.get("day") or "")
            sym = str(t.get("symbol") or "")
            key = (day, sym)
            et = _parse_iso(t.get("entry_time"))
            allow = True
            if sid == "ban_reentry_30m_after_stop":
                until = state.get(f"ban30_{key}")
                if until and et and et.timestamp() < float(until):
                    allow = False
            elif sid == "ban_reentry_60m_after_stop":
                until = state.get(f"ban60_{key}")
                if until and et and et.timestamp() < float(until):
                    allow = False
            elif sid == "ban_after_1_stop_same_day":
                if stop_counts[key] >= 1:
                    allow = False
            elif sid == "ban_after_2_stops_same_day":
                if stop_counts[key] >= 2:
                    allow = False
            elif sid == "reentry_requires_board_improvement":
                if state.get(f"need_board_{key}") and not (
                    bool(t.get("board_improvement")) or bool(t.get("num_board_improvement"))
                ):
                    allow = False
            if allow:
                kept.append(t)
            if allow and _is_stop_hit(t):
                stop_counts[key] += 1
                xt = _parse_iso(t.get("exit_time") or t.get("entry_time"))
                if xt:
                    state[f"ban30_{key}"] = xt.timestamp() + 30 * 60
                    state[f"ban60_{key}"] = xt.timestamp() + 60 * 60
                if stop_counts[key] >= 1:
                    state[f"need_board_{key}"] = True
        m = _metrics(kept)
        rows.append(
            {
                "scenario_id": sid,
                "kept_count": len(kept),
                "blocked_count": len(chron) - len(kept),
                "baseline_pnl_yen": base_m.get("pnl_yen_100"),
                "scenario_pnl_yen": m.get("pnl_yen_100"),
                "delta_pnl_yen": round(float(m.get("pnl_yen_100") or 0) - float(base_m.get("pnl_yen_100") or 0), 2),
                "baseline_pf": base_m.get("profit_factor"),
                "scenario_pf": m.get("profit_factor"),
            }
        )
    return rows


def _decide_verdict(
    *,
    early_summary: Mapping[str, Any],
    churn_summary: Mapping[str, Any],
    ranking: Sequence[Mapping[str, Any]],
    combo_rules: Sequence[Mapping[str, Any]],
) -> tuple[str, dict[str, Any]]:
    early_share = float(early_summary.get("early_stop_share_of_stops") or 0)
    churn_loss = float(churn_summary.get("stop_loop_followup_loss_yen") or churn_summary.get("reentry_after_stop_total_pnl_yen") or 0)
    churn_reentry_n = int(churn_summary.get("same_symbol_reentry_after_stop_30m_count") or 0)
    top_d = abs(float(ranking[0].get("cohens_d") or 0)) if ranking else 0.0
    best_combo = next(
        (
            r
            for r in combo_rules
            if "fwd" not in str(r.get("rule") or "").lower()
            and "stop_hit" not in str(r.get("rule") or "").lower()
        ),
        combo_rules[0] if combo_rules else {},
    )
    best_capture = float(best_combo.get("early_stop_capture_rate") or 0)

    answers = {
        "1_early_stop_prevalence": {
            "early_stop_count": early_summary.get("early_stop_count"),
            "share_of_stops": early_share,
            "share_of_entries": early_summary.get("early_stop_share_of_all_entries"),
            "verdict_text": "多い" if early_share >= 0.35 else "中程度" if early_share >= 0.2 else "少ない",
        },
        "2_churn_loss_yen": churn_summary.get("stop_loop_followup_loss_yen"),
        "3_reentry_pnl_source": "損失源" if churn_loss < 0 else "利益源" if churn_loss > 0 else "中立",
        "4_top_features": [r.get("feature") for r in list(ranking)[:5]],
        "5_human_readable_rule_candidate": best_combo.get("rule"),
        "6_flat_weak_shadow_on_early_stop": None,
        "7_next_shadow_candidate": "same_symbol_reentry_cooloff_after_stop_30m",
    }

    if churn_reentry_n >= 10 and churn_loss < -50000 and abs(churn_loss) > abs(float(early_summary.get("early_stop_total_pnl_yen") or 0)) * 0.25:
        verdict = PHASE671_VERDICT_FOUND_CHURN_BUG
    elif top_d >= 0.35 and best_capture >= 0.45:
        verdict = PHASE671_VERDICT_FOUND_SIGNAL
    elif top_d >= 0.2 or churn_reentry_n >= 5:
        verdict = PHASE671_VERDICT_HOLD
    else:
        verdict = PHASE671_VERDICT_REJECT

    return verdict, answers


def _trade_export_rows(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for t in trades:
        rows.append(
            {
                "day": t.get("day"),
                "symbol": t.get("symbol"),
                "entry_time": t.get("entry_time"),
                "entry_type": t.get("entry_type") or t.get("entry_pool"),
                "session_bucket": t.get("session_bucket"),
                "exit_reason": t.get("exit_reason"),
                "hold_sec": _hold_sec(t),
                "early_stop": bool(t.get("early_stop")),
                "pnl_yen_100": t.get("pnl_yen_100"),
                "flat_weak_range_shadow_block": t.get("flat_weak_range_shadow_block"),
                "flat_weak_range_shadow_reason": t.get("flat_weak_range_shadow_reason"),
                "pretrend_shape": t.get("pretrend_shape"),
                "breakout_class": t.get("breakout_class"),
            }
        )
    return rows


def run_audit(*, skip_enrich: bool = False) -> dict[str, Any]:
    disk_before = _disk_usage_pct(NATIVE_ROOT)
    repo_root = resolve_kabu_root(NATIVE_ROOT)
    trades = _load_canonical_trades_extended(repo_root)
    if not skip_enrich:
        trades = _enrich_shadow_flags(trades, repo_root=repo_root)

    early = _early_stop_summary(trades)
    by_symbol = _group_counts([t for t in trades if t.get("early_stop")], lambda t: str(t.get("symbol") or ""))
    by_day = _group_counts([t for t in trades if t.get("early_stop")], lambda t: str(t.get("day") or ""))
    by_bucket = _group_counts(trades, lambda t: str(t.get("session_bucket") or ""))
    by_entry_type = _group_counts(trades, lambda t: str(t.get("entry_type") or t.get("entry_pool") or ""))

    churn_rows, churn_summary = _analyze_churn(trades)
    ranking = _feature_ranking(trades)
    tree_rows = _tree_rules(trades, [r["feature"] for r in ranking[:20]])
    sweep_rows = _threshold_sweep(trades, ranking)
    combo_rows = _combo_rules(trades, ranking)
    cf_rows = _counterfactual_rows(trades)

    early_stops = [t for t in trades if t.get("early_stop")]
    fwr_on_early = sum(1 for t in early_stops if t.get("flat_weak_range_shadow_block"))
    verdict, answers = _decide_verdict(
        early_summary=early,
        churn_summary=churn_summary,
        ranking=ranking,
        combo_rules=combo_rows,
    )
    answers["6_flat_weak_shadow_on_early_stop"] = {
        "early_stop_count": len(early_stops),
        "flat_weak_range_shadow_would_block": fwr_on_early,
        "share": round(fwr_on_early / len(early_stops), 4) if early_stops else 0.0,
    }

    disk_after = _disk_usage_pct(NATIVE_ROOT)
    report: dict[str, Any] = {
        "verdict": verdict,
        "entry_count": len(trades),
        "trading_day_count": len({t.get("day") for t in trades}),
        "canonical_days": list(CANONICAL_DAYS),
        "early_stop_summary": early,
        "churn_summary": churn_summary,
        "mandatory_answers": answers,
        "early_stop_by_symbol_top10": sorted(by_symbol, key=lambda r: r["early_stop_count"], reverse=True)[:10],
        "early_stop_by_day": by_day,
        "early_stop_by_session": by_bucket,
        "early_stop_by_entry_type": by_entry_type,
        "top_features": ranking[:20],
        "best_combo_rules": combo_rows[:10],
        "disk_usage_pct_before": disk_before,
        "disk_usage_pct_after": disk_after,
        "disk_cap_exceeded": disk_after > DISK_USAGE_MAX_PCT,
    }

    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    (REPORT_ROOT / "phase671_early_stop_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    trade_export = _trade_export_rows(trades)
    trade_fields = list(trade_export[0].keys()) if trade_export else []
    _write_csv(REPORT_ROOT / "phase671_early_stop_trades.csv", trade_fields, trade_export)
    _write_csv(
        REPORT_ROOT / "phase671_same_symbol_churn.csv",
        list(churn_rows[0].keys()) if churn_rows else ["day", "symbol"],
        churn_rows,
    )
    _write_csv(
        REPORT_ROOT / "phase671_feature_discovery_rank.csv",
        ["rank", "feature", "early_stop_mean", "non_early_stop_mean", "cohens_d", "mutual_information", "early_stop_n", "non_early_stop_n"],
        ranking,
    )
    _write_csv(REPORT_ROOT / "phase671_tree_rules.csv", ["rule_line", "tree_export"], tree_rows)
    _write_csv(
        REPORT_ROOT / "phase671_threshold_sweep.csv",
        ["rank", "feature", "threshold", "side", "bucket_count", "early_stop_rate", "delta_vs_baseline", "rate_jump"],
        sweep_rows,
    )
    _write_csv(
        REPORT_ROOT / "phase671_counterfactual.csv",
        ["scenario_id", "kept_count", "blocked_count", "baseline_pnl_yen", "scenario_pnl_yen", "delta_pnl_yen", "baseline_pf", "scenario_pf"],
        cf_rows,
    )
    _write_decision_md(report=report)
    return report


def _write_decision_md(*, report: Mapping[str, Any]) -> None:
    ans = report.get("mandatory_answers") or {}
    early = report.get("early_stop_summary") or {}
    churn = report.get("churn_summary") or {}
    lines = [
        "# Phase671 — Early STOP / Same Symbol Churn / Feature Discovery",
        "",
        f"**Verdict:** `{report.get('verdict')}`",
        "",
        "## Mandatory answers",
        "",
        f"1. 5分以内STOPは多いか: {ans.get('1_early_stop_prevalence', {}).get('verdict_text')} "
        f"({early.get('early_stop_count')}件 / stop {early.get('stop_hit_count')}件中)",
        f"2. 同一銘柄STOPループ損失: {churn.get('stop_loop_followup_loss_yen'):+,.0f} yen",
        f"3. stop後re-entry: {ans.get('3_reentry_pnl_source')} (PnL {churn.get('reentry_after_stop_total_pnl_yen'):+,.0f})",
        f"4. 上位特徴量: {', '.join(ans.get('4_top_features') or [])}",
        f"5. 人間可読ルール候補: {ans.get('5_human_readable_rule_candidate')}",
        f"6. Flat Weak+Range Shadow: {ans.get('6_flat_weak_shadow_on_early_stop')}",
        f"7. 次Shadow候補: {ans.get('7_next_shadow_candidate')}",
        "",
        "## Constraints",
        "",
        "- Runtime / YAML / Shadow 変更なし（分析のみ）",
        "",
    ]
    (REPORT_ROOT / "phase671_decision.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    report = run_audit()
    print(json.dumps({"verdict": report["verdict"], "early_stop": report["early_stop_summary"].get("early_stop_count")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
