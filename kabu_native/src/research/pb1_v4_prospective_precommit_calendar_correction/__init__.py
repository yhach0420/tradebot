"""PB1 V4 prospective precommit calendar correction.

Does not mutate V4. Does not open prospective / Confirmation / Frozen Validation.
"""
from __future__ import annotations

from research.current_day1_information_close_v1 import FEATURE_MINING_CLOSED
from research.pb1_v4_clarified_machine_correction_v4.definitions import machine_sha256 as v4_machine_sha256
from research.pb1_v4_clarified_machine_correction_v4.spec import source_sha256 as v4_source_sha256
from research.pb1_v4_clarified_machine_correction_v4_freeze_and_prospective_prep import (
    EXPECTED_SPEC_SHA,
    EXPECTED_V4_MACHINE_SHA256,
    EXPECTED_V4_SOURCE_SHA256,
    FROZEN_IDENTITY,
)
from research.pb1_v4_semantic_spec_clarification.spec import spec_sha256

PROGRAM_ID = "PB1_V4_PROSPECTIVE_PRECOMMIT_CALENDAR_CORRECTION"
ANALYSIS_ID = "PB1_V4_PROSPECTIVE_PRECOMMIT_CALENDAR_CORRECTION_V1"
PARENT_FREEZE_VERDICT = "PB1_V4_CLARIFIED_MACHINE_CORRECTION_V4_FROZEN_AND_PROSPECTIVE_READY_V1"
OLD_PRECOMMIT_SHA256 = "c6bf03f61c9bf936e0440a0994d51ee0c5df15bd91421c5bb2416d596540a50c"
FIRST_ELIGIBLE_JP_CASH_SESSION = "20260924"
NON_CASH_DATES = ("20260921", "20260922", "20260923")

assert spec_sha256() == EXPECTED_SPEC_SHA
assert v4_machine_sha256() == EXPECTED_V4_MACHINE_SHA256
assert v4_source_sha256() == EXPECTED_V4_SOURCE_SHA256
assert FROZEN_IDENTITY == "PB1_V4_CLARIFIED_MACHINE_CORRECTION_V4_FROZEN_V1"

CASE_READY = "PB1_V4_PROSPECTIVE_PRECOMMIT_CALENDAR_CORRECTED_V1"
CASE_BIND = "PB1_V4_PROSPECTIVE_PRECOMMIT_CALENDAR_CORRECTION_BIND_FAILED_V1"
NEXT_PROSPECTIVE = "PB1_V4_PROSPECTIVE_SEMANTIC_VALIDATION_V1"

OLD_CONFIRMATION_OPENED = False
FROZEN_VALIDATION_OPENED = False
PROSPECTIVE_DATA_OPENED = False
V4_CHANGED = False
FEATURE_MINING = FEATURE_MINING_CLOSED
_ = FEATURE_MINING
