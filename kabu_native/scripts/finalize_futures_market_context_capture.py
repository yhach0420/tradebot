"""Post-capture NEW_INFO finalizer. Read-only. No sendorder / register / unregister.

Operator command (from repo root tradebotfile):

  python kabu_native\\scripts\\finalize_futures_market_context_capture.py --trading-date 20260911
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1]
SRC = NATIVE / "src"
REPO = NATIVE.parent
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def main() -> int:
    parser = argparse.ArgumentParser(description="NEW_INFO postcapture finalizer (read-only)")
    parser.add_argument("--trading-date", required=True, help="YYYYMMDD")
    args = parser.parse_args()
    from small_paper.paper_trade_checked_runner import default_pythonpath

    os.environ["PYTHONPATH"] = default_pythonpath()
    src = Path(__file__).read_text(encoding="utf-8")
    if ("send" + "order(") in src or ("/" + "sendorder") in src:
        print("FAIL CLOSED: finalizer source contains order API")
        return 2
    from research.new_causal_information_acquisition_v1.finalize import run_finalize
    from research.new_causal_information_acquisition_v1.isolation import NATIVE as ROOT
    from research.new_causal_information_acquisition_v1.launcher import live_order_counts
    from research.new_causal_information_acquisition_v1.__main__ import run_preflight_tests

    tests = run_preflight_tests()
    body = run_finalize(trading_date=str(args.trading_date), native_root=ROOT, tests=tests)
    print(json.dumps({"VERDICT": (body.get("decision") or {}).get("VERDICT") or body.get("VERDICT"), "NEXT": (body.get("decision") or {}).get("NEXT"), "classification": body.get("classification"), "FULL": body.get("FULL"), "orders": live_order_counts(), "paths": body.get("artifact_paths")}, ensure_ascii=False, indent=2))
    if body.get("VERDICT") == "FAIL_CLOSED_CAPTURE_STILL_ALIVE":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
