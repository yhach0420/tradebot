"""Bind FACE_VALID_SR_MATCHED_PATH_SEPARATION_FOUND_V1. Discovery only."""
from __future__ import annotations

import json
from typing import Any

from research.causal_path_to_complete_strategy_v1.bind import bind_prior as bind_split
from research.causal_path_to_complete_strategy_v1.blocks import freeze_discovery_blocks
from research.support_resistance_matched_separation_not_a_strategy_v1 import (
    EXPECTED_BLOCK_SHA256,
    EXPECTED_PRIMARY_FIRST_TEST_N,
    EXPECTED_SPLIT_SHA256,
    PARENT_VERDICT,
)
from research.support_resistance_matched_separation_not_a_strategy_v1.freeze import freeze_record
from research.support_resistance_matched_separation_not_a_strategy_v1.isolation import FOUNDATION_OUT, MATCHED_OUT


def _load_json(path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def bind_prior() -> dict[str, Any]:
    base = bind_split()
    prev = _load_json(MATCHED_OUT / "report.json")
    ans = dict(prev.get("answers") or {})
    verd = str(ans.get("VERDICT?") or (prev.get("decision") or {}).get("VERDICT") or "")
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
    ident = dict(prev.get("identity") or {})
    parent_n = int(ident.get("walk_primary_first_test_n") or (prev.get("population_primary") or {}).get("first_test_n") or 0)
    ok = (
        bool(base.get("ok"))
        and parent_ok
        and parent_n == int(EXPECTED_PRIMARY_FIRST_TEST_N)
        and split.get("split_sha256") == EXPECTED_SPLIT_SHA256
        and bool(blocks.get("ok"))
        and str(blocks.get("block_sha256") or "") == EXPECTED_BLOCK_SHA256
        and pool_ok
        and disc[:1] == ["20240917"]
        and disc[-1:] == ["20251126"]
        and len(disc) == 291
        and MATCHED_OUT.is_dir()
        and (MATCHED_OUT / "report.json").is_file()
        and bool(fr.get("detector_hash_match"))
        and bool(fr.get("state_machine_hash_match"))
        and bool(fr.get("confirm_atr_frozen"))
        and bool(fr.get("zone_half_atr_frozen"))
    )
    return {
        **{k: v for k, v in base.items() if k != "by_symbol"},
        "ok": ok,
        "symbols": list(base.get("symbols") or []),
        "by_symbol": dict(base.get("by_symbol") or {}),
        "blocks": blocks,
        "research_pool_n": pool_n,
        "parent_verdict": verd,
        "parent_primary_first_test_n": parent_n,
        "freeze": fr,
        "reason": None if ok else "split_block_pool_parent_or_freeze_mismatch",
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "matched_artifacts_preserved": bool((MATCHED_OUT / "report.json").is_file()),
        "no_pnl_parameter_tuning": True,
        "detector_retuned": False,
        "proven": "face-valid causally-known S/R interactions show matched path separation",
        "not_proven": [
            "executable ENTRY",
            "execution",
            "ENTRY-aligned EXIT",
            "portfolio economics",
            "complete strategy",
        ],
    }
