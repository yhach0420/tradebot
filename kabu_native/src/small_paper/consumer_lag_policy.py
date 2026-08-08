"""Paper consumer lag policy — NORMAL / DEGRADED / REALTIME_RESYNC / POSITION_RECOVERY.

Uses lag count, rates, and estimated catch-up time — not fixed count alone.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

STATE_NORMAL = "NORMAL"
STATE_DEGRADED = "DEGRADED"
STATE_REALTIME_RESYNC_REQUIRED = "REALTIME_RESYNC_REQUIRED"
STATE_POSITION_RECOVERY_REQUIRED = "POSITION_RECOVERY_REQUIRED"

REASON_CONSUMER_LAG = "CONSUMER_LAG"
REASON_REALTIME_RESYNC = "CONSUMER_LAG_REALTIME_RESYNC"
REASON_POSITION_RECOVERY = "CONSUMER_LAG_POSITION_RECOVERY"

# Defaults tuned from 20260804/20260805 evidence (pub ~60-80/s, paper often ~40/s).
DEFAULT_DEGRADED_LAG = 1000
DEFAULT_RESYNC_LAG = 5000
DEFAULT_RESYNC_CATCHUP_SEC = 90.0
DEFAULT_WARMUP_LOOKBACK_EVENTS = 2000


@dataclass
class LagPolicyInput:
    lag: int
    publisher_rate: float = 0.0
    consumer_rate: float = 0.0
    ack_rate: float = 0.0
    open_positions: int = 0
    queue_depth: int = 0


@dataclass
class LagPolicyDecision:
    state: str
    entry_block: bool
    allow_realtime_resync: bool
    allow_skip_backlog: bool
    reason: str
    estimated_catchup_sec: Optional[float]
    detail: dict[str, Any]


def estimated_catchup_seconds(
    lag: int,
    *,
    publisher_rate: float,
    consumer_rate: float,
    ack_rate: float = 0.0,
) -> Optional[float]:
    """Seconds to clear lag if rates hold. None if cannot catch up (rate <= pub)."""
    lag_i = max(0, int(lag))
    if lag_i <= 0:
        return 0.0
    proc = max(float(consumer_rate or 0.0), float(ack_rate or 0.0))
    pub = max(0.0, float(publisher_rate or 0.0))
    net = proc - pub
    if net <= 0.1:
        return None  # diverging or stalled
    return float(lag_i) / net


def evaluate_lag_policy(
    inp: LagPolicyInput,
    *,
    degraded_lag: int = DEFAULT_DEGRADED_LAG,
    resync_lag: int = DEFAULT_RESYNC_LAG,
    resync_catchup_sec: float = DEFAULT_RESYNC_CATCHUP_SEC,
) -> LagPolicyDecision:
    lag = max(0, int(inp.lag))
    open_n = max(0, int(inp.open_positions))
    catchup = estimated_catchup_seconds(
        lag,
        publisher_rate=inp.publisher_rate,
        consumer_rate=inp.consumer_rate,
        ack_rate=inp.ack_rate,
    )
    detail = {
        "lag": lag,
        "publisher_rate": inp.publisher_rate,
        "consumer_rate": inp.consumer_rate,
        "ack_rate": inp.ack_rate,
        "open_positions": open_n,
        "queue_depth": int(inp.queue_depth),
        "estimated_catchup_sec": catchup,
        "degraded_lag": int(degraded_lag),
        "resync_lag": int(resync_lag),
        "resync_catchup_sec": float(resync_catchup_sec),
    }

    if lag < int(degraded_lag) and (catchup is None or catchup < float(resync_catchup_sec)):
        if lag <= 0:
            return LagPolicyDecision(
                state=STATE_NORMAL,
                entry_block=False,
                allow_realtime_resync=False,
                allow_skip_backlog=False,
                reason="",
                estimated_catchup_sec=catchup,
                detail=detail,
            )
        # small lag that is catching up → stay NORMAL (natural catch-up)
        if catchup is not None and catchup < float(resync_catchup_sec):
            return LagPolicyDecision(
                state=STATE_NORMAL,
                entry_block=False,
                allow_realtime_resync=False,
                allow_skip_backlog=False,
                reason="",
                estimated_catchup_sec=catchup,
                detail=detail,
            )

    backlog_bad = lag >= int(resync_lag) or (
        catchup is None and lag >= int(degraded_lag)
    ) or (catchup is not None and catchup >= float(resync_catchup_sec) and lag >= int(degraded_lag))

    if backlog_bad and open_n > 0:
        return LagPolicyDecision(
            state=STATE_POSITION_RECOVERY_REQUIRED,
            entry_block=True,
            allow_realtime_resync=False,
            allow_skip_backlog=False,
            reason=REASON_POSITION_RECOVERY,
            estimated_catchup_sec=catchup,
            detail=detail,
        )

    if backlog_bad and open_n == 0:
        return LagPolicyDecision(
            state=STATE_REALTIME_RESYNC_REQUIRED,
            entry_block=True,
            allow_realtime_resync=True,
            allow_skip_backlog=True,
            reason=REASON_REALTIME_RESYNC,
            estimated_catchup_sec=catchup,
            detail=detail,
        )

    # DEGRADED: lag growing but not yet resync threshold
    return LagPolicyDecision(
        state=STATE_DEGRADED,
        entry_block=True,
        allow_realtime_resync=False,
        allow_skip_backlog=False,
        reason=REASON_CONSUMER_LAG,
        estimated_catchup_sec=catchup,
        detail=detail,
    )


def read_ingress_status(native_root: Any, trading_date: str) -> dict[str, Any]:
    from pathlib import Path

    p = Path(native_root) / "data" / "market_capture" / str(trading_date) / "ingress_status.json"
    if not p.is_file():
        return {}
    try:
        import json

        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
