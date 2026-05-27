"""
Phase 124: Predict MFE>0.15 at fade time using only contemporaneous features (review only).
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.fade_extension_conditions import (
    _build_sym_timelines,
    _candidate_rank_at_entry,
    _load_candidate_events,
    _nearest_snapshot,
    _overlap_replaced_before,
    build_fade_cluster_rows,
)
from research.mfe_mae_exit_review import as_float, load_structural_trades, parse_ts

LABEL_THRESHOLD = 0.15
EXCLUDE_FROM_RULES = frozenset(
    {
        "mfe_pct",
        "mfe_so_far",
        "mfe_label",
        "hold60_pnl",
        "hold60_delta",
        "cluster",
        "cluster_label",
        "session_id",
        "symbol",
        "entry_time",
        "close_time",
        "exit_reason",
        "quality_tier",
        "session_bucket",
    }
)

BOOL_FEATURES = ("take_reached", "overlap_replaced", "vwap_above")
NUMERIC_FEATURES = (
    "quality_score",
    "quality_at_fade",
    "entry_quality_snapshot",
    "candidate_rank",
    "accepted_rank",
    "hold_sec",
    "pnl_at_fade",
    "mae_so_far",
    "momentum_at_fade",
    "favorable_continuation",
    "range_pct",
    "atr_pct",
    "volume_ratio",
    "volume_acceleration",
    "price_change_pct",
    "vwap_distance",
    "vol_liq_score",
)


def _fade_snapshot(
    by_sym: dict[str, list[tuple[float, dict[str, Any]]]],
    symbol: str,
    entry_ts: float,
    close_ts: float,
) -> Optional[dict[str, Any]]:
    items = by_sym.get(symbol) or []
    best: Optional[dict[str, Any]] = None
    best_ts = -1.0
    for ts, row in items:
        if entry_ts <= ts <= close_ts and ts >= best_ts:
            best_ts = ts
            best = row
    return best


def _entry_snapshot(
    by_sym: dict[str, list[tuple[float, dict[str, Any]]]],
    symbol: str,
    entry_ts: float,
) -> Optional[dict[str, Any]]:
    return _nearest_snapshot(by_sym, symbol, entry_ts)


def enrich_fade_predictor_rows(session_dirs: Sequence[Path]) -> list[dict[str, Any]]:
    base = build_fade_cluster_rows([Path(p) for p in session_dirs])
    enriched: list[dict[str, Any]] = []

    by_session_events: dict[str, dict[str, list[tuple[float, dict[str, Any]]]]] = {}
    by_session_cands: dict[str, list[tuple[float, str, float]]] = {}

    for sdir in session_dirs:
        sdir = Path(sdir)
        sid = str(sdir.relative_to(sdir.parent.parent)) if sdir.parent.parent else sdir.name
        events_csv = sdir / "small_paper_events.csv"
        by_session_events[sid] = _build_sym_timelines(events_csv)
        by_session_cands[sid] = _load_candidate_events(events_csv)

    for row in base:
        sid = str(row.get("session_id") or "")
        sym = str(row.get("symbol") or "")
        entry_ts = parse_ts(str(row.get("entry_time") or ""))
        close_ts = parse_ts(str(row.get("close_time") or ""))
        sym_events = by_session_events.get(sid, {})
        cands = by_session_cands.get(sid, [])

        entry_snap = _entry_snapshot(sym_events, sym, entry_ts)
        fade_snap = _fade_snapshot(sym_events, sym, entry_ts, close_ts)

        entry_q = as_float(row.get("quality_score"))
        if entry_snap:
            entry_q = entry_q or as_float(entry_snap.get("continuation_quality_score"))

        fade_q = as_float(fade_snap.get("continuation_quality_score")) if fade_snap else None
        mfe_so_far = as_float(fade_snap.get("rolling_mfe_pct")) if fade_snap else None
        mae_so_far = as_float(fade_snap.get("rolling_mae_pct")) if fade_snap else None
        momentum = as_float(fade_snap.get("momentum_continuation_score")) if fade_snap else None
        fav = as_float(fade_snap.get("favorable_continuation")) if fade_snap else None
        range_pct = as_float(fade_snap.get("intraday_range_pct")) if fade_snap else None
        atr_pct = as_float(fade_snap.get("atr_pct")) if fade_snap else None
        turn_entry = as_float(entry_snap.get("turnover_proxy")) if entry_snap else None
        turn_fade = as_float(fade_snap.get("turnover_proxy")) if fade_snap else None

        vwap_dist = row.get("vwap_distance")
        if fade_snap:
            qc_raw = fade_snap.get("quality_components_json") or ""
            if qc_raw and vwap_dist is None:
                try:
                    qc = json.loads(qc_raw)
                    vwap_dist = as_float(qc.get("vwap_distance_pct") or qc.get("vwap_distance"))
                except json.JSONDecodeError:
                    pass

        vol_ratio = None
        if turn_entry and turn_fade and turn_entry > 0:
            vol_ratio = round(turn_fade / turn_entry, 4)
        vol_accel = None
        if turn_entry is not None and turn_fade is not None and close_ts > entry_ts:
            vol_accel = round((turn_fade - turn_entry) / (close_ts - entry_ts), 6)

        rank = row.get("candidate_rank")
        if rank is None:
            rank = _candidate_rank_at_entry(cands, sym, entry_ts)

        mfe_pct = as_float(row.get("mfe_pct"))
        label = mfe_pct is not None and float(mfe_pct) > LABEL_THRESHOLD

        enriched.append(
            {
                **row,
                "mfe_label": "positive" if label else "negative",
                "mfe_label_positive": label,
                "pnl_at_fade": row.get("pnl_at_exit"),
                "mfe_so_far": mfe_so_far,
                "mae_so_far": mae_so_far if mae_so_far is not None else row.get("mae_pct"),
                "quality_at_fade": fade_q,
                "entry_quality_snapshot": entry_q,
                "momentum_at_fade": momentum,
                "favorable_continuation": fav,
                "range_pct": range_pct,
                "atr_pct": atr_pct,
                "volume_ratio": vol_ratio,
                "volume_acceleration": vol_accel,
                "price_change_pct": row.get("pnl_at_exit"),
                "vwap_distance": vwap_dist,
                "vwap_above": (float(vwap_dist) > 0) if vwap_dist is not None else None,
                "accepted_rank": row.get("message_index"),
                "candidate_rank": rank,
            }
        )

    return enriched


def _available_features(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    out: list[str] = []
    n = len(rows)
    if n == 0:
        return out
    for feat in NUMERIC_FEATURES + BOOL_FEATURES:
        present = sum(1 for r in rows if r.get(feat) is not None)
        if present >= max(10, int(n * 0.15)):
            out.append(feat)
    return out


def compare_positive_negative(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    pos = [r for r in rows if r.get("mfe_label_positive")]
    neg = [r for r in rows if not r.get("mfe_label_positive")]
    feats = list(NUMERIC_FEATURES) + list(BOOL_FEATURES) + ["mfe_pct", "mfe_so_far"]
    comparison: list[dict[str, Any]] = []

    for feat in feats:
        pvals = [as_float(r.get(feat)) for r in pos if as_float(r.get(feat)) is not None]
        nvals = [as_float(r.get(feat)) for r in neg if as_float(r.get(feat)) is not None]
        if feat in BOOL_FEATURES:
            pr = sum(1 for r in pos if r.get(feat) in (True, "True", 1)) / len(pos) if pos else None
            nr = sum(1 for r in neg if r.get(feat) in (True, "True", 1)) / len(neg) if neg else None
            comparison.append(
                {
                    "feature": feat,
                    "positive_count": len(pos),
                    "negative_count": len(neg),
                    "positive_mean": pr,
                    "negative_mean": nr,
                    "mean_delta": round(pr - nr, 4) if pr is not None and nr is not None else None,
                    "present_rate": round(
                        (len([r for r in rows if r.get(feat) is not None])) / len(rows), 4
                    ),
                }
            )
            continue
        if not pvals and not nvals:
            continue
        pm = statistics.mean(pvals) if pvals else None
        nm = statistics.mean(nvals) if nvals else None
        comparison.append(
            {
                "feature": feat,
                "positive_count": len(pvals),
                "negative_count": len(nvals),
                "positive_mean": round(pm, 4) if pm is not None else None,
                "negative_mean": round(nm, 4) if nm is not None else None,
                "mean_delta": round(pm - nm, 4) if pm is not None and nm is not None else None,
                "present_rate": round(
                    len([r for r in rows if as_float(r.get(feat)) is not None]) / len(rows), 4
                ),
            }
        )
    comparison.sort(
        key=lambda x: abs(float(x.get("mean_delta") or 0)), reverse=True
    )
    return comparison


def _eval_rule(rows: Sequence[Mapping[str, Any]], rule: Mapping[str, Any]) -> dict[str, Any]:
    n = len(rows)
    positives = [r for r in rows if r.get("mfe_label_positive")]
    n_pos = len(positives)
    selected = [r for r in rows if _rule_mask_predict(r, rule)]
    sel_n = len(selected)
    tp = sum(1 for r in selected if r.get("mfe_label_positive"))
    prec = tp / sel_n if sel_n else None
    rec = tp / n_pos if n_pos else None
    cov = sel_n / n if n else None
    delta = round(sum(float(r.get("hold60_delta") or 0) for r in selected), 4)
    return {
        "rule": rule,
        "description": _rule_desc(rule),
        "selected_trade_count": sel_n,
        "coverage": round(cov, 4) if cov is not None else None,
        "precision": round(prec, 4) if prec is not None else None,
        "recall": round(rec, 4) if rec is not None else None,
        "true_positive": tp,
        "selected_total_delta": delta,
        "avg_hold60_delta": round(delta / sel_n, 4) if sel_n else None,
    }


def _rule_mask_predict(row: Mapping[str, Any], rule: Mapping[str, Any]) -> bool:
    for key, val in rule.items():
        if key.endswith("_gt"):
            feat = key[:-3]
            v = as_float(row.get(feat))
            if v is None or v <= float(val):
                return False
        elif key.endswith("_lt"):
            feat = key[:-3]
            v = as_float(row.get(feat))
            if v is None or v >= float(val):
                return False
        elif key.endswith("_lte"):
            feat = key[:-4]
            v = as_float(row.get(feat))
            if v is None or v > float(val):
                return False
        else:
            rv = row.get(key)
            if rv is None:
                return False
            if bool(rv) != bool(val):
                return False
    return True


def _rule_desc(rule: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for k, v in rule.items():
        if k.endswith("_gt"):
            parts.append(f"{k[:-3]} > {v}")
        elif k.endswith("_lt"):
            parts.append(f"{k[:-3]} < {v}")
        elif k.endswith("_lte"):
            parts.append(f"{k[:-4]} <= {v}")
        else:
            parts.append(f"{k} = {v}")
    return " AND ".join(parts)


def _single_rule_candidates(
    rows: Sequence[Mapping[str, Any]],
    features: Sequence[str],
) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    thresholds: dict[str, list[float]] = {
        "quality_score": [0.65, 0.68, 0.7, 0.72, 0.75, 0.78, 0.8, 0.85],
        "entry_quality_snapshot": [0.65, 0.7, 0.72, 0.75, 0.78, 0.8],
        "quality_at_fade": [0.65, 0.7, 0.72, 0.75, 0.78, 0.8],
        "hold_sec": [8, 10, 12, 15, 20, 25, 30],
        "pnl_at_fade": [-0.2, -0.1, 0.0, 0.05, 0.1, 0.15],
        "mae_so_far": [-0.3, -0.15, -0.1, 0.0],
        "momentum_at_fade": [0.3, 0.4, 0.5, 0.6],
        "favorable_continuation": [0.5, 0.75, 1.0],
        "range_pct": [1.5, 2.0, 2.5, 3.0],
        "atr_pct": [1.5, 2.0, 2.5],
        "volume_ratio": [0.9, 1.0, 1.05, 1.1],
        "volume_acceleration": [0.0, 0.0001, 0.001],
        "price_change_pct": [-0.1, 0.0, 0.05, 0.1],
        "vwap_distance": [0.0, 0.2, 0.35, 0.5],
        "candidate_rank": [3, 5, 8, 10],
        "accepted_rank": [1000, 5000, 10000],
        "vol_liq_score": [20, 25, 30, 35],
    }

    for feat in features:
        if feat in BOOL_FEATURES:
            for val in (True, False):
                rules.append(_eval_rule(rows, {feat: val}))
            continue
        if feat not in thresholds:
            vals = [
                as_float(r.get(feat))
                for r in rows
                if as_float(r.get(feat)) is not None
            ]
            if not vals:
                continue
            qs = statistics.quantiles(vals, n=4) if len(vals) >= 8 else [statistics.median(vals)]
            thresholds[feat] = [round(q, 4) for q in qs]

        for thr in thresholds.get(feat, []):
            if feat in ("candidate_rank", "accepted_rank"):
                rules.append(_eval_rule(rows, {f"{feat}_lte": thr}))
            else:
                rules.append(_eval_rule(rows, {f"{feat}_gt": thr}))

    rules = [r for r in rules if r.get("selected_trade_count", 0) >= 5]
    rules.sort(
        key=lambda r: (
            float(r.get("precision") or 0),
            float(r.get("selected_total_delta") or -1e9),
        ),
        reverse=True,
    )
    return rules


def _combo_rules(
    rows: Sequence[Mapping[str, Any]],
    singles: Sequence[Mapping[str, Any]],
    *,
    max_depth: int = 3,
    top_n: int = 12,
) -> list[dict[str, Any]]:
    bases = [s["rule"] for s in singles[:top_n] if s.get("precision", 0) and s["precision"] >= 0.35]
    combos: list[dict[str, Any]] = []
    seen: set[str] = set()

    for depth in range(2, max_depth + 1):
        for parts in combinations(bases, depth):
            merged: dict[str, Any] = {}
            for p in parts:
                merged.update(p)
            key = json.dumps(merged, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            ev = _eval_rule(rows, merged)
            if ev.get("selected_trade_count", 0) >= 5:
                combos.append(ev)

    combos.sort(
        key=lambda r: (
            float(r.get("precision") or 0),
            float(r.get("selected_total_delta") or -1e9),
        ),
        reverse=True,
    )
    return combos


def determine_verdict(
    rows: Sequence[Mapping[str, Any]],
    comparison: Sequence[Mapping[str, Any]],
    top_rules: Sequence[Mapping[str, Any]],
    available: Sequence[str],
) -> tuple[str, list[str]]:
    notes: list[str] = []
    n = len(rows)
    n_pos = sum(1 for r in rows if r.get("mfe_label_positive"))
    base_rate = n_pos / n if n else 0
    notes.append(f"n={n} positive={n_pos} base_rate={base_rate:.3f} features={len(available)}")

    missing_feats = [f for f in NUMERIC_FEATURES if f not in available]
    if len(missing_feats) > len(NUMERIC_FEATURES) * 0.5:
        return "need_additional_features", notes + [f"missing={missing_feats[:8]}"]

    if not top_rules:
        return "not_predictable", notes

    best = top_rules[0]
    prec = float(best.get("precision") or 0)
    rec = float(best.get("recall") or 0)
    delta = float(best.get("selected_total_delta") or 0)
    notes.append(
        f"best={best.get('description')} prec={prec:.3f} rec={rec:.3f} delta={delta:.3f}"
    )

    if prec >= 0.55 and rec >= 0.2 and delta > 0.5 and prec > base_rate + 0.08:
        return "predictable_extension_candidate", notes

    if prec >= 0.45 and (rec >= 0.15 or delta > 0):
        return "weak_predictive_signal", notes

    if len(available) < 4:
        return "need_additional_features", notes

    return "not_predictable", notes


def analyze_mfe_predictor(session_dirs: Sequence[Path]) -> dict[str, Any]:
    rows = enrich_fade_predictor_rows(session_dirs)
    available = _available_features(rows)
    comparison = compare_positive_negative(rows)
    singles = _single_rule_candidates(rows, [f for f in available if f not in EXCLUDE_FROM_RULES])
    combos = _combo_rules(rows, singles)
    all_rules = singles + combos
    all_rules.sort(
        key=lambda r: (
            float(r.get("precision") or 0),
            float(r.get("selected_total_delta") or -1e9),
        ),
        reverse=True,
    )
    top_rules = all_rules[:30]
    verdict, notes = determine_verdict(rows, comparison, top_rules, available)

    return {
        "verdict": verdict,
        "verdict_notes": notes,
        "fade_trade_count": len(rows),
        "positive_count": sum(1 for r in rows if r.get("mfe_label_positive")),
        "negative_count": sum(1 for r in rows if not r.get("mfe_label_positive")),
        "label_threshold_mfe_pct": LABEL_THRESHOLD,
        "available_features": available,
        "skipped_features": [f for f in NUMERIC_FEATURES + BOOL_FEATURES if f not in available],
        "positive_negative_comparison": comparison,
        "rule_search_results": all_rules[:80],
        "top_predictive_rules": top_rules[:15],
        "trade_rows": rows,
    }
