"""PB1_V4_IMPLEMENTATION_CORRECTION_RCA_V1.

Diagnosis only. Do not mutate the corrected candidate or leaked V4.
No thresholds. No prospective data. No PnL.
"""
from __future__ import annotations

from research.current_day1_information_close_v1 import FEATURE_MINING_CLOSED
from research.pb1_v3_2_face_failure_rca import PARENT_V32_SHA, SEMANTIC_RCA_N
from research.pb1_v4_implementation_correction import V4_LEGACY_LEAKED_IMPLEMENTATION
from research.pb1_v4_implementation_correction.definitions import machine_sha256 as corrected_machine_sha256
from research.pb1_v4_opening_drive_location_reaccel_spec import CASE_READY as PARENT_SPEC_VERDICT
from research.pb1_v4_machine_implementation.definitions import machine_sha256 as leaked_machine_sha256
from research.run_20260914_day2_futures_plus_first_live_breadth_v1 import TRADING_DATE as LIVE_TRADING_DATE

PROGRAM_ID = "PB1_V4_IMPLEMENTATION_CORRECTION_RCA"
ANALYSIS_ID = "PB1_V4_IMPLEMENTATION_CORRECTION_RCA_V1"
CORRECTED_PROGRAM = "PB1_V4_IMPLEMENTATION_CORRECTION"
V4_CORRECTED_MACHINE_SHA256 = "21fc72eb420afe5d21ff27bee3b0aa8c28cc6cb35d2a4a441544e4d7e5f5053e"
assert PARENT_SPEC_VERDICT == "PB1_V4_SEMANTIC_SPEC_READY_V1"
assert PARENT_V32_SHA == "56edb2c2576805e46193528824fb61faebb5ae7c85be0201f0331e90f9a67f43"
assert SEMANTIC_RCA_N == 88
assert leaked_machine_sha256() == V4_LEGACY_LEAKED_IMPLEMENTATION
assert corrected_machine_sha256() == V4_CORRECTED_MACHINE_SHA256

CASE_GAPS = "PB1_V4_CORRECTION_RCA_IMPLEMENTATION_GAPS_FOUND_V1"
CASE_SPEC = "PB1_V4_SEMANTIC_SPEC_CLARIFICATION_REQUIRED_V1"
CASE_S1 = "PB1_V4_S1_SEMANTIC_REPRESENTATION_INSUFFICIENT_V1"
CASE_BIND = "PB1_V4_CORRECTION_RCA_BIND_FAILED_V1"
NEXT_V2 = "PB1_V4_IMPLEMENTATION_CORRECTION_V2"
NEXT_CLARIFY = "PB1_V4_SEMANTIC_SPEC_CLARIFICATION_V1"
NEXT_S1 = "PB1_V4_S1_SEMANTIC_REBUILD_V1"
NEXT_BIND = "REPAIR_PRIOR_BIND_THEN_RETRY_V1"

MICRO_CASES = (
    ("7011", "20241205"),
    ("6920", "20250404"),
    ("9501", "20251113"),
)
FAILED_OPEN_FOCUS = (
    ("3382", "20241004"),
    ("7011", "20250523"),
    ("4063", "20251118"),
)

OLD_CONFIRMATION_OPENED = False
FROZEN_VALIDATION_OPENED = False
KABU50_APPLIED = False
PNL_OPTIMIZATION = False
THRESHOLD_OPTIMIZED = False
ANY_RULE_CHANGED = False
V4_1_CREATED = False
FEATURE_MINING = FEATURE_MINING_CLOSED
LIVE_DATE = LIVE_TRADING_DATE
_ = corrected_machine_sha256, leaked_machine_sha256
