"""Run the five-second acceptance entry test and stop."""
from __future__ import annotations

import json

from research.event_time_breakout_acceptance_entry import (
    ANALYSIS_ID,
    ENTRY_EXECUTION_ID,
    FAILED_EXECUTION_ID,
    FAILED_EXECUTION_VERDICT,
    PARENT_ANALYSIS_ID,
    PARENT_FULL_N,
    PARENT_VERDICT,
    REQUIRED_SIGNAL_SHA256,
)
from research.event_time_breakout_acceptance_entry.acceptance import self_check
from research.event_time_breakout_acceptance_entry.contract import contract_ids
from research.event_time_breakout_acceptance_entry.decide import decide
from research.event_time_breakout_acceptance_entry.publish import publish
from research.event_time_breakout_acceptance_entry.scan import scan


def main() -> int:
    checked = self_check()
    ids = contract_ids()
    if not all(checked.values()) or not ids["signal_sha_matches_required"]:
        print(json.dumps({"VERDICT": "FAIL_CLOSED", "SELF_CHECK": checked, "SHA": ids["parent_signal_sha256"]}), flush=True)
        return 2
    scanned = scan()
    decision = decide(scanned)
    report = {
        "analysis_id": ANALYSIS_ID,
        "verdict": decision["verdict"],
        "next": decision["next"],
        "manifest": {
            "analysis_id": ANALYSIS_ID,
            "verdict": decision["verdict"],
            "next": decision["next"],
            "BASE_SIGNAL_SHA256": REQUIRED_SIGNAL_SHA256,
            "ACCEPTANCE_SHA256": decision["identities"]["ACCEPTANCE_SHA256"],
            "EXECUTION_SHA256": decision["identities"]["EXECUTION_SHA256"],
            "ENTRY_ID": decision["ENTRY_ID"],
            "ENTRY_SHA256": decision["identities"]["ENTRY_SHA256"] if decision["ENTRY_FROZEN"] else None,
            "BASE_SIGNAL_CHANGED": False,
            "DURATION_SEARCHED": False,
        },
        "data_seal": {
            "min_capture_date_read": scanned.get("min_capture_date_read"),
            "max_capture_date_read": scanned.get("max_capture_date_read"),
            "NEW_DATA_ACQUIRED": False,
            "PROSPECTIVE_DATA_OPENED": False,
            "PROSPECTIVE_ROWS_READ": 0,
            "READ_20260911_PLUS": False,
            "push_records": scanned.get("push_records"),
            "mismatch_day": scanned.get("mismatch_day"),
            "mismatch_got_n": scanned.get("got_n"),
            "mismatch_expected_n": scanned.get("expected_n"),
        },
        "parent": {
            "analysis_id": PARENT_ANALYSIS_ID,
            "verdict": PARENT_VERDICT,
            "FULL_n": PARENT_FULL_N,
            "failed_execution_verdict": FAILED_EXECUTION_VERDICT,
            "failed_execution_id": FAILED_EXECUTION_ID,
            "filled_n": 3283,
            "expired_n": 8647,
            "filled_signal_bid_180_mean": -6.1309308333,
            "expired_signal_bid_180_mean": 9.2357525380,
            "deterioration_fraction": 0.9719768504,
            "identity_ok": decision["identity_ok"],
            "passive_bid_repaired": False,
        },
        "decision": decision,
        "verdict_row": {
            "verdict": decision["verdict"],
            "next": decision["next"],
            "information": decision.get("information"),
            "executable_entry_supported": bool(decision["pass"]),
            "ENTRY_FROZEN": bool(decision["ENTRY_FROZEN"]),
            "ENTRY_ID": decision["ENTRY_ID"],
            "ENTRY_EXECUTION_ID": ENTRY_EXECUTION_ID,
            "EXIT_RESEARCHED": False,
            "COMPLETE_STRATEGY_RUN": False,
            "PNL_RUN": False,
            "BASE_SIGNAL_CHANGED": False,
            "DURATION_SEARCHED": False,
        },
        "safety": {"research_only": True, "submit": 0, "cancel": 0, "live": 0},
        "self_check": checked,
        "gates": decision["gates"],
    }
    publish(report, list(scanned.get("rows") or []))
    primary = decision["horizons"]["180"]
    print(
        json.dumps(
            {
                "VERDICT": decision["verdict"],
                "NEXT": decision["next"],
                "FULL": decision["signal_n"],
                "ACCEPTED": decision["accepted_n"],
                "FAILED": decision["failed_n"],
                "UNAVAILABLE": decision["unavailable_n"],
                "ASK180_MEAN": primary["mean"],
                "ASK180_MEDIAN": primary["median"],
                "IDENTITY": decision["identity_ok"],
                "FROZEN": decision["ENTRY_FROZEN"],
                "MAX_DATE": scanned.get("max_capture_date_read"),
            }
        ),
        flush=True,
    )
    return 0 if decision["identity_ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
