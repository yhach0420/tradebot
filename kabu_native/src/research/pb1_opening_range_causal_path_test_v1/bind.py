"""Bind frozen V2 face-valid machine. Do not mutate V2. Do not open Confirmation or FV."""
from __future__ import annotations

import json
from typing import Any

from research.causal_path_to_complete_strategy_v1.bind import bind_prior as bind_split
from research.causal_path_to_complete_strategy_v1.blocks import freeze_discovery_blocks
from research.pb1_opening_range_causal_path_test_v1 import (
    EXPECTED_BLOCK_SHA256,
    EXPECTED_DETECTOR_SHA256,
    EXPECTED_SETUP_N,
    EXPECTED_SPLIT_SHA256,
    EXPECTED_STATE_MACHINE_SHA256,
    PARENT_NEXT,
    PARENT_PLAYBOOK_MACHINE_SHA256,
    PARENT_VERDICT,
)
from research.pb1_opening_range_causal_path_test_v1.isolation import FOUNDATION_OUT, PARENT_OUT
from research.pb1_opening_range_continuation_face_valid_v2.definitions import machine_sha256 as v2_machine_sha256
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
    parent_mach = str((parent.get("hashes") or {}).get("PLAYBOOK_MACHINE_SHA256") or "")
    live_mach = v2_machine_sha256()
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
    setup_n = int((parent.get("events_summary") or {}).get("setup_n") or 0)
    ok = (
        bool(base.get("ok"))
        and verd == PARENT_VERDICT
        and nxt == PARENT_NEXT
        and parent_mach == PARENT_PLAYBOOK_MACHINE_SHA256
        and live_mach == PARENT_PLAYBOOK_MACHINE_SHA256
        and setup_n == int(EXPECTED_SETUP_N)
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
        "parent_playbook_machine_sha256": parent_mach,
        "live_v2_machine_sha256": live_mach,
        "parent_setup_n": setup_n,
        "parent_clear_share": ((parent.get("human") or {}).get("clear_share")),
        "parent_range_noise_share": ((parent.get("human") or {}).get("RANGE_NOISE_share")),
        "parent_pattern_face_valid_share": ((parent.get("human") or {}).get("pattern_face_valid_share")),
        "DETECTOR_SHA256": det,
        "STATE_MACHINE_SHA256": mach,
        "reason": None if ok else "split_block_pool_parent_or_frozen_hash_mismatch",
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "v2_mutated": False,
        "false_break_merged": False,
        "complete_strategy_not_run": True,
    }
