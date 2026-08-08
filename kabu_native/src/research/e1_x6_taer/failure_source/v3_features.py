"""V3 feature table rebuild + schema gate (setup-specific missingness)."""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any, Optional

from research.e1_x6_fcrr.features import FeatureBuffer
from research.e1_x6_taer.failure_source.opportunity import _build_entry_features
from research.e1_x6_taer.failure_source.precommit import FEATURE_SCHEMA, FORBIDDEN_FEATURES, FRESHNESS_MAX_SEC
from research.e1_x6_taer.failure_source.v3_precommit import DEPTH_UNAVAILABLE, SETUP_SPECIFIC_FEATURES


def rebuild_feature_table_for_reps(
    reps: list[dict[str, Any]],
    episodes_by_id: dict[str, dict],
    events_by_day: dict[str, list],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Rebuild decision-time features for cluster representatives only."""
    by_day: dict[str, list] = defaultdict(list)
    for r in reps:
        by_day[r["day"]].append(r)

    out: list[dict[str, Any]] = []
    meta = {"days": {}}

    for day in sorted(by_day):
        events = events_by_day[day]
        day_reps = sorted(by_day[day], key=lambda x: (float(x["entry_time"]), x["episode_id"]))
        pending = {r["episode_id"]: r for r in day_reps}
        bufs: dict[str, FeatureBuffer] = {}
        last_spread: dict[str, float] = {}
        captured: set[str] = set()

        from datetime import datetime
        from zoneinfo import ZoneInfo
        JST = ZoneInfo("Asia/Tokyo")

        def _sess(ts):
            return "AM" if ts.hour < 12 else "PM"

        for idx, (t, sym, row) in enumerate(events):
            if not pending:
                break
            ts = row["ts"]
            sess = _sess(ts)
            bid, ask, vwap, vol = float(row["bid"]), float(row["ask"]), float(row["vwap"]), float(row["vol"])
            buf = bufs.get(sym)
            if buf is None:
                buf = FeatureBuffer()
                bufs[sym] = buf
            buf.push(t, bid, ask, vwap, vol)
            age = buf.age(t)
            fresh = age <= FRESHNESS_MAX_SEC + 1e-9
            if not fresh:
                if ask + bid > 0:
                    last_spread[sym] = (ask - bid) / ((ask + bid) / 2.0) * 10000.0
                continue

            done = []
            for eid, rep in list(pending.items()):
                if rep["symbol"] != sym:
                    continue
                if float(t) + 1e-12 < float(rep["entry_time"]):
                    continue
                if sess != rep.get("session"):
                    continue
                ep = episodes_by_id.get(eid) or {}
                # merge setup/anchor from episode if present
                ep_merged = {
                    **ep,
                    "episode_id": eid,
                    "setup_type": rep["setup_type"],
                    "day": day,
                    "session": rep.get("session"),
                    "symbol": sym,
                    "anchor": ep.get("anchor") or {},
                    "setup_detail": ep.get("setup_detail") or {},
                }
                snap = buf.snapshot(t)
                feats = _build_entry_features(ep_merged, snap, buf, t, last_spread.get(sym))
                asof = snap.get("asof_time")
                if asof is None:
                    asof = t - (age if age is not None and math.isfinite(age) else 0.0)
                out.append({
                    **feats,
                    "cluster_id": rep.get("overlap_cluster_id"),
                    "episode_id": eid,
                    "setup_type": rep["setup_type"],
                    "anchor_type": (ep.get("anchor") or {}).get("anchor_kind"),
                    "day": day,
                    "session": rep.get("session"),
                    "symbol": sym,
                    "decision_time": float(rep["entry_time"]),
                    "feature_asof_time": float(asof),
                    "overlap_cluster_id": rep.get("overlap_cluster_id"),
                    "is_cluster_representative": True,
                })
                captured.add(eid)
                done.append(eid)
            for eid in done:
                pending.pop(eid, None)
            if ask + bid > 0:
                last_spread[sym] = (ask - bid) / ((ask + bid) / 2.0) * 10000.0

        meta["days"][day] = {
            "reps": len(day_reps),
            "captured": len([e for e in day_reps if e["episode_id"] in captured]),
            "missing": [e["episode_id"] for e in day_reps if e["episode_id"] not in captured],
        }

    return out, meta


def feature_schema_gate(
    feat_rows: list[dict[str, Any]],
    label_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    errors = []
    if len(feat_rows) != 399:
        errors.append(f"feature_n={len(feat_rows)} != 399")
    if len(label_rows) != 399:
        errors.append(f"label_n={len(label_rows)} != 399")

    cids = [r.get("cluster_id") or r.get("overlap_cluster_id") for r in feat_rows]
    if len(cids) != len(set(cids)):
        errors.append("duplicate_cluster_id")
    if any(not r.get("setup_type") for r in feat_rows):
        errors.append("setup_type_missing")

    label_cids = {r["cluster_id"] for r in label_rows}
    feat_cids = set(cids)
    if label_cids != feat_cids:
        errors.append("identity_mismatch_cluster")

    future = 0
    for r in feat_rows:
        dt = r.get("decision_time")
        at = r.get("feature_asof_time")
        if dt is not None and at is not None and float(at) > float(dt) + 1e-9:
            future += 1
    if future:
        errors.append(f"feature_asof_future={future}")

    # forbidden as model features present as columns used in FEATURE_SCHEMA? none should be
    for bad in FORBIDDEN_FEATURES:
        if bad in FEATURE_SCHEMA:
            errors.append(f"forbidden_in_schema:{bad}")

    coverage = []
    for fname in FEATURE_SCHEMA:
        if fname in ("setup_type_code", "anchor_type_code", "trade_side_quality_code", "missing_feature_count"):
            continue
        applicable_setup = SETUP_SPECIFIC_FEATURES.get(fname)
        if applicable_setup:
            applicable = [r for r in feat_rows if r.get("setup_type") == applicable_setup]
        else:
            applicable = feat_rows
        # depth unavailable: applicable_n = 0 for primary purposes
        if fname in DEPTH_UNAVAILABLE:
            applicable_n = 0
            non_miss = 0
            miss_rate = 1.0
        else:
            applicable_n = len(applicable)
            non_miss = sum(1 for r in applicable if r.get(fname) is not None)
            miss_rate = 1.0 - (non_miss / applicable_n) if applicable_n else 1.0
        days = {r["day"] for r in applicable if r.get(fname) is not None}
        syms = {r["symbol"] for r in applicable if r.get(fname) is not None}
        coverage.append({
            "feature": fname,
            "applicable_setup": applicable_setup or "ALL",
            "applicable_n": applicable_n if fname not in DEPTH_UNAVAILABLE else len(feat_rows),
            "non_missing_n": non_miss,
            "missing_rate": miss_rate,
            "day_support": len(days),
            "symbol_support": len(syms),
            "primary_candidate_eligible": (
                fname not in DEPTH_UNAVAILABLE and miss_rate <= 0.20 and applicable_n >= 20
            ),
        })

    status = "PASS" if not errors else "FAIL"
    return {
        "status": status,
        "errors": errors,
        "coverage": coverage,
        "verdict_if_fail": "TAER_FAILURE_ANALYSIS_INSUFFICIENT_FEATURE_SCHEMA",
        "n_feature_rows": len(feat_rows),
        "n_label_rows": len(label_rows),
    }
