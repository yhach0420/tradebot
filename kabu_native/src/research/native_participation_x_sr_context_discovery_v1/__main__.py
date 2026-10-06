"""Native participation × S/R context discovery. Development only. Runtime 0/0/0."""
from __future__ import annotations

import hashlib
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
from research.fixed_daytrade_universe_v1.secrets import assert_no_secret
from research.native_participation_x_sr_context_discovery_v1 import ANALYSIS_ID, PROGRAM_ID
from research.native_participation_x_sr_context_discovery_v1.analyze import build_answers, build_report_body
from research.native_participation_x_sr_context_discovery_v1.bind import bind_prior
from research.native_participation_x_sr_context_discovery_v1.isolation import (
    BRACKET_OUT,
    CACHE,
    COMPLETE_OUT,
    FOUNDATION_OUT,
    MATCHED_OUT,
    NOT_STRATEGY_OUT,
    OUT,
    REBUILD_OUT,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.native_participation_x_sr_context_discovery_v1.publish import SHEET_ORDER, build_markdown, build_sheets, write_artifacts
from research.native_participation_x_sr_context_discovery_v1.spec import source_sha256

JST = ZoneInfo("Asia/Tokyo")


def _fingerprint(root: Path) -> str:
    h = hashlib.sha256()
    if not root.is_dir():
        return ""
    for p in sorted(root.iterdir()):
        if p.is_file():
            h.update(p.name.encode("utf-8"))
            h.update(p.read_bytes())
    return h.hexdigest()


def _safety() -> dict[str, Any]:
    return {
        "SUBMIT_N": 0,
        "CANCEL_N": 0,
        "LIVE_ORDER_N": 0,
        "KABU_50_APPLIED": False,
        "FROZEN_VALIDATION_OPENED": False,
        "OLD_CONFIRMATION_OPENED": False,
        "A2_REOPENED_AS_STRATEGY": False,
        "C1_REOPENED": False,
        "PNL_OPTIMIZATION": False,
        "DETECTOR_RETUNED": False,
        "STRATEGY_ECONOMICS_RUN": False,
        "D1_D4_STATUS": "ALL_DEVELOPMENT",
        "SAME_BAR_ENTRY_N": 0,
        "FUTURE_NORMALIZATION": False,
        "RETROSPECTIVE_EVENT_SELECTION": False,
    }


def _slim_bind(bind: dict[str, Any]) -> dict[str, Any]:
    split = dict(bind.get("split") or {})
    blocks = dict(bind.get("blocks") or {})
    return {
        "ok": bind.get("ok"),
        "reason": bind.get("reason"),
        "research_pool_n": bind.get("research_pool_n"),
        "prior_sr_complete_closed": bind.get("prior_sr_complete_closed"),
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "a2_reopened_as_strategy": False,
        "c1_reopened": False,
        "d1_d4_status": "ALL_DEVELOPMENT",
        "detector_retuned": False,
        "split": {
            "split_sha256": split.get("split_sha256"),
            "discovery_n": len(list(split.get("discovery_dates") or [])),
            "discovery_first": (list(split.get("discovery_dates") or []) or [None])[0],
            "discovery_last": (list(split.get("discovery_dates") or []) or [None])[-1],
        },
        "blocks": {"ok": blocks.get("ok"), "block_sha256": blocks.get("block_sha256")},
        "symbol_n": len(list(bind.get("symbols") or [])),
        "freeze": bind.get("freeze"),
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
    print("SAFETY submit/cancel/live=0/0/0 NATIVE_PART_X_SR FROZEN_VAL CLOSED CONFIRMATION CLOSED", flush=True)
    print(f"PROGRAM_ID {PROGRAM_ID}", flush=True)
    print(f"ANALYSIS_ID {ANALYSIS_ID}", flush=True)
    before = snapshot(phase="PRE")
    overlap = write_overlap_n(
        str((before.get("capture") or {}).get("active_dir") or before.get("ACTIVE_CAPTURE_PATH") or ""),
        str((before.get("paper") or {}).get("session_dir") or before.get("ACTIVE_PAPER_SESSION") or ""),
    )
    if overlap:
        raise RuntimeError(f"WRITE_OVERLAP {overlap}")
    fps = {
        k: _fingerprint(p)
        for k, p in {
            "complete": COMPLETE_OUT,
            "matched": MATCHED_OUT,
            "rebuild": REBUILD_OUT,
            "foundation": FOUNDATION_OUT,
            "bracket": BRACKET_OUT,
            "not_strategy": NOT_STRATEGY_OUT,
        }.items()
    }
    bind = bind_prior()
    if not bind.get("ok"):
        raise RuntimeError(f"BIND_FAILED {bind.get('reason')}")
    body = build_report_body(bind)
    after = snapshot(phase="POST")
    safety = _safety()
    safety["SAME_BAR_ENTRY_N"] = int(body.get("same_bar_entry_n") or 0)
    decision = dict(body.get("decision") or {})
    decision["WRITE_OVERLAP_N"] = overlap
    report = {
        "PROGRAM_ID": PROGRAM_ID,
        "ANALYSIS_ID": ANALYSIS_ID,
        "objective_alignment": {
            "PRIMARY_GOAL": "Do native 1m participation+displacement events behave differently in pre-known S/R context?",
            "NON_GOALS": [
                "A2/C1 rescue",
                "S/R standalone strategy",
                "participation-only strategy",
                "CAP/PF/X0/X1/Paper",
                "open Confirmation or Frozen Validation",
                "retune detector",
                "TV percentile grid",
            ],
        },
        "hashes": {
            "SOURCE_SHA256": source_sha256(),
            "DETECTOR_SHA256": ((bind.get("freeze") or {}).get("DETECTOR_SHA256")),
            "STATE_MACHINE_SHA256": ((bind.get("freeze") or {}).get("STATE_MACHINE_SHA256")),
        },
        "bind": _slim_bind(bind),
        **body,
        "decision": decision,
        "safety": safety,
        "isolation_before": before,
        "isolation_after": after,
        "isolation_advanced": advanced(before, after),
        "DISCOVERY_TIMESTAMP": datetime.now(JST).isoformat(),
    }
    _publish(report)
    for k, p in {
        "complete": COMPLETE_OUT,
        "matched": MATCHED_OUT,
        "rebuild": REBUILD_OUT,
        "foundation": FOUNDATION_OUT,
        "bracket": BRACKET_OUT,
        "not_strategy": NOT_STRATEGY_OUT,
    }.items():
        if fps[k] != _fingerprint(p):
            raise RuntimeError(f"PRIOR_MUTATED_{k}")
    if int(report.get("same_bar_entry_n") or 0) != 0:
        raise RuntimeError("SAME_BAR_ENTRY_N_NOT_ZERO")
    print(f"OUT {OUT}", flush=True)
    print(f"VERDICT {report['decision']['VERDICT']}", flush=True)
    print(f"NEXT {report['decision']['NEXT']}", flush=True)
    print("STOP.", flush=True)
    _ = CACHE
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
