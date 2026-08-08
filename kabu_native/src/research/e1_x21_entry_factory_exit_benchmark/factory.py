"""Feature registry, Discovery thresholds, ENTRY candidate factory."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Optional

import numpy as np

from research.e1_x19_outcome_pre_path.analyze import classify_population
from research.e1_x19_outcome_pre_path.population import attach_derived

from . import (
    CREATE_MIN_DAYS,
    CREATE_MIN_SUPPORT,
    DISCOVERY,
    EXPECTED_POP_N,
    FAMILY_BY_FEATURE,
    FEATURE_REGISTRY,
    RULE_TYPES,
    SOURCE_X19,
)

NATIVE = Path(__file__).resolve().parents[3]
X19_POP = NATIVE / "results" / "research" / "e1_x19_outcome_pre_path" / "_population.jsonl"
X19_REPORT = NATIVE / "results" / "research" / "e1_x19_outcome_pre_path" / "report.json"
OUT = NATIVE / "results" / "research" / "e1_x21_entry_factory_exit_benchmark"


def load_population() -> list[dict[str, Any]]:
    x19 = json.loads(X19_REPORT.read_text(encoding="utf-8"))
    assert x19["run_id"] == SOURCE_X19
    assert x19["population_n"] == EXPECTED_POP_N
    raw = [json.loads(l) for l in X19_POP.read_text(encoding="utf-8").splitlines() if l.strip()]
    rows = attach_derived(raw)
    rows = classify_population(rows)
    assert len(rows) == EXPECTED_POP_N
    return rows


def feature_availability(rows: list[dict[str, Any]]) -> dict[str, Any]:
    registered = []
    unavailable = []
    for fam, feats in FEATURE_REGISTRY.items():
        for f in feats:
            n = sum(1 for r in rows if r.get(f) is not None)
            days = len({r["date"] for r in rows if r.get(f) is not None})
            rec = {
                "feature_name": f,
                "family": fam,
                "support": n,
                "entry_days": days,
                "available": n >= CREATE_MIN_SUPPORT and days >= CREATE_MIN_DAYS,
            }
            if not rec["available"]:
                rec["status"] = "FEATURE_UNAVAILABLE"
                rec["reason"] = (
                    "not_in_X19_population_cache"
                    if n == 0 else f"support={n}<{CREATE_MIN_SUPPORT} or days={days}<{CREATE_MIN_DAYS}"
                )
                unavailable.append(rec)
            else:
                rec["status"] = "AVAILABLE"
                registered.append(rec)
    return {
        "registered": registered,
        "unavailable": unavailable,
        "registered_count": len(registered) + len(unavailable),
        "available_count": len(registered),
        "unavailable_count": len(unavailable),
    }


def discovery_thresholds(rows: list[dict[str, Any]], available_features: list[str]) -> dict[str, Any]:
    disc = [r for r in rows if r["date"] in DISCOVERY]
    out = {}
    for f in available_features:
        xs = [float(r[f]) for r in disc if r.get(f) is not None]
        body = {
            "feature_name": f,
            "discovery_support": len(xs),
            "q20": float(np.quantile(xs, 0.20)),
            "q40": float(np.quantile(xs, 0.40)),
            "q60": float(np.quantile(xs, 0.60)),
            "q80": float(np.quantile(xs, 0.80)),
            "threshold_source_dates": list(DISCOVERY),
        }
        body["threshold_sha256"] = hashlib.sha256(
            json.dumps(body, sort_keys=True, default=str).encode()
        ).hexdigest()
        out[f] = body
    return {"by_feature": out, "discovery_only": True, "no_retune": True}


def _dup_group(feature: str) -> str:
    # return_60s and slope_60s share trend mechanism (near-linear)
    if feature in ("return_60s", "slope_60s"):
        return "DUP_TREND_60S"
    if feature in ("return_180s", "slope_180s"):
        return "DUP_TREND_180S"
    return f"DUP_{feature}"


def build_single_candidates(avail: list[str], thr: dict[str, Any]) -> list[dict[str, Any]]:
    cands = []
    for f in avail:
        t = thr["by_feature"][f]
        q20, q80 = t["q20"], t["q80"]
        for rule in RULE_TYPES:
            if rule == "UPPER_REJECT":
                threshold, op = q80, "<="
            elif rule == "LOWER_REJECT":
                threshold, op = q20, ">="
            elif rule == "UPPER_SELECT":
                threshold, op = q80, ">="
            else:
                threshold, op = q20, "<="
            cid = f"ENTRY_{f.upper()}_{rule}"
            cands.append({
                "candidate_id": cid,
                "feature_name": f,
                "rule_type": rule,
                "threshold": threshold,
                "op": op,
                "threshold_source": "DISCOVERY_q20_q80",
                "threshold_sha256": t["threshold_sha256"],
                "missing_behavior": "FEATURE_MISSING",
                "anchor_contract": "X19_CANONICAL_CLUSTER_FIRST_ANCHOR",
                "source_run": SOURCE_X19,
                "duplicate_group_id": _dup_group(f),
                "family": FAMILY_BY_FEATURE.get(f, "OTHER"),
                "n_features": 1,
                "status": "EXPERIMENTAL_CREATED",
                "implementation_id": f"{f}|{rule}|{threshold}",
            })
    return cands


def evaluate_entry_candidate(spec: dict[str, Any], anchor: dict[str, Any]) -> dict[str, Any]:
    f = spec["feature_name"]
    v = anchor.get(f)
    base = {
        "candidate_id": spec["candidate_id"],
        "symbol": anchor.get("symbol"),
        "session": anchor.get("session"),
        "cluster_id": anchor.get("cluster_id"),
        "anchor_time": anchor.get("grid_time") or anchor.get("anchor_time"),
        "anchor_price": anchor.get("CurrentPrice"),
        "feature_value": v,
        "threshold": spec["threshold"],
        "grid_epoch": anchor.get("grid_epoch"),
        "date": anchor.get("date"),
    }
    if v is None:
        return {**base, "decision": "FEATURE_MISSING", "reason": "feature_null"}
    thr = float(spec["threshold"])
    fv = float(v)
    op = spec["op"]
    ok = (fv <= thr) if op == "<=" else (fv >= thr)
    if ok:
        return {**base, "decision": "ENTRY_ALLOWED", "reason": f"{f} {op} {thr}"}
    return {**base, "decision": "ENTRY_REJECTED", "reason": f"{f} not {op} {thr}"}


def decision_mask(rows: list[dict[str, Any]], spec: dict[str, Any]) -> np.ndarray:
    """Boolean mask: ENTRY_ALLOWED (True) among population order."""
    f = spec["feature_name"]
    thr = float(spec["threshold"])
    op = spec["op"]
    vals = np.array([
        float(r[f]) if r.get(f) is not None else np.nan for r in rows
    ], dtype=float)
    if op == "<=":
        return np.isfinite(vals) & (vals <= thr)
    return np.isfinite(vals) & (vals >= thr)


def build_two_feature_candidates(
    singles: list[dict[str, Any]],
    masks: dict[str, np.ndarray],
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """AND pairs across different features; technical support only."""
    # Index dates for day count
    dates = np.array([r["date"] for r in rows])
    out = []
    n = len(singles)
    for i in range(n):
        a = singles[i]
        ma = masks[a["candidate_id"]]
        for j in range(i + 1, n):
            b = singles[j]
            if a["feature_name"] == b["feature_name"]:
                continue
            # meaningless same-feature-style extremes already skipped by feature inequality
            # skip exact opposite on same feature already handled
            mb = masks[b["candidate_id"]]
            comb = ma & mb
            support = int(comb.sum())
            if support < CREATE_MIN_SUPPORT:
                continue
            day_n = len(set(dates[comb].tolist()))
            if day_n < CREATE_MIN_DAYS:
                continue
            # Allowed exclusion: complete identical decision mask to a parent
            if bool(np.array_equal(comb, ma) or np.array_equal(comb, mb)):
                continue
            cid = f"AND__{a['candidate_id']}__{b['candidate_id']}"
            out.append({
                "candidate_id": cid,
                "feature_name": f"{a['feature_name']}+{b['feature_name']}",
                "rule_type": f"AND({a['rule_type']},{b['rule_type']})",
                "threshold": {"a": a["threshold"], "b": b["threshold"]},
                "op": "AND",
                "parents": [a["candidate_id"], b["candidate_id"]],
                "threshold_source": "DISCOVERY_q20_q80",
                "missing_behavior": "FEATURE_MISSING",
                "anchor_contract": "X19_CANONICAL_CLUSTER_FIRST_ANCHOR",
                "source_run": SOURCE_X19,
                "duplicate_group_id": f"AND_{_dup_group(a['feature_name'])}_{_dup_group(b['feature_name'])}",
                "family": "COMPOSITE",
                "n_features": 2,
                "status": "EXPERIMENTAL_CREATED",
                "implementation_id": f"{a['implementation_id']}&&{b['implementation_id']}",
                "identical_to_parent": False,
                "create_support": support,
                "create_days": day_n,
            })
    return out
