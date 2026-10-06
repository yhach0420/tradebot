"""Bind CURRENT_DRIVER_SET_INSUFFICIENT_V1, frozen split, 105-name pool. Do not retune HM1. Do not reopen Frozen Validation."""
from __future__ import annotations

import json
from typing import Any

from research.sector_symbol_driver_response_mapping_v1.bind import bind_prior as bind_mapping
from research.usd_jpy_sector_symbol_response_v1 import (
    EXPECTED_BLOCK_SHA256,
    EXPECTED_HM1_ANCHOR_SHA256,
    EXPECTED_SPLIT_SHA256,
    HM1_ID,
    PARENT_VERDICT,
    PRIOR_RESIDUAL_FLAGS,
)
from research.usd_jpy_sector_symbol_response_v1.isolation import FOUNDATION_OUT, MAPPING_OUT


def bind_prior() -> dict[str, Any]:
    base = bind_mapping()
    path = MAPPING_OUT / "report.json"
    if not path.is_file():
        return {**{k: v for k, v in base.items() if k != "by_symbol"}, "ok": False, "reason": "mapping_report_missing"}
    prev = json.loads(path.read_text(encoding="utf-8"))
    answers = dict(prev.get("answers") or {})
    verdict = str(answers.get("VERDICT") or (prev.get("decision") or {}).get("VERDICT") or "")
    split = dict(base.get("split") or {})
    blocks = dict(base.get("blocks") or {})
    hm1_sha = str(base.get("hm1_anchor_sha256") or "")
    pool_ok = True
    pool_n = len(list(base.get("symbols") or []))
    pool_path = FOUNDATION_OUT / "research_pool_manifest.json"
    if pool_path.is_file():
        pool = json.loads(pool_path.read_text(encoding="utf-8"))
        pool_n = int((pool.get("pool") or {}).get("union_n") or pool_n)
        pool_ok = pool_n == 105
    flags = [str(s) for s in list(answers.get("stock_specific_justified_symbols") or PRIOR_RESIDUAL_FLAGS)]
    if not flags:
        flags = list(PRIOR_RESIDUAL_FLAGS)
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
        "parent_mapping_verdict": verdict,
        "parent_stable_sector_driver_n": int(answers.get("stable_sector_driver_n") or 0),
        "parent_usable_lead_n": int(answers.get("usable_lead_n") or 0),
        "prior_residual_flags": flags,
        "prior_residual_flags_are_not_playbooks": True,
        "hm1_id": HM1_ID,
        "hm1_anchor_sha256": hm1_sha,
        "hm1_tuned": False,
        "hm1_discarded": False,
        "kabu_50_applied": False,
        "research_pool_n": pool_n,
        "japan_internal_drivers_not_mined_further": True,
        "prospective_true_futures_subtrack_retained": True,
        "reason": None if ok else "parent_verdict_or_anchor_mismatch",
    }
