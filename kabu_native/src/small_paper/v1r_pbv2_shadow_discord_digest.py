"""PBv2 SHADOW_ONLY Discord digest — aggregate trade-research notifies.

Internal shadow ledger (accept/exit/cap) stays hot-path.
Discord to trade-research is windowed (fixed V1R anchor or 5 minutes).
Never mutates Arch E / V1R Primary occupancy.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from small_paper.v1r_primary_runtime import CLOCK_GRID

JST = ZoneInfo("Asia/Tokyo")

# Prefer fixed-anchor flush when inside an anchor minute; otherwise 5-minute buckets.
DIGEST_FALLBACK_SEC = 300


def _now() -> datetime:
    return datetime.now(JST)


def _anchor_label(dt: datetime) -> Optional[str]:
    if (dt.hour, dt.minute) in CLOCK_GRID:
        return f"{dt.hour:02d}:{dt.minute:02d}"
    return None


def _window_id(dt: datetime) -> str:
    """5-minute wall clock bucket id (JST), e.g. 20260812|13:00."""
    bucket_min = (dt.minute // 5) * 5
    return f"{dt.strftime('%Y%m%d')}|{dt.hour:02d}:{bucket_min:02d}"


@dataclass
class PBv2ShadowDiscordDigest:
    """Accumulate SHADOW_ONLY divert attempts; flush one Discord message per window."""

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
            # Caller should flush before rotate; rotate empty if needed
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
        with self._lock:
            self.suppressed_immediate += 1
            self.evaluated += 1
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
                return self._flush_unlocked(trading_date=trading_date, open_n=open_n, cap=cap)
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
        with self._lock:
            if not self._flush_due_unlocked(force=force):
                return {"flushed": False, "window_id": self.window_id}
            return self._flush_unlocked(trading_date=trading_date, open_n=open_n, cap=cap)

    def _flush_due_unlocked(self, *, force: bool = False) -> bool:
        has = self.evaluated > 0 or self.accepted > 0 or self.exits > 0
        if force:
            return has
        if not has:
            return False
        dt = _now()
        if self.window_id and _window_id(dt) != self.window_id:
            return True
        if self.window_started_mono and (
            time.monotonic() - self.window_started_mono >= float(self.interval_sec)
        ):
            return True
        # Fixed V1R anchor: flush early in the minute if content accumulated in prior seconds
        if _anchor_label(dt) is not None and dt.second <= 2:
            if self.last_flush_mono <= 0 or (time.monotonic() - self.last_flush_mono) >= 60.0:
                return True
        return False

    def _flush_unlocked(
        self,
        *,
        trading_date: str = "",
        open_n: int = 0,
        cap: int = 0,
    ) -> dict[str, Any]:
        dt = _now()
        wid = self.window_id or _window_id(dt)
        anchor = _anchor_label(dt) or wid.split("|")[-1]
        payload = {
            "digest": True,
            "date": trading_date or dt.strftime("%Y%m%d"),
            "window_id": wid,
            "anchor": anchor,
            "role": "SHADOW_ONLY",
            "source": "pbv2_shadow",
            "status": "DIGEST",
            "evaluated": int(self.evaluated),
            "accepted": int(self.accepted),
            "already_open": int(self.already_open),
            "cap_blocked": int(self.cap_blocked),
            "exits": int(self.exits),
            "symbols": list(self.symbols_accepted),
            "symbols_seen_n": len(self.symbols_seen),
            "hypothetical_fills": int(self.accepted),  # shadow admit == hypothetical fill seed
            "pnl": "SHADOW_DIGEST",
            "trades": int(self.accepted),
            "open_n": int(open_n),
            "cap": int(cap),
            "note": (
                "SHADOW_ONLY digest — Primary occupancy unchanged; "
                f"suppressed_immediate={self.suppressed_immediate}"
            ),
            "suppressed_immediate": int(self.suppressed_immediate),
        }
        result = self._publish(payload)
        self._append_audit({"event": "PBV2_SHADOW_DIGEST_FLUSH", **payload, **result})
        # reset window counters
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
        return {"flushed": True, "window_id": wid, "payload": payload, **result}

    def _publish(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            from notify.v1r_discord_routing import V1RNotifyKind, publish_v1r

            r = publish_v1r(
                V1RNotifyKind.PBV2_SHADOW,
                payload,
                test_only=False,
                sync_http=False,
                session_id=f"pbv2-digest-{payload.get('window_id')}",
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
