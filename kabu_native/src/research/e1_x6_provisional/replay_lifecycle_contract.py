"""Locked E1_X6 research replay lifecycle contract (analysis_mask partitions).

Verbatim SoT text for Plan 1.3 §Replay lifecycle and P1 lock.
"""
from __future__ import annotations

REPLAY_LIFECYCLE_CONTRACT_TEXT = """\
AM_PM_CARRY = NO (fresh session per analysis_mask partition day×AM|PM).
WINDOW_END_OPEN = WINDOW_CENSORED / WINDOW_END_OPEN_EXCLUDED — orphan exclude from completed PnL; no force-close; no post-window exit grace.
EXIT_EVENT_SCOPE = every canonical board event in partition (FE+EXIT every event); ENTRY = score samples only (5s+STATE_CHANGE).
Partition scope = Source Manifest valid_window [start,end] inclusive for that AM/PM mask.
Events after valid_end are NOT processed for that partition (so EXIT 11:29 when mask ends 11:25 → position becomes WINDOW_CENSORED, not completed MAX_HOLD).
E1 continuous SESSION_CLOSE 11:30/15:30 may only fire if ts is still inside partition; for AM mask ending 11:25 it never fires.
No silent carry to next trading day.
Clock conflict AmPm 11:25 vs E1 11:30 is RESOLVED for research as: partition boundary = analysis_mask valid_window (canonical orphan rule). Document this in Plan 1.3.
"""

EVALUATION_MODE_REQUIRED = "FULL_CANONICAL_EVENT_REPLAY"

# Forbidden legacy modes (must not appear as evaluation_mode in final economics)
FORBIDDEN_EVALUATION_MODES = (
    "PORTFOLIO_REPLAY_ON_LABELED_SCORE_ROWS",
)
