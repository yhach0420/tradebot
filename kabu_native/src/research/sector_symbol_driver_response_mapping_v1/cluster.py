"""Interpretable response grouping. Hierarchical clustering is diagnostic, not a strategy."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np
from sklearn.cluster import AgglomerativeClustering
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

from research.sector_symbol_driver_response_mapping_v1 import CLUSTER_K, MIN_CLOCK_N, MIN_DAY_N
from research.sector_symbol_driver_response_mapping_v1.panel import DRIVER_IDS
from research.sector_symbol_driver_response_mapping_v1.response import STOCK_DIFF

VEC_DRIVERS = ("ETF_NK_1321", "ETF_TOPIX_1306", "MKT_BREADTH", "MKT_DISPERSION", "MKT_LEADERSHIP")


def _rho(row: dict[str, Any] | None) -> float:
    if not row:
        return 0.0
    v = row.get("rho_after_market")
    if v is None:
        v = row.get("rho_raw")
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return f if f == f else 0.0


def build_stock_cards(*, stock_driver: list[dict[str, Any]], sector_of: dict[str, str], sector_driver: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_sym: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for r in stock_driver:
        by_sym[str(r["symbol"])][str(r["driver"])] = r
    sec_peer: dict[tuple[str, str], list[float]] = defaultdict(list)
    for r in stock_driver:
        v = r.get("rho_after_sector")
        if v is None:
            continue
        sec_peer[(str(sector_of.get(r["symbol"]) or ""), str(r["driver"]))].append(float(v))
    cards = []
    for sym, by_d in sorted(by_sym.items()):
        sec = sector_of.get(sym) or ""
        vec = []
        named = {}
        for did in VEC_DRIVERS:
            val = _rho(by_d.get(did))
            vec.append(val)
            named[did] = val
        named["MKT_MEDIAN_RET_1M"] = _rho(by_d.get("MKT_MEDIAN_RET_1M"))
        vec.append(named["MKT_MEDIAN_RET_1M"])
        ranked = sorted(((abs(v), k, v) for k, v in named.items()), reverse=True)
        primary = ranked[0][1] if ranked else None
        secondary = ranked[1][1] if len(ranked) > 1 else None
        direct_n = sum(1 for d, row in by_d.items() if row.get("direct_beyond_sector"))
        justified = []
        fallback = True
        for did, row in by_d.items():
            if not row.get("direct_beyond_sector"):
                continue
            peers = sec_peer.get((sec, did)) or []
            med = float(np.median(peers)) if peers else 0.0
            resid = row.get("rho_after_sector")
            if resid is None:
                continue
            if abs(float(resid) - med) >= STOCK_DIFF and int(row.get("n") or 0) >= MIN_CLOCK_N and int(row.get("day_n") or 0) >= MIN_DAY_N:
                justified.append(did)
                fallback = False
        weak = all(abs(v) < 0.05 for v in named.values())
        if weak:
            primary = "UNKNOWN_IDIOSYNCRATIC"
            secondary = None
            fallback = True
        cards.append(
            {
                "symbol": sym,
                "sector": sec,
                "vector": vec,
                "named": named,
                "primary_driver": primary,
                "secondary_driver": secondary,
                "direct_driver_count": direct_n,
                "stock_specific_justified_drivers": justified,
                "stock_specific_rule_justified": bool(justified),
                "fallback_to_sector_or_group": fallback and not justified,
                "vector_keys": list(VEC_DRIVERS) + ["MKT_MEDIAN_RET_1M"],
            }
        )
    return cards


def cluster_cards(cards: list[dict[str, Any]]) -> dict[str, Any]:
    if len(cards) < CLUSTER_K:
        return {"ok": False, "k": CLUSTER_K, "clusters": []}
    x = np.asarray([c["vector"] for c in cards], dtype=float)
    x = np.nan_to_num(x, nan=0.0)
    xs = StandardScaler().fit_transform(x)
    model = AgglomerativeClustering(n_clusters=CLUSTER_K, linkage="ward")
    labels = model.fit_predict(xs)
    sil = None
    try:
        if len(set(labels)) > 1:
            sil = float(silhouette_score(xs, labels))
    except Exception:
        sil = None
    for c, lab in zip(cards, labels):
        c["cluster_id"] = f"C{int(lab)+1}"
    groups = []
    for k in range(CLUSTER_K):
        members = [c for c, lab in zip(cards, labels) if int(lab) == k]
        if not members:
            continue
        mean = np.mean(np.asarray([c["vector"] for c in members], dtype=float), axis=0)
        keys = members[0]["vector_keys"]
        loading = {keys[i]: float(mean[i]) for i in range(len(keys))}
        top = max(loading.items(), key=lambda kv: abs(kv[1]))
        sectors = {}
        for c in members:
            sectors[c["sector"]] = sectors.get(c["sector"], 0) + 1
        groups.append(
            {
                "cluster_id": f"C{k+1}",
                "n": len(members),
                "symbols": [c["symbol"] for c in members],
                "mean_loadings": loading,
                "dominant_loading": {"driver": top[0], "value": top[1]},
                "economic_interpretation": _interpret(top[0], top[1], loading),
                "official_sector_counts": sectors,
                "forced_label": False,
            }
        )
    return {
        "ok": True,
        "k_predeclared": CLUSTER_K,
        "k_searched": False,
        "method": "agglomerative_ward_on_standardized_residual_sensitivities",
        "silhouette": sil,
        "black_box_strategy": False,
        "clusters": groups,
    }


def _interpret(driver: str, value: float, loading: dict[str, float]) -> str:
    sign = "positive" if value > 0 else "negative"
    if abs(value) < 0.04 and abs(loading.get("MKT_MEDIAN_RET_1M") or 0) < 0.05:
        return "IDIOSYNCRATIC_OR_WEAK_COMMON_MODE"
    if driver == "MKT_MEDIAN_RET_1M":
        return f"INDEX_BETA_{sign}"
    if driver.startswith("ETF_NK"):
        return f"NK_PROXY_SENSITIVE_{sign}_PROXY_NOT_FUTURES"
    if driver.startswith("ETF_TOPIX"):
        return f"TOPIX_PROXY_SENSITIVE_{sign}_PROXY_NOT_FUTURES"
    if driver == "MKT_BREADTH":
        return f"DOMESTIC_BREADTH_SENSITIVE_{sign}"
    if driver == "MKT_DISPERSION":
        return f"DISPERSION_SENSITIVE_{sign}"
    if driver == "MKT_LEADERSHIP":
        return f"LEADERSHIP_CONCENTRATION_SENSITIVE_{sign}"
    return f"RESPONSE_TO_{driver}_{sign}"


def hm1_overlay(*, cards: list[dict[str, Any]], elec_name: str = "電気機器") -> dict[str, Any]:
    elec = [c for c in cards if c.get("sector") == elec_name]
    clusters = {}
    primaries = {}
    for c in elec:
        clusters[c.get("cluster_id") or "NA"] = clusters.get(c.get("cluster_id") or "NA", 0) + 1
        primaries[c.get("primary_driver") or "NA"] = primaries.get(c.get("primary_driver") or "NA", 0) + 1
    split = len([k for k, n in clusters.items() if n > 0]) > 1 and max(clusters.values(), default=0) < max(1, int(0.80 * len(elec)))
    return {
        "official_sector": elec_name,
        "n": len(elec),
        "symbols": [c["symbol"] for c in elec],
        "cluster_counts": clusters,
        "primary_driver_counts": primaries,
        "shares_one_common_driver": (not split) and len(elec) >= 3,
        "splits_into_multiple_response_groups": bool(split),
        "hm1_pnl_used_to_define_groups": False,
        "hm1_trade_list_not_required_for_sector_split": True,
        "explanation_candidate": (
            "Electrical-machinery names concentrate in HM1 because that official sector is large in the 105-name pool "
            "and the stock-only reclaim setup fires there; driver mapping tests whether they share one residual driver."
        ),
    }
