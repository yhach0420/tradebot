"""Winner-rate combination search (exclude time-of-day features)."""
from __future__ import annotations

from itertools import combinations
from typing import Any, Mapping, Optional, Sequence

import numpy as np

from research.winner_feature_filter.labels import LabeledTrade
from research.winner_feature_filter.rules import RULE_EXCLUDE_SUBSTRINGS, _col, _feat_stem, _threshold_candidates

TIME_EXCLUDE_SUBSTRINGS = (
    "minutes_from_open",
    "minutes_to_refresh",
    "near_refresh",
    "session_am",
    "day_high_from_open",
    "minutes_since_day_high",
)

# Display aliases for report readability
FEATURE_DISPLAY = {
    "f_tv": ("TV", "yen"),
    "vol_tv": ("TV", "yen"),
    "board_imb": ("imbalance", ""),
    "f_imb": ("imbalance", ""),
    "board_imb_pct": ("imbalance_pct", ""),
    "f_imb_pct": ("imbalance_pct", ""),
    "f_vwap": ("VWAP乖離", "%"),
    "px_vwap_dev": ("VWAP乖離", "%"),
    "f_atr": ("ATR", "%"),
    "px_atr": ("ATR", "%"),
    "f_chase": ("chase", ""),
    "f_rise5": ("rise5", "%"),
    "f_rise10": ("rise10", "%"),
    "f_near_high": ("near_high", "%"),
    "f_mom": ("mom", ""),
    "tech_mom": ("mom", ""),
    "board_spread": ("spread", "bps"),
    "f_spread": ("spread", "bps"),
    "board_age": ("board_age", "sec"),
    "w_60s_ret": ("return60", "%"),
    "vol_surge_60s": ("volume60_chg_pct", "%"),
    "w_60s_tv_chg": ("volume60_chg_pct", "%"),
    "w_60s_imb_chg": ("imb_chg60", ""),
    "f_np_imb_chg_60": ("imb_chg60", ""),
    "f_mom_alt": ("mom", ""),
}


def _excluded(name: str) -> bool:
    low = name.lower()
    if any(s in low for s in RULE_EXCLUDE_SUBSTRINGS):
        return True
    if any(s in low for s in TIME_EXCLUDE_SUBSTRINGS):
        return True
    return False


def _profit_factor(pnls: np.ndarray) -> Optional[float]:
    gains = float(pnls[pnls > 0].sum()) if (pnls > 0).any() else 0.0
    losses = float(-pnls[pnls < 0].sum()) if (pnls < 0).any() else 0.0
    if losses <= 1e-12:
        return None if gains <= 0 else 99.0
    return round(gains / losses, 4)


def _parse_predicates(rule: str) -> list[dict[str, Any]]:
    """Parse 'a >= 1 AND b <= 2' (with optional parens) into predicate dicts."""
    text = rule.replace("(", " ").replace(")", " ")
    chunks = [c.strip() for c in text.split(" AND ") if c.strip()]
    out: list[dict[str, Any]] = []
    for chunk in chunks:
        toks = chunk.split()
        if len(toks) < 3:
            continue
        feat, op, thr_s = toks[0], toks[1], toks[2]
        try:
            thr = float(thr_s)
        except ValueError:
            continue
        disp, unit = FEATURE_DISPLAY.get(feat, (feat, ""))
        direction = "高" if op == ">=" else "低/狭"
        if "spread" in feat.lower() and op == "<=":
            direction = "狭"
        if ("vwap" in feat.lower() or "rise" in feat.lower() or "ret" in feat.lower()) and op == "<=":
            direction = "低"
        thr_fmt = f"{thr:.6g}"
        if unit == "%":
            human = f"{disp} {op} {thr_fmt}%"
        elif unit == "bps":
            human = f"{disp} {op} {thr_fmt} bps"
        elif unit == "yen":
            human = f"{disp} {op} {thr_fmt}"
        elif unit == "sec":
            human = f"{disp} {op} {thr_fmt}s"
        else:
            human = f"{disp} {op} {thr_fmt}"
        out.append(
            {
                "feature": feat,
                "display": disp,
                "op": op,
                "threshold": thr,
                "unit": unit,
                "direction_label": direction,
                "human": human,
            }
        )
    return out


def evaluate_kept_metrics(kept: np.ndarray, labeled: Sequence[LabeledTrade]) -> dict[str, Any]:
    k = int(kept.sum())
    if k <= 0:
        return {
            "n_kept": 0,
            "winner_rate": 0.0,
            "n_winner": 0,
            "stop_rate": 0.0,
            "np_rate": 0.0,
            "mean_pnl": None,
            "total_pnl": 0.0,
            "pf": None,
        }
    winners = np.array([r.is_winner for r in labeled])
    stops = np.array([r.cohort == "STOP" for r in labeled])
    nps = np.array([r.cohort == "NoProgress" for r in labeled])
    pnls = np.array([r.pnl_yen for r in labeled], dtype=float)
    sub = pnls[kept]
    n_w = int((kept & winners).sum())
    return {
        "n_kept": k,
        "winner_rate": round(n_w / k, 4),
        "n_winner": n_w,
        "stop_rate": round(float((kept & stops).sum()) / k, 4),
        "np_rate": round(float((kept & nps).sum()) / k, 4),
        "mean_pnl": round(float(sub.mean()), 2),
        "total_pnl": round(float(sub.sum()), 2),
        "pf": _profit_factor(sub),
    }


