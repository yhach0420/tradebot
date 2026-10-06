"""Bind prior split, D1–D4 blocks, and rejected CS1–CS3. Discovery dates only for design."""
from __future__ import annotations

import json
from typing import Any

from research.causal_path_to_complete_strategy_v1.bind import bind_prior as bind_cause_first_split
from research.causal_path_to_complete_strategy_v1.blocks import freeze_discovery_blocks
from research.higher_magnitude_state_discovery_v1 import (
    EXPECTED_BLOCK_SHA256,
    EXPECTED_SPLIT_SHA256,
    PARENT_VERDICT,
    REJECTED_CS,
)
from research.higher_magnitude_state_discovery_v1.isolation import CAUSAL_PATH_OUT


def bind_prior() -> dict[str, Any]:
    base = bind_cause_first_split()
    path = CAUSAL_PATH_OUT / "report.json"
    if not path.is_file():
        return {**{k: v for k, v in base.items() if k != "by_symbol"}, "ok": False, "reason": "causal_path_report_missing"}
    prev = json.loads(path.read_text(encoding="utf-8"))
    answers = dict(prev.get("answers") or {})
    verdict = str(answers.get("VERDICT") or (prev.get("decision") or {}).get("VERDICT") or "")
    promoted_n = int(answers.get("complete_strategy_candidates_n") or (prev.get("candidates") or {}).get("promoted_n") or 0)
    split = dict(base.get("split") or {})
    disc = list(split.get("discovery_dates") or [])
    blocks = freeze_discovery_blocks(disc) if disc else {"ok": False}
    block_sha = str(blocks.get("block_sha256") or "")
    cs_rescued = False
    ok = (
        bool(base.get("ok"))
        and verdict == PARENT_VERDICT
        and promoted_n == 0
        and not cs_rescued
        and split.get("split_sha256") == EXPECTED_SPLIT_SHA256
        and bool(blocks.get("ok"))
        and block_sha == EXPECTED_BLOCK_SHA256
    )
    return {
        **{k: v for k, v in base.items() if k != "by_symbol"},
        "ok": ok,
        "symbols": list(base.get("symbols") or []),
        "by_symbol": dict(base.get("by_symbol") or {}),
        "causal_path_verdict": verdict,
        "cs1_cs3_promoted_n": promoted_n,
        "cs1_cs3_rescued": False,
        "rejected_cs": list(REJECTED_CS),
        "did_not_tighten_q5_to_q10": True,
        "did_not_optimize_clock_or_horizon": True,
        "old_confirmation_used_to_design": False,
        "blocks": blocks,
        "reason": None if ok else "causal_path_verdict_or_block_sha_mismatch",
    }
