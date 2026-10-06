"""Bind US_TECH_PROXY parent, frozen FX map, NQ/ES evidence, split, 105-name pool."""
from __future__ import annotations

import json
from typing import Any

from research.korea_semiconductor_driver_response_v1 import (
    EXPECTED_BLOCK_SHA256,
    EXPECTED_HM1_ANCHOR_SHA256,
    EXPECTED_SPLIT_SHA256,
    HM1_ID,
    PARENT_VERDICT,
)
from research.korea_semiconductor_driver_response_v1.isolation import FOUNDATION_OUT, FX_MAP_OUT, NQES_OUT, USDJPY_OUT
from research.sector_symbol_driver_response_mapping_v1.bind import bind_prior as bind_mapping


def _load_fx_map() -> dict[str, Any]:
    for path in (FX_MAP_OUT / "fx_response_map_v1.json", USDJPY_OUT / "fx_response_map_v1.json"):
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _load_nqes() -> dict[str, Any]:
    path = NQES_OUT / "report.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def bind_prior() -> dict[str, Any]:
    base = bind_mapping()
    nqes = _load_nqes()
    if not nqes:
        return {**{k: v for k, v in base.items() if k != "by_symbol"}, "ok": False, "reason": "nq_es_report_missing"}
    answers = dict(nqes.get("answers") or {})
    verdict = str(answers.get("VERDICT") or (nqes.get("decision") or {}).get("VERDICT") or "")
    split = dict(base.get("split") or {})
    blocks = dict(base.get("blocks") or {})
    hm1_sha = str(base.get("hm1_anchor_sha256") or "")
    pool_n = len(list(base.get("symbols") or []))
    pool_ok = True
    pool_path = FOUNDATION_OUT / "research_pool_manifest.json"
    if pool_path.is_file():
        pool = json.loads(pool_path.read_text(encoding="utf-8"))
        pool_n = int((pool.get("pool") or {}).get("union_n") or pool_n)
        pool_ok = pool_n == 105
    fx_map = _load_fx_map()
    not_true_cme = str(answers.get("NQ_true_futures_or_proxy") or "") == "PROXY_NOT_CME_FUTURES"
    ok = (
        bool(base.get("ok"))
        and verdict == PARENT_VERDICT
        and not_true_cme
        and split.get("split_sha256") == EXPECTED_SPLIT_SHA256
        and str(blocks.get("block_sha256") or "") == EXPECTED_BLOCK_SHA256
        and hm1_sha == EXPECTED_HM1_ANCHOR_SHA256
        and pool_ok
        and pool_n == 105
        and str(fx_map.get("ANALYSIS_ID") or "") == "FX_RESPONSE_MAP_V1"
        and int(fx_map.get("USABLE_DIRECT_CAUSAL_LEAD_N") or 0) == 3
    )
    return {
        **{k: v for k, v in base.items() if k != "by_symbol"},
        "ok": ok,
        "symbols": list(base.get("symbols") or []),
        "by_symbol": dict(base.get("by_symbol") or {}),
        "parent_nqes_verdict": verdict,
        "nqes_not_reinterpreted_as_true_cme": True,
        "fx_response_map_v1": fx_map,
        "nqes_answers": {
            "VERDICT": verdict,
            "NQ_true_futures_or_proxy": answers.get("NQ_true_futures_or_proxy"),
            "ES_true_futures_or_proxy": answers.get("ES_true_futures_or_proxy"),
            "any_purchase": False,
            "stocks_with_at_least_one_causally_usable_external_driver_n": answers.get(
                "stocks_with_at_least_one_causally_usable_external_driver_n"
            ),
            "remain_unexplained_n": answers.get("remain_unexplained_n"),
            "tech_SIMULTANEOUS_ONLY": answers.get("tech_SIMULTANEOUS_ONLY"),
            "NQ_incremental_after_ES": answers.get("NQ_incremental_after_ES"),
        },
        "nqes_coverage": dict(nqes.get("coverage") or {}),
        "nqes_groups": dict(nqes.get("groups") or {}),
        "nqes_stock_classes": {
            str(r.get("symbol")): {
                "class": r.get("class"),
                "usable_before_stock_move": r.get("usable_before_stock_move"),
                "nq_lead_lag": r.get("nq_lead_lag"),
                "es_lead_lag": r.get("es_lead_lag"),
                "sector": r.get("sector"),
            }
            for r in list(nqes.get("stock_response") or [])
            if r.get("symbol")
        },
        "hm1_id": HM1_ID,
        "hm1_anchor_sha256": hm1_sha,
        "hm1_tuned": False,
        "kabu_50_applied": False,
        "research_pool_n": pool_n,
        "usdjpy_not_redesigned": True,
        "nq_es_not_redesigned": True,
        "prospective_true_futures_subtrack_retained": True,
        "reason": None if ok else "parent_verdict_fx_map_or_anchor_mismatch",
    }
