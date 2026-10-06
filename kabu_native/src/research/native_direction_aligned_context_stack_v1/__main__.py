"""Direction-aligned native context-stack discovery. Development only. Runtime 0/0/0."""
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
from research.native_direction_aligned_context_stack_v1 import ANALYSIS_ID, PROGRAM_ID
from research.native_direction_aligned_context_stack_v1.analyze import build_answers, build_report_body
from research.native_direction_aligned_context_stack_v1.bind import bind_prior
from research.native_direction_aligned_context_stack_v1.isolation import (
    CACHE,
    FOUNDATION_OUT,
    OUT,
    PARENT_OUT,
    STACK_OUT,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.native_direction_aligned_context_stack_v1.publish import SHEET_ORDER, build_markdown, build_sheets, write_artifacts
from research.native_direction_aligned_context_stack_v1.spec import source_sha256

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
        "PNL_OPTIMIZATION": False,
        "THRESHOLD_RETUNED": False,
        "R14_REPAIRED": False,
        "R14_THRESHOLDS_REUSED": False,
        "STRATEGY_ECONOMICS_RUN": False,
        "COMPLETE_STRATEGY_NOT_RUN": True,
        "D1_STATUS": "DISCOVERY_RULE_LEARNING_ONLY",
        "D2_D4_STATUS": "LOCKED_INTERNAL_REPLICATION",
        "SAME_BAR_ENTRY_N": 0,
        "FUTURE_EVENT_SELECTION_N": 0,
        "RETROSPECTIVE_CLUSTER_N": 0,
    }


def _slim_bind(bind: dict[str, Any]) -> dict[str, Any]:
    split = dict(bind.get("split") or {})
    blocks = dict(bind.get("blocks") or {})
    return {
        "ok": bind.get("ok"),
        "reason": bind.get("reason"),
        "research_pool_n": bind.get("research_pool_n"),
        "parent_verdict": bind.get("parent_verdict"),
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "r14_closed": True,
        "t15_unsigned_closed": True,
        "r14_not_repaired": True,
        "r14_thresholds_not_reused": True,
        "d1_status": "DISCOVERY_RULE_LEARNING_ONLY",
        "d2_d4_status": "LOCKED_INTERNAL_REPLICATION",
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
        "EVENT_GENERATOR_SHA256": bind.get("EVENT_GENERATOR_SHA256"),
        "DETECTOR_SHA256": bind.get("DETECTOR_SHA256"),
        "STATE_MACHINE_SHA256": bind.get("STATE_MACHINE_SHA256"),
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
    print("SAFETY submit/cancel/live=0/0/0 DIR_ALIGNED_STACK FROZEN_VAL CLOSED CONFIRMATION CLOSED R14 CLOSED", flush=True)
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
            "parent": PARENT_OUT,
            "stack": STACK_OUT,
            "foundation": FOUNDATION_OUT,
        }.items()
    }
    bind = bind_prior()
    if not bind.get("ok"):
        raise RuntimeError(f"BIND_FAILED {bind.get('reason')}")
    body = build_report_body(bind)
    after = snapshot(phase="POST")
    safety = _safety()
    safety["SAME_BAR_ENTRY_N"] = int(body.get("same_bar_entry_n") or 0)
    safety["FUTURE_EVENT_SELECTION_N"] = 0
    safety["RETROSPECTIVE_CLUSTER_N"] = 0
    decision = dict(body.get("decision") or {})
    decision["WRITE_OVERLAP_N"] = overlap
    report = {
        "PROGRAM_ID": PROGRAM_ID,
        "ANALYSIS_ID": ANALYSIS_ID,
        "objective_alignment": {
            "PRIMARY_GOAL": "Which small combination of DIR-aligned contexts distinguishes continuation in event direction from failure/reversal?",
            "NON_GOALS": [
                "repair R14",
                "retune 0.0040059",
                "reuse R14 tree thresholds",
                "R14 Complete Strategy",
                "unsigned directional predictors",
                "indicator catalog",
                "CAP/PF/X0/X1/Paper",
                "open Confirmation or Frozen Validation",
                "complete strategy",
            ],
        },
        "hashes": {
            "SOURCE_SHA256": source_sha256(),
            "DETECTOR_SHA256": bind.get("DETECTOR_SHA256"),
            "STATE_MACHINE_SHA256": bind.get("STATE_MACHINE_SHA256"),
            "EVENT_GENERATOR_SHA256": bind.get("EVENT_GENERATOR_SHA256"),
            "TREE_SHA256": ((body.get("lock") or {}).get("TREE_SHA256")),
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
    for k, p in {"parent": PARENT_OUT, "stack": STACK_OUT, "foundation": FOUNDATION_OUT}.items():
        if fps[k] != _fingerprint(p):
            raise RuntimeError(f"PRIOR_MUTATED_{k}")
    if int(report.get("FUTURE_EVENT_SELECTION_N") or 0) != 0:
        raise RuntimeError("FUTURE_EVENT_SELECTION_N_NOT_ZERO")
    if int(report.get("RETROSPECTIVE_CLUSTER_N") or 0) != 0:
        raise RuntimeError("RETROSPECTIVE_CLUSTER_N_NOT_ZERO")
    print(f"OUT {OUT}", flush=True)
    print(f"VERDICT {report['decision']['VERDICT']}", flush=True)
    print(f"NEXT {report['decision']['NEXT']}", flush=True)
    print("STOP.", flush=True)
    _ = CACHE
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
