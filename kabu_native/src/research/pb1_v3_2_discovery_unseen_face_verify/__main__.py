"""PB1 V3.2 Discovery-unseen face verify. Frozen V3.2. Runtime 0/0/0."""
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
from research.pb1_v3_2_discovery_unseen_face_verify import ANALYSIS_ID, PROGRAM_ID
from research.pb1_v3_2_discovery_unseen_face_verify.analyze import append_manifest, build_report_body
from research.pb1_v3_2_discovery_unseen_face_verify.bind import bind_prior
from research.pb1_v3_2_discovery_unseen_face_verify.charts import render_sample
from research.pb1_v3_2_discovery_unseen_face_verify.classify import apply_labels
from research.pb1_v3_2_discovery_unseen_face_verify.isolation import (
    CACHE,
    FOUNDATION_OUT,
    OUT,
    STRUCTURE_RCA_OUT,
    TRIGGER_RCA_OUT,
    V1_OUT,
    V2_OUT,
    V3_OUT,
    V31_OUT,
    V32_CACHE,
    V32_OUT,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.pb1_v3_2_discovery_unseen_face_verify.publish import SHEET_ORDER, build_answers, build_sheets, write_artifacts
from research.pb1_v3_2_discovery_unseen_face_verify.sample import materialize_unseen
from research.pb1_v3_2_discovery_unseen_face_verify.spec import source_sha256

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
        "V32_RULE_CHANGED": False,
        "PROSPECTIVE_CAPTURE_USED": False,
        "INDEPENDENT_FACE_REVIEW_RUN": True,
        "ECONOMIC_TEST_RUN": False,
    }


def _slim_bind(bind: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": bind.get("ok"),
        "reason": bind.get("reason"),
        "parent_v32_verdict": bind.get("parent_v32_verdict"),
        "parent_v32_sha": bind.get("parent_v32_sha"),
        "live_v32_machine_sha256": bind.get("live_v32_machine_sha256"),
        "parent_v32_setup_n": bind.get("parent_v32_setup_n"),
        "v32_reported_unseen_n": bind.get("v32_reported_unseen_n"),
        "research_pool_n": bind.get("research_pool_n"),
        "DETECTOR_SHA256": bind.get("DETECTOR_SHA256"),
        "STATE_MACHINE_SHA256": bind.get("STATE_MACHINE_SHA256"),
    }


def main() -> int:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")
    set_research_priority_below_normal()
    print("SAFETY submit/cancel/live=0/0/0 PB1_V3_2_UNSEEN_FACE FROZEN_VAL CLOSED CONFIRMATION CLOSED", flush=True)
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
            "v32": V32_OUT,
            "v32c": V32_CACHE,
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
    materialized = materialize_unseen(bind)
    sample = list(materialized.get("sample") or [])
    print(
        f"UNSEEN reported={materialized.get('reported_discovery_unseen_n')} "
        f"materialized={materialized.get('materialized_unseen_n')} "
        f"overlap={materialized.get('overlap_n')} remaining={materialized.get('remaining_unseen_n')}",
        flush=True,
    )
    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / "unseen_events.json").write_text(
        json.dumps(
            {
                "events": [
                    {
                        "sample_id": e.get("sample_id"),
                        "symbol": e.get("symbol"),
                        "date": e.get("date"),
                        "DIR": e.get("DIR"),
                        "direction": e.get("direction"),
                        "trigger_time": e.get("trigger_t"),
                        "entry_time": e.get("entry_t"),
                        "auction": e.get("auction"),
                    }
                    for e in sample
                ],
                "overlap_n": materialized.get("overlap_n"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    relabel = os.environ.get("PB1_V32_UNSEEN_RELABEL") == "1"
    chart_cache = CACHE / "chart_meta.json"
    if relabel and chart_cache.is_file() and (OUT / "charts").is_dir():
        chart_meta = json.loads(chart_cache.read_text(encoding="utf-8"))
        print("LOADED_CHART_CACHE", flush=True)
    else:
        chart_meta = render_sample(bind, sample)
        chart_cache.write_text(json.dumps(chart_meta, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"CHARTS {len(chart_meta)}", flush=True)
    human = apply_labels(sample)
    print(
        f"REVIEWED {human.get('reviewed_n')}/{human.get('universe_n')} "
        f"CLEAR={human.get('CLEAR_CONTINUATION')} Q={human.get('QUESTIONABLE')} NOT={human.get('NOT_CONTINUATION')}",
        flush=True,
    )
    prev_manifest = {}
    man_path = V32_OUT / "face_review_exclusion_manifest.json"
    if man_path.is_file():
        prev_manifest = json.loads(man_path.read_text(encoding="utf-8"))
    if human.get("actual_manual_blinded_review"):
        manifest = append_manifest(prev_manifest, sample)
    else:
        manifest = {
            "face_review_exclusion_keys": list(prev_manifest.get("face_review_exclusion_keys") or []),
            "previous_manifest_n": len(list(prev_manifest.get("face_review_exclusion_keys") or [])),
            "newly_added_n": 0,
            "new_manifest_n": len(list(prev_manifest.get("face_review_exclusion_keys") or [])),
            "duplicate_n": 0,
            "note": "Labels incomplete; exclusion keys not appended yet.",
        }
    body = build_report_body(bind, materialized, human, chart_meta, manifest)
    after = snapshot(phase="POST")
    safety = _safety()
    safety["INDEPENDENT_FACE_REVIEW_RUN"] = bool(human.get("actual_manual_blinded_review"))
    decision = dict(body.get("decision") or {})
    decision["WRITE_OVERLAP_N"] = overlap
    report = {
        "PROGRAM_ID": PROGRAM_ID,
        "ANALYSIS_ID": ANALYSIS_ID,
        "objective_alignment": {
            "PRIMARY_GOAL": "Blinded semantic holdout of frozen V3.2 Discovery-unseen events",
            "NON_GOALS": [
                "mutate V3.2 / V3.1 / V3 / V2",
                "PnL / PF / MFE / MAE / first passage",
                "Confirmation or Frozen Validation",
                "prospective capture as V3.2 face candidates",
                "V3.3 semantic repair inside this run",
            ],
        },
        "hashes": {
            "SOURCE_SHA256": source_sha256(),
            "PLAYBOOK_MACHINE_SHA256": body.get("MACHINE_SHA256"),
            "PARENT_V32_SHA": body.get("PARENT_V32_SHA"),
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
    write_artifacts(report, sheets, manifest)
    for k, p in {
        "v32": V32_OUT,
        "v32c": V32_CACHE,
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
    if not report.get("v32_unchanged"):
        raise RuntimeError("V32_SHA_DRIFT")
    print(f"OUT {OUT}", flush=True)
    print(f"VERDICT {report['decision']['VERDICT']}", flush=True)
    print(f"NEXT {report['decision']['NEXT']}", flush=True)
    print("STOP.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
