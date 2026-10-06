"""Pin CSB V3 closure. Gate booleans only. Do not tune from CSB PnL."""
from __future__ import annotations

import json
from typing import Any

from research.new_full_strategy_architecture_construction_v2 import (
    CSB_EVAL_ID,
    CSB_REQUIRED_VERDICT,
    PINNED_V3_SHA256,
)
from research.new_full_strategy_architecture_construction_v2.isolation import RESEARCH_ROOT
from research.new_full_strategy_architecture_redesign_v1.spec import dumps_sha256, frozen_strategy as v3_frozen


def csb_closure() -> dict[str, Any]:
    path = RESEARCH_ROOT / "new_full_strategy_implementation_and_dev_eval_v1" / "report.json"
    if not path.is_file():
        return {
            "CSB_CLOSED": False,
            "REASON": "CSB_EVAL_REPORT_ABSENT",
            "CSB_EVAL_ID": CSB_EVAL_ID,
        }
    prev = json.loads(path.read_text(encoding="utf-8"))
    d = dict(prev.get("decision") or prev.get("answers") or {})
    verdict = str(d.get("VERDICT") or (prev.get("decision") or {}).get("VERDICT") or "")
    cov = dict(prev.get("coverage") or {})
    eco = dict(prev.get("economics") or {})
    gt = dict(eco.get("g_table") or {})
    sha = dumps_sha256(v3_frozen())
    g1 = bool(gt.get("G1_TOTAL_PNL"))
    g2 = bool(gt.get("G2_PF"))
    g3 = bool(gt.get("G3_DAY_SIGNS"))
    g4 = bool(gt.get("G4_EX_BEST"))
    g5 = bool(gt.get("G5_PNL_PLUS_MAXDD"))
    g6 = bool(gt.get("G6_CAUSAL_EX_TOP1"))
    coverage_pass = bool(cov.get("COVERAGE_PASS"))
    closed = (
        verdict == CSB_REQUIRED_VERDICT
        and coverage_pass is True
        and (not g1)
        and (not g2)
        and (not g3)
        and (not g4)
        and (not g5)
        and (not g6)
        and sha == PINNED_V3_SHA256
    )
    return {
        "CSB_EVAL_ID": CSB_EVAL_ID,
        "CSB_VERDICT": verdict,
        "CSB_REQUIRED_VERDICT": CSB_REQUIRED_VERDICT,
        "CSB_COVERAGE_PASS": coverage_pass,
        "CSB_G1_PASS": g1,
        "CSB_G2_PASS": g2,
        "CSB_G3_PASS": g3,
        "CSB_G4_PASS": g4,
        "CSB_G5_PASS": g5,
        "CSB_G6_PASS": g6,
        "V3_HASH_UNCHANGED": sha == PINNED_V3_SHA256,
        "PINNED_V3_SHA256": PINNED_V3_SHA256,
        "CSB_CLOSED": bool(closed),
        "CSB_RCA_RUN": False,
        "CSB_RETUNE": False,
        "CSB_PNL_USED_TO_TUNE_V2": False,
    }
