"""E1_X38 Frozen Strategy Operational Wiring & Latency Qualification."""

ANALYSIS_ID = "E1_X38_OPERATIONAL_WIRING_LATENCY_QUALIFICATION"
DOCUMENT_ID = "E1_X38_OPERATIONAL_WIRING"

V1R_SHA = "dfd311d4dc32a802b8e55f6d28d75a2db12d4192a71fb53b48d5308573a58e0a"
MODEL_ARTIFACT_SHA = "f63f7f88e9ff6ea5b84a89b0949baa76166d697525620287a7c230f821e7356b"
PRECOMMIT_SHA = "0504496c16bae635a2552524b24913d244799c3e8d5003b7655df0b0b3ba2b4c"
ENTRY_SHA = "f2887bb2be539cc173aee438a43ee8afb8cfa2b8c31380937ecd843e90dd9b29"
ANCHOR_SHA = "4a2f176ef6f52458cb0e5b38764275e6ddafc01e1849693965b116089514eac2"
EXEC_SHA = "040fa4b061e575d3f6cdb2a11ffd3f862da5351b298567b31363de923a590869"
EXIT_SHA = "2c9fcc6e92971c252c8df93716066dda515fcbff0283d748b03293379c5eb62c"

FORBIDDEN_FROM = "20260810"
WAIT_SEC = 1.0
POSITION_CAP = 5
LOT_QTY = 100

# Engineering latency guardrails (not strategy thresholds)
P95_DECISION_MS_TARGET = 100.0
MAX_DECISION_MS_TARGET = 250.0

FEATURE_ORDER = (
    "spread_bps",
    "imbalance",
    "mid_ret_60s",
    "mid_ret_180s",
    "event_rate_60s",
    "log_bid_qty",
)

PBV2_ROLE = "SHADOW_ONLY"
CAPITAL_1M_ROLE = "SHADOW_ONLY_DIAGNOSTIC"

VERDICT_READY = "E1_X38_OPERATIONAL_WIRING_READY"
VERDICT_LATENCY_OPT = "E1_X38_WIRING_LATENCY_REQUIRES_OPTIMIZATION"
VERDICT_FAIL = "E1_X38_OPERATIONAL_WIRING_FAILED"

TIMESTAMP_FIELDS = (
    "market_event_time",
    "local_receive_time",
    "anchor_signal_time",
    "snapshot_ready_time",
    "feature_ready_time",
    "score_ready_time",
    "admission_decision_time",
    "simulated_order_active_time",
    "fill_time",
    "exit_target_time",
    "exit_time",
    "notification_enqueue_time",
    "notification_sent_time",
)
