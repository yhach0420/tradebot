"""
Phase442 — No Progress Exit runtime policy (production EXIT).

Policy: linmfe_t900_i0p6_s0p05_c0p8_p0p3
  start: 900s
  required MFE: 0.6 + 0.05 per 5min, cap 0.8
  current pnl < 0.3%
"""

from __future__ import annotations

from dataclasses import dataclass

PHASE442_POLICY_KEY = "linmfe_t900_i0p6_s0p05_c0p8_p0p3"
NO_PROGRESS_EXIT_REASON = "no_progress_exit"

START_TIME_SEC = 900.0
INITIAL_MFE_PCT = 0.6
SLOPE_PER_5MIN = 0.05
MAX_MFE_CAP_PCT = 0.8
MAX_PNL_PCT = 0.3


@dataclass(frozen=True)
class NoProgressExitConfig:
    enabled: bool = False
    policy_key: str = PHASE442_POLICY_KEY
    start_time_sec: float = START_TIME_SEC
    initial_mfe_pct: float = INITIAL_MFE_PCT
    slope_per_5min: float = SLOPE_PER_5MIN
    max_mfe_cap_pct: float = MAX_MFE_CAP_PCT
    max_pnl_pct: float = MAX_PNL_PCT


def default_no_progress_exit_config(*, enabled: bool) -> NoProgressExitConfig:
    return NoProgressExitConfig(enabled=enabled)


def required_mfe_threshold_pct(elapsed_sec: float, *, cfg: NoProgressExitConfig | None = None) -> float | None:
    c = cfg or NoProgressExitConfig(enabled=True)
    if elapsed_sec < c.start_time_sec:
        return None
    steps_5m = (elapsed_sec - c.start_time_sec) / 300.0
    req = c.initial_mfe_pct + c.slope_per_5min * steps_5m
    return min(c.max_mfe_cap_pct, req)


def no_progress_exit_triggered(
    elapsed_sec: float,
    peak_mfe_pct: float,
    current_pnl_pct: float,
    *,
    cfg: NoProgressExitConfig | None = None,
) -> bool:
    """True when stagnation rule fires (hold>=start, peak MFE below schedule, pnl below cap)."""
    c = cfg or NoProgressExitConfig(enabled=True)
    req_mfe = required_mfe_threshold_pct(elapsed_sec, cfg=c)
    if req_mfe is None:
        return False
    return float(peak_mfe_pct) < req_mfe and float(current_pnl_pct) < c.max_pnl_pct
