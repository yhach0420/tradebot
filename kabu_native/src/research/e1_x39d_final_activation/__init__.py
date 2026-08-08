"""E1_X39D V1R Paper Primary Final Activation Closure."""

ANALYSIS_ID = "E1_X39D_V1R_PAPER_PRIMARY_FINAL_ACTIVATION"
DOCUMENT_ID = "E1_X39D_FINAL_ACTIVATION"

V1R_SHA = "dfd311d4dc32a802b8e55f6d28d75a2db12d4192a71fb53b48d5308573a58e0a"
MODEL_ARTIFACT_SHA = "f63f7f88e9ff6ea5b84a89b0949baa76166d697525620287a7c230f821e7356b"
UNIVERSE_BINDING_SHA = "45b2fb20d02abbe7d557a55fecc87da3e7c19126eb7415ce9bdc4579aca39fee"
PRECOMMIT_U1_SHA = "ebe2b86ca881dfe94d8af986e8689481b40f1e013ad64bc4d645f485b1da625b"
OLD_PRECOMMIT_SHA = "0504496c16bae635a2552524b24913d244799c3e8d5003b7655df0b0b3ba2b4c"

X38_RUN_ID = "e1x38_wiring_20260808_224446_A"
X39_RUN_ID = "e1x39_act_20260808_233044_A"
X39B_RUN_ID = "e1x39b_bridge_20260808_235351_A"
X39C_RUN_ID = "e1x39c_conc_20260809_002240_A"

UNIVERSE_CONTRACT = "DAY_FIXED_AM_RUNTIME_UNIVERSE_V1"
FORBIDDEN_FROM = "20260810"

PRIMARY_ROLE = "PAPER_PRIMARY"
PBV2_ROLE = "SHADOW_ONLY"
CAPITAL_1M_ROLE = "SHADOW_ONLY_DIAGNOSTIC"
INITIAL_1M_CASH = 1_000_000.0

VERDICT_READY = "E1_X39D_V1R_PAPER_PRIMARY_ACTIVATION_READY"
VERDICT_BLOCKED = "E1_X39D_ACTIVATION_BLOCKED"

STARTUP_ORDER = (
    "1_universe_prebuild_resolve",
    "2_kabu_readonly_readiness",
    "3_registration",
    "4_capture_ONLINE",
    "5_v1r_model_precommit_universe_sha_verify",
    "6_recovery",
    "7_rolling_state_initialization",
    "8_heartbeat",
    "9_v1r_primary_observer",
    "10_pbv2_shadow",
    "11_1m_shadow",
    "12_0900_market_ingest",
    "13_fixed_anchor_wait",
)

NOTIFY_PREFIXES = {
    "entry": "[V1R PAPER ENTRY]",
    "fill": "[V1R PAPER FILL]",
    "expired": "[V1R PAPER EXPIRED]",
    "exit": "[V1R PAPER EXIT]",
    "cap_blocked": "[V1R PAPER CAP BLOCKED]",
    "latency_warning": "[V1R PAPER LATENCY WARNING]",
    "pbv2": "[PBV2 SHADOW]",
    "capital_1m": "[V1R 1M SHADOW]",
}

CHECKPOINTS = {
    "EARLY_DIAGNOSTIC_ONLY": 5,
    "PRIMARY": 10,
    "EXTENDED": 20,
}
