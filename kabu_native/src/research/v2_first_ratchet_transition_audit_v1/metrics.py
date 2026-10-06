"""Descriptive separation. The 0.20 / 0.56 bars are precommitted, not searched."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.v2_first_ratchet_transition_audit_v1.features import FAMILIES, MIN_ABS_D, MIN_AUC_EDGE, MIN_GROUP_N, QUINTILE_KEYS, TIME_LANDMARKS_MS

T0_KEYS = ("vol_accel_10", "vol_accel_30", "buy_dominant", "tick_accel_10", "classified10", "break_distance_ticks", "spread")


def _auc(pos: np.ndarray, neg: np.ndarray) -> Optional[float]:
    if pos.size == 0 or neg.size == 0:
        return None
    if pos.size * neg.size > 8_000_000:
        rng = np.random.default_rng(0)
        pos = pos[rng.choice(pos.size, 2000, replace=False)] if pos.size > 2000 else pos
        neg = neg[rng.choice(neg.size, 2000, replace=False)] if neg.size > 2000 else neg
    diff = pos[:, None] - neg[None, :]
    return float((np.sum(diff > 0) + 0.5 * np.sum(diff == 0)) / diff.size)


def _compare(rows: list[dict[str, Any]], key: str, getter) -> dict[str, Any]:
    a = []
    b = []
    for row in rows:
        value = getter(row)
        if value is None or value != value:
            continue
        (b if row["label"] == "RATCHET_GE1" else a).append(float(value))
    xa = np.asarray(a, dtype=float)
    xb = np.asarray(b, dtype=float)
    if xa.size < 2 or xb.size < 2:
        return {"feature": key, "n0": int(xa.size), "n1": int(xb.size)}
    m0, m1 = float(np.mean(xa)), float(np.mean(xb))
    s0, s1 = float(np.std(xa, ddof=1)), float(np.std(xb, ddof=1))
    pooled = np.sqrt(((xa.size - 1) * s0 ** 2 + (xb.size - 1) * s1 ** 2) / (xa.size + xb.size - 2))
    d = None if pooled == 0 else (m1 - m0) / pooled
    return {
        "feature": key,
        "n0": int(xa.size),
        "n1": int(xb.size),
        "mean0": m0,
        "mean1": m1,
        "median0": float(np.median(xa)),
        "median1": float(np.median(xb)),
        "standardized_difference": None if d is None else float(d),
        "auc": _auc(xb, xa),
    }


def _sign_ok(block: dict[str, Any], reference: float) -> bool:
    d = block.get("standardized_difference")
    if d is None or reference == 0:
        return False
    return (d > 0) == (reference > 0)


def _stable(full: dict[str, Any], slices: list[dict[str, Any]]) -> bool:
    d = full.get("standardized_difference")
    auc = full.get("auc")
    if d is None or auc is None:
        return False
    if full.get("n0", 0) < MIN_GROUP_N or full.get("n1", 0) < MIN_GROUP_N:
        return False
    if abs(d) < MIN_ABS_D:
        return False
    if not (auc >= MIN_AUC_EDGE or auc <= 1.0 - MIN_AUC_EDGE):
        return False
    return all(_sign_ok(block, d) for block in slices if block.get("standardized_difference") is not None and block.get("n0", 0) >= 30 and block.get("n1", 0) >= 30)


def _econ(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnl = [float(r["pnl_yen"]) for r in rows]
    gain = sum(v for v in pnl if v > 0)
    loss = -sum(v for v in pnl if v < 0)
    pf = None if loss <= 0 else gain / loss
    return {"n": len(rows), "pnl_yen": float(sum(pnl)), "pf": pf, "mean_bps": float(np.mean([r["bps"] for r in rows])) if rows else None}


def _quintiles(rows: list[dict[str, Any]], key: str, getter) -> list[dict[str, Any]]:
    usable = []
    for row in rows:
        value = getter(row)
        if value is None or value != value:
            continue
        usable.append((float(value), 1 if row["label"] == "RATCHET_GE1" else 0))
    if len(usable) < 50:
        return []
    usable.sort(key=lambda item: item[0])
    out = []
    size = len(usable) / 5.0
    for i in range(5):
        chunk = usable[int(i * size):int((i + 1) * size)]
        out.append({"quintile": i + 1, "n": len(chunk), "future_ge1_fraction": float(np.mean([c[1] for c in chunk])) if chunk else None})
    return out


def _concentration(rows: list[dict[str, Any]], getter) -> bool:
    by_date: dict[str, list[tuple[float, int]]] = {}
    for row in rows:
        value = getter(row)
        if value is None or value != value:
            continue
        by_date.setdefault(row["date"], []).append((float(value), 1 if row["label"] == "RATCHET_GE1" else 0))
    diffs = []
    for date, pairs in by_date.items():
        a = [v for v, y in pairs if y == 0]
        b = [v for v, y in pairs if y == 1]
        if len(a) < 5 or len(b) < 5:
            continue
        diffs.append((date, float(np.mean(b) - np.mean(a)), len(pairs)))
    if not diffs:
        return False
    total = sum(abs(d) * n for _, d, n in diffs)
    top = max(diffs, key=lambda item: abs(item[1]) * item[2])
    if total <= 0:
        return False
    return abs(top[1]) * top[2] / total > 0.50


def build(raw: dict[str, Any]) -> dict[str, Any]:
    rows = raw["rows"]
    dates = list(raw["dates"])
    folds = [set(dates[i * len(dates) // 3:(i + 1) * len(dates) // 3]) for i in range(3)]
    periods = {
        "ORIGINAL18": set(raw["original"]),
        "EXTENSION17": set(raw["extension"]),
        "FOLD1": folds[0],
        "FOLD2": folds[1],
        "FOLD3": folds[2],
    }

    def t0_get(key):
        return lambda row: row["t0"].get(key)

    t0_full = {key: _compare(rows, key, t0_get(key)) for key in T0_KEYS}
    t0_slices = {
        name: {key: _compare([r for r in rows if r["date"] in dateset], key, t0_get(key)) for key in T0_KEYS}
        for name, dateset in periods.items()
    }
    t0_stable_features = [key for key, block in t0_full.items() if _stable(block, [t0_slices[name][key] for name in periods])]
    discovery = {}
    landmark_results = {}
    any_post = False
    earliest = None
    family_any = {name: False for name in FAMILIES}
    concentration = False
    for ms in TIME_LANDMARKS_MS:
        bucket = []
        ratcheted = exited = unresolved = 0
        for row in rows:
            state = row["landmarks"][str(ms)]["status"]
            if state == "ALREADY_RATCHETED":
                ratcheted += 1
            elif state == "ALREADY_EXITED":
                exited += 1
            else:
                unresolved += 1
                bucket.append(row)
        discovery[str(ms)] = {
            "already_ratcheted_n": ratcheted,
            "already_exited_n": exited,
            "still_unresolved_n": unresolved,
            "already_ratcheted_fraction": ratcheted / len(rows) if rows else None,
            "already_exited_fraction": exited / len(rows) if rows else None,
        }
        if ms == 0:
            continue
        sample = bucket
        feature_rows = {}
        stable_here = []
        for family, keys in FAMILIES.items():
            feature_rows[family] = {}
            for key in keys:
                getter = lambda row, key=key, ms=ms: (row["landmarks"][str(ms)]["features"] or {}).get(key)
                full = _compare(sample, key, getter)
                slices = [_compare([r for r in sample if r["date"] in dateset], key, getter) for dateset in periods.values()]
                ok = _stable(full, slices)
                feature_rows[family][key] = {"full": full, "stable": ok}
                if ok:
                    stable_here.append(f"{family}:{key}")
                    family_any[family] = True
                    any_post = True
                    if earliest is None:
                        earliest = ms
                    if _concentration(sample, getter):
                        concentration = True
        landmark_results[str(ms)] = {
            "at_risk_n": len(sample),
            "already_ratcheted_n": ratcheted,
            "already_exited_n": exited,
            "stable_features": stable_here,
            "stable_precursor": bool(stable_here),
            "families": {family: any(item["stable"] for item in feats.values()) for family, feats in feature_rows.items()},
            "quintiles": {key: _quintiles(sample, key, lambda row, key=key, ms=ms: (row["landmarks"][str(ms)]["features"] or {}).get(key)) for key in QUINTILE_KEYS},
        }
    r0 = [r for r in rows if r["label"] == "RATCHET_0"]
    ge1 = [r for r in rows if r["label"] == "RATCHET_GE1"]
    t0_sep = bool(t0_stable_features)
    post_sep = any_post
    if t0_sep:
        verdict, nxt = "V2_FIRST_RATCHET_SIGNAL_TIME_PRECURSOR_FOUND_V1", "PRECOMMIT_SINGLE_ENTRY_GATE_FROM_SUPPORTED_PRECURSOR"
    elif post_sep:
        verdict, nxt = "V2_FIRST_RATCHET_POST_SIGNAL_PRECURSOR_FOUND_V1", "PRECOMMIT_SINGLE_DELAY_OR_ABORT_RULE_FROM_SUPPORTED_PRECURSOR"
    else:
        verdict, nxt = "V2_FIRST_RATCHET_NO_STABLE_EARLY_SEPARATION_V1", "ACCEPT_RATCHET0_AS_CURRENTLY_UNPREDICTABLE"
    return {
        "verdict": verdict,
        "next": nxt,
        "t0": t0_full,
        "t0_by_period": {name: {key: {"standardized_difference": block.get("standardized_difference"), "auc": block.get("auc"), "n0": block.get("n0"), "n1": block.get("n1")} for key, block in feats.items()} for name, feats in t0_slices.items()},
        "t0_stable_features": t0_stable_features,
        "t0_stable_separation": t0_sep,
        "discovery": discovery,
        "landmarks": landmark_results,
        "family_any_post_signal": family_any,
        "post_signal_separation": post_sep,
        "earliest_landmark_ms": earliest,
        "concentration_problem": concentration,
        "ratchet0": _econ(r0),
        "ratchet_ge1": _econ(ge1),
        "predictable_at_entry": t0_sep,
        "information_before_first_ratchet": post_sep,
        "later_test_justified": bool(t0_sep or post_sep),
        "rule": {"min_abs_standardized_difference": MIN_ABS_D, "min_auc_edge": MIN_AUC_EDGE, "min_group_n": MIN_GROUP_N},
    }
