"""Offline OR overlay causal recon. Forensic gates first. No Capture harvest. No Stress."""
from __future__ import annotations

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
from research.or_overlay_causal_contribution_reconciliation_v1 import ANALYSIS_ID, STRESS_DAYS
from research.or_overlay_causal_contribution_reconciliation_v1.analyze import build_answers, decide
from research.or_overlay_causal_contribution_reconciliation_v1.audit import completeness_for_session, run_audit
from research.or_overlay_causal_contribution_reconciliation_v1.isolation import (
    OUT,
    set_research_priority_below_normal,
    snapshot,
    stress_path_touch_n,
    write_overlap_n,
)
from research.or_overlay_causal_contribution_reconciliation_v1.publish import (
    SHEET_ORDER,
    build_markdown,
    kv_rows,
    write_artifacts,
)
from research.or_overlay_causal_contribution_reconciliation_v1.spec import canonical_spec, source_sha256, spec_sha256

JST = ZoneInfo("Asia/Tokyo")


def _safety() -> dict[str, Any]:
    return {
        "SUBMIT_N": 0,
        "CANCEL_N": 0,
        "LIVE_ORDER_N": 0,
        "ENTRY_RUNTIME_CHANGED": False,
        "EXIT_RUNTIME_CHANGED": False,
        "FUTURE_DATA_USED": False,
        "STRESS_OPENED": False,
        "STRESS_RAW_READ_N": 0,
        "STRESS_NEW_METRIC_N": 0,
        "STRESS_NEW_REPLAY_N": 0,
        "FUTURE_DATA_N": 0,
        "NEW_REPLAY_FROM_CAPTURE": False,
        "NEW_PNL_SIMULATION": False,
        "STANDALONE_OPEN_STRENGTH": False,
        "SIZING": False,
        "ECONOMICS_OPENED": False,
        "STRESS_DAYS_SEALED": list(STRESS_DAYS),
    }


def _publish(report: dict[str, Any]) -> None:
    d = dict(report.get("decision") or {})
    audit = dict(report.get("audit") or {})
    report["answers"] = build_answers(d, audit)
    report["_markdown"] = build_markdown({"answers": report["answers"], "decision": d})
    sessions = list(audit.get("sessions") or [])
    flags = list((d.get("summary") or {}).get("session_flags") or [])
    sess_rows = []
    for s, f in zip(sessions, flags or [None] * len(sessions)):
        ident = dict(s.get("identity") or {})
        ev = dict(s.get("events") or {})
        sess_rows.append(
            {
                "TRADING_DATE": s.get("day"),
                "session": s.get("session"),
                "kind": s.get("kind"),
                "dir_exists": s.get("dir_exists"),
                "summary_exists": s.get("summary_exists"),
                "events_jsonl_exists": s.get("events_jsonl_exists"),
                "events_csv_exists": s.get("events_csv_exists"),
                "or_overlay_enabled": ident.get("or_overlay_enabled"),
                "cap_pbv2": ident.get("cap_pbv2"),
                "cap_or": ident.get("cap_or"),
                "or_max_update_count": ident.get("or_max_update_count"),
                "DAY_HIGH_NEAR_PCT": ident.get("DAY_HIGH_NEAR_PCT"),
                "structural_exit_policy": ident.get("structural_exit_policy"),
                "config_sha256": ident.get("config_sha256"),
                "accepted_csv": ev.get("accepted_n"),
                "rejected_csv": ev.get("rejected_n"),
                "pbv2_cap_reject_csv": ev.get("pbv2_cap_reject_n"),
                "has_pbv2_internal_reason_col": ev.get("has_pbv2_internal_reason_col"),
                "has_entry_type_col": ev.get("has_entry_type_col"),
                "session_present": None if f is None else f.get("session_present"),
            }
        )
    stream_rows = []
    for s in sessions:
        f = completeness_for_session(s)
        stream_rows.append({"day": s.get("day"), "session": s.get("session"), **f})
    ident_rows = []
    for s in sessions:
        ident_rows.append(dict(s.get("identity") or {}))
    sumd = dict(d.get("summary") or {})
    sheets = {
        "answers": kv_rows(report["answers"]),
        "or_era_sessions": sess_rows or [{"empty": True}],
        "stream_completeness": stream_rows or [{"empty": True}],
        "date_scoped_identity": ident_rows or [{"empty": True}],
        "reject_recoverability": kv_rows(
            {
                "TOTAL_PBV2_EVALUATION_N": sumd.get("TOTAL_PBV2_EVALUATION_N"),
                "PBV2_ACCEPT_N": sumd.get("PBV2_ACCEPT_N"),
                "PBV2_REJECT_N": sumd.get("PBV2_REJECT_N"),
                "PBV2_CAP_REJECT_N": sumd.get("PBV2_CAP_REJECT_N"),
                "PBV2_REJECT_WITH_FULL_FEATURE_STATE_N": sumd.get("PBV2_REJECT_WITH_FULL_FEATURE_STATE_N"),
                "PBV2_CAP_REJECT_REPLAYABLE_N": sumd.get("PBV2_CAP_REJECT_REPLAYABLE_N"),
                "PBV2_CAP5_COUNTERFACTUAL_PROVABLE": sumd.get("PBV2_CAP5_COUNTERFACTUAL_PROVABLE"),
                "OR_EVALUATION_N": sumd.get("OR_EVALUATION_N"),
                "OR_ACCEPT_N": sumd.get("OR_ACCEPT_N"),
                "OR_REJECT_N": sumd.get("OR_REJECT_N"),
                "OR_REJECT_REPLAYABLE_N": sumd.get("OR_REJECT_REPLAYABLE_N"),
            }
        ),
        "parity": kv_rows(
            {
                "OR_ACCEPT_IDENTITY_PARITY": False,
                "PBV2_ACCEPT_IDENTITY_PARITY": False,
                "LANE_IDENTITY_PARITY": False,
                "ENTRY_TIMESTAMP_PARITY": False,
                "FILL_IDENTITY_PARITY": False,
                "EXIT_IDENTITY_PARITY": False,
                "DAILY_PNL_PARITY": False,
                "FULL_CAUSAL_PARITY": False,
                "note": "Parity not opened: candidate stream incomplete. Aggregates are not trade identity.",
            }
        ),
        "decision": kv_rows(
            {
                "CASE": d.get("CASE"),
                "CASE_NAME": d.get("CASE_NAME"),
                "VERDICT": d.get("VERDICT"),
                "NEXT": d.get("NEXT"),
                "OR_ARCHITECTURE_STATUS": d.get("OR_ARCHITECTURE_STATUS"),
                "ECONOMICS_OPENED": False,
                "CALLED_ECONOMIC_FAIL": False,
                "COUNTERFACTUAL_CANDIDATE_STREAM_COMPLETE": sumd.get("COUNTERFACTUAL_CANDIDATE_STREAM_COMPLETE"),
                "DATE_SCOPED_RUNTIME_IDENTITY_PROVEN": sumd.get("DATE_SCOPED_RUNTIME_IDENTITY_PROVEN"),
                "CONTROL_A_VALID": d.get("CONTROL_A_VALID"),
                "CONTROL_B_VALID": d.get("CONTROL_B_VALID"),
                "OR_SLEEVE_INCREMENTAL_SUPPORTED": False,
                "PRODUCTION_4_PLUS_1_POLICY_SUPPORTED": False,
            }
        ),
        "safety": kv_rows(report.get("safety")),
    }
    assert tuple(sheets.keys()) == SHEET_ORDER
    write_artifacts(report, sheets)


