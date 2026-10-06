"""Compare next-event fills with the board already known at the target."""
from __future__ import annotations

import json

from research.nominal_latency_actual_fill_delay_audit_v1.metrics import build
from research.nominal_latency_actual_fill_delay_audit_v1.publish import publish
from research.nominal_latency_actual_fill_delay_audit_v1.scan import scan

ANALYSIS_ID = "NOMINAL_LATENCY_VS_ACTUAL_FILL_DELAY_AUDIT_V1"


def main() -> dict:
    scanned = scan()
    decision = build(scanned)
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
        "safety": {"research_only": True, "submit": 0, "cancel": 0, "live": 0, "frozen_v2_mutated": False},
    }
    publish(report)
    print(json.dumps({"verdict": report["verdict"], "parity": decision["trade_parity"], "mismatch": decision["mismatch_n"], "forced": decision["forced_last_bid_close_n"]}, default=str), flush=True)
    return report


if __name__ == "__main__":
    main()
