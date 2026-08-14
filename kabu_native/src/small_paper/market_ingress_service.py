"""Independent Market Ingress — sole Kabu WebSocket owner (V2).

Raw-first: PUSH → Envelope → Raw JSONL → Local Bus → Paper/Observer.
Paper never owns WebSocket under MARKET_INGRESS_V2.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
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

from small_paper.ingress_run_identity import (
    ENV_BUS_IDENTITY,
    ENV_INGRESS_RUN_ID,
    ENV_LAUNCH_NONCE,
    ROLE_MARKET_INGRESS_SERVICE,
    STATUS_SCHEMA_VERSION,
    activation_identity,
    capture_process_start_identity,
    generate_launch_nonce,
    make_bus_identity,
    make_ingress_run_id,
)
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
from small_paper.runtime_clock import (
    ingress_replay_path,
    now_jst as session_now,
    replay_max_eps,
    replay_not_before_hhmm,
    scheduled_end_passed,
    session_clock_armed,
    session_clock_enabled,
    sleep_until as session_sleep_until,
)

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
    n = now or session_now()
    t = n.timetz().replace(tzinfo=None)
    if LUNCH_START <= t < LUNCH_END:
        return False
    if MARKET_AM_START <= t < LUNCH_START:
        return True
    if LUNCH_END <= t < MARKET_PM_END:
        return True
    return False


def _parse_replay_dt(raw: str) -> Optional[datetime]:
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return dt.astimezone(JST)
    except Exception:
        return None


def replay_payload_from_record(obj: Any) -> Optional[dict[str, Any]]:
    """Normalize capture envelope / cached stream row to a Kabu PUSH payload."""
    if not isinstance(obj, dict):
        return None
    if "original_payload" in obj or (obj.get("kind") == KIND_MARKET_PUSH and "payload" in obj):
        body = obj.get("original_payload") or obj.get("payload") or {}
        if not isinstance(body, dict):
            return None
        out = dict(body)
        rec_at = obj.get("received_at") or body.get("received_at")
        if rec_at:
            out["__replay_received_at__"] = rec_at
        if not out.get("Symbol"):
            out["Symbol"] = str(obj.get("symbol") or body.get("symbol") or "")
        return out if out.get("Symbol") else None
    if obj.get("symbol") or obj.get("Symbol") or obj.get("raw"):
        skip = {"t", "raw"}
        out = {k: v for k, v in obj.items() if k not in skip}
        out["Symbol"] = str(obj.get("Symbol") or obj.get("symbol") or obj.get("raw") or "")
        if obj.get("received_at"):
            out["__replay_received_at__"] = obj["received_at"]
        return out if out.get("Symbol") else None
    return None


def iter_ingress_replay_records(source: str):
    """Yield JSON objects from a jsonl file or a capture session directory."""
    path = Path(source)
    files: list[Path] = []
    if path.is_file():
        files = [path]
    elif path.is_dir():
        files = sorted(path.glob("push_part_*.jsonl"))
        if not files:
            files = sorted(path.glob("*.jsonl"))
    for fp in files:
        with fp.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except Exception:
                    continue


def make_ingress_session_id() -> str:
    return f"ing_{trading_date_jst()}_{os.getpid()}_{int(time.time())}_{uuid.uuid4().hex[:8]}"


def universe_symbol_hash(symbols: Sequence[str]) -> str:
    norm = ",".join(sorted({str(s).split(".")[0].strip().upper() for s in symbols if str(s).strip()}))
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


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
        self.trading_date = trading_date or trading_date_jst(session_now())
        self.silence_stale_sec = float(silence_stale_sec)
        self.synthetic = bool(synthetic)
        self._replay_source = ""
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
        # Live Kabu register bookkeeping (Ingress is sole PUT owner).
        self._register_put_ok: bool = False
        self._last_register_generation: int = 0
        self._last_register_symbols: tuple[str, ...] = ()
        self._register_evidence: dict[str, Any] = {}
        self._register_in_flight: bool = False
        self._force_reconnect: bool = False
        self._last_status_mono: Optional[float] = None
        self._desired_source_trading_date: str = ""
        self._desired_source_path: str = ""
        self._desired_source_sha256: str = ""
        self._desired_reject_reason: str = ""
        self._last_readonly_verify_mono: float = 0.0
        self._last_actual_symbols: tuple[str, ...] = ()
        self._register_retry_count: int = 0
        self._auth_failure_count: int = 0
        self._rate_limit_count: int = 0
        self._backoff_count: int = 0
        self._circuit_open_count: int = 0
        self._circuit_open_until_mono: float = 0.0
        self._circuit_reason: str = ""
        self._auth_refresh_mono: float = 0.0
        self._token_refresh_fn: Optional[Callable[[], str]] = None
        self._had_verified_exact50: bool = False
        self._auth_recovery_in_flight: bool = False

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
        self.launch_nonce = str(os.environ.get(ENV_LAUNCH_NONCE) or "").strip() or generate_launch_nonce()
        self.ingress_run_id = str(os.environ.get(ENV_INGRESS_RUN_ID) or "").strip() or make_ingress_run_id(
            trading_date=str(self.trading_date),
            launch_nonce=self.launch_nonce,
        )
        self.activation_id, self.activation_sha = activation_identity()
        self.bus_identity = str(os.environ.get(ENV_BUS_IDENTITY) or "").strip() or make_bus_identity(
            host=str(self.bus.host),
            port=int(self.bus.port),
            trading_date=str(self.trading_date),
            launch_nonce=self.launch_nonce,
        )
        self.process_start_identity = capture_process_start_identity(os.getpid())

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
        # Bind same-day desired (or AM SoT) before the worker thread so synthetic
        # fallback 7203/6758 cannot race a stale prior-day file.
        self._apply_desired_from_control_or_am(register=False)
        replay = ingress_replay_path()
        from small_paper.kabu_token_authority import live_kabu_auth_allowed
        from small_paper.runtime_clock import MARKET_INPUT_SYNTHETIC, market_input_mode

        auth_ok, _auth_reason = live_kabu_auth_allowed(synthetic=bool(self.synthetic))
        input_mode = market_input_mode()
        if self.synthetic or input_mode == MARKET_INPUT_SYNTHETIC or not auth_ok:
            self._thread = threading.Thread(target=self._synthetic_loop, name="ingress-synth", daemon=True)
        elif replay:
            self._replay_source = replay
            self._thread = threading.Thread(target=self._replay_loop, name="ingress-replay", daemon=True)
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
        try:
            from small_paper.kabu_registration_authority import write_registration_owner

            write_registration_owner(
                self.native_root,
                trading_date=self.trading_date,
                pid=os.getpid(),
                ingress_session_id=self.session_id,
                committed=bool(self._register_put_ok),
                synthetic=bool(self.synthetic),
            )
        except Exception:
            pass

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
                "status_schema_version": STATUS_SCHEMA_VERSION,
                "activation_id": self.activation_id,
                "activation_sha": self.activation_sha,
                "ingress_run_id": self.ingress_run_id,
                "launch_nonce": self.launch_nonce,
                "process_start_identity": self.process_start_identity,
                "trading_date": str(self.trading_date),
                "role": ROLE_MARKET_INGRESS_SERVICE,
                "bus_identity": self.bus_identity,
                "status_written_unix": time.time(),
                "status_written_monotonic": time.monotonic(),
                "heartbeat_monotonic_age": 0.0,
                "receiver_task_count": self._receiver_count if not self.synthetic else 1,
                "entry_blocked": self.sm.entry_blocked,
                "entry_block_reason": self.sm.entry_block_reason or None,
                "latency": self.latency_stats(),
                "bus": pub,
                "paper_consumer_transport": paper.get("transport"),
                "paper_consumer_ready": bool(paper.get("ready")),
                "paper_consumer_connected": bool(paper.get("connected")),
                "first_recovered_sequence": self._first_recovered_sequence,
                "register_put_ok": bool(self._register_put_ok),
                "register_verified": bool(self._register_evidence.get("verified")),
                "register_actual_count": int(self._register_evidence.get("actual_count") or 0),
                "register_put_executed": bool(self._register_evidence.get("put_executed")),
                "registration_retry_count": int(self._register_retry_count),
                "auth_failure_count": int(self._auth_failure_count),
                "rate_limit_count": int(self._rate_limit_count),
                "backoff_count": int(self._backoff_count),
                "circuit_open_count": int(self._circuit_open_count),
                "circuit_reason": self._circuit_reason or None,
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
        expected = len(self.desired_symbols)
        registered = len(self.registered_symbols)
        tcp_ok = str(cons.get("transport") or "") == "TCP" and bool(cons.get("ready"))
        first_ok = self._last_push_mono is not None and raw_seq > 0
        # Live PUSH can advance pub_seq between Paper process and ACK; treat lag<=1 as caught up.
        if self._pending_recovery_success:
            need = int(self._first_recovered_sequence or pub_seq)
            ack_ok = ack >= need and lag <= 1 and pub_seq > 0
        else:
            ack_ok = ack > 0 and lag <= 1 and pub_seq > 0 and ack >= (pub_seq - 1)
        registered_ok = (
            expected > 0
            and registered == expected
            and (self.synthetic or self._register_put_ok)
        )
        return {
            "websocket_or_synthetic": True if self.synthetic else self._receiver_count == 1,
            "registered_ok": registered_ok,
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
        received = str(payload.pop("__replay_received_at__", "") or "").strip() or now_iso()
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

        # Continuous PUSH suppresses lifecycle ticks (timeout-based). Flush status/desired
        # on a short cadence so ACK/lag/ENTRY gate remain observable and refreshable.
        now_m = time.monotonic()
        if self._last_status_mono is None or (now_m - self._last_status_mono) >= 2.0:
            self._last_status_mono = now_m
            try:
                self._poll_desired_universe()
            except Exception:
                pass
            self._write_status()

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

    def _replay_try_register(self) -> dict[str, Any]:
        """Production token+register without live WebSocket (replay input).

        Replay does not imply live POST /token. KABU_AUTH_MODE=LIVE is required.
        Synthetic / preflight never issue.
        """
        out: dict[str, Any] = {"ok": False}
        from small_paper.kabu_token_authority import live_kabu_auth_allowed

        auth_ok, auth_reason = live_kabu_auth_allowed(synthetic=bool(self.synthetic))
        if not auth_ok:
            out["ok"] = True
            out["skipped"] = auth_reason
            out["token_issued"] = False
            return out
        try:
            from api.rest_client import KabuNativeRestClient, default_base_url, load_kabu_env
            from small_paper.kabu_token_authority import owner_issue_context, publish_owned_token

            load_kabu_env(repo_root=self.native_root.parent)
            load_kabu_env(repo_root=self.native_root)
            self._poll_desired_universe_apply_only()
            rest = KabuNativeRestClient(default_base_url())
            with owner_issue_context(
                native_root=self.native_root,
                trading_date=self.trading_date,
                pid=os.getpid(),
                session_id=self.session_id,
                caller="ingress_replay_connect",
            ):
                token = rest.issue_token_from_env()
            publish_owned_token(
                token,
                native_root=self.native_root,
                trading_date=self.trading_date,
                caller="ingress_replay_connect",
            )
            from api.push_client import KabuNativePushClient

            push = KabuNativePushClient(rest, token)
            self._push_client = push
            if self.desired_symbols:
                self._execute_live_register(push, reason="replay_connect")
            out["ok"] = True
            out["registered"] = list(self.registered_symbols)
        except Exception as exc:
            out["error"] = f"{type(exc).__name__}:{exc}"
            self.sm.last_error = type(exc).__name__
        return out

    def _replay_loop(self) -> None:
        """Recorded capture / production-schema replay at Ingress input (_on_push)."""
        self.sm.transition(CONNECTING, reason="replay")
        self.sm.bump_connection()
        self.sm.transition(REGISTERING, reason="replay")
        reg = self._replay_try_register()
        self._register_evidence["replay_register"] = reg
        if not self.desired_symbols:
            self._poll_desired_universe_apply_only()
        if not self.registered_symbols and self.desired_symbols:
            # Keep desired; registration may be off-hours. Still replay through Raw+Bus.
            self.registered_symbols = list(self.desired_symbols)
        self.sm.transition(WAITING_FIRST_PUSH, reason="replay")
        if session_clock_enabled():
            while not session_clock_armed():
                if self._stop.is_set() or self._operator_stop_requested():
                    return
                time.sleep(0.05)
        max_eps = replay_max_eps()
        min_interval = 1.0 / max_eps
        last_mono = 0.0
        try:
            for rec in iter_ingress_replay_records(self._replay_source):
                if self._stop.is_set() or self._scheduled_end_passed() or self._operator_stop_requested():
                    break
                payload = replay_payload_from_record(rec)
                if not payload:
                    continue
                rec_at = str(payload.get("__replay_received_at__") or "")
                event_dt = _parse_replay_dt(rec_at)
                if event_dt is not None:
                    sess = session_now()
                    event_dt = event_dt.replace(year=sess.year, month=sess.month, day=sess.day)
                    payload["__replay_received_at__"] = event_dt.isoformat(timespec="milliseconds")
                    nb = replay_not_before_hhmm()
                    if nb and len(nb) >= 4:
                        try:
                            hh, mm = int(nb[:2]), int(nb[3:5] if ":" in nb else nb[2:4])
                            if event_dt.hour < hh or (event_dt.hour == hh and event_dt.minute < mm):
                                continue
                        except Exception:
                            pass
                    if event_dt.hour < 8 or (event_dt.hour == 8 and event_dt.minute < 50):
                        continue
                    if event_dt > sess:
                        session_sleep_until(event_dt, poll_sec=0.02)
                wait = min_interval - (time.monotonic() - last_mono)
                if wait > 0:
                    time.sleep(wait)
                last_mono = time.monotonic()
                self._on_push(payload)
                self._poll_desired_universe()
                if int(self.writer.snapshot().get("last_sequence") or 0) % 200 == 0:
                    self._write_status()
        except Exception as exc:
            self.sm.last_error = type(exc).__name__
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
            self._apply_desired_from_control_or_am(register=True)
        except Exception:
            pass

    def _poll_desired_universe_apply_only(self) -> None:
        """Apply control-channel / AM SoT desired symbols without registering (pre-connect)."""
        try:
            self._apply_desired_from_control_or_am(register=False)
        except Exception:
            pass

    def _apply_desired_from_control_or_am(self, *, register: bool) -> dict[str, Any]:
        """Accept frozen AM50 (or same-day AM CSV before freeze). Never PUT a stale date or post-bind CSV."""
        from small_paper.day_fixed_am_registration import (
            FROZEN_AM_UNIVERSE_MISMATCH,
            SAME_DAY_AM_FROZEN_AUTHORITY,
            STALE_DESIRED_UNIVERSE,
            bind_same_day_am_desired_universe,
            canonical_membership_sha,
            load_am_canonical_50,
            load_frozen_am_universe,
        )
        from small_paper.ingress_control_channel import read_desired_universe

        req = read_desired_universe(self.native_root, requested_trading_date=self.trading_date)
        accepted: Optional[dict[str, Any]] = None
        frozen = load_frozen_am_universe(self.native_root, self.trading_date)
        if frozen.get("present"):
            if not frozen.get("ok"):
                self._desired_reject_reason = str(frozen.get("reason") or FROZEN_AM_UNIVERSE_MISMATCH)
                return {
                    "ok": False,
                    "reason": self._desired_reject_reason,
                    "allow_put": False,
                    "allow_put_new50": False,
                }
            frozen_syms = list(frozen.get("canonical_symbols") or [])
            frozen_sha = str(frozen.get("canonical_membership_sha") or "")
            if req and not req.get("rejected") and list(req.get("symbols") or []):
                req_sha = canonical_membership_sha(list(req.get("symbols") or []))
                if req_sha != frozen_sha:
                    self._desired_reject_reason = FROZEN_AM_UNIVERSE_MISMATCH
                    return {
                        "ok": False,
                        "reason": FROZEN_AM_UNIVERSE_MISMATCH,
                        "allow_put": False,
                        "allow_put_new50": False,
                        "authority": SAME_DAY_AM_FROZEN_AUTHORITY,
                    }
                accepted = req
                self._desired_reject_reason = ""
            else:
                bound = bind_same_day_am_desired_universe(
                    self.native_root,
                    self.trading_date,
                    symbols=frozen_syms,
                    source_path=str(frozen.get("frozen_csv_path") or frozen.get("source_csv_path") or ""),
                    source_sha256=str(frozen.get("source_csv_sha") or ""),
                )
                if not bound.get("ok"):
                    self._desired_reject_reason = str(bound.get("reason") or FROZEN_AM_UNIVERSE_MISMATCH)
                    return {
                        "ok": False,
                        "reason": self._desired_reject_reason,
                        "allow_put": False,
                        "allow_put_new50": False,
                    }
                accepted = {
                    "symbols": frozen_syms,
                    "generation": int((bound.get("desired") or {}).get("generation") or 0),
                    "position_symbols": [],
                    "source_path": str(bound.get("source_path") or ""),
                    "source_sha256": str(bound.get("source_sha256") or ""),
                    "source_trading_date": self.trading_date,
                    "trading_date": self.trading_date,
                }
                self._desired_reject_reason = ""
        elif req and not req.get("rejected") and list(req.get("symbols") or []):
            accepted = req
            self._desired_reject_reason = ""
        else:
            if req and req.get("rejected"):
                self._desired_reject_reason = str(req.get("reason") or STALE_DESIRED_UNIVERSE)
            else:
                self._desired_reject_reason = str((req or {}).get("reason") or "desired_universe_missing")
            # CASE C: stale/missing file must not win; use same-day AM SoT when present.
            am = load_am_canonical_50(self.native_root, self.trading_date)
            if am.get("ok"):
                bound = bind_same_day_am_desired_universe(
                    self.native_root,
                    self.trading_date,
                    symbols=list(am.get("symbols") or []),
                    source_path=str(am.get("universe_path") or ""),
                    source_sha256=str(am.get("universe_sha256") or ""),
                )
                if bound.get("ok"):
                    accepted = {
                        "symbols": list(bound.get("symbols") or []),
                        "generation": int((bound.get("desired") or {}).get("generation") or 0),
                        "position_symbols": [],
                        "source_path": str(bound.get("source_path") or ""),
                        "source_sha256": str(bound.get("source_sha256") or ""),
                        "source_trading_date": self.trading_date,
                        "trading_date": self.trading_date,
                    }
                    self._desired_reject_reason = ""
            if accepted is None:
                return {
                    "ok": False,
                    "reason": self._desired_reject_reason or STALE_DESIRED_UNIVERSE,
                    "allow_put": False,
                }

        gen = int(accepted.get("generation") or 0)
        if gen and gen < self.sm.registration_generation:
            return {"ok": False, "reason": "stale_generation", "allow_put": False}
        applied = self.set_desired_universe(
            list(accepted.get("symbols") or []),
            generation=gen or None,
            position_symbols=list(accepted.get("position_symbols") or []),
        )
        if not applied.get("ok"):
            return applied
        self._desired_source_trading_date = str(
            accepted.get("source_trading_date") or accepted.get("trading_date") or self.trading_date
        )
        self._desired_source_path = str(accepted.get("source_path") or "")
        self._desired_source_sha256 = str(accepted.get("source_sha256") or "")
        if self._desired_source_trading_date != str(self.trading_date):
            self.desired_symbols = []
            self._desired_reject_reason = STALE_DESIRED_UNIVERSE
            return {"ok": False, "reason": STALE_DESIRED_UNIVERSE, "allow_put": False}
        if self.synthetic:
            self.registered_symbols = list(self.desired_symbols)
            self._register_put_ok = True
            self._last_register_generation = int(self.sm.registration_generation)
            self._last_register_symbols = tuple(self.desired_symbols)
            self._publish_ingress_registration_result(
                verified=True,
                actual_symbols=list(self.registered_symbols),
                actual_count=len(self.registered_symbols),
                generation=int(self.sm.registration_generation),
                universe_hash=universe_symbol_hash(self.desired_symbols),
                put_executed=False,
            )
            return {"ok": True, "synthetic": True}
        if register:
            return self._maybe_register_desired_live(reason="desired_poll")
        return {"ok": True, "applied": True}

    def _registration_circuit_open(self) -> bool:
        until = float(self._circuit_open_until_mono or 0.0)
        return bool(until and time.monotonic() < until)

    def _open_register_circuit(self, *, reason: str) -> None:
        from small_paper.kabu_token_authority import next_backoff_sec

        delay = next_backoff_sec(self._backoff_count)
        self._backoff_count += 1
        self._circuit_open_count += 1
        self._circuit_reason = str(reason)
        self._circuit_open_until_mono = time.monotonic() + delay
        self._write_recovery_audit(event="circuit_open", reason=reason, backoff_sec=delay)

    def _write_recovery_audit(self, **extra: Any) -> None:
        try:
            payload = {
                "at": now_iso(),
                "registration_retry_count": int(self._register_retry_count),
                "auth_failure_count": int(self._auth_failure_count),
                "rate_limit_count": int(self._rate_limit_count),
                "backoff_count": int(self._backoff_count),
                "circuit_open_count": int(self._circuit_open_count),
                "circuit_reason": self._circuit_reason,
                "had_verified_exact50": bool(self._had_verified_exact50),
                **extra,
            }
            path = self.day_root / "registration_recovery_audit.jsonl"
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        except Exception:
            pass

    def _owner_refresh_token(self) -> bool:
        from small_paper.kabu_token_authority import owner_issue_context, publish_owned_token

        now_m = time.monotonic()
        if self._auth_refresh_mono and (now_m - self._auth_refresh_mono) < 5.0:
            return False
        token = ""
        try:
            if self._token_refresh_fn is not None:
                token = str(self._token_refresh_fn() or "")
            else:
                from api.rest_client import KabuNativeRestClient, default_base_url, load_kabu_env

                load_kabu_env(repo_root=self.native_root.parent)
                load_kabu_env(repo_root=self.native_root)
                rest = KabuNativeRestClient(default_base_url())
                with owner_issue_context(
                    native_root=self.native_root,
                    trading_date=self.trading_date,
                    pid=os.getpid(),
                    session_id=self.session_id,
                    caller="ingress_auth_recovery",
                ):
                    token = rest.issue_token_from_env()
                publish_owned_token(
                    token,
                    native_root=self.native_root,
                    trading_date=self.trading_date,
                    caller="ingress_auth_recovery",
                )
        except Exception:
            return False
        if not token:
            return False
        self._auth_refresh_mono = now_m
        push = self._push_client
        if push is not None:
            try:
                push._token = token
            except Exception:
                pass
        return True

    def _handle_auth_invalidated(self, *, source: str) -> None:
        if self._auth_recovery_in_flight:
            self._open_register_circuit(reason="AUTHORITY_TOKEN_INVALIDATED")
            return
        self._auth_recovery_in_flight = True
        try:
            self._auth_failure_count += 1
            self._write_recovery_audit(event="AUTHORITY_TOKEN_INVALIDATED", source=source)
            refreshed = self._owner_refresh_token()
            if not refreshed:
                self._open_register_circuit(reason="AUTHORITY_TOKEN_INVALIDATED")
                return
            skipped = self._skip_put_if_actual_kabu_matches()
            if skipped and skipped.get("ok"):
                return
            self._open_register_circuit(reason="AUTH_RECOVERY_VERIFY")
        finally:
            self._auth_recovery_in_flight = False

    def _handle_rate_limit(self, *, source: str) -> None:
        self._rate_limit_count += 1
        self._open_register_circuit(reason="RATE_LIMIT")
        self._write_recovery_audit(event="RATE_LIMIT_CIRCUIT", source=source)

    def _should_skip_live_register(self) -> bool:
        if self.synthetic or not self.desired_symbols:
            return True
        if self._register_in_flight:
            return True
        return False

    def _internal_same_generation_registered(self) -> bool:
        return bool(
            self._register_put_ok
            and self._last_register_generation == int(self.sm.registration_generation)
            and self._last_register_symbols == tuple(self.desired_symbols)
            and self.registered_symbols
        )

    def _skip_put_if_actual_kabu_matches(self) -> Optional[dict[str, Any]]:
        """Skip PUT only when readonly actual RegistList == desired. Never internal-state-only."""
        from small_paper.day_fixed_am_registration import canonical_symbols
        from small_paper.kabu_registration_authority import (
            REGISTRATION_DRIFT_DETECTED,
            append_authority_audit,
            fetch_kabu_regist_list,
            write_actual_regist_snapshot,
        )

        if self.synthetic or not self.desired_symbols or self._register_in_flight:
            return None
        if not self._internal_same_generation_registered():
            return None
        desired = canonical_symbols(self.desired_symbols)
        fetched = fetch_kabu_regist_list(self._push_client)
        if fetched.get("ok"):
            actual = canonical_symbols(list(fetched.get("symbols") or []))
            write_actual_regist_snapshot(
                self.native_root,
                trading_date=self.trading_date,
                symbols=actual,
                source=str(fetched.get("reason") or "kabu_readonly_get"),
                generation=int(self.sm.registration_generation),
            )
            self._last_actual_symbols = tuple(actual)
            if set(actual) == set(desired) and len(actual) == len(desired):
                return {
                    "ok": True,
                    "skipped": True,
                    "reason": "same_desired_generation_already_registered",
                    "put_executed": False,
                    "actual_count": len(actual),
                    "actual_source": fetched.get("reason"),
                }
            append_authority_audit(
                self.native_root,
                self.trading_date,
                REGISTRATION_DRIFT_DETECTED,
                {
                    "generation": int(self.sm.registration_generation),
                    "desired_n": len(desired),
                    "actual_n": len(actual),
                    "actual_empty": len(actual) == 0,
                    "am_only": sorted(set(desired) - set(actual)),
                    "kabu_only": sorted(set(actual) - set(desired)),
                },
            )
            return None
        if str(fetched.get("reason") or "") == "GET_NOT_SUPPORTED":
            # Cannot skip on internal state. Rate-limit verify PUT to avoid hammering Station.
            now_m = time.monotonic()
            if self._last_readonly_verify_mono and (now_m - self._last_readonly_verify_mono) < 30.0:
                return {
                    "ok": True,
                    "skipped": True,
                    "reason": "readonly_get_unsupported_verify_interval",
                    "put_executed": False,
                }
            return None
        from small_paper.kabu_token_authority import AUTH_INVALID, RATE_LIMIT, classify_kabu_api_error

        cls = classify_kabu_api_error(
            fetched.get("reason"),
            fetched.get("http_status"),
        )
        if cls == AUTH_INVALID:
            self._handle_auth_invalidated(source="readonly_get")
            return {
                "ok": True,
                "skipped": True,
                "reason": "AUTHORITY_TOKEN_INVALIDATED",
                "put_executed": False,
            }
        if cls == RATE_LIMIT:
            self._handle_rate_limit(source="readonly_get")
            return {
                "ok": True,
                "skipped": True,
                "reason": "RATE_LIMIT_CIRCUIT",
                "put_executed": False,
            }
        append_authority_audit(
            self.native_root,
            self.trading_date,
            REGISTRATION_DRIFT_DETECTED,
            {
                "generation": int(self.sm.registration_generation),
                "fetch_ok": False,
                "fetch_reason": fetched.get("reason"),
            },
        )
        return None

    def _maybe_register_desired_live(self, *, reason: str) -> dict[str, Any]:
        """Run canonical Kabu register when live desired/generation needs Station PUT."""
        if self._should_skip_live_register():
            return {
                "ok": True,
                "skipped": True,
                "reason": "synthetic_or_empty_or_in_flight",
                "put_executed": False,
            }
        if self._registration_circuit_open():
            return {
                "ok": True,
                "skipped": True,
                "reason": f"circuit_open:{self._circuit_reason}",
                "put_executed": False,
            }
        skipped = self._skip_put_if_actual_kabu_matches()
        if skipped:
            return skipped
        push = self._push_client
        if push is None:
            return {"ok": False, "skipped": True, "reason": "no_push_client", "put_executed": False}
        return self._execute_live_register(push, reason=reason)

    def _execute_live_register(self, push: Any, *, reason: str) -> dict[str, Any]:
        """Sole live registration path — reuses api.kabu_register.register_symbols_cleared."""
        from api.kabu_register import extract_regist_num, extract_symbol_set, register_symbols_cleared
        from api.rest_client import load_kabu_env
        from small_paper.kabu_registration_authority import (
            REGISTRATION_DRIFT_REPUT,
            append_authority_audit,
            write_actual_regist_snapshot,
            write_registration_owner,
        )

        if self._should_skip_live_register() and reason != "connect":
            return {
                "ok": True,
                "skipped": True,
                "reason": "synthetic_or_empty_or_in_flight",
                "put_executed": False,
            }
        if reason != "connect" or self._internal_same_generation_registered():
            skipped = self._skip_put_if_actual_kabu_matches()
            if skipped:
                return skipped
        specs = [(s, 1) for s in self.desired_symbols]
        if not specs:
            self.registered_symbols = []
            self._register_put_ok = False
            self.sm.block_entry("WAITING_DESIRED_REGISTER")
            self._publish_control(KIND_ENTRY_BLOCK, "WAITING_DESIRED_REGISTER")
            return {"ok": True, "skipped": True, "reason": "empty_desired", "put_executed": False}
        src_day = str(self._desired_source_trading_date or self.trading_date)
        if src_day != str(self.trading_date):
            self.registered_symbols = []
            self._register_put_ok = False
            self._desired_reject_reason = "STALE_DESIRED_UNIVERSE"
            self.sm.block_entry("STALE_DESIRED_UNIVERSE")
            self._publish_control(KIND_ENTRY_BLOCK, "STALE_DESIRED_UNIVERSE")
            return {
                "ok": False,
                "skipped": True,
                "reason": "STALE_DESIRED_UNIVERSE",
                "put_executed": False,
                "allow_put": False,
            }

        # Refresh credentials from disk .env on every register attempt.
        # Capture may have been started with a stale process-env password;
        # load_kabu_env(override=False) would keep the bad value forever.
        load_kabu_env(repo_root=self.native_root.parent)
        load_kabu_env(repo_root=self.native_root)
        try:
            from dotenv import load_dotenv

            for root in (self.native_root.parent, self.native_root):
                env_path = Path(root) / ".env"
                if env_path.is_file():
                    load_dotenv(dotenv_path=env_path, override=True)
        except Exception:
            pass
        drift_reput = bool(self._internal_same_generation_registered())
        allow_reuse = bool(
            (not drift_reput)
            and self._register_put_ok
            and self._last_register_symbols == tuple(self.desired_symbols)
        )
        self._register_in_flight = True
        gen = int(self.sm.registration_generation)
        uhash = universe_symbol_hash(self.desired_symbols)
        try:
            if self.sm.state in (WAITING_FIRST_PUSH, RUNNING, RECOVERED):
                self.sm.transition(REREGISTERING, reason=reason)
            elif self.sm.state != REGISTERING:
                self.sm.transition(REGISTERING, reason=reason)
            self.sm.block_entry("REGISTERING")
            self._publish_control(KIND_ENTRY_BLOCK, "REGISTERING")
            meta = register_symbols_cleared(
                push,
                specs,
                clear_first=False,
                native_root=self.native_root,
                trading_date=self.trading_date,
                allow_reuse_if_match=allow_reuse,
            )
            put_executed = not bool(meta.get("reused_existing"))
            resp = meta.get("response") if isinstance(meta, dict) else None
            regist_num = extract_regist_num(resp) if resp is not None else None
            if regist_num is None and meta.get("ok"):
                regist_num = int(meta.get("symbol_count") or len(specs))
            symbol_set = sorted(extract_symbol_set(resp) or []) if resp is not None else []
            if not symbol_set and meta.get("ok"):
                symbol_set = sorted({s for s, _ in specs})
            ok = bool(meta.get("ok")) and int(regist_num or 0) == len(specs)
            # verified requires a real PUT response this session (or prior PUT for same symbols + reuse)
            verified = bool(ok and (put_executed or self._register_put_ok))
            if put_executed and ok:
                self._register_put_ok = True
            evidence = {
                "ok": ok,
                "verified": verified,
                "put_executed": put_executed,
                "reused_existing": bool(meta.get("reused_existing")),
                "http_status": 200 if ok and put_executed else (None if meta.get("reused_existing") else None),
                "http_status_note": (
                    "kabu client raises on non-2xx; 200 recorded only when PUT succeeded"
                    if put_executed
                    else "PUT skipped (reuse_existing_registration); verified only if prior PUT ok"
                ),
                "response_body": resp,
                "actual_count": int(regist_num or 0) if ok else 0,
                "actual_symbols": symbol_set if ok else [],
                "desired_count": len(specs),
                "generation": gen,
                "universe_hash": uhash,
                "executed_at": now_iso(),
                "reason": reason,
                "steps": meta.get("steps") if isinstance(meta, dict) else [],
                "owner": "MARKET_INGRESS_SERVICE",
                "verification_basis": (
                    "kabu_put_response"
                    if put_executed and ok
                    else ("prior_put_plus_reuse" if verified else "unverified")
                ),
            }
            self._register_evidence = evidence
            self._write_register_evidence(evidence)
            if ok and verified:
                self.registered_symbols = list(self.desired_symbols)
                self._last_register_generation = gen
                self._last_register_symbols = tuple(self.desired_symbols)
                self._last_readonly_verify_mono = time.monotonic()
                actual_for_snap = list(symbol_set) if symbol_set else list(self.registered_symbols)
                write_actual_regist_snapshot(
                    self.native_root,
                    trading_date=self.trading_date,
                    symbols=actual_for_snap,
                    source="kabu_put_response",
                    generation=gen,
                    extra={"put_executed": put_executed, "reason": reason},
                )
                write_registration_owner(
                    self.native_root,
                    trading_date=self.trading_date,
                    pid=os.getpid(),
                    ingress_session_id=self.session_id,
                    committed=True,
                    synthetic=bool(self.synthetic),
                )
                if drift_reput and put_executed:
                    append_authority_audit(
                        self.native_root,
                        self.trading_date,
                        REGISTRATION_DRIFT_REPUT,
                        {
                            "generation": gen,
                            "actual_count": int(regist_num or len(specs)),
                            "reason": reason,
                        },
                    )
                    evidence["drift_reput"] = True
                if ok and verified:
                    self._had_verified_exact50 = True
                self._publish_ingress_registration_result(
                    verified=True,
                    actual_symbols=list(self.registered_symbols),
                    actual_count=int(regist_num or len(specs)),
                    generation=gen,
                    universe_hash=uhash,
                    put_executed=put_executed,
                )
                self.sm.transition(WAITING_FIRST_PUSH, reason=f"register_ok:{reason}")
                self.sm.block_entry("WAITING_FIRST_PUSH")
                self._publish_control(KIND_ENTRY_BLOCK, "WAITING_FIRST_PUSH")
            else:
                self.registered_symbols = []
                self._register_put_ok = False
                self._publish_ingress_registration_result(
                    verified=False,
                    actual_symbols=[],
                    actual_count=0,
                    generation=gen,
                    universe_hash=uhash,
                    put_executed=put_executed,
                )
                self.sm.block_entry("REGISTER_FAILED")
                self._publish_control(KIND_ENTRY_BLOCK, "REGISTER_FAILED")
                self.sm.transition(WAITING_FIRST_PUSH, reason=f"register_not_verified:{reason}")
            self._write_status()
            return {**evidence, "meta": meta}
        except Exception as exc:
            from small_paper.kabu_token_authority import AUTH_INVALID, RATE_LIMIT, classify_kabu_api_error

            err_cls = classify_kabu_api_error(exc)
            evidence = {
                "ok": False,
                "verified": False,
                "put_executed": False,
                "http_status": None,
                "response_body": None,
                "actual_count": 0,
                "actual_symbols": [],
                "desired_count": len(specs),
                "generation": gen,
                "universe_hash": uhash,
                "executed_at": now_iso(),
                "reason": reason,
                "error": str(exc)[:800],
                "error_type": type(exc).__name__,
                "error_class": err_cls,
                "owner": "MARKET_INGRESS_SERVICE",
                "verification_basis": "register_exception",
            }
            self._register_retry_count += 1
            keep_exact = bool(self._had_verified_exact50 or self._register_put_ok)
            if err_cls == AUTH_INVALID:
                evidence["reason"] = "AUTHORITY_TOKEN_INVALIDATED"
                if not keep_exact:
                    self.registered_symbols = []
                    self._register_put_ok = False
                self._register_evidence = evidence
                self._write_register_evidence(evidence)
                self._handle_auth_invalidated(source=f"register:{reason}")
                self._write_status()
                self.sm.last_error = type(exc).__name__
                return evidence
            if err_cls == RATE_LIMIT:
                evidence["reason"] = "RATE_LIMIT"
                if not keep_exact:
                    self.registered_symbols = []
                    self._register_put_ok = False
                self._register_evidence = evidence
                self._write_register_evidence(evidence)
                self._handle_rate_limit(source=f"register:{reason}")
                self._write_status()
                self.sm.last_error = type(exc).__name__
                return evidence
            self.registered_symbols = []
            self._register_put_ok = False
            self._register_evidence = evidence
            self._write_register_evidence(evidence)
            self._publish_ingress_registration_result(
                verified=False,
                actual_symbols=[],
                actual_count=0,
                generation=gen,
                universe_hash=uhash,
                put_executed=False,
            )
            self.sm.block_entry("REGISTER_FAILED")
            self._publish_control(KIND_ENTRY_BLOCK, "REGISTER_FAILED")
            try:
                self.sm.transition(WAITING_FIRST_PUSH, reason=f"register_exc:{reason}")
            except Exception:
                pass
            self._write_status()
            self.sm.last_error = type(exc).__name__
            return evidence
        finally:
            self._register_in_flight = False

    def _write_register_evidence(self, evidence: dict[str, Any]) -> None:
        payload = dict(evidence)
        payload["ingress_session_id"] = self.session_id
        payload["trading_date"] = self.trading_date
        payload["pid"] = os.getpid()
        text = json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n"
        try:
            (self.session_path / "ingress_register_api_trace.json").write_text(text, encoding="utf-8")
            (self.day_root / "ingress_register_api_trace.json").write_text(text, encoding="utf-8")
        except Exception:
            pass
        try:
            with (self.day_root / "ingress_register_api_events.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        except Exception:
            pass

    def _publish_ingress_registration_result(
        self,
        *,
        verified: bool,
        actual_symbols: Sequence[str],
        actual_count: int,
        generation: int,
        universe_hash: str,
        put_executed: bool,
    ) -> None:
        """Update registration manifest from Ingress Kabu PUT evidence (not Paper MATCH)."""
        try:
            from small_paper.market_capture_registration import (
                record_generation_change,
                write_registration_manifest,
            )

            gen_id = f"gen_{self.trading_date}_{int(generation)}"
            src_day = str(self._desired_source_trading_date or self.trading_date)
            if src_day != str(self.trading_date):
                return
            from small_paper.day_fixed_am_registration import canonical_membership_sha

            write_registration_manifest(
                self.native_root,
                trading_date=self.trading_date,
                symbols=list(self.desired_symbols),
                generation_id=gen_id,
                universe_path=self._desired_source_path or None,
                universe_sha256=self._desired_source_sha256 or universe_hash,
                verified=bool(verified),
                owner="MARKET_INGRESS_SERVICE",
                extra={
                    "status": "MATCH" if verified else "REGISTER_FAILED",
                    "actual_symbols": list(actual_symbols),
                    "actual_count": int(actual_count),
                    "put_executed": bool(put_executed),
                    "verification_source": "ingress_kabu_put",
                    "registration_generation": int(generation),
                    "source_trading_date": src_day,
                    "source_path": self._desired_source_path,
                    "source_sha256": self._desired_source_sha256 or universe_hash,
                    "desired_count": len(self.desired_symbols),
                    "registered_count": int(actual_count),
                    "canonical_membership_sha": canonical_membership_sha(self.desired_symbols),
                },
            )
            record_generation_change(
                self.day_root,
                generation_id=gen_id,
                previous_symbols=list(self._last_register_symbols),
                new_symbols=list(self.desired_symbols),
                registration_verified=bool(verified),
                capture_sequence_at_change=int(self.writer.snapshot().get("last_sequence") or 0),
            )
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
        while not self._stop.is_set() and not self._scheduled_end_passed() and not self._operator_stop_requested():
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
        from api.rest_client import KabuNativeRestClient, default_base_url, load_kabu_env

        self.sm.transition(CONNECTING, reason="connect")
        self.sm.bump_connection()
        load_kabu_env(repo_root=self.native_root.parent)
        load_kabu_env(repo_root=self.native_root)
        # Apply any pre-written desired universe before first register attempt
        self._poll_desired_universe_apply_only()
        rest = KabuNativeRestClient(default_base_url())
        from small_paper.kabu_token_authority import owner_issue_context, publish_owned_token

        with owner_issue_context(
            native_root=self.native_root,
            trading_date=self.trading_date,
            pid=os.getpid(),
            session_id=self.session_id,
            caller="ingress_connect",
        ):
            token = rest.issue_token_from_env()
        publish_owned_token(
            token,
            native_root=self.native_root,
            trading_date=self.trading_date,
            caller="ingress_connect",
        )
        push = KabuNativePushClient(rest, token)
        self._push_client = push
        self.sm.transition(REGISTERING, reason="register")
        if self.desired_symbols:
            self._execute_live_register(push, reason="connect")
        else:
            # Empty desired at connect: wait for control-channel update (do not fake registered)
            self.registered_symbols = []
            self._register_put_ok = False
            self.sm.block_entry("WAITING_DESIRED_REGISTER")
            self._publish_control(KIND_ENTRY_BLOCK, "WAITING_DESIRED_REGISTER")
            self.sm.transition(WAITING_FIRST_PUSH, reason="waiting_desired")
        if self.sm.state == REGISTERING:
            self.sm.transition(WAITING_FIRST_PUSH, reason="waiting_push")
        self._receiver_count = 1
        try:
            async for payload in push.iter_messages(recv_poll_sec=5.0):
                if self._stop.is_set() or self._scheduled_end_passed() or self._operator_stop_requested():
                    break
                if self._force_reconnect:
                    self._force_reconnect = False
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
                # Recovery path already consumed the first post-reconnect PUSH at `cur`.
                self._first_recovered_sequence = cur if cur > 0 else 1
                self.sm.transition(RECOVERED, reason=f"attempt{attempt}_pending_ack")
                self.sm.block_entry("recovery_warmup")
                self._publish_control(KIND_ENTRY_BLOCK, "recovery_warmup")
                elapsed = time.monotonic() - t0
                self._notify(
                    "[INGRESS RECOVERY PENDING ACK]",
                    f"attempt={attempt} elapsed_sec={elapsed:.1f} registered={len(self.registered_symbols)}",
                )
                self.maybe_promote_running(reason="live_recovery_pending_ack")
                # Break the pre-recovery receive loop (if still alive) so `_live_async` reconnects.
                self._force_reconnect = True
                self._push_client = None
                return
            except Exception as exc:
                last_err = type(exc).__name__
                continue
        self.sm.transition(RECOVERY_FAILED, reason=last_err or "exhausted")
        self.sm.block_entry("RECOVERY_FAILED")
        self._publish_control(KIND_ENTRY_BLOCK, "RECOVERY_FAILED")
        self._notify("[INGRESS RECOVERY FAILED]", f"err={last_err}")
        self._force_reconnect = True

    async def _connect_register_consume_once_until_first_push(self) -> None:
        from api.push_client import KabuNativePushClient
        from api.rest_client import KabuNativeRestClient, default_base_url, load_kabu_env

        self.sm.bump_connection()
        load_kabu_env(repo_root=self.native_root.parent)
        load_kabu_env(repo_root=self.native_root)
        self._poll_desired_universe_apply_only()
        rest = KabuNativeRestClient(default_base_url())
        from small_paper.kabu_token_authority import owner_issue_context, publish_owned_token

        with owner_issue_context(
            native_root=self.native_root,
            trading_date=self.trading_date,
            pid=os.getpid(),
            session_id=self.session_id,
            caller="ingress_recovery",
        ):
            token = rest.issue_token_from_env()
        publish_owned_token(
            token,
            native_root=self.native_root,
            trading_date=self.trading_date,
            caller="ingress_recovery",
        )
        push = KabuNativePushClient(rest, token)
        self._push_client = push
        # Force re-PUT on recovery even if generation/symbols unchanged
        self._register_put_ok = False
        self._last_register_generation = -1
        reg = self._execute_live_register(push, reason="recovery")
        if not reg.get("ok") or not reg.get("verified"):
            raise RuntimeError(reg.get("error") or "register_failed_recovery")
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
        return scheduled_end_passed(self.trading_date, finalize_hour=FINALIZE_TIME.hour, finalize_minute=FINALIZE_TIME.minute)

    def _operator_stop_requested(self) -> bool:
        """Formal stop path: day_root/operator_stop.flag (checked-runner / cleanup)."""
        try:
            return (self.day_root / "operator_stop.flag").is_file()
        except Exception:
            return False

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
            try:
                svc._write_status()
            except Exception:
                pass
            if svc._stop.is_set() or svc._operator_stop_requested():
                break
    finally:
        if svc.sm.state != STOPPED:
            svc.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
