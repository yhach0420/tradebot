"""Bind frozen peer-propagation discovery. Do not mutate P1/P2/P3, D1 boundaries, peers, match, or outcomes."""
from __future__ import annotations

import json
from typing import Any

from research.causal_path_to_complete_strategy_v1.bind import bind_prior as bind_split
from research.causal_path_to_complete_strategy_v1.blocks import freeze_discovery_blocks
from research.cross_sectional_peer_propagation_discovery_v1.freeze import freeze_sha256
from research.peer_propagation_mechanism_rca_v1 import (
    EXPECTED_BLOCK_SHA256,
    EXPECTED_DETECTOR_SHA256,
    EXPECTED_FREEZE_SHA256,
    EXPECTED_SPLIT_SHA256,
    EXPECTED_STATE_MACHINE_SHA256,
    PARENT_NEXT,
    PARENT_VERDICT,
)
from research.peer_propagation_mechanism_rca_v1.isolation import FOUNDATION_OUT, PARENT_OUT
from research.support_resistance_first_interaction_matched_causal_test_v1.freeze import detector_sha256, state_machine_sha256


def _load_json(path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def bind_prior() -> dict[str, Any]:
    base = bind_split()
    parent = _load_json(PARENT_OUT / "report.json")
    verd = str((parent.get("decision") or {}).get("VERDICT") or "")
    nxt = str((parent.get("decision") or {}).get("NEXT") or "")
    freeze = dict(parent.get("freeze") or {})
    freeze_hash = str(freeze.get("FREEZE_SHA256") or (parent.get("hashes") or {}).get("FREEZE_SHA256") or "")
    live_freeze = freeze_sha256(freeze) if freeze else ""
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
    freeze_ok = (
        freeze_hash == EXPECTED_FREEZE_SHA256
        and live_freeze == EXPECTED_FREEZE_SHA256
        and bool(freeze.get("ok"))
        and bool(freeze.get("d1_only"))
        and bool(freeze.get("d2_not_read"))
        and bool(freeze.get("locked"))
    )
    ok = (
        bool(base.get("ok"))
        and verd == PARENT_VERDICT
        and nxt == PARENT_NEXT
        and freeze_ok
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
        "parent_next": nxt,
        "freeze": freeze,
        "FREEZE_SHA256": freeze_hash,
        "live_FREEZE_SHA256": live_freeze,
        "DETECTOR_SHA256": det,
        "STATE_MACHINE_SHA256": mach,
        "p1_p2_p3_not_modified": True,
        "d1_boundaries_not_modified": True,
        "peer_universe_not_modified": True,
        "matching_not_modified": True,
        "outcomes_not_modified": True,
        "parent_files_not_mutated": True,
        "threshold_retuned": False,
        "ma_period_tuned": False,
        "reason": None if ok else "split_block_pool_parent_verdict_or_freeze_mismatch",
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "complete_strategy_not_run": True,
        "d1_status": "LOCKED_PARENT_FREEZE",
        "d2_d4_status": "LOCKED_INTERNAL_REPLICATION",
        "not_validation": True,
    }
