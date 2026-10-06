"""Run the first-reconfirm candidate and stop."""
from __future__ import annotations

import json

from research.event_time_reconfirmed_impulse_complete_strategy_v1.metrics import build
from research.event_time_reconfirmed_impulse_complete_strategy_v1.publish import publish
from research.event_time_reconfirmed_impulse_complete_strategy_v1.scan import scan
from research.event_time_reconfirmed_impulse_complete_strategy_v1.simulate import self_check

ANALYSIS_ID = "EVENT_TIME_RECONFIRMED_IMPULSE_COMPLETE_STRATEGY_V1"
REQUIRED_SIGNAL_SHA256 = "06345e9f7ddd2fcdf21a7beac49241495d3a4b6607715c879cf2ef0a307b4f68"


def main() -> dict:
    checks = self_check()
    if not all(checks.values()):
        raise RuntimeError(json.dumps(checks))
    scanned = scan()
    decision = build(scanned)
    if decision["parent_signal_sha256"] != REQUIRED_SIGNAL_SHA256:
        raise RuntimeError("parent_signal_sha_mismatch")
    if decision["counts"]["0"]["initial_full"] != 11930:
        raise RuntimeError(f"parent_full_mismatch:{decision['counts']['0']['initial_full']}")
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
            "entry_frozen": decision["frozen"],
            "exit_frozen": decision["frozen"],
            "complete_strategy_frozen": decision["frozen"],
            "operationally_certified": False,
            "parent_signal_changed": False,
        },
    }
    publish(report)
    zero = decision["blocks"]["0"]["all"]
    primary = decision["blocks"]["100"]["all"]
    print(json.dumps({"verdict": report["verdict"], "zero": zero, "ms100": primary, "counts": decision["counts"]["0"]}, default=str), flush=True)
    return report


if __name__ == "__main__":
    main()
