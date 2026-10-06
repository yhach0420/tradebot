"""Run the frozen complete strategy twice and stop."""
from __future__ import annotations

import json

from research.symbol_setup_baseline_complete_development import (
    ANALYSIS_ID,
    CASE_FAIL,
    CASE_MISMATCH,
    CASE_NONDET,
    CASE_PASS,
    COMPLETE_STRATEGY_ID,
    COMPLETE_STRATEGY_SHA256,
    NEXT_DET,
    NEXT_FAIL,
    NEXT_PASS,
    NEXT_STOP,
)
from research.symbol_setup_baseline_complete_development.metrics import gates, summarize
from research.symbol_setup_baseline_complete_development.publish import publish
from research.symbol_setup_baseline_complete_development.replay import replay
from research.symbol_setup_baseline_complete_development.verify import identity


def _evidence(summary: dict) -> dict:
    funnel = summary["funnel"]
    reasons = {r["exit_reason"]: r for r in summary["reasons"]}
    return {
        "TREND_FALSE": "not selected",
        "LOCATION_FALSE": "not selected",
        "REVERSAL_FALSE": "not selected",
        "VOLUME_FALSE_CONFIRM": "not selected",
        "PRICE_TRIGGER_LATE": "not selected",
        "EXECUTION_FILL_BIAS": {"pending_n": funnel["pending_n"], "fill_n": funnel["fill_n"], "expired_n": funnel["expired_n"]},
        "THESIS_EXIT_TOO_EARLY": {k: reasons[k]["trade_n"] for k in ("FAST_SLOW_CROSS_LOSS", "SLOW_TREND_SLOPE_LOSS", "BOTH_TREND_COMPONENTS_LOST")},
        "THESIS_EXIT_TOO_LATE": {"SESSION_FAIL_CLOSE_n": reasons["SESSION_FAIL_CLOSE"]["trade_n"]},
        "INVALID_DATA": reasons["FAIL_CLOSE_INVALID_DATA"]["trade_n"],
        "CAP_OCCUPANCY": {"CAP_blocked_n": funnel["CAP_blocked_n"], "same_symbol_blocked_n": funnel["same_symbol_blocked_n"]},
        "CONCENTRATION": summary["concentration"],
        "TIMEFRAME_MIXING_CANDIDATE": True,
        "STRUCTURE_LAYER_MISSING_CANDIDATE": True,
        "OTHER": "no repair selected",
    }


def main() -> int:
    before = identity()
    if not before["ok"]:
        publish(
            {
                "analysis_id": ANALYSIS_ID,
                "verdict": CASE_MISMATCH,
                "next": NEXT_STOP,
                "complete_strategy_id": COMPLETE_STRATEGY_ID,
                "complete_strategy_sha256": COMPLETE_STRATEGY_SHA256,
                "identity": {"before": before["checks"], "after": None, "strategy_mutation": None},
                "new_pnl_run": False,
            }
        )
        print(json.dumps({"VERDICT": CASE_MISMATCH, "checks": before["checks"]}), flush=True)
        return 2
    sessions = before["universe_sessions"]
    report_path_dates = before["dates"]
    pre = json.loads(
        (
            __import__("pathlib").Path(__file__).resolve().parents[3]
            / "results/research/symbol_setup_baseline_complete_strategy_precommit_v1/report.json"
        ).read_text(encoding="utf-8")
    )
    first = summarize(
        replay(sessions),
        dates=report_path_dates,
        original=pre["original18"],
        extension=pre["extension17"],
        folds=pre["folds"],
    )
    second = summarize(
        replay(sessions),
        dates=report_path_dates,
        original=pre["original18"],
        extension=pre["extension17"],
        folds=pre["folds"],
    )
    same = first["hashes"] == second["hashes"]
    after = identity()
    if not same:
        verdict, nxt = CASE_NONDET, NEXT_DET
        gate = None
    elif not after["ok"]:
        verdict, nxt = CASE_MISMATCH, NEXT_STOP
        gate = None
    else:
        gate = gates(first)
        verdict, nxt = (CASE_PASS, NEXT_PASS) if gate["all_pass"] else (CASE_FAIL, NEXT_FAIL)
    report = {
        "analysis_id": ANALYSIS_ID,
        "verdict": verdict,
        "next": nxt,
        "complete_strategy_id": COMPLETE_STRATEGY_ID,
        "complete_strategy_sha256": COMPLETE_STRATEGY_SHA256,
        "identity_before_ok": True,
        "identity_after_ok": bool(after["ok"]),
        "strategy_mutation": not bool(after["ok"]),
        "identity": {"before": before["checks"], "after": after["checks"]},
        "determinism": {"match": same, "first": first["hashes"], "second": second["hashes"]},
        "funnel": first["funnel"],
        "primary": first["primary"],
        "reasons": first["reasons"],
        "groups": first["groups"],
        "daily": first["daily"],
        "symbols": first["symbols"],
        "concentration": first["concentration"],
        "reentry": first["reentry"],
        "gates": gate,
        "failure_evidence": None if gate and gate["all_pass"] else _evidence(first),
        "trades": first["trades"],
        "entry_changed": False,
        "exit_changed": False,
        "pb1_required": False,
        "market_context_required": False,
        "sector_context_required": False,
        "prospective_data_opened": False,
        "prospective_rows_read": 0,
        "research_only": True,
        "submit": 0,
        "cancel": 0,
        "live": 0,
    }
    publish(report)
    print(json.dumps({"VERDICT": verdict, "NEXT": nxt, "DET": same, "TRADES": first["funnel"]["fill_n"]}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
