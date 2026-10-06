"""Run the sealed context interaction and stop before entry execution."""
from __future__ import annotations

import json

from research.context_conditioned_stock_setup import (
    ANALYSIS_ID,
    BREADTH_CUT,
    C1_FIRST,
    C1_LAST,
    CAPTURE_FIRST,
    CAPTURE_LAST,
    DEV_FIRST,
    DEV_LAST,
    FV_FIRST,
    INFORMATION_INSUFFICIENT,
    MIN_SECTOR_PEERS,
    SECTOR_MAPPING_SHA256,
    TRIGGER,
    UNIVERSE_ID,
    UNIVERSE_SHA256,
    VERDICT_NOT,
)
from research.context_conditioned_stock_setup.context import self_check
from research.context_conditioned_stock_setup.decide import decide
from research.context_conditioned_stock_setup.publish import publish
from research.context_conditioned_stock_setup.scan import scan


def main() -> int:
    checked = self_check()
    if not all(checked.values()):
        print(json.dumps({"VERDICT": "FAIL_CLOSED", "SELF_CHECK": checked}), flush=True)
        return 2
    scanned = scan()
    decision = decide(scanned)
    aligned_n = len(scanned["aligned"])
    other_n = len(scanned["not_aligned"])
    del scanned["aligned"]
    del scanned["not_aligned"]
    capture = {"ran": False, "n": 0, "reason": "historical_mechanism_not_supported", "max_capture_date_read": None}
    actionability = {"ran": False, "pass_180s": False, "reason": "capture_confirmation_not_run"}
    if decision["pass"]:
        from research.context_conditioned_stock_setup.capture import confirm

        capture = confirm()
        actionability = {
            "ran": True,
            "pass_180s": bool(capture.get("pass")),
            "raw_mid_180_mean": (capture.get("raw_mid") or {}).get("180", {}).get("mean"),
            "bid_anchor_180_mean": (capture.get("bid_anchor") or {}).get("180", {}).get("mean"),
            "ask_to_bid_180_mean": (capture.get("ask_to_bid") or {}).get("180", {}).get("mean"),
        }
        if not capture.get("pass"):
            decision["verdict"] = VERDICT_NOT
            decision["next"] = "STOP_CURRENT_EXISTING_DATA_STRATEGY_DISCOVERY_V1"
            decision["information"] = INFORMATION_INSUFFICIENT
            decision["pass"] = False
    a5 = decision["horizons"]["aligned"]["ret5"]
    b5 = decision["horizons"]["not_aligned"]["ret5"]
    report = {
        "analysis_id": ANALYSIS_ID,
        "verdict": decision["verdict"],
        "next": decision["next"],
        "information": decision.get("information"),
        "aligned_n": aligned_n,
        "not_aligned_n": other_n,
        "interaction_delta": decision["delta"],
        "max_date": scanned["max_date"],
        "manifest": {"analysis_id": ANALYSIS_ID, "verdict": decision["verdict"], "next": decision["next"]},
        "data_seal": {
            "historical_first": DEV_FIRST,
            "historical_last": C1_LAST,
            "dev": f"{DEV_FIRST}:{DEV_LAST}",
            "c1": f"{C1_FIRST}:{C1_LAST}",
            "max_historical_date_read": scanned["max_date"],
            "rows_read": scanned["rows_read"],
            "fv_rows_read": 0,
            "FROZEN_VALIDATION_OPENED": False,
            "NEW_DATA_ACQUIRED": False,
            "PROSPECTIVE_DATA_OPENED": False,
            "PROSPECTIVE_ROWS_READ": 0,
            "capture_window": f"{CAPTURE_FIRST}:{CAPTURE_LAST}",
            "max_capture_date_read": capture.get("max_capture_date_read"),
            "prior_stock_result_overwritten": False,
        },
        "universe": {
            "id": UNIVERSE_ID,
            "sha256": scanned["universe_sha256"],
            "expected_sha256": UNIVERSE_SHA256,
            "n": scanned["universe_n"],
            "sector_mapping_sha256": scanned["sector_mapping_sha256"],
            "expected_sector_mapping_sha256": SECTOR_MAPPING_SHA256,
        },
        "context_contract": [
            {"name": "MARKET_BREADTH_UP", "rule": "leave-target-out share of other stocks with close[T] > close[T-1]"},
            {"name": "MARKET_POSITIVE", "rule": "MARKET_BREADTH_UP > 0.50"},
            {"name": "SECTOR_BREADTH_UP", "rule": "leave-target-out share of same-sector peers with close[T] > close[T-1]"},
            {"name": "SECTOR_POSITIVE", "rule": "SECTOR_BREADTH_UP > 0.50 and eligible peers >= 3"},
            {"name": "ALIGNED_UP_CONTEXT", "rule": "MARKET_POSITIVE AND SECTOR_POSITIVE"},
            {"name": "LEAVE_TARGET_OUT", "rule": "true"},
            {"breadth_cut": BREADTH_CUT, "min_sector_peers": MIN_SECTOR_PEERS, "threshold_searched": False},
        ],
        "stock_setup": [
            {"trigger": TRIGGER, "path": "TREND_CONTEXT -> IMPULSE -> PULLBACK -> STABILIZATION -> PRIOR_BAR_HIGH_RECLAIM"},
            {"EMA_CHANGED": False, "RCI_CHANGED": False, "BB_CHANGED": False, "VOLUME_THRESHOLD_CHANGED": False, "STRUCTURE_LOOKBACK_CHANGED": False},
        ],
        "population": {
            "trigger_n": scanned["trigger_n"],
            "aligned_n": aligned_n,
            "not_aligned_n": other_n,
            "sector_unavailable_n": scanned["sector_unavailable_n"],
            "market_unavailable_n": scanned["market_unavailable_n"],
            "unavailable_excluded_from_both": True,
        },
        "interaction": {
            "aligned_mean_ret5": a5["mean"],
            "not_aligned_mean_ret5": b5["mean"],
            "delta": decision["delta"],
            "dev_delta": decision["dev"]["delta"],
            "c1_delta": decision["c1"]["delta"],
        },
        "decision": decision,
        "concentration": {"day": decision["day"], "symbol": decision["symbol"], "sector": decision["sector"]},
        "capture": capture,
        "actionability": actionability,
        "verdict_row": {
            "verdict": decision["verdict"],
            "next": decision["next"],
            "historical_pass": bool(decision["pass"]),
            "information": decision.get("information"),
            "ENTRY_FROZEN": False,
            "EXIT_RESEARCHED": False,
            "COMPLETE_STRATEGY_RUN": False,
            "PNL_RUN": False,
        },
        "safety": {"research_only": True, "submit": 0, "cancel": 0, "live": 0},
        "self_check": checked,
        "gates": decision["gates"],
    }
    publish({k: v for k, v in report.items() if k != "decision"} | {"decision": {key: value for key, value in decision.items()}})
    print(
        json.dumps(
            {
                "VERDICT": decision["verdict"],
                "NEXT": decision["next"],
                "ALIGNED": aligned_n,
                "OTHER": other_n,
                "DELTA": decision["delta"],
                "MAX_DATE": scanned["max_date"],
                "PASS": decision["pass"],
            }
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
