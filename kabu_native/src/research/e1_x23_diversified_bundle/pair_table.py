"""Phase B: unique-mask × EXIT evaluation table (no X22 regeneration of logics)."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.e1_x21_entry_factory_exit_benchmark import FAMILY_BY_FEATURE
from research.e1_x22_actual_exit_factory.evaluate import aggregate_matrix

from . import ACTUAL_EXITS, DISCOVERY, EVALUATION, FAMILY_ORDER, STRESS_DAY


def price_band(px: float) -> str:
    if px < 1000:
        return "LT_1000"
    if px < 5000:
        return "1000_5000"
    if px < 20000:
        return "5000_20000"
    return "GE_20000"


def component_signature(cand: dict[str, Any]) -> tuple[str, str]:
    """Return (logic_depth, component_family_signature)."""
    if cand.get("n_features", 1) == 1:
        fam = cand.get("family") or FAMILY_BY_FEATURE.get(cand.get("feature_name") or "", "OTHER")
        if fam == "COMPOSITE":
            fam = FAMILY_BY_FEATURE.get(cand.get("feature_name") or "", "OTHER")
        return "SINGLE", fam
    fn = cand.get("feature_name") or ""
    feats = [x.strip() for x in fn.split("+") if x.strip()]
    if not feats and cand.get("parents"):
        # fallback: recover feature from parent implementation_id prefix
        feats = []
        for p in cand["parents"]:
            # ENTRY_{FEAT}_{RULE} — rule ends with _REJECT or _SELECT
            body = p[len("ENTRY_"):] if p.startswith("ENTRY_") else p
            for suffix in ("_UPPER_REJECT", "_LOWER_REJECT", "_UPPER_SELECT", "_LOWER_SELECT"):
                if body.endswith(suffix):
                    feats.append(body[: -len(suffix)].lower())
                    break
    fams = [FAMILY_BY_FEATURE.get(f, "OTHER") for f in feats]
    order = {f: i for i, f in enumerate(FAMILY_ORDER)}
    fams_sorted = tuple(sorted(set(fams), key=lambda x: order.get(x, 99)))
    if len(fams_sorted) >= 2:
        sig = "+".join(fams_sorted[:2])
    elif len(fams_sorted) == 1:
        sig = fams_sorted[0]
    else:
        sig = "OTHER"
    return "TWO_FEATURE", sig


def retention_band(retention: float) -> str:
    if retention >= 0.70:
        return "HIGH_RETENTION"
    if retention >= 0.30:
        return "MID_RETENTION"
    if retention >= 0.10:
        return "LOW_RETENTION"
    return "TAIL_SELECT"


def period_tags(period_avgs: dict[str, float | None]) -> dict[str, Any]:
    def tag(name: str, v):
        if v is None:
            return f"{name}_UNKNOWN"
        return f"{name}_POSITIVE" if v > 0 else f"{name}_NEGATIVE"

    disc = period_avgs.get("DISCOVERY")
    ev = period_avgs.get("EVALUATION")
    st = period_avgs.get("STRESS")
    tags = {
        "DISCOVERY": tag("DISCOVERY", disc),
        "EVALUATION": tag("EVALUATION", ev),
        "STRESS": tag("STRESS", st),
    }
    signs = []
    for v in (disc, ev, st):
        if v is None:
            signs.append(None)
        else:
            signs.append(v > 0)
    if all(s is True for s in signs):
        agg = "ALL_PERIOD_POSITIVE"
    elif all(s is False for s in signs if s is not None) and None not in signs:
        agg = "ALL_PERIOD_NEGATIVE"
    elif disc is not None and disc > 0 and ev is not None and ev <= 0:
        agg = "EVALUATION_REVERSED"
    elif (disc is not None and disc > 0 or ev is not None and ev > 0) and st is not None and st <= 0:
        agg = "STRESS_REVERSED"
    elif disc is not None and disc > 0 and (ev is None or ev <= 0) and (st is None or st <= 0):
        agg = "DISCOVERY_ONLY"
    elif disc is not None and ev is not None and ((disc > 0) != (ev > 0)):
        agg = "EVALUATION_REVERSED"
    else:
        agg = "MIXED_PERIOD"
    tags["aggregate"] = agg
    return tags


def enrich_metrics_with_bands(
    mat,
    mask: np.ndarray,
    dates: np.ndarray,
    symbols: np.ndarray,
    prices: np.ndarray,
    bands: np.ndarray,
    base: dict[str, Any],
) -> dict[str, Any]:
    """Add price-band balanced return + contribution shares on top of aggregate_matrix."""
    m = dict(base)
    sel = mask & mat.valid
    idx = np.where(sel)[0]
    if idx.size == 0:
        m["price_band_count"] = 0
        m["price_band_balanced_return_bps"] = None
        m["max_day_contribution_share"] = None
        m["max_symbol_contribution_share"] = None
        m["max_price_band_contribution_share"] = None
        return m
    rets = mat.ret_bps[idx]
    pnls = mat.pnl[idx]
    # price band balanced
    b = bands[idx]
    uniq_b, inv_b = np.unique(b, return_inverse=True)
    b_sum = np.bincount(inv_b, weights=rets)
    b_cnt = np.bincount(inv_b)
    b_means = b_sum / np.maximum(b_cnt, 1)
    m["price_band_count"] = int(uniq_b.size)
    m["price_band_balanced_return_bps"] = float(np.mean(b_means))
    # contribution shares of |pnl|
    total_abs = float(np.sum(np.abs(pnls))) or 1.0
    d = dates[idx]
    uniq_d, inv_d = np.unique(d, return_inverse=True)
    day_abs = np.bincount(inv_d, weights=np.abs(pnls))
    s = symbols[idx]
    uniq_s, inv_s = np.unique(s, return_inverse=True)
    sym_abs = np.bincount(inv_s, weights=np.abs(pnls))
    band_abs = np.bincount(inv_b, weights=np.abs(pnls))
    m["max_day_contribution_share"] = float(np.max(day_abs) / total_abs)
    m["max_symbol_contribution_share"] = float(np.max(sym_abs) / total_abs)
    m["max_price_band_contribution_share"] = float(np.max(band_abs) / total_abs)
    return m


def build_pair_evaluation_table(
    rows: list[dict[str, Any]],
    unique_masks: dict[str, np.ndarray],
    candidates: list[dict[str, Any]],
    alias_rows: list[dict[str, Any]],
    mats: dict[str, Any],
    baseline_masks_metrics: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    dates = np.array([r["date"] for r in rows])
    symbols = np.array([r["symbol"] for r in rows])
    prices = np.array([float(r["CurrentPrice"]) if r.get("CurrentPrice") is not None else np.nan for r in rows])
    bands = np.array([price_band(float(p)) if np.isfinite(p) else "NA" for p in prices])
    pop_n = len(rows)
    cand_by_id = {c["candidate_id"]: c for c in candidates}
    alias_by_id = {a["candidate_id"]: a for a in alias_rows}

    # baselines
    base_mask = np.ones(pop_n, dtype=bool)
    baselines = {}
    for eid in ACTUAL_EXITS:
        base = aggregate_matrix(mats[eid], base_mask, dates, symbols, "ALL")
        baselines[eid] = enrich_metrics_with_bands(
            mats[eid], base_mask, dates, symbols, prices, bands, base
        )

    out = []
    rep_ids = list(unique_masks.keys())
    for ri, rid in enumerate(rep_ids):
        if (ri + 1) % 500 == 0 or ri == 0:
            print(f"  pair table {ri+1}/{len(rep_ids)}", flush=True)
        mask = unique_masks[rid]
        cand = cand_by_id[rid]
        depth, sig = component_signature(cand)
        alias = alias_by_id[rid]
        support = int(mask.sum())
        retention = support / pop_n
        rband = retention_band(retention)

        exit_avgs = {}
        for eid in ACTUAL_EXITS:
            m_all = aggregate_matrix(mats[eid], mask, dates, symbols, "ALL")
            m_all = enrich_metrics_with_bands(mats[eid], mask, dates, symbols, prices, bands, m_all)
            periods = {
                p: aggregate_matrix(mats[eid], mask, dates, symbols, p)
                for p in ("DISCOVERY", "EVALUATION", "STRESS_20260803", "ALL")
            }
            period_avgs = {
                "DISCOVERY": (periods["DISCOVERY"] or {}).get("avg_reference_pnl_yen_100"),
                "EVALUATION": (periods["EVALUATION"] or {}).get("avg_reference_pnl_yen_100"),
                "STRESS": (periods["STRESS_20260803"] or {}).get("avg_reference_pnl_yen_100"),
            }
            ptags = period_tags(period_avgs)
            exit_avgs[eid] = m_all.get("avg_reference_pnl_yen_100")
            b = baselines[eid]
            hard_p = (m_all.get("exit_reason_counts") or {}).get("hard_stop", 0) / max(m_all.get("trades") or 1, 1)
            hard_b = (b.get("exit_reason_counts") or {}).get("hard_stop", 0) / max(b.get("trades") or 1, 1)

            def d(a, bb):
                if a is None or bb is None:
                    return None
                return a - bb

            vs = {
                "avg_yen_delta_vs_same_exit_baseline": d(m_all.get("avg_reference_pnl_yen_100"), b.get("avg_reference_pnl_yen_100")),
                "avg_bps_delta_vs_same_exit_baseline": d(m_all.get("avg_return_bps"), b.get("avg_return_bps")),
                "day_balanced_delta_vs_same_exit_baseline": d(m_all.get("day_balanced_return_bps"), b.get("day_balanced_return_bps")),
                "symbol_balanced_delta_vs_same_exit_baseline": d(m_all.get("symbol_balanced_return_bps"), b.get("symbol_balanced_return_bps")),
                "price_band_balanced_delta_vs_same_exit_baseline": d(
                    m_all.get("price_band_balanced_return_bps"), b.get("price_band_balanced_return_bps")
                ),
                "PF_delta": d(m_all.get("profit_factor_reference"), b.get("profit_factor_reference")),
                "worst_trade_delta": d(m_all.get("worst_trade"), b.get("worst_trade")),
                "max_drawdown_delta": d(m_all.get("max_drawdown_reference_yen_100"), b.get("max_drawdown_reference_yen_100")),
                "hard_stop_rate_delta": d(hard_p, hard_b),
            }
            out.append({
                "candidate_id": rid,
                "alias_representative_id": rid,
                "decision_mask_sha256": alias["decision_mask_sha256"],
                "actual_exit_id": eid,
                "pair_id": f"{rid}×{eid}",
                "logic_depth": depth,
                "component_family_signature": sig,
                "pre_entry_feature_family": cand.get("family"),
                "n_features": cand.get("n_features"),
                "retention": retention,
                "retention_band": rband,
                "period_tags": ptags,
                "metrics": m_all,
                "period": periods,
                "vs_baseline": vs,
                "support": m_all.get("trades"),
                "days": m_all.get("days"),
                "symbols": m_all.get("symbols"),
            })

        # mark EXIT_SENSITIVE on pairs if exit avgs flip sign
        signs = [1 if (v or 0) > 0 else (-1 if (v or 0) < 0 else 0) for v in exit_avgs.values()]
        sensitive = len(set(signs) - {0}) > 1 and min(signs) < 0 < max(signs)
        if sensitive:
            for row in out[-len(ACTUAL_EXITS):]:
                row["exit_sensitive"] = True
                if row["period_tags"]["aggregate"] not in (
                    "ALL_PERIOD_POSITIVE", "EVALUATION_REVERSED", "STRESS_REVERSED"
                ):
                    row["period_tags"]["bundle_tag"] = "EXIT_SENSITIVE"
                else:
                    row["period_tags"]["bundle_tag"] = row["period_tags"]["aggregate"]
        else:
            for row in out[-len(ACTUAL_EXITS):]:
                row["exit_sensitive"] = False
                row["period_tags"]["bundle_tag"] = row["period_tags"]["aggregate"]

    return out, baselines
