"""R14 trend incrementality RCA. Development only. Runtime 0/0/0."""
from __future__ import annotations

import hashlib
import json
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
from research.r14_trend_incrementality_rca_v1 import ANALYSIS_ID, PROGRAM_ID
from research.r14_trend_incrementality_rca_v1.analyze import build_answers, build_report_body
from research.r14_trend_incrementality_rca_v1.bind import bind_prior
from research.r14_trend_incrementality_rca_v1.isolation import (
    CACHE,
    FOUNDATION_OUT,
    OUT,
    PARENT_OUT,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.r14_trend_incrementality_rca_v1.publish import SHEET_ORDER, build_markdown, build_sheets, write_artifacts
from research.r14_trend_incrementality_rca_v1.spec import source_sha256

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
        "NEW_FEATURE_ADDED": False,
        "STRATEGY_ECONOMICS_RUN": False,
        "COMPLETE_STRATEGY_NOT_RUN": True,
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
        "parent_published_verdict": bind.get("parent_published_verdict"),
        "current_judgment": bind.get("current_judgment"),
        "judgment_downgraded": True,
        "downgrade_reason": bind.get("downgrade_reason"),
        "TREE_SHA256": bind.get("TREE_SHA256"),
        "EVENT_GENERATOR_SHA256": bind.get("EVENT_GENERATOR_SHA256"),
        "frozen_r14": bind.get("frozen_r14"),
        "threshold_retuned": False,
        "parent_files_not_mutated": True,
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "split": {
            "split_sha256": split.get("split_sha256"),
            "discovery_n": len(list(split.get("discovery_dates") or [])),
        },
        "blocks": {"ok": blocks.get("ok"), "block_sha256": blocks.get("block_sha256")},
        "symbol_n": len(list(bind.get("symbols") or [])),
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
    print("SAFETY submit/cancel/live=0/0/0 R14_RCA FROZEN_VAL CLOSED CONFIRMATION CLOSED", flush=True)
    print(f"PROGRAM_ID {PROGRAM_ID}", flush=True)
    before = snapshot(phase="PRE")
    overlap = write_overlap_n(
        str((before.get("capture") or {}).get("active_dir") or before.get("ACTIVE_CAPTURE_PATH") or ""),
        str((before.get("paper") or {}).get("session_dir") or before.get("ACTIVE_PAPER_SESSION") or ""),
    )
    if overlap:
        raise RuntimeError(f"WRITE_OVERLAP {overlap}")
    fps = {k: _fingerprint(p) for k, p in {"parent": PARENT_OUT, "foundation": FOUNDATION_OUT}.items()}
    bind = bind_prior()
    if not bind.get("ok"):
        raise RuntimeError(f"BIND_FAILED {bind.get('reason')}")
    parent_report = {}
    ppath = PARENT_OUT / "report.json"
    if ppath.is_file():
        parent_report = json.loads(ppath.read_text(encoding="utf-8"))
    body = build_report_body(bind, parent_report)
    after = snapshot(phase="POST")
    safety = _safety()
    decision = dict(body.get("decision") or {})
    decision["WRITE_OVERLAP_N"] = overlap
    report = {
        "PROGRAM_ID": PROGRAM_ID,
        "ANALYSIS_ID": ANALYSIS_ID,
        "objective_alignment": {
            "PRIMARY_GOAL": "Does frozen R14 contain incremental predictive information beyond composition / T15?",
            "NON_GOALS": [
                "complete strategy",
                "threshold retune",
                "new feature search",
                "CAP/PF/X0/X1/Paper",
                "open Confirmation or Frozen Validation",
                "LONG-only or SHORT-only selection",
            ],
        },
        "hashes": {
            "SOURCE_SHA256": source_sha256(),
            "TREE_SHA256": bind.get("TREE_SHA256"),
            "EVENT_GENERATOR_SHA256": bind.get("EVENT_GENERATOR_SHA256"),
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
    for k, p in {"parent": PARENT_OUT, "foundation": FOUNDATION_OUT}.items():
        if fps[k] != _fingerprint(p):
            raise RuntimeError(f"PRIOR_MUTATED_{k}")
    print(f"OUT {OUT}", flush=True)
    print(f"VERDICT {report['decision']['VERDICT']}", flush=True)
    print(f"NEXT {report['decision']['NEXT']}", flush=True)
    print("STOP.", flush=True)
    _ = CACHE
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
