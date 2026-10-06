"""Bind closed 1-minute SMA playbook. New 5-minute structure. Not a rescue."""
from __future__ import annotations

import json
from typing import Any

from research.causal_path_to_complete_strategy_v1.bind import bind_prior as bind_split
from research.causal_path_to_complete_strategy_v1.blocks import freeze_discovery_blocks
from research.mtf_5min_sma5_25_75_with_1min_trigger_v1 import (
    EXPECTED_BLOCK_SHA256,
    EXPECTED_DETECTOR_SHA256,
    EXPECTED_SPLIT_SHA256,
    EXPECTED_STATE_MACHINE_SHA256,
    ONE_MIN_ARCHITECTURE,
    PARENT_NEXT,
    PARENT_VERDICT,
)
from research.mtf_5min_sma5_25_75_with_1min_trigger_v1.isolation import FOUNDATION_OUT, PARENT_OUT
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
    one_min_closed = verd == PARENT_VERDICT and nxt == PARENT_NEXT
    ok = (
        bool(base.get("ok"))
        and one_min_closed
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
        "one_min_sma_architecture": ONE_MIN_ARCHITECTURE,
        "one_min_sma_architecture_closed": True,
        "sma5_25_75_not_declared_useless": True,
        "peer_propagation_closed": True,
        "peer_rescue": False,
        "ma_period_tuned": False,
        "parent_report": {
            "VERDICT": verd,
            "NEXT": nxt,
            "a_vs_b_10m": ((parent.get("answers") or {}).get("A vs B D2/D3/D4 10m gap?")),
            "structure_adds": ((parent.get("answers") or {}).get("Does 5/25/75 aligned structure add to the same 1m trigger?")),
            "setup_n": ((parent.get("answers") or {}).get("setup_n?")),
            "unconditional_10m": ((parent.get("setups") or {}).get("A") or {}).get("ret_10m"),
        },
        "DETECTOR_SHA256": det,
        "STATE_MACHINE_SHA256": mach,
        "freeze": {"DETECTOR_SHA256": det, "STATE_MACHINE_SHA256": mach},
        "reason": None if ok else "split_block_pool_parent_or_frozen_hash_mismatch",
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "d1_status": "DISCOVERY_SEMANTICS_FREEZE_ONLY",
        "d2_d4_status": "LOCKED_INTERNAL_REPLICATION",
        "not_validation": True,
        "complete_strategy_not_run": True,
    }
