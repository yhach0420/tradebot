"""Rule / score search: maximize Winner capture while minimizing STOP & NoProgress."""
from __future__ import annotations

from itertools import combinations
from typing import Any, Mapping, Optional, Sequence

import numpy as np

from research.winner_feature_filter.labels import LabeledTrade


def _metrics(
    kept: np.ndarray,
    labeled: Sequence[LabeledTrade],
    *,
    baseline_mean_pnl: Optional[float] = None,
    baseline_w_prec: Optional[float] = None,
) -> dict[str, Any]:
    n = len(labeled)
    k = int(kept.sum())
    if k == 0:
        return {
            "n_kept": 0,
            "keep_rate": 0.0,
            "winner_capture": 0.0,
            "winner_precision": 0.0,
            "stop_rate": 0.0,
            "np_rate": 0.0,
            "normal_rate": 0.0,
            "mean_pnl": None,
            "total_pnl": 0.0,
            "score": -1e9,
        }
    winners = np.array([r.is_winner for r in labeled])
    stops = np.array([r.cohort == "STOP" for r in labeled])
    nps = np.array([r.cohort == "NoProgress" for r in labeled])
    pnls = np.array([r.pnl_yen for r in labeled], dtype=float)
    n_w = max(int(winners.sum()), 1)
    keep_rate = float(k) / n
    w_cap = float((kept & winners).sum()) / n_w
    w_prec = float((kept & winners).sum()) / k
    stop_r = float((kept & stops).sum()) / k
    np_r = float((kept & nps).sum()) / k
    mean_pnl = float(pnls[kept].mean())
    total_pnl = float(pnls[kept].sum())
    # Identity / near-all keep is not a filter — hard-penalize.
    if keep_rate >= 0.92:
        score = -1e6 + w_prec
    else:
        base_pnl = float(baseline_mean_pnl) if baseline_mean_pnl is not None else float(pnls.mean())
        base_prec = float(baseline_w_prec) if baseline_w_prec is not None else float(winners.mean())
        score = (
            1.6 * (w_prec - base_prec)
            + 0.7 * w_cap
            - 1.3 * stop_r
            - 1.3 * np_r
            + 1.0 * np.tanh((mean_pnl - base_pnl) / 700.0)
            - 0.55 * abs(keep_rate - 0.40)
        )
        if mean_pnl < base_pnl:
            score -= 0.75
        if np_r > float(np.mean([r.cohort == "NoProgress" for r in labeled])) + 0.02:
            score -= 0.35
        if w_prec < base_prec + 0.01 and mean_pnl <= base_pnl + 50:
            score -= 0.4
    return {
        "n_kept": k,
        "keep_rate": round(keep_rate, 4),
        "winner_capture": round(w_cap, 4),
        "winner_precision": round(w_prec, 4),
        "stop_rate": round(stop_r, 4),
        "np_rate": round(np_r, 4),
        "normal_rate": round(
            float((kept & np.array([r.cohort == "Normal" for r in labeled])).sum()) / k, 4
        ),
        "mean_pnl": round(mean_pnl, 2),
        "total_pnl": round(total_pnl, 2),
        "score": round(float(score), 6),
    }


def _col(rows: Sequence[Mapping[str, Optional[float]]], name: str) -> np.ndarray:
    return np.array([np.nan if r.get(name) is None else float(r[name]) for r in rows], dtype=float)


