"""Readable rule search with chronological train/test (no same-data peek)."""
from __future__ import annotations

from itertools import combinations
from typing import Any, Mapping, Optional, Sequence

import numpy as np

from research.winner_multiclass.labels import MulticlassRow
from research.winner_multiclass.lanes import lane_of
from research.winner_multiclass.walk_forward import daily_stability, portfolio_metrics


def _col(rows: Sequence[Mapping[str, Optional[float]]], name: str) -> np.ndarray:
    return np.array([np.nan if r.get(name) is None else float(r[name]) for r in rows], dtype=float)


def _qs(col: np.ndarray) -> list[float]:
    v = col[~np.isnan(col)]
    if len(v) < 40:
        return []
    return sorted({float(np.quantile(v, q)) for q in (0.3, 0.4, 0.5, 0.6, 0.7)})


def search_readable_rules_chronological(
    labeled: Sequence[MulticlassRow],
    rows: Sequence[Mapping[str, Optional[float]]],
    feature_names: Sequence[str],
    *,
    top_feats: int = 10,
    min_kept: int = 25,
) -> list[dict[str, Any]]:
    """Fit candidate rules on past days only; evaluate on next day; aggregate OOS."""
    # pick dense lane-A features
    fills = sorted(
        ((sum(1 for r in rows if r.get(f) is not None), f) for f in feature_names if lane_of(f) == "A"),
        reverse=True,
    )
    feats = [f for n, f in fills[:top_feats] if n >= 200]
    days = sorted({r.trade.day for r in labeled})
    # Collect OOS evaluations per rule signature
    from collections import defaultdict

    oos_by_rule: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for d in days[3:]:  # need some train history
        tr_idx = [i for i, r in enumerate(labeled) if r.trade.day < d]
        te_idx = [i for i, r in enumerate(labeled) if r.trade.day == d]
        if len(tr_idx) < 100 or len(te_idx) < 5:
            continue
        tr_rows = [rows[i] for i in tr_idx]
        tr_lab = [labeled[i] for i in tr_idx]
        te_rows = [rows[i] for i in te_idx]
        te_lab = [labeled[i] for i in te_idx]

        # build single predicates on train
        preds: list[tuple[str, np.ndarray, np.ndarray]] = []  # label, train_mask, test_mask
        for name in feats:
            ctr, cte = _col(tr_rows, name), _col(te_rows, name)
            for thr in _qs(ctr):
                for op in (">=", "<="):
                    if op == ">=":
                        mtr = (~np.isnan(ctr)) & (ctr >= thr)
                        mte = (~np.isnan(cte)) & (cte >= thr)
                    else:
                        mtr = (~np.isnan(ctr)) & (ctr <= thr)
                        mte = (~np.isnan(cte)) & (cte <= thr)
                    lab = f"{name} {op} {thr:.6g}"
                    preds.append((lab, mtr, mte))

        # score singles on train by PF/mean among kept
        scored = []
        for lab, mtr, mte in preds:
            if int(mtr.sum()) < min_kept:
                continue
            pm = portfolio_metrics(tr_lab, mtr)
            if (pm.get("PF") or 0) < 1.0 or (pm.get("mean_pnl") or -1) <= 0:
                continue
            if (pm.get("STOP率") or 0) >= 0.25:
                continue
            scored.append((pm.get("PF") or 0, pm.get("winner_rate") or 0, lab, mtr, mte))
        scored.sort(reverse=True)
        seeds = scored[:12]

        # AND2 / AND3 from seeds
        cands = [(lab, mtr, mte) for _, _, lab, mtr, mte in seeds]
        for (a, mtr_a, mte_a), (b, mtr_b, mte_b) in combinations(cands, 2):
            if a.split()[0] == b.split()[0]:
                continue
            cands.append((f"({a}) AND ({b})", mtr_a & mtr_b, mte_a & mte_b))
        for trip in combinations(cands[:8], 3):
            names = {t[0].replace("(", "").split()[0] for t in trip}
            if len(names) < 3:
                continue
            mtr = trip[0][1] & trip[1][1] & trip[2][1]
            mte = trip[0][2] & trip[1][2] & trip[2][2]
            rule = " AND ".join(f"({t[0]})" if " AND " not in t[0] else t[0] for t in trip)
            cands.append((rule, mtr, mte))

        # evaluate each on test
        seen = set()
        for lab, mtr, mte in cands:
            if lab in seen or int(mte.sum()) < 3:
                continue
            seen.add(lab)
            # require train quality
            if int(mtr.sum()) < min_kept:
                continue
            pm_te = portfolio_metrics(te_lab, mte)
            oos_by_rule[lab].append({"day": d, **pm_te})

    # aggregate
    out = []
    for rule, day_rows in oos_by_rule.items():
        if len(day_rows) < 3:
            continue
        pnls = [r["total_pnl_5bps"] for r in day_rows]
        out.append(
            {
                "rule": rule,
                "n_oos_days": len(day_rows),
                "total_pnl_5bps": round(sum(pnls), 2),
                "mean_daily_pnl_5bps": round(float(np.mean(pnls)), 2),
                "median_PF": float(np.median([r["PF"] for r in day_rows if r.get("PF") is not None] or [0])),
                "median_winner_rate": float(np.median([r["winner_rate"] for r in day_rows])),
                "median_STOP": float(np.median([r["STOP率"] for r in day_rows])),
                "median_NP": float(np.median([r["NoProgress率"] for r in day_rows])),
                "mean_trades": round(float(np.mean([r["trades"] for r in day_rows])), 2),
                "pos_days": sum(1 for p in pnls if p > 0),
                "neg_days": sum(1 for p in pnls if p < 0),
            }
        )
    out.sort(key=lambda r: (-(r["total_pnl_5bps"]), -r["median_PF"], r["median_STOP"]))
    return out[:25]
