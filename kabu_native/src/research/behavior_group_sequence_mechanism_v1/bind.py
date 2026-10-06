"""Bind atlas parent, 105-name pool, frozen split/blocks, HM1 freeze. Discovery dates only."""
from __future__ import annotations

import json
from typing import Any

from research.behavior_group_sequence_mechanism_v1 import (
    EXPECTED_BLOCK_SHA256,
    EXPECTED_HM1_ANCHOR_SHA256,
    EXPECTED_SPLIT_SHA256,
    HM1_ID,
    PARENT_VERDICT,
    ZERO_TRADE_CAUSE,
)
from research.behavior_group_sequence_mechanism_v1.isolation import ATLAS_OUT, FOUNDATION_OUT, HIGHER_MAG_OUT
from research.causal_path_to_complete_strategy_v1.bind import bind_prior as bind_split
from research.causal_path_to_complete_strategy_v1.blocks import freeze_discovery_blocks


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
        "role": "FROZEN_NEAR_THRESHOLD_REFERENCE",
        "tuned": False,
        "mean_x0_bps": econ.get("mean_x0_bps"),
        "mean_x1_bps": econ.get("mean_x1_bps"),
        "trade_n": econ.get("trade_n"),
        "day_n": econ.get("day_n"),
        "profit_factor": econ.get("profit_factor"),
        "hit_rate": econ.get("hit_rate"),
        "block_mean_x0": econ.get("block_mean_x0"),
        "parent_hm_verdict": answers.get("VERDICT") or (hm.get("decision") or {}).get("VERDICT"),
        "not_retuned": True,
    }


def _atlas_reference(atlas: dict[str, Any]) -> dict[str, Any]:
    members = dict((atlas.get("behavior_groups") or {}).get("members") or {})
    plays = list((atlas.get("playbooks") or {}).get("proposals") or [])
    lag = None
    for p in plays:
        if str((p.get("spec") or {}).get("candidate_id") or "") == "PB_NATIVE_LAG_CATCHUP":
            lag = p
            break
    econ = dict((lag or {}).get("economics") or {})
    return {
        "verdict": (atlas.get("answers") or {}).get("VERDICT") or (atlas.get("decision") or {}).get("VERDICT"),
        "atlas_id": atlas.get("atlas_id"),
        "members_full_discovery_reference_only": {k: list(v) for k, v in members.items()},
        "pb_native_lag_catchup_trade_n": econ.get("trade_n"),
        "pb_native_lag_catchup_ok": econ.get("ok"),
        "zero_trade_cause": ZERO_TRADE_CAUSE,
        "not_used_as_entry_filter": True,
    }


def bind_prior() -> dict[str, Any]:
    base = bind_split()
    hm = _load_json(HIGHER_MAG_OUT / "report.json")
    atlas = _load_json(ATLAS_OUT / "report.json")
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
    atlas_ref = _atlas_reference(atlas)
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
        and str(atlas_ref.get("verdict") or "") == PARENT_VERDICT
        and bool(hm)
        and atlas_ref.get("pb_native_lag_catchup_trade_n") == 0
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
        "atlas_reference": atlas_ref,
        "catalog_stopped": True,
        "no_new_paid_data": True,
        "kabu_50_applied": False,
        "full_discovery_group_leakage": False,
        "reason": None if ok else "split_block_pool_or_atlas_parent_mismatch",
    }
