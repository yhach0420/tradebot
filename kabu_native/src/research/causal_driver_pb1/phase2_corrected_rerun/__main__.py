"""Phase 2 USDJPY contract-correct rerun. Research only. Does not overwrite the invalid run."""
from __future__ import annotations

from typing import Any

from research.am_c0_indicator_exit.isolation import snapshot
from research.causal_driver_pb1.phase2_corrected_rerun.evaluate import evaluate
from research.causal_driver_pb1.phase2_corrected_rerun.isolation import (
    CACHE,
    OUT,
    PHASE2_INVALID_OUT,
    assert_write_root,
    set_research_priority_below_normal,
    write_overlap_n,
)
from research.causal_driver_pb1.phase2_corrected_rerun.publish import publish


def main() -> dict[str, Any]:
    set_research_priority_below_normal()
    assert_write_root()
    OUT.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    pre = snapshot(phase="PRE")
    if write_overlap_n(str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or "")) != 0:
        raise SystemExit("write_overlap")
    evaluation = evaluate()
    post = snapshot(phase="POST")
    safety = {
        "research_only": True,
        "ALPHA_CREATED": False,
        "MECHANISM_FROZEN": False,
        "PB1_BOUND": False,
        "COMPLETE_STRATEGY_RUN": False,
        "FROZEN_VALIDATION_ECONOMIC_OPENED": False,
        "PROSPECTIVE_DATA_OPENED": False,
        "V4_CHANGED": bool(evaluation.get("V4_CHANGED")),
        "V5_CREATED": False,
        "submit": 0,
        "cancel": 0,
        "live": 0,
        "write_overlap_n": 0,
        "PHASE2_OUTCOMES_OPENED": bool(evaluation.get("PHASE2_OUTCOMES_OPENED")),
        "C1_opened": bool(evaluation.get("C1_opened")),
        "invalid_run_preserved": (PHASE2_INVALID_OUT / "report.json").is_file(),
        "invalid_out": str(PHASE2_INVALID_OUT).replace("\\", "/"),
        "corrected_out": str(OUT).replace("\\", "/"),
    }
    pub = publish(evaluation=evaluation, safety=safety, isolation_pre=pre, isolation_post=post)
    print("VERDICT", evaluation.get("VERDICT"))
    print("NEXT", evaluation.get("NEXT"))
    print("DEV_candidate_n", evaluation.get("DEV_candidate_n"))
    print("candidate_list_sha256", evaluation.get("candidate_list_sha256"))
    print("C1_opened", evaluation.get("C1_opened"))
    print("C1_rows_read_before_corrected_candidate_freeze", evaluation.get("C1_rows_read_before_corrected_candidate_freeze"))
    print("USDJPY_DRIVER_FAMILY_STATUS", evaluation.get("USDJPY_DRIVER_FAMILY_STATUS"))
    print("leader_laggard_selection_status", evaluation.get("leader_laggard_selection_status"))
    print("OUT", pub.get("out"))
    blocked = evaluation.get("VERDICT") == "USDJPY_STANDALONE_CAUSAL_LEAD_DISCOVERY_BLOCKED_V1"
    return {"ok": not blocked, **evaluation, "publish": pub}


if __name__ == "__main__":
    out = main()
    blocked = out.get("VERDICT") == "USDJPY_STANDALONE_CAUSAL_LEAD_DISCOVERY_BLOCKED_V1"
    raise SystemExit(1 if blocked else 0)
