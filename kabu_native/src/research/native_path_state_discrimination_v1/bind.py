"""Bind closed BG_CONT_VWAP, atlas episodes, split/blocks, 105 pool. Discovery only."""
from __future__ import annotations

import json
from typing import Any

from research.causal_path_to_complete_strategy_v1.bind import bind_prior as bind_split
from research.causal_path_to_complete_strategy_v1.blocks import freeze_discovery_blocks
from research.native_path_state_discrimination_v1 import (
    ATLAS_VERDICT,
    EXPECTED_ATLAS_EPISODE_N,
    EXPECTED_BLOCK_SHA256,
    EXPECTED_SPLIT_SHA256,
    PARENT_VERDICT,
)
from research.native_path_state_discrimination_v1.isolation import ATLAS_OUT, BG_EXIT_OUT, FOUNDATION_OUT


def _load_json(path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def bind_prior() -> dict[str, Any]:
    base = bind_split()
    bg = _load_json(BG_EXIT_OUT / "report.json")
    atlas = _load_json(ATLAS_OUT / "report.json")
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
    bg_ans = dict(bg.get("answers") or {})
    atlas_ans = dict(atlas.get("answers") or {})
    atlas_ep = int((atlas.get("episode_meta") or {}).get("episode_n") or atlas_ans.get("episode_n") or 0)
    bg_ok = str(bg_ans.get("VERDICT") or (bg.get("decision") or {}).get("VERDICT") or "") == PARENT_VERDICT
    atlas_ok = str(atlas_ans.get("VERDICT") or (atlas.get("decision") or {}).get("VERDICT") or "") == ATLAS_VERDICT
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
        and bg_ok
        and atlas_ok
        and atlas_ep == EXPECTED_ATLAS_EPISODE_N
    )
    return {
        **{k: v for k, v in base.items() if k != "by_symbol"},
        "ok": ok,
        "symbols": list(base.get("symbols") or []),
        "by_symbol": dict(base.get("by_symbol") or {}),
        "blocks": blocks,
        "research_pool_n": pool_n,
        "bg_cont_vwap_closed": bg_ok,
        "bg_cont_vwap_verdict": bg_ans.get("VERDICT"),
        "atlas_verdict": atlas_ans.get("VERDICT"),
        "atlas_episode_n": atlas_ep,
        "reason": None if ok else "split_block_pool_bg_close_or_atlas_mismatch",
    }
