"""Final all14 V1R model diagnostic — IN_SAMPLE only, not Historical evidence."""
from __future__ import annotations

from typing import Any

from research.e1_x36_joint_allocator.metrics import summarize_replay
from research.e1_x36_joint_allocator.replay import simulate_joint
from research.e1_x36r_freeze_integrity.serialize import score_fn_from_serialized
from research.e1_x37_prospective.freeze import load_model_artifact


def final_v1r_am_diagnostic(am_panel: list[dict]) -> dict[str, Any]:
    ser = load_model_artifact()
    sfn = score_fn_from_serialized(ser)
    sim = simulate_joint(am_panel, score_fn=sfn)
    sm = summarize_replay(sim)
    return {
        "label": "IN_SAMPLE_OPERATIONAL_DIAGNOSTIC_ONLY",
        "warning": (
            "Final all14 V1R model scored on Historical AM day-fixed panel. "
            "PnL MUST NOT be used as Historical evidence for Bridge gate."
        ),
        "used_as_historical_evidence": False,
        "purpose": [
            "runtime scoring compatibility",
            "feature availability",
            "rank functionality",
            "cap/fill/EXIT plumbing",
        ],
        "metrics": {
            "signals": sm.get("signals"),
            "admitted": sm.get("admitted"),
            "fills": sm.get("fills"),
            "expired": sm.get("expired"),
            "total_pnl_yen": sm.get("total_pnl_yen"),
            "pf": sm.get("pf"),
            "positive_days": sm.get("positive_days"),
            "hard_cap_violations": sm.get("hard_cap_violations"),
            "max_open_plus_pending": sm.get("max_open_plus_pending"),
        },
        "plumbing_ok": (
            int(sm.get("hard_cap_violations") or 0) == 0
            and int(sm.get("max_open_plus_pending") or 0) <= 5
            and int(sm.get("admitted") or 0) > 0
        ),
    }
