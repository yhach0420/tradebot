"""PB1 V3.2 face-failure RCA. Frozen V3.2. Runtime 0/0/0."""
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
from research.pb1_v3_2_face_failure_rca import ANALYSIS_ID, PROGRAM_ID
from research.pb1_v3_2_face_failure_rca.analyze import build_report_body
from research.pb1_v3_2_face_failure_rca.bind import bind_prior
from research.pb1_v3_2_face_failure_rca.charts import render_second_pass
from research.pb1_v3_2_face_failure_rca.descriptors import compute_descriptors
from research.pb1_v3_2_face_failure_rca.isolation import (
    CACHE,
    FOUNDATION_OUT,
    OUT,
    STRUCTURE_RCA_OUT,
    TRIGGER_RCA_OUT,
    UNSEEN_OUT,
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
from research.pb1_v3_2_face_failure_rca.publish import (
    SHEET_ORDER,
    _json_sanitize,
    build_answers,
    build_sheets,
    write_artifacts,
)
from research.pb1_v3_2_face_failure_rca.second_pass import apply_second_pass
from research.pb1_v3_2_face_failure_rca.spec import source_sha256
from research.pb1_v3_2_face_failure_rca.universe import assemble_universe

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
        "V32_RULE_CHANGED": False,
        "V33_CREATED": False,
        "PROSPECTIVE_CAPTURE_USED": False,
        "FUTURE_OUTCOME_USED": False,
        "HOLDOUT_REUSED": False,
    }


def _slim_bind(bind: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": bind.get("ok"),
        "reason": bind.get("reason"),
        "parent_v32_sha": bind.get("parent_v32_sha"),
        "live_v32_machine_sha256": bind.get("live_v32_machine_sha256"),
        "parent_unseen_verdict": bind.get("parent_unseen_verdict"),
        "parent_unseen_reviewed_n": bind.get("parent_unseen_reviewed_n"),
        "parent_v3_reviewed_n": bind.get("parent_v3_reviewed_n"),
        "semantic_rca_n": bind.get("semantic_rca_n"),
        "manifest_final_n": bind.get("manifest_final_n"),
        "holdout_consumed": bind.get("holdout_consumed"),
        "research_pool_n": bind.get("research_pool_n"),
        "DETECTOR_SHA256": bind.get("DETECTOR_SHA256"),
        "STATE_MACHINE_SHA256": bind.get("STATE_MACHINE_SHA256"),
    }


def _slim_desc(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        five = list(r.get("five_m_bars") or [])
        out.append(
            {
                "rca_id": r.get("rca_id"),
                "cohort": r.get("cohort"),
                "symbol": r.get("symbol"),
                "date": r.get("date"),
                "direction": r.get("direction"),
                "machine_auction": r.get("machine_auction"),
                "fp_pattern": r.get("fp_pattern"),
                "opening_descriptor_family": r.get("opening_descriptor_family"),
                "opening_5m": r.get("opening_5m"),
                "five_m_dirs": [b.get("direction") for b in five],
                "five_m_body_over_range": [b.get("body_over_range") for b in five],
                "five_m_range_over_normal": [b.get("range_over_normal_opening_5m") for b in five],
                "reaccel": r.get("reaccel"),
                "location_desc": r.get("location_desc"),
                "stale_desc": r.get("stale_desc"),
                "htf": r.get("htf"),
                "in_play_reason": r.get("in_play_reason"),
                "abs_gap_atr": r.get("abs_gap_atr"),
                "tv_0915_pctl": r.get("tv_0915_pctl"),
                "defended_level_type": r.get("defended_level_type"),
                "zone_class": r.get("zone_class"),
            }
        )
    return out


def main() -> int:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")
    set_research_priority_below_normal()
    print("SAFETY submit/cancel/live=0/0/0 PB1_V3_2_FACE_FAILURE_RCA FROZEN_VAL CLOSED CONFIRMATION CLOSED", flush=True)
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
            "unseen": UNSEEN_OUT,
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
    universe = assemble_universe(bind)
    print(f"UNIVERSE n={universe.get('n')} v3={universe.get('v3_n')} unseen={universe.get('unseen_n')} holdout={universe.get('holdout_reused')}", flush=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    desc_path = CACHE / "descriptors.json"
    relabel = os.environ.get("PB1_V32_RCA_RELABEL") == "1"
    if relabel and desc_path.is_file():
        rows = json.loads(desc_path.read_text(encoding="utf-8"))
        print("LOADED_DESCRIPTOR_CACHE", flush=True)
    else:
        rows = compute_descriptors(bind, list(universe.get("events") or []))
        desc_path.write_text(json.dumps(_json_sanitize(rows), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"DESCRIPTORS {sum(1 for r in rows if r.get('descriptors_ok'))}/{len(rows)}", flush=True)
    (CACHE / "descriptor_slim.json").write_text(json.dumps(_json_sanitize(_slim_desc(rows)), ensure_ascii=False, indent=2), encoding="utf-8")
    skip_charts = os.environ.get("PB1_V32_RCA_SKIP_CHARTS") == "1"
    chart_cache = CACHE / "chart_meta.json"
    if skip_charts and chart_cache.is_file():
        chart_meta = json.loads(chart_cache.read_text(encoding="utf-8"))
        print("LOADED_CHART_CACHE", flush=True)
    elif skip_charts:
        chart_meta = []
        print("SKIP_CHARTS", flush=True)
    else:
        chart_meta = render_second_pass(bind, rows)
        chart_cache.write_text(json.dumps(chart_meta, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"CHARTS {len(chart_meta)}", flush=True)
    human = apply_second_pass(rows)
    print(
        f"SECOND_PASS {human.get('reviewed_n')}/{human.get('universe_n')} "
        f"CLEAR={human.get('CLEAR_CONTINUATION')} Q={human.get('QUESTIONABLE')} NOT={human.get('NOT_CONTINUATION')}",
        flush=True,
    )
    body = build_report_body(bind, universe, human, chart_meta)
    after = snapshot(phase="POST")
    safety = _safety()
    decision = dict(body.get("decision") or {})
    decision["WRITE_OVERLAP_N"] = overlap
    report = {
        "PROGRAM_ID": PROGRAM_ID,
        "ANALYSIS_ID": ANALYSIS_ID,
        "objective_alignment": {
            "PRIMARY_GOAL": "Why frozen V3.2 does not represent Opening Range Continuation as a human sees it",
            "NON_GOALS": [
                "V3.3 / V4 machine implementation",
                "mutate V3.2",
                "PnL / PF / MFE / MAE / first passage / future return",
                "threshold grid",
                "reuse 21 as holdout",
                "Confirmation or Frozen Validation",
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
    write_artifacts(report, sheets)
    for k, p in {
        "v32": V32_OUT,
        "v32c": V32_CACHE,
        "unseen": UNSEEN_OUT,
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
