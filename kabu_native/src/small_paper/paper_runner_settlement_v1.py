"""Paper causal clock. The live launcher arms it. The offline flag does not.

TRADEBOT_PAPER_EXECUTION_CAUSAL=0 keeps the immediate-fill clock.
Unset stays immediate until the live launcher sets the paper flag.
TRADEBOT_PAPER_EXECUTION_OFFLINE is replay-only and is removed for a live start.
"""
from __future__ import annotations

import os

from research.anchor_timing_robustness.grid import hm_epoch

CAUSAL_ENV = "TRADEBOT_PAPER_EXECUTION_CAUSAL"
OFFLINE_ENV = "TRADEBOT_PAPER_EXECUTION_OFFLINE"
_OFF = {"0", "false", "no", "off"}
_ON = {"1", "true", "yes", "on"}


def _raw(name: str) -> str:
    return str(os.environ.get(name, "")).strip().lower()


def causal_explicitly_off() -> bool:
    return _raw(CAUSAL_ENV) in _OFF


def causal_paper_enabled() -> bool:
    """True only when the paper flag is explicitly on. Offline is not consulted."""
    return _raw(CAUSAL_ENV) in _ON


def prepare_live_paper_execution_env() -> dict[str, str]:
    """Arm causal fills for the normal paper start and drop the offline flag."""
    os.environ.pop(OFFLINE_ENV, None)
    if causal_explicitly_off():
        os.environ[CAUSAL_ENV] = "0"
    else:
        os.environ[CAUSAL_ENV] = "1"
    return {
        "paper_causal": os.environ[CAUSAL_ENV],
        "offline": "",
    }


def _mark(day: str, kind: str) -> tuple[float, float, float]:
    if str(kind).lower() == "pm":
        return (
            float(hm_epoch(day, 15, 20)),
            float(hm_epoch(day, 15, 20)),
            float(hm_epoch(day, 15, 25)),
        )
    return (
        float(hm_epoch(day, 11, 20)),
        float(hm_epoch(day, 11, 25)),
        float(hm_epoch(day, 11, 30)),
    )


def feed_session_end(session_end: str, *, kind: str | None) -> str:
    """Keep the quote loop through the AM settlement minute when paper causal is on."""
    if not causal_paper_enabled():
        return session_end
    if str(kind or "").lower() == "am" and str(session_end) in {"11:25", "11:25:00"}:
        return "11:30"
    return session_end


def runner_close_action(kind: str, *, day: str, now_epoch: float | None = None) -> str:
    """legacy: caller uses the immediate-fill force-close clock.

    continue: keep feeding quotes. hold: settlement only, do not stop.
    stop: record residuals, then stop the runner.
    """
    if not causal_paper_enabled():
        return "legacy"
    if now_epoch is None:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        clock = datetime.now(ZoneInfo("Asia/Tokyo"))
        now_epoch = float(hm_epoch(clock.strftime("%Y%m%d"), clock.hour, clock.minute)) + clock.second
    entry_stop, exit_request, settlement_end = _mark(day, kind)
    if float(now_epoch) + 1e-9 >= settlement_end:
        return "stop"
    if float(now_epoch) + 1e-9 >= min(entry_stop, exit_request):
        return "hold"
    return "continue"
