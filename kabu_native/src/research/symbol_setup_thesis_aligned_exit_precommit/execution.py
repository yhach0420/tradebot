"""Bind the existing first-causal Bid1 scanner. Does not add a second scanner."""
from __future__ import annotations

import hashlib
import inspect

from research.simple_tech_exit_family.v14_harvest import _snap, first_exit_bid
from research.simple_tech_redesign.v28_harvest import _bid_ok, _clock_ok, first_causal_bid, last_session_bid


def _src(*fns) -> str:
    return "\n".join(inspect.getsource(fn) for fn in fns)


def execution_identity() -> dict:
    semantic = _src(_snap, _bid_ok, _clock_ok, first_causal_bid)
    return {
        "EXIT_EXECUTION_ID": "FIRST_CAUSAL_EXECUTABLE_BID",
        "EXIT_EXECUTION_SHA256": hashlib.sha256(semantic.encode("utf-8")).hexdigest(),
        "SOURCE_FILE": "src/research/simple_tech_redesign/v28_harvest.py",
        "FUNCTION": "first_causal_bid",
        "predicate_files": [
            "src/research/simple_tech_exit_family/v14_harvest.py:_snap",
            "src/research/simple_tech_redesign/v28_harvest.py:_bid_ok",
            "src/research/simple_tech_redesign/v28_harvest.py:_clock_ok",
        ],
        "rule": (
            "First board event at or after the decision time and at or before the AM session end "
            "with a positive bid, bid quantity at least 100, freshness at most 5 seconds, "
            "executable, not special, and a quote clock that is not after the event time."
        ),
        "ask_required": False,
        "price": "that bid, with no improvement and no midpoint",
        "earlier_snapshot_reused": False,
        "identity_resolved": True,
        "rejected": [
            {
                "function": "simple_tech_exit_family.v14_harvest.first_exit_bid",
                "why": "It accepts a bid only when execution_eligible is true, and that gate also requires the ask.",
            },
            {
                "function": "simple_full_strategy_discovery_v1.exits.first_causal_bid",
                "why": "It reads a different board layout: continuous, bid_fresh_sec, and CONTINUOUS_STATES.",
            },
            {
                "function": "simple_tech_redesign.branch_u_bb_harvest.first_causal_bid_evidence",
                "why": "It imports the V28 bid and clock predicates and adds evidence fields. It is not a second rule.",
            },
            {
                "function": "small_paper.v1r_live_dual_lane._last_valid_executable_bid",
                "why": "It selects the last bid and can fall back to the fill price. That is not this exit.",
            },
        ],
        "session_close_function": "research.simple_tech_redesign.v28_harvest.last_session_bid",
        "session_close_sha256": hashlib.sha256(inspect.getsource(last_session_bid).encode("utf-8")).hexdigest(),
        "v14_scanner_present": first_exit_bid.__name__ == "first_exit_bid",
    }
