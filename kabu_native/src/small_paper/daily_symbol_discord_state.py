"""Phase687W25C-R3 — read-only daily symbol state for Discord re-entry visibility.

Does NOT affect ENTRY/EXIT/gate/trading logic. Display and Summary audit only.
Persists AM→PM within trading_date; resets on next day; restorable from events JSONL.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from small_paper.discord_message_builder import format_hold_duration, format_time_hms_jst, humanize_exit_reason
from small_paper.entry_pipeline_stages import SAME_PUSH_REENTRY_AFTER_NO_PROGRESS_EXIT

log = logging.getLogger("kabu_native.small_paper.daily_symbol_discord_state")

JST = ZoneInfo("Asia/Tokyo")
STATE_FILENAME = "daily_symbol_discord_state.json"


def trading_date_jst(now: Optional[datetime] = None) -> str:
    dt = now or datetime.now(JST)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JST)
    else:
        dt = dt.astimezone(JST)
    return dt.strftime("%Y%m%d")


def _norm_symbol(symbol: str) -> str:
    s = str(symbol or "").strip().upper()
    if s and not s.endswith(".T") and s.isdigit():
        return f"{s}.T"
    return s


def _parse_ts(ts: Any) -> Optional[datetime]:
    s = str(ts or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return dt.astimezone(JST)
    except ValueError:
        return None


def elapsed_label(from_ts: Optional[str], to_ts: Optional[str]) -> str:
    a = _parse_ts(from_ts)
    b = _parse_ts(to_ts)
    if a is None or b is None:
        return "N/A"
    sec = max(0.0, (b - a).total_seconds())
    return format_hold_duration(sec / 60.0)


def format_yen_100(yen: Optional[float]) -> str:
    if yen is None:
        return "N/A"
    try:
        v = float(yen)
    except (TypeError, ValueError):
        return "N/A"
    sign = "+" if v >= 0 else ""
    return f"{sign}{int(round(v)):,}円（100株換算）".replace(",", ",")


@dataclass
class SymbolDayState:
    trading_date: str
    symbol: str
    entry_count_today: int = 0
    previous_exit_reason: str = ""
    previous_exit_at: str = ""
    previous_exit_price: Optional[float] = None
    realized_pnl_yen_100_today: float = 0.0
    same_push_suppression_count: int = 0
    reentry_after_no_progress_count: int = 0
    # accepted entries that were 2nd+ same day
    same_symbol_reentry_count: int = 0

    def snapshot_for_entry_display(self, *, entry_time: Optional[str] = None) -> dict[str, Any]:
        """State BEFORE recording this entry (caller should peek then record)."""
        n = int(self.entry_count_today) + 1
        out: dict[str, Any] = {
            "entry_count_today_after": n,
            "is_reentry": n >= 2,
        }
        if n >= 2 and self.previous_exit_at:
            out.update(
                {
                    "previous_exit_reason": self.previous_exit_reason,
                    "previous_exit_reason_ja": humanize_exit_reason(self.previous_exit_reason),
                    "previous_exit_at": self.previous_exit_at,
                    "previous_exit_time_hms": format_time_hms_jst(self.previous_exit_at),
                    "previous_exit_elapsed": elapsed_label(self.previous_exit_at, entry_time),
                    "previous_exit_price": self.previous_exit_price,
                }
            )
        return out


@dataclass
class DailySymbolDiscordState:
    trading_date: str = ""
    symbols: dict[str, SymbolDayState] = field(default_factory=dict)
    same_push_suppression_count_day: int = 0
    same_symbol_reentry_count_day: int = 0
    reentry_after_no_progress_count_day: int = 0
    _path: Optional[Path] = field(default=None, repr=False)

    def ensure_day(self, trading_date: str) -> None:
        day = str(trading_date or "").strip()
        if not day:
            day = trading_date_jst()
        if self.trading_date and self.trading_date != day:
            self.symbols.clear()
            self.same_push_suppression_count_day = 0
            self.same_symbol_reentry_count_day = 0
            self.reentry_after_no_progress_count_day = 0
        self.trading_date = day

    def get(self, symbol: str) -> SymbolDayState:
        sym = _norm_symbol(symbol)
        if sym not in self.symbols:
            self.symbols[sym] = SymbolDayState(trading_date=self.trading_date, symbol=sym)
        return self.symbols[sym]

    def peek_entry(self, symbol: str, *, entry_time: Optional[str] = None) -> dict[str, Any]:
        self.ensure_day(self.trading_date or trading_date_jst())
        return self.get(symbol).snapshot_for_entry_display(entry_time=entry_time)

    def record_accepted_entry(self, symbol: str, *, entry_time: Optional[str] = None) -> dict[str, Any]:
        """Record actual accepted ENTRY. Returns display snapshot for this entry."""
        self.ensure_day(self.trading_date or trading_date_jst())
        st = self.get(symbol)
        snap = st.snapshot_for_entry_display(entry_time=entry_time)
        st.entry_count_today += 1
        if st.entry_count_today >= 2:
            st.same_symbol_reentry_count += 1
            self.same_symbol_reentry_count_day += 1
            if "no_progress" in str(st.previous_exit_reason or "").lower():
                st.reentry_after_no_progress_count += 1
                self.reentry_after_no_progress_count_day += 1
        self._persist()
        return snap

    def record_official_exit(
        self,
        symbol: str,
        *,
        exit_reason: str,
        exit_time: str,
        exit_price: Optional[float],
        pnl_yen_100: Optional[float],
    ) -> dict[str, Any]:
        self.ensure_day(self.trading_date or trading_date_jst())
        st = self.get(symbol)
        if pnl_yen_100 is not None:
            try:
                st.realized_pnl_yen_100_today = round(
                    float(st.realized_pnl_yen_100_today) + float(pnl_yen_100), 2
                )
            except (TypeError, ValueError):
                pass
        st.previous_exit_reason = str(exit_reason or "")
        st.previous_exit_at = str(exit_time or "")
        try:
            st.previous_exit_price = float(exit_price) if exit_price is not None else None
        except (TypeError, ValueError):
            st.previous_exit_price = None
        self._persist()
        return {
            "realized_pnl_yen_100_today": st.realized_pnl_yen_100_today,
            "previous_exit_reason": st.previous_exit_reason,
            "previous_exit_at": st.previous_exit_at,
            "previous_exit_price": st.previous_exit_price,
        }

    def record_same_push_suppression(self, symbol: str = "") -> None:
        self.ensure_day(self.trading_date or trading_date_jst())
        self.same_push_suppression_count_day += 1
        if symbol:
            self.get(symbol).same_push_suppression_count += 1
        self._persist()

    def day_realized_pnl_yen_100(self) -> float:
        return round(sum(float(s.realized_pnl_yen_100_today) for s in self.symbols.values()), 2)

    def summary_audit(self) -> dict[str, Any]:
        return {
            "same_symbol_reentry_count": int(self.same_symbol_reentry_count_day),
            "reentry_after_no_progress_count": int(self.reentry_after_no_progress_count_day),
            "same_push_suppression_count": int(self.same_push_suppression_count_day),
            "day_realized_pnl_yen_100": self.day_realized_pnl_yen_100(),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "trading_date": self.trading_date,
            "same_push_suppression_count_day": self.same_push_suppression_count_day,
            "same_symbol_reentry_count_day": self.same_symbol_reentry_count_day,
            "reentry_after_no_progress_count_day": self.reentry_after_no_progress_count_day,
            "symbols": {k: asdict(v) for k, v in self.symbols.items()},
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, path: Optional[Path] = None) -> "DailySymbolDiscordState":
        st = cls(
            trading_date=str(data.get("trading_date") or ""),
            same_push_suppression_count_day=int(data.get("same_push_suppression_count_day") or 0),
            same_symbol_reentry_count_day=int(data.get("same_symbol_reentry_count_day") or 0),
            reentry_after_no_progress_count_day=int(data.get("reentry_after_no_progress_count_day") or 0),
            _path=path,
        )
        raw = data.get("symbols") or {}
        if isinstance(raw, Mapping):
            for sym, row in raw.items():
                if not isinstance(row, Mapping):
                    continue
                st.symbols[_norm_symbol(str(sym))] = SymbolDayState(
                    trading_date=str(row.get("trading_date") or st.trading_date),
                    symbol=_norm_symbol(str(row.get("symbol") or sym)),
                    entry_count_today=int(row.get("entry_count_today") or 0),
                    previous_exit_reason=str(row.get("previous_exit_reason") or ""),
                    previous_exit_at=str(row.get("previous_exit_at") or ""),
                    previous_exit_price=(
                        float(row["previous_exit_price"])
                        if row.get("previous_exit_price") is not None
                        else None
                    ),
                    realized_pnl_yen_100_today=float(row.get("realized_pnl_yen_100_today") or 0),
                    same_push_suppression_count=int(row.get("same_push_suppression_count") or 0),
                    reentry_after_no_progress_count=int(row.get("reentry_after_no_progress_count") or 0),
                    same_symbol_reentry_count=int(row.get("same_symbol_reentry_count") or 0),
                )
        return st

    def _persist(self) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except Exception as exc:
            log.debug("daily symbol state persist failed (fail-open): %s", exc)

    def save(self) -> None:
        self._persist()


def state_path_for_day(native_root: Path, trading_date: str) -> Path:
    return Path(native_root) / "results" / "small_paper" / str(trading_date) / STATE_FILENAME


def load_or_create_state(
    *,
    native_root: Path,
    trading_date: Optional[str] = None,
) -> DailySymbolDiscordState:
    day = trading_date or trading_date_jst()
    path = state_path_for_day(native_root, day)
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            st = DailySymbolDiscordState.from_dict(data, path=path)
            st.ensure_day(day)
            return st
        except Exception as exc:
            log.warning("daily symbol state load failed, recreating: %s", exc)
    st = DailySymbolDiscordState(trading_date=day, _path=path)
    return st


def restore_state_from_events(
    events: Sequence[Mapping[str, Any]],
    *,
    trading_date: str,
    native_root: Optional[Path] = None,
) -> DailySymbolDiscordState:
    """Rebuild display state from accepted ENTRY + observer_exit + same-PUSH rejects."""
    path = state_path_for_day(native_root, trading_date) if native_root else None
    st = DailySymbolDiscordState(trading_date=trading_date, _path=path)
    for ev in events:
        et = str(ev.get("event_type") or "")
        sym = _norm_symbol(str(ev.get("symbol") or ""))
        if not sym:
            continue
        if et == "accepted":
            st.record_accepted_entry(sym, entry_time=str(ev.get("event_time") or ev.get("entry_time") or ""))
        elif et == "observer_exit":
            yen = ev.get("pnl_yen_100")
            if yen is None:
                try:
                    from replay.pnl_yen import resolve_pnl_yen_100

                    yen = resolve_pnl_yen_100(
                        entry_price=float(ev.get("entry_price") or 0),
                        exit_price=float(ev.get("current_price") or ev.get("exit_price") or 0),
                        side=str(ev.get("side") or "long"),
                        pnl_yen_100=None,
                    )
                except Exception:
                    yen = None
            st.record_official_exit(
                sym,
                exit_reason=str(ev.get("exit_reason") or ev.get("structural_exit_reason") or ""),
                exit_time=str(ev.get("exit_time") or ev.get("event_time") or ""),
                exit_price=(
                    float(ev.get("current_price") or ev.get("exit_price"))
                    if (ev.get("current_price") or ev.get("exit_price")) is not None
                    else None
                ),
                pnl_yen_100=float(yen) if yen is not None else None,
            )
        elif et == "rejected":
            reason = str(ev.get("gate_reject_reason") or ev.get("final_reject_reason") or "")
            if reason == SAME_PUSH_REENTRY_AFTER_NO_PROGRESS_EXIT or ev.get("same_push_reentry_skip"):
                st.record_same_push_suppression(sym)
    st.save()
    return st


def restore_state_from_day_sessions(
    *,
    native_root: Path,
    trading_date: str,
) -> DailySymbolDiscordState:
    """Scan results/small_paper/{day}/live_session_*/small_paper_events.jsonl chronologically."""
    day_dir = Path(native_root) / "results" / "small_paper" / str(trading_date)
    events: list[Mapping[str, Any]] = []
    if day_dir.is_dir():
        for jl in sorted(day_dir.glob("live_session_*/small_paper_events.jsonl")):
            try:
                for line in jl.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    events.append(json.loads(line))
            except Exception as exc:
                log.debug("skip journal %s: %s", jl, exc)
    return restore_state_from_events(events, trading_date=trading_date, native_root=native_root)


# Process-wide singleton for Paper Discord enrich (read-only for trading)
_STATE: Optional[DailySymbolDiscordState] = None


def get_daily_symbol_state(
    *,
    native_root: Optional[Path] = None,
    trading_date: Optional[str] = None,
    force_reload: bool = False,
) -> DailySymbolDiscordState:
    global _STATE
    day = trading_date or trading_date_jst()
    root = Path(native_root) if native_root else Path(__file__).resolve().parents[2]
    if force_reload or _STATE is None or _STATE.trading_date != day:
        path = state_path_for_day(root, day)
        if path.is_file():
            _STATE = load_or_create_state(native_root=root, trading_date=day)
        else:
            # Prefer restore from journals when cold-starting PM
            _STATE = restore_state_from_day_sessions(native_root=root, trading_date=day)
            if not _STATE.symbols and not path.is_file():
                _STATE = load_or_create_state(native_root=root, trading_date=day)
    return _STATE


def reset_daily_symbol_state_for_tests() -> None:
    global _STATE
    _STATE = None
