"""CAP=5 event-driven portfolios P0–P7 for Entry–Exit Contract study."""
from __future__ import annotations

from typing import Any, Sequence

from research.entry_exit_contract.constants import CAP
from research.price_flow_exit_integrity.portfolio import filter_no_overlap, replay_cap5
from research.price_flow_exit_integrity.trades import SimTrade


def run_portfolios(
    *,
    pbv2_x0: Sequence[SimTrade],
    pbv2_x6: Sequence[SimTrade],
    ec1_m2: Sequence[SimTrade],
    ec2_m2: Sequence[SimTrade],
    ec3_m2: Sequence[SimTrade],
    ec1_m1: Sequence[SimTrade],
    ec2_m1: Sequence[SimTrade],
    ec3_m1: Sequence[SimTrade],
    ec1_m0: Sequence[SimTrade],
    ec2_m0: Sequence[SimTrade],
    ec3_m0: Sequence[SimTrade],
) -> dict[str, Any]:
    defs = {
        "P0": list(pbv2_x0),
        "P1": list(pbv2_x6),
        "P2": list(ec1_m2),
        "P3": list(ec2_m2),
        "P4": list(ec3_m2),
        "P5": list(ec1_m2) + list(ec2_m2) + list(ec3_m2),
        "P6": list(ec1_m1) + list(ec2_m1) + list(ec3_m1),
        "P7": list(ec1_m0) + list(ec2_m0) + list(ec3_m0),
    }
    out = {}
    event_log = []
    blocked = []
    for pid, cands in defs.items():
        # deterministic sort
        cands = sorted(cands, key=lambda t: (t.entry_time, t.entry_method, t.setup_id))
        cands_f, _ = filter_no_overlap(cands)
        res = replay_cap5(cands_f, portfolio_id=pid, cap=CAP)
        out[pid] = res.summary()
        out[pid]["diagnostic_only"] = pid == "P1"
        event_log.extend(res.event_log[:300])
        blocked.extend(res.blocked[:300])
    return {"portfolios": out, "event_log": event_log, "blocked": blocked}
