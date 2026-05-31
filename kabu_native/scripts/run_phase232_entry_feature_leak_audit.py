#!/usr/bin/env python3
"""
Phase232: Entry-time feature leak audit (review only).

Audit Phase229 score map and Phase231 adopted clusters for entry-time availability.
Invalidate clusters containing future-leak features; recalc Score>=5/6 impact.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal, Optional

REPO = Path(__file__).resolve().parents[2]
P229 = REPO / "kabu_native/results/reports/phase229_entry_score_discovery.json"
P231 = REPO / "kabu_native/results/reports/phase231_score_cohort_expectancy_discovery.json"
OUT = REPO / "kabu_native/results/reports/phase232_entry_feature_leak_audit.json"

LOOKBACK_SEC = 600.0
SCORE_GE5 = 5
SCORE_GE6 = 6

FeatureClass = Literal["entry_available", "after_entry_only", "research_offline_pre_entry", "unknown"]

# Label → (field, classification, evidence)
FEATURE_REGISTRY: dict[str, dict[str, Any]] = {
    "Board": {
        "field": "entry_order_book_imbalance",
        "class": "entry_available",
        "live_source": "pilot_runner EVENT_FIELDS + board_imbalance_shadow at accept",
        "notes": "Logged on accept from PUSH payload / shadow compute.",
    },
    "VWAP": {
        "field": "entry_vwap_dev_pct",
        "class": "entry_available",
        "live_source": "extended_entry_shadow at accept",
        "notes": "CurrentPrice vs VWAP at accept tick.",
    },
    "TV": {
        "field": "trading_value",
        "class": "entry_available",
        "live_source": "pilot_runner EVENT_FIELDS at accept",
        "notes": "From PUSH / suitability at accept.",
    },
    "TickRatio": {
        "field": "tick_ratio_pct",
        "class": "entry_available",
        "live_source": "entry_price_risk_guard at accept",
        "notes": "Sparse coverage when guard inactive.",
    },
    "Momentum": {
        "field": "momentum_continuation_score",
        "class": "entry_available",
        "live_source": "LiveFeatureBridge → accept event",
        "notes": "Rolling pre-entry ticks only.",
    },
    "Duration": {
        "field": "max_continuation_duration",
        "class": "entry_available",
        "live_source": "LiveFeatureBridge favorable streak at accept",
        "notes": "Not hold duration; streak before entry.",
    },
    "RollingMFE": {
        "field": "rolling_mfe_pct",
        "class": "entry_available",
        "live_source": "LiveFeatureBridge snapshot at accept (phase217 uses acc row)",
        "notes": (
            "Pre-entry rolling window MFE vs ref_price. "
            "Exit events overwrite rolling_mfe_pct with peak_pnl — research must use accept row only."
        ),
        "focus_audit": True,
    },
    "RollingMAE": {
        "field": "rolling_mae_pct",
        "class": "entry_available",
        "live_source": "LiveFeatureBridge snapshot at accept",
        "notes": "Pre-entry rolling MAE; exit row overwrites with path MAE — accept row only.",
        "focus_audit": True,
    },
    "Quality": {
        "field": "continuation_quality_score",
        "class": "entry_available",
        "live_source": "LiveFeatureBridge / gate at accept",
        "notes": "Quality from pre-entry components.",
    },
    "Rise5m": {
        "field": "entry_rise_5min_pct",
        "class": "entry_available",
        "live_source": "extended_entry_shadow price_ring at accept",
        "notes": "300s lookback from ring; offline backfill if missing.",
    },
    "Rise10m": {
        "field": "entry_rise_10min_pct",
        "class": "entry_available",
        "live_source": "extended_entry_shadow price_ring at accept",
        "notes": "600s lookback from ring.",
    },
    "HBCount": {
        "field": "high_break_count_full_jsonl",
        "class": "research_offline_pre_entry",
        "live_source": "NOT in pilot_runner EVENT_FIELDS",
        "notes": (
            "Phase223: not logged at accept. Offline recompute from push jsonl ticks t<=entry_ts. "
            "No post-entry temporal leak, but NOT available at live entry without new logging."
        ),
        "focus_audit": True,
    },
    "HBRecent": {
        "field": "entry_high_break_recent",
        "class": "entry_available",
        "live_source": "extended_entry_shadow _high_break_recent at accept",
        "notes": (
            "Research Phase229 uses high_break_recent_recomputed (full jsonl, t<=entry). "
            "Live logs entry_high_break_recent from price_ring — same logic, entry-time only."
        ),
        "focus_audit": True,
    },
    "Price": {
        "field": "current_price",
        "class": "entry_available",
        "live_source": "accept event CurrentPrice",
        "notes": "Entry price at accept.",
    },
    "Turnover": {
        "field": "turnover_proxy",
        "class": "entry_available",
        "live_source": "accept event / suitability",
        "notes": "Sparse coverage.",
    },
}

# Fields that must never be used at entry (post-entry / path metrics)
FUTURE_LEAK_FIELDS: dict[str, str] = {
    "mfe_pct": "Trade-path MFE through exit (structural_trades / observer exit)",
    "peak_mfe_pct": "Peak favorable excursion during hold; exit event field",
    "max_favorable_excursion_pct": "Full hold path unless accept-time bridge snapshot",
    "r30_sec": "Forward return 30s after entry (extended_entry_shadow exit enrich)",
    "r60_sec": "Forward return 60s after entry",
    "r120_sec": "Forward return 120s after entry",
    "realized_pnl_pct": "Known only after exit",
    "pnl_pct": "Known only after exit (for filtering at entry)",
    "exit_reason": "After exit",
    "hold_sec": "After exit",
}

# Exit-row rolling_* pollution (observer overwrites at exit)
EXIT_ROW_FIELD_LEAK = {
    "rolling_mfe_pct": "Observer exit sets rolling_mfe_pct=peak_pnl_pct (post-entry path MFE)",
    "rolling_mae_pct": "Observer exit sets rolling_mae_pct=mae_pnl_pct (post-entry path MAE)",
}


def _load_module(name: str, rel_path: str) -> Any:
    path = REPO / rel_path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    sys.path[:0] = [str(REPO), str(REPO / "kabu_native" / "src")]
    spec.loader.exec_module(mod)
    return mod


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _pf(pnls: list[float]) -> float | None:
    wins = sum(p for p in pnls if p > 0)
    loss = sum(p for p in pnls if p < 0)
    gl = abs(loss)
    if gl <= 0:
        return None if wins <= 0 else float("inf")
    return round(wins / gl, 4)


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "n": 0,
            "profit_factor": None,
            "total_pnl_pct": 0.0,
            "IS_n": 0,
            "IS_pf": None,
            "IS_total_pnl_pct": 0.0,
            "OOS_n": 0,
            "OOS_pf": None,
            "OOS_total_pnl_pct": 0.0,
        }
    pnls = [float(r.get("pnl_pct") or 0) for r in rows]
    is_pnls = [float(r.get("pnl_pct") or 0) for r in rows if r.get("split") == "in_sample"]
    oos_pnls = [float(r.get("pnl_pct") or 0) for r in rows if r.get("split") == "oos"]
    pf = _pf(pnls)
    return {
        "n": len(rows),
        "profit_factor": pf if pf != float("inf") else pf,
        "total_pnl_pct": round(sum(pnls), 4),
        "IS_n": len(is_pnls),
        "IS_pf": _pf(is_pnls) if is_pnls else None,
        "IS_total_pnl_pct": round(sum(is_pnls), 4) if is_pnls else 0.0,
        "OOS_n": len(oos_pnls),
        "OOS_pf": _pf(oos_pnls) if oos_pnls else None,
        "OOS_total_pnl_pct": round(sum(oos_pnls), 4) if oos_pnls else 0.0,
    }


def _parse_cluster_tokens(cluster: str) -> list[str]:
    labels: list[str] = []
    for part in cluster.split(" & "):
        part = part.strip()
        if ":" not in part:
            continue
        labels.append(part.split(":", 1)[0])
    return labels


def _cluster_has_future_leak(cluster: str) -> tuple[bool, list[str]]:
    bad: list[str] = []
    for lbl in _parse_cluster_tokens(cluster):
        info = FEATURE_REGISTRY.get(lbl)
        if info and info["class"] == "after_entry_only":
            bad.append(lbl)
    return bool(bad), bad


def _cluster_entry_compliant(cluster: str) -> tuple[bool, list[str]]:
    bad: list[str] = []
    for lbl in _parse_cluster_tokens(cluster):
        info = FEATURE_REGISTRY.get(lbl)
        if info is None:
            bad.append(lbl)
        elif info["class"] in ("after_entry_only", "research_offline_pre_entry", "unknown"):
            bad.append(lbl)
    return not bad, bad


def _find_push_jsonl(push_dir: Path, symbol: str) -> Optional[Path]:
    sym = symbol.replace(".T", "")
    for name in (f"{symbol}.jsonl", f"{sym}.jsonl"):
        p = push_dir / name
        if p.is_file():
            return p
    return None


def _load_ticks(path: Path, mod: Any) -> list[tuple[float, float]]:
    ticks: list[tuple[float, float]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = mod._parse_ts(str(rec.get("recorded_at") or ""))
            px = _float((rec.get("payload") or {}).get("CurrentPrice"))
            if px and px > 0:
                ticks.append((ts, float(px)))
    return ticks


def _high_break_count(window: list[tuple[float, float]]) -> int:
    if len(window) < 2:
        return 0
    running = window[0][1]
    count = 0
    for _, px in window[1:]:
        if px > running * 1.0001:
            count += 1
            running = px
    return count


def _high_break_recent(ring: list[tuple[float, float]], entry_ts: float, entry_px: float) -> bool:
    cur = [(t, px) for t, px in ring if entry_ts - 300 <= t <= entry_ts]
    prev = [(t, px) for t, px in ring if entry_ts - 600 <= t < entry_ts - 300]
    if len(cur) < 2 or len(prev) < 2 or entry_px <= 0:
        return False
    m5 = max(px for _, px in cur)
    m5_prev = max(px for _, px in prev)
    if m5 <= m5_prev * 1.0001:
        return False
    if entry_px < m5 * 0.998:
        return False
    last_high_ts = max(t for t, px in cur if px >= m5 * 0.998)
    return (entry_ts - last_high_ts) <= 60.0


def _augment_high_break(mod: Any, rows: list[dict[str, Any]]) -> None:
    tick_cache: dict[str, list[tuple[float, float]]] = {}
    for r in rows:
        sym = str(r.get("symbol") or "")
        entry_ts = mod._parse_ts(str(r.get("entry_time") or ""))
        entry_day = str(r.get("day_stamp") or "")
        session_rel = str(r.get("session_id") or "")
        entry_px = _float(r.get("current_price"))
        push_dir = mod._push_dir_for_day(entry_day) or mod._push_dir(session_rel)
        if not push_dir or not entry_px:
            continue
        jpath = _find_push_jsonl(push_dir, sym)
        if not jpath:
            continue
        key = str(jpath)
        if key not in tick_cache:
            tick_cache[key] = _load_ticks(jpath, mod)
        ticks = tick_cache[key]
        before = [(t, px) for t, px in ticks if t <= entry_ts]
        window = [(t, px) for t, px in before if entry_ts - LOOKBACK_SEC <= t <= entry_ts]
        if len(window) >= 2:
            r["high_break_count_full_jsonl"] = _high_break_count(window)
            r["high_break_recent_recomputed"] = _high_break_recent(before, entry_ts, float(entry_px))
        logged = r.get("entry_high_break_recent")
        if logged is not None:
            r["hb_recent_live_logged"] = str(logged).lower() in ("true", "1", "yes")
        elif r.get("high_break_recent_recomputed") is not None:
            r["hb_recent_live_logged"] = r["high_break_recent_recomputed"]


def _quantile(xs: list[float], q: float) -> float:
    ys = sorted(xs)
    pos = (len(ys) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ys[lo]
    return ys[lo] + (ys[hi] - ys[lo]) * (pos - lo)


def _bin_label(val: float, q33: float, q66: float) -> str:
    if val <= q33:
        return "low"
    if val <= q66:
        return "mid"
    return "high"


def _assign_bins(rows: list[dict[str, Any]], cuts: dict[str, dict[str, Any]], key: str) -> None:
    for r in rows:
        bins: dict[str, str] = {}
        for label, info in cuts.items():
            if not info.get("usable"):
                continue
            v = _float(r.get(info["field"]))
            if v is None:
                continue
            bins[label] = _bin_label(v, info["p33"], info["p66"])
        hb = r.get("high_break_recent_recomputed")
        if hb is not None:
            bins["HBRecent"] = "yes" if hb else "no"
        elif r.get("hb_recent_live_logged") is not None:
            bins["HBRecent"] = "yes" if r.get("hb_recent_live_logged") else "no"
        r[key] = bins


def _build_tertiles(rows: list[dict[str, Any]], features: list[tuple[str, str]]) -> dict[str, dict[str, Any]]:
    cuts: dict[str, dict[str, Any]] = {}
    for label, field in features:
        vals = [_float(r.get(field)) for r in rows]
        vals = [v for v in vals if v is not None]
        if len(vals) < 9:
            cuts[label] = {"field": field, "coverage_n": len(vals), "usable": False}
            continue
        cuts[label] = {
            "field": field,
            "coverage_n": len(vals),
            "usable": True,
            "p33": round(_quantile(vals, 1.0 / 3.0), 6),
            "p66": round(_quantile(vals, 2.0 / 3.0), 6),
        }
    return cuts


def _trade_score(r: dict[str, Any], score_map: dict[str, int]) -> int:
    bins = r.get("_score_bins") or {}
    total = 0
    for token, pts in score_map.items():
        lbl = token.split(":", 1)[0]
        level = token.split(":", 1)[1]
        if bins.get(lbl) == level:
            total += pts
    return total


def _row_matches_cluster(r: dict[str, Any], cluster: str, bin_key: str) -> bool:
    bins = r.get(bin_key) or {}
    for part in cluster.split(" & "):
        part = part.strip()
        if ":" not in part:
            return False
        lbl, level = part.split(":", 1)
        if bins.get(lbl) != level:
            return False
    return True


def _score_map_labels(score_map: dict[str, int]) -> list[str]:
    return sorted({tok.split(":", 1)[0] for tok in score_map})


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    p229 = json.loads(P229.read_text(encoding="utf-8"))
    p231 = json.loads(P231.read_text(encoding="utf-8"))
    score_map = p229["work3_score_components"]["score_map"]
    score_cutoffs = json.loads(
        (REPO / "kabu_native/results/reports/phase228_entry_expectancy_discovery.json").read_text(
            encoding="utf-8"
        )
    )["tertile_cutoffs"]

    entry_safe_features: list[dict[str, Any]] = []
    future_leak_features: list[dict[str, Any]] = []
    research_offline_features: list[dict[str, Any]] = []

    for lbl, info in FEATURE_REGISTRY.items():
        row = {"label": lbl, **info}
        if info["class"] == "entry_available":
            entry_safe_features.append(row)
        elif info["class"] == "after_entry_only":
            future_leak_features.append(row)
        elif info["class"] == "research_offline_pre_entry":
            research_offline_features.append(row)

    for fld, note in FUTURE_LEAK_FIELDS.items():
        future_leak_features.append(
            {
                "label": fld,
                "field": fld,
                "class": "after_entry_only",
                "live_source": "exit / post-entry only",
                "notes": note,
            }
        )

    focus_audit = {
        lbl: FEATURE_REGISTRY[lbl]
        for lbl in ("RollingMFE", "RollingMAE", "HBCount", "HBRecent")
    }

    score_labels = _score_map_labels(score_map)
    score_map_audit = {
        "tokens": list(score_map.keys()),
        "labels_used": score_labels,
        "all_entry_compliant": all(
            FEATURE_REGISTRY.get(lbl, {}).get("class") == "entry_available" for lbl in score_labels
        ),
        "contains_future_leak": False,
        "non_live_labels": [],
    }
    for lbl in score_labels:
        cls = FEATURE_REGISTRY.get(lbl, {}).get("class")
        if cls == "after_entry_only":
            score_map_audit["contains_future_leak"] = True
        if cls == "research_offline_pre_entry":
            score_map_audit["non_live_labels"].append(lbl)

    phase231_clusters: list[dict[str, Any]] = []
    for cohort_key in ("score_ge5", "score_ge6"):
        cohort = p231["cohorts"][cohort_key]
        for c in cohort.get("all_adopted_clusters") or []:
            cluster = c["cluster"]
            has_leak, leak_labels = _cluster_has_future_leak(cluster)
            compliant, non_compliant = _cluster_entry_compliant(cluster)
            phase231_clusters.append(
                {
                    "cohort": cohort_key,
                    "cluster": cluster,
                    "original": c,
                    "labels": _parse_cluster_tokens(cluster),
                    "has_future_leak": has_leak,
                    "future_leak_labels": leak_labels,
                    "entry_compliant": compliant,
                    "non_compliant_labels": non_compliant,
                    "invalidated_by_future_leak": has_leak,
                }
            )

    valid_after_leak = [c for c in phase231_clusters if not c["invalidated_by_future_leak"]]
    entry_compliant_clusters = [c for c in phase231_clusters if c["entry_compliant"]]

    p217 = _load_module("phase217_loader_p232", "kabu_native/scripts/run_phase217_stop_hit_root_cause_review.py")
    p221 = _load_module("phase221_loader_p232", "kabu_native/scripts/run_phase221_early_momentum_discovery_review.py")
    mod = p217._load_phase213c_module()

    print("loading trades...", flush=True)
    rows = p217._build_all(mod)
    p221._augment_early_features(mod, rows)

    # Accept-event shadow fields not copied by phase217 enrich
    from collections import defaultdict as _dd

    by_sess: dict[str, list[dict[str, Any]]] = _dd(list)
    for r in rows:
        by_sess[str(r.get("session_id") or "")].append(r)
    for session_rel, sess_rows in by_sess.items():
        sdir = mod.BASE / session_rel
        if not sdir.is_dir():
            continue
        acc_map: dict[tuple[str, str], dict[str, Any]] = {}
        for ev in mod._load_events(sdir):
            if ev.get("event_type") == "accepted":
                acc_map[(str(ev.get("symbol") or ""), str(ev.get("entry_time") or ""))] = ev
        for r in sess_rows:
            acc = acc_map.get((str(r.get("symbol") or ""), str(r.get("entry_time") or "")), {})
            for fld in ("entry_high_break_recent", "entry_rise_5min_pct", "entry_rise_10min_pct"):
                if r.get(fld) in (None, "") and acc.get(fld) not in (None, ""):
                    r[fld] = acc.get(fld)

    _augment_high_break(mod, rows)

    for r in rows:
        for lbl, info in FEATURE_REGISTRY.items():
            if info["class"] == "entry_available" and info.get("field"):
                if lbl == "HBRecent" and r.get("entry_high_break_recent") is not None:
                    continue
                if lbl == "HBRecent":
                    if r.get("high_break_recent_recomputed") is not None:
                        r["entry_high_break_recent"] = r["high_break_recent_recomputed"]

    _assign_bins(rows, score_cutoffs, "_score_bins")
    for r in rows:
        r["_entry_score"] = _trade_score(r, score_map)

    score5 = [r for r in rows if int(r.get("_entry_score") or 0) >= SCORE_GE5]
    score6 = [r for r in rows if int(r.get("_entry_score") or 0) >= SCORE_GE6]

    baseline = {
        "score_ge5": _metrics(score5),
        "score_ge6": _metrics(score6),
    }

    cohort_features = [
        (lbl, info["field"])
        for lbl, info in FEATURE_REGISTRY.items()
        if info["class"] in ("entry_available", "research_offline_pre_entry")
    ]
    cohort_cuts_ge5 = _build_tertiles(score5, cohort_features)
    cohort_cuts_ge6 = _build_tertiles(score6, cohort_features)

    impact: dict[str, Any] = {"baseline": baseline, "after_future_leak_invalidation": {}}

    for cohort_key, cohort_rows, cuts in (
        ("score_ge5", score5, cohort_cuts_ge5),
        ("score_ge6", score6, cohort_cuts_ge6),
    ):
        bin_key = f"_{cohort_key}_bins"
        for r in cohort_rows:
            r[bin_key] = {}
        _assign_bins(cohort_rows, cuts, bin_key)

        bl = baseline[cohort_key]
        bl_pf = float(bl["profit_factor"] or 0)
        bl_pnl = float(bl["total_pnl_pct"] or 0)

        surviving = [c for c in valid_after_leak if c["cohort"] == cohort_key]
        compliant = [c for c in entry_compliant_clusters if c["cohort"] == cohort_key]

        def _eval_clusters(cluster_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            for c in cluster_list:
                members = [r for r in cohort_rows if _row_matches_cluster(r, c["cluster"], bin_key)]
                m = _metrics(members)
                if m["n"] < 200:
                    continue
                pf = m.get("profit_factor")
                if pf is None or pf <= bl_pf or m["total_pnl_pct"] <= bl_pnl:
                    continue
                if not (m.get("IS_pf") and m["IS_pf"] > 1 and m.get("OOS_pf") and m["OOS_pf"] > 1):
                    continue
                out.append({**c, "recalc_metrics": m})
            out.sort(
                key=lambda x: (
                    -(x["recalc_metrics"].get("profit_factor") or 0),
                    -(x["recalc_metrics"].get("total_pnl_pct") or 0),
                )
            )
            return out

        recalc_surviving = _eval_clusters(surviving)
        recalc_compliant = _eval_clusters(compliant)

        impact["after_future_leak_invalidation"][cohort_key] = {
            "original_adopted_count": len([c for c in phase231_clusters if c["cohort"] == cohort_key]),
            "invalidated_by_future_leak": len([c for c in phase231_clusters if c["cohort"] == cohort_key and c["invalidated_by_future_leak"]]),
            "surviving_count": len(surviving),
            "still_meets_phase231_criteria": len(recalc_surviving),
            "best_surviving": recalc_surviving[0] if recalc_surviving else None,
        }
        impact["after_future_leak_invalidation"][cohort_key]["entry_compliant_only"] = {
            "compliant_count": len(compliant),
            "still_meets_phase231_criteria": len(recalc_compliant),
            "best_compliant": recalc_compliant[0] if recalc_compliant else None,
        }

    hb_mismatch = 0
    hb_both = 0
    for r in rows:
        if r.get("high_break_recent_recomputed") is None or r.get("hb_recent_live_logged") is None:
            continue
        hb_both += 1
        if bool(r["high_break_recent_recomputed"]) != bool(r["hb_recent_live_logged"]):
            hb_mismatch += 1

    report = {
        "phase": 232,
        "mode": "entry_feature_leak_audit",
        "constraints": {
            "review_only": True,
            "production_changes_forbidden": True,
            "yaml_changes_forbidden": True,
        },
        "method": {
            "classification": [
                "entry_available — logged/computed at accept from t<=entry data",
                "after_entry_only — post-entry path / forward returns (future leak)",
                "research_offline_pre_entry — offline pre-entry recompute, not live-logged",
            ],
            "invalidation_rule": "Phase231 cluster invalidated if any label is after_entry_only",
            "research_note": "research_offline_pre_entry is NOT future leak; reported separately",
            "phase217_safety": "rolling_mfe/mae loaded from accept event row only (not exit overwrite)",
        },
        "exit_row_field_pollution": EXIT_ROW_FIELD_LEAK,
        "focus_audit": focus_audit,
        "entry_safe_features": entry_safe_features,
        "future_leak_features": future_leak_features,
        "research_offline_pre_entry_features": research_offline_features,
        "score_map_audit": score_map_audit,
        "phase231_cluster_audit": phase231_clusters,
        "invalidated_clusters": [c for c in phase231_clusters if c["invalidated_by_future_leak"]],
        "valid_clusters_after_leak_filter": valid_after_leak,
        "entry_compliant_clusters": entry_compliant_clusters,
        "hb_recent_live_vs_research": {
            "trades_with_both": hb_both,
            "mismatch_count": hb_mismatch,
            "mismatch_rate": round(hb_mismatch / max(1, hb_both), 4),
        },
        "impact_recalculation": impact,
        "summary": {
            "score_map_entry_safe": score_map_audit["all_entry_compliant"],
            "score_map_future_leak": score_map_audit["contains_future_leak"],
            "phase231_total_adopted": len(phase231_clusters),
            "phase231_invalidated_future_leak": len([c for c in phase231_clusters if c["invalidated_by_future_leak"]]),
            "phase231_surviving_after_leak": len(valid_after_leak),
            "phase231_entry_compliant": len(entry_compliant_clusters),
            "score_ge5_baseline_unchanged": baseline["score_ge5"],
            "score_ge6_baseline_unchanged": baseline["score_ge6"],
            "best_entry_compliant_ge5": impact["after_future_leak_invalidation"]["score_ge5"]["entry_compliant_only"].get(
                "best_compliant"
            ),
        },
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"wrote {OUT} invalidated={report['summary']['phase231_invalidated_future_leak']} "
        f"compliant={report['summary']['phase231_entry_compliant']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
