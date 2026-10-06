"""Bind Korea source-blocked parent, frozen FX map, NQ/ES proxy evidence, split, 105-name pool."""
from __future__ import annotations

import json
from typing import Any

from research.hkg_china_a50_driver_response_v1 import (
    EXPECTED_BLOCK_SHA256,
    EXPECTED_HM1_ANCHOR_SHA256,
    EXPECTED_SPLIT_SHA256,
    HM1_ID,
    PARENT_VERDICT,
)
from research.hkg_china_a50_driver_response_v1.isolation import FOUNDATION_OUT, FX_MAP_OUT, KOREA_OUT, NQES_OUT, USDJPY_OUT
from research.sector_symbol_driver_response_mapping_v1.bind import bind_prior as bind_mapping


def _load_fx_map() -> dict[str, Any]:
    for path in (FX_MAP_OUT / "fx_response_map_v1.json", USDJPY_OUT / "fx_response_map_v1.json"):
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _load_json(path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def bind_prior() -> dict[str, Any]:
    base = bind_mapping()
    korea = _load_json(KOREA_OUT / "report.json")
    nqes = _load_json(NQES_OUT / "report.json")
    if not korea:
        return {**{k: v for k, v in base.items() if k != "by_symbol"}, "ok": False, "reason": "korea_report_missing"}
    k_ans = dict(korea.get("answers") or {})
    k_verdict = str(k_ans.get("VERDICT") or (korea.get("decision") or {}).get("VERDICT") or "")
    n_ans = dict(nqes.get("answers") or {})
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
    korea_source_blocked = k_verdict == PARENT_VERDICT
    ok = (
        bool(base.get("ok"))
        and korea_source_blocked
        and str(n_ans.get("VERDICT") or "") == "US_TECH_PROXY_RESPONSE_FOUND_TRUE_FUTURES_PROOF_REQUIRED_V1"
        and str(n_ans.get("NQ_true_futures_or_proxy") or "") == "PROXY_NOT_CME_FUTURES"
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
        "parent_korea_verdict": k_verdict,
        "korea_not_tested": True,
        "korea_not_economic_null": True,
        "korea_did_not_fail_economically": True,
        "do_not_claim_korea_has_no_causal_lead": True,
        "nqes_not_reinterpreted_as_true_cme": True,
        "fx_response_map_v1": fx_map,
        "korea_answers": {
            "VERDICT": k_verdict,
            "purchase": False,
            "KOSPI200_minute_source_found": k_ans.get("KOSPI200_minute_source_found"),
            "stocks_with_at_least_one_usable_external_driver_n": k_ans.get("stocks_with_at_least_one_usable_external_driver_n"),
            "remain_unexplained_n": k_ans.get("remain_unexplained_n"),
        },
        "nqes_answers": {
            "VERDICT": n_ans.get("VERDICT"),
            "NQ_true_futures_or_proxy": n_ans.get("NQ_true_futures_or_proxy"),
            "stocks_with_at_least_one_causally_usable_external_driver_n": n_ans.get(
                "stocks_with_at_least_one_causally_usable_external_driver_n"
            ),
        },
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
        "korea_not_redesigned": True,
        "reason": None if ok else "parent_korea_verdict_fx_map_or_anchor_mismatch",
    }
