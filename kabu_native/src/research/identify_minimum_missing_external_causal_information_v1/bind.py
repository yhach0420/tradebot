"""Bind parent magnitude-limit verdict and frozen split. Do not reopen HM1 design."""
from __future__ import annotations

import json
from typing import Any

from research.higher_magnitude_state_discovery_v1.bind import bind_prior as bind_stock_side
from research.identify_minimum_missing_external_causal_information_v1 import (
    EXPECTED_SPLIT_SHA256,
    HM1_ID,
    PARENT_VERDICT,
)
from research.identify_minimum_missing_external_causal_information_v1.isolation import HIGHER_MAG_OUT


def bind_prior() -> dict[str, Any]:
    base = bind_stock_side()
    path = HIGHER_MAG_OUT / "report.json"
    if not path.is_file():
        return {**{k: v for k, v in base.items() if k != "by_symbol"}, "ok": False, "reason": "higher_magnitude_report_missing"}
    prev = json.loads(path.read_text(encoding="utf-8"))
    answers = dict(prev.get("answers") or {})
    verdict = str(answers.get("VERDICT") or (prev.get("decision") or {}).get("VERDICT") or "")
    promoted_n = int(answers.get("complete_strategy_candidates_n") or 0)
    split = dict(base.get("split") or {})
    ok = (
        bool(base.get("ok"))
        and verdict == PARENT_VERDICT
        and promoted_n == 0
        and split.get("split_sha256") == EXPECTED_SPLIT_SHA256
    )
    return {
        **{k: v for k, v in base.items() if k != "by_symbol"},
        "ok": ok,
        "symbols": list(base.get("symbols") or []),
        "by_symbol": dict(base.get("by_symbol") or {}),
        "higher_magnitude_verdict": verdict,
        "hm1_promoted": False,
        "hm1_id": HM1_ID,
        "hm1_tuned": False,
        "hm1_discarded": False,
        "interpretation": "CURRENT_STOCK_PANEL_DIRECTIONAL_EDGE_DOES_NOT_CLEAR_EXECUTION_SCALE",
        "not_zero_value": True,
        "reason": None if ok else "higher_magnitude_verdict_mismatch",
    }
