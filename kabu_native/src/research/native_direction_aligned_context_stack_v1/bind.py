"""Bind closed unsigned R14 and frozen event generator / detector. New architecture."""
from __future__ import annotations

import json
from typing import Any

from research.causal_path_to_complete_strategy_v1.bind import bind_prior as bind_split
from research.causal_path_to_complete_strategy_v1.blocks import freeze_discovery_blocks
from research.native_causal_context_stack_discovery_v1.freeze import event_generator_sha256
from research.native_direction_aligned_context_stack_v1 import (
    EXPECTED_BLOCK_SHA256,
    EXPECTED_DETECTOR_SHA256,
    EXPECTED_EVENT_GENERATOR_SHA256,
    EXPECTED_SPLIT_SHA256,
    EXPECTED_STATE_MACHINE_SHA256,
    PARENT_VERDICT,
)
from research.native_direction_aligned_context_stack_v1.isolation import FOUNDATION_OUT, PARENT_OUT, STACK_OUT
from research.support_resistance_first_interaction_matched_causal_test_v1.freeze import detector_sha256, state_machine_sha256


def _load_json(path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def bind_prior() -> dict[str, Any]:
    base = bind_split()
    rca = _load_json(PARENT_OUT / "report.json")
    verd = str((rca.get("decision") or {}).get("VERDICT") or "")
    egen = event_generator_sha256()
    det = detector_sha256()
    mach = state_machine_sha256()
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
    ok = (
        bool(base.get("ok"))
        and verd == PARENT_VERDICT
        and egen == EXPECTED_EVENT_GENERATOR_SHA256
        and det == EXPECTED_DETECTOR_SHA256
        and mach == EXPECTED_STATE_MACHINE_SHA256
        and split.get("split_sha256") == EXPECTED_SPLIT_SHA256
        and bool(blocks.get("ok"))
        and str(blocks.get("block_sha256") or "") == EXPECTED_BLOCK_SHA256
        and pool_ok
        and disc[:1] == ["20240917"]
        and disc[-1:] == ["20251126"]
        and len(disc) == 291
    )
    return {
        **{k: v for k, v in base.items() if k != "by_symbol"},
        "ok": ok,
        "symbols": list(base.get("symbols") or []),
        "by_symbol": dict(base.get("by_symbol") or {}),
        "blocks": blocks,
        "research_pool_n": pool_n,
        "parent_verdict": verd,
        "EVENT_GENERATOR_SHA256": egen,
        "DETECTOR_SHA256": det,
        "STATE_MACHINE_SHA256": mach,
        "freeze": {
            "EVENT_GENERATOR_SHA256": egen,
            "DETECTOR_SHA256": det,
            "STATE_MACHINE_SHA256": mach,
        },
        "r14_closed": True,
        "t15_unsigned_closed": True,
        "r14_not_repaired": True,
        "r14_thresholds_not_reused": True,
        "reason": None if ok else "split_block_pool_parent_or_frozen_hash_mismatch",
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "d1_status": "DISCOVERY_RULE_LEARNING_ONLY",
        "d2_d4_status": "LOCKED_INTERNAL_REPLICATION",
        "not_validation": True,
        "parent_stack_out": str(STACK_OUT),
    }
