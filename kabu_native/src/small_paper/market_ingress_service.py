"""Independent Market Ingress — sole Kabu WebSocket owner (V2).

Raw-first: PUSH → Envelope → Raw JSONL → Local Bus → Paper/Observer.
Paper never owns WebSocket under MARKET_INGRESS_V2.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any, Callable, Optional, Sequence
from zoneinfo import ZoneInfo

from small_paper.local_market_bus import LocalMarketBusPublisher, bus_host, bus_port
from small_paper.market_ingress_health import build_ingress_heartbeat, write_heartbeat, write_status_json
from small_paper.market_ingress_protocol import (
    KIND_ENTRY_BLOCK,
    KIND_ENTRY_UNBLOCK,
    KIND_MARKET_PUSH,
    MarketEnvelope,
    now_iso,
)
from small_paper.market_ingress_state import (
    CLOSING_STALE_SOCKET,
    CONNECTING,
    ENTRY_BLOCKED,
    RECOVERED,
    RECOVERY_FAILED,
    RECONNECTING,
    REGISTERING,
    REREGISTERING,
    RUNNING,
    STARTING,
    STALE_DETECTED,
    STOPPED,
    STOPPING,
    STORAGE_BLOCKED,
    WAITING_FIRST_PUSH,
    IngressStateMachine,
)
from small_paper.market_raw_writer import MarketRawWriter, session_dir, trading_date_jst

JST = ZoneInfo("Asia/Tokyo")
NATIVE_ROOT = Path(__file__).resolve().parents[2]

SILENCE_STALE_SEC = 90.0
RECOVERY_BACKOFFS = (0.0, 5.0, 15.0)
MAX_FAST_RECOVERY_ATTEMPTS = 3
FINALIZE_TIME = dtime(15, 35)
LUNCH_START = dtime(11, 30)
LUNCH_END = dtime(12, 30)
MARKET_AM_START = dtime(9, 0)
MARKET_PM_END = dtime(15, 30)


def is_market_session_jst(now: Optional[datetime] = None) -> bool:
    n = now or datetime.now(JST)
    t = n.timetz().replace(tzinfo=None)
    if LUNCH_START <= t < LUNCH_END:
        return False
    if MARKET_AM_START <= t < LUNCH_START:
        return True
    if LUNCH_END <= t < MARKET_PM_END:
        return True
    return False


def make_ingress_session_id() -> str:
    return f"ing_{trading_date_jst()}_{os.getpid()}_{int(time.time())}_{uuid.uuid4().hex[:8]}"


class MarketIngressService:
    def __init__(
        self,
        *,
        native_root: Path,
        trading_date: Optional[str] = None,
        silence_stale_sec: float = SILENCE_STALE_SEC,
        enable_tcp_bus: bool = True,
        bus_port_override: Optional[int] = None,
        synthetic: bool = False,
        on_notify: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self.native_root = Path(native_root)
        self.trading_date = trading_date or trading_date_jst()
        self.silence_stale_sec = float(silence_stale_sec)
        self.synthetic = bool(synthetic)
        self.on_notify = on_notify
        self.session_id = make_ingress_session_id()
        self.sm = IngressStateMachine()
        self.desired_symbols: list[str] = []
        self.registered_symbols: list[str] = []
        self.position_symbols: list[str] = []
        self._last_push_mono: Optional[float] = None
        self._last_push_at: str = ""
        self._stop = threading.Event()
        self._receiver_task: Any = None
        self._receiver_count = 0
        self._push_client: Any = None
        self._ws_loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._inject_queue: list[dict[str, Any]] = []
        self._paper_seen_sequences: set[int] = set()
        self._warmup_until_mono: float = 0.0
        self._lat_raw: list[float] = []
        self._lat_pub: list[float] = []

        self.session_path = session_dir(self.native_root, self.trading_date, self.session_id)
        self.session_path.mkdir(parents=True, exist_ok=True)
        self.writer = MarketRawWriter(
            output_dir=self.session_path,
            ingress_session_id=self.session_id,
        )
        self.bus = LocalMarketBusPublisher(
            host=bus_host(),
            port=int(bus_port_override if bus_port_override is not None else bus_port()),
            enable_tcp=enable_tcp_bus,
            ingress_session_id=self.session_id,
        )
        self.bus.on_ack = self._on_consumer_ack
        self._first_recovered_sequence: Optional[int] = None
        self._pending_recovery_success = False
        self.day_root = self.native_root / "data" / "market_capture" / self.trading_date
        self.day_root.mkdir(parents=True, exist_ok=True)

    def _on_consumer_ack(self, consumer_id: str, result: Any) -> None:
        if consumer_id == "paper_runtime":
            self.maybe_promote_running(reason="paper_ack")

    def _notify(self, title: str, body: str) -> None:
        if self.on_notify:
            try:
                self.on_notify(title, body)
            except Exception:
                pass

    def set_desired_universe(
        self,
        symbols: Sequence[str],
        *,
        generation: Optional[int] = None,
        position_symbols: Optional[Sequence[str]] = None,
    ) -> dict[str, Any]:
        """Universe manager → Ingress registration request (generation-ordered)."""
        with self._lock:
            if generation is not None and int(generation) < self.sm.registration_generation:
                return {
                    "ok": False,
                    "reason": "stale_generation",
                    "current": self.sm.registration_generation,
                    "requested": int(generation),
                }
            self.desired_symbols = [str(s).split(".")[0] for s in symbols]
            if position_symbols is not None:
                self.position_symbols = [str(s).split(".")[0] for s in position_symbols]
            # merge positions into desired
            merged = list(dict.fromkeys(self.desired_symbols + self.position_symbols))
            self.desired_symbols = merged
            gen = self.sm.bump_registration() if generation is None else int(generation)
            if generation is not None:
                self.sm.registration_generation = max(self.sm.registration_generation, int(generation))
                gen = self.sm.registration_generation
            if self.synthetic:
                self.registered_symbols = list(self.desired_symbols)
            return {
                "ok": True,
                "registration_generation": gen,
                "desired_count": len(self.desired_symbols),
                "desired_symbols": list(self.desired_symbols),
            }

    def start(self) -> None:
        self.sm.transition(STARTING, reason="start")
        self.bus.start()
        self._write_manifest()
        self._stop.clear()
        if self.synthetic:
            self._thread = threading.Thread(target=self._synthetic_loop, name="ingress-synth", daemon=True)
        else:
            self._thread = threading.Thread(target=self._live_thread, name="ingress-live", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.sm.transition(STOPPING, reason="stop")
        self._stop.set()
        if self._ws_loop is not None:
            try:
                self._ws_loop.call_soon_threadsafe(lambda: None)
            except Exception:
                pass
        # Never join the calling thread (synthetic loop may invoke stop()).
        if self._thread and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=5.0)
        self.writer.close()
        self.bus.stop()
        self._finalize_seal()
        self.sm.transition(STOPPED, reason="stopped")
        self._write_status()

    def inject_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Test / synthetic inject — same Raw-first path as live PUSH."""
        return self._on_push(payload)

    def force_stale_for_test(self) -> None:
        self._last_push_mono = time.monotonic() - (self.silence_stale_sec + 5.0)

    def _write_manifest(self) -> None:
        man = {
            "schema_version": "ingress_v2.1",
            "ingress_session_id": self.session_id,
            "trading_date": self.trading_date,
            "started_at": now_iso(),
            "pid": os.getpid(),
            "topology": "INDEPENDENT_MARKET_INGRESS",
            "websocket_owner": "MARKET_INGRESS_SERVICE",
            "capture_source": "INGRESS_RAW_WRITER",
            "legacy_paper_websocket": "DISABLED",
            "legacy_capture_fanout": "DISABLED",
            "bus_port": self.bus.port,
            "DEMO_SESSION": bool(self.synthetic),
            "synthetic": bool(self.synthetic),
        }
        (self.session_path / "manifest.json").write_text(
            json.dumps(man, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        write_status_json(self.day_root / "ingress_active_session.json", man)

    def _write_status(self) -> None:
        snap = self.health_snapshot()
        write_status_json(self.session_path / "status.json", snap)
        write_status_json(self.day_root / "ingress_status.json", snap)
        write_heartbeat(self.session_path / "heartbeat.jsonl", snap)

    def health_snapshot(self) -> dict[str, Any]:
        age = 0.0
        if self._last_push_mono is not None:
            age = max(0.0, time.monotonic() - self._last_push_mono)
        pub = self.bus.publisher_health()
        cons = self.bus.consumer_health()
        paper = cons.get("paper_runtime") or {}
        return build_ingress_heartbeat(
            ingress_session_id=self.session_id,
            pid=os.getpid(),
            state=self.sm.state,
            last_push_at=self._last_push_at,
            push_age_sec=age,
            connection_generation=self.sm.connection_generation,
            registration_generation=self.sm.registration_generation,
            desired_symbol_count=len(self.desired_symbols),
            registered_symbol_count=len(self.registered_symbols),
            raw_last_sequence=int(self.writer.snapshot().get("last_sequence") or 0),
            raw_last_write_at=self.writer.last_write_at,
            publisher_last_sequence=int(pub.get("last_published_sequence") or 0),
            paper_consumer_last_ack=int(paper.get("last_ack_sequence") or paper.get("last_ack") or 0),
            paper_consumer_lag=int(paper.get("lag") or 0),
            reconnect_attempt=self.sm.recovery_attempt,
            recovery_count=self.sm.recovery_count,
            recovery_success_count=self.sm.recovery_success_count,
            storage_error_count=self.writer.storage_errors,
            extra={
                "receiver_task_count": self._receiver_count if not self.synthetic else 1,
                "entry_blocked": self.sm.entry_blocked,
                "entry_block_reason": self.sm.entry_block_reason or None,
                "latency": self.latency_stats(),
                "bus": pub,
                "paper_consumer_transport": paper.get("transport"),
                "paper_consumer_ready": bool(paper.get("ready")),
                "paper_consumer_connected": bool(paper.get("connected")),
                "first_recovered_sequence": self._first_recovered_sequence,
            },
        )

    def readiness_conditions(self) -> dict[str, Any]:
        """Strict RUNNING / ENTRY unblock conditions."""
        pub = self.bus.publisher_health()
        cons = self.bus.consumer_health().get("paper_runtime") or {}
        raw_seq = int(self.writer.snapshot().get("last_sequence") or 0)
        pub_seq = int(pub.get("last_published_sequence") or 0)
        ack = int(cons.get("last_ack_sequence") or cons.get("last_ack") or 0)
        lag = int(cons.get("lag") or max(0, pub_seq - ack))
        expected = len(self.desired_symbols) or len(self.registered_symbols)
        registered = len(self.registered_symbols) or len(self.desired_symbols)
        tcp_ok = str(cons.get("transport") or "") == "TCP" and bool(cons.get("ready"))
        first_ok = self._last_push_mono is not None and raw_seq > 0
        if self._pending_recovery_success:
            need = int(self._first_recovered_sequence or (pub_seq + 1))
            ack_ok = ack >= need and lag == 0 and pub_seq > 0
        else:
            ack_ok = ack >= pub_seq and lag == 0 and pub_seq > 0
        return {
            "websocket_or_synthetic": True if self.synthetic else self._receiver_count == 1,
            "registered_ok": expected > 0 and registered == expected,
            "first_push": first_ok,
            "raw_ok": raw_seq > 0 and self.writer.storage_errors == 0,
            "publish_ok": pub_seq > 0 and int(pub.get("publish_fail") or 0) == 0,
            "tcp_paper_ready": tcp_ok,
            "ack_caught_up": ack_ok,
            "raw_seq": raw_seq,
            "pub_seq": pub_seq,
            "ack": ack,
            "lag": lag,
            "expected": expected,
            "registered": registered,
            "tcp_clients": int(pub.get("tcp_clients") or 0),
        }

    def maybe_promote_running(self, *, reason: str = "") -> bool:
        """Promote to RUNNING and clear ENTRY_BLOCK only when ACK lag is zero on TCP Paper."""
        c = self.readiness_conditions()
        if not (
            c["websocket_or_synthetic"]
            and c["registered_ok"]
            and c["first_push"]
            and c["raw_ok"]
            and c["publish_ok"]
            and c["tcp_paper_ready"]
            and c["ack_caught_up"]
        ):
            # Keep blocked while waiting for Paper ACK / TCP
            if c["first_push"] and (not c["tcp_paper_ready"] or not c["ack_caught_up"]):
                if not self.sm.entry_blocked:
                    self.sm.block_entry("recovery_warmup" if self._pending_recovery_success else "WAITING_PAPER_ACK")
            return False
        if self.sm.state != RUNNING:
            self.sm.transition(RUNNING, reason=reason or "ack_caught_up")
        if self.sm.entry_blocked:
            self.sm.unblock_entry()
            self._publish_control(KIND_ENTRY_UNBLOCK, "paper_ack_caught_up")
        if self._pending_recovery_success:
            self.sm.recovery_success_count += 1
            self._pending_recovery_success = False
            self._first_recovered_sequence = None
            self._notify("[INGRESS RECOVERED]", f"ack_caught_up attempt={self.sm.recovery_attempt}")
        return True

    def latency_stats(self) -> dict[str, Any]:
        def _pct(xs: list[float], p: float) -> float:
            if not xs:
                return 0.0
            s = sorted(xs)
            i = min(len(s) - 1, max(0, int(round((p / 100.0) * (len(s) - 1)))))
            return float(s[i])

        return {
            "raw_write_ms": {
                "p50": _pct(self._lat_raw, 50),
                "p95": _pct(self._lat_raw, 95),
                "p99": _pct(self._lat_raw, 99),
                "n": len(self._lat_raw),
            },
            "ingress_to_publish_ms": {
                "p50": _pct(self._lat_pub, 50),
                "p95": _pct(self._lat_pub, 95),
                "p99": _pct(self._lat_pub, 99),
                "n": len(self._lat_pub),
            },
        }

    def _on_push(self, payload: dict[str, Any]) -> dict[str, Any]:
        t0 = time.perf_counter()
        received = now_iso()
        sym = str(payload.get("Symbol") or payload.get("symbol") or "")
        event_time = str(
            payload.get("CurrentPriceTime")
            or payload.get("current_price_time")
            or received
        )
        # STORAGE / ENTRY block paths
        if self.writer.status == "STORAGE_BLOCKED" and self.sm.state != STORAGE_BLOCKED:
            self.sm.transition(STORAGE_BLOCKED, reason="storage")
            self.sm.block_entry("INGRESS_STORAGE_BLOCKED")
            self._publish_control(KIND_ENTRY_BLOCK, "INGRESS_STORAGE_BLOCKED")

        rec = {
            "schema_version": "ingress_v2.1",
            "kind": KIND_MARKET_PUSH,
            "received_at": received,
            "event_time": event_time,
            "symbol": sym,
            "connection_generation": self.sm.connection_generation,
            "registration_generation": self.sm.registration_generation,
            "original_payload": payload,
            "payload": payload,
        }
        wr = self.writer.write_envelope_record(rec)
        raw_ms = (time.perf_counter() - t0) * 1000.0
        self._lat_raw.append(raw_ms)
        if len(self._lat_raw) > 5000:
            self._lat_raw = self._lat_raw[-5000:]

        if not wr.ok:
            self.sm.transition(STORAGE_BLOCKED, reason=wr.error)
            self.sm.block_entry("INGRESS_STORAGE_BLOCKED")
            self._publish_control(KIND_ENTRY_BLOCK, "INGRESS_STORAGE_BLOCKED")
            return {"ok": False, "stage": "raw", "error": wr.error}

        # Raw success → publish (never before persist)
        env = MarketEnvelope(
            kind=KIND_MARKET_PUSH,
            ingress_session_id=self.session_id,
            sequence=wr.sequence,
            event_time=event_time,
            received_at=received,
            persisted_at=wr.persisted_at,
            published_at="",
            symbol=sym,
            payload=payload,
            connection_generation=self.sm.connection_generation,
            registration_generation=self.sm.registration_generation,
            capture_part=wr.part_name,
            raw_record_id=wr.raw_record_id,
            entry_blocked=self.sm.entry_blocked,
            entry_block_reason=self.sm.entry_block_reason,
        )
        t1 = time.perf_counter()
        self.bus.publish(env)
        pub_ms = (time.perf_counter() - t1) * 1000.0
        self._lat_pub.append(pub_ms)
        if len(self._lat_pub) > 5000:
            self._lat_pub = self._lat_pub[-5000:]

        self._last_push_at = received
        self._last_push_mono = time.monotonic()
        if self._first_recovered_sequence is None and self._pending_recovery_success:
            self._first_recovered_sequence = int(wr.sequence)
        # Do NOT promote to RUNNING / clear ENTRY_BLOCK until Paper TCP ACK catch-up.
        if self.bus.should_block_entry_for_lag():
            self.sm.block_entry("CONSUMER_LAG")
            self._publish_control(KIND_ENTRY_BLOCK, "CONSUMER_LAG")
        else:
            self.maybe_promote_running(reason="push_then_ack_check")

        return {
            "ok": True,
            "sequence": wr.sequence,
            "raw_ms": raw_ms,
            "pub_ms": pub_ms,
            "delivered_audit": False,
        }

    def _publish_control(self, kind: str, reason: str) -> None:
        env = MarketEnvelope(
            kind=kind,
            ingress_session_id=self.session_id,
            sequence=int(self.writer.snapshot().get("last_sequence") or 0),
            event_time=now_iso(),
            received_at=now_iso(),
            persisted_at="",
            published_at=now_iso(),
            symbol="",
            payload={},
            connection_generation=self.sm.connection_generation,
            registration_generation=self.sm.registration_generation,
            entry_blocked=(kind == KIND_ENTRY_BLOCK),
            entry_block_reason=reason,
            meta={"reason": reason},
        )
        self.bus.publish(env)

    def _synthetic_loop(self) -> None:
        self.sm.transition(CONNECTING, reason="synthetic")
        self.sm.bump_connection()
        self.sm.transition(REGISTERING, reason="synthetic")
        self.registered_symbols = list(self.desired_symbols) or ["7203", "6758"]
        self.sm.bump_registration()
        self.sm.transition(WAITING_FIRST_PUSH, reason="synthetic")
        while not self._stop.is_set():
            self._poll_desired_universe()
            self._poll_demo_control()
            self._maybe_silence_recovery(synthetic=True)
            with self._lock:
                batch = list(self._inject_queue)
                self._inject_queue.clear()
            try:
                from small_paper.ingress_control_channel import drain_demo_inject

                batch.extend(drain_demo_inject(self.native_root, max_rows=500))
            except Exception:
                pass
            for p in batch:
                self._on_push(p)
            self._write_status()
            # Synthetic mode ignores cash-session finalize clock (tests/preflight).
            if self._stop.is_set():
                break
            time.sleep(0.05)
        if self.sm.state not in (STOPPED, STOPPING):
            self.stop()

    def _poll_demo_control(self) -> None:
        """Cross-process demo commands (force_stale / stop). Live path never uses this."""
        try:
            from small_paper.ingress_control_channel import drain_demo_control

            for cmd in drain_demo_control(self.native_root):
                name = str(cmd.get("cmd") or "")
                if name == "force_stale":
                    self.force_stale_for_test()
                elif name == "stop":
                    self._stop.set()
        except Exception:
            pass

    def _poll_desired_universe(self) -> None:
        try:
            from small_paper.ingress_control_channel import read_desired_universe

            req = read_desired_universe(self.native_root)
            if not req:
                return
            gen = int(req.get("generation") or 0)
            if gen and gen < self.sm.registration_generation:
                return
            if gen == self.sm.registration_generation and self.registered_symbols:
                return
            self.set_desired_universe(
                list(req.get("symbols") or []),
                generation=gen or None,
                position_symbols=list(req.get("position_symbols") or []),
            )
            # In synthetic mode, mark registered immediately
            if self.synthetic:
                self.registered_symbols = list(self.desired_symbols)
        except Exception:
            pass

    def queue_inject(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self._inject_queue.append(payload)

    def _live_thread(self) -> None:
        try:
            asyncio.run(self._live_async())
        except Exception:
            self.sm.transition(RECOVERY_FAILED, reason="live_thread_exc")
            self._write_status()

    async def _live_async(self) -> None:
        self._ws_loop = asyncio.get_running_loop()
        self.sm.transition(CONNECTING, reason="live")
        while not self._stop.is_set() and not self._scheduled_end_passed():
            try:
                await self._connect_register_consume()
            except Exception as exc:
                self.sm.last_error = type(exc).__name__
                await self._hard_recovery(reason=type(exc).__name__)
            await asyncio.sleep(0.2)
        await self._async_stop_receiver()
        self.stop()

    async def _connect_register_consume(self) -> None:
        from api.push_client import KabuNativePushClient
        from api.rest_client import KabuNativeRestClient, default_base_url
        from api.kabu_register import register_symbols_cleared

        self.sm.transition(CONNECTING, reason="connect")
        self.sm.bump_connection()
        rest = KabuNativeRestClient(default_base_url())
        token = rest.issue_token_from_env()
        push = KabuNativePushClient(rest, token)
        self._push_client = push
        self.sm.transition(REGISTERING, reason="register")
        specs = [(s, 1) for s in self.desired_symbols]
        if specs:
            register_symbols_cleared(push, specs, clear_first=False)
        self.registered_symbols = list(self.desired_symbols)
        self.sm.bump_registration()
        self.sm.transition(WAITING_FIRST_PUSH, reason="waiting_push")
        self._receiver_count = 1
        try:
            async for payload in push.iter_messages(recv_poll_sec=5.0):
                if self._stop.is_set() or self._scheduled_end_passed():
                    break
                if isinstance(payload, dict) and payload.get("__ws_lifecycle_tick__"):
                    self._poll_desired_universe()
                    self._maybe_silence_recovery(synthetic=False)
                    self._write_status()
                    continue
                if isinstance(payload, dict):
                    self._on_push(payload)
                self._poll_desired_universe()
                self._maybe_silence_recovery(synthetic=False)
        finally:
            self._receiver_count = 0
            self._push_client = None

    async def _async_stop_receiver(self) -> None:
        # Best-effort close; push client context managed by iter_messages
        self._receiver_count = 0

    def _maybe_silence_recovery(self, *, synthetic: bool) -> None:
        # Synthetic/demo may run outside cash-session wall clock; live still gated.
        if not synthetic and not is_market_session_jst():
            return
        if self._last_push_mono is None:
            return
        age = time.monotonic() - self._last_push_mono
        if age <= self.silence_stale_sec:
            return
        if self.sm.state in (STALE_DETECTED, CLOSING_STALE_SOCKET, RECONNECTING, REREGISTERING, RECOVERY_FAILED):
            return
        # Fire hard recovery (sync wrapper schedules async if needed)
        if synthetic:
            self._hard_recovery_sync(reason="silence")
        else:
            # Called from async loop
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._hard_recovery(reason="silence"))
            except RuntimeError:
                self._hard_recovery_sync(reason="silence")

    def _hard_recovery_sync(self, *, reason: str) -> None:
        """Synthetic/test recovery without real sockets."""
        self.sm.transition(STALE_DETECTED, reason=reason)
        self.sm.block_entry("ingress_stale")
        self._publish_control(KIND_ENTRY_BLOCK, "ingress_stale")
        self._notify("[INGRESS STALE]", f"silence reason={reason} session={self.session_id}")
        self.sm.transition(CLOSING_STALE_SOCKET, reason=reason)
        self.sm.transition(RECONNECTING, reason=reason)
        self.sm.recovery_count += 1
        ok = False
        backoffs = getattr(self, "_recovery_backoffs", RECOVERY_BACKOFFS)[:MAX_FAST_RECOVERY_ATTEMPTS]
        for attempt, delay in enumerate(backoffs, start=1):
            self.sm.recovery_attempt = attempt
            if delay:
                time.sleep(delay)
            # Simulate reconnect + reregister
            self.sm.bump_connection()
            self.sm.transition(REREGISTERING, reason=f"attempt{attempt}")
            self.registered_symbols = list(self.desired_symbols) or list(self.registered_symbols)
            self.sm.bump_registration()
            self.sm.transition(WAITING_FIRST_PUSH, reason=f"attempt{attempt}")
            # Success requires a subsequent inject; mark recovered on next push via _on_push.
            # For attempt simulation in tests, if inject arrives we succeed.
            if self._inject_queue or self._last_push_mono and (time.monotonic() - self._last_push_mono) < 1.0:
                ok = True
                break
            # For forced test path: attempt 2 success when test queues push after attempt 1 fail flag
            if getattr(self, "_test_fail_attempts", 0) < attempt:
                ok = True
                break
        if ok:
            # recovery_success_count increments only after Paper ACK catch-up.
            self._pending_recovery_success = True
            cur = int(self.writer.snapshot().get("last_sequence") or 0)
            self._first_recovered_sequence = cur + 1
            self.sm.transition(RECOVERED, reason="sync_recovery_pending_ack")
            self.sm.block_entry("recovery_warmup")
            self._publish_control(KIND_ENTRY_BLOCK, "recovery_warmup")
            self.maybe_promote_running(reason="recovery_pending_ack")
        else:
            self.sm.transition(RECOVERY_FAILED, reason="exhausted")
            self.sm.block_entry("RECOVERY_FAILED")
            self._publish_control(KIND_ENTRY_BLOCK, "RECOVERY_FAILED")
            self._notify("[INGRESS RECOVERY FAILED]", f"attempts={MAX_FAST_RECOVERY_ATTEMPTS}")

    async def _hard_recovery(self, *, reason: str) -> None:
        self.sm.transition(STALE_DETECTED, reason=reason)
        self.sm.block_entry("ingress_stale")
        self._publish_control(KIND_ENTRY_BLOCK, "ingress_stale")
        t0 = time.monotonic()
        self._notify("[INGRESS STALE]", f"silence recovery start reason={reason}")
        self.sm.transition(CLOSING_STALE_SOCKET, reason=reason)
        await self._async_stop_receiver()
        self._push_client = None
        self.sm.transition(RECONNECTING, reason=reason)
        self.sm.recovery_count += 1
        last_err = ""
        for attempt, delay in enumerate(RECOVERY_BACKOFFS[:MAX_FAST_RECOVERY_ATTEMPTS], start=1):
            self.sm.recovery_attempt = attempt
            if delay:
                await asyncio.sleep(delay)
            try:
                await self._connect_register_consume_once_until_first_push()
                self._pending_recovery_success = True
                cur = int(self.writer.snapshot().get("last_sequence") or 0)
                self._first_recovered_sequence = cur + 1
                self.sm.transition(RECOVERED, reason=f"attempt{attempt}_pending_ack")
                self.sm.block_entry("recovery_warmup")
                self._publish_control(KIND_ENTRY_BLOCK, "recovery_warmup")
                elapsed = time.monotonic() - t0
                self._notify(
                    "[INGRESS RECOVERY PENDING ACK]",
                    f"attempt={attempt} elapsed_sec={elapsed:.1f} registered={len(self.registered_symbols)}",
                )
                self.maybe_promote_running(reason="live_recovery_pending_ack")
                return
            except Exception as exc:
                last_err = type(exc).__name__
                continue
        self.sm.transition(RECOVERY_FAILED, reason=last_err or "exhausted")
        self.sm.block_entry("RECOVERY_FAILED")
        self._publish_control(KIND_ENTRY_BLOCK, "RECOVERY_FAILED")
        self._notify("[INGRESS RECOVERY FAILED]", f"err={last_err}")

    async def _connect_register_consume_once_until_first_push(self) -> None:
        from api.push_client import KabuNativePushClient
        from api.rest_client import KabuNativeRestClient, default_base_url
        from api.kabu_register import register_symbols_cleared

        self.sm.bump_connection()
        rest = KabuNativeRestClient(default_base_url())
        token = rest.issue_token_from_env()
        push = KabuNativePushClient(rest, token)
        self._push_client = push
        self.sm.transition(REREGISTERING, reason="recovery")
        specs = [(s, 1) for s in self.desired_symbols]
        if specs:
            register_symbols_cleared(push, specs, clear_first=False)
        self.registered_symbols = list(self.desired_symbols)
        self.sm.bump_registration()
        self.sm.transition(WAITING_FIRST_PUSH, reason="recovery")
        self._receiver_count = 1
        got = False
        async for payload in push.iter_messages(recv_poll_sec=5.0):
            if isinstance(payload, dict) and not payload.get("__ws_lifecycle_tick__"):
                r = self._on_push(payload)
                if r.get("ok"):
                    got = True
                    break
            if self._stop.is_set():
                break
        self._receiver_count = 0
        if not got:
            raise TimeoutError("no_first_push")

    def _scheduled_end_passed(self) -> bool:
        y, m, d = int(self.trading_date[:4]), int(self.trading_date[4:6]), int(self.trading_date[6:8])
        end = datetime(y, m, d, FINALIZE_TIME.hour, FINALIZE_TIME.minute, tzinfo=JST)
        return datetime.now(JST) >= end

    def _finalize_seal(self) -> None:
        from small_paper.capture_completeness_gate import evaluate_capture_completeness

        snap = self.writer.snapshot()
        # Scan first/last from parts
        first = None
        last = None
        rows = 0
        for p in sorted(self.session_path.glob("push_part_*.jsonl")):
            if p.stat().st_size == 0:
                continue
            with p.open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        o = json.loads(line)
                    except Exception:
                        continue
                    rows += 1
                    ts = o.get("received_at") or o.get("event_time")
                    if first is None:
                        first = ts
                    last = ts
        completeness = evaluate_capture_completeness(
            trading_date=self.trading_date,
            first_event_at=first,
            last_event_at=last,
            dropped_event_count=int(snap.get("dropped") or 0),
            disconnect_count=self.sm.recovery_count,
            reconnect_count=self.sm.recovery_success_count,
            registration_symbol_count=len(self.registered_symbols),
            heartbeat_at=now_iso(),
            raw_row_count=rows,
            seal_row_count=rows,
            stale_or_silence=self.sm.state in (RECOVERY_FAILED, STALE_DETECTED),
        )
        (self.session_path / "capture_completeness.json").write_text(
            json.dumps(completeness, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        seal = {
            "schema_version": "ingress_v2.1",
            "ingress_session_id": self.session_id,
            "trading_date": self.trading_date,
            "sealed_at": now_iso(),
            "raw_rows": rows,
            "first_event_at": first,
            "last_event_at": last,
            "completeness": completeness,
            "seal_pass": bool(completeness.get("seal_pass")),
            "writer": snap,
            "state": self.sm.snapshot(),
        }
        (self.session_path / "seal.json").write_text(
            json.dumps(seal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Independent Market Ingress V2")
    p.add_argument("--native-root", type=str, default=str(NATIVE_ROOT))
    p.add_argument("--trading-date", type=str, default="")
    p.add_argument("--synthetic", action="store_true")
    p.add_argument("--bus-port", type=int, default=0)
    p.add_argument("--symbols", type=str, default="")
    p.add_argument("--silence-stale-sec", type=float, default=0.0)
    args = p.parse_args(list(argv) if argv is not None else None)
    kw: dict[str, Any] = {
        "native_root": Path(args.native_root),
        "trading_date": args.trading_date or None,
        "enable_tcp_bus": True,
        "bus_port_override": int(args.bus_port) if args.bus_port else None,
        "synthetic": bool(args.synthetic),
    }
    if float(args.silence_stale_sec or 0) > 0:
        kw["silence_stale_sec"] = float(args.silence_stale_sec)
    svc = MarketIngressService(**kw)
    if args.symbols:
        svc.set_desired_universe([s.strip() for s in args.symbols.split(",") if s.strip()])
    svc.start()

    def _sig(*_a: Any) -> None:
        svc.stop()

    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(s, _sig)
        except Exception:
            pass
    try:
        while svc.sm.state != STOPPED and not svc._scheduled_end_passed():
            time.sleep(1.0)
            if svc._stop.is_set():
                break
    finally:
        if svc.sm.state != STOPPED:
            svc.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
