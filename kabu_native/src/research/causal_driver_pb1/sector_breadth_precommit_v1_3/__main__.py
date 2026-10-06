"""Sector breadth/dispersion precommit V1.3. Research only. Does not run corrected OFFSET betas."""
from __future__ import annotations

from typing import Any

from research.causal_driver_pb1.phase1.dates import assert_ingest_date_allowed  # noqa: F401
from research.am_c0_indicator_exit.isolation import snapshot
from research.causal_driver_pb1.sector_breadth_precommit_v1_3.evaluate import evaluate
from research.causal_driver_pb1.sector_breadth_precommit_v1_3.isolation import (
    CACHE,
    DISC_V2_OUT,
    OUT,
    V12_OUT,
    assert_write_root,
    set_research_priority_below_normal,
    write_overlap_n,
)
from research.causal_driver_pb1.sector_breadth_precommit_v1_3.publish import publish


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
        "LEADER_LAGGARD_REOPENED": False,
        "USDJPY_REOPENED": False,
        "DEV_OUTCOMES_PREVIOUSLY_OPENED": True,
        "C1_OUTCOMES_PREVIOUSLY_OPENED": True,
        "fresh_blind_first_look": False,
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
        "corrected_offset_beta_opened": False,
        "v2_out_not_overwritten": True,
        "v2_path": str(DISC_V2_OUT),
        "v1_2_path": str(V12_OUT),
    }
    pub = publish(evaluation=evaluation, safety=safety, isolation_pre=pre, isolation_post=post)
    print("VERDICT", evaluation.get("VERDICT"))
    print("NEXT", evaluation.get("NEXT"))
    print("precommit_sha256", evaluation.get("precommit_sha256"))
    print("parent_precommit_sha256", evaluation.get("parent_precommit_sha256"))
    print("c1_confirmed_set_sha256", evaluation.get("c1_confirmed_set_sha256"))
    print("C1_confirmed_n", evaluation.get("C1_confirmed_n"))
    print("overlap_ok", evaluation.get("overlap_ok"))
    print("common_sample_pass", (evaluation.get("common_sample") or {}).get("pass"))
    print("corrected_offset_beta_opened", evaluation.get("corrected_offset_beta_opened"))
    print("OUT", pub.get("out"))
    print("blockers", evaluation.get("blockers"))
    return {"ok": bool(evaluation.get("ok")), **evaluation, "publish": pub}


if __name__ == "__main__":
    out = main()
    raise SystemExit(0 if out.get("ok") else 1)
