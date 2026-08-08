"""X26 EXIT library: common controls + family exits; Discovery trigger simulation."""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

import numpy as np

from research.e1_x22_actual_exit_factory.paths import session_end_epoch

from . import EVENT_PRIORITY, TOUCH_EPS

NATIVE = Path(__file__).resolve().parents[3]
THIS_FILE = Path(__file__).resolve()


@dataclass
class ExitSpec:
    exit_id: str
    path_family: Optional[str]
    variant: Optional[str]
    stop_bps: Optional[float]  # magnitude; applied as -stop
    target_bps: Optional[float]
    trail_activation_bps: Optional[float]
    giveback_bps: Optional[float]
    giveback_mode: Optional[str]
    no_progress_sec: Optional[float]
    max_hold_sec: float
    no_progress_mfe_bps: Optional[float] = 5.0
    no_progress_abs_ret_bps: Optional[float] = 5.0
    is_control: bool = False
    notes: str = ""


def common_controls() -> list[ExitSpec]:
    return [
        ExitSpec(
            "CONTROL_SHORT_TOUCH", None, "CONTROL",
            stop_bps=10.0, target_bps=10.0, trail_activation_bps=None, giveback_bps=None,
            giveback_mode=None, no_progress_sec=None, max_hold_sec=300.0,
            no_progress_mfe_bps=None, no_progress_abs_ret_bps=None,
            is_control=True, notes="source=EX_TOUCH_10_10_MAX300; diagnostic only",
        ),
        ExitSpec(
            "CONTROL_HOLD_300", None, "CONTROL",
            stop_bps=None, target_bps=None, trail_activation_bps=None, giveback_bps=None,
            giveback_mode=None, no_progress_sec=None, max_hold_sec=300.0,
            no_progress_mfe_bps=None, no_progress_abs_ret_bps=None,
            is_control=True, notes="session_close preferred at boundary",
        ),
        ExitSpec(
            "CONTROL_HOLD_900", None, "CONTROL",
            stop_bps=None, target_bps=None, trail_activation_bps=None, giveback_bps=None,
            giveback_mode=None, no_progress_sec=None, max_hold_sec=900.0,
            no_progress_mfe_bps=None, no_progress_abs_ret_bps=None,
            is_control=True, notes="session_close preferred at boundary",
        ),
        ExitSpec(
            "CONTROL_HOLD_1800", None, "CONTROL",
            stop_bps=None, target_bps=None, trail_activation_bps=None, giveback_bps=None,
            giveback_mode=None, no_progress_sec=None, max_hold_sec=1800.0,
            no_progress_mfe_bps=None, no_progress_abs_ret_bps=None,
            is_control=True, notes="session_close preferred at boundary",
        ),
    ]


def pbv2_control_status() -> dict[str, Any]:
    """
    PBv2 in this repo is primarily an ENTRY selection / guard stack plus
    board-dynamic structural trailing_mfe EXIT. Formal CurrentPrice-only parity
    for a CONTROL_PBv2 EXIT is not established (see X21 CANONICAL_EXIT_PARITY_NOT_ESTABLISHED).
    Do not invent a substitute.
    """
    yaml_path = NATIVE / "configs" / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
    impl_candidates = [
        NATIVE / "src" / "small_paper" / "structural_exit_policies.py",
        NATIVE / "src" / "small_paper" / "observer_position_tracker.py",
    ]
    found = [str(p.relative_to(NATIVE)) for p in impl_candidates if p.exists()]
    return {
        "status": "CONTROL_PBV2_UNAVAILABLE",
        "reason": (
            "PBv2 is ENTRY/guard policy + board-dynamic trailing_mfe structural EXIT; "
            "CurrentPrice-only research parity not established; refuse to invent CONTROL_PBv2."
        ),
        "config_source": str(yaml_path.relative_to(NATIVE)) if yaml_path.exists() else None,
        "implementation_candidates": found,
        "parity_status": "CANONICAL_EXIT_PARITY_NOT_ESTABLISHED",
        "invented_substitute": False,
    }


def specs_from_design(design_rows: list[dict[str, Any]]) -> list[ExitSpec]:
    out = []
    for d in design_rows:
        out.append(ExitSpec(
            exit_id=d["exit_id"],
            path_family=d.get("path_family"),
            variant=d.get("variant"),
            stop_bps=d.get("stop_bps"),
            target_bps=d.get("target_bps"),
            trail_activation_bps=d.get("trail_activation_bps"),
            giveback_bps=d.get("giveback_bps"),
            giveback_mode=d.get("giveback_mode"),
            no_progress_sec=d.get("no_progress_sec"),
            max_hold_sec=float(d.get("max_hold_sec") or 900.0),
            no_progress_mfe_bps=d.get("no_progress_mfe_bps", 5.0),
            no_progress_abs_ret_bps=d.get("no_progress_abs_ret_bps", 5.0),
            is_control=False,
        ))
    return out


