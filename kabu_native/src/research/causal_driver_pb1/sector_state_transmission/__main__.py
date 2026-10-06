"""Symbol transmission run. Research only."""
from __future__ import annotations

from typing import Any

from research.am_c0_indicator_exit.isolation import snapshot
from research.causal_driver_pb1.phase1.dates import assert_ingest_date_allowed  # noqa: F401
from research.causal_driver_pb1.sector_state_transmission import CASE_BLOCKED
from research.causal_driver_pb1.sector_state_transmission.evaluate import evaluate
from research.causal_driver_pb1.sector_state_transmission.isolation import OUT, PRECOMMIT_V11_OUT, assert_write_root, set_research_priority_below_normal, write_overlap_n
from research.causal_driver_pb1.sector_state_transmission.publish import publish


def main() -> dict[str, Any]:
    set_research_priority_below_normal()
    assert_write_root()
    OUT.mkdir(parents=True, exist_ok=True)
    pre = snapshot(phase="PRE")
    if write_overlap_n(str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or "")) != 0:
        raise SystemExit("write_overlap")
    evaluation = evaluate()
    safety = {
        "research_only": True,
        "PROSPECTIVE_DATA_OPENED": False,
        "FROZEN_VALIDATION_ECONOMIC_OPENED": bool(evaluation.get("FROZEN_VALIDATION_ECONOMIC_OPENED")),
        "ALPHA_CREATED": False,
        "MECHANISM_FROZEN": False,
        "PB1_BOUND": False,
        "COMPLETE_STRATEGY_RUN": False,
        "V4_CHANGED": bool(evaluation.get("V4_CHANGED")),
        "V5_CREATED": False,
        "submit": 0,
        "cancel": 0,
        "live": 0,
        "precommit_preserved": str(PRECOMMIT_V11_OUT),
    }
    pub = publish(evaluation=evaluation, safety=safety)
    print("VERDICT", evaluation.get("VERDICT"))
    print("NEXT", evaluation.get("NEXT"))
    print("candidates", evaluation.get("transmission_candidate_n"), evaluation.get("transmission_candidate_list_sha256"))
    print("fv_confirmed", evaluation.get("FV_confirmed_n"), "opened", evaluation.get("FROZEN_VALIDATION_ECONOMIC_OPENED"))
    print("validated", evaluation.get("validated_parent_mechanisms"))
    print("blockers", evaluation.get("blockers"))
    print("OUT", pub.get("out"))
    return {"ok": evaluation.get("VERDICT") != CASE_BLOCKED, **evaluation, "publish": pub}


if __name__ == "__main__":
    out = main()
    raise SystemExit(0 if out.get("ok") else 1)
