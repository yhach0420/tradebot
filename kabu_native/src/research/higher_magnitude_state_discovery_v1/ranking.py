"""Contemporaneous cross-sectional ranking. Inputs known at decision_time only. K in {1,3} predeclared."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.higher_magnitude_state_discovery_v1 import RANK_DIAGNOSTIC_K

Y = "x0_h15_bps"


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def rank_score(e: dict[str, Any]) -> float | None:
    if not _finite(e.get("sector_rs")) or not _finite(e.get("rs_5m")):
        return None
    return float(e["sector_rs"]) + float(e["rs_5m"])


def attach_ranks(episodes: list[dict[str, Any]]) -> None:
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for e in episodes:
        buckets[(str(e.get("date")), str(e.get("event_time")))].append(e)
    for xs in buckets.values():
        scored = []
        for e in xs:
            s = rank_score(e)
            e["rank_score"] = s
            e["rank_inputs"] = "sector_rs + rs_5m"
            e["rank_used_future"] = False
            if s is None:
                e["cs_rank"] = None
                e["cs_n_at_clock"] = len(xs)
                e["cs_rank_pct"] = None
                continue
            scored.append(e)
        scored.sort(key=lambda r: (-float(r["rank_score"]), str(r.get("symbol"))))
        n = len(scored)
        for i, e in enumerate(scored, start=1):
            e["cs_rank"] = i
            e["cs_n_at_clock"] = n
            e["cs_rank_pct"] = (i - 1) / max(n - 1, 1) if n > 1 else 0.0


def rank_response(episodes: list[dict[str, Any]], *, y: str = Y) -> dict[str, Any]:
    rows = [e for e in episodes if e.get("cs_rank") is not None and _finite(e.get(y))]
    if len(rows) < 50:
        return {"n": len(rows), "ordered": False, "reason": "too_few"}
    pct = np.asarray([float(e["cs_rank_pct"]) for e in rows], dtype=float)
    yy = np.asarray([float(e[y]) for e in rows], dtype=float)
    mfe = np.asarray([float(e["mfe_bps"]) for e in rows if _finite(e.get("mfe_bps"))], dtype=float)
    bins = np.digitize(pct, [0.2, 0.4, 0.6, 0.8], right=True)
    q_means = []
    q_n = []
    q_mfe = []
    for b in range(5):
        sl = yy[bins == b]
        q_means.append(float(np.mean(sl)) if sl.size else None)
        q_n.append(int(sl.size))
        mf = np.asarray([float(e["mfe_bps"]) for e, bb in zip(rows, bins) if bb == b and _finite(e.get("mfe_bps"))], dtype=float)
        q_mfe.append(float(np.mean(mf)) if mf.size else None)
    finite = [v for v in q_means if v is not None]
    # Q1 = best rank (pct near 0) should have higher y if ranking works
    ordered = False
    if len(finite) >= 4:
        diffs = np.diff(np.asarray(finite, dtype=float))
        ordered = bool(np.all(diffs <= 0) or np.sum(diffs <= 0) >= 3)
    by_k = {}
    for k in RANK_DIAGNOSTIC_K:
        sub = [float(e[y]) for e in rows if int(e["cs_rank"]) <= int(k)]
        rest = [float(e[y]) for e in rows if int(e["cs_rank"]) > int(k)]
        by_k[f"top{k}"] = {
            "n": len(sub),
            "mean_y": float(np.mean(sub)) if sub else None,
            "rest_n": len(rest),
            "rest_mean_y": float(np.mean(rest)) if rest else None,
            "lift_vs_rest": (float(np.mean(sub) - np.mean(rest)) if sub and rest else None),
        }
    day_bucket: dict[str, list[float]] = defaultdict(list)
    for e in rows:
        if int(e["cs_rank"]) == 1:
            day_bucket[str(e["date"])].append(float(e[y]))
    return {
        "n": len(rows),
        "quintile_means_best_to_worst": q_means,
        "quintile_n": q_n,
        "quintile_mfe": q_mfe,
        "ordered_rank_response": ordered,
        "k_predeclared": list(RANK_DIAGNOSTIC_K),
        "k_searched_1_to_30": False,
        "by_k": by_k,
        "rank1_day_n": len(day_bucket),
        "mean_mfe": float(np.mean(mfe)) if mfe.size else None,
        "did_not_use_future_in_rank": True,
    }


def absolute_vs_rank(*, episodes: list[dict[str, Any]], y: str = Y) -> dict[str, Any]:
    rows = [e for e in episodes if _finite(e.get(y))]
    abs_pos = [float(e[y]) for e in rows if _finite(e.get("sector_rs")) and float(e["sector_rs"]) > 0]
    abs_non = [float(e[y]) for e in rows if _finite(e.get("sector_rs")) and float(e["sector_rs"]) <= 0]
    top3 = [float(e[y]) for e in rows if e.get("cs_rank") is not None and int(e["cs_rank"]) <= 3]
    return {
        "absolute_rule": "sector_rs > 0 (sign, not Q10 search)",
        "absolute_n": len(abs_pos),
        "absolute_mean_y": float(np.mean(abs_pos)) if abs_pos else None,
        "absolute_control_n": len(abs_non),
        "absolute_control_mean_y": float(np.mean(abs_non)) if abs_non else None,
        "ranked_top3_n": len(top3),
        "ranked_top3_mean_y": float(np.mean(top3)) if top3 else None,
        "ranking_vs_absolute_lift": (
            float(np.mean(top3) - np.mean(abs_pos)) if top3 and abs_pos else None
        ),
    }
