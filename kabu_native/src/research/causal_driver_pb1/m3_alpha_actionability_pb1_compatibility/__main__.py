"""Run the diagnostic and stop. No new complete strategy."""
from __future__ import annotations

import json
from typing import Any

import pandas as pd

from research.causal_driver_pb1 import PROSPECTIVE_FROM
from research.causal_driver_pb1.m3_alpha_actionability_pb1_compatibility import (
    ADAPTER_DISCREPANCY_MIN_N,
    ANALYSIS_ID,
    CASE_ALPHA,
    CASE_BLOCKED,
    CASE_PB1,
    CASE_RCA,
    COST_BPS,
    EXPECTED_FUNNEL,
    FAILED_STRATEGY_SHA256,
    NEXT_BLOCKED,
    NEXT_DESIGN,
    NEXT_RCA,
    NEXT_STOP,
    PRIMARY_HORIZON_MIN,
    SENSITIVITY_HORIZONS,
    SPARSE_MAX_FRACTION,
)
from research.causal_driver_pb1.m3_alpha_actionability_pb1_compatibility.pb1_census import (
    adapter_consistent,
    annotate,
    base_rate,
    clock_report,
    collect_pb1,
    direction_report,
    overlap,
    _count_table,
)
from research.causal_driver_pb1.m3_alpha_actionability_pb1_compatibility.publish import publish
from research.causal_driver_pb1.m3_alpha_actionability_pb1_compatibility.response import TARGETS, observations, sensitivity, summarize
from research.causal_driver_pb1.sector_state_alpha_complete_economic.alpha_state import build_alpha, calendar
from research.causal_driver_pb1.sector_state_alpha_complete_economic.replay import run_replay
from research.causal_driver_pb1.sector_state_alpha_complete_precommit.contract import contract
from research.causal_driver_pb1.sector_state_alpha_shadow.identity import verify as verify_alpha
from research.cause_first_mechanism_discovery_v1.panel import load_minutes


def _opens(minutes: pd.DataFrame) -> dict[tuple[str, str, str], float]:
    out: dict[tuple[str, str, str], float] = {}
    if minutes.empty:
        return out
    for date, symbol, label, px in zip(
        minutes["date"].astype(str),
        minutes["symbol"].astype(str),
        minutes["time_label"].astype(str).str.slice(0, 5),
        minutes["open"],
    ):
        try:
            value = float(px)
        except (TypeError, ValueError):
            continue
        if value == value and value > 0:
            out[(date, symbol, label)] = value
    return out


def _decide(*, actionability_pass: bool, sparse: bool, in_episode_n: int) -> tuple[str, str]:
    if not actionability_pass:
        return CASE_ALPHA, NEXT_STOP
    if sparse:
        return CASE_PB1, NEXT_DESIGN
    if not adapter_consistent(in_episode_n=in_episode_n):
        return CASE_RCA, NEXT_RCA
    return CASE_RCA, NEXT_RCA


