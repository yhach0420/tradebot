"""Freeze the passive-bid entry or stop. No exit research in this run."""
from __future__ import annotations

import json

from research.event_time_volume_confirmed_impulse_entry import (
    ANALYSIS_ID,
    ENTRY_EXECUTION_ID,
    ENTRY_SIGNAL_ID,
    PARENT_ANALYSIS_ID,
    PARENT_ATB180_MEAN,
    PARENT_ATB180_MEDIAN,
    PARENT_BID180_MEAN,
    PARENT_BID180_MEDIAN,
    PARENT_BID180_NEGATIVE,
    PARENT_BID180_POSITIVE,
    PARENT_FULL_N,
    PARENT_VERDICT,
)
from research.event_time_volume_confirmed_impulse_entry.decide import decide
from research.event_time_volume_confirmed_impulse_entry.execution import self_check
from research.event_time_volume_confirmed_impulse_entry.identity import identities
from research.event_time_volume_confirmed_impulse_entry.publish import publish
from research.event_time_volume_confirmed_impulse_entry.scan import scan
from research.event_time_volume_confirmed_impulse_entry import EXPECTED_DAY_FULL


def main() -> int:
    checked = self_check()
    ids = identities()
    if not all(checked.values()) or sum(EXPECTED_DAY_FULL.values()) != PARENT_FULL_N or len(ids["ENTRY_SIGNAL_SHA256"]) != 64:
        print(json.dumps({"VERDICT": "FAIL_CLOSED", "SELF_CHECK": checked}), flush=True)
        return 2
    scanned = scan()
    decision = decide(scanned)
    report = {
        "analysis_id": ANALYSIS_ID,
        "verdict": decision["verdict"],
        "next": decision["next"],
        "manifest": {
            "analysis_id": ANALYSIS_ID,
            "verdict": decision["verdict"],
            "next": decision["next"],
            "ENTRY_SIGNAL_ID": ENTRY_SIGNAL_ID,
            "ENTRY_SIGNAL_SHA256": decision["identities"]["ENTRY_SIGNAL_SHA256"],
            "ENTRY_EXECUTION_ID": ENTRY_EXECUTION_ID,
            "EXECUTION_SHA256": decision["identities"]["EXECUTION_SHA256"],
            "ENTRY_ID": decision["ENTRY_ID"],
            "ENTRY_SHA256": decision["identities"]["ENTRY_SHA256"] if decision["ENTRY_FROZEN"] else None,
        },
        "data_seal": {
            "min_capture_date_read": scanned.get("min_capture_date_read"),
            "max_capture_date_read": scanned.get("max_capture_date_read"),
            "NEW_DATA_ACQUIRED": False,
            "PROSPECTIVE_DATA_OPENED": False,
            "PROSPECTIVE_ROWS_READ": 0,
            "READ_20260911_PLUS": False,
            "push_records": scanned.get("push_records"),
            "universe_sha256": scanned.get("universe_sha256"),
            "sector_mapping_sha256": scanned.get("sector_mapping_sha256"),
            "mismatch_day": scanned.get("mismatch_day"),
            "mismatch_got_n": scanned.get("got_n"),
            "mismatch_expected_n": scanned.get("expected_n"),
        },
        "parent": {
            "analysis_id": PARENT_ANALYSIS_ID,
            "verdict": PARENT_VERDICT,
            "FULL_n": PARENT_FULL_N,
            "bid180_mean": PARENT_BID180_MEAN,
            "bid180_median": PARENT_BID180_MEDIAN,
            "bid180_positive_fraction": PARENT_BID180_POSITIVE,
            "bid180_negative_fraction": PARENT_BID180_NEGATIVE,
            "ask_to_bid_180_mean": PARENT_ATB180_MEAN,
            "ask_to_bid_180_median": PARENT_ATB180_MEDIAN,
            "recomputed": False,
            "identity_ok": decision["identity_ok"],
        },
        "thesis": {
            "text": "A new buy-side impulse has appeared, participation and tick activity have accelerated, price has broken the prior 60-second high, and spread has not deteriorated.",
            "EXIT_DEFINED": False,
            "old_k1_exit_reused": False,
        },
        "decision": {key: value for key, value in decision.items() if key not in {"by_date", "by_symbol", "by_sector"}},
        "verdict_row": {
            "verdict": decision["verdict"],
            "next": decision["next"],
            "executable_entry_supported": bool(decision["pass"]),
            "ENTRY_FROZEN": bool(decision["ENTRY_FROZEN"]),
            "ENTRY_ID": decision["ENTRY_ID"],
            "ENTRY_SHA256": decision["identities"]["ENTRY_SHA256"] if decision["ENTRY_FROZEN"] else None,
            "EXIT_RESEARCHED": False,
            "COMPLETE_STRATEGY_RUN": False,
            "PNL_RUN": False,
            "SIGNAL_CHANGED": False,
            "THRESHOLD_SEARCHED": False,
            "EXECUTION_SEARCHED": False,
        },
        "safety": {"research_only": True, "submit": 0, "cancel": 0, "live": 0},
        "self_check": checked,
        "gates": decision["gates"],
    }
    report["decision"]["by_date"] = decision["by_date"]
    report["decision"]["by_symbol"] = decision["by_symbol"]
    report["decision"]["by_sector"] = decision["by_sector"]
    publish(report, list(scanned.get("rows") or []))
    primary = decision["horizons"]["180"]
    print(
        json.dumps(
            {
                "VERDICT": decision["verdict"],
                "NEXT": decision["next"],
                "FULL": decision["signal_n"],
                "FILLED": decision["filled_n"],
                "EXPIRED": decision["expired_n"],
                "FILL_RATE": decision["fill_rate"],
                "BID180_MEAN": primary["mean"],
                "BID180_MEDIAN": primary["median"],
                "IDENTITY": decision["identity_ok"],
                "FROZEN": decision["ENTRY_FROZEN"],
                "MAX_DATE": scanned.get("max_capture_date_read"),
            }
        ),
        flush=True,
    )
    return 0 if decision["identity_ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
