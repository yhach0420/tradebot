"""PB1 V3.2 opening-drive and reacceleration semantics. Frozen parents. Discovery only. Runtime 0/0/0."""
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
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics import ANALYSIS_ID, PROGRAM_ID
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.analyze import (
    build_report_body,
    unseen_inventory,
    write_exclusion_manifest,
)
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.audit import audit_old67
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.bind import bind_prior
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.isolation import (
    CACHE,
    FOUNDATION_OUT,
    OUT,
    STRUCTURE_RCA_OUT,
    TRIGGER_RCA_OUT,
    V1_OUT,
    V2_OUT,
    V3_OUT,
    V31_OUT,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.publish import SHEET_ORDER, build_answers, build_sheets, write_artifacts
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.spec import source_sha256
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.walk import emit_v32

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
        "THRESHOLD_OPTIMIZED": False,
        "RETEST_5MIN_GATE": False,
        "CLOCK_0930_CUTOFF": False,
        "NEW_INDICATOR": False,
        "RECLAIM_THRESHOLD_FROM_PROFIT": False,
        "V2_MUTATED": False,
        "V3_MUTATED": False,
        "V31_MUTATED": False,
        "INDEPENDENT_FACE_REVIEW_RUN": False,
        "COMPLETE_STRATEGY_NOT_RUN": True,
        "ECONOMIC_TEST_RUN": False,
    }


def _slim_bind(bind: dict[str, Any]) -> dict[str, Any]:
    split = dict(bind.get("split") or {})
    blocks = dict(bind.get("blocks") or {})
    return {
        "ok": bind.get("ok"),
        "reason": bind.get("reason"),
        "research_pool_n": bind.get("research_pool_n"),
        "parent_v31_verdict": bind.get("parent_v31_verdict"),
        "parent_v31_sha": bind.get("parent_v31_sha"),
        "live_v31_machine_sha256": bind.get("live_v31_machine_sha256"),
        "live_v3_machine_sha256": bind.get("live_v3_machine_sha256"),
        "live_v2_machine_sha256": bind.get("live_v2_machine_sha256"),
        "parent_v31_setup_n": bind.get("parent_v31_setup_n"),
        "v3_face_n": len(list(bind.get("v3_face_keys") or [])),
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


def main() -> int:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")
    set_research_priority_below_normal()
    print("SAFETY submit/cancel/live=0/0/0 PB1_V3_2 FROZEN_VAL CLOSED CONFIRMATION CLOSED", flush=True)
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
            "v31": V31_OUT,
            "v3": V3_OUT,
            "v2": V2_OUT,
            "v1": V1_OUT,
            "trig": TRIGGER_RCA_OUT,
            "struct": STRUCTURE_RCA_OUT,
            "foundation": FOUNDATION_OUT,
        }.items()
    }
    bind = bind_prior()
    if not bind.get("ok"):
        raise RuntimeError(f"BIND_FAILED {bind.get('reason')}")
    CACHE.mkdir(parents=True, exist_ok=True)
    cache_walk = CACHE / "walked.json"
    relabel = os.environ.get("PB1_V32_RELABEL") == "1"
    if relabel and cache_walk.is_file():
        walked = json.loads(cache_walk.read_text(encoding="utf-8"))
        print("LOADED_WALK_CACHE", flush=True)
    else:
        walked = emit_v32(bind)
        cache_walk.write_text(json.dumps({k: v for k, v in walked.items()}, default=str, ensure_ascii=False), encoding="utf-8")
    if not walked.get("ok"):
        raise RuntimeError(f"WALK_FAILED {walked.get('reason')}")
    events = list(walked.get("events") or [])
    print(f"SETUP_N {len(events)} SAME_BAR {walked.get('same_bar_entry_n')} ARCHIVE {len(walked.get('failed_push_archive') or [])}", flush=True)
    audit = audit_old67(
        v3_human_rows=list(bind.get("v3_human_rows") or []),
        v32_events=events,
        v32_funnel=list(walked.get("funnel_days") or []),
    )
    print(f"DROPPED_CLEAR_V31 {audit.get('dropped_clear_n')} session_mismatch={audit.get('dropped_clear_session_open_mismatch_n')} other={audit.get('dropped_clear_other_n')}", flush=True)
    print(f"CONFUSION {audit.get('confusion')}", flush=True)
    unseen_meta = unseen_inventory(bind, events)
    print(
        f"UNSEEN_DISC {unseen_meta.get('discovery_unseen_event_n')} POST11 {unseen_meta.get('post_20260911_historical_candidate_n')} CAPTURE_DATES {unseen_meta.get('prospective_capture_date_n')}",
        flush=True,
    )
    print("NO_INDEPENDENT_REVIEW_UNTIL_FROZEN_THEN_UNSEEN", flush=True)
    write_exclusion_manifest(OUT, bind, unseen_meta)
    body = build_report_body(bind, walked, audit, unseen_meta)
    after = snapshot(phase="POST")
    safety = _safety()
    decision = dict(body.get("decision") or {})
    decision["WRITE_OVERLAP_N"] = overlap
    report = {
        "PROGRAM_ID": PROGRAM_ID,
        "ANALYSIS_ID": ANALYSIS_ID,
        "objective_alignment": {
            "PRIMARY_GOAL": "Rebuild opening-drive and reacceleration semantics from human labels only",
            "NON_GOALS": [
                "PnL / PF / best subgroup",
                "Confirmation or Frozen Validation",
                "mutate V3.1 or V3 or V2",
                "5m retest or 09:30 cutoff",
                "reclaim_move>=1.0 profit gate",
                "independent face claim from old 67",
                "Complete Strategy",
            ],
        },
        "hashes": {
            "SOURCE_SHA256": source_sha256(),
            "DETECTOR_SHA256": bind.get("DETECTOR_SHA256"),
            "STATE_MACHINE_SHA256": bind.get("STATE_MACHINE_SHA256"),
            "PLAYBOOK_MACHINE_SHA256": body.get("MACHINE_SHA256"),
            "PARENT_V31_SHA": body.get("PARENT_V31_SHA"),
            "PARENT_V3_SHA": body.get("PARENT_V3_SHA"),
            "PARENT_V2_SHA": body.get("PARENT_V2_SHA"),
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
    report["answers"] = build_answers(report)
    CACHE.mkdir(parents=True, exist_ok=True)
    assert_no_secret(report, where="report")
    sheets = build_sheets(report)
    assert tuple(sheets.keys()) == SHEET_ORDER
    write_artifacts(report, sheets)
    for k, p in {
        "v31": V31_OUT,
        "v3": V3_OUT,
        "v2": V2_OUT,
        "v1": V1_OUT,
        "trig": TRIGGER_RCA_OUT,
        "struct": STRUCTURE_RCA_OUT,
        "foundation": FOUNDATION_OUT,
    }.items():
        if fps[k] != _fingerprint(p):
            raise RuntimeError(f"PRIOR_MUTATED_{k}")
    if int(report.get("same_bar_entry_n") or 0) != 0:
        raise RuntimeError("SAME_BAR_ENTRY")
    if not report.get("v31_unchanged"):
        raise RuntimeError("V31_SHA_DRIFT")
    if not report.get("v3_unchanged"):
        raise RuntimeError("V3_SHA_DRIFT")
    if not report.get("v2_unchanged"):
        raise RuntimeError("V2_SHA_DRIFT")
    print(f"OUT {OUT}", flush=True)
    print(f"VERDICT {report['decision']['VERDICT']}", flush=True)
    print(f"NEXT {report['decision']['NEXT']}", flush=True)
    print("STOP.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
