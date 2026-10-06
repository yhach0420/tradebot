"""Audit latency replay against frozen V2. Stop if zero-delay parity fails."""
from __future__ import annotations

import json

from research.latency_replay_integrity_holdtime_audit_v1.compare import compare
from research.latency_replay_integrity_holdtime_audit_v1.publish import publish
from research.latency_replay_integrity_holdtime_audit_v1.scan import scan

ANALYSIS_ID = "LATENCY_REPLAY_INTEGRITY_AND_HOLDTIME_AUDIT_V1"


def main() -> dict:
    scanned = scan()
    decision = compare(scanned)
    report = {
        "analysis_id": ANALYSIS_ID,
        "verdict": decision["verdict"],
        "next": decision["next"],
        "decision": decision,
        "data_seal": {
            "new_data_acquired": False,
            "min_capture_date_read": scanned["min_capture_date_read"],
            "max_capture_date_read": scanned["max_capture_date_read"],
            "read_20260911_plus": False,
            "prospective_data_opened": False,
            "fv_opened": False,
            "push_records": scanned["push_records"],
        },
        "safety": {"research_only": True, "submit": 0, "cancel": 0, "live": 0, "entry_modified": False, "exit_modified": False},
    }
    publish(report)
    print(json.dumps({"verdict": report["verdict"], "baseline": decision["baseline"], "zero": decision["zero"], "mismatch_n": decision["mismatch_n"], "first": decision["first_divergence"], "classes": decision["classes"]}, default=str), flush=True)
    return report


if __name__ == "__main__":
    main()
