"""Run the sealed event-time impulse test and stop before entry execution."""
from __future__ import annotations

import json

from research.event_time_volume_confirmed_impulse import (
    ANALYSIS_ID,
    CAPTURE_FIRST,
    CAPTURE_LAST,
    ESSENTIALLY_ALL_SHARE,
    FRESH_SEC,
    MIN_QTY,
)
from research.event_time_volume_confirmed_impulse.contract import RESET_IMPLEMENTATION
from research.event_time_volume_confirmed_impulse.decide import decide
from research.event_time_volume_confirmed_impulse.features import self_check
from research.event_time_volume_confirmed_impulse.publish import publish
from research.event_time_volume_confirmed_impulse.scan import scan


def main() -> int:
    checked = self_check()
    if not all(checked.values()):
        print(json.dumps({"VERDICT": "FAIL_CLOSED", "SELF_CHECK": checked}), flush=True)
        return 2
    scanned = scan()
    decision = decide(scanned)
    coverage = scanned["coverage"]
    report = {
        "analysis_id": ANALYSIS_ID,
        "verdict": decision["verdict"],
        "next": decision["next"],
        "information": decision.get("information"),
        "min_capture_date_read": scanned["min_capture_date_read"],
        "max_capture_date_read": scanned["max_capture_date_read"],
        "manifest": {
            "analysis_id": ANALYSIS_ID,
            "verdict": decision["verdict"],
            "next": decision["next"],
            "preserved_prior": "SYMBOL_SETUP_DIRECTIONAL_VOLUME_MECHANISM_NOT_SUPPORTED_V1",
        },
        "data_seal": {
            "capture_first": CAPTURE_FIRST,
            "capture_last": CAPTURE_LAST,
            "min_capture_date_read": scanned["min_capture_date_read"],
            "max_capture_date_read": scanned["max_capture_date_read"],
            "NEW_DATA_ACQUIRED": False,
            "PROSPECTIVE_DATA_OPENED": False,
            "PROSPECTIVE_ROWS_READ": 0,
            "FV_ROWS_READ": scanned["fv_rows_read"],
            "push_records": coverage["push_records"],
            "time_inversions": coverage["time_inversions"],
            "parity_fails": coverage["parity_fails"],
            "universe_n": scanned["universe_n"],
            "universe_sha256": scanned["universe_sha256"],
            "sector_mapping_sha256": scanned["sector_mapping_sha256"],
        },
        "event_semantics": [
            {"name": "ASK_CLASSIFIED_VOLUME", "rule": "positive TradingVolume delta and price >= event ask. Not an exchange aggressor flag."},
            {"name": "BID_CLASSIFIED_VOLUME", "rule": "not ask-classified and price <= event bid"},
            {"name": "exchange_aggressor_flag", "rule": "false"},
            {"name": "VOL_ACCEL_10", "rule": "current 10s volume > median of previous six non-overlapping 10s bins"},
            {"name": "VOL_ACCEL_30", "rule": "current 30s volume > median of previous four non-overlapping 30s bins"},
            {"name": "BUY_DOMINANT_10", "rule": "ask_vol_10s > bid_vol_10s"},
            {"name": "TICK_ACCEL_10", "rule": "current 10s tick count > median of previous six 10s tick counts"},
            {"name": "PRICE_BREAK", "rule": "previous price <= max price on [T-60s, T) and price[T] is strictly above that max"},
            {"name": "SPREAD_NOT_WORSE", "rule": "fresh spread at T <= median fresh spread on [T-60s, T)"},
            {"name": "fresh_quote", "rule": f"executable, not special, fresh_sec<={FRESH_SEC}, bid>0, ask>=bid, both qty>={MIN_QTY}"},
            {"name": "RESET", "rule": RESET_IMPLEMENTATION},
            {"name": "essentially_all_share", "rule": ESSENTIALLY_ALL_SHARE},
            {"name": "threshold_searched", "rule": False},
        ],
        "coverage": {
            "volume_windows": coverage["volume_windows"],
            "classified_windows": coverage["classified_windows"],
            "classified_window_fraction": decision["classified_window_fraction"],
            "full_classified_fraction_10s_mean": decision["classified_volume_fraction_10s_mean"],
            "push_records": coverage["push_records"],
            "parity_fails": coverage["parity_fails"],
        },
        "episodes": {
            "rule": "first event per population episode; reset only after price <= frozen PRE_BREAK_HIGH and the population predicate is false",
            "FULL_n": decision["n"]["FULL"],
            "PRICE_BREAK_ONLY_n": decision["n"]["PRICE_BREAK_ONLY"],
            "VOLUME_BREAK_NO_BUY_n": decision["n"]["VOLUME_BREAK_NO_BUY"],
        },
        "decision": decision,
        "concentration": {"day": decision["day"], "symbol": decision["symbol"], "sector": decision["sector"]},
        "verdict_row": {
            "verdict": decision["verdict"],
            "next": decision["next"],
            "mechanism_supported": bool(decision["pass"]),
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
    publish(report)
    full_180 = decision["horizons"]["FULL"]["180"]
    print(
        json.dumps(
            {
                "VERDICT": decision["verdict"],
                "NEXT": decision["next"],
                "FULL": decision["n"]["FULL"],
                "BREAK": decision["n"]["PRICE_BREAK_ONLY"],
                "NOBUY": decision["n"]["VOLUME_BREAK_NO_BUY"],
                "BID_MEAN_180": full_180["bid_anchor"]["mean"],
                "RAW_MEAN_180": full_180["raw_mid"]["mean"],
                "MAX_DATE": scanned["max_capture_date_read"],
                "PASS": decision["pass"],
            }
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
