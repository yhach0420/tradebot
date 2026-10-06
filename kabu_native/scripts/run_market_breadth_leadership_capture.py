"""Read-only market-breadth ranking collector.

Does not PUT /register, /unregister, or sendorder.
Does not change standard Paper Core10+Dynamic40=50.
Does not backfill 20260911.

Operator commands (from repo root tradebotfile):

  python kabu_native\\scripts\\run_market_breadth_leadership_capture.py --probe
  python kabu_native\\scripts\\run_market_breadth_leadership_capture.py --live

--probe is the weekend capability proof (empty/stale ranking OK).
--live is 09:05-11:25 JST only, today only, never 20260911.
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
    parser = argparse.ArgumentParser(description="MARKET_BREADTH ranking GET collector (read-only)")
    parser.add_argument("--probe", action="store_true", help="Weekend/read-only /apisoftlimit + /ranking capability proof")
    parser.add_argument("--live", action="store_true", help="Live poll 09:05-11:25 JST. Default without flags is --probe.")
    parser.add_argument("--trading-date", default="", help="YYYYMMDD (live only; default today JST; 20260911 forbidden)")
    parser.add_argument("--once", action="store_true", help="Live: one 7-type cycle then stop")
    args = parser.parse_args()
    from research.market_breadth_leadership_acquisition_v1.isolation import NATIVE as ROOT
    from research.new_causal_information_acquisition_v1.launcher import live_order_counts
    from small_paper.paper_trade_checked_runner import default_pythonpath

    os.environ["PYTHONPATH"] = default_pythonpath()

    if args.probe and args.live:
        print("FAIL CLOSED: --probe and --live are mutually exclusive.")
        return 2

    if args.live:
        from research.market_breadth_leadership_acquisition_v1.collector import run_live_poll

        day = str(args.trading_date or datetime.now(JST).strftime("%Y%m%d"))
        try:
            result = run_live_poll(native_root=ROOT, trading_date=day, once=bool(args.once))
        except Exception as exc:
            _dump(
                {
                    "mode": "live",
                    "ok": False,
                    "error": f"{type(exc).__name__}:{exc}",
                    "orders": live_order_counts(),
                    "register_mutation_n": 0,
                    "unregister_n": 0,
                    "sendorder_n": 0,
                }
            )
            return 2
        _dump({"mode": "live", "ok": True, "result": result, "orders": live_order_counts()})
        return 0

    from research.market_breadth_leadership_acquisition_v1.analyze import run_acquisition_report
    from research.market_breadth_leadership_acquisition_v1.publish import write_artifacts

    body = run_acquisition_report(native_root=ROOT)
    paths = write_artifacts(body)
    d = dict(body.get("decision") or {})
    a = dict(body.get("answers") or {})
    _dump(
        {
            "mode": "probe",
            "CASE": d.get("CASE"),
            "VERDICT": d.get("VERDICT"),
            "NEXT": d.get("NEXT"),
            "answers": {
                k: a.get(k)
                for k in (
                    "1_apisoftlimit_reachable",
                    "3_ranking_reachable",
                    "11_registration_mutation_n",
                    "12_unregister_n",
                    "13_sendorder_n",
                    "14_standard_Paper_config_changed",
                    "15_proposed_safe_cadence",
                    "19_VERDICT",
                    "20_NEXT",
                )
            },
            "token_error": (body.get("probe") or {}).get("token_error"),
            "orders": live_order_counts(),
            "paths": paths,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
