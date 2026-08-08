"""E1_X29 Sealed Prospective Observer Precommit."""

ANALYSIS_ID = "E1_X29_SEALED_PROSPECTIVE_OBSERVER"
DOCUMENT_ID = "E1_X29_PROSPECTIVE_ENTRY_EXIT_VALIDATION"
PRECOMMIT_ID = "X29_PRECOMMIT_FROZEN"
VERDICT_PRECOMMIT = "X29_PRECOMMIT_FROZEN"

SOURCE_X28C = "e1x28c_exec_20260807_224708_A"
LOGIC_MANIFEST_SHA = "3a18b414afe46a4209bf636a86565a1f23acabb5c8a93d06bbd0447a98c4f7db"
ASSIGNMENT_REGISTRY_SHA = "11ff956354e441e8e62c767bef0f06a0f7f3b65ca5e01b2823ffd2a92c39a040"
SEMANTIC_EXIT_REGISTRY_SHA = "f8b5dfd70858e97e211ac29562e216c2cf96c196c8bd402ca0c40bd6bd6ba6ef"
FAMILY_BASELINE_REGISTRY_SHA = "893fd2b75467a3d104d246b4c2dca558da290a05c2145b2bee90a6caf5b48f6b"
BOARD_MAPPING_SHA = "6adf923a0fa850e5882d217b44030cb36a3287c967c4710c8951b305808a0a97"

EXPECTED_SPECIFIC = 49
EXPECTED_FAMILY = 118
EXPECTED_SURVIVOR = 24
EXPECTED_EMERGENT = 25
EXPECTED_UNIQUE_MASKS = 6441

CONSUMED_ALPHA_DATES = (
    "20260721", "20260722", "20260723", "20260724", "20260727",
    "20260728", "20260729", "20260730", "20260731",
    "20260803", "20260804",
)
# 20260805+ already-captured risk-only must not be used as retrospective alpha
RISK_INFRASTRUCTURE_FROM = "20260805"

# JPX closed weekdays 2026 (national holidays + year-end/new-year market holidays)
JPX_HOLIDAYS_2026 = {
    "20260101", "20260102", "20260112",
    "20260211", "20260223",
    "20260320",
    "20260429",
    "20260504", "20260505", "20260506",
    "20260720",
    "20260811",
    "20260921", "20260922", "20260923",
    "20261012",
    "20261103", "20261123",
    "20261231",
}

INVALID_DAY_REASONS = (
    "capture_integrity_failure",
    "board_data_unavailable_globally",
    "clock_session_corruption",
    "observer_process_failure",
)

QUOTE_CONTRACT = {
    "entry_ask_raw": "Sell1.Price",
    "exit_bid_raw": "Buy1.Price",
    "window_sec": 5.0,
    "min_qty": 100.0,
    "freshness_sec": 5.0,
    "special_quote_block": True,
    "no_future_best": True,
    "no_mid": True,
    "no_currentprice_fill": True,
    "no_session_cross": True,
    "primary_trigger_mark": "CurrentPrice",
    "exit_basis": "actual_ask",
}

SUPPORT_GATE = {
    "executable_trades_min": 20,
    "valid_days_min": 3,
    "symbols_min": 5,
    "exit_coverage_given_entry_min": 0.70,
    "entry_common_episodes_min": 20,
    "specific_family_common_episodes_min": 20,
    "prospective_broad_support_days_min": 3,
}

BOOTSTRAP_RULES = {
    "iters": 2000,
    "unit": "cluster_id",
    "seed": 20260807,
}

FDR_POOLS = (
    "PROSPECTIVE_SPECIFIC_RETURN",
    "PROSPECTIVE_SPECIFIC_ENTRY",
    "PROSPECTIVE_PERSONALIZATION",
    "PROSPECTIVE_FAMILY_RETURN",
    "PROSPECTIVE_FAMILY_ENTRY",
)
