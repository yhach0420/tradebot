"""Bind MULTI_TOUCH_ZONE_NO_INCREMENTAL_INFORMATION_V1. Discovery split only. Confirmation and Frozen Validation closed."""
from __future__ import annotations

import json
from typing import Any

from research.causal_path_to_complete_strategy_v1.bind import bind_prior as bind_split
from research.causal_path_to_complete_strategy_v1.blocks import freeze_discovery_blocks
from research.support_resistance_test_design_audit_v1 import (
    EXPECTED_BLOCK_SHA256,
    EXPECTED_SPLIT_SHA256,
    PARENT_VERDICT,
)
from research.support_resistance_test_design_audit_v1.isolation import FOUNDATION_OUT, ZONE_OUT


def _load_json(path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def bind_prior() -> dict[str, Any]:
    base = bind_split()
    prev = _load_json(ZONE_OUT / "report.json")
    ans = dict(prev.get("answers") or {})
    verd = str(ans.get("VERDICT") or (prev.get("decision") or {}).get("VERDICT") or "")
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
    ok = (
        bool(base.get("ok"))
        and parent_ok
        and split.get("split_sha256") == EXPECTED_SPLIT_SHA256
        and bool(blocks.get("ok"))
        and str(blocks.get("block_sha256") or "") == EXPECTED_BLOCK_SHA256
        and pool_ok
        and disc[:1] == ["20240917"]
        and disc[-1:] == ["20251126"]
        and len(disc) == 291
        and ZONE_OUT.is_dir()
        and (ZONE_OUT / "report.json").is_file()
    )
    return {
        **{k: v for k, v in base.items() if k != "by_symbol"},
        "ok": ok,
        "symbols": list(base.get("symbols") or []),
        "by_symbol": dict(base.get("by_symbol") or {}),
        "blocks": blocks,
        "research_pool_n": pool_n,
        "parent_verdict": verd,
        "parent_answers": ans,
        "parent_report": prev,
        "rca_deferred": True,
        "reason": None if ok else "split_block_pool_or_parent_mismatch",
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "zone_artifacts_preserved": bool((ZONE_OUT / "report.json").is_file()),
        "no_pnl_parameter_tuning": True,
    }
