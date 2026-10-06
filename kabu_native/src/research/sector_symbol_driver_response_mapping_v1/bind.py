"""Bind parent source-availability verdict, frozen split, 105-name pool, HM1 freeze. Do not retune HM1."""
from __future__ import annotations

import json
from typing import Any

from research.higher_magnitude_state_discovery_v1.bind import bind_prior as bind_stock_side
from research.sector_symbol_driver_response_mapping_v1 import (
    EXPECTED_BLOCK_SHA256,
    EXPECTED_HM1_ANCHOR_SHA256,
    EXPECTED_SPLIT_SHA256,
    HM1_ID,
    PARENT_VERDICT,
)
from research.sector_symbol_driver_response_mapping_v1.isolation import EXTERNAL_OUT, FOUNDATION_OUT


def bind_prior() -> dict[str, Any]:
    base = bind_stock_side()
    path = EXTERNAL_OUT / "report.json"
    if not path.is_file():
        return {**{k: v for k, v in base.items() if k != "by_symbol"}, "ok": False, "reason": "external_info_report_missing"}
    prev = json.loads(path.read_text(encoding="utf-8"))
    answers = dict(prev.get("answers") or {})
    verdict = str(answers.get("VERDICT") or (prev.get("decision") or {}).get("VERDICT") or "")
    split = dict(base.get("split") or {})
    blocks = dict(base.get("blocks") or {})
    hm1_sha = str((dict((prev.get("external_join_anchor") or {}).get("hm1") or {}).get("anchor_sha256") or answers.get("anchor_sha") or ""))
    pool_ok = True
    pool_path = FOUNDATION_OUT / "research_pool_manifest.json"
    pool_n = len(list(base.get("symbols") or []))
    if pool_path.is_file():
        pool = json.loads(pool_path.read_text(encoding="utf-8"))
        pool_n = int((pool.get("pool") or {}).get("union_n") or pool_n)
        pool_ok = pool_n == 105
    ok = (
        bool(base.get("ok"))
        and verdict == PARENT_VERDICT
        and split.get("split_sha256") == EXPECTED_SPLIT_SHA256
        and str(blocks.get("block_sha256") or "") == EXPECTED_BLOCK_SHA256
        and hm1_sha == EXPECTED_HM1_ANCHOR_SHA256
        and pool_ok
        and pool_n == 105
    )
    return {
        **{k: v for k, v in base.items() if k != "by_symbol"},
        "ok": ok,
        "symbols": list(base.get("symbols") or []),
        "by_symbol": dict(base.get("by_symbol") or {}),
        "parent_external_verdict": verdict,
        "hm1_id": HM1_ID,
        "hm1_anchor_sha256": hm1_sha,
        "hm1_tuned": False,
        "hm1_discarded": False,
        "hm1_defines_universe": False,
        "kabu_50_applied": False,
        "research_pool_n": pool_n,
        "prospective_true_futures_subtrack_retained": True,
        "prospective_subtrack_is_not_sole_strategy_next": True,
        "reason": None if ok else "parent_verdict_or_anchor_mismatch",
    }