def implementation_sha() -> str:
    return hashlib.sha256(THIS_FILE.read_bytes()).hexdigest()


def simulate_exit(
    *,
    spec: ExitSpec,
    entry_epoch: float,
    entry_price: float,
    date: str,
    session: str,
    times: np.ndarray,
    prices: np.ndarray,
) -> Optional[dict[str, Any]]:
    """
    Event-driven long EXIT with X26 priority:
    mid-path: hard_stop > target > trailing > NoProgress
    terminal: session_close > max_hold
    Same-event stop vs target: hard_stop wins.
    TOUCH_EPS = 1e-12
    """
    if times.size == 0 or entry_price is None or entry_price <= 0:
        return None
    sess_end = session_end_epoch(date, session)
    max_t = min(entry_epoch + spec.max_hold_sec, sess_end)
    eps = TOUCH_EPS

    hard = (-spec.stop_bps / 10000.0) if spec.stop_bps is not None else None
    target = (spec.target_bps / 10000.0) if spec.target_bps is not None else None
    trail_act = (spec.trail_activation_bps / 10000.0) if spec.trail_activation_bps is not None else None
    trail_gb = (spec.giveback_bps / 10000.0) if spec.giveback_bps is not None else None
    np_mfe = (spec.no_progress_mfe_bps / 10000.0) if spec.no_progress_mfe_bps is not None else None
    np_abs = (spec.no_progress_abs_ret_bps / 10000.0) if spec.no_progress_abs_ret_bps is not None else None

    mfe = 0.0
    mae = 0.0
    trail_on = False
    np_checked = False
    last_i = None

    for i in range(times.size):
        t = float(times[i])
        if t > max_t + eps:
            break
        if t > sess_end + eps:
            break
        px = float(prices[i])
        ret = px / entry_price - 1.0
        mfe = max(mfe, ret)
        mae = min(mae, ret)
        last_i = i
        hold = t - entry_epoch

        # If at/near session close during walk — session_close has top priority at boundary
        if t >= sess_end - eps:
            return _res(spec, entry_epoch, entry_price, t, px, "session_close", hold, mfe, mae, ret)

        if hard is not None and ret <= hard + eps:
            return _res(spec, entry_epoch, entry_price, t, px, "hard_stop", hold, mfe, mae, ret)
        if target is not None and ret >= target - eps:
            return _res(spec, entry_epoch, entry_price, t, px, "profit_target", hold, mfe, mae, ret)
        if trail_act is not None and trail_gb is not None:
            if mfe >= trail_act - eps:
                trail_on = True
            if trail_on and ret <= (mfe - trail_gb) + eps:
                return _res(spec, entry_epoch, entry_price, t, px, "trailing_exit", hold, mfe, mae, ret)
        if (
            spec.no_progress_sec is not None
            and not np_checked
            and hold + eps >= spec.no_progress_sec
        ):
            np_checked = True
            if np_mfe is not None and np_abs is not None:
                if mfe < np_mfe - eps and abs(ret) < np_abs - eps:
                    return _res(spec, entry_epoch, entry_price, t, px, "no_progress_exit", hold, mfe, mae, ret)

    if last_i is None:
        return None
    t = float(times[last_i])
    px = float(prices[last_i])
    hold = t - entry_epoch
    ret = px / entry_price - 1.0
    lim = min(entry_epoch + spec.max_hold_sec, sess_end)
    if abs(lim - sess_end) < 1e-6 and t >= lim - 2.0:
        reason = "session_close"
    elif hold + eps >= spec.max_hold_sec or t >= entry_epoch + spec.max_hold_sec - eps:
        reason = "max_hold_exit"
    elif lim == sess_end:
        reason = "session_close"
    else:
        reason = "max_hold_exit"
    return _res(spec, entry_epoch, entry_price, t, px, reason, hold, mfe, mae, ret)


def _res(spec, entry_epoch, entry_price, exit_t, exit_px, reason, hold, mfe, mae, ret):
    return {
        "exit_id": spec.exit_id,
        "exit_reason": reason,
        "hold_sec": float(hold),
        "entry_price": float(entry_price),
        "exit_price": float(exit_px),
        "MFE_at_exit_bps": float(mfe * 10000.0),
        "MAE_at_exit_bps": float(mae * 10000.0),
        # intentionally omit pnl ranking fields for X26 publish constraints
    }


def spec_to_manifest_row(spec: ExitSpec, extra: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    row = {
        **asdict(spec),
        "event_priority": list(EVENT_PRIORITY),
        "TOUCH_EPS": TOUCH_EPS,
        "implementation_file": str(THIS_FILE.relative_to(NATIVE)).replace("\\", "/"),
        "implementation_sha256": implementation_sha(),
        "hard_stop_sign": "negative_of_stop_bps",
    }
    if extra:
        row.update(extra)
    return row
