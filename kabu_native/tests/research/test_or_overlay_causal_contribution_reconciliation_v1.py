"""OR overlay causal recon tests. Forensic gates. No harvest."""
from __future__ import annotations

from research.or_overlay_causal_contribution_reconciliation_v1 import (
    ANALYSIS_ID,
    CASE_A,
    CASE_E1,
    CASE_E2,
    NEXT_IF_E2,
    OR_ERA_DAYS,
    STRESS_DAYS,
    W27_OR_ERA_SESSIONS,
)
from research.or_overlay_causal_contribution_reconciliation_v1.analyze import decide
from research.or_overlay_causal_contribution_reconciliation_v1.audit import completeness_for_session
from research.or_overlay_causal_contribution_reconciliation_v1.spec import canonical_spec


def test_frozen_or_era_and_no_standalone():
    spec = canonical_spec()
    assert spec["ANALYSIS_ID"] == ANALYSIS_ID
    assert spec["STANDALONE_OPEN_STRENGTH"] is False
    assert spec["CARRY_BACK_CURRENT_CONFIG"] is False
    assert spec["STRESS_OPEN"] is False
    assert tuple(spec["OR_ERA_DAYS"]) == OR_ERA_DAYS
    assert len(W27_OR_ERA_SESSIONS) == 15
    assert "20260828" not in OR_ERA_DAYS
    assert STRESS_DAYS == ("20260828", "20260831", "20260901", "20260902")


def test_missing_session_fails_stream():
    row = {
        "day": "20260714",
        "session": "live_session_082256",
        "dir_exists": False,
        "summary_exists": False,
        "events_jsonl_exists": False,
        "events_csv_exists": False,
        "identity": {"or_overlay_enabled": None, "DAY_HIGH_NEAR_PCT": None},
        "events": {},
        "structural": {},
    }
    f = completeness_for_session(row)
    assert f["session_present"] is False
    assert f["jsonl_present"] is False
    assert f["pbv2_lane_occupancy"] is False
    assert f["fill_event"] is False
    assert f["slot_release_event"] is False


def test_case_e2_when_stream_unrecoverable():
    audit = {
        "or_era_days": list(OR_ERA_DAYS),
        "recovery": {"jsonl_n": 0, "jsonl_found": [], "session_20260714_w27_found": []},
        "sessions": [
            {
                "day": day,
                "session": sess,
                "kind": kind,
                "dir_exists": False,
                "summary_exists": False,
                "events_jsonl_exists": False,
                "events_csv_exists": False,
                "identity": {"or_overlay_enabled": True, "DAY_HIGH_NEAR_PCT": None, "config_sha256": "x"},
                "events": {},
                "structural": {},
            }
            for day, sess, kind in W27_OR_ERA_SESSIONS
        ],
    }
    pack = decide(audit)
    assert pack["CASE_NAME"] == CASE_E2
    assert pack["NEXT"] == NEXT_IF_E2
    assert pack["ECONOMICS_OPENED"] is False
    assert pack["CONTROL_A_VALID"] is False
    assert pack["CONTROL_B_VALID"] is False
    assert pack["summary"]["COUNTERFACTUAL_CANDIDATE_STREAM_COMPLETE"] is False
    assert pack["OR_SLEEVE_INCREMENTAL_STATUS"] == "NOT_PROVABLE"
    assert pack["CASE_A_ALLOWED"] is False
    assert CASE_A != pack["VERDICT"]
    assert CASE_E1 != pack["VERDICT"]
    assert pack["summary"]["integrity_gap_recoverable"] is False
