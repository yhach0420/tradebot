"""Phase687W9 — Dual WebSocket probe + single-ingress gateway scaffolding.

Does not assume official multi-WS guarantee. Default Paper ingress remains KABU_DIRECT.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence

TOPOLOGY_PASSIVE_DUAL = "PASSIVE_DUAL_WEBSOCKET"
TOPOLOGY_SINGLE_INGRESS = "SINGLE_INGRESS_LOCAL_FANOUT"

PUSH_SOURCE_KABU_DIRECT = "KABU_DIRECT"
PUSH_SOURCE_LOCAL_GATEWAY = "LOCAL_CAPTURE_GATEWAY"

DUAL_WS_COMPATIBLE = "DUAL_WS_COMPATIBLE"
DUAL_WS_CONNECTION_ONLY_UNVERIFIED = "DUAL_WS_CONNECTION_ONLY_UNVERIFIED"
DUAL_WS_INCOMPATIBLE = "DUAL_WS_INCOMPATIBLE"
DUAL_WS_RECONNECT_STORM = "DUAL_WS_RECONNECT_STORM"
DUAL_WS_EVENT_DIVERGENCE = "DUAL_WS_EVENT_DIVERGENCE"


@dataclass
class DualWsProbeResult:
    status: str
    primary_open: bool = False
    secondary_open: bool = False
    primary_stayed_open: bool = False
    registration_unchanged: bool = False
    token_race: bool = False
    reconnect_storm: bool = False
    event_count_primary: int = 0
    event_count_secondary: int = 0
    event_divergence: bool = False
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "primary_open": self.primary_open,
            "secondary_open": self.secondary_open,
            "primary_stayed_open": self.primary_stayed_open,
            "registration_unchanged": self.registration_unchanged,
            "token_race": self.token_race,
            "reconnect_storm": self.reconnect_storm,
            "event_count_primary": self.event_count_primary,
            "event_count_secondary": self.event_count_secondary,
            "event_divergence": self.event_divergence,
            "notes": list(self.notes),
            "topology_preferred": TOPOLOGY_PASSIVE_DUAL,
            "official_multi_ws_guaranteed": False,
        }


def dual_websocket_compatibility_probe(
    *,
    open_primary: Callable[[], bool],
    open_secondary: Callable[[], bool],
    primary_still_open: Callable[[], bool],
    registration_before: Sequence[str],
    registration_after: Sequence[str],
    reconnect_count: int = 0,
    reconnect_storm_threshold: int = 5,
    primary_events: Optional[Sequence[Any]] = None,
    secondary_events: Optional[Sequence[Any]] = None,
    weekend_connection_only: bool = False,
) -> DualWsProbeResult:
    """Classify dual-WS compatibility without assuming vendor guarantee."""
    notes: list[str] = []
    p_ok = bool(open_primary())
    s_ok = bool(open_secondary())
    stayed = bool(primary_still_open()) if p_ok else False
    reg_ok = sorted(registration_before) == sorted(registration_after)
    storm = reconnect_count >= reconnect_storm_threshold

    pe = list(primary_events or [])
    se = list(secondary_events or [])
    divergence = False
    if pe and se:
        # compare payload hashes in order for overlapping prefix
        n = min(len(pe), len(se))
        for i in range(n):
            hp = _payload_hash(pe[i])
            hs = _payload_hash(se[i])
            if hp != hs:
                divergence = True
                break
        if abs(len(pe) - len(se)) > max(3, int(0.05 * max(len(pe), len(se), 1))):
            divergence = True
            notes.append("event_count_delta_high")

    if storm:
        return DualWsProbeResult(
            status=DUAL_WS_RECONNECT_STORM,
            primary_open=p_ok,
            secondary_open=s_ok,
            primary_stayed_open=stayed,
            registration_unchanged=reg_ok,
            reconnect_storm=True,
            event_count_primary=len(pe),
            event_count_secondary=len(se),
            event_divergence=divergence,
            notes=notes + ["reconnect_storm"],
        )
    if not p_ok or not s_ok or not stayed or not reg_ok:
        return DualWsProbeResult(
            status=DUAL_WS_INCOMPATIBLE,
            primary_open=p_ok,
            secondary_open=s_ok,
            primary_stayed_open=stayed,
            registration_unchanged=reg_ok,
            reconnect_storm=False,
            event_count_primary=len(pe),
            event_count_secondary=len(se),
            event_divergence=divergence,
            notes=notes + ["connection_or_registration_failed"],
        )
    if weekend_connection_only or (not pe and not se):
        return DualWsProbeResult(
            status=DUAL_WS_CONNECTION_ONLY_UNVERIFIED,
            primary_open=True,
            secondary_open=True,
            primary_stayed_open=True,
            registration_unchanged=reg_ok,
            event_count_primary=len(pe),
            event_count_secondary=len(se),
            notes=notes + ["weekend_or_no_push_events"],
        )
    if divergence:
        return DualWsProbeResult(
            status=DUAL_WS_EVENT_DIVERGENCE,
            primary_open=True,
            secondary_open=True,
            primary_stayed_open=True,
            registration_unchanged=reg_ok,
            event_count_primary=len(pe),
            event_count_secondary=len(se),
            event_divergence=True,
            notes=notes,
        )
    return DualWsProbeResult(
        status=DUAL_WS_COMPATIBLE,
        primary_open=True,
        secondary_open=True,
        primary_stayed_open=True,
        registration_unchanged=reg_ok,
        event_count_primary=len(pe),
        event_count_secondary=len(se),
        event_divergence=False,
        notes=notes,
    )


def _payload_hash(payload: Any) -> str:
    raw = repr(payload).encode("utf-8", errors="replace")
    return hashlib.sha256(raw).hexdigest()


@dataclass
class GatewayFanoutStats:
    received: int = 0
    captured: int = 0
    fanout: int = 0
    loss: int = 0
    duplicate: int = 0
    order_inversion: int = 0
    latencies_ms: list[float] = field(default_factory=list)

    def percentile(self, p: float) -> float:
        if not self.latencies_ms:
            return 0.0
        xs = sorted(self.latencies_ms)
        idx = min(len(xs) - 1, max(0, int(round((p / 100.0) * (len(xs) - 1)))))
        return xs[idx]


class MarketCaptureGateway:
    """
    SINGLE_INGRESS_LOCAL_FANOUT scaffold.

    Capture first, then localhost-only fanout to Paper PushSource.
    Default Paper mode remains KABU_DIRECT — do not auto-switch on Monday.
    """

    def __init__(self, *, capture_append: Callable[[Any], None], fanout_send: Callable[[Any], None]) -> None:
        self._capture = capture_append
        self._fanout = fanout_send
        self.stats = GatewayFanoutStats()
        self._last_seq: Optional[int] = None
        self._seen_hashes: set[str] = set()
        self.localhost_only = True
        self.mutate_payload = False
        self.fanout_credentials = False

    def on_message(self, payload: Any, *, seq: Optional[int] = None) -> None:
        t0 = time.perf_counter()
        self.stats.received += 1
        h = _payload_hash(payload)
        if h in self._seen_hashes:
            self.stats.duplicate += 1
        else:
            self._seen_hashes.add(h)
        if seq is not None and self._last_seq is not None and seq < self._last_seq:
            self.stats.order_inversion += 1
        if seq is not None:
            self._last_seq = seq
        # capture first
        self._capture(payload)
        self.stats.captured += 1
        # then fanout (same payload, no mutation, no credentials)
        self._fanout(payload)
        self.stats.fanout += 1
        self.stats.latencies_ms.append((time.perf_counter() - t0) * 1000.0)

    def parity_report(self) -> dict[str, Any]:
        loss = max(0, self.stats.received - self.stats.captured)
        self.stats.loss = loss
        return {
            "mode": TOPOLOGY_SINGLE_INGRESS,
            "default_paper_push_source": PUSH_SOURCE_KABU_DIRECT,
            "auto_switch_forbidden": True,
            "localhost_only": self.localhost_only,
            "fanout_credentials": self.fanout_credentials,
            "payload_mutation": self.mutate_payload,
            "received": self.stats.received,
            "captured": self.stats.captured,
            "fanout": self.stats.fanout,
            "loss": self.stats.loss,
            "duplicate": self.stats.duplicate,
            "order_inversion": self.stats.order_inversion,
            "relay_latency_p50_ms": self.stats.percentile(50),
            "relay_latency_p95_ms": self.stats.percentile(95),
            "parity_pass": (
                self.stats.loss == 0
                and self.stats.duplicate == 0
                and self.stats.order_inversion == 0
                and self.stats.captured == self.stats.fanout == self.stats.received
            ),
        }


def run_gateway_synthetic_parity(n: int = 100_000) -> dict[str, Any]:
    captured: list[Any] = []
    fanout: list[Any] = []
    gw = MarketCaptureGateway(capture_append=captured.append, fanout_send=fanout.append)
    for i in range(n):
        payload = {"Symbol": f"{1000 + (i % 50)}", "seq": i, "CurrentPrice": i % 1000}
        gw.on_message(payload, seq=i)
    report = gw.parity_report()
    # hash equality check
    hash_match = all(_payload_hash(a) == _payload_hash(b) for a, b in zip(captured, fanout))
    report["payload_hash_match"] = hash_match
    report["n"] = n
    report["parity_pass"] = bool(report["parity_pass"] and hash_match)
    return report
