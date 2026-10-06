"""Sector-state symbol transmission precommit. Research only. No symbol betas."""
from __future__ import annotations

from typing import Any

from research.am_c0_indicator_exit.isolation import snapshot
from research.causal_driver_pb1.phase1.dates import assert_ingest_date_allowed  # noqa: F401
from research.causal_driver_pb1.sector_state_transmission_precommit.evaluate import evaluate
from research.causal_driver_pb1.sector_state_transmission_precommit.isolation import (
    OUT,
    PARENT_OUT,
    assert_write_root,
    set_research_priority_below_normal,
    write_overlap_n,
)
from research.causal_driver_pb1.sector_state_transmission_precommit.publish import publish


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
        "symbol_outcomes_opened": False,
        "parent_not_overwritten": True,
        "parent_path": str(PARENT_OUT),
    }
    pub = publish(evaluation=evaluation, safety=safety, isolation_pre=pre, isolation_post=post)
    print("VERDICT", evaluation.get("VERDICT"))
    print("NEXT", evaluation.get("NEXT"))
    print("precommit_sha256", evaluation.get("precommit_sha256"))
    print("parent_mechanism_set_sha256", evaluation.get("parent_mechanism_set_sha256"))
    print("family_n", evaluation.get("family_n"), evaluation.get("family_sha256"))
    print("lto", evaluation.get("lto_all_270"), "feasible", evaluation.get("input_feasible_all_270"))
    print("discovery_n", evaluation.get("discovery_date_n"), "fv_n", evaluation.get("fv_input_eligible_n"))
    print("blockers", evaluation.get("blockers"))
    print("OUT", pub.get("out"))
    return {"ok": bool(evaluation.get("ok")), **evaluation, "publish": pub}


if __name__ == "__main__":
    out = main()
    raise SystemExit(0 if out.get("ok") else 1)
