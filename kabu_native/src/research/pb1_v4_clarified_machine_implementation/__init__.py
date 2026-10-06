"""PB1_V4_CLARIFIED_MACHINE_IMPLEMENTATION_V1.

New machine for PB1_V4_SEMANTIC_SPEC_CLARIFIED_V2.
Does not mutate READY_V1, clarified spec, 21fc72eb, or fa0451bb.
"""
from __future__ import annotations

from research.current_day1_information_close_v1 import FEATURE_MINING_CLOSED
from research.pb1_v3_1_face_validity_fix import NOISE_LOOKBACK_SESSIONS, NOISE_MIN_OBS
from research.pb1_v3_2_face_failure_rca import PARENT_V32_SHA, SEMANTIC_RCA_N
from research.pb1_v4_implementation_correction import V4_LEGACY_LEAKED_IMPLEMENTATION
from research.pb1_v4_implementation_correction.definitions import machine_sha256 as corrected_machine_sha256
from research.pb1_v4_implementation_correction_rca import V4_CORRECTED_MACHINE_SHA256
from research.pb1_v4_machine_implementation.definitions import machine_sha256 as leaked_machine_sha256
from research.pb1_v4_opening_drive_location_reaccel_spec import CASE_READY as PARENT_READY_VERDICT
from research.pb1_v4_semantic_spec_clarification import CASE_READY as PARENT_CLARIFIED_VERDICT
from research.pb1_v4_semantic_spec_clarification.spec import spec_sha256
from research.run_20260914_day2_futures_plus_first_live_breadth_v1 import TRADING_DATE as LIVE_TRADING_DATE
from research.support_resistance_face_valid_first_interaction_rebuild_v1 import CONFIRM_ATR, ZONE_HALF_ATR

PROGRAM_ID = "PB1_V4_CLARIFIED_MACHINE_IMPLEMENTATION"
ANALYSIS_ID = "PB1_V4_CLARIFIED_MACHINE_IMPLEMENTATION_V1"
EXPECTED_SPEC_SHA256 = "d8df35df3d3e8338db98f2b4a73dc0942bc793bbce1499acf57c40796a01e4a6"
assert spec_sha256() == EXPECTED_SPEC_SHA256
assert PARENT_READY_VERDICT == "PB1_V4_SEMANTIC_SPEC_READY_V1"
assert PARENT_CLARIFIED_VERDICT == "PB1_V4_SEMANTIC_SPEC_CLARIFIED_V2"
assert V4_CORRECTED_MACHINE_SHA256 == "21fc72eb420afe5d21ff27bee3b0aa8c28cc6cb35d2a4a441544e4d7e5f5053e"
assert leaked_machine_sha256() == V4_LEGACY_LEAKED_IMPLEMENTATION
assert corrected_machine_sha256() == V4_CORRECTED_MACHINE_SHA256
assert PARENT_V32_SHA == "56edb2c2576805e46193528824fb61faebb5ae7c85be0201f0331e90f9a67f43"
assert SEMANTIC_RCA_N == 88
assert CONFIRM_ATR == 0.75
assert ZONE_HALF_ATR == 0.15
assert NOISE_LOOKBACK_SESSIONS == 20
assert NOISE_MIN_OBS == 10

PRIMARY_SETUP_TIMEFRAME = "5m"
V4_IS_1M_STRATEGY = False
ONE_M_CAN_CREATE_THESIS = False
OR_KNOWN_FROM = "09:15"
# Operational AM research window only. Not semantic thesis expiry. Not a 10:00 break cutoff.
OPERATIONAL_AM_LAST = "11:20"

OPENING_5M_MIN_OBS = 8
S0_RANGE_MIN = 0.75
S0_GAP_ATR_MIN = 0.35
S0_GAP_RANGE_MIN = 0.55
S0_ATR_RANGE_MIN = 0.20

# Semantic correspondence on 88 charts. Not profit-tuned. See calibrate.py.
ATR_SANITY_FRAC = 0.40
TRUE_DISP_MIN = 0.80
TRUE_RANGE_MIN = 1.00
TRUE_N_SAME_MIN = 2
TRUE_BODY_FRAC_MIN = 0.35
TRUE_COUNTER_FRAC = 0.55
LAST_BODY_MIN = 0.25
ONE_BAR_SHARE_MAX = 0.55
WEAK_BAR_BODY_MAX = 0.20
FAIL_VISIBLE_RANGE_MIN = 0.80
FAIL_COUNTER_BODY_FRAC = 0.40
WIDE_REJECTION_BODY_MAX = 0.25
FAIL_DRIVE_DISP_MIN = 0.80
FAIL_DRIVE_N_SAME_MIN = 2
FAIL_DRIVE_BODY_MIN = 0.30
FLAT_DISP_MAX = 0.50
FLAT_RANGE_MAX = 0.80
UNWIND_FRAC = 0.75
FAILED_ATTEMPT_N = 3
OR_RECROSS_CLOSES = 2
REPEATED_NO_EXPANSION_N = 3
CONFLUENCE_N1M = 1.5
# E1 inherited N1M correspondence. Not optimized. Not a new alpha.
E1_NET_N1M = 1.00
E1_RANGE_N1M = 0.90
E1_BODY_N1M = 0.55
EXEC_5M_DIRECT = "E0_5M_CONFIRMED_EXECUTION"
EXEC_1M_CONFIRMED = "E1_1M_TIMED_EXECUTION"

CASE_IMPLEMENTED = "PB1_V4_CLARIFIED_MACHINE_IMPLEMENTED_V1"
CASE_INCOMPLETE = "PB1_V4_CLARIFIED_MACHINE_IMPLEMENTATION_INCOMPLETE_V1"
CASE_BIND = "PB1_V4_CLARIFIED_MACHINE_BIND_FAILED_V1"
NEXT_AUDIT = "PB1_V4_CLARIFIED_MACHINE_SPEC_PARITY_AUDIT_V1"
NEXT_RCA = "PB1_V4_CLARIFIED_MACHINE_IMPLEMENTATION_RCA_V1"
NEXT_BIND = "REPAIR_PRIOR_BIND_THEN_RETRY_V1"

OLD_CONFIRMATION_OPENED = False
FROZEN_VALIDATION_OPENED = False
KABU50_APPLIED = False
PNL_OPTIMIZATION = False
THRESHOLD_OPTIMIZED = False
V4_1_CREATED = False
FEATURE_MINING = FEATURE_MINING_CLOSED
LIVE_DATE = LIVE_TRADING_DATE
_ = corrected_machine_sha256, leaked_machine_sha256
