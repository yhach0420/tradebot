"""Bind trigger RCA + structure RCA + frozen V2. Do not mutate parents."""
from __future__ import annotations

import json
from typing import Any

from research.causal_path_to_complete_strategy_v1.bind import bind_prior as bind_split
from research.causal_path_to_complete_strategy_v1.blocks import freeze_discovery_blocks
from research.pb1_opening_range_continuation_face_valid_v2.charts import V1_DEV
from research.pb1_opening_range_continuation_face_valid_v2.definitions import machine_sha256 as v2_machine_sha256
from research.pb1_playbook_redesign_v3 import (
    EXPECTED_BLOCK_SHA256,
    EXPECTED_DETECTOR_SHA256,
    EXPECTED_PARENT_SETUP_N,
    EXPECTED_SPLIT_SHA256,
    EXPECTED_STATE_MACHINE_SHA256,
    PARENT_STRUCTURE_VERDICT,
    PARENT_TRIGGER_VERDICT,
    PARENT_V2_SHA,
)
from research.pb1_playbook_redesign_v3.isolation import FOUNDATION_OUT, STRUCTURE_RCA_OUT, TRIGGER_RCA_OUT, V2_OUT
from research.support_resistance_first_interaction_matched_causal_test_v1.freeze import detector_sha256, state_machine_sha256


def _load_json(path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _keys_from(rows: list[dict[str, Any]]) -> set[tuple[str, str, str]]:
    out: set[tuple[str, str, str]] = set()
    for e in rows:
        if not e:
            continue
        out.add((str(e.get("symbol")), str(e.get("date")), str(e.get("direction"))))
    return out


def bind_prior() -> dict[str, Any]:
    base = bind_split()
    trig = _load_json(TRIGGER_RCA_OUT / "report.json")
    struct = _load_json(STRUCTURE_RCA_OUT / "report.json")
    v2 = _load_json(V2_OUT / "report.json")
    trig_v = str((trig.get("decision") or {}).get("VERDICT") or "")
    struct_v = str((struct.get("decision") or {}).get("VERDICT") or "")
    live_v2 = v2_machine_sha256()
    parent_mach = str((trig.get("hashes") or {}).get("PLAYBOOK_MACHINE_SHA256") or (v2.get("hashes") or {}).get("PLAYBOOK_MACHINE_SHA256") or "")
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
    setup_n = int(trig.get("setup_n") or struct.get("setup_n") or 0)
    ok = (
        bool(base.get("ok"))
        and trig_v == PARENT_TRIGGER_VERDICT
        and struct_v == PARENT_STRUCTURE_VERDICT
        and parent_mach == PARENT_V2_SHA
        and live_v2 == PARENT_V2_SHA
        and setup_n == int(EXPECTED_PARENT_SETUP_N)
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
    v2_keys = _keys_from(list(v2.get("sample") or []))
    rca_keys = _keys_from(list((trig.get("human") or {}).get("rows") or []))
    struct_keys = _keys_from(list((struct.get("human") or {}).get("rows") or []))
    return {
        **{k: v for k, v in base.items() if k != "by_symbol"},
        "ok": ok,
        "symbols": list(base.get("symbols") or []),
        "by_symbol": dict(base.get("by_symbol") or {}),
        "blocks": blocks,
        "research_pool_n": pool_n,
        "parent_trigger_verdict": trig_v,
        "parent_structure_verdict": struct_v,
        "parent_v2_sha": parent_mach,
        "live_v2_machine_sha256": live_v2,
        "parent_setup_n": setup_n,
        "v1_dev_keys": set(V1_DEV),
        "v2_verify_keys": v2_keys,
        "trigger_rca96_keys": rca_keys,
        "structure_rca96_keys": struct_keys,
        "DETECTOR_SHA256": det,
        "STATE_MACHINE_SHA256": mach,
        "reason": None if ok else "split_block_pool_parent_or_frozen_hash_mismatch",
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "v2_mutated": False,
        "eligibility_gap_tv_retuned": False,
    }
