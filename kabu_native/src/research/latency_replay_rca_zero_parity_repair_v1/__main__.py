"""Repair zero-latency parity, then split price decay from state attrition."""
from __future__ import annotations

import json

from research.latency_replay_rca_zero_parity_repair_v1.metrics import build
from research.latency_replay_rca_zero_parity_repair_v1.publish import publish
from research.latency_replay_rca_zero_parity_repair_v1.scan import scan

ANALYSIS_ID = "LATENCY_REPLAY_RCA_AND_ZERO_PARITY_REPAIR_V1"


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
    print(json.dumps({"verdict": report["verdict"], "baseline": decision["baseline"], "zero": decision["zero"], "mismatch_n": decision["mismatch_n"], "hold_parity": decision.get("hold_parity")}, default=str), flush=True)
    return report


if __name__ == "__main__":
    main()
