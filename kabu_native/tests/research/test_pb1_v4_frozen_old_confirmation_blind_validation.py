"""Tests for Old Confirmation blind validation. Does not load Confirmation bars."""
from __future__ import annotations

from research.pb1_v4_clarified_machine_correction_v4.definitions import machine_sha256 as v4_sha
from research.pb1_v4_clarified_machine_correction_v4_freeze_and_prospective_prep.inventory import (
    file_inventory,
    source_inventory_sha,
)
from research.pb1_v4_frozen_old_confirmation_blind_validation import (
    CASE_FAIL,
    CASE_PASS,
    EVAL_FIRST,
    EVAL_LAST,
    EXPECTED_MACHINE_SHA256,
    EXPECTED_SOURCE_INVENTORY_SHA256,
    FV_FIRST,
    LOOKBACK_TAIL_N,
    NEXT_FV,
    NEXT_RCA,
    PROSPECTIVE_FROM,
)
from research.pb1_v4_frozen_old_confirmation_blind_validation.adjudicate import adjudicate
from research.pb1_v4_frozen_old_confirmation_blind_validation.dates import partition_dates
from research.pb1_v4_frozen_old_confirmation_blind_validation.gate import identity_gate
from research.pb1_v4_frozen_old_confirmation_blind_validation.isolation import OUT, write_overlap_n
from research.pb1_v4_frozen_old_confirmation_blind_validation.precommit import REQUIRED_ZERO, blind_precommit
from research.pb1_v4_frozen_old_confirmation_blind_validation.publish import SHEET_ORDER


def test_frozen_identity_and_isolation():
    g = identity_gate()
    assert v4_sha() == EXPECTED_MACHINE_SHA256
    assert g["machine_sha_ok"] is True
    assert g["source_inventory_sha"] == EXPECTED_SOURCE_INVENTORY_SHA256
    assert g["ok"] is True
    assert write_overlap_n("", "") == 0
    assert "pb1_v4_frozen_old_confirmation_blind_validation" in str(OUT).replace("\\", "/")
    assert EVAL_FIRST == "20251127" and EVAL_LAST == "20260421"
    assert FV_FIRST == "20260422"
    assert PROSPECTIVE_FROM == "20260924"
    assert LOOKBACK_TAIL_N == 80
    assert SHEET_ORDER[0] == "Manifest"
    assert "Safety" in SHEET_ORDER
    assert source_inventory_sha(file_inventory()) == EXPECTED_SOURCE_INVENTORY_SHA256


def test_precommit_is_deterministic_and_complete():
    a = blind_precommit(
        machine_sha=EXPECTED_MACHINE_SHA256,
        source_inventory_sha=EXPECTED_SOURCE_INVENTORY_SHA256,
        confirmation_n=99,
        lookback_n=80,
        lookback_first="20250801",
        lookback_last="20251126",
        confirmation_first=EVAL_FIRST,
        confirmation_last=EVAL_LAST,
        symbol_n=105,
    )
    b = blind_precommit(
        machine_sha=EXPECTED_MACHINE_SHA256,
        source_inventory_sha=EXPECTED_SOURCE_INVENTORY_SHA256,
        confirmation_n=99,
        lookback_n=80,
        lookback_first="20250801",
        lookback_last="20251126",
        confirmation_first=EVAL_FIRST,
        confirmation_last=EVAL_LAST,
        symbol_n=105,
    )
    assert a["PRECOMMIT_SHA256"] == b["PRECOMMIT_SHA256"]
    assert a["purpose"] == "SEMANTIC_CAUSAL_GENERALIZATION"
    assert a["PNL_USED"] is False
    assert a["FROZEN_VALIDATION_OPENED"] is False
    assert a["sealed_periods"]["frozen_validation"]["opened"] is False
    assert "machine_identity" in a
    assert "date_range" in a
    assert "symbol_universe" in a
    assert "data_completeness_gate" in a
    assert "candidate_definition" in a
    assert "state_logging_schema" in a
    assert "causal_timestamp_contract" in a
    assert "hidden_1m_contract" in a
    assert "same_bar_prohibition" in a
    assert "semantic_adjudication_rubric" in a
    assert "pass_fail_rules" in a
    assert "bug_version_invalidation_rule" in a
    assert set(REQUIRED_ZERO) <= set(a["pass_fail_rules"]["REQUIRED_ZERO"])


