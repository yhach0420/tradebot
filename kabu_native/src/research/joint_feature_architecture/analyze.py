"""Per-architecture frozen A-I gate, incremental attribution, CASE A-D."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np
from scipy.stats import spearmanr

from research.canonical_entry_performance_rebase.analyze import _f
from research.joint_feature_architecture import (
    A0_EXPECTED,
    A0_PARITY_ABS_TOL,
    BLOCK_EFFECTS,
    JOINT_RATE_MIN,
    PREVIOUS_PROBE_MEDIAN,
)


def _close(a: Any, b: Any, tol: float) -> bool:
    x, y = _f(a), _f(b)
    if x is None or y is None:
        return False
    return abs(float(x) - float(y)) <= float(tol)


def gates(body: dict[str, Any]) -> dict[str, Any]:
    mfe = _f(body.get("TOP3_MFE_DELTA"))
    dn = _f(body.get("TOP3_DOWNSIDE_DELTA"))
    rate = _f(body.get("JOINT_COHORT_SUCCESS_RATE"))
    mpos = int(body.get("MFE_POSITIVE_DAYS") or 0)
    mneg = int(body.get("MFE_NEGATIVE_DAYS") or 0)
    dpos = int(body.get("DOWNSIDE_POSITIVE_DAYS") or 0)
    dneg = int(body.get("DOWNSIDE_NEGATIVE_DAYS") or 0)
    g = {
        "A_MFE_DELTA_GT_0": bool(mfe is not None and mfe > 0),
        "B_DOWNSIDE_DELTA_GT_0": bool(dn is not None and dn > 0),
        "C_JOINT_COHORT_SUCCESS_RATE": bool(rate is not None and rate >= JOINT_RATE_MIN),
        "D_MFE_POS_GT_NEG": bool(mpos > mneg),
        "E_DOWNSIDE_POS_GT_NEG": bool(dpos > dneg),
        "F_MFE_EX_BEST_GT_0": bool(body.get("MFE_EX_BEST_DAY") is not None and float(body["MFE_EX_BEST_DAY"]) > 0),
        "G_MFE_EX_TOP3_GE_0": bool(body.get("MFE_EX_TOP3_DAYS") is not None and float(body["MFE_EX_TOP3_DAYS"]) >= 0),
        "H_DOWNSIDE_EX_BEST_GT_0": bool(body.get("DOWNSIDE_EX_BEST_DAY") is not None and float(body["DOWNSIDE_EX_BEST_DAY"]) > 0),
        "I_DOWNSIDE_EX_TOP3_GE_0": bool(body.get("DOWNSIDE_EX_TOP3_DAYS") is not None and float(body["DOWNSIDE_EX_TOP3_DAYS"]) >= 0),
    }
    fail = [k for k, v in g.items() if not v]
    return {
        "gates": g,
        "gate_fail": fail,
        "PASS": len(fail) == 0,
        "MFE_DELTA": mfe,
        "DOWNSIDE_DELTA": dn,
        "JOINT_RATE": rate,
    }


def base_parity(a0: dict[str, Any]) -> dict[str, Any]:
    checks = {
        "MFE": _close(a0.get("TOP3_MFE_DELTA"), A0_EXPECTED["TOP3_MFE_DELTA"], A0_PARITY_ABS_TOL),
        "DOWNSIDE": _close(a0.get("TOP3_DOWNSIDE_DELTA"), A0_EXPECTED["TOP3_DOWNSIDE_DELTA"], A0_PARITY_ABS_TOL),
        "JOINT": _close(a0.get("JOINT_COHORT_SUCCESS_RATE"), A0_EXPECTED["JOINT_COHORT_SUCCESS_RATE"], A0_PARITY_ABS_TOL),
    }
    return {
        "BASE_PARITY": all(checks.values()),
        "checks": checks,
        "expected": dict(A0_EXPECTED),
        "observed": {
            "TOP3_MFE_DELTA": a0.get("TOP3_MFE_DELTA"),
            "TOP3_DOWNSIDE_DELTA": a0.get("TOP3_DOWNSIDE_DELTA"),
            "JOINT_COHORT_SUCCESS_RATE": a0.get("JOINT_COHORT_SUCCESS_RATE"),
        },
        "control_previous_median": dict(PREVIOUS_PROBE_MEDIAN),
    }


def attribution(by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    a0 = by_id.get("A0") or {}
    out = {}
    for aid, name, block in BLOCK_EFFECTS:
        b = by_id.get(aid) or {}
        out[name] = {
            "architecture_id": aid,
            "block": block,
            "delta_MFE": _sub(b.get("TOP3_MFE_DELTA"), a0.get("TOP3_MFE_DELTA")),
            "delta_DOWNSIDE": _sub(b.get("TOP3_DOWNSIDE_DELTA"), a0.get("TOP3_DOWNSIDE_DELTA")),
            "delta_JOINT_RATE": _sub(b.get("JOINT_COHORT_SUCCESS_RATE"), a0.get("JOINT_COHORT_SUCCESS_RATE")),
        }
    return out


def _sub(a: Any, b: Any) -> Optional[float]:
    x, y = _f(a), _f(b)
    if x is None or y is None:
        return None
    return float(x) - float(y)


def arch_summary(body: dict[str, Any]) -> dict[str, Any]:
    g = gates(body)
    return {
        "architecture_id": body.get("architecture_id"),
        "name": body.get("name"),
        "n_features": body.get("n_features") or len(body.get("features") or []),
        "n_cohorts": body.get("n_cohorts"),
        "MFE_DELTA": g["MFE_DELTA"],
        "DOWNSIDE_DELTA": g["DOWNSIDE_DELTA"],
        "JOINT_RATE": g["JOINT_RATE"],
        "MFE_POSITIVE_DAYS": body.get("MFE_POSITIVE_DAYS"),
        "MFE_NEGATIVE_DAYS": body.get("MFE_NEGATIVE_DAYS"),
        "DOWNSIDE_POSITIVE_DAYS": body.get("DOWNSIDE_POSITIVE_DAYS"),
        "DOWNSIDE_NEGATIVE_DAYS": body.get("DOWNSIDE_NEGATIVE_DAYS"),
        "MFE_EX_BEST_DAY": body.get("MFE_EX_BEST_DAY"),
        "MFE_EX_TOP3_DAYS": body.get("MFE_EX_TOP3_DAYS"),
        "DOWNSIDE_EX_BEST_DAY": body.get("DOWNSIDE_EX_BEST_DAY"),
        "DOWNSIDE_EX_TOP3_DAYS": body.get("DOWNSIDE_EX_TOP3_DAYS"),
        "ROC_AUC": body.get("ROC_AUC"),
        "AVERAGE_PRECISION": body.get("AVERAGE_PRECISION"),
        "PASS": g["PASS"],
        "gate_fail": g["gate_fail"],
        "gates": g["gates"],
    }


def redundancy(rows: list[dict[str, Any]], keys: list[str]) -> dict[str, Any]:
    pairs = []
    max_abs = None
    max_pair = None
    named = []
    for i, a in enumerate(keys):
        xa = [_f(r.get(a)) for r in rows]
        for b in keys[i + 1 :]:
            xb = [_f(r.get(b)) for r in rows]
            xs = []
            ys = []
            for u, v in zip(xa, xb):
                if u is None or v is None:
                    continue
                xs.append(float(u))
                ys.append(float(v))
            if len(xs) < 50:
                continue
            rho, _p = spearmanr(xs, ys)
            if rho != rho:
                continue
            rec = {"a": a, "b": b, "spearman": float(rho), "n": len(xs)}
            pairs.append(rec)
            ar = abs(float(rho))
            if max_abs is None or ar > max_abs:
                max_abs = ar
                max_pair = (a, b, float(rho))
            if {a, b} == {"volume_rate_60s", "volume_percentile_60s"}:
                named.append(rec)
    pairs.sort(key=lambda r: -abs(float(r["spearman"])))
    return {
        "MAX_FEATURE_CORRELATION": max_abs,
        "MAX_FEATURE_CORRELATION_PAIR": max_pair,
        "VOLUME_RATE_VS_PERCENTILE": named[0] if named else None,
        "HIGH_ABS_GE_080": [p for p in pairs if abs(float(p["spearman"])) >= 0.80],
        "pairs_head": pairs[:40],
    }


def decide(
    summaries: list[dict[str, Any]],
    *,
    effects: dict[str, Any],
    parity_ok: bool,
    future_n: int,
    contamination_n: int,
) -> dict[str, Any]:
    if not parity_ok:
        return {
            "CASE": None,
            "VERDICT": "FEATURE_ARCHITECTURE_REDESIGN_INTEGRITY_FAILED",
            "NEXT_RESEARCH": "NONE",
            "PRIMARY_FINDING": "A0 F2_UNION|none did not reproduce the previous Direct Joint control.",
            "INTERACTION_DEPENDENT": False,
            "PRIMARY_INFORMATION_SOURCE": "NONE",
            "FEATURE_ARCHITECTURE_PASS": False,
            "note": "STOP. BASE_PARITY failed.",
        }
    if int(future_n) != 0 or int(contamination_n) != 0:
        return {
            "CASE": None,
            "VERDICT": "FEATURE_ARCHITECTURE_REDESIGN_INTEGRITY_FAILED",
            "NEXT_RESEARCH": "NONE",
            "PRIMARY_FINDING": f"Causality/integrity fail future={future_n} contamination={contamination_n}.",
            "INTERACTION_DEPENDENT": False,
            "PRIMARY_INFORMATION_SOURCE": "NONE",
            "FEATURE_ARCHITECTURE_PASS": False,
            "note": "STOP. Future event or target contamination.",
        }
    by = {str(s.get("architecture_id")): s for s in summaries}
    passing = [s for s in summaries if s.get("PASS")]
    single = [s for s in passing if str(s.get("architecture_id")) in {"A1", "A2", "A3", "A4"}]
    a5_pass = bool((by.get("A5") or {}).get("PASS"))
    interaction = bool(a5_pass and not single)
    source = "NONE"
    if single:
        best = sorted(single, key=lambda s: (-float(s.get("JOINT_RATE") or -1), str(s.get("architecture_id"))))[0]
        source = str(best.get("name") or best.get("architecture_id"))
    else:
        scored = []
        for aid, name, _block in BLOCK_EFFECTS:
            d = (effects.get(name) or {}).get("delta_JOINT_RATE")
            if d is not None and float(d) > 0:
                scored.append((float(d), aid, name))
        if scored:
            scored.sort(reverse=True)
            source = scored[0][2]
    diag = sorted(
        summaries,
        key=lambda s: (
            -float(s.get("JOINT_RATE") or -1e9),
            -((float(s.get("MFE_DELTA") or 0) + float(s.get("DOWNSIDE_DELTA") or 0))),
            str(s.get("architecture_id")),
        ),
    )[0]
    if interaction:
        return {
            "CASE": "B",
            "VERDICT": "JOINT_FEATURE_INTERACTION_REQUIRED",
            "NEXT_RESEARCH": "INTERACTION_FEATURE_ARCHITECTURE_DEVELOPMENT",
            "PRIMARY_FINDING": "Only the full P+L+M+X stack passes the frozen joint gate. Single blocks do not.",
            "INTERACTION_DEPENDENT": True,
            "PRIMARY_INFORMATION_SOURCE": source,
            "BEST_DIAGNOSTIC_ARCHITECTURE": diag.get("architecture_id"),
            "FEATURE_ARCHITECTURE_PASS": True,
            "note": "CASE B. STOP. Do not start interaction development this run.",
        }
    if passing:
        ids = [str(s.get("architecture_id")) for s in passing]
        return {
            "CASE": "A",
            "VERDICT": "JOINT_FEATURE_ARCHITECTURE_FOUND",
            "NEXT_RESEARCH": "FEATURE_ARCHITECTURE_PRECOMMIT_DEVELOPMENT",
            "PRIMARY_FINDING": f"Frozen joint gate passed for {ids}. No architecture adopted. Exact not run.",
            "INTERACTION_DEPENDENT": False,
            "PRIMARY_INFORMATION_SOURCE": source,
            "BEST_DIAGNOSTIC_ARCHITECTURE": diag.get("architecture_id"),
            "FEATURE_ARCHITECTURE_PASS": True,
            "note": "CASE A. STOP. Do not start precommit development this run.",
        }
    both = [s for s in summaries if (s.get("MFE_DELTA") or 0) > 0 and (s.get("DOWNSIDE_DELTA") or 0) > 0]
    one = [
        s
        for s in summaries
        if ((s.get("MFE_DELTA") or 0) > 0) != ((s.get("DOWNSIDE_DELTA") or 0) > 0)
        and (((s.get("MFE_DELTA") or 0) > 0) or ((s.get("DOWNSIDE_DELTA") or 0) > 0))
    ]
    if one and not both:
        return {
            "CASE": "C",
            "VERDICT": "FEATURES_STILL_SINGLE_OBJECTIVE",
            "NEXT_RESEARCH": "RAW_STATE_REPRESENTATION_REDESIGN",
            "PRIMARY_FINDING": "New feature blocks still move only one of MFE / downside-avoid vs CURRENT Top3.",
            "INTERACTION_DEPENDENT": False,
            "PRIMARY_INFORMATION_SOURCE": source,
            "BEST_DIAGNOSTIC_ARCHITECTURE": diag.get("architecture_id"),
            "FEATURE_ARCHITECTURE_PASS": False,
            "note": "CASE C. STOP. Sequence model not started.",
        }
    return {
        "CASE": "D",
        "VERDICT": "ENGINEERED_FEATURE_ARCHITECTURE_INSUFFICIENT",
        "NEXT_RESEARCH": "SEQUENCE_OR_EVENT_LEVEL_REPRESENTATION_REDESIGN",
        "PRIMARY_FINDING": "No frozen architecture identifies the joint region vs CURRENT Top3 under A-I.",
        "INTERACTION_DEPENDENT": False,
        "PRIMARY_INFORMATION_SOURCE": source,
        "BEST_DIAGNOSTIC_ARCHITECTURE": diag.get("architecture_id"),
        "FEATURE_ARCHITECTURE_PASS": False,
        "note": "CASE D. STOP. Sequence model not started this run.",
    }