def main() -> None:
    set_research_priority_below_normal()
    before = snapshot(phase="PRE")
    overlap = write_overlap_n(
        str((before.get("capture") or {}).get("active_dir") or ""),
        str((before.get("paper") or {}).get("session_dir") or ""),
    )
    if overlap:
        raise RuntimeError(f"WRITE_OVERLAP {overlap}")
    audit = run_audit()
    touched = []
    for s in audit.get("sessions") or []:
        touched.append(Path("results") / "small_paper" / str(s.get("day")) / str(s.get("session")))
    if stress_path_touch_n(touched):
        raise RuntimeError("STRESS_PATH_TOUCH")
    pack = decide(audit)
    if pack.get("ECONOMICS_OPENED"):
        raise RuntimeError("ECONOMICS_OPENED_WITHOUT_GATES")
    after = snapshot(phase="POST")
    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "precommit": canonical_spec(),
        "spec_sha256": spec_sha256(),
        "source_sha256": source_sha256(),
        "audit": {
            "or_era_days": audit.get("or_era_days"),
            "w27_session_n": audit.get("w27_session_n"),
            "recovery": audit.get("recovery"),
            "sessions": audit.get("sessions"),
        },
        "decision": pack,
        "safety": _safety(),
        "isolation_before": before,
        "isolation_after": after,
        "isolation_advanced": advanced(before, after),
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "generated_at_jst": datetime.now(JST).isoformat(timespec="seconds"),
    }
    _publish(report)
    print(f"ANALYSIS_ID {ANALYSIS_ID}", flush=True)
    print(f"CASE {pack['CASE_NAME']}", flush=True)
    print(f"VERDICT {pack['VERDICT']}", flush=True)
    print(f"NEXT {pack['NEXT']}", flush=True)
    print(f"STREAM {pack['summary']['COUNTERFACTUAL_CANDIDATE_STREAM_COMPLETE']}", flush=True)
    print(f"CONTROL_A_VALID {pack['CONTROL_A_VALID']}", flush=True)
    print(f"CONTROL_B_VALID {pack['CONTROL_B_VALID']}", flush=True)
    print(f"ECONOMICS_OPENED {pack['ECONOMICS_OPENED']}", flush=True)
    print(f"OUT {OUT}", flush=True)
    print("STOP", flush=True)


if __name__ == "__main__":
    main()
