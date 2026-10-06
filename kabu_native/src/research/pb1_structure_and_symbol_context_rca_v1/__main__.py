"""PB1 structure and symbol-context RCA V1. Frozen V2. Discovery only. Runtime 0/0/0."""
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
from research.pb1_structure_and_symbol_context_rca_v1 import ANALYSIS_ID, EXPECTED_SETUP_N, PROGRAM_ID
from research.pb1_structure_and_symbol_context_rca_v1.analyze import build_report_body
from research.pb1_structure_and_symbol_context_rca_v1.bind import bind_prior
from research.pb1_structure_and_symbol_context_rca_v1.charts import pick_sample, render_sample
from research.pb1_structure_and_symbol_context_rca_v1.isolation import (
    CACHE,
    FOUNDATION_OUT,
    OUT,
    PARENT_OUT,
    PATH_OUT,
    V1_OUT,
    V2_OUT,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.pb1_structure_and_symbol_context_rca_v1.publish import SHEET_ORDER, build_answers, build_sheets, json_sanitize, write_artifacts
from research.pb1_structure_and_symbol_context_rca_v1.spec import source_sha256
from research.pb1_structure_and_symbol_context_rca_v1.walk import emit_structured

JST = ZoneInfo("Asia/Tokyo")


def _fingerprint(root: Path) -> str:
    h = hashlib.sha256()
    if not root.is_dir():
        return ""
    for p in sorted(root.iterdir()):
        if p.is_file():
            h.update(p.name.encode("utf-8"))
            h.update(p.read_bytes())
        elif p.is_dir() and p.name == "charts":
            for c in sorted(p.iterdir()):
                if c.is_file():
                    h.update(c.name.encode("utf-8"))
                    h.update(c.read_bytes())
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
        "THRESHOLD_CHANGED": False,
        "THRESHOLD_OPTIMIZED": False,
        "BEST_SUBGROUP_SELECTED": False,
        "FAILED_PUSH_PURGED": False,
        "RECLAIM_SELECTED": False,
        "TRIGGER_SELECTED_BY_PNL": False,
        "NEW_INDICATOR_STRATEGY": False,
        "COMPLETE_STRATEGY_NOT_RUN": True,
        "ELIGIBILITY_CHANGED": False,
        "V2_MUTATED": False,
        "PATH_TEST_MUTATED": False,
        "FAILURE_RCA_MUTATED": False,
        "HUMAN_48_USED_AS_FILTER": False,
        "WINNER_PROMOTED_FROM_BEST_RETURN": False,
        "REDESIGN_FROM_TRIGGER_RCA_ALONE": False,
    }


def _slim_bind(bind: dict[str, Any]) -> dict[str, Any]:
    split = dict(bind.get("split") or {})
    blocks = dict(bind.get("blocks") or {})
    return {
        "ok": bind.get("ok"),
        "reason": bind.get("reason"),
        "research_pool_n": bind.get("research_pool_n"),
        "parent_verdict": bind.get("parent_verdict"),
        "parent_playbook_machine_sha256": bind.get("parent_playbook_machine_sha256"),
        "live_v2_machine_sha256": bind.get("live_v2_machine_sha256"),
        "parent_setup_n": bind.get("parent_setup_n"),
        "eligibility_changed": False,
        "failed_push_purged": False,
        "reclaim_selected": False,
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "split": {
            "split_sha256": split.get("split_sha256"),
            "discovery_n": len(list(split.get("discovery_dates") or [])),
            "discovery_first": (list(split.get("discovery_dates") or []) or [None])[0],
            "discovery_last": (list(split.get("discovery_dates") or []) or [None])[-1],
        },
        "blocks": {"ok": blocks.get("ok"), "block_sha256": blocks.get("block_sha256")},
        "symbol_n": len(list(bind.get("symbols") or [])),
        "DETECTOR_SHA256": bind.get("DETECTOR_SHA256"),
        "STATE_MACHINE_SHA256": bind.get("STATE_MACHINE_SHA256"),
    }


def _publish(report: dict[str, Any]) -> None:
    report["answers"] = build_answers(report)
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
    print("SAFETY submit/cancel/live=0/0/0 PB1_STRUCTURE_RCA FROZEN_VAL CLOSED CONFIRMATION CLOSED", flush=True)
    print(f"PROGRAM_ID {PROGRAM_ID}", flush=True)
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
            "failure_rca": PARENT_OUT,
            "path": PATH_OUT,
            "v2": V2_OUT,
            "v1": V1_OUT,
            "foundation": FOUNDATION_OUT,
        }.items()
    }
    bind = bind_prior()
    if not bind.get("ok"):
        raise RuntimeError(f"BIND_FAILED {bind.get('reason')}")
    walked = emit_structured(bind)
    if not walked.get("ok"):
        raise RuntimeError(f"WALK_FAILED {walked.get('reason')}")
    events = list(walked.get("events") or [])
    print(f"SETUP_N {len(events)} SAME_BAR {walked.get('same_bar_entry_n')}", flush=True)
    if len(events) != int(EXPECTED_SETUP_N):
        raise RuntimeError(f"SETUP_N_IDENTITY {len(events)} != {EXPECTED_SETUP_N}")
    sample = pick_sample(events, set(bind.get("v2_verify_keys") or set()), set(bind.get("rca96_keys") or set()))
    print(f"SAMPLE_N {len(sample)}", flush=True)
    chart_meta = render_sample(bind, sample)
    body = build_report_body(bind, walked, sample, chart_meta)
    after = snapshot(phase="POST")
    safety = _safety()
    decision = dict(body.get("decision") or {})
    decision["WRITE_OVERLAP_N"] = overlap
    report = {
        "PROGRAM_ID": PROGRAM_ID,
        "ANALYSIS_ID": ANALYSIS_ID,
        "objective_alignment": {
            "PRIMARY_GOAL": "Did PB1 enter at a sensible causal price location for that specific stock?",
            "NON_GOALS": [
                "PB1 playbook redesign",
                "eligibility retune",
                "purge FAILED_PUSH / select RECLAIM",
                "best structural filter",
                "Complete Strategy / CAP / PF",
                "open Confirmation or Frozen Validation",
                "mutate V2 / path test / trigger RCA",
            ],
        },
        "hashes": {
            "SOURCE_SHA256": source_sha256(),
            "DETECTOR_SHA256": bind.get("DETECTOR_SHA256"),
            "STATE_MACHINE_SHA256": bind.get("STATE_MACHINE_SHA256"),
            "PLAYBOOK_MACHINE_SHA256": body.get("MACHINE_SHA256"),
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
    CACHE.mkdir(parents=True, exist_ok=True)
    _publish(report)
    for k, p in {
        "failure_rca": PARENT_OUT,
        "path": PATH_OUT,
        "v2": V2_OUT,
        "v1": V1_OUT,
        "foundation": FOUNDATION_OUT,
    }.items():
        if fps[k] != _fingerprint(p):
            raise RuntimeError(f"PRIOR_MUTATED_{k}")
    if int(report.get("same_bar_entry_n") or 0) != 0:
        raise RuntimeError("SAME_BAR_ENTRY")
    _ = json_sanitize
    print(f"OUT {OUT}", flush=True)
    print(f"VERDICT {report['decision']['VERDICT']}", flush=True)
    print(f"NEXT {report['decision']['NEXT']}", flush=True)
    print("STOP.", flush=True)
    _ = json
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
