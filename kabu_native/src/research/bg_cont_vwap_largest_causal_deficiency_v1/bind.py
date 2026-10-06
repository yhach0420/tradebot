"""Bind RCA mixed verdict, frozen BG_CONT_VWAP hash, split/blocks. Discovery only."""
from __future__ import annotations

import json
from typing import Any

from research.causal_path_to_complete_strategy_v1.bind import bind_prior as bind_split
from research.causal_path_to_complete_strategy_v1.blocks import freeze_discovery_blocks
from research.bg_cont_vwap_largest_causal_deficiency_v1 import (
    EXPECTED_BLOCK_SHA256,
    EXPECTED_E0_TRADE_N,
    EXPECTED_MECHANISM_HASH,
    EXPECTED_SPEC_SHA256,
    EXPECTED_SPLIT_SHA256,
    PARENT_VERDICT,
)
from research.bg_cont_vwap_largest_causal_deficiency_v1.isolation import FOUNDATION_OUT, RCA_OUT


def _load_json(path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def bind_prior() -> dict[str, Any]:
    base = bind_split()
    rca = _load_json(RCA_OUT / "report.json")
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
    ans = dict(rca.get("answers") or {})
    hashes = dict(rca.get("mechanism_hashes") or {})
    ok = (
        bool(base.get("ok"))
        and split.get("split_sha256") == EXPECTED_SPLIT_SHA256
        and bool(blocks.get("ok"))
        and str(blocks.get("block_sha256") or "") == EXPECTED_BLOCK_SHA256
        and pool_ok
        and pool_n == 105
        and disc[:1] == ["20240917"]
        and disc[-1:] == ["20251126"]
        and len(disc) == 291
        and str(ans.get("VERDICT") or (rca.get("decision") or {}).get("VERDICT") or "") == PARENT_VERDICT
        and str(hashes.get("mechanism_hash") or "") == EXPECTED_MECHANISM_HASH
        and str(hashes.get("spec_sha256") or "") == EXPECTED_SPEC_SHA256
        and int(ans.get("trade_n") or 0) == EXPECTED_E0_TRADE_N
    )
    return {
        **{k: v for k, v in base.items() if k != "by_symbol"},
        "ok": ok,
        "symbols": list(base.get("symbols") or []),
        "by_symbol": dict(base.get("by_symbol") or {}),
        "blocks": blocks,
        "research_pool_n": pool_n,
        "rca_verdict": ans.get("VERDICT"),
        "rca_root_cause": ans.get("Root_cause"),
        "rca_primary": ans.get("Root_cause_primary"),
        "parent_mechanism_hash": hashes.get("mechanism_hash"),
        "parent_spec_sha256": hashes.get("spec_sha256"),
        "parent_frozen_spec": dict(rca.get("frozen_spec") or {}),
        "reason": None if ok else "split_block_pool_or_rca_parent_mismatch",
    }
