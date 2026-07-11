"""Phase663A — Observer session scope (AM/PM boundary, no cross-session EXIT)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

from small_paper.session_schedule import parse_hhmm
from storage.intraday_recorder import parse_kabu_time

JST = ZoneInfo("Asia/Tokyo")


@dataclass(frozen=True)
class ObserverSessionScope:
    session_id: str
    session_kind: str
    trade_date: date
    allowed_entry_start: datetime

    def entry_allowed_at(self, when: datetime) -> bool:
        return when >= self.allowed_entry_start


def build_observer_session_scope(
    *,
    output_dir: Path,
    trade_date: date,
    am_pm_policy: Any | None,
) -> ObserverSessionScope:
    kind = str(getattr(am_pm_policy, "kind", "am") or "am").lower()
    allowed = str(getattr(am_pm_policy, "allowed_entry_start", "09:00") or "09:00")
    if am_pm_policy is None:
        allowed = "09:00"
    start_t = parse_hhmm(allowed)
    allowed_dt = datetime.combine(trade_date, start_t, tzinfo=JST)
    day = trade_date.strftime("%Y%m%d")
    session_id = f"{day}_{kind}_{output_dir.name}"
    return ObserverSessionScope(
        session_id=session_id,
        session_kind=kind,
        trade_date=trade_date,
        allowed_entry_start=allowed_dt,
    )


def observer_entry_allowed_for_scope(
    scope: ObserverSessionScope,
    trade: Mapping[str, Any],
    *,
    payload: Mapping[str, Any] | None = None,
    now: Optional[datetime] = None,
) -> bool:
    from small_paper.observer_entry_time import resolve_observer_entry_time

    ent = resolve_observer_entry_time(trade, payload=payload, fallback_now=now or datetime.now(JST))
    return scope.entry_allowed_at(ent)


def classify_entry_session(
    observer_entry_time: Optional[str],
    *,
    pm_allowed_start: str,
    trade_date: date,
) -> str:
    if not observer_entry_time:
        return "unknown"
    ent = parse_kabu_time(observer_entry_time, fallback=datetime.now(JST))
    pm_start = datetime.combine(trade_date, parse_hhmm(pm_allowed_start), tzinfo=JST)
    return "pm" if ent >= pm_start else "am"
