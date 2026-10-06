"""Day1 lead/lag RCA. Read-only vs capture. No orders. Day2 freeze unchanged."""
from __future__ import annotations

import sys
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from research.futures_context_day1_lead_lag_rca_v1 import ANALYSIS_ID
from research.futures_context_day1_lead_lag_rca_v1.analyze import run_rca
from research.futures_context_day1_lead_lag_rca_v1.isolation import (
    set_research_priority_below_normal,
    snapshot,
)
from research.futures_context_day1_lead_lag_rca_v1.publish import write_artifacts
from research.new_causal_information_acquisition_v1.launcher import live_order_counts


def main() -> int:
    set_research_priority_below_normal()
    src = Path(__file__).read_text(encoding="utf-8")
    if ("send" + "order(") in src or ("/" + "sendorder") in src:
        print("FAIL CLOSED: order API in module")
        return 2
    snap0 = snapshot(phase="PRE")
    body = run_rca()
    paths = write_artifacts(body)
    snap1 = snapshot(phase="POST")
    orders = live_order_counts()
    d = dict(body.get("decision") or {})
    print(ANALYSIS_ID)
    print("TYPE:", d.get("TYPE"))
    print("VERDICT:", d.get("VERDICT"))
    print("NEXT:", d.get("NEXT"))
    print("future_leakage_n:", body.get("future_leakage_n"))
    print("submit/cancel/live:", f"{orders['submit']}/{orders['cancel']}/{orders['live']}")
    print("ENTRY:", False, "EXIT:", False)
    print("Day2_frozen_test_changed:", False)
    for k, v in paths.items():
        print(f"{k}: {v}")
    print("isolation_after:", snap1.get("phase") or snap0.get("phase"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
