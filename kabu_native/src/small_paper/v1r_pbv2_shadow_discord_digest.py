"""PBv2 SHADOW_ONLY Discord summary — AM/PM session aggregate (not 5-minute).

Internal shadow ledger (accept/exit/cap) stays hot-path.
Raw/shadow event logging is unchanged.
Discord to trade-research is one Shadow Summary at AM end and one at PM end.
Never mutates Arch E / V1R Primary occupancy.
Notification-only: does not change evaluation, ENTRY, EXIT, CAP, or occupancy.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

from small_paper.v1r_primary_runtime import CLOCK_GRID

JST = ZoneInfo("Asia/Tokyo")

# Kept for dataclass/compat; periodic Discord flush is disabled.
DIGEST_FALLBACK_SEC = 300
DIGEST_INTERVAL_SEC = DIGEST_FALLBACK_SEC
_NA = "n/a"
_SESSION_KINDS = frozenset({"AM", "PM", "DAILY", "SESSION"})


def _now() -> datetime:
    return datetime.now(JST)


def _anchor_label(dt: datetime) -> Optional[str]:
    if (dt.hour, dt.minute) in CLOCK_GRID:
        return f"{dt.hour:02d}:{dt.minute:02d}"
    return None


def _window_id(dt: datetime) -> str:
    """5-minute wall clock bucket id (JST), e.g. 20260812|13:00 (audit only)."""
    bucket_min = (dt.minute // 5) * 5
    return f"{dt.strftime('%Y%m%d')}|{dt.hour:02d}:{bucket_min:02d}"


def _session_kind_from_summary(summary: Mapping[str, Any]) -> str:
    am_pm = summary.get("am_pm_session") or {}
    if isinstance(am_pm, Mapping):
        kind = str(am_pm.get("kind") or "").strip().lower()
        if kind in ("am", "pm"):
            return kind.upper()
    label = str(summary.get("session_label") or summary.get("session_am_pm") or "").lower()
    if label.startswith("am") or label == "morning":
        return "AM"
    if label.startswith("pm") or label == "afternoon":
        return "PM"
    stop = str(summary.get("stop_reason") or "")
    if stop == "morning_session_close":
        return "AM"
    if stop == "afternoon_session_close":
        return "PM"
    return ""


@dataclass
class PBv2ShadowDiscordDigest:
    """Accumulate SHADOW_ONLY divert attempts; Discord only at AM/PM session end."""

    interval_sec: int = DIGEST_FALLBACK_SEC
    trace_dir: Optional[Path] = None
    evaluated: int = 0
    accepted: int = 0
    already_open: int = 0
    cap_blocked: int = 0
    exits: int = 0
    symbols_accepted: list[str] = field(default_factory=list)
    symbols_seen: set[str] = field(default_factory=set)
    last_prices: dict[str, float] = field(default_factory=dict)
    window_id: str = ""
    window_started_mono: float = 0.0
    last_flush_mono: float = 0.0
    flush_count: int = 0
    suppressed_immediate: int = 0
    day_evaluated: int = 0
    day_accepted: int = 0
    day_already_open: int = 0
    day_cap_blocked: int = 0
    day_exits: int = 0
    last_open_n: int = 0
    last_cap: int = 0
    flushed_session_kinds: set[str] = field(default_factory=set)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def bind_trace_dir(self, trace_dir: Optional[Path]) -> None:
        if trace_dir is not None:
            self.trace_dir = Path(trace_dir)

    def _ensure_window(self, now: Optional[datetime] = None) -> None:
        dt = now or _now()
        wid = _window_id(dt)
        if not self.window_id:
            self.window_id = wid
            self.window_started_mono = time.monotonic()
            return
        if wid != self.window_id:
            # Window id is audit-only; do not Discord-flush on rotate.
            self.window_id = wid
            self.window_started_mono = time.monotonic()

    def note_accept_attempt(
        self,
        *,
        symbol: str,
        shadow_admit: dict[str, Any],
        entry_price: float = 0.0,
        trading_date: str = "",
        open_n: int = 0,
        cap: int = 0,
        force_flush: bool = False,
    ) -> dict[str, Any]:
        """Record one SHADOW divert attempt. Never publishes per-call."""
        date_s = trading_date
        with self._lock:
            self.suppressed_immediate += 1
            self.evaluated += 1
            self.last_open_n = int(open_n)
            self.last_cap = int(cap)
            sym = str(symbol).replace(".T", "")
            self.symbols_seen.add(sym)
            if entry_price:
                try:
                    self.last_prices[sym] = float(entry_price)
                except (TypeError, ValueError):
                    pass
            admitted = bool(shadow_admit.get("admitted"))
            reason = str(shadow_admit.get("reason") or "")
            if admitted:
                self.accepted += 1
                if sym not in self.symbols_accepted:
                    self.symbols_accepted.append(sym)
            elif reason == "already_open":
                self.already_open += 1
            elif reason == "shadow_cap":
                self.cap_blocked += 1
            self._ensure_window()
            due = self._flush_due_unlocked(force=force_flush)
            if due:
                return self._flush_unlocked(
                    trading_date=date_s,
                    open_n=open_n,
                    cap=cap,
                )
            return {"flushed": False, "suppressed": True, "window_id": self.window_id}

    def note_exit(self, *, symbol: str = "") -> None:
        with self._lock:
            self.exits += 1
            sym = str(symbol).replace(".T", "")
            if sym:
                self.symbols_seen.add(sym)

    def maybe_flush(
        self,
        *,
        trading_date: str = "",
        open_n: int = 0,
        cap: int = 0,
        force: bool = False,
    ) -> dict[str, Any]:
        """No periodic Discord. Only explicit force=True publishes (tests / legacy)."""
        with self._lock:
            if not self._flush_due_unlocked(force=force):
                return {"flushed": False, "window_id": self.window_id}
            return self._flush_unlocked(
                trading_date=trading_date, open_n=open_n, cap=cap, session_kind=""
            )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot_unlocked()

    def _snapshot_unlocked(self) -> dict[str, Any]:
        cap_v = int(self.last_cap or 0)
        newly = int(self.accepted)
        return {
            "evaluated": int(self.evaluated),
            "accepted": int(self.accepted),
            "newly_admitted": newly,
            "already_open": int(self.already_open),
            "shadow_cap": cap_v,
            "cap_blocked": int(self.cap_blocked),
            "shadow_trade_count": newly,
            "exits": int(self.exits),
            "wins": _NA,
            "losses": _NA,
            "pnl": _NA,
            "pf": _NA,
            "open_n": int(self.last_open_n or 0),
            "cap": cap_v,
            "symbols": list(self.symbols_accepted),
            "day_evaluated": int(self.day_evaluated + self.evaluated),
            "day_accepted": int(self.day_accepted + self.accepted),
            "day_newly_admitted": int(self.day_accepted + self.accepted),
            "day_already_open": int(self.day_already_open + self.already_open),
            "day_cap_blocked": int(self.day_cap_blocked + self.cap_blocked),
            "day_exits": int(self.day_exits + self.exits),
            "day_shadow_trade_count": int(self.day_accepted + self.accepted),
            "flushed_session_kinds": sorted(self.flushed_session_kinds),
        }

    def flush_session_summary(
        self,
        *,
        session_kind: str,
        trading_date: str = "",
        open_n: int = 0,
        cap: int = 0,
        allow_empty: bool = True,
    ) -> dict[str, Any]:
        """Publish one AM/PM (or DAILY leftover) Shadow Summary. Deduped per date+kind."""
        kind = str(session_kind or "").strip().upper()
        if kind not in _SESSION_KINDS:
            return {"flushed": False, "reason": "invalid_session_kind", "session_kind": kind}
        with self._lock:
            dt = _now()
            day = str(trading_date or "").replace("-", "")[:8] or dt.strftime("%Y%m%d")
            dedupe = f"{day}|{kind}"
            if dedupe in self.flushed_session_kinds:
                return {
                    "flushed": False,
                    "reason": "already_flushed",
                    "session_kind": kind,
                    "window_id": self.window_id,
                }
            has = self.evaluated > 0 or self.accepted > 0 or self.exits > 0
            if not has and not allow_empty:
                return {"flushed": False, "reason": "empty", "session_kind": kind}
            if cap:
                self.last_cap = int(cap)
            self.last_open_n = int(open_n)
            out = self._flush_unlocked(
                trading_date=day,
                open_n=int(open_n or self.last_open_n),
                cap=int(cap or self.last_cap),
                session_kind=kind,
                fold_day=True,
            )
            self.flushed_session_kinds.add(dedupe)
            return out

    def publish_processing_error(
        self,
        *,
        where: str,
        error: str,
        symbol: str = "",
    ) -> dict[str, Any]:
        """Immediate trade-research notify for PBv2 Shadow processing failures."""
        payload = {
            "digest": False,
            "session_summary": False,
            "status": "ERROR",
            "role": "SHADOW_ONLY",
            "source": "pbv2_shadow",
            "where": str(where),
            "error": str(error),
            "symbol": str(symbol).replace(".T", ""),
            "note": "PBv2 Shadow processing error",
        }
        with self._lock:
            result = self._publish(payload)
            self._append_audit({"event": "PBV2_SHADOW_PROCESSING_ERROR", **payload, **result})
            return result

    def _flush_due_unlocked(self, *, force: bool = False) -> bool:
        has = self.evaluated > 0 or self.accepted > 0 or self.exits > 0
        if force:
            return has
        return False

    def _fold_session_into_day_unlocked(self) -> None:
        self.day_evaluated += int(self.evaluated)
        self.day_accepted += int(self.accepted)
        self.day_already_open += int(self.already_open)
        self.day_cap_blocked += int(self.cap_blocked)
        self.day_exits += int(self.exits)

    def _reset_session_unlocked(self, *, dt: datetime) -> None:
        self.evaluated = 0
        self.accepted = 0
        self.already_open = 0
        self.cap_blocked = 0
        self.exits = 0
        self.symbols_accepted = []
        self.symbols_seen = set()
        self.last_prices = {}
        self.suppressed_immediate = 0
        self.window_id = _window_id(dt)
        self.window_started_mono = time.monotonic()
        self.last_flush_mono = time.monotonic()
        self.flush_count += 1

    def _flush_unlocked(
        self,
        *,
        trading_date: str = "",
        open_n: int = 0,
        cap: int = 0,
        session_kind: str = "",
        fold_day: bool = False,
    ) -> dict[str, Any]:
        dt = _now()
        wid = self.window_id or _window_id(dt)
        anchor = _anchor_label(dt) or wid.split("|")[-1]
        kind = str(session_kind or "").strip().upper()
        is_session = kind in _SESSION_KINDS
        newly = int(self.accepted)
        cap_v = int(cap or self.last_cap or 0)
        open_v = int(open_n if open_n else self.last_open_n)
        day_eval = int(self.day_evaluated + (self.evaluated if fold_day else 0))
        day_acc = int(self.day_accepted + (self.accepted if fold_day else 0))
        payload = {
            "digest": True,
            "session_summary": is_session,
            "date": trading_date or dt.strftime("%Y%m%d"),
            "window_id": wid,
            "anchor": anchor,
            "role": "SHADOW_ONLY",
            "source": "pbv2_shadow",
            "status": kind if is_session else "DIGEST",
            "session_kind": kind if is_session else "",
            "evaluated": int(self.evaluated),
            "accepted": int(self.accepted),
            "newly_admitted": newly,
            "already_open": int(self.already_open),
            "shadow_cap": cap_v,
            "cap_blocked": int(self.cap_blocked),
            "shadow_trade_count": newly,
            "exits": int(self.exits),
            "wins": _NA,
            "losses": _NA,
            "win_loss": f"{_NA}/{_NA}",
            "pnl": _NA,
            "pf": _NA,
            "symbols": list(self.symbols_accepted),
            "symbols_seen_n": len(self.symbols_seen),
            "hypothetical_fills": newly,
            "trades": newly,
            "open_n": open_v,
            "cap": cap_v,
            "day_evaluated": day_eval,
            "day_accepted": day_acc,
            "day_newly_admitted": day_acc,
            "day_already_open": int(self.day_already_open + (self.already_open if fold_day else 0)),
            "day_shadow_trade_count": day_acc,
            "day_wins": _NA,
            "day_losses": _NA,
            "day_pnl": _NA,
            "day_pf": _NA,
            "note": (
                (
                    "SHADOW_ONLY session summary — Primary occupancy unchanged; "
                    f"suppressed_immediate={self.suppressed_immediate}; "
                    "closed-trade pnl not in occupancy ledger"
                )
                if is_session
                else (
                    "SHADOW_ONLY digest — Primary occupancy unchanged; "
                    f"suppressed_immediate={self.suppressed_immediate}"
                )
            ),
            "suppressed_immediate": int(self.suppressed_immediate),
        }
        result = self._publish(payload)
        audit_event = (
            "PBV2_SHADOW_SESSION_SUMMARY_FLUSH" if is_session else "PBV2_SHADOW_DIGEST_FLUSH"
        )
        self._append_audit({"event": audit_event, **payload, **result})
        if is_session:
            # Keep a digest-named audit row so existing flush logging is not reduced.
            self._append_audit({"event": "PBV2_SHADOW_DIGEST_FLUSH", **payload, **result})
        if fold_day:
            self._fold_session_into_day_unlocked()
        self._reset_session_unlocked(dt=dt)
        return {"flushed": True, "window_id": wid, "payload": payload, **result}

    def _publish(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            from notify.v1r_discord_routing import V1RNotifyKind, publish_v1r

            status = str(payload.get("status") or "DIGEST")
            r = publish_v1r(
                V1RNotifyKind.PBV2_SHADOW,
                payload,
                test_only=False,
                sync_http=False,
                session_id=f"pbv2-shadow-{status}-{payload.get('date') or payload.get('window_id')}",
            )
            return {
                "discord_status": r.status,
                "channel": r.channel,
                "queued": r.queued,
                "error": r.error or "",
                "notification_id": r.notification_id,
            }
        except Exception as exc:
            err = f"{type(exc).__name__}:{exc}"
            self._append_audit(
                {
                    "event": "PBV2_SHADOW_DIGEST_PUBLISH_EXCEPTION",
                    "error": err,
                    "payload_window": payload.get("window_id"),
                    "payload_status": payload.get("status"),
                }
            )
            print(f"[PBV2_SHADOW_DIGEST_PUBLISH_EXCEPTION] {err}", flush=True)
            return {
                "discord_status": "EXCEPTION",
                "channel": "trade-research",
                "queued": False,
                "error": err,
                "notification_id": "",
            }

    def _append_audit(self, row: dict[str, Any]) -> None:
        if not self.trace_dir:
            return
        try:
            self.trace_dir.mkdir(parents=True, exist_ok=True)
            p = self.trace_dir / "v1r_pbv2_shadow_discord_digest.jsonl"
            with p.open("a", encoding="utf-8") as f:
                f.write(
                    json.dumps(
                        {"ts": _now().isoformat(timespec="seconds"), **row},
                        ensure_ascii=False,
                        default=str,
                    )
                    + "\n"
                )
        except Exception as exc:
            print(
                f"[PBV2_SHADOW_DIGEST_AUDIT_FAIL] {type(exc).__name__}:{exc}",
                flush=True,
            )


_DIGEST: Optional[PBv2ShadowDiscordDigest] = None
_DIGEST_LOCK = threading.Lock()


def get_pbv2_shadow_discord_digest(
    *, trace_dir: Optional[Path] = None
) -> PBv2ShadowDiscordDigest:
    global _DIGEST
    with _DIGEST_LOCK:
        if _DIGEST is None:
            _DIGEST = PBv2ShadowDiscordDigest(trace_dir=trace_dir)
        elif trace_dir is not None and _DIGEST.trace_dir is None:
            _DIGEST.bind_trace_dir(trace_dir)
        return _DIGEST


def reset_pbv2_shadow_discord_digest_for_tests() -> None:
    global _DIGEST
    with _DIGEST_LOCK:
        _DIGEST = None


def format_pbv2_shadow_summary_lines() -> list[str]:
    """Lines for AM/PM/Daily operator/research summary. Fail-open."""
    try:
        snap = get_pbv2_shadow_discord_digest().snapshot()
    except Exception:
        return []
    lines = [
        "PBv2 Shadow Summary:",
        (
            f"evaluated={snap['evaluated']} accepted={snap['accepted']} "
            f"newly_admitted={snap['newly_admitted']} already_open={snap['already_open']}"
        ),
        (
            f"shadow_cap={snap['shadow_cap']} shadow_trade_count={snap['shadow_trade_count']} "
            f"win/loss={snap['wins']}/{snap['losses']} PnL={snap['pnl']} PF={snap['pf']}"
        ),
        (
            f"day evaluated={snap['day_evaluated']} accepted={snap['day_accepted']} "
            f"newly_admitted={snap['day_newly_admitted']} already_open={snap['day_already_open']} "
            f"shadow_trade_count={snap['day_shadow_trade_count']} "
            f"win/loss={_NA}/{_NA} PnL={_NA} PF={_NA}"
        ),
    ]
    return lines


def publish_pbv2_shadow_session_summary_for_finalize(
    summary: Mapping[str, Any],
    *,
    output_dir: Optional[Path] = None,
) -> dict[str, Any]:
    """AM/PM: one PBv2 Shadow Summary Discord. Fail-open. Notification-only."""
    kind = _session_kind_from_summary(summary)
    digest = get_pbv2_shadow_discord_digest(
        trace_dir=Path(output_dir) if output_dir else None
    )
    if output_dir is not None:
        digest.bind_trace_dir(Path(output_dir))
    open_n = int(digest.last_open_n or 0)
    cap = int(digest.last_cap or 0)
    try:
        from small_paper.v1r_native_entry_live import get_native_entry

        eng = get_native_entry()
        if eng is not None:
            snap = eng.shadow_pbv2.snapshot()
            open_n = int(snap.get("open_n") or open_n)
            cap = int(snap.get("cap") or cap)
            occ_exits = int(snap.get("exits") or 0)
            with digest._lock:
                if occ_exits > int(digest.exits):
                    digest.exits = occ_exits
    except Exception:
        pass
    trading_date = str(
        summary.get("trading_date") or summary.get("day_stamp") or summary.get("output_date") or ""
    )
    if kind in ("AM", "PM"):
        return digest.flush_session_summary(
            session_kind=kind,
            trading_date=trading_date,
            open_n=open_n,
            cap=cap,
            allow_empty=True,
        )
    # Daily leftover only if AM/PM summaries were never sent this process.
    if not digest.flushed_session_kinds:
        return digest.flush_session_summary(
            session_kind="DAILY",
            trading_date=trading_date,
            open_n=open_n,
            cap=cap,
            allow_empty=False,
        )
    return {"flushed": False, "reason": "daily_uses_am_pm_totals", "session_kind": kind or "DAILY"}
