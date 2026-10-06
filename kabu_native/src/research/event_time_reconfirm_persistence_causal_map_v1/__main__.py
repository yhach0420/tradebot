"""Run the persistence map and stop. No entry is frozen."""
from __future__ import annotations

import json

from research.event_time_reconfirm_persistence_causal_map_v1 import ANALYSIS_ID, EXPECTED_FULL_N, REQUIRED_SIGNAL_SHA256
from research.event_time_reconfirm_persistence_causal_map_v1.metrics import build
from research.event_time_reconfirm_persistence_causal_map_v1.publish import publish
from research.event_time_reconfirm_persistence_causal_map_v1.scan import scan
from research.event_time_reconfirm_persistence_causal_map_v1.simulate import self_check


def main() -> dict:
    checks = self_check()
    if not all(checks.values()):
        raise RuntimeError(json.dumps(checks))
    scanned = scan()
    decision = build(scanned)
    if decision["parent_signal_sha256"] != REQUIRED_SIGNAL_SHA256:
        raise RuntimeError("parent_signal_sha_mismatch")
    for key, counts in decision["counts"].items():
        if int(counts.get("initial_full") or 0) != EXPECTED_FULL_N:
            raise RuntimeError(f"parent_full_mismatch:{key}:{counts.get('initial_full')}")
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
            "prospective_rows_read": 0,
            "fv_opened": False,
            "push_records": scanned["push_records"],
        },
        "safety": {
            "research_only": True,
            "submit": 0,
            "cancel": 0,
            "live": 0,
            "entry_frozen": False,
            "exit_frozen": False,
            "complete_strategy_frozen": False,
            "operationally_certified": False,
            "parent_signal_changed": False,
        },
    }
    publish(report)
    print(
        json.dumps(
            {
                "verdict": report["verdict"],
                "next": report["next"],
                "earliest": decision["earliest_executable_ordinal"],
                "ordered": decision["ordered"]["ordered_persistence_improvement"],
                "passed": decision["passed"],
            },
            default=str,
        ),
        flush=True,
    )
    return report


if __name__ == "__main__":
    main()
