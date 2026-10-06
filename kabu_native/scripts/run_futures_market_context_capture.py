"""Independent NEW_INFO futures context capture launcher.

Does not modify Paper / OPVAL / standard Capture defaults.
Mutually exclusive with those processes. No sendorder.

Operator commands (from repo root tradebotfile):

  python kabu_native\\scripts\\run_futures_market_context_capture.py --prepare-only --trading-date 20260911
  python kabu_native\\scripts\\run_futures_market_context_capture.py --live --trading-date 20260911

Default without flags is dry-run (no registration).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

NATIVE = Path(__file__).resolve().parents[1]
SRC = NATIVE / "src"
REPO = NATIVE.parent
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

JST = ZoneInfo("Asia/Tokyo")


def _dump(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def main() -> int:
    parser = argparse.ArgumentParser(description="NEW_INFO Core10+Dynamic38+2 futures capture (read-only)")
    parser.add_argument("--prepare-only", action="store_true", help="Prebuild same-day AM universe + prepared_manifest. Allowed outside 07:55-11:30. No registration.")
    parser.add_argument("--live", action="store_true", help="Start live capture. 07:55-11:30 JST only. Default is dry exclusive/window preflight.")
    parser.add_argument("--trading-date", default="", help="YYYYMMDD (default today JST)")
    args = parser.parse_args()
    from research.new_causal_information_acquisition_v1 import LIVE_OPERATOR_COMMAND, PREPARE_OPERATOR_COMMAND
    from research.new_causal_information_acquisition_v1.isolation import NATIVE as ROOT
    from research.new_causal_information_acquisition_v1.launcher import dry_preflight_live_blockers, live_order_counts
    from small_paper.paper_trade_checked_runner import default_pythonpath

    os.environ["PYTHONPATH"] = default_pythonpath()

    if args.prepare_only and args.live:
        print("FAIL CLOSED: --prepare-only and --live are mutually exclusive.")
        return 2

    day = str(args.trading_date or datetime.now(JST).strftime("%Y%m%d"))

    if args.prepare_only:
        from research.new_causal_information_acquisition_v1.prepare import run_prepare_only
        from research.new_causal_information_acquisition_v1.publish_prepare import write_prepare_artifacts

        result = run_prepare_only(native_root=ROOT, trading_date=day)
        write_prepare_artifacts(result)
        _dump(
            {
                "mode": "prepare-only",
                "ok": result.get("ok"),
                "VERDICT": result.get("VERDICT"),
                "NEXT": result.get("NEXT"),
                "answers": result.get("answers"),
                "orders": live_order_counts(),
                "live_command": LIVE_OPERATOR_COMMAND,
                "prepare_command": PREPARE_OPERATOR_COMMAND,
            }
        )
        return 0 if result.get("ok") else 2

    if args.live:
        from research.new_causal_information_acquisition_v1.live import LiveStartError, run_live_capture

        try:
            final = run_live_capture(native_root=ROOT, trading_date=day)
        except LiveStartError as exc:
            _dump(
                {
                    "mode": "live",
                    "ok": False,
                    "VERDICT": exc.verdict,
                    "error": str(exc),
                    "orders": live_order_counts(),
                    "unregister_competitor": False,
                }
            )
            print("Will not unregister an existing 50-stock session.")
            return 2
        _dump({"mode": "live", "ok": True, "result": {k: final.get(k) for k in ("VERDICT", "NEXT", "FULL", "orders")}})
        return 0 if final.get("FULL") else 3

    pre = dry_preflight_live_blockers(native_root=ROOT, trading_date=day)
    print(json.dumps({"dry_preflight": {k: pre[k] for k in ("ok", "blockers", "window_ok", "orders") if k in pre}}, ensure_ascii=False, indent=2))
    print("LIVE_NOT_STARTED default is dry-run. Pass --live during 07:55-11:30 JST after --prepare-only PASS.")
    print(f"prepare: {PREPARE_OPERATOR_COMMAND}")
    print(f"live: {LIVE_OPERATOR_COMMAND}")
    return 0 if pre.get("ok") or "OUTSIDE_0755_1130_JST" in (pre.get("blockers") or []) else 2


if __name__ == "__main__":
    raise SystemExit(main())
