"""Bind parent NO_STABLE_NATIVE_PARTICIPATION_SR_INTERACTION_V1. Discovery only."""
from __future__ import annotations

import json
from typing import Any

from research.causal_path_to_complete_strategy_v1.bind import bind_prior as bind_split
from research.causal_path_to_complete_strategy_v1.blocks import freeze_discovery_blocks
from research.native_causal_context_stack_discovery_v1 import (
    EXPECTED_BLOCK_SHA256,
    EXPECTED_SPLIT_SHA256,
    PARENT_VERDICT,
)
from research.native_causal_context_stack_discovery_v1.freeze import freeze_record
from research.native_causal_context_stack_discovery_v1.isolation import FOUNDATION_OUT, PARENT_OUT


def _load_json(path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def bind_prior() -> dict[str, Any]:
    base = bind_split()
    prev = _load_json(PARENT_OUT / "report.json")
    verd = str((prev.get("decision") or {}).get("VERDICT") or (prev.get("answers") or {}).get("VERDICT?") or "")
    parent_ok = verd == PARENT_VERDICT
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
    fr = freeze_record()
    ok = (
        bool(base.get("ok"))
        and parent_ok
        and split.get("split_sha256") == EXPECTED_SPLIT_SHA256
        and bool(blocks.get("ok"))
        and str(blocks.get("block_sha256") or "") == EXPECTED_BLOCK_SHA256
        and pool_ok
        and disc[:1] == ["20240917"]
        and disc[-1:] == ["20251126"]
        and len(disc) == 291
        and bool(fr.get("detector_hash_match"))
        and bool(fr.get("state_machine_hash_match"))
    )
    return {
        **{k: v for k, v in base.items() if k != "by_symbol"},
        "ok": ok,
        "symbols": list(base.get("symbols") or []),
        "by_symbol": dict(base.get("by_symbol") or {}),
        "blocks": blocks,
        "research_pool_n": pool_n,
        "parent_verdict": verd,
        "freeze": fr,
        "reason": None if ok else "split_block_pool_parent_or_freeze_mismatch",
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "d1_status": "DISCOVERY_RULE_LEARNING_ONLY",
        "d2_d4_status": "LOCKED_INTERNAL_REPLICATION",
        "not_validation": True,
        "i2_not_reopened": True,
        "detector_retuned": False,
    }
