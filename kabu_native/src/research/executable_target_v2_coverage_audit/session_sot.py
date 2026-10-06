"""TSE cash-equity session reference vs repository constants. Audit-only. No Runtime write."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

# Current TSE cash equity (audit reference SoT; not applied to PRIMARY this run).
TSE_AM_OPEN = (9, 0)
TSE_AM_CLOSE = (11, 30)
TSE_PM_OPEN = (12, 30)
TSE_ZARABA_END = (15, 25)  # continuous / Zaraba
TSE_CLOSING_AUCTION_START = (15, 25)
TSE_MARKET_CLOSE = (15, 30)

# V1R / Dual Lane / e1_x22 values actually used by EXECUTABLE TARGET V2.
V1R_AM_CLOSE = (11, 30)
V1R_PM_CLOSE = (15, 0)

BOARD_FRESHNESS_SEC = 5.0
HORIZON_SEC = 600.0


def hm_minutes(h: int, m: int) -> int:
    return int(h) * 60 + int(m)


def tse_phase_hm(h: int, m: int) -> str:
    t = hm_minutes(h, m)
    if hm_minutes(*TSE_AM_OPEN) <= t < hm_minutes(*TSE_AM_CLOSE):
        return "AM_CONTINUOUS"
    if hm_minutes(*TSE_AM_CLOSE) <= t < hm_minutes(*TSE_PM_OPEN):
        return "LUNCH"
    if hm_minutes(*TSE_PM_OPEN) <= t < hm_minutes(*TSE_ZARABA_END):
        return "PM_CONTINUOUS_ZARABA"
    if hm_minutes(*TSE_CLOSING_AUCTION_START) <= t < hm_minutes(*TSE_MARKET_CLOSE):
        return "PM_CLOSING_AUCTION"
    if t == hm_minutes(*TSE_MARKET_CLOSE):
        return "PM_MARKET_CLOSE"
    if t < hm_minutes(*TSE_AM_OPEN):
        return "PREOPEN"
    return "AFTER_HOURS"


def plus_sec_hm(h: int, m: int, sec: float) -> tuple[int, int]:
    """Add seconds to HH:MM. HORIZON_SEC=600 is exactly +10 minutes."""
    tot = int(h) * 3600 + int(m) * 60 + int(round(float(sec)))
    return tot // 3600, (tot % 3600) // 60


def tse_continuous_at_hm(h: int, m: int) -> bool:
    return tse_phase_hm(h, m) in {"AM_CONTINUOUS", "PM_CONTINUOUS_ZARABA"}


def v1r_endpoint_in_session(h0: int, m0: int, sec: float = HORIZON_SEC) -> bool:
    """V2 PRIMARY calendar: t0+sec must be <= AM 11:30 / PM 15:00."""
    h1, m1 = plus_sec_hm(h0, m0, sec)
    t1 = hm_minutes(h1, m1)
    if int(h0) < 12:
        return t1 <= hm_minutes(*V1R_AM_CLOSE)
    return t1 <= hm_minutes(*V1R_PM_CLOSE)


def tse_phase_epoch(day: str, t: float) -> str:
    dt = datetime.fromtimestamp(float(t), JST)
    return tse_phase_hm(dt.hour, dt.minute)


def v1r_session_of_t0(h: int, m: int) -> Optional[str]:
    t = hm_minutes(h, m)
    if hm_minutes(9, 0) <= t <= hm_minutes(11, 30):
        return "AM"
    if hm_minutes(12, 30) <= t <= hm_minutes(15, 0):
        return "PM"
    return None


SESSION_INVENTORY: list[dict[str, Any]] = [
    {
        "file": "src/research/e1_x22_actual_exit_factory/__init__.py",
        "function_or_constant": "PM_SESSION_CLOSE_HM",
        "current_value": "15:00",
        "semantic_meaning": "e1_x22 path reconstruction session end; Dual Lane SESSION_CLOSE clock via session_end_epoch",
        "classification": "D_LEGACY_OLD_TSE_CLOSE + C_STRATEGY_ENTRY_CUTOFF",
        "used_by_target_v2": True,
        "note": "Comment: Session ends aligned with X14/X19 labels. Pre-2024 TSE PM close was 15:00.",
    },
    {
        "file": "src/research/e1_x22_actual_exit_factory/__init__.py",
        "function_or_constant": "AM_SESSION_CLOSE_HM",
        "current_value": "11:30",
        "semantic_meaning": "AM continuous / lunch start. Matches current TSE AM close.",
        "classification": "B_CONTINUOUS_MARKET_CLOSE",
        "used_by_target_v2": True,
        "note": "AM 11:30 is still correct TSE AM end.",
    },
    {
        "file": "src/research/e1_x22_actual_exit_factory/paths.py",
        "function_or_constant": "session_end_epoch",
        "current_value": "AM 11:30 / PM 15:00 JST epoch",
        "semantic_meaning": "Converts AM/PM label to session-end unix time",
        "classification": "C_STRATEGY_ENTRY_CUTOFF via 15:00",
        "used_by_target_v2": True,
        "note": "EXECUTABLE TARGET V2 continuous_session_end() calls this.",
    },
    {
        "file": "src/research/executable_target_v2_b_threshold/contract.py",
        "function_or_constant": "continuous_session_end",
        "current_value": "delegates to session_end_epoch (PM 15:00)",
        "semantic_meaning": "PRIMARY t+600 eligibility: target_time <= this epoch",
        "classification": "D_LEGACY_OLD_TSE_CLOSE misused as market continuous close",
        "used_by_target_v2": True,
        "note": "Docstring says V1R canonical continuous close AM 11:30 / PM 15:00. Not PBv2 15:23/15:30.",
    },
    {
        "file": "src/small_paper/v1r_live_dual_lane.py",
        "function_or_constant": "session_end_for_position",
        "current_value": "AM 11:30 / PM 15:00",
        "semantic_meaning": "Frozen V1R SESSION_CLOSE sweep. Last valid executable Buy1 at/before session_end.",
        "classification": "C_STRATEGY_ENTRY_CUTOFF",
        "used_by_target_v2": False,
        "note": "Intentional V1R EXIT cutoff. Docstring: Not PBv2 11:25/15:23. Must not be copied as TSE market close.",
    },
    {
        "file": "src/small_paper/v1r_primary_runtime.py",
        "function_or_constant": "CLOCK_GRID last slot",
        "current_value": "15:00",
        "semantic_meaning": "Last Runtime ENTRY fire. Not market close.",
        "classification": "C_STRATEGY_ENTRY_CUTOFF",
        "used_by_target_v2": False,
        "note": "Runtime CLOCK frozen. 15:00 is last admit, not TSE close.",
    },
    {
        "file": "src/research/uniform10_entry_rebuild/__init__.py",
        "function_or_constant": "UNIFORM10_PM",
        "current_value": "12:40 ... 15:20 step 10 including 15:00, 15:10, 15:20",
        "semantic_meaning": "Research-only 31-anchor grid. Not Runtime CLOCK.",
        "classification": "E_OTHER",
        "used_by_target_v2": True,
        "note": "15:10/15:20 exist as research anchors after V1R last clock 15:00.",
    },
    {
        "file": "src/research/fixed_anchor_mechanism_audit_p3_0/grid.py",
        "function_or_constant": "PM_END",
        "current_value": "15:00",
        "semantic_meaning": "session_of_epoch support for shifted CLOCK. Claimed JPX continuous session.",
        "classification": "D_LEGACY_OLD_TSE_CLOSE",
        "used_by_target_v2": False,
        "note": "Comment incorrectly calls 15:00 JPX continuous session.",
    },
    {
        "file": "src/research/anchor_timing_robustness/__init__.py",
        "function_or_constant": "PM_END",
        "current_value": "15:00",
        "semantic_meaning": "Same Dual Lane / claimed JPX continuous bound",
        "classification": "D_LEGACY_OLD_TSE_CLOSE",
        "used_by_target_v2": False,
        "note": "",
    },
    {
        "file": "src/research/e1_x14_board_independent_signal/__init__.py",
        "function_or_constant": "PM_END",
        "current_value": "15:00",
        "semantic_meaning": "X14 research session bound (ancestor of e1_x22 15:00)",
        "classification": "D_LEGACY_OLD_TSE_CLOSE",
        "used_by_target_v2": False,
        "note": "e1_x22 comment: aligned with X14/X19 labels.",
    },
    {
        "file": "src/research/e1_x6_provisional/replay_lifecycle_contract.py",
        "function_or_constant": "REPLAY_LIFECYCLE_CONTRACT_TEXT",
        "current_value": "E1 continuous SESSION_CLOSE 11:30/15:30",
        "semantic_meaning": "E1 research partition close uses 15:30",
        "classification": "A_MARKET_SESSION_CLOSE",
        "used_by_target_v2": False,
        "note": "E1/X6 knows 15:30. V1R Dual Lane did not adopt it.",
    },
    {
        "file": "src/small_paper/session_schedule.py",
        "function_or_constant": "AFTERNOON_END",
        "current_value": "15:30",
        "semantic_meaning": "Full-day live dry-run bucket end",
        "classification": "A_MARKET_SESSION_CLOSE",
        "used_by_target_v2": False,
        "note": "PBv2/live path. Isolated from V1R Primary.",
    },
    {
        "file": "src/small_paper/config.py",
        "function_or_constant": "live_session_end",
        "current_value": "15:30",
        "semantic_meaning": "PBv2 YAML live.session_end default",
        "classification": "A_MARKET_SESSION_CLOSE",
        "used_by_target_v2": False,
        "note": "Must not alter V1R Primary CLOCK/EXIT.",
    },
    {
        "file": "src/small_paper/am_pm_session_policy.py",
        "function_or_constant": "AmPmSessionPolicy.afternoon session_end/force_close",
        "current_value": "15:23 / entry_stop 15:18",
        "semantic_meaning": "PBv2 AM/PM shadow force-close before close",
        "classification": "C_STRATEGY_ENTRY_CUTOFF",
        "used_by_target_v2": False,
        "note": "Not V1R. Dual Lane docstring explicitly not PBv2 15:23.",
    },
    {
        "file": "src/universe/am_pm_universe.py",
        "function_or_constant": "session_close_design.afternoon_session_close_time",
        "current_value": "15:25",
        "semantic_meaning": "Phase114 design: close before closing auction. Not production.",
        "classification": "B_CONTINUOUS_MARKET_CLOSE",
        "used_by_target_v2": False,
        "note": "Matches TSE Zaraba end 15:25. Design-only.",
    },
    {
        "file": "src/small_paper/capture_completeness_gate.py",
        "function_or_constant": "PM_END / EARLY_END / FINALIZE",
        "current_value": "15:20 / 15:00 / 15:35",
        "semantic_meaning": "Capture quality windows, not trade session close",
        "classification": "E_OTHER",
        "used_by_target_v2": False,
        "note": "EARLY_END=15:00 is capture completeness, not market SoT.",
    },
    {
        "file": "src/small_paper/certification_input_coverage.py",
        "function_or_constant": "PM_SESSION / SESSION_CLOSE spans",
        "current_value": "PM (12:30-15:10); SESSION_CLOSE (14:50-15:35)",
        "semantic_meaning": "Certification coverage buckets",
        "classification": "E_OTHER",
        "used_by_target_v2": False,
        "note": "",
    },
    {
        "file": "src/research/e1_x28_executable_joint/__init__.py",
        "function_or_constant": "BOARD_FRESHNESS_SEC",
        "current_value": "5.0",
        "semantic_meaning": "Canonical board freshness for executable quotes / V2 last_executable_mid",
        "classification": "E_OTHER",
        "used_by_target_v2": True,
        "note": "Same 5s as V1R BOARD_FRESHNESS_SEC_V1R.",
    },
    {
        "file": "src/small_paper/v1r_primary_runtime.py",
        "function_or_constant": "CLOCK_GRID",
        "current_value": "09:05,09:15,09:25,09:40,10:00,10:20,10:40,11:00,12:40,13:00,13:20,13:40,14:00,14:20,14:40,15:00",
        "semantic_meaning": "Runtime ENTRY fire grid. Last PM slot 15:00. No 15:10/15:20.",
        "classification": "C_STRATEGY_ENTRY_CUTOFF",
        "used_by_target_v2": False,
        "note": "Frozen. UNIFORM10 research grid is wider than CLOCK_GRID.",
    },
    {
        "file": "src/small_paper/session_schedule.py",
        "function_or_constant": "MORNING_END / MIDDAY_END / AFTERNOON_END",
        "current_value": "11:00 / 12:30 / 15:30",
        "semantic_meaning": "Live dry-run session_bucket() partitions. MORNING_END=11:00 is not AM close.",
        "classification": "E_OTHER",
        "used_by_target_v2": False,
        "note": "AFTERNOON_END=15:30 matches current TSE market close. Isolated from V1R Primary.",
    },
    {
        "file": "src/small_paper/market_ingress_service.py",
        "function_or_constant": "MARKET_PM_END",
        "current_value": "15:30",
        "semantic_meaning": "Ingress / capture market-hours end",
        "classification": "A_MARKET_SESSION_CLOSE",
        "used_by_target_v2": False,
        "note": "Capture continues to TSE close. V2 PRIMARY does not.",
    },
    {
        "file": "src/research/dynamic_anchor_p2_0b/contract.py",
        "function_or_constant": "session_end_epoch / PM_END",
        "current_value": "PM complete 15:00; latest t0 14:50 for +600s",
        "semantic_meaning": "P2 confirmation window uses Dual Lane 15:00 as continuous end",
        "classification": "D_LEGACY_OLD_TSE_CLOSE",
        "used_by_target_v2": False,
        "note": "Same 15:00 ancestor as V2 continuous_session_end.",
    },
    {
        "file": "src/research/trailing10_dynamic_anchor_p2_4a/binding.py",
        "function_or_constant": "session_end",
        "current_value": "Dual Lane SESSION_CLOSE AM 11:30 / PM 15:00; PM window 12:30-15:00",
        "semantic_meaning": "Documents [g-600,g] must lie in one continuous session ending 15:00",
        "classification": "D_LEGACY_OLD_TSE_CLOSE + C_STRATEGY_ENTRY_CUTOFF",
        "used_by_target_v2": False,
        "note": "Strategy/research window, not TSE Zaraba 15:25.",
    },
    {
        "file": "src/research/e1_x34a_execution_policy/executable_board.py",
        "function_or_constant": "is_executable_continuous_board",
        "current_value": "AskSign/BidSign 0101 + CurrentPriceStatus in {1,2} + opened + volume",
        "semantic_meaning": "Execution SoT: continuous Zaraba board, not itayose/special",
        "classification": "E_OTHER",
        "used_by_target_v2": True,
        "note": "Fill gate unchanged this audit. Board.executable[] is this gate.",
    },
]


FIFTEEN_00_CLASSIFICATION: list[dict[str, Any]] = [
    {
        "location": "v1r_primary_runtime.CLOCK_GRID last slot",
        "class": "C_STRATEGY_ENTRY_CUTOFF",
        "is_market_session_close": False,
        "is_continuous_market_close": False,
        "affects_primary_v2_endpoint": False,
        "note": "Intentional last Runtime ENTRY fire. Must not be copied as TSE close.",
    },
    {
        "location": "v1r_live_dual_lane.session_end_for_position",
        "class": "C_STRATEGY_ENTRY_CUTOFF",
        "is_market_session_close": False,
        "is_continuous_market_close": False,
        "affects_primary_v2_endpoint": False,
        "note": "Intentional Dual Lane SESSION_CLOSE / EXIT sweep. Frozen. Not market SoT.",
    },
    {
        "location": "e1_x22 PM_SESSION_CLOSE_HM via contract.continuous_session_end",
        "class": "D_LEGACY_OLD_TSE_CLOSE",
        "is_market_session_close": False,
        "is_continuous_market_close": False,
        "affects_primary_v2_endpoint": True,
        "note": "V2 PRIMARY t+600 eligibility uses this as if it were continuous market end. Pre-2024 TSE PM close was 15:00; current TSE Zaraba ends 15:25 / market 15:30.",
    },
    {
        "location": "capture_completeness_gate.EARLY_END",
        "class": "E_OTHER",
        "is_market_session_close": False,
        "is_continuous_market_close": False,
        "affects_primary_v2_endpoint": False,
        "note": "Capture quality flag, not session close.",
    },
]
