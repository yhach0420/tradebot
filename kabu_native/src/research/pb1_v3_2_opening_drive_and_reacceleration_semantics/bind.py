"""Bind frozen V3.1 + V3 + V2. Do not mutate parents."""
from __future__ import annotations

import json
from typing import Any

from research.causal_path_to_complete_strategy_v1.bind import bind_prior as bind_split
from research.causal_path_to_complete_strategy_v1.blocks import freeze_discovery_blocks
from research.pb1_opening_range_continuation_face_valid_v2.charts import V1_DEV
from research.pb1_opening_range_continuation_face_valid_v2.definitions import machine_sha256 as v2_machine_sha256
from research.pb1_playbook_redesign_v3.definitions import machine_sha256 as v3_machine_sha256
from research.pb1_v3_1_face_validity_fix.definitions import machine_sha256 as v31_machine_sha256
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics import (
    EXPECTED_BLOCK_SHA256,
    EXPECTED_DETECTOR_SHA256,
    EXPECTED_SPLIT_SHA256,
    EXPECTED_STATE_MACHINE_SHA256,
    PARENT_V2_SHA,
    PARENT_V3_SHA,
    PARENT_V31_SETUP_N,
    PARENT_V31_SHA,
    PARENT_V31_VERDICT,
)
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.isolation import (
    FOUNDATION_OUT,
    STRUCTURE_RCA_OUT,
    TRIGGER_RCA_OUT,
    V2_OUT,
    V3_OUT,
    V31_OUT,
)
from research.support_resistance_first_interaction_matched_causal_test_v1.freeze import detector_sha256, state_machine_sha256


def _load_json(path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _keys3(rows: list[dict[str, Any]]) -> set[tuple[str, str, str]]:
    out: set[tuple[str, str, str]] = set()
    for e in rows:
        if not e:
            continue
        out.add((str(e.get("symbol")), str(e.get("date")), str(e.get("direction"))))
    return out


def _dates(rows: list[dict[str, Any]]) -> set[tuple[str, str]]:
    return {(str(e.get("symbol")), str(e.get("date"))) for e in rows if e}


def bind_prior() -> dict[str, Any]:
    base = bind_split()
    v31 = _load_json(V31_OUT / "report.json")
    v3 = _load_json(V3_OUT / "report.json")
    v2 = _load_json(V2_OUT / "report.json")
    trig = _load_json(TRIGGER_RCA_OUT / "report.json")
    struct = _load_json(STRUCTURE_RCA_OUT / "report.json")
    v31_v = str((v31.get("decision") or {}).get("VERDICT") or "")
    live_v2 = v2_machine_sha256()
    live_v3 = v3_machine_sha256()
    live_v31 = v31_machine_sha256()
    v31_sha = str((v31.get("hashes") or {}).get("PLAYBOOK_MACHINE_SHA256") or v31.get("MACHINE_SHA256") or "")
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
        and v31_v == PARENT_V31_VERDICT
        and live_v31 == PARENT_V31_SHA
        and v31_sha == PARENT_V31_SHA
        and live_v3 == PARENT_V3_SHA
        and live_v2 == PARENT_V2_SHA
        and det == EXPECTED_DETECTOR_SHA256
        and mach == EXPECTED_STATE_MACHINE_SHA256
        and split.get("split_sha256") == EXPECTED_SPLIT_SHA256
        and bool(blocks.get("ok"))
        and str(blocks.get("block_sha256") or "") == EXPECTED_BLOCK_SHA256
        and pool_ok
        and disc[:1] == ["20240917"]
        and disc[-1:] == ["20251126"]
        and len(disc) == 291
        and int(v31.get("setup_n") or 0) == int(PARENT_V31_SETUP_N)
        and int(v3.get("setup_n") or 0) == 143
    )
    v3_human = list((v3.get("human") or {}).get("rows") or v3.get("sample") or [])
    return {
        **{k: v for k, v in base.items() if k != "by_symbol"},
        "ok": ok,
        "symbols": list(base.get("symbols") or []),
        "by_symbol": dict(base.get("by_symbol") or {}),
        "blocks": blocks,
        "research_pool_n": pool_n,
        "parent_v31_verdict": v31_v,
        "parent_v31_sha": v31_sha,
        "live_v31_machine_sha256": live_v31,
        "live_v3_machine_sha256": live_v3,
        "live_v2_machine_sha256": live_v2,
        "parent_v31_setup_n": int(v31.get("setup_n") or 0),
        "parent_v3_setup_n": int(v3.get("setup_n") or 0),
        "v1_dev_keys": set(V1_DEV),
        "v2_verify_keys": _keys3(list(v2.get("sample") or [])),
        "trigger_rca96_keys": _keys3(list((trig.get("human") or {}).get("rows") or [])),
        "structure_rca96_keys": _keys3(list((struct.get("human") or {}).get("rows") or [])),
        "v3_face_keys": _keys3(v3_human),
        "v3_face_symbol_dates": _dates(v3_human),
        "v3_human_rows": v3_human,
        "DETECTOR_SHA256": det,
        "STATE_MACHINE_SHA256": mach,
        "reason": None if ok else "split_block_pool_parent_or_frozen_hash_mismatch",
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "v2_mutated": False,
        "v3_mutated": False,
        "v31_mutated": False,
    }
