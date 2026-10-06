"""Separate M3 shadow registration profile. Does not mutate Kabu."""
from __future__ import annotations

ANALYSIS_ID = "SECTOR_STATE_ALPHA_SHADOW_REGISTRATION_V1"
CASE_READY = "SECTOR_STATE_ALPHA_SHADOW_REGISTRATION_READY_V1"
CASE_BLOCKED = "SECTOR_STATE_ALPHA_SHADOW_REGISTRATION_BLOCKED_V1"
NEXT_READY = "ACTIVATE_SECTOR_STATE_ALPHA_SIGNAL_SHADOW_PROSPECTIVE_V1"
OWNER = "SECTOR_STATE_ALPHA_SHADOW_V1"
MODE = "PROSPECTIVE_ALPHA_SHADOW_CAPTURE"
PROFILE_MODE = "ALPHA_SHADOW_PROSPECTIVE_AM"
STANDARD_MODE = "PAPER_STANDARD_REGISTER_PROFILE"
ACTIVATION_FLAG = "--sector-state-alpha-shadow"
REGISTER_LIMIT = 50
SOURCE_DAY = "20260918"
M3_SHA256 = "40b0c1945ffc064227df52db7788d6f43e744fe6338aacbe5096e06bc11fdb17"
M3_TARGET_SHA256 = "506809673c4d458f1a993d41c3f6c53314f8f51c6d77c60d713534bf99a44ef5"
EXPECTED_IMPLEMENTATION_SHA256 = "c6f990c40fc4d2df6a385f259dd141fe521409e435ae1c82d94955f7c3937af2"
EXPECTED_DRIVER_SHA256 = "a294f0a06da865acf3aaa665d1eb726ebde8ef0e8a09b0ca8ca9a217f28852b5"
EXPECTED_STATE_SHA256 = "4e30d64f76305fbd2a57b9dba1ac1a669b689976f69d6420932ad16966b8c4ca"
EXPECTED_SCHEMA_SHA256 = "3003af983e6bb1b13a5c5eec6b392c650c52068b109b93eafadcc2ac1134ac12"

SECTOR_STATE_ALPHA_SHADOW_ENABLED = False
SECTOR_STATE_ALPHA_SHADOW_REGISTER_PROFILE_ENABLED = False
SECTOR_STATE_ALPHA_SHADOW_ORDERS_ENABLED = False

ACTIVATION_STEPS = (
    "verify_kabu_auth",
    "verify_trading_date",
    "verify_prospective_date_ge_20260924",
    "verify_no_conflicting_register_owner",
    "snapshot_current_register",
    "construct_selected_shadow_profile",
    "verify_n_le_50",
    "register_profile",
    "read_back_active_registration",
    "verify_all_m3_30",
    "start_capture",
    "verify_warmup",
    "enable_alpha_shadow_evaluation_at_0910",
)
WINDOW_END_STEPS = (
    "final_alpha_evaluation_1125",
    "window_end_if_active",
    "stop_shadow_1126",
    "snapshot_shadow_final_state",
    "restore_previous_registration_profile",
    "verify_restore_sha",
    "release_registration_owner",
)