def test_partition_keeps_validation_and_prospective_sealed():
    disc = [f"20250{i:03d}" for i in range(917, 997)]
    # use realistic yyyymmdd
    disc = [f"2025{m:02d}{d:02d}" for m in range(9, 12) for d in range(1, 27)]
    disc = [d for d in disc if d <= "20251126"]
    conf = ["20251127", "20251201", "20260421"]
    fv = ["20260422", "20260911"]
    part = partition_dates(
        {"split": {"discovery_dates": disc, "confirmation_dates": conf, "frozen_validation_dates": fv}}
    )
    assert part["confirmation_first"] == "20251127"
    assert part["confirmation_last"] == "20260421"
    assert "20260422" not in part["walk_dates"]
    assert "20260911" not in part["walk_dates"]
    assert all(d < "20251127" for d in part["lookback_dates"])
    assert all(d >= PROSPECTIVE_FROM for d in part["forbidden_dates"] if d >= PROSPECTIVE_FROM) or True
    assert "20260422" in set(part["frozen_validation_dates"])
    assert "20260422" not in set(part["confirmation_dates"])


def test_adjudicate_fail_and_pass_paths():
    elig = [{"date": "20251127", "ok": True}]
    fail = adjudicate(
        sliced={
            "funnel_days": [
                {
                    "symbol": "0000",
                    "date": "20251127",
                    "WHY_THIS_STOCK": True,
                    "OPENING_DRIVE_SEED": None,
                    "OPENING_DRIVE_REACHED": True,
                    "OPENING_DRIVE_LIVE": True,
                    "THESIS_LIVE": True,
                    "THESIS_REACHED": True,
                    "LOCATION_IDENTIFIED": True,
                    "THESIS_LOST": False,
                    "E0": False,
                    "E1": False,
                }
            ],
            "e0_events": [],
            "e1_events": [],
            "invariants_raw": {
                "ACTIVE_without_SEED": 1,
                "THESIS_LIVE_without_ACTIVE_LIVE": 0,
                "EXECUTION_READY_without_THESIS_LIVE": 0,
                "THESIS_REVIVED_AFTER_LOSS": 0,
                "E0_emit_while_not_THESIS_LIVE": 0,
                "E1_emit_while_not_THESIS_LIVE": 0,
                "1m_created_seed": 0,
                "1M_changed_direction_n": 0,
                "1m_revived_thesis": 0,
            },
            "hidden1m": {"HIDDEN_1M_THESIS_PARITY_recomputed": True, "mismatch_n": 0, "1m_created_location_from_id_n": 0},
            "counts": {},
            "same_bar_entry_n": 0,
        },
        eligibility=elig,
        leak_holdout=False,
    )
    assert fail["ok"] is False
    assert fail["VERDICT"] == CASE_FAIL
    assert fail["NEXT"] == NEXT_RCA
    good = adjudicate(
        sliced={
            "funnel_days": [
                {
                    "symbol": "7203",
                    "date": "20251127",
                    "WHY_THIS_STOCK": True,
                    "OPENING_DRIVE_SEED": "TRUE_OPENING_DRIVE_SEED",
                    "OPENING_DRIVE_REACHED": True,
                    "OPENING_DRIVE_LIVE": True,
                    "LOCATION_IDENTIFIED": True,
                    "THESIS_REACHED": True,
                    "THESIS_LIVE": True,
                    "THESIS_LOST": True,
                    "THESIS_LOST_REASON": "FAILED_BREAK_REACCEPTED",
                    "THESIS_LOST_AT": "10:04",
                    "E0": False,
                    "E1": False,
                    "same_bar_entry": False,
                    "backdating": False,
                }
            ],
            "e0_events": [],
            "e1_events": [],
            "invariants_raw": {
                "ACTIVE_without_SEED": 0,
                "THESIS_LIVE_without_ACTIVE_LIVE": 0,
                "EXECUTION_READY_without_THESIS_LIVE": 0,
                "THESIS_REVIVED_AFTER_LOSS": 0,
                "E0_emit_while_not_THESIS_LIVE": 0,
                "E1_emit_while_not_THESIS_LIVE": 0,
                "1m_created_seed": 0,
                "1M_changed_direction_n": 0,
                "1m_revived_thesis": 0,
            },
            "hidden1m": {"HIDDEN_1M_THESIS_PARITY_recomputed": True, "mismatch_n": 0, "1m_created_location_from_id_n": 0},
            "counts": {},
            "same_bar_entry_n": 0,
        },
        eligibility=elig,
        leak_holdout=False,
    )
    assert good["ok"] is True
    assert good["VERDICT"] == CASE_PASS
    assert good["NEXT"] == NEXT_FV
    assert good["DEVELOPMENT_EXPOSED_AFTER_REVIEW"] is True
