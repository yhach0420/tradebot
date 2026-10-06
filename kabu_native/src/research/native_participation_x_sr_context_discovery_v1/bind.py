"""Bind split/pool/detector. Prior S/R complete strategies stay closed. Discovery only."""
from __future__ import annotations

import json
from typing import Any

from research.causal_path_to_complete_strategy_v1.bind import bind_prior as bind_split
from research.causal_path_to_complete_strategy_v1.blocks import freeze_discovery_blocks
from research.native_participation_x_sr_context_discovery_v1 import (
    EXPECTED_BLOCK_SHA256,
    EXPECTED_SPLIT_SHA256,
)
from research.native_participation_x_sr_context_discovery_v1.freeze import freeze_record
from research.native_participation_x_sr_context_discovery_v1.isolation import COMPLETE_OUT, FOUNDATION_OUT


def _load_json(path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def bind_prior() -> dict[str, Any]:
    base = bind_split()
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
    prev = _load_json(COMPLETE_OUT / "report.json")
    parent_closed = str((prev.get("decision") or {}).get("VERDICT") or "") == "SR_PATH_SEPARATION_NOT_COMPLETE_STRATEGY_V1"
    ok = (
        bool(base.get("ok"))
        and split.get("split_sha256") == EXPECTED_SPLIT_SHA256
        and bool(blocks.get("ok"))
        and str(blocks.get("block_sha256") or "") == EXPECTED_BLOCK_SHA256
        and pool_ok
        and disc[:1] == ["20240917"]
        and disc[-1:] == ["20251126"]
        and len(disc) == 291
        and bool(fr.get("detector_hash_match"))
        and bool(fr.get("state_machine_hash_match"))
        and parent_closed
    )
    return {
        **{k: v for k, v in base.items() if k != "by_symbol"},
        "ok": ok,
        "symbols": list(base.get("symbols") or []),
        "by_symbol": dict(base.get("by_symbol") or {}),
        "blocks": blocks,
        "research_pool_n": pool_n,
        "freeze": fr,
        "prior_sr_complete_closed": parent_closed,
        "reason": None if ok else "split_block_pool_detector_or_parent_closed_mismatch",
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "a2_reopened_as_strategy": False,
        "c1_reopened": False,
        "d1_d4_status": "ALL_DEVELOPMENT",
        "not_validation": True,
        "detector_retuned": False,
    }
