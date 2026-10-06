"""Bind 105-name pool, frozen split/blocks, HM1 freeze, catalog stop. Discovery dates only for design."""
from __future__ import annotations

import json
from typing import Any

from research.causal_path_to_complete_strategy_v1.bind import bind_prior as bind_split
from research.causal_path_to_complete_strategy_v1.blocks import freeze_discovery_blocks
from research.one_minute_native_playbook_discovery_v1 import (
    EXPECTED_BLOCK_SHA256,
    EXPECTED_HM1_ANCHOR_SHA256,
    EXPECTED_SPLIT_SHA256,
    HM1_ID,
    PARENT_VERDICT,
)
from research.one_minute_native_playbook_discovery_v1.isolation import (
    CME_OUT,
    FOUNDATION_OUT,
    FX_MAP_OUT,
    HIGHER_MAG_OUT,
    HKG_OUT,
    USDJPY_OUT,
)


def _load_json(path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _hm1_reference(hm: dict[str, Any]) -> dict[str, Any]:
    proposals = list((hm.get("candidates") or {}).get("proposals") or hm.get("proposals") or [])
    hit = None
    for p in proposals:
        spec = dict(p.get("spec") or {})
        if str(spec.get("candidate_id") or p.get("candidate_id") or "") == HM1_ID:
            hit = p
            break
    econ = dict((hit or {}).get("discovery_economics") or (hit or {}).get("ranked") or {})
    answers = dict(hm.get("answers") or {})
    return {
        "candidate_id": HM1_ID,
        "role": "ONE_PLAYBOOK_CANDIDATE_NOT_ARCHITECTURE",
        "tuned": False,
        "promoted_n_in_hm_study": answers.get("Promoted n") or (hm.get("candidates") or {}).get("promoted_n") or 0,
        "mean_x0_bps": econ.get("mean_x0_bps"),
        "mean_x1_bps": econ.get("mean_x1_bps"),
        "trade_n": econ.get("trade_n"),
        "day_n": econ.get("day_n"),
        "profit_factor": econ.get("profit_factor"),
        "hit_rate": econ.get("hit_rate"),
        "block_mean_x0": econ.get("block_mean_x0"),
        "parent_hm_verdict": answers.get("VERDICT") or (hm.get("decision") or {}).get("VERDICT"),
        "not_retuned": True,
        "not_5m_grid_in_this_phase": True,
    }


def bind_prior() -> dict[str, Any]:
    base = bind_split()
    hm = _load_json(HIGHER_MAG_OUT / "report.json")
    cme = _load_json(CME_OUT / "report.json")
    hkg = _load_json(HKG_OUT / "report.json")
    fx_map = _load_json(FX_MAP_OUT / "fx_response_map_v1.json")
    if not fx_map:
        fx_map = _load_json(USDJPY_OUT / "fx_response_map_v1.json")
    split = dict(base.get("split") or {})
    disc = list(split.get("discovery_dates") or [])
    blocks = freeze_discovery_blocks(disc) if disc else {"ok": False}
    pool_n = len(list(base.get("symbols") or []))
    pool_ok = pool_n == 105
    pool_path = FOUNDATION_OUT / "research_pool_manifest.json"
    if pool_path.is_file():
        pool = json.loads(pool_path.read_text(encoding="utf-8"))
        pool_n = int((pool.get("pool") or {}).get("union_n") or pool_n)
        pool_ok = pool_n == 105
    cme_ans = dict(cme.get("answers") or {})
    hkg_ans = dict(hkg.get("answers") or {})
    hm1 = _hm1_reference(hm)
    ok = (
        bool(base.get("ok"))
        and split.get("split_sha256") == EXPECTED_SPLIT_SHA256
        and bool(blocks.get("ok"))
        and str(blocks.get("block_sha256") or "") == EXPECTED_BLOCK_SHA256
        and pool_ok
        and pool_n == 105
        and disc[:1] == ["20240917"]
        and disc[-1:] == ["20251126"]
        and len(disc) == 291
        and str(cme_ans.get("VERDICT") or (cme.get("decision") or {}).get("VERDICT") or "") == PARENT_VERDICT
        and bool(hm)
    )
    return {
        **{k: v for k, v in base.items() if k != "by_symbol"},
        "ok": ok,
        "symbols": list(base.get("symbols") or []),
        "by_symbol": dict(base.get("by_symbol") or {}),
        "blocks": blocks,
        "research_pool_n": pool_n,
        "hm1_reference": hm1,
        "hm1_anchor_sha256": EXPECTED_HM1_ANCHOR_SHA256,
        "hm1_tuned": False,
        "catalog_stopped": True,
        "no_new_paid_data": True,
        "cme_verdict": cme_ans.get("VERDICT"),
        "hkg_closed_as": "NO_USABLE_CAUSAL_LEAD",
        "hkg_verdict": hkg_ans.get("VERDICT"),
        "fx_response_map_v1": {
            "ANALYSIS_ID": fx_map.get("ANALYSIS_ID"),
            "USABLE_DIRECT_CAUSAL_LEAD_N": fx_map.get("USABLE_DIRECT_CAUSAL_LEAD_N"),
        },
        "external_optional_only": True,
        "kabu_50_applied": False,
        "reason": None if ok else "split_block_pool_or_catalog_stop_mismatch",
    }
