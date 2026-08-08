"""E1_X7–X9 Research Program Closure — assembly only; no new computation."""

ANALYSIS_ID = "E1_X7_TO_X9_RESEARCH_PROGRAM_CLOSURE"
DOCUMENT_ID = "E1_X7_X8_X9_FINAL_CLOSURE"
FINAL_VERDICT = "E1_X7_X9_RESEARCH_PROGRAM_CLOSED_NO_ROBUST_STRATEGY"

# Expected canonical source identities (exact)
EXPECTED_SOURCES = {
    "pfq_design": {
        "run_id": "e1x7_pfq_20260804_080510",
        "dir": "e1_x7_pfq",
        "expected_verdict": "E1_X7_DESIGN_PERIOD_COMPLETE_PENDING_PROSPECTIVE",
        "canonical": True,
        "role": "PFQ design period source",
        "superseded": False,
    },
    "bridge_v2": {
        "run_id": "e1x7_pfq_bridge_v2_20260804_232049_A",
        "dir": "e1_x7_pfq_bridge_v2",
        "expected_verdict": "E1_X7_PFQ_ENTRY_SUPPORTED_EXIT_CAPTURE_LIMITATION",
        "canonical": True,
        "role": "Bridge Audit V2 — ENTRY path + EXIT capture limitation",
        "superseded": False,
    },
    "exit_gate_v2": {
        "run_id": "e1x7_pfq_exit_gate_v2_20260804_235659_A",
        "dir": "e1_x7_pfq_exit_gate_v2",
        "expected_verdict": "E1_X7_PFQ_EXIT_REVISION_BASELINE_CONFIRMED",
        "canonical": True,
        "role": "EXIT Gate Reconciliation V2 — sole revision baseline",
        "superseded": False,
    },
    "exit_revision": {
        "run_id": "e1x7_pfq_exit_rev_20260805_000745_A",
        "dir": "e1_x7_pfq_exit_revision",
        "expected_verdict": "E1_X7_PFQ_EXIT_REVISION_MECHANISM_FAILED",
        "canonical": True,
        "role": "Single EXIT Revision — mechanism failed; line closed",
        "superseded": False,
    },
    "symbol_leverage": {
        "run_id": "e1x8_symlev_20260805_004104_A",
        "dir": "e1_x8_symbol_leverage",
        "expected_verdict": "E1_X8_KIOXIA_THRESHOLD_LEVERAGE_SIGNAL_SURVIVES",
        "canonical": True,
        "role": "Threshold Symbol Leverage Audit",
        "superseded": False,
    },
    "universe_regime": {
        "run_id": "e1x9_univ_20260805_005802_A",
        "dir": "e1_x9_universe_regime",
        "expected_verdict": "E1_X9_NO_STABLE_UNIVERSE_REGIME_SEPARATION",
        "canonical": True,
        "role": "Universe Regime Audit",
        "superseded": False,
    },
}

SUPERSEDED_SOURCES = {
    "exit_gate_v1": {
        "run_id": "e1x7_pfq_exit_gate_20260804_235025_A",
        "dir": "e1_x7_pfq_exit_gate",
        "expected_verdict": "E1_X7_PFQ_MULTIPLE_EXIT_BASELINES_REVIEW_REQUIRED",
        "canonical": False,
        "role": "EXIT Gate V1 (historical only)",
        "superseded": True,
        "superseded_by": "SUPERSEDED_BY_EXIT_GATE_RECONCILIATION_V2",
        "reason": "profitable soft exits outside repairable denominator were mixed into repairable set",
    },
}

# Verdicts that must match exactly for closure (section 2)
REQUIRED_VERDICT_CHECKS = {
    "bridge_v2": "E1_X7_PFQ_ENTRY_SUPPORTED_EXIT_CAPTURE_LIMITATION",
    "exit_gate_v2": "E1_X7_PFQ_EXIT_REVISION_BASELINE_CONFIRMED",
    "exit_revision": "E1_X7_PFQ_EXIT_REVISION_MECHANISM_FAILED",
    "symbol_leverage": "E1_X8_KIOXIA_THRESHOLD_LEVERAGE_SIGNAL_SURVIVES",
    "universe_regime": "E1_X9_NO_STABLE_UNIVERSE_REGIME_SEPARATION",
}
