"""Phase 2 precommit V1.1 correction. Research only. Does not run lead discovery."""
from __future__ import annotations

from typing import Any

from research.am_c0_indicator_exit.isolation import snapshot
from research.causal_driver_pb1.phase2_precommit_v1_1.evaluate import evaluate
from research.causal_driver_pb1.phase2_precommit_v1_1.isolation import (
    CACHE,
    OUT,
    assert_write_root,
    set_research_priority_below_normal,
    write_overlap_n,
)
from research.causal_driver_pb1.phase2_precommit_v1_1.publish import publish


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
        "PHASE2_OUTCOMES_OPENED": False,
        "FROZEN_VALIDATION_ECONOMIC_OPENED": False,
        "PROSPECTIVE_DATA_OPENED": False,
        "V4_CHANGED": bool(evaluation.get("V4_CHANGED")),
        "V5_CREATED": False,
        "submit": 0,
        "cancel": 0,
        "live": 0,
        "write_overlap_n": 0,
    }
    pub = publish(evaluation=evaluation, safety=safety, isolation_pre=pre, isolation_post=post)
    print("VERDICT", evaluation.get("VERDICT"))
    print("NEXT", evaluation.get("NEXT"))
    print("precommit_sha256", evaluation.get("precommit_sha256"))
    print("fold_boundary_sha256", evaluation.get("fold_boundary_sha256"))
    print("OUT", pub.get("out"))
    print("blockers", evaluation.get("blockers"))
    return {"ok": bool(evaluation.get("ok")), **evaluation, "publish": pub}


if __name__ == "__main__":
    out = main()
    raise SystemExit(0 if out.get("ok") else 1)
