"""Bind parent freeze verdict, 105 pool, split/blocks, HM1. Discovery only."""
from __future__ import annotations

import json
from typing import Any

from research.causal_path_to_complete_strategy_v1.bind import bind_prior as bind_split
from research.causal_path_to_complete_strategy_v1.blocks import freeze_discovery_blocks
from research.freeze_group_mechanism_definitions_v1 import (
    EXPECTED_BLOCK_SHA256,
    EXPECTED_HM1_ANCHOR_SHA256,
    EXPECTED_PARENT_SPEC_SHA256,
    EXPECTED_PARENT_TRADE_N,
    EXPECTED_SPLIT_SHA256,
    FROZEN_MECHANISM_ID,
    HM1_ID,
    PARENT_VERDICT,
)
from research.freeze_group_mechanism_definitions_v1.isolation import FOUNDATION_OUT, HIGHER_MAG_OUT, PARENT_OUT


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
        "tuned": False,
        "mean_x0_bps": econ.get("mean_x0_bps"),
        "mean_x1_bps": econ.get("mean_x1_bps"),
        "trade_n": econ.get("trade_n"),
        "day_n": econ.get("day_n"),
        "profit_factor": econ.get("profit_factor"),
        "hit_rate": econ.get("hit_rate"),
        "median_x0_bps": econ.get("median_x0_bps"),
        "max_dd_daily_mean_bps": econ.get("max_dd_daily_mean_bps") or econ.get("max_dd_bps"),
        "block_mean_x0": econ.get("block_mean_x0"),
        "top_symbol_share_of_positive_bps": econ.get("top_symbol_share_of_positive_bps"),
        "not_retuned": True,
        "not_combined_with_bg_cont_vwap": True,
    }


def bind_prior() -> dict[str, Any]:
    base = bind_split()
    hm = _load_json(HIGHER_MAG_OUT / "report.json")
    parent = _load_json(PARENT_OUT / "report.json")
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
    parent_ans = dict(parent.get("answers") or {})
    parent_dec = dict(parent.get("decision") or {})
    frozen = None
    for p in list((parent.get("candidates") or {}).get("proposals") or []):
        if str((p.get("spec") or {}).get("candidate_id") or "") == FROZEN_MECHANISM_ID:
            frozen = p
            break
    spec = dict((frozen or {}).get("spec") or {})
    econ = dict((frozen or {}).get("economics") or {})
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
        and str(parent_ans.get("VERDICT") or parent_dec.get("VERDICT") or "") == PARENT_VERDICT
        and str(spec.get("spec_sha256") or "") == EXPECTED_PARENT_SPEC_SHA256
        and int(econ.get("trade_n") or 0) == EXPECTED_PARENT_TRADE_N
        and bool(hm)
    )
    return {
        **{k: v for k, v in base.items() if k != "by_symbol"},
        "ok": ok,
        "symbols": list(base.get("symbols") or []),
        "by_symbol": dict(base.get("by_symbol") or {}),
        "blocks": blocks,
        "research_pool_n": pool_n,
        "hm1_reference": _hm1_reference(hm),
        "hm1_anchor_sha256": EXPECTED_HM1_ANCHOR_SHA256,
        "parent_verdict": parent_ans.get("VERDICT") or parent_dec.get("VERDICT"),
        "parent_frozen_spec": spec,
        "parent_frozen_econ": {k: econ.get(k) for k in ("trade_n", "day_n", "symbol_n", "mean_x0_bps", "mean_x1_bps", "profit_factor", "hit_rate", "median_x0_bps", "block_mean_x0", "max_dd_daily_mean_bps", "top_symbol_share_of_positive_bps", "exit_reasons", "symbol_n_map")},
        "parent_closed_pairs": list(parent.get("global_vs_group") or []),
        "parent_material_pairs": list(parent_ans.get("material_pairs") or parent.get("material_pairs") or []),
        "kabu_50_applied": False,
        "reason": None if ok else "split_block_pool_or_parent_freeze_mismatch",
    }
