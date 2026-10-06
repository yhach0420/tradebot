"""Phase 0 entry. Research only. submit/cancel/live 0/0/0."""
from __future__ import annotations

from typing import Any

from research.am_c0_indicator_exit.isolation import snapshot
from research.causal_driver_pb1.audit.publish import publish
from research.causal_driver_pb1.isolation import CACHE, OUT, assert_write_root, set_research_priority_below_normal, write_overlap_n
from research.causal_driver_pb1.phase0.evaluate import evaluate


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
        "V1_VERDICT_CHANGED": False,
        "V4_CHANGED": False,
        "V5_CREATED": False,
        "FROZEN_VALIDATION_ECONOMIC_OPENED": False,
        "PROSPECTIVE_DATA_OPENED": False,
        "submit": 0,
        "cancel": 0,
        "live": 0,
        "orders_submit": 0,
        "orders_cancel": 0,
        "orders_live": 0,
        "write_overlap_n": 0,
    }
    pub = publish(evaluation=evaluation, safety=safety, isolation_pre=pre, isolation_post=post)
    print("VERDICT", evaluation.get("VERDICT"))
    print("NEXT", evaluation.get("NEXT"))
    print("OUT", pub.get("out"))
    return {"ok": bool(evaluation.get("ok")), **evaluation, "publish": pub}


if __name__ == "__main__":
    out = main()
    raise SystemExit(0 if out.get("ok") else 1)
