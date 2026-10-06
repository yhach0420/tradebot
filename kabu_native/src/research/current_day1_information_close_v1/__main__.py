"""Close Day1 existing-raw mining. Read-only. No orders. Day2 freeze untouched."""
from __future__ import annotations

import sys
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from research.current_day1_information_close_v1 import ANALYSIS_ID
from research.current_day1_information_close_v1.analyze import run_close
from research.current_day1_information_close_v1.isolation import (
    DAY2_PRIMARY_OUT,
    set_research_priority_below_normal,
    snapshot,
)
from research.current_day1_information_close_v1.publish import write_artifacts
from research.new_causal_information_acquisition_v1.launcher import live_order_counts


def main() -> int:
    set_research_priority_below_normal()
    src = Path(__file__).read_text(encoding="utf-8")
    if ("send" + "order(") in src or ("/" + "sendorder") in src:
        print("FAIL CLOSED: order API in module")
        return 2
    freeze_before = (DAY2_PRIMARY_OUT / "day2_freeze_manifest.json").read_bytes()
    snap0 = snapshot(phase="PRE")
    body = run_close()
    paths = write_artifacts(body)
    freeze_after = (DAY2_PRIMARY_OUT / "day2_freeze_manifest.json").read_bytes()
    if freeze_before != freeze_after:
        print("FAIL CLOSED: Day2 freeze manifest mutated")
        return 3
    snap1 = snapshot(phase="POST")
    orders = live_order_counts()
    d = dict(body.get("decision") or {})
    print(ANALYSIS_ID)
    print("VERDICT:", d.get("VERDICT"))
    print("NEXT:", d.get("NEXT"))
    print("Day1_feature_mining_closed:", True)
    print("Day2_primary_changed:", False)
    print("submit/cancel/live:", f"{orders['submit']}/{orders['cancel']}/{orders['live']}")
    print("ENTRY:", False, "EXIT:", False)
    for k, v in paths.items():
        print(f"{k}: {v}")
    print("isolation_after:", snap1.get("phase") or snap0.get("phase"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
