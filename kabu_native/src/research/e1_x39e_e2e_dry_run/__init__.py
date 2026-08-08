"""E1_X39E V1R 20260810 Paper Trade Final End-to-End Dry Run (demo only)."""

ANALYSIS_ID = "E1_X39E_V1R_20260810_E2E_DRY_RUN"
DOCUMENT_ID = "E1_X39E_E2E_DRY_RUN"

V1R_SHA = "dfd311d4dc32a802b8e55f6d28d75a2db12d4192a71fb53b48d5308573a58e0a"
MODEL_ARTIFACT_SHA = "f63f7f88e9ff6ea5b84a89b0949baa76166d697525620287a7c230f821e7356b"
UNIVERSE_BINDING_SHA = "45b2fb20d02abbe7d557a55fecc87da3e7c19126eb7415ce9bdc4579aca39fee"
PRECOMMIT_U1_SHA = "ebe2b86ca881dfe94d8af986e8689481b40f1e013ad64bc4d645f485b1da625b"
ACTIVATION_SHA = "3f567810afb6cef713021f543d2b0fae7f4856ea4bff131fe0d91e903cc70801"

UNIVERSE_CONTRACT = "DAY_FIXED_AM_RUNTIME_UNIVERSE_V1"
FORBIDDEN_FROM = "20260810"
# Synthetic demo calendar day — NOT real 20260810 market data
DEMO_DAY = "20990728"
DEMO_MARKER = "V1R_E2E_DRY_RUN_TEST"

WAIT_SEC = 1.0
EXIT_HOLD_SEC = 600.0
POSITION_CAP = 5
LOT_QTY = 100
BOARD_FRESHNESS_SEC = 5.0
MIN_QTY = 100.0

FEATURE_ORDER = (
    "spread_bps",
    "imbalance",
    "mid_ret_60s",
    "mid_ret_180s",
    "event_rate_60s",
    "log_bid_qty",
)

# Day-fixed demo AM universe (>=6)
DEMO_UNIVERSE = ("1001", "1002", "1003", "1004", "1005", "1006", "1007")

VERDICT_READY = "V1R_20260810_END_TO_END_DRY_RUN_READY"
VERDICT_BLOCKED = "V1R_20260810_STARTUP_BLOCKED"

NOTIFY_PREFIXES = {
    "entry": "[V1R PAPER ENTRY]",
    "fill": "[V1R PAPER FILL]",
    "expired": "[V1R PAPER EXPIRED]",
    "exit": "[V1R PAPER EXIT]",
    "cap_blocked": "[V1R PAPER CAP BLOCKED]",
    "pbv2": "[PBV2 SHADOW]",
    "capital_1m": "[V1R 1M SHADOW]",
}

STARTUP_SEQUENCE = (
    "universe_resolve",
    "kabu_readonly_readiness_mock",
    "registration_demo",
    "capture_demo_ONLINE",
    "sha_verify",
    "recovery_bind",
    "rolling_state_init",
    "heartbeat",
    "v1r_primary_observer",
    "pbv2_shadow",
    "1m_shadow",
)

LEDGERS = (
    "V1R_RESEARCH_PROSPECTIVE_TEST",
    "V1R_OPERATIONAL_REALIZABLE_TEST",
    "PBV2_SHADOW_TEST",
    "V1R_1M_SHADOW_TEST",
)
