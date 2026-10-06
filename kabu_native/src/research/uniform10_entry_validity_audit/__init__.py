"""UNIFORM10 ENTRY rebuild validity audit. Offline only. No Runtime / C14 / activation."""
from __future__ import annotations

ANALYSIS_ID = "UNIFORM10_ENTRY_VALIDITY_AUDIT_V2"
TASK_LABEL = "OFFLINE_AUDIT_ONLY"
WAIT_SEC = 1.0
MAX_WORKERS = 2
NEW_FORWARD_N = 0
PRIMARY_TARGET = "FORWARD_MID_RETURN_600S"
C14_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14"

GROUP_A = "EXECUTABLE_AT_ANCHOR"
GROUP_B = "NON_EXECUTABLE_AT_ANCHOR_BUT_OPENS_WITHIN_1S"
GROUP_C = "NON_EXECUTABLE_AT_ANCHOR_AND_DOES_NOT_OPEN_WITHIN_1S"

# Fill-SoT partition used as the primary 3 groups:
# GROUP B = not executable at t0 but becomes is_executable_continuous_board
# inside (t0, t0+WAIT_SEC]. That is the wait-window tradable set.
# opening_transition is recorded separately (literal OpeningPrice appearance).
