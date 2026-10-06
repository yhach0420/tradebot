"""Run Family D V1 discovery. Do not freeze an entry."""
from __future__ import annotations

import json

from research.relative_strength_family_d.evaluate import mechanism_self_check
from research.relative_strength_family_d.rules import self_check
from research.relative_strength_family_d_discovery_v1 import ANALYSIS_ID, UNIVERSE_ID, UNIVERSE_SHA256
from research.relative_strength_family_d_discovery_v1.metrics import apply_capture, decide
from research.relative_strength_family_d_discovery_v1.publish import publish
from research.relative_strength_family_d_discovery_v1.scan import scan


def main() -> dict:
    rules = self_check()
    mechanism = mechanism_self_check()
    if not all(rules.values()) or not all(mechanism.values()):
        raise RuntimeError(json.dumps({"rules": rules, "mechanism": mechanism}))
    scanned = scan()
    decision = decide(scanned)
    capture = None
    if decision["historical_pass"]:
        from research.relative_strength_family_d.capture import confirm

        capture = confirm()
        decision = apply_capture(decision, capture)
    report = {
        "analysis_id": ANALYSIS_ID,
        "verdict": decision["verdict"],
        "next": decision["next"],
        "decision": decision,
        "capture": capture or {"ran": False},
        "data_seal": {
            "new_data_acquired": False,
            "historical_min_date": scanned["dates"][0],
            "historical_max_date": scanned["max_date"],
            "rows_read": scanned["rows_read"],
            "rows_on_or_after_20260422": 0,
            "fv_opened": False,
            "prospective_data_opened": False,
            "capture_opened": bool(decision["capture_ran"]),
        },
        "universe": {"id": UNIVERSE_ID, "sha256": scanned["universe_sha256"], "expected_sha256": UNIVERSE_SHA256},
        "safety": {
            "research_only": True,
            "submit": 0,
            "cancel": 0,
            "live": 0,
            "entry_frozen": False,
            "exit_researched": False,
            "complete_strategy_researched": False,
        },
    }
    publish(report)
    print(json.dumps({"verdict": report["verdict"], "next": report["next"], "full_n": decision["full_n"], "failed": decision["failed_premises"]}, default=str), flush=True)
    return report


if __name__ == "__main__":
    main()
