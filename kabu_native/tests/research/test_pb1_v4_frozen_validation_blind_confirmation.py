"""Tests for Frozen Validation blind confirmation. Does not load Frozen Validation bars."""
from __future__ import annotations

from research.pb1_v4_clarified_machine_correction_v4.definitions import machine_sha256 as v4_sha
from research.pb1_v4_frozen_old_confirmation_blind_validation.precommit import (
    ALLOWED_THESIS_LOST_REASONS as OC_ALLOWED,
    REQUIRED_ZERO as OC_ZERO,
)
from research.pb1_v4_frozen_validation_blind_confirmation import (
    CASE_FAIL,
    CASE_PASS,
    EVAL_FIRST,
    EVAL_LAST,
    EXPECTED_MACHINE_SHA256,
    EXPECTED_SOURCE_INVENTORY_SHA256,
    KNOWN_AUDIT_TAXONOMY_MISMATCH,
    NEXT_CLOSE,
    NEXT_RCA,
    PARENT_OC_PRECOMMIT_SHA256,
    PROSPECTIVE_FROM,
)
from research.pb1_v4_frozen_validation_blind_confirmation.adjudicate import adjudicate
from research.pb1_v4_frozen_validation_blind_confirmation.asf_audit import static_asf_audit
from research.pb1_v4_frozen_validation_blind_confirmation.dates import partition_dates
from research.pb1_v4_frozen_validation_blind_confirmation.gate import identity_gate
from research.pb1_v4_frozen_validation_blind_confirmation.isolation import OUT, write_overlap_n
from research.pb1_v4_frozen_validation_blind_confirmation.precommit import (
    ALLOWED_THESIS_LOST_REASONS,
    REQUIRED_ZERO,
    fv_precommit,
)
from research.pb1_v4_frozen_validation_blind_confirmation.publish import SHEET_ORDER


def test_frozen_identity_allowed_list_unchanged():
    g = identity_gate()
    assert v4_sha() == EXPECTED_MACHINE_SHA256
    assert g["ok"] is True
    assert g["source_inventory_sha"] == EXPECTED_SOURCE_INVENTORY_SHA256
    assert write_overlap_n("", "") == 0
    assert EVAL_FIRST == "20260422" and EVAL_LAST == "20260911"
    assert PROSPECTIVE_FROM == "20260924"
    assert tuple(ALLOWED_THESIS_LOST_REASONS) == tuple(OC_ALLOWED)
    assert tuple(REQUIRED_ZERO) == tuple(OC_ZERO)
    assert "ACCEPTED_STRUCTURAL_FAILURE" not in ALLOWED_THESIS_LOST_REASONS
    assert KNOWN_AUDIT_TAXONOMY_MISMATCH == "ACCEPTED_STRUCTURAL_FAILURE"
    assert "Accepted_Struct_Failure_Audit" in SHEET_ORDER
    assert "pb1_v4_frozen_validation_blind_confirmation" in str(OUT).replace("\\", "/")


def test_precommit_does_not_add_asf_to_allowed_list():
    a = fv_precommit(
        machine_sha=EXPECTED_MACHINE_SHA256,
        source_inventory_sha=EXPECTED_SOURCE_INVENTORY_SHA256,
        frozen_validation_n=88,
        lookback_n=80,
        lookback_first="20251201",
        lookback_last="20260421",
        eval_first=EVAL_FIRST,
        eval_last=EVAL_LAST,
        symbol_n=105,
    )
    b = fv_precommit(
        machine_sha=EXPECTED_MACHINE_SHA256,
        source_inventory_sha=EXPECTED_SOURCE_INVENTORY_SHA256,
        frozen_validation_n=88,
        lookback_n=80,
        lookback_first="20251201",
        lookback_last="20260421",
        eval_first=EVAL_FIRST,
        eval_last=EVAL_LAST,
        symbol_n=105,
    )
    assert a["PRECOMMIT_SHA256"] == b["PRECOMMIT_SHA256"]
    assert a["precommit_id"] == "PB1_V4_FROZEN_VALIDATION_BLIND_CONFIRMATION_PRECOMMIT_V1"
    assert a["ALLOWED_LIST_CHANGED"] is False
    assert a["semantic_adjudication_rubric"]["taxonomy_mismatch_not_added_to_allowed_list"] is True
    assert "ACCEPTED_STRUCTURAL_FAILURE" not in a["semantic_adjudication_rubric"]["allowed_thesis_lost_reasons_after_THESIS_REACHED"]
    assert a["pass_fail_rules"]["degeneracy"]["THESIS_LOST_without_allowed_reason_share_gt_0.50_when_lost_n_ge_10"] == "FAIL"
    assert a["parent_old_confirmation_precommit_sha256"] == PARENT_OC_PRECOMMIT_SHA256
    asf = static_asf_audit()
    assert asf["added_to_frozen_validation_allowed_list"] is False
    assert asf["is_executable_frozen_death_path"] is True
    assert asf["future_information_required"] is False


