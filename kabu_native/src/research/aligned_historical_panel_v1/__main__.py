"""Build aligned historical panel. No strategy. No PnL. Runtime 0/0/0. Universe immutable."""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.am_c0_indicator_exit.isolation import advanced
from research.aligned_historical_panel_v1 import ANALYSIS_ID, ENTRY, EXIT
from research.aligned_historical_panel_v1.analyze import build_answers, build_report_body
from research.aligned_historical_panel_v1.isolation import (
    CACHE,
    FREEZE_OUT,
    OUT,
    REF_PANEL,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.aligned_historical_panel_v1.publish import SHEET_ORDER, build_markdown, build_sheets, write_artifacts
from research.aligned_historical_panel_v1.spec import already_executed_check, source_sha256
from research.fixed_daytrade_universe_v1.secrets import assert_no_secret

JST = ZoneInfo("Asia/Tokyo")


def _safety() -> dict[str, Any]:
    return {
        "SUBMIT_N": 0,
        "CANCEL_N": 0,
        "LIVE_ORDER_N": 0,
        "ENTRY": bool(ENTRY),
        "EXIT": bool(EXIT),
        "RUNTIME_CHANGED": False,
        "PAPER_CHANGED": False,
        "CAPTURE_CHANGED": False,
        "LIVE_20260914_CHANGED": False,
        "STRATEGY_SEARCH_STARTED": False,
        "UNIVERSE_CHANGED": False,
        "YFINANCE_USED": False,
    }


def _publish(report: dict[str, Any]) -> None:
    report["answers"] = build_answers(report)
    report["_markdown"] = build_markdown(report)
    assert_no_secret(report, where="report")
    sheets = build_sheets(report)
    assert tuple(sheets.keys()) == SHEET_ORDER
    assert_no_secret(sheets, where="sheets")
    write_artifacts(report, sheets)


def main() -> int:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")
    set_research_priority_below_normal()
    print("SAFETY submit/cancel/live=0/0/0 ALIGNED PANEL NO STRATEGY NO PNL UNIVERSE IMMUTABLE", flush=True)
    print(f"ANALYSIS_ID {ANALYSIS_ID}", flush=True)
    print(f"FREEZE_ARTIFACTS {FREEZE_OUT}", flush=True)

    reused = already_executed_check()
    if reused.get("REUSED_EXISTING_RESULT"):
        print("REUSE_EXISTING_READY_PANEL=true", flush=True)
        print("STOP.", flush=True)
        return 0

    before = snapshot(phase="PRE")
    overlap = write_overlap_n(
        str((before.get("capture") or {}).get("active_dir") or before.get("ACTIVE_CAPTURE_PATH") or ""),
        str((before.get("paper") or {}).get("session_dir") or before.get("ACTIVE_PAPER_SESSION") or ""),
    )
    if overlap:
        raise RuntimeError(f"WRITE_OVERLAP {overlap}")

    body = build_report_body()
    after = snapshot(phase="POST")
    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "REUSED_EXISTING_RESULT": False,
        "objective_alignment": {
            "PRIMARY_GOAL": "Bind frozen universe and build a causal aligned historical panel, or fail closed.",
            "NON_GOALS": [
                "strategy search",
                "ENTRY/EXIT/MA/RCI/RSI search",
                "PnL optimization",
                "future-return selection",
                "parameter tuning",
                "universe change",
                "20260914 live change",
                "daily substitute for minute",
                "futures autosplice",
            ],
        },
        "hashes": {"SOURCE_SHA256": source_sha256()},
        **body,
        "decision": body["decision"] | {"WRITE_OVERLAP_N": overlap},
        "safety": _safety(),
        "isolation_before": before,
        "isolation_after": after,
        "isolation_advanced": advanced(before, after),
        "PANEL_TIMESTAMP": datetime.now(JST).isoformat(),
    }
    _publish(report)
    print(f"OUT {OUT}", flush=True)
    print(f"VERDICT {report['decision']['VERDICT']}", flush=True)
    print(f"NEXT {report['decision']['NEXT']}", flush=True)
    print("STOP.", flush=True)
    _ = CACHE
    _ = REF_PANEL
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
