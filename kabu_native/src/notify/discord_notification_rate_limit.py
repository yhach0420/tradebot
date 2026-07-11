"""Phase687W10 — Category rate limits for Discord notifications."""

from __future__ import annotations

import threading
import time
from typing import Any

from notify.discord_notification_model import NotificationCategory

# seconds
LIMITS: dict[str, float] = {
    NotificationCategory.TRADE_ACTUAL.value: 0.0,  # dedupe only
    NotificationCategory.CAP_BLOCKED.value: 0.0,  # 1 per symbol/session/reason via dedupe
    NotificationCategory.OPERATIONS.value: 15 * 60,
    NotificationCategory.MARKET_CAPTURE.value: 15 * 60,
    NotificationCategory.RESEARCH_SHADOW.value: 0.0,  # AM/PM only by caller
    NotificationCategory.CRITICAL_SAFETY.value: 30 * 60,
    NotificationCategory.SESSION_SUMMARY.value: 0.0,
}


class RateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last: dict[str, float] = {}

    def allow(self, *, category: str, state_key: str, severity: str = "INFO") -> dict[str, Any]:
        """Return {allow, reason}. CRITICAL first-shot always allowed; continuation uses 30m."""
        key = f"{category}|{state_key}"
        cooldown = float(LIMITS.get(category, 0.0))
        with self._lock:
            now = time.monotonic()
            prev = self._last.get(key)
            if prev is None:
                self._last[key] = now
                return {"allow": True, "reason": "first"}
            if cooldown <= 0:
                self._last[key] = now
                return {"allow": True, "reason": "no_cooldown"}
            if severity == "CRITICAL" and (now - prev) < cooldown:
                # first already sent; suppress until window unless caller uses severity upgrade path
                return {"allow": False, "reason": f"critical_cooldown_{int(cooldown)}s"}
            if (now - prev) < cooldown:
                return {"allow": False, "reason": f"rate_limited_{int(cooldown)}s"}
            self._last[key] = now
            return {"allow": True, "reason": "cooldown_elapsed"}

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"keys": len(self._last), "limits": dict(LIMITS)}
