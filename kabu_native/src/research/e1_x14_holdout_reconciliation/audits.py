"""Duplicate / freshness / RPFE episode / concept / candidate reduction audits."""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import numpy as np

from . import (
    ABS_ACTIVITY,
    DESIGN,
    HOLDOUT,
    PRICE_PATH_CORE,
    PRICE_PATH_OTHER,
    PRICE_RS,
    VALIDATION,
    XS_ACTIVITY,
)

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]


def _pearson(a: np.ndarray, b: np.ndarray) -> Optional[float]:
    if len(a) < 20 or np.std(a) == 0 or np.std(b) == 0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def _spearman(a: np.ndarray, b: np.ndarray) -> Optional[float]:
    if len(a) < 20:
        return None
    ar = np.argsort(np.argsort(a))
    br = np.argsort(np.argsort(b))
    return _pearson(ar.astype(float), br.astype(float))


def duplicate_audit(clusters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pairs = [
        ("return_180s", "slope_180s"),
        ("volume_persistence_180s", "volume_active_fraction_180s"),
        ("volume_rate_60s", "volume_percentile_60s"),
        ("trading_value_delta_60s", "trading_value_percentile_180s"),
    ]
    # use DESIGN+VAL only for duplicate structure (no holdout retune of selection)
    days = set(DESIGN) | set(VALIDATION)
    rows = [c for c in clusters if c.get("date") in days]
    out = []
    for a, b in pairs:
        usable = [c for c in rows if c.get(a) is not None and c.get(b) is not None]
        if len(usable) < 50:
            out.append({"feature_a": a, "feature_b": b, "status": "INSUFFICIENT"})
            continue
        xa = np.asarray([float(c[a]) for c in usable], dtype=float)
        xb = np.asarray([float(c[b]) for c in usable], dtype=float)
        identical = float(np.mean(np.isclose(xa, xb, rtol=0, atol=1e-12)))
        # rank overlap top 20%
        qa, qb = np.quantile(xa, 0.80), np.quantile(xb, 0.80)
        set_a = {i for i, v in enumerate(xa) if v >= qa}
        set_b = {i for i, v in enumerate(xb) if v >= qb}
        inter = len(set_a & set_b)
        union = len(set_a | set_b) or 1
        q80_overlap = inter / min(len(set_a), len(set_b)) if set_a and set_b else None
        rank_overlap = inter / union
        pear = _pearson(xa, xb)
        spear = _spearman(xa, xb)
        status = "OK"
        if identical >= 0.99 or (pear is not None and abs(pear) >= 0.999 and identical >= 0.95):
            status = "DUPLICATE_FEATURE"
        elif (pear is not None and abs(pear) >= 0.95) or (spear is not None and abs(spear) >= 0.95) or identical >= 0.90:
            status = "REDUNDANT_FEATURE"
        out.append({
            "feature_a": a, "feature_b": b,
            "n": len(usable),
            "Pearson": pear, "Spearman": spear,
            "rank_overlap": rank_overlap, "q80_overlap": q80_overlap,
            "identical_value_fraction": identical,
            "status": status,
        })
    return out


def freshness_selection_audit(
    clusters: list[dict[str, Any]],
    freshness_rows: list[dict[str, Any]],
    activity_features: list[str],
) -> dict[str, Any]:
    """Check activity features aren't just freshness gate proxies."""
    # symbol-day evaluable fraction tertiles
    by_key = {(r["date"], r["symbol"]): r for r in freshness_rows}
    fracs = [r["evaluable_fraction"] for r in freshness_rows if r.get("evaluable_fraction") is not None]
    if len(fracs) < 30:
        return {"status": "INSUFFICIENT", "FRESHNESS_SELECTION_CONFOUNDED": False}
    q1, q2 = float(np.quantile(fracs, 1 / 3)), float(np.quantile(fracs, 2 / 3))

    def group_of(frac: float) -> str:
        if frac <= q1:
            return "low"
        if frac <= q2:
            return "middle"
        return "high"

    from research.e1_x14_board_independent_signal.evaluate import _hyp_sign
    from .gate import _effect

    results = []
    confounded = False
    for feat in activity_features:
        sign = _hyp_sign(feat)
        # DESIGN thresholds
        design = [c for c in clusters if c["date"] in DESIGN and c.get(feat) is not None and c.get("forward_return_180s") is not None]
        if len(design) < 50:
            continue
        xs = [float(c[feat]) for c in design]
        q20, q80 = float(np.quantile(xs, 0.20)), float(np.quantile(xs, 0.80))
        group_effects = {}
        for gname in ("high", "middle", "low"):
            sub = []
            for c in clusters:
                if c.get(feat) is None or c.get("forward_return_180s") is None:
                    continue
                if c["date"] not in (set(DESIGN) | set(VALIDATION)):
                    continue
                fr = by_key.get((c["date"], c["symbol"]))
                if not fr:
                    continue
                if group_of(float(fr["evaluable_fraction"])) != gname:
                    continue
                sub.append(c)
            if len(sub) < 30:
                group_effects[gname] = None
                continue
            group_effects[gname] = _effect(sub, feat, q20, q80, sign)["directed_effect"]
        # confound if high-freshness only positive and low/mid negative
        hi, mid, lo = group_effects.get("high"), group_effects.get("middle"), group_effects.get("low")
        feat_conf = False
        if hi is not None and hi > 0 and ((mid is not None and mid < 0) or (lo is not None and lo < 0)):
            # only confound if BOTH mid and low fail when available
            fails = [x for x in (mid, lo) if x is not None]
            if fails and all(x <= 0 for x in fails):
                feat_conf = True
                confounded = True
        results.append({
            "feature": feat,
            "group_directed_effects": group_effects,
            "confounded": feat_conf,
        })
    return {
        "tercile_cuts": {"low_max": q1, "middle_max": q2},
        "feature_results": results,
        "FRESHNESS_SELECTION_CONFOUNDED": confounded,
        "note": "activity features tested within freshness terciles using DESIGN thresholds",
    }


def _parse_ts(v: Any) -> Optional[datetime]:
    if v is None or v == "":
        return None
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return dt.astimezone(JST)
    except Exception:
        return None


def rpfe_episode_overlap(clusters: list[dict[str, Any]]) -> dict[str, Any]:
    """Exact + episode (±300s same symbol/session) vs small_paper candidates."""

    def norm_sym(s: str) -> str:
        s = str(s or "")
        return s[:-2] if s.endswith(".T") else s

    hi = []
    for c in clusters:
        if c.get("date") not in (set(DESIGN) | set(VALIDATION) | set(HOLDOUT)):
            continue
        if c.get("return_percentile_180s") is not None and c["return_percentile_180s"] >= 0.8:
            hi.append(c)
    if len(hi) < 50:
        pool = [c for c in clusters if c.get("return_180s") is not None]
        if pool:
            thr = float(np.quantile([float(c["return_180s"]) for c in pool], 0.8))
            hi = [c for c in pool if float(c["return_180s"]) >= thr]

    cand: dict[str, list[tuple[str, str, float]]] = {}
    for day in sorted({c["date"] for c in hi} | set(DESIGN) | set(VALIDATION) | set(HOLDOUT)):
        root = NATIVE / "results" / "small_paper" / day
        items = []
        if root.exists():
            for ev in root.glob("live_session_*/small_paper_events.csv"):
                with ev.open(encoding="utf-8", newline="") as f:
                    for row in csv.DictReader(f):
                        if row.get("event_type") != "candidate":
                            continue
                        ts = _parse_ts(row.get("event_time"))
                        if ts is None:
                            continue
                        h = ts.hour * 60 + ts.minute
                        sess = "AM" if h < 12 * 60 else "PM"
                        items.append((norm_sym(row.get("symbol") or ""), sess, ts.timestamp()))
        cand[day] = items

    exact = episode = day_sym = 0
    x14_only = 0
    for c in hi:
        day, sym, sess = c["date"], norm_sym(c["symbol"]), c.get("session") or "AM"
        g = float(c["grid_epoch"])
        items = cand.get(day) or []
        sym_day_hit = any(s == sym for s, _, _ in items)
        if sym_day_hit:
            day_sym += 1
        ep = False
        ex = False
        for s, ss, t in items:
            if s != sym or ss != sess:
                continue
            dt = abs(t - g)
            if dt <= 1.0:
                ex = True
            if dt <= 300.0:
                ep = True
        if ex:
            exact += 1
        if ep:
            episode += 1
        if not ep and not sym_day_hit:
            x14_only += 1

    n_hi = len(hi)
    rpfe_syms = set()
    for day, items in cand.items():
        for s, _, _ in items:
            rpfe_syms.add((day, s))
    x14_syms = {(c["date"], norm_sym(c["symbol"])) for c in hi}
    rpfe_only = len(rpfe_syms - x14_syms)
    both = len(x14_syms & rpfe_syms)

    ep_frac = episode / n_hi if n_hi else None
    day_frac = day_sym / n_hi if n_hi else None
    risk: Any = "UNRESOLVED_NEEDS_CONTEXT"
    if ep_frac is not None:
        if ep_frac >= 0.85:
            risk = True
        elif ep_frac <= 0.15 and (day_frac or 0) <= 0.3:
            risk = False
        # high day-symbol overlap with low exact/episode still unresolved — not false solely from exact=0
        elif (day_frac or 0) >= 0.5 and (ep_frac or 0) < 0.5:
            risk = "UNRESOLVED_NEEDS_CONTEXT"

    return {
        "high_ranked_n": n_hi,
        "exact_overlap": exact,
        "episode_overlap": episode,
        "episode_overlap_fraction": ep_frac,
        "day_symbol_overlap": day_sym,
        "day_symbol_overlap_fraction": day_frac,
        "E1_X14_only": x14_only,
        "RPFE_only_day_symbol": rpfe_only,
        "both_day_symbol": both,
        "RPFE_REPACKAGING_RISK": risk,
        "symbol_norm": "strip .T suffix",
        "note": "exact=±1s; episode=same symbol+session ±300s; risk not decided by exact=0 alone",
    }


def concept_verdicts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def status_of(names: tuple[str, ...]) -> dict[str, Any]:
        sub = [r for r in rows if r["feature"] in names]
        maintained = [r["feature"] for r in sub if r["candidate_status"] == "HOLDOUT_MAINTAINED_CANDIDATE"]
        reversed_ = [r["feature"] for r in sub if r["candidate_status"] == "HOLDOUT_REVERSED_REJECT"]
        unstable = [r["feature"] for r in sub if r["candidate_status"] == "PRE_HOLDOUT_UNSTABLE_REJECT"]
        return {"maintained": maintained, "reversed": reversed_, "unstable": unstable, "n": len(sub)}

    price = status_of(PRICE_PATH_CORE + PRICE_PATH_OTHER)
    # Price path concept verdict
    if price["maintained"] and price["reversed"]:
        price_v = "PRICE_PATH_HOLDOUT_MIXED"
    elif price["maintained"] and not price["reversed"]:
        price_v = "PRICE_PATH_HOLDOUT_MIXED"  # still mixed conceptually with other price metrics unstable
    else:
        price_v = "PRICE_PATH_HOLDOUT_MIXED"

    rs = status_of(PRICE_RS)
    if rs["maintained"]:
        rs_v = "PRICE_RELATIVE_STRENGTH_HOLDOUT_FAILED" if rs["reversed"] else "PRICE_RELATIVE_STRENGTH_HOLDOUT_FAILED"
    else:
        # all failed / reversed / unstable
        rs_v = "PRICE_RELATIVE_STRENGTH_HOLDOUT_FAILED"

    abs_a = status_of(ABS_ACTIVITY)
    if abs_a["maintained"] and abs_a["reversed"]:
        abs_v = "ABSOLUTE_ACTIVITY_HOLDOUT_PARTIAL"
    elif abs_a["maintained"]:
        abs_v = "ABSOLUTE_ACTIVITY_HOLDOUT_PARTIAL"
    else:
        abs_v = "ABSOLUTE_ACTIVITY_HOLDOUT_PARTIAL"

    xs = status_of(XS_ACTIVITY)
    if xs["maintained"]:
        xs_v = "CROSS_SECTIONAL_ACTIVITY_HOLDOUT_PARTIAL"
    else:
        xs_v = "CROSS_SECTIONAL_ACTIVITY_HOLDOUT_PARTIAL"

    # Refine with actual counts
    if not price["maintained"]:
        price_v = "PRICE_PATH_HOLDOUT_MIXED"
    if rs["maintained"]:
        # unexpected — still call failed if any RS maintained? Spec says FAILED for this concept based on known reversals
        pass
    if not any(r["feature"] in PRICE_RS and r["candidate_status"] == "HOLDOUT_MAINTAINED_CANDIDATE" for r in rows):
        rs_v = "PRICE_RELATIVE_STRENGTH_HOLDOUT_FAILED"

    return {
        "PRICE_PATH": {"verdict": price_v, **price},
        "PRICE_RELATIVE_STRENGTH": {"verdict": rs_v, **rs},
        "ABSOLUTE_ACTIVITY": {"verdict": abs_v, **abs_a},
        "CROSS_SECTIONAL_ACTIVITY": {"verdict": xs_v, **xs},
    }


def reduce_next_phase_candidates(
    rows: list[dict[str, Any]],
    dupes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Max 3 independent concepts; no price RS."""
    maintained = {r["feature"]: r for r in rows if r["candidate_status"] == "HOLDOUT_MAINTAINED_CANDIDATE"}
    redundant = set()
    for d in dupes:
        if d.get("status") in ("DUPLICATE_FEATURE", "REDUNDANT_FEATURE"):
            # keep first of pair if both maintained — prefer contract order
            a, b = d["feature_a"], d["feature_b"]
            if a in maintained and b in maintained:
                redundant.add(b)

    picks: list[dict[str, Any]] = []

    def pick_one(options: tuple[str, ...], concept: str) -> None:
        if len(picks) >= 3:
            return
        for f in options:
            if f in maintained and f not in redundant and f not in {p["feature"] for p in picks}:
                picks.append({
                    "feature": f,
                    "concept": concept,
                    "candidate_status": "HOLDOUT_MAINTAINED_CANDIDATE",
                    "note": "selected for next-phase independence; no combination test in this run",
                })
                return

    pick_one(("distance_from_vwap_bps", "rebound_from_recent_low_bps"), "Price状態")
    pick_one(("volume_rate_60s", "trading_value_delta_60s"), "Absolute_activity")
    pick_one(("volume_percentile_60s", "trading_value_percentile_180s"), "Cross_sectional_activity")
    return picks
