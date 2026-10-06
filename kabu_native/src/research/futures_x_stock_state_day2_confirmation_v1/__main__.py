"""FUTURES_X_STOCK_STATE_DAY2_CONFIRMATION_V1. Read-only. No orders. No Day1 mining."""
from __future__ import annotations

import sys
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from research.futures_x_stock_state_day2_confirmation_v1 import ANALYSIS_ID, TRADING_DATE
from research.futures_x_stock_state_day2_confirmation_v1.analyze import run_day2_confirmation
from research.futures_x_stock_state_day2_confirmation_v1.isolation import (
    set_research_priority_below_normal,
    snapshot,
)
from research.futures_x_stock_state_day2_confirmation_v1.publish import write_artifacts
from research.new_causal_information_acquisition_v1.launcher import live_order_counts


def main() -> int:
    set_research_priority_below_normal()
    src = Path(__file__).read_text(encoding="utf-8")
    if ("send" + "order(") in src or ("/" + "sendorder") in src:
        print("FAIL CLOSED: order API in module")
        return 2
    snap0 = snapshot(phase="PRE")
    body = run_day2_confirmation(trading_date=TRADING_DATE)
    paths = write_artifacts(body)
    snap1 = snapshot(phase="POST")
    orders = live_order_counts()
    d = dict(body.get("decision") or {})
    print(ANALYSIS_ID)
    print("day:", body.get("trading_date"))
    print("CASE:", d.get("CASE"))
    print("VERDICT:", d.get("VERDICT"))
    print("NEXT:", d.get("NEXT"))
    print("PASS:", d.get("PASS"))
    print("secondary_rescue_used:", False)
    print("submit/cancel/live:", f"{orders['submit']}/{orders['cancel']}/{orders['live']}")
    print("ENTRY:", False, "EXIT:", False)
    for k, v in paths.items():
        print(f"{k}: {v}")
    print("isolation_after:", snap1.get("phase") or snap0.get("phase"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
