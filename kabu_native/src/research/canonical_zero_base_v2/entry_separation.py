"""TRAIN-only ENTRY feature separation & stability (no forced selection)."""
from __future__ import annotations

import math
import random
from collections import defaultdict
from typing import Any, Optional, Sequence

from research.canonical_zero_base_v2.constants import SEED


def _arr(vals: Sequence[Optional[float]]) -> list[float]:
    return [float(v) for v in vals if v is not None and not (isinstance(v, float) and math.isnan(v))]


def cohens_d(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    if len(a) < 10 or len(b) < 10:
        return None
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    va = sum((x - ma) ** 2 for x in a) / len(a)
    vb = sum((x - mb) ** 2 for x in b) / len(b)
    pooled = math.sqrt((va + vb) / 2 + 1e-12)
    return (ma - mb) / pooled


def rank_auc(pos: Sequence[float], neg: Sequence[float]) -> Optional[float]:
    if len(pos) < 5 or len(neg) < 5:
        return None
    pairs = [(x, 1) for x in pos] + [(y, 0) for y in neg]
    pairs.sort(key=lambda t: t[0])
    rank_sum = 0.0
    for i, (_, lab) in enumerate(pairs, start=1):
        if lab == 1:
            rank_sum += i
    n1, n0 = len(pos), len(neg)
    return (rank_sum - n1 * (n1 + 1) / 2) / (n1 * n0)


def ks_stat(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    if len(a) < 10 or len(b) < 10:
        return None
    sa, sb = sorted(a), sorted(b)
    allv = sorted(set(sa + sb))
    i = j = 0
    maxd = 0.0
    for v in allv:
        while i < len(sa) and sa[i] <= v:
            i += 1
        while j < len(sb) and sb[j] <= v:
            j += 1
        maxd = max(maxd, abs(i / len(sa) - j / len(sb)))
    return maxd


WINNER_CLASSES = {"WINNER_FAST", "WINNER_SLOW", "WINNER_REVERSAL", "SMALL_WIN"}
STOP_CLASSES = {"EARLY_STOP_PATH", "LATE_STOP_PATH"}
NP_CLASSES = {"NOPROGRESS", "NEVER_PROFITABLE"}


def evaluate_feature_separation(
    rows: Sequence[dict[str, Any]],
    feature_names: Sequence[str],
    *,
    class_key: str = "class_name",
) -> list[dict[str, Any]]:
    """rows: {features: dict, class_name, day, symbol}"""
    results = []
    by_day = defaultdict(list)
    for r in rows:
        by_day[r["day"]].append(r)

    for fname in feature_names:
        winners = _arr([r["features"].get(fname) for r in rows if r[class_key] in WINNER_CLASSES])
        never = _arr([r["features"].get(fname) for r in rows if r[class_key] == "NEVER_PROFITABLE"])
        early = _arr([r["features"].get(fname) for r in rows if r[class_key] == "EARLY_STOP_PATH"])
        npg = _arr([r["features"].get(fname) for r in rows if r[class_key] in NP_CLASSES])
        miss = len([1 for r in rows if r["features"].get(fname) is None]) / max(1, len(rows))
        d_wn = cohens_d(winners, never)
        d_we = cohens_d(winners, early)
        d_wp = cohens_d(winners, npg)
        auc_wn = rank_auc(winners, never)
        ks_wn = ks_stat(winners, never)
        # day stability: sign of winner-never median diff across days
        signs = []
        for day, dr in by_day.items():
            w = _arr([r["features"].get(fname) for r in dr if r[class_key] in WINNER_CLASSES])
            n = _arr([r["features"].get(fname) for r in dr if r[class_key] == "NEVER_PROFITABLE"])
            if len(w) >= 5 and len(n) >= 5:
                signs.append(1 if (sum(w) / len(w)) > (sum(n) / len(n)) else -1)
        sign_stab = (abs(sum(signs)) / len(signs)) if signs else 0.0
        stable = bool(sign_stab >= 0.66 and d_wn is not None and abs(d_wn) >= 0.15 and len(by_day) >= 1)
        # single-day only => not STABLE per spec when only 1 train day we mark UNSTABLE_DAY_LIMITED
        if len(by_day) < 2:
            stable = False
            stab_label = "UNSTABLE_SINGLE_TRAIN_DAY"
        else:
            stab_label = "STABLE" if stable else "UNSTABLE"
        score = abs(d_wn or 0) + abs(d_we or 0) * 0.5 + abs(d_wp or 0) * 0.5 + (auc_wn or 0.5) - 0.5
        results.append({
            "feature": fname,
            "missing_rate": miss,
            "d_winner_vs_never": d_wn,
            "d_winner_vs_early_stop": d_we,
            "d_winner_vs_noprogress": d_wp,
            "auc_winner_vs_never": auc_wn,
            "ks_winner_vs_never": ks_wn,
            "sign_stability": sign_stab,
            "stability": stab_label,
            "score": score,
            "keep_as_interaction": True,  # do not reject solely on univariate
        })
    results.sort(key=lambda r: -(r["score"] or 0))
    return results


def bootstrap_top(
    rows: Sequence[dict[str, Any]],
    feature_names: Sequence[str],
    *,
    n_boot: int = 20,
    top_k: int = 30,
) -> list[dict[str, Any]]:
    rng = random.Random(SEED)
    counts: dict[str, int] = defaultdict(int)
    for _ in range(n_boot):
        sample = [rows[rng.randrange(len(rows))] for _ in range(len(rows))]
        sep = evaluate_feature_separation(sample, feature_names)
        for r in sep[:top_k]:
            counts[r["feature"]] += 1
    return [{"feature": f, "bootstrap_top_hits": c, "rate": c / n_boot} for f, c in sorted(counts.items(), key=lambda x: -x[1])]