def main() -> int:
    frozen = contract()
    if frozen.get("NEW_ALPHA_COMPLETE_STRATEGY_SHA256") != FAILED_STRATEGY_SHA256:
        report = {
            "analysis_id": ANALYSIS_ID,
            "verdict": CASE_BLOCKED,
            "next": NEXT_BLOCKED,
            "funnel_reproduced": False,
            "reason": "strategy_sha_mismatch",
            "prospective_data_opened": False,
            "prospective_rows_read": 0,
        }
        publish(report)
        print(json.dumps({"VERDICT": CASE_BLOCKED, "NEXT": NEXT_BLOCKED}), flush=True)
        return 2
    alpha_id = verify_alpha()
    funnel_run = run_replay(list(alpha_id.get("symbols") or []))
    funnel = {k: funnel_run.get(k) for k in EXPECTED_FUNNEL}
    reproduced = funnel == EXPECTED_FUNNEL and bool(funnel_run.get("ok"))
    if not reproduced:
        report = {
            "analysis_id": ANALYSIS_ID,
            "verdict": CASE_BLOCKED,
            "next": NEXT_BLOCKED,
            "funnel_reproduced": False,
            "funnel": funnel,
            "expected": EXPECTED_FUNNEL,
            "prospective_data_opened": False,
            "prospective_rows_read": 0,
        }
        publish(report)
        print(json.dumps({"VERDICT": CASE_BLOCKED, "NEXT": NEXT_BLOCKED}), flush=True)
        return 2
    dates = calendar()
    alpha = build_alpha(list(alpha_id.get("symbols") or []))
    minutes = load_minutes(symbols=list(TARGETS), allowed_dates=set(dates), forbidden_dates=set())
    if not minutes.empty and minutes["date"].astype(str).ge(PROSPECTIVE_FROM).any():
        raise RuntimeError("prospective_minutes")
    book = _opens(minutes)
    primary_rows = observations(episodes=alpha["episodes"], opens=book, horizon=PRIMARY_HORIZON_MIN)
    actionability = summarize(primary_rows)
    extra = {h: observations(episodes=alpha["episodes"], opens=book, horizon=h) for h in SENSITIVITY_HORIZONS}
    sens = sensitivity(extra)
    events = annotate(collect_pb1(dates), alpha["clocks"])
    joined = overlap(events, alpha["episodes"])
    rates = base_rate(events, eligible_day_n=len(dates))
    directions = direction_report(events)
    clocks = clock_report(events)
    classes = []
    for kind in ("THESIS_READY", "E0", "E1"):
        classes.append({"event_type": kind, **_count_table(events, event_type=kind)})
    in_episode_n = int(joined["episodes_with_any_pb1_confirmation_n"])
    verdict, nxt = _decide(
        actionability_pass=bool(actionability["pass"]),
        sparse=bool(joined["sparse"]),
        in_episode_n=in_episode_n,
    )
    diagnosis = {
        "ALPHA_EDGE": "ESTABLISHED" if actionability["pass"] else "NOT_ESTABLISHED",
        "PB1_COMPATIBILITY": "STRUCTURALLY_SPARSE" if joined["sparse"] else "NOT_SPARSE",
        "ADAPTER_SEMANTICS": "CONSISTENT_WITH_FAILED_FUNNEL" if adapter_consistent(in_episode_n=in_episode_n) else "DISCREPANT",
        "sparse_max_fraction_frozen": SPARSE_MAX_FRACTION,
        "adapter_discrepancy_min_n_frozen": ADAPTER_DISCREPANCY_MIN_N,
        "cost_bps_frozen": COST_BPS,
        "primary_horizon_min_frozen": PRIMARY_HORIZON_MIN,
    }
    if verdict == CASE_ALPHA and joined["sparse"]:
        narrative = (
            "The 3-minute execution-aligned response does not clear the frozen actionability gate. "
            "PB1 confirmation on these five targets is also structurally sparse. "
            "The matrix stops on the missing Alpha edge and does not open a new entry architecture."
        )
    elif verdict == CASE_ALPHA:
        narrative = "The 3-minute execution-aligned response does not clear the frozen actionability gate."
    elif verdict == CASE_PB1:
        narrative = (
            "The frozen 3-minute response clears the diagnostic gate, and PB1 V4 confirmation inside "
            "the same Alpha episode is structurally sparse. That does not authorize trading every Alpha event."
        )
    else:
        narrative = "Actionability cleared, but the PB1 overlap recount does not match the failed funnel's scarcity."
    report: dict[str, Any] = {
        "analysis_id": ANALYSIS_ID,
        "verdict": verdict,
        "next": nxt,
        "failed_complete_strategy_sha256": FAILED_STRATEGY_SHA256,
        "failed_complete_strategy_verdict": "SECTOR_STATE_ALPHA_COMPLETE_STRATEGY_ECONOMIC_FEASIBILITY_FAIL_V1",
        "funnel_reproduced": True,
        "funnel": funnel,
        "actionability": actionability,
        "sensitivity": sens,
        "pb1_classification": classes,
        "pb1_events": events,
        "overlap": joined,
        "base_rate": rates,
        "base_rate_symbols": rates["per_symbol"],
        "direction": directions,
        "clocks": clocks,
        "diagnosis": diagnosis,
        "narrative": narrative,
        "classification": "ALPHA_ACTIONABILITY_DIAGNOSTIC_ONLY",
        "new_complete_strategy_created": False,
        "prospective_data_opened": False,
        "prospective_rows_read": 0,
        "research_only": True,
        "submit": 0,
        "cancel": 0,
        "live": 0,
        "v4_changed": False,
        "v5_created": False,
    }
    publish(report)
    print(json.dumps({"VERDICT": verdict, "NEXT": nxt, "observation_n": actionability["observation_n"]}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
