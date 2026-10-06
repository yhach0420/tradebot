"""RUN_20260914 combined EOD. Read-only. Does not start live capture. No orders."""
from __future__ import annotations

import sys
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from research.new_causal_information_acquisition_v1.launcher import live_order_counts
from research.run_20260914_day2_futures_plus_first_live_breadth_v1 import ANALYSIS_ID, TRADING_DATE
from research.run_20260914_day2_futures_plus_first_live_breadth_v1.analyze import run_eod
from research.run_20260914_day2_futures_plus_first_live_breadth_v1.isolation import (
    set_research_priority_below_normal,
    snapshot,
)
from research.run_20260914_day2_futures_plus_first_live_breadth_v1.publish import write_artifacts


def main() -> int:
    set_research_priority_below_normal()
    src = Path(__file__).read_text(encoding="utf-8")
    if ("send" + "order(") in src or ("/" + "sendorder") in src:
        print("FAIL CLOSED: order API in module")
        return 2
    snap0 = snapshot(phase="PRE")
    body = run_eod(trading_date=TRADING_DATE)
    paths = write_artifacts(body)
    snap1 = snapshot(phase="POST")
    orders = live_order_counts()
    d = dict(body.get("decision") or {})
    print(ANALYSIS_ID)
    print("day:", body.get("trading_date"), "today:", body.get("today_jst"))
    print("VERDICT:", d.get("VERDICT"))
    print("NEXT:", d.get("NEXT"))
    print("Day2:", (body.get("day2") or {}).get("status"))
    print("transport_FULL:", (body.get("transport") or {}).get("FULL"))
    print("submit/cancel/live:", f"{orders['submit']}/{orders['cancel']}/{orders['live']}")
    print("ENTRY:", False, "EXIT:", False)
    print("live_started:", False)
    for k, v in paths.items():
        print(f"{k}: {v}")
    print("isolation_after:", snap1.get("phase") or snap0.get("phase"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
