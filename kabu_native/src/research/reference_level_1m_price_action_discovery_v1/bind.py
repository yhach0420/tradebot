"""Bind R11 closed as invalid causal strategy. Discovery split only. No Confirmation. No Frozen Validation."""
from __future__ import annotations

import json
from typing import Any

from research.causal_path_to_complete_strategy_v1.bind import bind_prior as bind_split
from research.causal_path_to_complete_strategy_v1.blocks import freeze_discovery_blocks
from research.reference_level_1m_price_action_discovery_v1 import (
    EXPECTED_BLOCK_SHA256,
    EXPECTED_SPLIT_SHA256,
    PARENT_VERDICT,
)
from research.reference_level_1m_price_action_discovery_v1.isolation import FOUNDATION_OUT, R11_AUDIT_OUT


def _load_json(path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def bind_prior() -> dict[str, Any]:
    base = bind_split()
    audit = _load_json(R11_AUDIT_OUT / "report.json")
    ans = dict(audit.get("answers") or {})
    verd = str(ans.get("VERDICT") or (audit.get("decision") or {}).get("VERDICT") or "")
    r11_ok = verd == PARENT_VERDICT
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
        and r11_ok
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
        "r11_audit_verdict": verd,
        "r11_invalid_causal_strategy": r11_ok,
        "r11_not_repaired": True,
        "reason": None if ok else "split_block_pool_or_r11_audit_mismatch",
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
    }
