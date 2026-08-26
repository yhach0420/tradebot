"""P3-0R: reconcile Fixed selection independent-fill diagnostic vs P1 267.

Research only. Does not retune clock, ENTRY, EXIT, or Runtime.
Clock results remain frozen from P3-0.
Label: REUSED_HISTORY_MECHANISM_DIAGNOSTIC.
"""

from research.dynamic_anchor_p2_3 import FULL14, PREDECLARED_TOP3
from research.fixed_anchor_mechanism_audit_p3_0 import (
    CLOCK_EXACT_TIME_SUPPORTED,
    CLOCK_NOT_UNIQUELY_SPECIAL,
)

ANALYSIS_ID = "P3_0R_FIXED_SELECTION_DIAGNOSTIC_RECONCILE"
DOCUMENT_ID = "P3_0R_FILL_CONTRACT_RECONCILE"
TASK_LABEL = "REUSED_HISTORY_MECHANISM_DIAGNOSTIC"

P3_0_SELECTED_N = 1115
P3_0_INDEPENDENT_FILL_N = 137
P1_FILLS = 267
P2_3_SELECTED = 1115
P2_3_ADMITTED = 1073

CLOCK_FULL14 = CLOCK_EXACT_TIME_SUPPORTED
CLOCK_REST11 = CLOCK_NOT_UNIQUELY_SPECIAL

SELECTION_SUPPORTED = "SELECTION_SUPPORTED"
SELECTION_NOT_SUPPORTED = "SELECTION_NOT_SUPPORTED"
SELECTION_MIXED = "SELECTION_MIXED"

MECH_CLOCK_AND_SELECTION = "CLOCK_AND_SELECTION"
MECH_SEL_DOMINANT_CLOCK_TOP3 = "SELECTION_DOMINANT_CLOCK_TOP3_SENSITIVE"
MECH_CLOCK_DOMINANT = "CLOCK_DOMINANT"
MECH_MIXED = "MIXED"
MECH_NONE = "NO_CLEAR_MECHANISM"

VERDICT_OK = "P3_0R_SELECTION_DIAGNOSTIC_RECONCILED"
VERDICT_ISSUE = "P3_0R_MATERIAL_DIAGNOSTIC_ISSUE_FOUND"
VERDICT_BLOCKED = "P3_0R_BLOCKED"

MISMATCH_CLASSES = (
    "MATCH",
    "CANDIDATE_MISSING",
    "ANCHOR_TIME_MISMATCH",
    "LIMIT_PRICE_MISMATCH",
    "WAIT_WINDOW_MISMATCH",
    "ASK_EVENT_SELECTION_MISMATCH",
    "SEQUENCE_BOUNDARY_MISMATCH",
    "SESSION_BOUNDARY_MISMATCH",
    "OTHER",
)

ROOT_CAUSE_COMPACT = (
    "Two diagnostic reconstruction defects, not a Runtime trading defect. "
    "(1) ingest_push compact_tail(20000) drops early-session ticks before P3-0 "
    "post-stream independent fill, so fill_n under-counts live fills. "
    "(2) P3-0 SELECTED is a post-stream simulate_joint rescore gated on "
    "feature_evaluable of the compacted board, not harvest-time "
    "ANCHOR_SYMBOL_SNAPSHOT.admitted (P2-3 selected). Canonical fills can sit "
    "on actual_admitted=true / actual_trade=true with selected=false."
)

MAX_WORKERS = 2
LOT_QTY = 100
FULL14 = FULL14
PREDECLARED_TOP3 = PREDECLARED_TOP3
REST11 = tuple(d for d in FULL14 if d not in PREDECLARED_TOP3)
