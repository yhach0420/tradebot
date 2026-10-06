"""Bind frozen R14 / event generator. Downgrade parent interpretation. Do not mutate parent files."""
from __future__ import annotations

import json
from typing import Any

from research.causal_path_to_complete_strategy_v1.bind import bind_prior as bind_split
from research.causal_path_to_complete_strategy_v1.blocks import freeze_discovery_blocks
from research.native_causal_context_stack_discovery_v1.freeze import event_generator_sha256
from research.r14_trend_incrementality_rca_v1 import (
    CURRENT_JUDGMENT,
    EXPECTED_BLOCK_SHA256,
    EXPECTED_EVENT_GENERATOR_SHA256,
    EXPECTED_SPLIT_SHA256,
    EXPECTED_TREE_SHA256,
    PARENT_PUBLISHED_VERDICT,
    PRIOR5_RANGE_THR,
    PRIOR5_RET_THR,
    R15_THR,
)
from research.r14_trend_incrementality_rca_v1.isolation import FOUNDATION_OUT, PARENT_OUT


def _load_json(path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def bind_prior() -> dict[str, Any]:
    base = bind_split()
    prev = _load_json(PARENT_OUT / "report.json")
    verd = str((prev.get("decision") or {}).get("VERDICT") or "")
    tree = str((prev.get("hashes") or {}).get("TREE_SHA256") or (prev.get("lock") or {}).get("TREE_SHA256") or "")
    egen = str((prev.get("hashes") or {}).get("EVENT_GENERATOR_SHA256") or "")
    live_egen = event_generator_sha256()
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
    hashes_ok = (
        tree == EXPECTED_TREE_SHA256
        and egen == EXPECTED_EVENT_GENERATOR_SHA256
        and live_egen == EXPECTED_EVENT_GENERATOR_SHA256
        and verd == PARENT_PUBLISHED_VERDICT
    )
    ok = (
        bool(base.get("ok"))
        and hashes_ok
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
        "parent_published_verdict": verd,
        "current_judgment": CURRENT_JUDGMENT,
        "judgment_downgraded": True,
        "downgrade_reason": "R14 beats unconditional base but matched same-symbol controls beat R14 on primary p40",
        "TREE_SHA256": tree,
        "EVENT_GENERATOR_SHA256": egen,
        "live_EVENT_GENERATOR_SHA256": live_egen,
        "frozen_r14": {
            "prior5_ret": f"> {PRIOR5_RET_THR}",
            "r15": f"> {R15_THR}",
            "prior5_range_rel": f"> {PRIOR5_RANGE_THR}",
        },
        "threshold_retuned": False,
        "parent_files_not_mutated": True,
        "reason": None if ok else "split_block_pool_or_frozen_hash_mismatch",
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "complete_strategy_not_run": True,
    }
