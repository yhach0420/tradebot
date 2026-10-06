"""Day1 futures-context effect check. Read-only vs capture. No orders."""
from __future__ import annotations

import sys
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from research.futures_context_day1_effect_check_v1 import ANALYSIS_ID, FEATURE_NAMES
from research.futures_context_day1_effect_check_v1.analyze import run_effect_check
from research.futures_context_day1_effect_check_v1.isolation import (
    OUT,
    set_research_priority_below_normal,
    snapshot,
)
from research.futures_context_day1_effect_check_v1.publish import write_artifacts
from research.new_causal_information_acquisition_v1.launcher import live_order_counts


def main() -> int:
    set_research_priority_below_normal()
    src = Path(__file__).read_text(encoding="utf-8")
    if ("send" + "order(") in src or ("/" + "sendorder") in src:
        print("FAIL CLOSED: order API in module")
        return 2
    snap0 = snapshot(phase="PRE")
    body = run_effect_check()
    paths = write_artifacts(body)
    snap1 = snapshot(phase="POST")
    orders = live_order_counts()
    print(ANALYSIS_ID)
    print("frozen_features:", ",".join(FEATURE_NAMES))
    print("CASE:", (body.get("decision") or {}).get("CASE"))
    print("VERDICT:", (body.get("decision") or {}).get("VERDICT"))
    print("NEXT:", (body.get("decision") or {}).get("NEXT"))
    print("future_leakage_n:", body.get("future_leakage_n"))
    print("submit/cancel/live:", f"{orders['submit']}/{orders['cancel']}/{orders['live']}")
    print("ENTRY:", False, "EXIT:", False)
    for k, v in paths.items():
        print(f"{k}: {v}")
    print("isolation_after:", snap1.get("phase") or snap0.get("phase"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
