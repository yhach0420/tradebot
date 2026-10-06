"""Market-language short mechanism. No Runtime short. No Full Causal."""
from __future__ import annotations

from typing import Any

from research.post_open_causal_downside_mechanism_discovery_v1 import CASE_A, CASE_B, CASE_C


def interpret(*, decision: dict[str, Any], pop: dict[str, Any], exec_pops: dict[str, Any], uni: list[dict[str, Any]], tree: dict[str, Any]) -> dict[str, Any]:
    case = str(decision.get("CASE") or "")
    s0 = dict(exec_pops.get("S0") or {})
    s2 = dict(exec_pops.get("S2") or {})
    stable = [u["feature"] for u in uni if u.get("mechanism_candidate")]
    if case == "A":
        story = (
            "Post-open 09:10-11:10の中立clockでは、Bid1で売ったあとの10分Cover Askが、"
            "説明可能なdownside stateで平均も中央値もプラスになる。"
            "Current Captureは方向情報を持たないのではなく、この期間はdownsideの方が強い。"
        )
        entry = "Next run: SHORT ENTRY on the qualifying downside state only. Thresholds not frozen here. Not a Runtime short."
        transition = "pre-T causal features enter the qualifying short tree leaf."
        fail = "path-edge turns non-down or Ask1 recrosses above the short entry Bid1/Ask-fill state."
        exit_t = "ENTRY-aligned: first causal Ask1 that invalidates the downside state. Not the 10m label window."
    elif case == "B":
        story = (
            f"Neutral-clock Short X1 population is positive (mean {s0.get('mean')} bps, median {s0.get('median')} bps) "
            "but no stable selectable state / LOBO-confirmed leaf exists. "
            "Downside drift is present but not selectable as a strategy state."
        )
        entry = None
        transition = None
        fail = None
        exit_t = None
    else:
        story = (
            f"Short X1 mean {s0.get('mean')} bps / median {s0.get('median')} bps; "
            f"Short W5 S2 mean {s2.get('mean')} bps. "
            "No stable positive executable short state and no ranking edge that qualifies as a mechanism. "
            "Current Capture bidirectional post-open information limit is confirmed."
        )
        entry = None
        transition = None
        fail = None
        exit_t = None
    return {
        "CASE": case,
        "MARKET_MECHANISM": story,
        "ENTRY_THESIS": entry,
        "STATE_TRANSITION": transition,
        "THESIS_FAILURE": fail,
        "TECHNICAL_EXIT": exit_t,
        "EXACT_CLOSED_ENTRY_REUSE": False,
        "COMPLETE_STRATEGY_PRECOMMITTED": False,
        "RUNTIME_SHORT": False,
        "stable_features": stable,
        "VERDICT_IF_A": CASE_A,
        "VERDICT_IF_B": CASE_B,
        "VERDICT_IF_C": CASE_C,
    }
