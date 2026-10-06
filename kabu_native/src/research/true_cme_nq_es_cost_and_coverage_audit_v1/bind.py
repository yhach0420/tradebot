"""Bind accepted HKG/CHI close, frozen FX map, NQ/ES proxy evidence, Korea source-block, split, 105-name pool."""
from __future__ import annotations

import json
from typing import Any

from research.hkg_china_a50_driver_response_v1.bind import bind_prior as bind_hkg
from research.true_cme_nq_es_cost_and_coverage_audit_v1 import (
    EXPECTED_BLOCK_SHA256,
    EXPECTED_HM1_ANCHOR_SHA256,
    EXPECTED_SPLIT_SHA256,
    HKG_CHI_CLOSE,
    HM1_ID,
    PARENT_VERDICT,
    TECH_FOCUS,
)
from research.true_cme_nq_es_cost_and_coverage_audit_v1.isolation import FOUNDATION_OUT, FX_MAP_OUT, HKG_OUT, KOREA_OUT, NQES_OUT


def _load_json(path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def bind_prior() -> dict[str, Any]:
    base = bind_hkg()
    hkg = _load_json(HKG_OUT / "report.json")
    korea = _load_json(KOREA_OUT / "report.json")
    nqes = _load_json(NQES_OUT / "report.json")
    h_ans = dict(hkg.get("answers") or {})
    k_ans = dict(korea.get("answers") or {})
    n_ans = dict(nqes.get("answers") or {})
    split = dict(base.get("split") or {})
    blocks = dict(base.get("blocks") or {})
    hm1_sha = str(base.get("hm1_anchor_sha256") or "")
    pool_n = int(base.get("research_pool_n") or len(list(base.get("symbols") or [])))
    pool_ok = pool_n == 105
    pool_path = FOUNDATION_OUT / "research_pool_manifest.json"
    if pool_path.is_file():
        pool = json.loads(pool_path.read_text(encoding="utf-8"))
        pool_n = int((pool.get("pool") or {}).get("union_n") or pool_n)
        pool_ok = pool_n == 105
    fx_map = dict(base.get("fx_response_map_v1") or {})
    if not fx_map:
        fx_path = FX_MAP_OUT / "fx_response_map_v1.json"
        fx_map = _load_json(fx_path)
    hkg_verdict = str(h_ans.get("VERDICT") or (hkg.get("decision") or {}).get("VERDICT") or "")
    ok = (
        bool(base.get("ok"))
        and hkg_verdict == PARENT_VERDICT
        and str(k_ans.get("VERDICT") or "") == "KOREA_INTRADAY_SOURCE_NOT_AVAILABLE_V1"
        and str(n_ans.get("VERDICT") or "") == "US_TECH_PROXY_RESPONSE_FOUND_TRUE_FUTURES_PROOF_REQUIRED_V1"
        and str(n_ans.get("NQ_true_futures_or_proxy") or "PROXY_NOT_CME_FUTURES") == "PROXY_NOT_CME_FUTURES"
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
        "parent_hkg_verdict": hkg_verdict,
        "hkg_chi_closed_as": HKG_CHI_CLOSE,
        "do_not_retune_hkg_lag_threshold_clock_subset": True,
        "korea_not_tested": True,
        "korea_not_economic_null": True,
        "do_not_claim_korea_has_no_causal_lead": True,
        "nqes_not_reinterpreted_as_true_cme": True,
        "fx_response_map_v1": fx_map,
        "hkg_answers": {
            "VERDICT": hkg_verdict,
            "purchase": False,
            "stocks_with_at_least_one_usable_external_driver_n": h_ans.get("stocks_with_at_least_one_usable_external_driver_n"),
            "remain_unexplained_n": h_ans.get("remain_unexplained_n"),
            "STRICT_causal_stock_leads_n": h_ans.get("STRICT_causal_stock_leads_n"),
            "TECH_DRIVER_BLOCKER_OPEN": True,
        },
        "korea_answers": {
            "VERDICT": k_ans.get("VERDICT"),
            "purchase": False,
            "KOSPI200_minute_source_found": k_ans.get("KOSPI200_minute_source_found"),
        },
        "nqes_answers": {
            "VERDICT": n_ans.get("VERDICT"),
            "NQ_true_futures_or_proxy": n_ans.get("NQ_true_futures_or_proxy") or "PROXY_NOT_CME_FUTURES",
            "stocks_with_at_least_one_causally_usable_external_driver_n": n_ans.get(
                "stocks_with_at_least_one_causally_usable_external_driver_n"
            ),
        },
        "tech_focus": list(TECH_FOCUS),
        "tech_usable_causal_driver_n": 0,
        "hm1_id": HM1_ID,
        "hm1_anchor_sha256": hm1_sha,
        "hm1_tuned": False,
        "kabu_50_applied": False,
        "research_pool_n": pool_n,
        "usdjpy_not_redesigned": True,
        "nq_es_not_redesigned": True,
        "korea_not_redesigned": True,
        "hkg_not_redesigned": True,
        "reason": None if ok else "parent_hkg_verdict_fx_map_or_anchor_mismatch",
    }
