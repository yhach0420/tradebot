"""PB1 opening-range continuation face validity. Development only. Runtime 0/0/0."""
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
from research.pb1_opening_range_continuation_face_valid_v1 import ANALYSIS_ID, PROGRAM_ID
from research.pb1_opening_range_continuation_face_valid_v1.analyze import build_report_body, decide
from research.pb1_opening_range_continuation_face_valid_v1.bind import bind_prior
from research.pb1_opening_range_continuation_face_valid_v1.charts import pick_sample, render_sample
from research.pb1_opening_range_continuation_face_valid_v1.classify import apply_labels
from research.pb1_opening_range_continuation_face_valid_v1.isolation import (
    CACHE,
    FOUNDATION_OUT,
    OUT,
    PARENT_OUT,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.pb1_opening_range_continuation_face_valid_v1.publish import SHEET_ORDER, build_sheets, write_artifacts
from research.pb1_opening_range_continuation_face_valid_v1.spec import source_sha256
from research.pb1_opening_range_continuation_face_valid_v1.walk import emit_events

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
        "THRESHOLD_PNL_TUNED": False,
        "MATCHING_RUN": False,
        "ECONOMIC_TEST_RUN": False,
        "STRATEGY_ECONOMICS_RUN": False,
        "COMPLETE_STRATEGY_NOT_RUN": True,
        "FALSE_BREAK_MERGED": False,
        "SAME_BAR_ENTRY_N": 0,
        "FUTURE_OUTCOME_N": 0,
        "PB2_STARTED": False,
        "PB3_STARTED": False,
    }


def _slim_bind(bind: dict[str, Any]) -> dict[str, Any]:
    split = dict(bind.get("split") or {})
    blocks = dict(bind.get("blocks") or {})
    return {
        "ok": bind.get("ok"),
        "reason": bind.get("reason"),
        "research_pool_n": bind.get("research_pool_n"),
        "parent_verdict": bind.get("parent_verdict"),
        "parent_next": bind.get("parent_next"),
        "false_break_merged": False,
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "matching_run": False,
        "economic_test_run": False,
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
    assert_no_secret(report, where="report")
    sheets = build_sheets(report)
    assert tuple(sheets.keys()) == SHEET_ORDER
    assert_no_secret(sheets, where="sheets")
    write_artifacts(report, sheets)


def _republish(bind: dict[str, Any]) -> int:
    path = OUT / "report.json"
    prev = json.loads(path.read_text(encoding="utf-8"))
    sample = list(prev.get("sample") or [])
    chart_meta = list(prev.get("chart_meta") or [])
    human = apply_labels(sample)
    walked = {
        "events": sample,
        "day_rows": [],
        "counts": dict((prev.get("events_summary") or {}).get("counts") or {}),
        "same_bar_entry_n": int(prev.get("same_bar_entry_n") or 0),
        "false_break_n": int(prev.get("false_break_n") or 0),
        "or_modified_after_freeze_n": int(prev.get("or_modified_after_freeze_n") or 0),
        "future_outcome_n": 0,
    }
    body = build_report_body(bind, walked, sample, chart_meta, human)
    body["events_summary"] = prev.get("events_summary") or body.get("events_summary")
    body["in_play"] = prev.get("in_play") or body.get("in_play")
    body["or15_freeze"] = prev.get("or15_freeze") or body.get("or15_freeze")
    body["triggers"] = (body.get("events_summary") or {}).get("trigger_primary") or prev.get("triggers")
    body["decision"] = decide(body)
    after = snapshot(phase="POST")
    safety = _safety()
    safety["SAME_BAR_ENTRY_N"] = int(body.get("same_bar_entry_n") or 0)
    hashes = dict(prev.get("hashes") or {})
    hashes["SOURCE_SHA256"] = source_sha256()
    hashes["PLAYBOOK_MACHINE_SHA256"] = body.get("MACHINE_SHA256")
    report = {
        "PROGRAM_ID": PROGRAM_ID,
        "ANALYSIS_ID": ANALYSIS_ID,
        "republish": True,
        "hashes": hashes,
        "bind": prev.get("bind"),
        **body,
        "safety": safety,
        "isolation_after": after,
        "DISCOVERY_TIMESTAMP": datetime.now(JST).isoformat(),
    }
    _publish(report)
    print(f"OUT {OUT}", flush=True)
    print(f"VERDICT {report['decision']['VERDICT']}", flush=True)
    print(f"NEXT {report['decision']['NEXT']}", flush=True)
    print("STOP.", flush=True)
    return 0


def main() -> int:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")
    set_research_priority_below_normal()
    print("SAFETY submit/cancel/live=0/0/0 PB1_OR_CONT FACE_VALID FROZEN_VAL CLOSED CONFIRMATION CLOSED", flush=True)
    print(f"PROGRAM_ID {PROGRAM_ID}", flush=True)
    print(f"ANALYSIS_ID {ANALYSIS_ID}", flush=True)
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
    if "--republish" in sys.argv:
        return _republish(bind)
    walked = emit_events(bind)
    if not walked.get("ok"):
        raise RuntimeError(f"WALK_FAILED {walked.get('reason')}")
    sample = pick_sample(list(walked.get("events") or []))
    print(f"SAMPLE_N {len(sample)} SETUP_N {len(list(walked.get('events') or []))}", flush=True)
    chart_meta = render_sample(bind, sample)
    human = apply_labels(sample)
    body = build_report_body(bind, walked, sample, chart_meta, human)
    after = snapshot(phase="POST")
    safety = _safety()
    safety["SAME_BAR_ENTRY_N"] = int(body.get("same_bar_entry_n") or 0)
    safety["FUTURE_OUTCOME_N"] = int(body.get("future_outcome_n") or 0)
    decision = dict(body.get("decision") or {})
    decision["WRITE_OVERLAP_N"] = overlap
    report = {
        "PROGRAM_ID": PROGRAM_ID,
        "ANALYSIS_ID": ANALYSIS_ID,
        "objective_alignment": {
            "PRIMARY_GOAL": "Does the event generator detect an ordinary opening-range continuation that a trader would recognize?",
            "NON_GOALS": [
                "economic path test",
                "matching",
                "PnL / PF / CAP / 8bps",
                "false-break reversal playbook",
                "PB2 / PB3",
                "open Confirmation or Frozen Validation",
                "threshold search from future performance",
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
    _publish(report)
    for k, p in {"parent": PARENT_OUT, "foundation": FOUNDATION_OUT}.items():
        if fps[k] != _fingerprint(p):
            raise RuntimeError(f"PRIOR_MUTATED_{k}")
    if int(report.get("same_bar_entry_n") or 0) != 0:
        raise RuntimeError("SAME_BAR_ENTRY")
    if int(report.get("false_break_n") or 0) != 0:
        raise RuntimeError("FALSE_BREAK_EMIT")
    if int(report.get("future_outcome_n") or 0) != 0:
        raise RuntimeError("FUTURE_OUTCOME")
    print(f"OUT {OUT}", flush=True)
    print(f"VERDICT {report['decision']['VERDICT']}", flush=True)
    print(f"NEXT {report['decision']['NEXT']}", flush=True)
    print("STOP.", flush=True)
    _ = CACHE
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
