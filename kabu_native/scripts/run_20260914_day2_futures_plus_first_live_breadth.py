"""20260914 combined EOD analyzer. Does not start live capture.

Operator (repo root tradebotfile), Monday 20260914:

  python kabu_native\\scripts\\run_futures_market_context_capture.py --prepare-only --trading-date 20260914
  python kabu_native\\scripts\\run_futures_market_context_capture.py --live --trading-date 20260914
  python kabu_native\\scripts\\run_market_breadth_leadership_capture.py --live --trading-date 20260914
  python kabu_native\\scripts\\run_20260914_day2_futures_plus_first_live_breadth.py

This script is read-only EOD. It never PUT /register, /unregister, or sendorder.
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
    parser = argparse.ArgumentParser(description="20260914 Day2 + first live breadth EOD (read-only)")
    parser.add_argument("--live", action="store_true", help="Rejected. This script does not start capture.")
    args = parser.parse_args()
    from research.new_causal_information_acquisition_v1.launcher import live_order_counts
    from research.run_20260914_day2_futures_plus_first_live_breadth_v1 import (
        BREADTH_LIVE_COMMAND_EXPLICIT,
        FUTURES_LIVE_COMMAND_EXPLICIT,
        FUTURES_PREPARE_COMMAND,
        TRADING_DATE,
    )
    from small_paper.paper_trade_checked_runner import default_pythonpath

    os.environ["PYTHONPATH"] = default_pythonpath()
    if args.live:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "this EOD script does not start live capture",
                    "prepare": FUTURES_PREPARE_COMMAND,
                    "futures_live": FUTURES_LIVE_COMMAND_EXPLICIT,
                    "breadth_live": BREADTH_LIVE_COMMAND_EXPLICIT,
                    "orders": live_order_counts(),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    from research.run_20260914_day2_futures_plus_first_live_breadth_v1.analyze import run_eod
    from research.run_20260914_day2_futures_plus_first_live_breadth_v1.isolation import (
        set_research_priority_below_normal,
        snapshot,
    )
    from research.run_20260914_day2_futures_plus_first_live_breadth_v1.publish import write_artifacts

    set_research_priority_below_normal()
    snap0 = snapshot(phase="PRE")
    body = run_eod(trading_date=TRADING_DATE)
    paths = write_artifacts(body)
    snap1 = snapshot(phase="POST")
    d = dict(body.get("decision") or {})
    out = {
        "ANALYSIS_ID": body.get("ANALYSIS_ID"),
        "trading_date": body.get("trading_date"),
        "today_jst": body.get("today_jst"),
        "VERDICT": d.get("VERDICT"),
        "NEXT": d.get("NEXT"),
        "answers": body.get("answers"),
        "paths": paths,
        "orders": live_order_counts(),
        "ENTRY": False,
        "EXIT": False,
        "live_started": False,
        "isolation": snap1.get("phase") or snap0.get("phase"),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