def _expectancy_score(m: Mapping[str, Any], *, base_wr: float, base_mean: float) -> float:
    """Composite rank: PF + mean_pnl lift + winner_rate (not feature importance)."""
    pf = m.get("pf")
    pf_v = float(pf) if pf is not None else 0.0
    # Cap extreme PF for ranking stability
    pf_term = min(max(pf_v, 0.0), 5.0) / 5.0
    mean_pnl = float(m.get("mean_pnl") or 0.0)
    mean_term = float(np.tanh((mean_pnl - base_mean) / 1500.0))
    wr = float(m.get("winner_rate") or 0.0)
    wr_term = (wr - base_wr) / max(1.0 - base_wr, 1e-6)
    n = int(m.get("n_kept") or 0)
    # mild sample-size preference (avoid n~40 overfitting dominance)
    n_term = float(np.tanh(n / 80.0))
    stop_pen = float(m.get("stop_rate") or 0.0)
    np_pen = float(m.get("np_rate") or 0.0)
    score = 0.40 * pf_term + 0.35 * mean_term + 0.25 * wr_term + 0.10 * n_term - 0.20 * stop_pen - 0.15 * np_pen
    if mean_pnl <= 0:
        score -= 0.35
    if pf_v < 1.0:
        score -= 0.25
    return round(float(score), 6)


def _mask_from_rule(rule: str, label_to_mask: Mapping[str, np.ndarray]) -> Optional[np.ndarray]:
    """Rebuild mask from AND of known single predicates."""
    preds = _parse_predicates(rule)
    if not preds:
        return None
    masks = []
    for p in preds:
        key = f"{p['feature']} {p['op']} {p['threshold']:.6g}"
        # try exact; also scan keys
        if key in label_to_mask:
            masks.append(label_to_mask[key])
            continue
        found = None
        for lab, m in label_to_mask.items():
            toks = lab.split()
            if len(toks) >= 3 and toks[0] == p["feature"] and toks[1] == p["op"]:
                try:
                    if abs(float(toks[2]) - float(p["threshold"])) < 1e-9:
                        found = m
                        break
                except ValueError:
                    continue
        if found is None:
            return None
        masks.append(found)
    out = masks[0].copy()
    for m in masks[1:]:
        out &= m
    return out


