"""PB1 V4 Complete Strategy build. Frozen ENTRY. Development binding only.

Does not open Old Confirmation / Frozen Validation economic outcomes.
Does not place live orders.
"""
from __future__ import annotations

from research.cause_first_mechanism_discovery_v1 import X1_TAX_BPS
from research.current_day1_information_close_v1 import FEATURE_MINING_CLOSED
from research.pb1_opening_range_continuation_face_valid_v2 import SESSION_FLAT
from research.pb1_v4_clarified_machine_correction_v4.definitions import machine_sha256 as v4_machine_sha256
from research.pb1_v4_clarified_machine_correction_v4.spec import source_sha256 as v4_source_sha256
from research.pb1_v4_clarified_machine_correction_v4_freeze_and_prospective_prep import (
    EXPECTED_SPEC_SHA,
    EXPECTED_V4_MACHINE_SHA256,
    EXPECTED_V4_SOURCE_SHA256,
    FROZEN_IDENTITY,
)
from research.pb1_v4_semantic_spec_clarification.spec import spec_sha256
from small_paper.v1r_primary_runtime import LOT_QTY, POSITION_CAP

PROGRAM_ID = "PB1_V4_COMPLETE_STRATEGY_BUILD_AND_ECONOMIC_VALIDATION"
ANALYSIS_ID = "PB1_V4_COMPLETE_STRATEGY_BUILD_AND_ECONOMIC_VALIDATION_V1"
FROZEN_ENTRY_IDENTITY = FROZEN_IDENTITY
EXPECTED_MACHINE_SHA256 = "8d9c79b644616146ad01100e27f34368460d4c032d955d0ced2aab171fbfb6cf"
EXPECTED_SOURCE_INVENTORY_SHA256 = "12299debc918a8b509c4105bc60448e0990c15748ded62ab72cbdf050886e4cf"

DEV_FIRST = "20240917"
DEV_LAST = "20251126"
OC_FIRST = "20251127"
OC_LAST = "20260421"
FV_FIRST = "20260422"
FV_LAST = "20260911"
PROSPECTIVE_FROM = "20260924"

SHARES = int(LOT_QTY)
CAP = int(POSITION_CAP)
assert SHARES == 100
assert CAP == 5
assert float(X1_TAX_BPS) == 8.0
assert SESSION_FLAT == "15:20"

LUNCH_POLICY = "HOLD_THROUGH_LUNCH_RESUME_PM"
COST_MODEL_ID = "X1_8BPS_EXECUTION_STRESS"
HISTORICAL_FILL_ID = "HISTORICAL_NEXT_BAR_OPEN_EXECUTION"
LIVE_FILL_SOT_ID = "X1_IMMEDIATE_ASK"
TECHNICAL_EXIT_ID = "PB1_V4_THESIS_LOST_NEXT_OPEN"
OPS_EXIT_ID = "SESSION_FLAT_1520"

CASE_READY = "PB1_V4_COMPLETE_STRATEGY_FROZEN_READY_FOR_ECONOMIC_CONFIRMATION_V1"
CASE_FAIL = "PB1_V4_COMPLETE_STRATEGY_BINDING_NOT_READY_V1"
NEXT_CONF1 = "PB1_V4_COMPLETE_STRATEGY_ECONOMIC_CONFIRMATION1_V1"
NEXT_STOP = "STOP"

assert spec_sha256() == EXPECTED_SPEC_SHA
assert v4_machine_sha256() == EXPECTED_V4_MACHINE_SHA256
assert v4_source_sha256() == EXPECTED_V4_SOURCE_SHA256
assert EXPECTED_MACHINE_SHA256 == EXPECTED_V4_MACHINE_SHA256
assert FROZEN_ENTRY_IDENTITY == "PB1_V4_CLARIFIED_MACHINE_CORRECTION_V4_FROZEN_V1"

FEATURE_MINING = FEATURE_MINING_CLOSED
_ = FEATURE_MINING