def test_partition_keeps_prospective_sealed():
    disc = [f"2025{m:02d}{d:02d}" for m in range(9, 12) for d in range(1, 27) if f"2025{m:02d}{d:02d}" <= "20251126"]
    conf = ["20251127", "20260421"]
    fv = ["20260422", "20260911"]
    part = partition_dates(
        {"split": {"discovery_dates": disc, "confirmation_dates": conf, "frozen_validation_dates": fv}}
    )
    assert part["eval_first"] == "20260422"
    assert part["eval_last"] == "20260911"
    assert "20260924" not in part["walk_dates"]
    assert all(d < "20260422" for d in part["lookback_dates"])
    assert "20251127" not in set(part["frozen_validation_dates"])


def test_adjudicate_does_not_pass_by_adding_asf():
    elig = [{"date": "20260422", "ok": True}]
    zeros = {
        "ACTIVE_without_SEED": 0,
        "LOCATION_without_ACTIVE": 0,
        "THESIS_READY_without_LOCATION": 0,
        "THESIS_LIVE_without_ACTIVE_LIVE": 0,
        "EXECUTION_READY_without_THESIS_LIVE": 0,
        "THESIS_REVIVED_AFTER_LOSS": 0,
        "E0_emit_while_not_THESIS_LIVE": 0,
        "E1_emit_while_not_THESIS_LIVE": 0,
        "1m_created_seed": 0,
        "1M_changed_direction_n": 0,
        "1m_revived_thesis": 0,
    }
    good = adjudicate(
        sliced={
            "funnel_days": [
                {
                    "symbol": "7203",
                    "date": "20260422",
                    "WHY_THIS_STOCK": True,
                    "OPENING_DRIVE_SEED": "TRUE_OPENING_DRIVE_SEED",
                    "OPENING_DRIVE_REACHED": True,
                    "OPENING_DRIVE_LIVE": True,
                    "LOCATION_IDENTIFIED": True,
                    "THESIS_REACHED": True,
                    "THESIS_LIVE": True,
                    "THESIS_LOST": True,
                    "THESIS_LOST_REASON": "FAILED_BREAK_REACCEPTED",
                    "E0": False,
                    "E1": False,
                    "same_bar_entry": False,
                    "backdating": False,
                }
            ],
            "e0_events": [],
            "e1_events": [],
            "invariants_raw": zeros,
            "hidden1m": {"HIDDEN_1M_THESIS_PARITY_recomputed": True, "mismatch_n": 0, "1m_created_location_from_id_n": 0},
            "counts": {},
            "same_bar_entry_n": 0,
        },
        eligibility=elig,
        leak_holdout=False,
    )
    assert good["ok"] is True
    assert good["VERDICT"] == CASE_PASS
    assert good["NEXT"] == NEXT_CLOSE
    assert good["allowed_list_changed"] is False
    mass_asf = []
    for _i in range(12):
        mass_asf.append(
            {
                "symbol": "0000",
                "date": "20260422",
                "WHY_THIS_STOCK": True,
                "OPENING_DRIVE_SEED": "TRUE_OPENING_DRIVE_SEED",
                "OPENING_DRIVE_REACHED": True,
                "OPENING_DRIVE_LIVE": True,
                "LOCATION_IDENTIFIED": True,
                "THESIS_REACHED": True,
                "THESIS_LIVE": True,
                "THESIS_LOST": True,
                "THESIS_LOST_REASON": "ACCEPTED_STRUCTURAL_FAILURE",
                "E0": False,
                "E1": False,
            }
        )
    fail = adjudicate(
        sliced={
            "funnel_days": mass_asf,
            "e0_events": [],
            "e1_events": [],
            "invariants_raw": zeros,
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
    assert fail["loss_audit"]["systematic_unexplained_loss"] is True