def _threshold_candidates(col: np.ndarray) -> list[float]:
    v = col[~np.isnan(col)]
    if len(v) < 30:
        return []
    qs = [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
    return sorted(set(float(np.quantile(v, q)) for q in qs))


# Exclude near-leaky / non-actionable / duplicate aliases from rule search.
RULE_EXCLUDE_SUBSTRINGS = (
    "rolling_mfe",
    "rolling_mae",
    "f_rolling_",
    "tech_rolling_",
)


def _rule_feature_ok(name: str) -> bool:
    low = name.lower()
    return not any(s in low for s in RULE_EXCLUDE_SUBSTRINGS)


def search_single_rules(
    labeled: Sequence[LabeledTrade],
    rows: Sequence[Mapping[str, Optional[float]]],
    feature_names: Sequence[str],
    *,
    top_features: Sequence[str],
    baseline_mean_pnl: float,
    baseline_w_prec: float,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for name in top_features:
        if name not in feature_names or not _rule_feature_ok(name):
            continue
        col = _col(rows, name)
        for thr in _threshold_candidates(col):
            for op, fn in ((">=", lambda x, t: x >= t), ("<=", lambda x, t: x <= t)):
                mask = ~np.isnan(col)
                kept = mask & fn(col, thr)
                m = _metrics(
                    kept,
                    labeled,
                    baseline_mean_pnl=baseline_mean_pnl,
                    baseline_w_prec=baseline_w_prec,
                )
                if m["n_kept"] < 40 or m["keep_rate"] >= 0.92:
                    continue
                results.append(
                    {
                        "type": "single",
                        "rule": f"{name} {op} {thr:.6g}",
                        "feature": name,
                        "op": op,
                        "threshold": thr,
                        **m,
                    }
                )
    results.sort(key=lambda r: -r["score"])
    return results[:80]


def _feat_stem(name: str) -> str:
    n = name
    for p in ("tech_", "board_", "px_", "vol_", "mom_", "mkt_", "f_"):
        if n.startswith(p):
            n = n[len(p) :]
            break
    return n.replace("vwap_dev", "vwap").replace("imb_pct", "imb")


def search_and_or_rules(
    labeled: Sequence[LabeledTrade],
    rows: Sequence[Mapping[str, Optional[float]]],
    *,
    single_best: Sequence[dict[str, Any]],
    baseline_mean_pnl: float,
    baseline_w_prec: float,
    max_pair: int = 12,
) -> list[dict[str, Any]]:
    """Combine top single rules with AND / OR."""
    # Prefer unique stems among seeds
    seeds: list[dict[str, Any]] = []
    seen: set[str] = set()
    for s in single_best:
        stem = _feat_stem(str(s["feature"]))
        if stem in seen:
            continue
        seen.add(stem)
        seeds.append(s)
        if len(seeds) >= max_pair:
            break
    parsed = []
    for s in seeds:
        name = s["feature"]
        op = s["op"]
        thr = float(s["threshold"])
        col = _col(rows, name)
        if op == ">=":
            mask = (~np.isnan(col)) & (col >= thr)
        else:
            mask = (~np.isnan(col)) & (col <= thr)
        parsed.append((s["rule"], mask, _feat_stem(str(name))))

    results: list[dict[str, Any]] = []
    for (r1, m1, s1), (r2, m2, s2) in combinations(parsed, 2):
        if s1 == s2:
            continue
        for kind, kept in (("AND", m1 & m2), ("OR", m1 | m2)):
            m = _metrics(
                kept,
                labeled,
                baseline_mean_pnl=baseline_mean_pnl,
                baseline_w_prec=baseline_w_prec,
            )
            if m["n_kept"] < 40 or m["keep_rate"] >= 0.92:
                continue
            results.append(
                {
                    "type": kind,
                    "rule": f"({r1}) {kind} ({r2})",
                    **m,
                }
            )
    results.sort(key=lambda r: -r["score"])
    return results[:60]


def build_winner_score(
    rows: Sequence[Mapping[str, Optional[float]]],
    labeled: Sequence[LabeledTrade],
    *,
    weight_features: Sequence[tuple[str, float, str]],
) -> tuple[np.ndarray, dict[str, Any], list[dict[str, Any]]]:
    """
    weight_features: list of (feature, weight, direction) where direction in ('high','low').
    Score = sum(weight * z(feature) * sign).
    """
    n = len(rows)
    score = np.zeros(n, dtype=float)
    used = []
    for name, w, direction in weight_features:
        col = _col(rows, name)
        mu = float(np.nanmean(col))
        sd = float(np.nanstd(col)) + 1e-9
        z = (col - mu) / sd
        z = np.nan_to_num(z, nan=0.0)
        sign = 1.0 if direction == "high" else -1.0
        score += float(w) * sign * z
        used.append({"feature": name, "weight": w, "direction": direction, "mean": mu, "std": sd})

    base_pnl = float(np.mean([r.pnl_yen for r in labeled])) if labeled else 0.0
    base_prec = float(np.mean([1 if r.is_winner else 0 for r in labeled])) if labeled else 0.2
    cands = []
    for q in (0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75):
        thr = float(np.quantile(score, q))
        kept = score >= thr
        m = _metrics(kept, labeled, baseline_mean_pnl=base_pnl, baseline_w_prec=base_prec)
        if m["keep_rate"] >= 0.92:
            continue
        cands.append(
            {
                "type": "score",
                "rule": f"winner_rise_score >= {thr:.6g} (q={q})",
                "threshold": thr,
                "quantile": q,
                **m,
            }
        )
    cands.sort(key=lambda r: -r["score"])
    best = cands[0] if cands else {}
    meta = {"components": used, "best": best, "candidates": cands[:15]}
    return score, meta, cands


def infer_direction(
    labeled: Sequence[LabeledTrade],
    rows: Sequence[Mapping[str, Optional[float]]],
    feature: str,
) -> str:
    col = _col(rows, feature)
    w = np.array([r.is_winner for r in labeled])
    a = col[w]
    b = col[~w]
    a = a[~np.isnan(a)]
    b = b[~np.isnan(b)]
    if len(a) < 10 or len(b) < 10:
        return "high"
    return "high" if float(np.mean(a)) >= float(np.mean(b)) else "low"


def search_all_rules(
    labeled: Sequence[LabeledTrade],
    rows: Sequence[Mapping[str, Optional[float]]],
    feature_names: Sequence[str],
    importance_merged: Sequence[Mapping[str, Any]],
    *,
    top_n: int = 22,
) -> dict[str, Any]:
    base_kept = np.ones(len(labeled), dtype=bool)
    base_pnl = float(np.mean([r.pnl_yen for r in labeled])) if labeled else 0.0
    base_prec = float(np.mean([1 if r.is_winner else 0 for r in labeled])) if labeled else 0.2
    baseline = _metrics(base_kept, labeled, baseline_mean_pnl=base_pnl, baseline_w_prec=base_prec)
    baseline["type"] = "baseline_pbv2_all"
    baseline["rule"] = "PBv2 accept all"
    # Override identity penalty for explicit baseline reporting
    baseline["score"] = 0.0

    # Deduplicate aliases for rule seeds (prefer canonical short names)
    seen_stem: set[str] = set()
    top_feats: list[str] = []
    for r in importance_merged:
        name = str(r["feature"])
        if not _rule_feature_ok(name):
            continue
        stem = name.replace("tech_", "").replace("board_", "").replace("px_", "").replace("vol_", "").replace("mkt_", "")
        if stem in seen_stem:
            continue
        seen_stem.add(stem)
        top_feats.append(name)
        if len(top_feats) >= top_n:
            break

    singles = search_single_rules(
        labeled,
        rows,
        feature_names,
        top_features=top_feats,
        baseline_mean_pnl=base_pnl,
        baseline_w_prec=base_prec,
    )
    combos = search_and_or_rules(
        labeled,
        rows,
        single_best=singles,
        baseline_mean_pnl=base_pnl,
        baseline_w_prec=base_prec,
        max_pair=10,
    )

    weights: list[tuple[str, float, str]] = []
    for row in importance_merged:
        name = str(row["feature"])
        if not _rule_feature_ok(name):
            continue
        w = float(row.get("consensus_score") or 0.0)
        if w <= 0:
            continue
        direction = infer_direction(labeled, rows, name)
        weights.append((name, max(w, 0.05), direction))
        if len(weights) >= 10:
            break
    scores, score_meta, score_cands = build_winner_score(rows, labeled, weight_features=weights)

    all_rules = singles[:40] + combos[:40] + score_cands[:15]
    all_rules.sort(key=lambda r: (-r["score"], -(r.get("mean_pnl") or -1e18)))

    def _is_deployable(r: Mapping[str, Any]) -> bool:
        if (r.get("mean_pnl") is None) or float(r["mean_pnl"]) <= base_pnl:
            return False
        if float(r.get("keep_rate") or 0) < 0.08 or float(r.get("keep_rate") or 0) > 0.70:
            return False
        if float(r.get("stop_rate") or 1) > baseline["stop_rate"] + 0.03:
            return False
        if float(r.get("np_rate") or 1) > baseline["np_rate"] + 0.03:
            return False
        if r.get("type") == "OR":
            return False  # OR is exploratory; prefer AND/single/score for deployable filter
        return True

    deployable = [r for r in all_rules if _is_deployable(r)]
    if deployable:
        # Prefer AND, then single, then score; then mean_pnl / score
        type_rank = {"AND": 3, "single": 2, "score": 1}
        best = max(
            deployable,
            key=lambda r: (
                type_rank.get(str(r.get("type")), 0),
                float(r.get("score") or -1e9),
                float(r.get("mean_pnl") or -1e9),
            ),
        )
    elif all_rules:
        best = all_rules[0]
    else:
        best = score_meta.get("best") or {}
    score_best = score_meta.get("best") or {}

    return {
        "baseline_pbv2": baseline,
        "best_rule": best,
        "best_objective_rule": all_rules[0] if all_rules else best,
        "best_score_filter": score_best,
        "single_rules": singles[:40],
        "and_or_rules": combos[:40],
        "score_meta": score_meta,
        "score_candidates": score_cands[:15],
        "winner_rise_scores": [round(float(x), 6) for x in scores.tolist()],
        "recommended_filter": best,
        "recommended_score_filter": score_best if score_best else None,
    }
