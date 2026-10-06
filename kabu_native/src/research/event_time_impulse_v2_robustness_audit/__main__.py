"""Run the robustness audit and stop."""
from __future__ import annotations

import json

from research.event_time_impulse_v2_robustness_audit.metrics import build
from research.event_time_impulse_v2_robustness_audit.publish import publish
from research.event_time_impulse_v2_robustness_audit.scan import scan

ANALYSIS_ID = "ROBUSTNESS_AND_FAILURE_AUDIT_EVENT_TIME_IMPULSE_V2"


def main() -> dict:
    scanned = scan()
    audit = build(scanned)
    report = {
        "analysis_id": ANALYSIS_ID,
        "verdict": audit["verdict"],
        "next": audit["next"],
        "audit": audit,
        "data_seal": {
            "new_data_acquired": False,
            "min_capture_date_read": scanned["min_capture_date_read"],
            "max_capture_date_read": scanned["max_capture_date_read"],
            "read_20260911_plus": False,
            "prospective_data_opened": False,
            "fv_opened": False,
            "push_records": scanned["push_records"],
        },
        "safety": {
            "research_only": True,
            "submit": 0,
            "cancel": 0,
            "live": 0,
            "research_strategy_frozen": True,
            "operationally_certified": audit["verdict"] == "EVENT_TIME_IMPULSE_V2_ROBUSTNESS_SUPPORTED",
        },
    }
    publish(report)
    print(json.dumps({"verdict": report["verdict"], "parity": audit["parity"], "latency_250": audit["latency"]["250"]["all"]}, default=str), flush=True)
    return report


if __name__ == "__main__":
    main()
