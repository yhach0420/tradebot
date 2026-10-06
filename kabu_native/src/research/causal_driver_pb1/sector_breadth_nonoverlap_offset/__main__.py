"""Non-overlap offset correction. Research only. Does not reopen discovery or C1 selection."""
from __future__ import annotations

from typing import Any

from research.am_c0_indicator_exit.isolation import snapshot
from research.causal_driver_pb1.phase1.dates import assert_ingest_date_allowed  # noqa: F401
from research.causal_driver_pb1.sector_breadth_nonoverlap_offset.evaluate import evaluate
from research.causal_driver_pb1.sector_breadth_nonoverlap_offset.isolation import (
    DISC_V2_OUT,
    OUT,
    V13_OUT,
    assert_write_root,
    set_research_priority_below_normal,
    write_overlap_n,
)
from research.causal_driver_pb1.sector_breadth_nonoverlap_offset.publish import publish


def main() -> dict[str, Any]:
    set_research_priority_below_normal()
    assert_write_root()
    OUT.mkdir(parents=True, exist_ok=True)
    pre = snapshot(phase="PRE")
    if write_overlap_n(str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or "")) != 0:
        raise SystemExit("write_overlap")
    evaluation = evaluate()
    post = snapshot(phase="POST")
    safety = {
        "research_only": True,
        "DEV_OUTCOMES_PREVIOUSLY_OPENED": True,
        "C1_OUTCOMES_PREVIOUSLY_OPENED": True,
        "fresh_blind_first_look": False,
        "new_driver_acquisition_started": False,
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
        "v13_not_overwritten": True,
        "v2_not_overwritten": True,
        "v13_path": str(V13_OUT),
        "v2_path": str(DISC_V2_OUT),
    }
    pub = publish(evaluation=evaluation, safety=safety, isolation_pre=pre, isolation_post=post)
    print("VERDICT", evaluation.get("VERDICT"))
    print("NEXT", evaluation.get("NEXT"))
    print("reason", evaluation.get("reason"))
    print("offset_pass_n", evaluation.get("offset_pass_n"))
    print("final_pass_n", evaluation.get("final_pass_n"))
    print("blockers", evaluation.get("blockers"))
    print("OUT", pub.get("out"))
    return {"ok": bool(evaluation.get("ok")), **evaluation, "publish": pub}


if __name__ == "__main__":
    out = main()
    raise SystemExit(0 if out.get("ok") else 1)
