"""
Phase645: Pre-session warmup — connect/register/init before allowed_entry_start.

Warmup PUSH updates price rings only; full gate evaluation starts at allowed_entry_start.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping, Optional

from small_paper.session_schedule import parse_hhmm, wait_until
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

DEFAULT_AM_WARMUP_START = "08:50"
DEFAULT_PM_WARMUP_START = "12:15"

PHASE645_VERDICT = "phase645_pre_session_warmup_done"


def pre_session_warmup_enabled(config: Any) -> bool:
    return bool(getattr(config, "pre_session_warmup_enabled", False))


def warmup_start_hhmm(config: Any, *, session_kind: str) -> str:
    kind = str(session_kind or "am").strip().lower()
    if kind == "pm":
        return str(
            getattr(config, "pre_session_warmup_pm_start", DEFAULT_PM_WARMUP_START)
            or DEFAULT_PM_WARMUP_START
        )
    return str(
        getattr(config, "pre_session_warmup_am_start", DEFAULT_AM_WARMUP_START)
        or DEFAULT_AM_WARMUP_START
    )


def warmup_start_dt(
    trade_date: date,
    *,
    session_kind: str,
    config: Any,
) -> datetime:
    hhmm = warmup_start_hhmm(config, session_kind=session_kind)
    t = parse_hhmm(hhmm)
    return datetime.combine(trade_date, t, tzinfo=JST)


def allowed_entry_start_dt(am_pm_policy: Any, trade_date: date) -> Optional[datetime]:
    if am_pm_policy is None:
        return None
    start = str(getattr(am_pm_policy, "allowed_entry_start", "") or "").strip()
    if not start:
        return None
    t = parse_hhmm(start)
    return datetime.combine(trade_date, t, tzinfo=JST)


@dataclass(frozen=True)
class WarmupInitPlan:
    """When to wait and whether init runs before session_start."""

    warmup_enabled: bool
    wait_until_init: Optional[datetime]
    legacy_wait_until_session: bool


def resolve_warmup_init_plan(
    *,
    config: Any,
    full_session: bool,
    wait_until_session: bool,
    session_start: str,
    trade_date: date,
    am_pm_policy: Optional[Any],
    now: Optional[datetime] = None,
) -> WarmupInitPlan:
    """Return wait target for connection/register/init."""
    now = now or datetime.now(JST)
    sched_start = datetime.combine(trade_date, parse_hhmm(session_start), tzinfo=JST)
    kind = str(getattr(am_pm_policy, "kind", "am") or "am").lower() if am_pm_policy else "am"
    enabled = (
        pre_session_warmup_enabled(config)
        and full_session
        and wait_until_session
        and am_pm_policy is not None
    )
    if not enabled:
        legacy = full_session and wait_until_session and now < sched_start
        return WarmupInitPlan(
            warmup_enabled=False,
            wait_until_init=sched_start if legacy else None,
            legacy_wait_until_session=legacy,
        )
    warm_dt = warmup_start_dt(trade_date, session_kind=kind, config=config)
    if now < warm_dt:
        return WarmupInitPlan(
            warmup_enabled=True,
            wait_until_init=warm_dt,
            legacy_wait_until_session=False,
        )
    return WarmupInitPlan(
        warmup_enabled=True,
        wait_until_init=None,
        legacy_wait_until_session=False,
    )


def apply_init_wait(plan: WarmupInitPlan) -> None:
    if plan.wait_until_init is not None:
        wait_until(plan.wait_until_init)


def entry_evaluation_allowed(am_pm_policy: Optional[Any], *, now: Optional[datetime] = None) -> bool:
    if am_pm_policy is None:
        return True
    return am_pm_policy.entry_allowed_now(now)


def ring_only_warmup_active(
    *,
    config: Any,
    am_pm_policy: Optional[Any],
    now: Optional[datetime] = None,
) -> bool:
    if not pre_session_warmup_enabled(config) or am_pm_policy is None:
        return False
    return not entry_evaluation_allowed(am_pm_policy, now=now)


def compute_ready_delay_sec(
    *,
    allowed_entry_start: Optional[str],
    first_gate_eval_ts: Optional[str],
    trade_date: date,
) -> Optional[float]:
    if not allowed_entry_start or not first_gate_eval_ts:
        return None
    from storage.intraday_recorder import parse_kabu_time

    start = datetime.combine(trade_date, parse_hhmm(allowed_entry_start), tzinfo=JST)
    first = parse_kabu_time(first_gate_eval_ts, fallback=start)
    if first is None:
        return None
    return round((first - start).total_seconds(), 3)


def warmup_summary_fields(
    *,
    config: Any,
    state: Any,
    am_pm_policy: Optional[Any],
    trade_date: date,
) -> dict[str, Any]:
    if isinstance(am_pm_policy, Mapping):
        allowed = str(am_pm_policy.get("allowed_entry_start") or "")
    elif am_pm_policy is not None:
        allowed = str(getattr(am_pm_policy, "allowed_entry_start", "") or "")
    else:
        allowed = ""
    ready_delay = compute_ready_delay_sec(
        allowed_entry_start=allowed or None,
        first_gate_eval_ts=getattr(state, "first_gate_eval_ts", None),
        trade_date=trade_date,
    )
    return {
        "pre_session_warmup_enabled": pre_session_warmup_enabled(config),
        "pre_session_warmup_am_start": warmup_start_hhmm(config, session_kind="am"),
        "pre_session_warmup_pm_start": warmup_start_hhmm(config, session_kind="pm"),
        "session_ready_ts": getattr(state, "session_ready_ts", None),
        "first_gate_eval_ts": getattr(state, "first_gate_eval_ts", None),
        "ready_delay_sec": ready_delay,
        "pre_session_warmup_ring_push_count": int(
            getattr(state, "pre_session_warmup_ring_push_count", 0) or 0
        ),
        "allowed_entry_start": allowed or None,
    }


def format_warmup_health_lines(summary: Mapping[str, Any]) -> list[str]:
    if not summary.get("pre_session_warmup_enabled"):
        return []
    delay = summary.get("ready_delay_sec")
    if delay is None:
        return ["pre-session warmup: enabled (ready delay pending)"]
    return [f"pre-session ready delay: {float(delay):.1f}s"]
