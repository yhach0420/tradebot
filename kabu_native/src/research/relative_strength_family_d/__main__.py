"""Run Family D discovery and stop."""
from __future__ import annotations

import json

from research.relative_strength_family_d import ANALYSIS_ID, UNIVERSE_ID, UNIVERSE_SHA256
from research.relative_strength_family_d.decide import apply_capture, decide
from research.relative_strength_family_d.evaluate import mechanism_self_check
from research.relative_strength_family_d.publish import publish
from research.relative_strength_family_d.rules import self_check
from research.relative_strength_family_d.scan import scan


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
        "max_date": scanned["max_date"],
        "max_capture_date_read": None if not capture else capture.get("max_capture_date_read"),
        "decision": decision,
        "capture": capture or {"ran": False},
        "manifest": {
            "analysis_id": ANALYSIS_ID,
            "microstructure_reopened": False,
            "simple_tech_reopened": False,
            "rule_self_check": rules,
            "mechanism_self_check": mechanism,
        },
        "data_seal": {
            "new_data_acquired": False,
            "historical_window": "20240917-20260421",
            "rows_read": scanned["rows_read"],
            "max_historical_date_read": scanned["max_date"],
            "rows_on_or_after_20260422": 0,
            "frozen_validation_opened": False,
            "prospective_data_opened": False,
            "capture_opened": bool(decision["capture_ran"]),
            "max_capture_date_read": None if not capture else capture.get("max_capture_date_read"),
        },
        "universe": {
            "id": UNIVERSE_ID,
            "sha256": scanned["universe_sha256"],
            "expected_sha256": UNIVERSE_SHA256,
            "sector_mapping_sha256": scanned["sector_mapping_sha256"],
        },
        "market_context": {
            "rule": "MARKET_BREADTH_UP <= 0.50",
            "target_excluded": True,
            "threshold_search": False,
            "market_unavailable": scanned["counts"]["market_unavailable"],
            "not_weak_neutral": scanned["counts"]["not_weak_neutral"],
        },
        "sector_context": {
            "rule": "SECTOR_BREADTH_UP > MARKET_BREADTH_UP",
            "minimum_peers": 3,
            "target_excluded": True,
            "sector_unavailable": scanned["counts"]["sector_unavailable"],
            "sector_not_resilient": scanned["counts"]["sector_not_resilient"],
        },
        "relative_strength": {
            "lookback_minutes": 5,
            "rule": "STOCK_RET_5 - SECTOR_MEDIAN_RET_5 > 0",
            "magnitude_search": False,
            "unavailable": scanned["counts"]["relative_strength_unavailable"],
        },
        "participation": {"rule": "volume[T] > 0 AND volume[T] >= 1.5 * median(volume[T-5:T-1])", "multiplier_search": False},
        "technical_continuation": {"rule": "close[T] > high[T-1]", "strict": True, "indicator_search": False},
        "actionability": {
            "ran": bool(decision["capture_ran"]),
            "pass": bool(decision["actionability_pass"]),
            "primary_horizon_sec": 180,
            "metric": "ASK_TO_BID",
        },
        "safety": {
            "research_only": True,
            "submit": 0,
            "cancel": 0,
            "live": 0,
            "entry_frozen": False,
            "exit_researched": False,
            "complete_strategy_run": False,
            "pnl_run": False,
        },
    }
    publish(report)
    print(json.dumps({"verdict": report["verdict"], "next": report["next"], "full_n": decision["full_n"], "control_n": decision["control_n"]}, ensure_ascii=False), flush=True)
    return report


if __name__ == "__main__":
    main()
