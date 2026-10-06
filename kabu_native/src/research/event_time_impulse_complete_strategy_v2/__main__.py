"""Run the complete strategy and stop."""
from __future__ import annotations

import json

from research.event_time_impulse_complete_strategy_v2 import (
    ANALYSIS_ID,
    COMPLETE_STRATEGY_ID,
    ENTRY_EXECUTION_ID,
    REQUIRED_SIGNAL_SHA256,
)
from research.event_time_impulse_complete_strategy_v2.decide import decide
from research.event_time_impulse_complete_strategy_v2.identity import bind
from research.event_time_impulse_complete_strategy_v2.publish import publish
from research.event_time_impulse_complete_strategy_v2.rules import activity_lost, exhausted, reconfirm, support_failure
from research.event_time_impulse_complete_strategy_v2.scan import scan
from research.event_time_impulse_complete_strategy_v2.simulate import self_check


def main() -> dict:
    checks = self_check()
    if not all(checks.values()):
        raise RuntimeError(json.dumps(checks))
    if reconfirm(True, True, True, 1.0, True, True) and reconfirm(True, True, True, 1.0, True, False):
        raise RuntimeError("reconfirm_must_require_price_break")
    if support_failure(float("nan"), 1.0) or not support_failure(1.0, 1.0):
        raise RuntimeError("support_rule")
    if not exhausted(True, True, 30.0) or exhausted(True, False, 30.0) or exhausted(True, True, 29.0):
        raise RuntimeError("exhausted_rule")
    if activity_lost(True, False, False):
        raise RuntimeError("activity_rule")
    bound = bind()
    if bound["signal_sha256"] != REQUIRED_SIGNAL_SHA256:
        raise RuntimeError("signal_sha_mismatch")
    scanned = scan()
    decision = decide(scanned)
    frozen = bool(decision["pass"])
    report = {
        "analysis_id": ANALYSIS_ID,
        "verdict": decision["verdict"],
        "next": decision["next"],
        "decision": decision,
        "counts": scanned["counts"],
        "trades": scanned["trades"],
        "manifest": {
            "analysis_id": ANALYSIS_ID,
            "complete_strategy_id": COMPLETE_STRATEGY_ID,
            "self_check": checks,
            "signal_changed": False,
        },
        "data_seal": {
            "new_data_acquired": False,
            "min_capture_date_read": scanned["min_capture_date_read"],
            "max_capture_date_read": scanned["max_capture_date_read"],
            "read_20260911_plus": False,
            "prospective_data_opened": False,
            "prospective_rows_read": 0,
            "fv_opened": False,
            "push_records": scanned["push_records"],
        },
        "parent": {
            "signal_sha256": bound["signal_sha256"],
            "signal_changed": False,
            "full_n": scanned["counts"]["full"],
        },
        "entry": {
            "ENTRY_EXECUTION_ID": ENTRY_EXECUTION_ID,
            "execution_sha256": bound["execution_sha256"],
            "entry_frozen": frozen,
        },
        "thesis": {"fields_frozen_at_entry": True},
        "reconfirm": {"includes_spread": False, "gap_sec": 30},
        "safety": {
            "research_only": True,
            "submit": 0,
            "cancel": 0,
            "live": 0,
            "entry_frozen": frozen,
            "exit_frozen": frozen,
            "complete_strategy_frozen": frozen,
            "exit_sha256": bound["exit_sha256"] if frozen else None,
            "strategy_sha256": bound["strategy_sha256"] if frozen else None,
        },
    }
    publish(report)
    print(json.dumps({"verdict": report["verdict"], "next": report["next"], "trades": decision["economics"]["trade_n"], "pnl": decision["economics"]["pnl"], "failure": decision["primary_failure"]}, default=str), flush=True)
    return report


if __name__ == "__main__":
    main()
