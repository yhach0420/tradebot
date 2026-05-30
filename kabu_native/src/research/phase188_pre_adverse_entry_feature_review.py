"""
Phase188: Pre-adverse entry feature review (entry-time features only).

Labels use post-entry r30 (A: adverse cluster, B: non-adverse among VWAP candidates),
but feature analysis uses entry-time information only — no r30/r60 as predictors.
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

from research.phase181_entry_expectancy_review import (
    _float,
    _load_events,
    _mean,
    _parse_ts,
    _tick_ratio_pct,
)
from research.phase185_vwap_dev_shadow_candidate_multisession_review import (
    REFERENCE_SESSIONS,
    OBSERVER_EXIT_SESSIONS,
    VWAP_DEV_THRESHOLD_B,
    VwapReviewTrade,
    _bounded_ticks_for_trades,
    _day_stamp_from_session,
    _payload_at_or_before,
    _session_id,
    discover_sessions,
    load_session_trades,
)
from research.phase187_vwap_adverse_cluster_analysis import (
    CandidateTrade,
    _entry_px_map,
    load_enriched_trades,
)
from small_paper.extended_entry_shadow import (
    append_price_tick,
    compute_entry_shadow_fields,
    rolling_mfe_ratio_to_pct,
)

MIN_FEATURE_SAMPLES_PER_GROUP = 5

FEATURE_DEFS: tuple[tuple[str, str, str], ...] = (
    ("continuation_quality", "continuation_quality_score", "numeric"),
    ("momentum_continuation", "momentum_continuation_score", "numeric"),
    ("rolling_mfe", "rolling_mfe_pct", "numeric"),
    ("rolling_mae", "rolling_mae_pct", "numeric"),
    ("rise_5min", "entry_rise_5min_pct", "numeric"),
    ("rise_10min", "entry_rise_10min_pct", "numeric"),
    ("near_day_high", "entry_near_day_high_pct", "numeric"),
    ("high_break_recent", "entry_high_break_recent", "boolean"),
    ("trading_value", "trading_value", "numeric"),
    ("turnover_proxy", "turnover_proxy", "numeric"),
    ("current_price", "current_price", "numeric"),
    ("tick_ratio", "tick_ratio_pct", "numeric"),
)


@dataclass
class EntryFeatureRow:
    candidate: CandidateTrade
    cluster: str  # A_adverse_r30_lt_0 | B_non_adverse_r30_gte_0
    features: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.candidate.to_dict(),
            "cluster": self.cluster,
            **self.features,
        }


def _load_accept_snapshots(session_dir: Path) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    csv_path = session_dir / "small_paper_events.csv"
    if csv_path.is_file():
        with csv_path.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if str(row.get("event_type") or "") != "accepted":
                    continue
                sym = str(row.get("symbol") or "").strip()
                ent = str(row.get("entry_time") or "").strip()
                if sym and ent:
                    out[(sym, ent)] = dict(row)
        return out
    jsonl = session_dir / "small_paper_events.jsonl"
    if jsonl.is_file():
        for line in jsonl.open(encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("event_type") != "accepted":
                continue
            sym = str(ev.get("symbol") or "").strip()
            ent = str(ev.get("entry_time") or "").strip()
            if sym and ent:
                out[(sym, ent)] = dict(ev)
    return out


def _load_structural_row(session_dir: Path) -> dict[tuple[str, str], dict[str, Any]]:
    path = session_dir / "structural_trades.csv"
    out: dict[tuple[str, str], dict[str, Any]] = {}
    if not path.is_file():
        return out
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            sym = str(row.get("symbol") or "").strip()
            ent = str(row.get("entry_time") or "").strip()
            if sym and ent:
                out[(sym, ent)] = dict(row)
    return out


def _shadow_features_for_trade(
    *,
    accept: dict[str, Any],
    push_ticks: Sequence[tuple[float, dict[str, Any]]],
    entry_ts: float,
    entry_px: float,
) -> dict[str, Any]:
    ring: list[tuple[float, float]] = []
    for ts, payload in push_ticks:
        if ts > entry_ts:
            break
        try:
            px = float(payload.get("CurrentPrice") or 0)
        except (TypeError, ValueError):
            px = 0.0
        if px > 0:
            append_price_tick(ring, ts=ts, px=px)
    payload = _payload_at_or_before(push_ticks, entry_ts)
    if payload.get("CurrentPrice") is None:
        payload = {**payload, "CurrentPrice": entry_px}
    shadow = compute_entry_shadow_fields(
        trade=accept,
        payload=payload,
        price_ring=ring,
        entry_ts=entry_ts,
        session_momentum_samples=[],
    )
    return shadow


def _build_entry_features(
    candidate: CandidateTrade,
    *,
    accept: dict[str, Any],
    structural: dict[str, Any],
    push_ticks: Sequence[tuple[float, dict[str, Any]]],
    entry_px: float,
) -> dict[str, Any]:
    merged = {**structural, **accept}
    shadow = _shadow_features_for_trade(
        accept=merged,
        push_ticks=push_ticks,
        entry_ts=candidate.entry_ts,
        entry_px=entry_px,
    )
    px = _float(merged.get("current_price")) or entry_px
    tick = _float(merged.get("tick_ratio_pct"))
    if tick is None and px and px > 0:
        tick = _tick_ratio_pct(px)
    rolling_mfe = _float(merged.get("rolling_mfe_pct"))
    rolling_mae = _float(merged.get("rolling_mae_pct"))
    return {
        "continuation_quality": _float(merged.get("continuation_quality_score")),
        "momentum_continuation": _float(merged.get("momentum_continuation_score")),
        "rolling_mfe": rolling_mfe_ratio_to_pct(rolling_mfe) if rolling_mfe is not None else None,
        "rolling_mfe_ratio": rolling_mfe,
        "rolling_mae": rolling_mae,
        "rise_5min": shadow.get("entry_rise_5min_pct"),
        "rise_10min": shadow.get("entry_rise_10min_pct"),
        "near_day_high": shadow.get("entry_near_day_high_pct"),
        "high_break_recent": bool(shadow.get("entry_high_break_recent")),
        "trading_value": _float(merged.get("trading_value")),
        "turnover_proxy": _float(merged.get("turnover_proxy")),
        "current_price": px,
        "tick_ratio": tick,
    }


def load_labeled_entry_rows(repo_root: Path, base: Path) -> tuple[list[EntryFeatureRow], dict[str, Any]]:
    session_dirs, excluded = discover_sessions(base)
    trades_by_session: dict[str, list[VwapReviewTrade]] = {}
    for sdir in session_dirs:
        sid = _session_id(sdir, base)
        trades_by_session[sid] = load_session_trades(sdir, repo_root=repo_root, base=base)

    candidates = load_enriched_trades(repo_root, base, all_trades_by_session=trades_by_session)
    labeled: list[EntryFeatureRow] = []
    r30_unknown = 0

    session_dir_by_id = {_session_id(s, base): s for s in session_dirs}

    labeled_with_r30 = [c for c in candidates if c.r30_sec is not None]
    session_symbol_times: dict[str, dict[str, list[float]]] = {}
    for cand in labeled_with_r30:
        session_symbol_times.setdefault(cand.session_id, {}).setdefault(cand.symbol, []).append(
            cand.entry_ts
        )

    session_cache: dict[str, dict[str, Any]] = {}

    for cand in labeled_with_r30:
        if cand.r30_sec is None:
            r30_unknown += 1
            continue
        cluster = "A_adverse_r30_lt_0" if cand.r30_sec < 0 else "B_non_adverse_r30_gte_0"
        sdir = session_dir_by_id.get(cand.session_id)
        if sdir is None:
            continue

        if cand.session_id not in session_cache:
            day_stamp = _day_stamp_from_session(sdir)
            y = f"{day_stamp[:4]}-{day_stamp[4:6]}-{day_stamp[6:8]}"
            push_dir = repo_root / "kabu_native" / "data" / "push_jsonl" / y
            tick_cache: dict[str, list[tuple[float, dict[str, Any]]]] = {}
            for sym, times in session_symbol_times.get(cand.session_id, {}).items():
                tick_cache[sym] = _bounded_ticks_for_trades(push_dir, sym, times)
            session_cache[cand.session_id] = {
                "accept_map": _load_accept_snapshots(sdir),
                "struct_map": _load_structural_row(sdir),
                "entry_px_map": _entry_px_map(sdir),
                "tick_cache": tick_cache,
            }
        cache = session_cache[cand.session_id]
        key = (cand.symbol, cand.entry_time)
        accept = cache["accept_map"].get(key, {})
        structural = cache["struct_map"].get(key, {})
        entry_px = cache["entry_px_map"].get(key, 0.0)
        push_ticks = cache["tick_cache"].get(cand.symbol, [])
        feats = _build_entry_features(
            cand,
            accept=accept,
            structural=structural,
            push_ticks=push_ticks,
            entry_px=entry_px,
        )
        labeled.append(EntryFeatureRow(candidate=cand, cluster=cluster, features=feats))

    meta = {
        "candidate_total": len(candidates),
        "labeled_with_r30": len(labeled),
        "r30_unknown_excluded": r30_unknown,
        "excluded_sessions": excluded,
    }
    return labeled, meta


def _cohens_d(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    if len(a) < 2 or len(b) < 2:
        return None
    ma = sum(a) / len(a)
    mb = sum(b) / len(b)
    va = sum((x - ma) ** 2 for x in a) / (len(a) - 1)
    vb = sum((x - mb) ** 2 for x in b) / (len(b) - 1)
    pooled = math.sqrt(((len(a) - 1) * va + (len(b) - 1) * vb) / max(1, len(a) + len(b) - 2))
    if pooled <= 0:
        return 0.0
    return (ma - mb) / pooled


def _feature_comparison(
    rows: Sequence[EntryFeatureRow],
    feature: str,
    kind: str,
) -> dict[str, Any]:
    a_rows = [r for r in rows if r.cluster == "A_adverse_r30_lt_0"]
    b_rows = [r for r in rows if r.cluster == "B_non_adverse_r30_gte_0"]
    if kind == "boolean":
        a_true = sum(1 for r in a_rows if r.features.get(feature))
        b_true = sum(1 for r in b_rows if r.features.get(feature))
        a_rate = a_true / max(1, len(a_rows))
        b_rate = b_true / max(1, len(b_rows))
        return {
            "feature": feature,
            "kind": kind,
            "A_rate_true": round(a_rate, 4),
            "B_rate_true": round(b_rate, 4),
            "rate_delta_A_minus_B": round(a_rate - b_rate, 4),
            "A_count": len(a_rows),
            "B_count": len(b_rows),
            "importance_score": round(abs(a_rate - b_rate), 4),
            "direction": "higher_in_A" if a_rate > b_rate else "higher_in_B",
        }

    a_vals = [_float(r.features.get(feature)) for r in a_rows]
    a_vals = [v for v in a_vals if v is not None]
    b_vals = [_float(r.features.get(feature)) for r in b_rows]
    b_vals = [v for v in b_vals if v is not None]
    out: dict[str, Any] = {
        "feature": feature,
        "kind": kind,
        "A_mean": round(_mean(a_vals), 4) if a_vals else None,
        "B_mean": round(_mean(b_vals), 4) if b_vals else None,
        "A_median": round(sorted(a_vals)[len(a_vals) // 2], 4) if a_vals else None,
        "B_median": round(sorted(b_vals)[len(b_vals) // 2], 4) if b_vals else None,
        "A_n": len(a_vals),
        "B_n": len(b_vals),
        "mean_delta_A_minus_B": None,
        "cohens_d_A_minus_B": None,
        "importance_score": None,
        "direction": None,
    }
    if len(a_vals) < MIN_FEATURE_SAMPLES_PER_GROUP or len(b_vals) < MIN_FEATURE_SAMPLES_PER_GROUP:
        out["insufficient_samples"] = True
        return out
    d = _cohens_d(a_vals, b_vals)
    if d is not None:
        out["cohens_d_A_minus_B"] = round(d, 4)
        out["importance_score"] = round(abs(d), 4)
        out["direction"] = "higher_in_A" if d > 0 else "higher_in_B"
    if out["A_mean"] is not None and out["B_mean"] is not None:
        out["mean_delta_A_minus_B"] = round(float(out["A_mean"]) - float(out["B_mean"]), 4)
    return out


def _rank_features(comparisons: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = [c for c in comparisons if c.get("importance_score") is not None]
    ranked.sort(key=lambda x: float(x["importance_score"]), reverse=True)
    for i, row in enumerate(ranked, start=1):
        row["rank"] = i
    return ranked


def evaluate_pre_adverse_entry_feature_review(*, repo_root: Path) -> dict[str, Any]:
    base = repo_root / "kabu_native" / "results" / "small_paper"
    rows, meta = load_labeled_entry_rows(repo_root, base)

    a_rows = [r for r in rows if r.cluster == "A_adverse_r30_lt_0"]
    b_rows = [r for r in rows if r.cluster == "B_non_adverse_r30_gte_0"]

    comparisons = [
        _feature_comparison(rows, name, kind) for name, _key, kind in FEATURE_DEFS
    ]
    ranking = _rank_features(comparisons)
    top3 = ranking[:3]

    false_positives = [r for r in b_rows if r.candidate.pnl_pct > 0]
    false_positive_profile = {
        "count": len(false_positives),
        "features": {
            name: round(
                _mean([_float(r.features.get(name)) for r in false_positives if _float(r.features.get(name)) is not None])
                or 0,
                4,
            )
            if FEATURE_DEFS[[f[0] for f in FEATURE_DEFS].index(name)][2] == "numeric"
            else round(sum(1 for r in false_positives if r.features.get(name)) / max(1, len(false_positives)), 4)
            for name, _, kind in FEATURE_DEFS
            if kind == "numeric"
        },
    }
    fp_numeric = {}
    for name, _, kind in FEATURE_DEFS:
        if kind != "numeric":
            continue
        vals = [_float(r.features.get(name)) for r in false_positives]
        vals = [v for v in vals if v is not None]
        fp_numeric[name] = round(_mean(vals), 4) if vals else None
    fp_bool = {}
    for name, _, kind in FEATURE_DEFS:
        if kind != "boolean":
            continue
        fp_bool[name] = round(
            sum(1 for r in false_positives if r.features.get(name)) / max(1, len(false_positives)), 4
        )
    false_positive_profile = {
        "count": len(false_positives),
        "note": "B cluster trades with pnl>0 (profitable despite non-adverse r30 label context)",
        "avg_features_numeric": fp_numeric,
        "rate_features_boolean": fp_bool,
    }

    winners_a = [r for r in a_rows if r.candidate.pnl_pct > 0]
    losers_a = [r for r in a_rows if r.candidate.pnl_pct <= 0]

    def _group_feature_means(group: Sequence[EntryFeatureRow]) -> dict[str, Any]:
        out: dict[str, Any] = {"trade_count": len(group)}
        for name, _, kind in FEATURE_DEFS:
            if kind == "boolean":
                out[name] = round(
                    sum(1 for r in group if r.features.get(name)) / max(1, len(group)), 4
                )
            else:
                vals = [_float(r.features.get(name)) for r in group]
                vals = [v for v in vals if v is not None]
                out[name] = round(_mean(vals), 4) if vals else None
        return out

    pre_entry_predictable = bool(
        top3
        and float(top3[0].get("importance_score") or 0) >= 0.35
        and len(a_rows) >= MIN_FEATURE_SAMPLES_PER_GROUP
        and len(b_rows) >= MIN_FEATURE_SAMPLES_PER_GROUP
    )

    return {
        "phase": 188,
        "mode": "pre_adverse_entry_feature_review",
        "hypothesis": (
            "Entry-time features can distinguish VWAP candidate adverse cluster (r30<0) "
            "from non-adverse VWAP candidates without using future returns as features."
        ),
        "constraints": {
            "hard_reject": False,
            "shadow_review_only": True,
            "no_future_features": True,
            "r30_r60_label_only_not_predictor": True,
            "no_single_day_optimization": True,
            "fixed_comparisons_only": True,
        },
        "reference_session_set": list(REFERENCE_SESSIONS) + list(OBSERVER_EXIT_SESSIONS),
        "meta": meta,
        "clusters": {
            "A_adverse_r30_lt_0": {
                "description": "vwap_shadow_reject_candidate AND r30_sec < 0",
                "trade_count": len(a_rows),
            },
            "B_non_adverse_r30_gte_0": {
                "description": "vwap_shadow_reject_candidate AND r30_sec >= 0",
                "trade_count": len(b_rows),
            },
        },
        "feature_comparisons": {c["feature"]: c for c in comparisons},
        "feature_importance_ranking": ranking,
        "top_3_adverse_cluster_features": top3,
        "false_positive_profitable_in_B": false_positive_profile,
        "within_candidate_A_winners_vs_losers": {
            "winners_pnl_gt_0": _group_feature_means(winners_a),
            "losers_pnl_lte_0": _group_feature_means(losers_a),
        },
        "verdict": {
            "pre_entry_predictable": pre_entry_predictable,
            "top_feature": top3[0]["feature"] if top3 else None,
            "top_feature_importance": top3[0].get("importance_score") if top3 else None,
            "note": (
                "r30 used only for cluster labeling; all ranked features are entry-time only. "
                "Moderate separation suggests partial pre-entry signal, not full predictability."
            ),
        },
        "trades": [r.to_dict() for r in rows],
    }
