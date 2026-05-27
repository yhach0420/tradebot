"""
Phase 116: AM/PM shadow session times, entry cutoff, and session-close exit policy.

Applied at runtime via --am-pm-session (no production YAML change).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time
from typing import Literal, Optional
from zoneinfo import ZoneInfo

from small_paper.allowed_trading_windows import TradingWindow
from small_paper.config import SmallPaperPilotConfig
from small_paper.observer_position_tracker import ObserverTrackerConfig
from small_paper.session_schedule import parse_hhmm

JST = ZoneInfo("Asia/Tokyo")

AmPmKind = Literal["am", "pm"]

# Universe screening (Phase114 design; generation scripts)
AM_SCREENING_WINDOW = "09:00-09:03"
PM_SCREENING_WINDOW = "12:25-12:32"

MORNING_SESSION_CLOSE = "morning_session_close"
AFTERNOON_SESSION_CLOSE = "afternoon_session_close"


@dataclass(frozen=True)
class AmPmSessionPolicy:
    kind: AmPmKind
    session_start: str
    session_end: str
    entry_stop: str
    force_close: str
    force_close_reason: str
    allowed_entry_start: str
    allowed_entry_end: str
    screening_window: str

    @classmethod
    def morning(cls) -> "AmPmSessionPolicy":
        return cls(
            kind="am",
            session_start="09:03",
            session_end="11:25",
            entry_stop="11:20",
            force_close="11:25",
            force_close_reason=MORNING_SESSION_CLOSE,
            allowed_entry_start="09:03",
            allowed_entry_end="11:20",
            screening_window=AM_SCREENING_WINDOW,
        )

    @classmethod
    def afternoon(cls) -> "AmPmSessionPolicy":
        return cls(
            kind="pm",
            session_start="12:33",
            session_end="15:23",
            entry_stop="15:18",
            force_close="15:23",
            force_close_reason=AFTERNOON_SESSION_CLOSE,
            allowed_entry_start="12:33",
            allowed_entry_end="15:18",
            screening_window=PM_SCREENING_WINDOW,
        )

    @classmethod
    def from_kind(cls, kind: str) -> "AmPmSessionPolicy":
        k = kind.strip().lower()
        if k == "am":
            return cls.morning()
        if k == "pm":
            return cls.afternoon()
        raise ValueError(f"unknown am_pm_session: {kind!r}")

    def _now(self, now: Optional[datetime]) -> datetime:
        return now if now is not None else datetime.now(JST)

    def _today_time(self, hhmm: str, now: Optional[datetime] = None) -> datetime:
        n = self._now(now)
        t = parse_hhmm(hhmm)
        return datetime.combine(n.date(), t, tzinfo=JST)

    def entry_allowed_now(self, now: Optional[datetime] = None) -> bool:
        n = self._now(now)
        t = n.time()
        return parse_hhmm(self.allowed_entry_start) <= t <= parse_hhmm(self.allowed_entry_end)

    def force_close_due(self, now: Optional[datetime] = None) -> bool:
        return self._now(now).time() >= parse_hhmm(self.force_close)

    def entry_stop_reached(self, now: Optional[datetime] = None) -> bool:
        return self._now(now).time() >= parse_hhmm(self.entry_stop)

    def allowed_trading_windows(self) -> list[TradingWindow]:
        return [
            TradingWindow(
                parse_hhmm(self.allowed_entry_start),
                parse_hhmm(self.allowed_entry_end),
                label=f"{self.kind}_entry",
            )
        ]

    def apply_to_pilot_config(self, config: SmallPaperPilotConfig) -> SmallPaperPilotConfig:
        return replace(
            config,
            allowed_trading_windows=self.allowed_trading_windows(),
            live_session_start=self.session_start,
            live_session_end=self.session_end,
        )

    def observer_tracker_config(self, config: SmallPaperPilotConfig) -> ObserverTrackerConfig:
        from research.structural_exit_policies import POLICY_COMBINED_STRUCTURAL_EXIT_V1
        from small_paper.discord_notifier import observer_tracker_config_from_pilot

        base = observer_tracker_config_from_pilot(config)
        policy = str(
            getattr(config, "structural_exit_policy", None) or POLICY_COMBINED_STRUCTURAL_EXIT_V1
        )
        return replace(
            base,
            structural_exit_policy=policy,
            live_session_end=self.session_end,
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "session_start": self.session_start,
            "session_end": self.session_end,
            "entry_stop": self.entry_stop,
            "force_close": self.force_close,
            "force_close_reason": self.force_close_reason,
            "allowed_entry_start": self.allowed_entry_start,
            "allowed_entry_end": self.allowed_entry_end,
            "screening_window": self.screening_window,
        }


def apply_am_pm_policy(
    config: SmallPaperPilotConfig,
    kind: str,
) -> tuple[SmallPaperPilotConfig, AmPmSessionPolicy]:
    policy = AmPmSessionPolicy.from_kind(kind)
    return policy.apply_to_pilot_config(config), policy