def search_winner_rate_combinations(
    labeled: Sequence[LabeledTrade],
    rows: Sequence[Mapping[str, Optional[float]]],
    importance_merged: Sequence[Mapping[str, Any]],
    *,
    top_features: int = 14,
    min_kept: int = 40,
    max_keep_rate: float = 0.70,
    top_n: int = 20,
) -> dict[str, Any]:
    """Search single / AND2 / AND3 rules; return Top20 + expectancy-ranked deploy candidates."""
    base_wr = float(np.mean([1 if r.is_winner else 0 for r in labeled])) if labeled else 0.2
    base_mean = float(np.mean([r.pnl_yen for r in labeled])) if labeled else 0.0
    n = len(labeled)

    forced = [
        "board_imb",
        "f_imb",
        "board_spread",
        "f_spread",
        "f_vwap",
        "px_vwap_dev",
        "f_atr",
        "px_atr",
        "f_tv",
        "f_near_high",
        "f_chase",
        "f_rise5",
        "f_rise10",
        "f_mom",
        "tech_mom",
        "w_60s_imb_chg",
        "f_np_imb_chg_60",
        "w_60s_ret",
        "vol_surge_60s",
    ]
    feats: list[str] = []
    seen: set[str] = set()

    def _add(name: str) -> None:
        if _excluded(name):
            return
        stem = _feat_stem(name)
        if stem in seen:
            return
        col = _col(rows, name)
        if int((~np.isnan(col)).sum()) < 40:
            return
        seen.add(stem)
        feats.append(name)

    for name in forced:
        _add(name)
    for row in importance_merged:
        _add(str(row["feature"]))
        if len(feats) >= top_features:
            break

    preds: list[tuple[str, np.ndarray]] = []
    for name in feats:
        col = _col(rows, name)
        for thr in _threshold_candidates(col):
            for op, fn in ((">=", lambda x, t: x >= t), ("<=", lambda x, t: x <= t)):
                mask = (~np.isnan(col)) & fn(col, thr)
                label = f"{name} {op} {thr:.6g}"
                preds.append((label, mask))

    results: list[dict[str, Any]] = []
    rule_masks: dict[str, np.ndarray] = {}

    def _consider(rule: str, kind: str, kept: np.ndarray) -> None:
        kr = float(kept.sum()) / n if n else 0.0
        if kept.sum() < min_kept or kr > max_keep_rate or kr < 0.03:
            return
        m = evaluate_kept_metrics(kept, labeled)
        lift = round(float(m["winner_rate"]) - base_wr, 4)
        if lift <= 0:
            return
        thresholds = _parse_predicates(rule)
        row = {
            "type": kind,
            "rule": rule,
            "thresholds": thresholds,
            "threshold_text": " AND ".join(t["human"] for t in thresholds),
            "keep_rate": round(kr, 4),
            "lift_vs_base": lift,
            "baseline_winner_rate": round(base_wr, 4),
            **m,
        }
        row["expectancy_score"] = _expectancy_score(row, base_wr=base_wr, base_mean=base_mean)
        results.append(row)
        rule_masks[rule] = kept.copy()

    for label, mask in preds:
        _consider(label, "single", mask)

    label_to_mask = {lab: m for lab, m in preds}

    def _feat_name(rule: str) -> str:
        return rule.replace("(", "").replace(")", "").strip().split()[0]

    def _rule_stems(rule: str) -> set[str]:
        return {_feat_stem(p["feature"]) for p in _parse_predicates(rule)}

    best_by_feat: dict[str, dict[str, Any]] = {}
    for r in results:
        if r["type"] != "single":
            continue
        fname = _feat_name(r["rule"])
        prev = best_by_feat.get(fname)
        if prev is None or (r["winner_rate"], r["n_kept"]) > (prev["winner_rate"], prev["n_kept"]):
            best_by_feat[fname] = r
    single_ranked = sorted(
        [r for r in results if r["type"] == "single"],
        key=lambda r: (-r["winner_rate"], -r["n_kept"]),
    )[:16]
    seed_labels: list[str] = []
    seen_seed: set[str] = set()
    for r in list(best_by_feat.values()) + single_ranked:
        lab = r["rule"]
        if lab in seen_seed or lab not in label_to_mask:
            continue
        seen_seed.add(lab)
        seed_labels.append(lab)
        if len(seed_labels) >= 28:
            break

    for a, b in combinations(seed_labels, 2):
        if _feat_name(a) == _feat_name(b) or (_rule_stems(a) & _rule_stems(b)):
            continue
        kept = label_to_mask[a] & label_to_mask[b]
        _consider(f"({a}) AND ({b})", "AND2", kept)

    top12 = seed_labels[:14]
    for a, b, c in combinations(top12, 3):
        names = {_feat_name(a), _feat_name(b), _feat_name(c)}
        stems = _rule_stems(a) | _rule_stems(b) | _rule_stems(c)
        if len(names) < 3 or len(stems) < 3:
            continue
        kept = label_to_mask[a] & label_to_mask[b] & label_to_mask[c]
        _consider(f"({a}) AND ({b}) AND ({c})", "AND3", kept)

    uniq: dict[str, dict[str, Any]] = {}
    for r in results:
        prev = uniq.get(r["rule"])
        if prev is None or (r["winner_rate"], r["n_kept"]) > (prev["winner_rate"], prev["n_kept"]):
            uniq[r["rule"]] = r

    by_wr = sorted(
        uniq.values(),
        key=lambda r: (-r["winner_rate"], -r["n_kept"], -(r.get("mean_pnl") or -1e18)),
    )
    top20 = []
    for i, r in enumerate(by_wr[:top_n], 1):
        top20.append({"rank_by_winner_rate": i, **r})

    # Expectancy ranking strictly among Winner-rate Top20 (統合順位)
    deploy_pool = [dict(r) for r in top20]
    by_exp = sorted(
        deploy_pool,
        key=lambda r: (
            -float(r.get("expectancy_score") or -1e9),
            -(r.get("pf") or 0),
            -(r.get("mean_pnl") or -1e18),
            -float(r.get("winner_rate") or 0),
        ),
    )
    ranked_expectancy = []
    for i, r in enumerate(by_exp[:20], 1):
        ranked_expectancy.append(
            {
                "rank_by_expectancy": i,
                "adopt_candidate": i <= 5,
                **{k: v for k, v in r.items() if k != "rank_by_winner_rate"},
            }
        )

    # Threshold dictionary used in Top20 (unique feature boundaries)
    thr_dict: dict[str, list[dict[str, Any]]] = {}
    for r in top20:
        for t in r.get("thresholds") or []:
            thr_dict.setdefault(t["feature"], [])
            key = (t["op"], t["threshold"])
            if not any(x["op"] == key[0] and abs(x["threshold"] - key[1]) < 1e-12 for x in thr_dict[t["feature"]]):
                thr_dict[t["feature"]].append(
                    {
                        "display": t["display"],
                        "op": t["op"],
                        "threshold": t["threshold"],
                        "unit": t["unit"],
                        "human": t["human"],
                        "direction_label": t["direction_label"],
                    }
                )

    return {
        "baseline_winner_rate": round(base_wr, 4),
        "baseline_mean_pnl": round(base_mean, 2),
        "top20_by_winner_rate": top20,
        "top20_by_expectancy": ranked_expectancy,
        "threshold_dictionary_from_top20": thr_dict,
        "n_rules_evaluated": len(uniq),
    }
