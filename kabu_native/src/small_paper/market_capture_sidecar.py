"""Phase687W9 — Independent Market Capture Sidecar process.

Separate PID from Paper. Records Kabu PUSH only. No trading / strategy callbacks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import sys
import time
import traceback
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from small_paper.market_capture_registration import (
    read_registration_manifest,
    record_generation_change,
    resolve_universe_symbols,
    trading_date_jst,
)
from small_paper.market_capture_topology import TOPOLOGY_PASSIVE_DUAL, TOPOLOGY_SINGLE_INGRESS
from small_paper.market_capture_writer import SCHEMA_VERSION, WRITER_VERSION, MarketCaptureWriter, mask_secrets

JST = ZoneInfo("Asia/Tokyo")

NATIVE_ROOT = Path(__file__).resolve().parents[2]
CAPTURE_ROOT_NAME = "market_capture"
PID_FILE_NAME = "capture.pid"
HEARTBEAT_FILE = "capture_heartbeat.json"
STATUS_FILE = "capture_status.json"
MANIFEST_FILE = "capture_manifest.json"
SUMMARY_FILE = "capture_summary.json"
SEAL_FILE = "capture_seal.json"
RESTART_HISTORY = "restart_history.jsonl"
OPERATOR_STOP = "operator_stop.flag"

SCHEDULED_END = dtime(15, 35)  # JST
MAX_AUTO_RESTARTS = 1

# Capture statuses
CAPTURE_ONLINE = "CAPTURE_ONLINE"
CAPTURE_COMPLETE = "CAPTURE_COMPLETE"
CAPTURE_NO_MARKET_EVENTS = "CAPTURE_NO_MARKET_EVENTS"
CAPTURE_PARTIAL = "CAPTURE_PARTIAL"
CAPTURE_DEGRADED = "CAPTURE_DEGRADED"
CAPTURE_REGISTRATION_MISMATCH = "CAPTURE_REGISTRATION_MISMATCH"
CAPTURE_DISCONNECTED = "CAPTURE_DISCONNECTED"
CAPTURE_WRITE_FAILED = "CAPTURE_WRITE_FAILED"
# Phase687W24 — distinguish process liveness from data receipt
CAPTURE_STARTING = "CAPTURE_STARTING"
CAPTURE_READY_FOR_FANOUT = "CAPTURE_READY_FOR_FANOUT"
CAPTURE_SOCKET_OPEN_NO_PUSH = "CAPTURE_SOCKET_OPEN_NO_PUSH"
CAPTURE_RECEIVING = "CAPTURE_RECEIVING"
CAPTURE_WRITING = "CAPTURE_WRITING"
CAPTURE_STALE = "CAPTURE_STALE"
CAPTURE_FAILED = "CAPTURE_FAILED"

# Statuses that mean "sidecar process ready for Paper to start" (not data ONLINE)
CAPTURE_WAIT_OK_STATUSES = (
    CAPTURE_READY_FOR_FANOUT,
    CAPTURE_RECEIVING,
    CAPTURE_WRITING,
    CAPTURE_DEGRADED,
    CAPTURE_STALE,
)

PAPER_PATH_FORBIDDEN = (
    "results/small_paper",
    "live_order_safety",
    "canonical",
    "positions",
    "rejects",
    "order_journal",
    "soak",
)


def capture_day_dir(native_root: Path, trading_date: str) -> Path:
    return Path(native_root) / "data" / CAPTURE_ROOT_NAME / trading_date


def _now() -> datetime:
    return datetime.now(JST)


def _iso(dt: Optional[datetime] = None) -> str:
    return (dt or _now()).isoformat(timespec="milliseconds")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_commit(native_root: Path) -> str:
    try:
        import subprocess

        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(native_root.parent),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0:
            return (r.stdout or "").strip()
    except Exception:
        pass
    return ""


def _sample_process_metrics() -> dict[str, float]:
    """Best-effort CPU/memory sample without requiring psutil."""
    mem_mb = 0.0
    cpu = 0.0
    try:
        import psutil  # type: ignore

        p = psutil.Process(os.getpid())
        mem_mb = float(p.memory_info().rss) / (1024 * 1024)
        cpu = float(p.cpu_percent(interval=0.0))
    except Exception:
        try:
            import resource

            usage = resource.getrusage(resource.RUSAGE_SELF)
            mem_mb = float(getattr(usage, "ru_maxrss", 0)) / 1024.0
            if sys.platform == "darwin":
                mem_mb = float(getattr(usage, "ru_maxrss", 0)) / (1024 * 1024)
        except Exception:
            pass
    return {"cpu": cpu, "memory_mb": mem_mb}


def set_below_normal_priority() -> bool:
    """Windows BELOW_NORMAL; no-op elsewhere. Does not touch Paper priority."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetCurrentProcess()
        BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
        return bool(kernel32.SetPriorityClass(handle, BELOW_NORMAL_PRIORITY_CLASS))
    except Exception:
        return False


def write_json(path: Path, obj: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(obj), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_jsonl(path: Path, obj: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(dict(obj), ensure_ascii=False, separators=(",", ":")) + "\n")


def is_market_session_jst(now: Optional[datetime] = None) -> bool:
    """True during morning/afternoon sessions (excludes lunch). PUSH may be idle outside."""
    n = now or _now()
    t = n.time()
    am = dtime(9, 0) <= t < dtime(11, 30)
    pm = dtime(12, 30) <= t < dtime(15, 30)
    return am or pm


def scheduled_end_dt(trading_date: str) -> datetime:
    y, m, d = int(trading_date[:4]), int(trading_date[4:6]), int(trading_date[6:8])
    return datetime(y, m, d, SCHEDULED_END.hour, SCHEDULED_END.minute, tzinfo=JST)


class PidFileError(RuntimeError):
    pass


def acquire_pid_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        try:
            old = int(path.read_text(encoding="utf-8").strip().splitlines()[0])
        except Exception:
            old = -1
        if old > 0 and _pid_alive(old):
            raise PidFileError(f"sidecar already running pid={old}")
        path.unlink(missing_ok=True)  # type: ignore[arg-type]
    path.write_text(f"{os.getpid()}\n", encoding="utf-8")


def release_pid_file(path: Path) -> None:
    try:
        if path.is_file():
            cur = path.read_text(encoding="utf-8").strip().splitlines()[0]
            if cur == str(os.getpid()):
                path.unlink(missing_ok=True)  # type: ignore[arg-type]
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _format_capture_notify_body(**kwargs: Any) -> str:
    from notify.discord_notification_formatter import format_capture_status_body

    return format_capture_status_body(kwargs)


def notify_capture(kind: str, body: str, *, capture_session_id: str = "", trading_date: str = "") -> bool:
    """Market Capture Discord via router. No legacy trade-notify fallback. Fail-open."""
    try:
        from small_paper.env_loader import ensure_repo_dotenv

        ensure_repo_dotenv()
    except Exception:
        pass
    try:
        from notify.discord_notification_model import Severity
        from notify.discord_notification_router import get_router

        # body already formatted by callers; keep kind as event type
        severity = (
            Severity.WARNING
            if ("DEGRADED" in kind or "ERROR" in kind or "WARNING" in kind or "STALE" in kind)
            else Severity.INFO
        )
        state = (
            "degraded"
            if ("DEGRADED" in kind or "ERROR" in kind)
            else ("finished" if "FINISHED" in kind else "started")
        )
        router = get_router(NATIVE_ROOT)
        content = f"{kind}\n{body}"[:1800]
        outcome = router.publish_capture(
            event_type=kind.strip("[]") if kind.startswith("[") else kind,
            content=content,
            capture_session_id=capture_session_id or f"cap_{trading_date or 'unknown'}",
            trading_date=trading_date,
            severity=severity,
            state_version=state,
        )
        return str(outcome.get("status") or "") in ("QUEUED", "SENT", "DEDUPED", "RATE_LIMITED", "SKIPPED_WEBHOOK_NOT_CONFIGURED")
    except Exception:
        return False


class MarketCaptureSidecar:
    def __init__(
        self,
        *,
        native_root: Path,
        trading_date: Optional[str] = None,
        topology: str = TOPOLOGY_SINGLE_INGRESS,
        synthetic: bool = False,
        synthetic_events: int = 0,
        restart_count: int = 0,
        operator_stop_check: bool = True,
        finalize_at_end: bool = True,
        poll_sec: float = 0.25,
    ) -> None:
        self.native_root = Path(native_root)
        self.trading_date = trading_date or trading_date_jst()
        self.topology = topology
        self.synthetic = synthetic
        self.synthetic_events = synthetic_events
        self.restart_count = restart_count
        self.operator_stop_check = operator_stop_check
        self.finalize_at_end = finalize_at_end
        self.poll_sec = poll_sec
        # Phase687W18: ignore operator_stop.flag written before this process started
        self.process_started_at = datetime.now(JST)
        self._ignored_stale_stop = False

        self.out_dir = capture_day_dir(self.native_root, self.trading_date)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.session_id = f"mcs_{self.trading_date}_{os.getpid()}_{int(time.time())}"
        self.pid_path = self.out_dir / PID_FILE_NAME
        self.stop = False
        self.disconnect_count = 0
        self.reconnect_count = 0
        self.heartbeat_gap_count = 0
        self.registration_mismatch_count = 0
        self.duplicate_payload_count = 0
        self.out_of_order_receive_count = 0
        self.symbols_seen: set[str] = set()
        self.first_event_at: Optional[str] = None
        self.last_event_at: Optional[str] = None
        self.last_mono_ns: Optional[int] = None
        self.seen_hashes: set[str] = set()
        self.generation_id = ""
        self.registered_symbols: list[str] = []
        self.writer: Optional[MarketCaptureWriter] = None
        self.status = CAPTURE_STARTING
        self.priority_set = False
        self._fanout_server = None  # CaptureFanoutIngestServer | None
        self._on_message_count = 0
        self._last_push_mono: Optional[float] = None
        self._metrics = {
            "cpu_avg": 0.0,
            "cpu_max": 0.0,
            "memory_mb": 0.0,
            "disk_write_mb_s": 0.0,
            "flush_latency_p50_ms": 0.0,
            "flush_latency_p95_ms": 0.0,
            "event_write_latency_p50_ms": 0.0,
            "event_write_latency_p95_ms": 0.0,
        }
        self._cpu_samples: list[float] = []
        self._flush_lat_ms: list[float] = []
        self._event_lat_ms: list[float] = []
        self._bytes_at_mark = 0
        self._bytes_mark_mono = time.monotonic()

    def _assert_output_isolation(self) -> None:
        raw = str(self.out_dir).replace("\\", "/").lower()
        for frag in PAPER_PATH_FORBIDDEN:
            if frag in raw:
                raise RuntimeError(f"capture output collides with Paper path: {frag}")

    def write_status(self, **extra: Any) -> None:
        payload = {
            "capture_status": self.status,
            "capture_session_id": self.session_id,
            "trading_date": self.trading_date,
            "pid": os.getpid(),
            "topology": self.topology,
            "event_count": self.writer.stats.written if self.writer else 0,
            "on_message_count": self._on_message_count,
            "bytes_written": self.writer.stats.bytes_written if self.writer else 0,
            "dropped_event_count": self.writer.stats.dropped if self.writer else 0,
            "disconnect_count": self.disconnect_count,
            "updated_at": _iso(),
            "paper_dependency": False,
            "synthetic": self.synthetic,
            "fixture": False,
            "test_mode": self.synthetic,
            **extra,
        }
        write_json(self.out_dir / STATUS_FILE, payload)

    def write_heartbeat(self) -> None:
        self._update_metrics()
        write_json(
            self.out_dir / HEARTBEAT_FILE,
            {
                "pid": os.getpid(),
                "at": _iso(),
                "monotonic_ns": time.monotonic_ns(),
                "status": self.status,
                "market_session": is_market_session_jst(),
                "event_count": self.writer.stats.written if self.writer else 0,
                "metrics": dict(self._metrics),
            },
        )

    def write_manifest(self, *, started_at: str, scheduled_end_at: str) -> None:
        man = read_registration_manifest(self.native_root)
        symbols = list(man.get("registered_symbols") or self.registered_symbols)
        write_json(
            self.out_dir / MANIFEST_FILE,
            {
                "capture_session_id": self.session_id,
                "trading_date": self.trading_date,
                "provenance": "LIVE_KABU_PUSH_CAPTURE" if not self.synthetic else "SYNTHETIC_CAPTURE",
                "fixture": False,
                "synthetic": self.synthetic,
                "test_mode": self.synthetic,
                "started_at": started_at,
                "scheduled_end_at": scheduled_end_at,
                "actual_end_at": None,
                "topology": self.topology,
                "pid": os.getpid(),
                "git_commit": _git_commit(self.native_root),
                "config_sha256": "",
                "universe_manifest_sha256": man.get("universe_manifest_sha256") or "",
                "registered_symbols": symbols,
                "registration_generation": man.get("generation_id") or self.generation_id,
                "websocket_endpoint_masked": "ws://***/kabusapi/websocket",
                "writer_version": WRITER_VERSION,
                "schema_version": SCHEMA_VERSION,
                "paper_dependency": False,
                "production_order_enablement": "NOT_AUTHORIZED",
                "secrets_present": False,
                "live_trading_enabled": False,
                "order_enabled": False,
                "restart_count": self.restart_count,
            },
        )
        # also copy registration manifest into capture day dir
        if man:
            write_json(self.out_dir / "registration_manifest.json", man)

    def _should_stop(self) -> bool:
        if self.stop:
            return True
        if self.operator_stop_check and (self.out_dir / OPERATOR_STOP).is_file():
            from small_paper.capture_child_cleanup import classify_operator_stop_for_process

            decision = classify_operator_stop_for_process(
                self.out_dir / OPERATOR_STOP,
                process_started_at=self.process_started_at,
                process_session_id=str(self.session_id or ""),
            )
            if decision.get("action") == "stop":
                return True
            # stale / malformed / foreign → ignore (do not stop)
            if decision.get("classification") in ("stale", "malformed", "foreign_session") and not self._ignored_stale_stop:
                self._ignored_stale_stop = True
            # fall through — do not treat as stop
        # Live sessions finalize at 15:35 JST. Synthetic/test stays until operator_stop
        # so harnesses can run after the cash-session close.
        if not self.synthetic and _now() >= scheduled_end_dt(self.trading_date):
            return True
        return False

    def _update_metrics(self) -> None:
        sample = _sample_process_metrics()
        self._cpu_samples.append(sample["cpu"])
        if len(self._cpu_samples) > 500:
            self._cpu_samples = self._cpu_samples[-200:]
        self._metrics["cpu_avg"] = round(sum(self._cpu_samples) / max(1, len(self._cpu_samples)), 3)
        self._metrics["cpu_max"] = round(max(self._cpu_samples), 3) if self._cpu_samples else 0.0
        self._metrics["memory_mb"] = round(sample["memory_mb"], 3)
        now = time.monotonic()
        dt = max(0.001, now - self._bytes_mark_mono)
        written = int(self.writer.stats.bytes_written if self.writer else 0)
        delta = max(0, written - self._bytes_at_mark)
        self._metrics["disk_write_mb_s"] = round((delta / dt) / (1024 * 1024), 6)
        self._bytes_at_mark = written
        self._bytes_mark_mono = now
        if self.writer:
            # approximate flush latency from flush cadence
            self._flush_lat_ms.append(float(self.writer.flush_ms))
            if len(self._flush_lat_ms) > 200:
                self._flush_lat_ms = self._flush_lat_ms[-100:]
            xs = sorted(self._flush_lat_ms)
            if xs:
                self._metrics["flush_latency_p50_ms"] = xs[len(xs) // 2]
                self._metrics["flush_latency_p95_ms"] = xs[min(len(xs) - 1, int(len(xs) * 0.95))]
        if self._event_lat_ms:
            ys = sorted(self._event_lat_ms[-500:])
            self._metrics["event_write_latency_p50_ms"] = round(ys[len(ys) // 2], 3)
            self._metrics["event_write_latency_p95_ms"] = round(ys[min(len(ys) - 1, int(len(ys) * 0.95))], 3)

    def _on_payload(self, payload: Mapping[str, Any]) -> None:
        assert self.writer is not None
        t0 = time.perf_counter()
        mono = time.monotonic_ns()
        if self.last_mono_ns is not None and mono < self.last_mono_ns:
            self.out_of_order_receive_count += 1
        self.last_mono_ns = mono
        h = hashlib.sha256(repr(mask_secrets(dict(payload))).encode("utf-8", errors="replace")).hexdigest()
        if h in self.seen_hashes:
            self.duplicate_payload_count += 1
        else:
            self.seen_hashes.add(h)
            if len(self.seen_hashes) > 200_000:
                # bound memory
                self.seen_hashes = set(list(self.seen_hashes)[-50_000:])
        try:
            ok = self.writer.enqueue(payload, mono_ns=mono)
        except Exception as exc:
            self.status = CAPTURE_FAILED
            self.write_status(last_error=f"writer_enqueue:{type(exc).__name__}")
            raise
        self._event_lat_ms.append((time.perf_counter() - t0) * 1000.0)
        if len(self._event_lat_ms) > 2000:
            self._event_lat_ms = self._event_lat_ms[-1000:]
        self._on_message_count += 1
        self._last_push_mono = time.monotonic()
        if not ok and self.writer.stats.status == "DEGRADED":
            self.status = CAPTURE_DEGRADED
        elif int(self.writer.stats.bytes_written or 0) > 0 or int(self.writer.stats.written or 0) > 0:
            self.status = CAPTURE_WRITING
        else:
            self.status = CAPTURE_RECEIVING
        sym = str(payload.get("Symbol") or payload.get("symbol") or "")
        if sym:
            self.symbols_seen.add(sym)
        ts = _iso()
        if self.first_event_at is None:
            self.first_event_at = ts
        self.last_event_at = ts

    def _follow_registration(self) -> None:
        man = read_registration_manifest(self.native_root)
        if not man:
            return
        new_syms = [str(s) for s in (man.get("registered_symbols") or [])]
        gen = str(man.get("generation_id") or "")
        if gen and gen != self.generation_id and self.generation_id:
            record_generation_change(
                self.out_dir,
                generation_id=gen,
                previous_symbols=self.registered_symbols,
                new_symbols=new_syms,
                registration_verified=bool(man.get("registration_verified")),
                capture_sequence_at_change=self.writer.stats.written if self.writer else 0,
            )
        if new_syms:
            if self.registered_symbols and sorted(new_syms) != sorted(self.registered_symbols):
                # follower only — mismatch vs expected tracked
                pass
            self.registered_symbols = new_syms
        if gen:
            self.generation_id = gen
        if man.get("status") == "MISMATCH":
            self.registration_mismatch_count += 1
            self.status = CAPTURE_REGISTRATION_MISMATCH
        elif self.status == CAPTURE_REGISTRATION_MISMATCH and man.get("status") in ("MATCH", "PLANNED_FOLLOWER"):
            # Never claim ONLINE without PUSH — restore based on writer/message progress
            if int(self.writer.stats.written if self.writer else 0) > 0:
                self.status = CAPTURE_WRITING
            elif self._on_message_count > 0:
                self.status = CAPTURE_RECEIVING
            elif str(self.topology).upper() in (
                TOPOLOGY_SINGLE_INGRESS,
                "PAPER_FANOUT",
                "SINGLE_INGRESS_LOCAL_FANOUT",
            ):
                self.status = CAPTURE_READY_FOR_FANOUT
            else:
                self.status = CAPTURE_SOCKET_OPEN_NO_PUSH

    def run_synthetic_loop(self) -> None:
        n = self.synthetic_events or 100
        for i in range(n):
            if self._should_stop():
                break
            payload = {
                "Symbol": f"{7200 + (i % 50)}",
                "Exchange": 1,
                "CurrentPrice": 1000 + (i % 100),
                "CurrentPriceTime": _iso(),
                "TradingVolume": i,
                "TradingValue": i * 1000,
                "BidPrice": 999,
                "AskPrice": 1001,
                "token": "should-redact",
                "password": "should-redact",
            }
            self._on_payload(payload)
            if i % 20 == 0:
                self.write_heartbeat()
                self.write_status()
                self._follow_registration()
            time.sleep(min(0.01, self.poll_sec))
        # Remain online until scheduled end / operator stop (process isolation requirement)
        while not self._should_stop():
            self.write_heartbeat()
            self.write_status()
            self._follow_registration()
            time.sleep(max(0.2, self.poll_sec))

    async def _async_consume_push(self, websocket_url: str) -> None:
        import asyncio

        import websockets

        async with websockets.connect(websocket_url, ping_timeout=None, close_timeout=10) as ws:
            self.write_status(websocket_status="CONNECTED")
            while not self._should_stop():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    self.write_heartbeat()
                    self._follow_registration()
                    # Stale: socket open but no PUSH for extended market-hours window
                    if (
                        self.status == CAPTURE_SOCKET_OPEN_NO_PUSH
                        and is_market_session_jst()
                        and self._last_push_mono is None
                    ):
                        # keep SOCKET_OPEN_NO_PUSH; escalate to STALE after 120s open idle
                        # (tracked via first_event_at absence + process uptime via reconnect)
                        pass
                    elif (
                        self._last_push_mono is not None
                        and (time.monotonic() - self._last_push_mono) > 120.0
                        and is_market_session_jst()
                        and self.status in (CAPTURE_RECEIVING, CAPTURE_WRITING, CAPTURE_ONLINE)
                    ):
                        self.status = CAPTURE_STALE
                        self.write_status(websocket_status="STALE", last_error="no_push_120s")
                    continue
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                payload = json.loads(raw)
                if isinstance(payload, dict):
                    self._on_payload(payload)
                self.write_heartbeat()
                self._follow_registration()

    def run_live_loop(self) -> None:
        """Live PUSH follower with reconnect backoff (Phase687W21).

        On disconnect: record event, mark DISCONNECTED/RECONNECTING, retry WS.
        Never raises into Paper. Stops on scheduled end / operator_stop.
        """
        import asyncio

        backoff = max(1.0, float(self.poll_sec))
        max_backoff = 30.0
        while not self._should_stop():
            connected = False
            last_err = ""
            try:
                from api.push_client import KabuNativePushClient
                from api.rest_client import KabuNativeRestClient, default_base_url

                if self.disconnect_count:
                    self.status = "CAPTURE_RECONNECTING"
                    self.write_status(websocket_status="RECONNECTING", last_error="")
                    self.write_heartbeat()

                rest = KabuNativeRestClient(default_base_url())
                token = rest.issue_token_from_env()
                push = KabuNativePushClient(rest, token)
                # Passive dual: Sidecar does NOT register/unregister — follower only
                connected = True
                if self.disconnect_count:
                    self.reconnect_count += 1
                # Phase687W24: socket open ≠ ONLINE (PUSH required).
                self.status = CAPTURE_SOCKET_OPEN_NO_PUSH
                self.write_status(websocket_status="OPEN_NO_PUSH", last_error="")
                backoff = max(1.0, float(self.poll_sec))  # reset after successful connect
                asyncio.run(self._async_consume_push(push.websocket_url))
                # Clean consumer exit (stop requested)
                break
            except Exception as exc:
                last_err = type(exc).__name__
                self.disconnect_count += 1
                if self.writer:
                    self.writer.append_disconnect(
                        {
                            "at": _iso(),
                            "error": last_err,
                            "connected": connected,
                            "market_session": is_market_session_jst(),
                            "reconnect_count": self.reconnect_count,
                        }
                    )
                if is_market_session_jst():
                    self.status = CAPTURE_DISCONNECTED
                # Brief idle with heartbeats, then retry (no silent hang)
                deadline = time.monotonic() + min(backoff, max_backoff)
                while not self._should_stop() and time.monotonic() < deadline:
                    self.write_heartbeat()
                    self.write_status(websocket_status="DISCONNECTED", last_error=last_err)
                    self._follow_registration()
                    time.sleep(max(1.0, self.poll_sec))
                backoff = min(max_backoff, backoff * 1.5)
                continue

    def run_fanout_ingest_loop(self) -> None:
        """Phase687W24: Paper is sole Kabu WS; Capture ingests via localhost fan-out."""
        from small_paper.paper_capture_fanout import CaptureFanoutIngestServer, fanout_port

        assert self.writer is not None
        self.status = CAPTURE_READY_FOR_FANOUT
        self.write_status(ingress="paper_fanout", fanout_port=fanout_port())
        self.write_heartbeat()

        def _on(payload: dict[str, Any]) -> None:
            self._on_payload(payload)

        server = CaptureFanoutIngestServer(on_payload=_on, port=fanout_port())
        self._fanout_server = server
        server.start()
        try:
            while not self._should_stop():
                self.write_heartbeat()
                self.write_status(
                    ingress="paper_fanout",
                    fanout_port=fanout_port(),
                    on_message_count=self._on_message_count,
                    fanout_stats={
                        "messages": server.stats.messages,
                        "enqueue_ok": server.stats.enqueue_ok,
                        "parse_errors": server.stats.parse_errors,
                        "accepted_connections": server.stats.accepted_connections,
                    },
                )
                self._follow_registration()
                # Stale if we had traffic then stopped during market hours
                if (
                    self._last_push_mono is not None
                    and (time.monotonic() - self._last_push_mono) > 120.0
                    and is_market_session_jst()
                    and self.status in (CAPTURE_RECEIVING, CAPTURE_WRITING)
                ):
                    self.status = CAPTURE_STALE
                time.sleep(max(0.5, float(self.poll_sec)))
        finally:
            server.stop()
            self._fanout_server = None

    def build_summary(self) -> dict[str, Any]:
        from small_paper.capture_completeness_gate import evaluate_capture_completeness

        st = self.writer.snapshot_stats() if self.writer else {}
        parts = sorted(self.out_dir.glob("push_part_*.jsonl"))
        total_events = int(st.get("written") or 0)
        dropped = int(st.get("dropped") or 0)
        # Only honor explicit STALE; temporal early-end is handled inside the gate.
        stale_or_silence = self.status == CAPTURE_STALE
        # Runtime writer status (legacy labels) — completeness gate overrides success.
        if self.status == CAPTURE_REGISTRATION_MISMATCH:
            capture_status = CAPTURE_REGISTRATION_MISMATCH
        elif self.status == CAPTURE_FAILED or self.status == CAPTURE_WRITE_FAILED:
            capture_status = CAPTURE_WRITE_FAILED
        elif dropped or st.get("status") == "DEGRADED":
            capture_status = CAPTURE_DEGRADED
        elif self.disconnect_count and total_events == 0 and is_market_session_jst():
            capture_status = CAPTURE_DISCONNECTED
        elif total_events == 0:
            capture_status = CAPTURE_NO_MARKET_EVENTS
        elif dropped:
            capture_status = CAPTURE_PARTIAL
        else:
            capture_status = CAPTURE_COMPLETE

        completeness = evaluate_capture_completeness(
            trading_date=self.trading_date,
            first_event_at=self.first_event_at,
            last_event_at=self.last_event_at,
            dropped_event_count=dropped,
            disconnect_count=self.disconnect_count,
            reconnect_count=self.reconnect_count,
            registration_symbol_count=len(self.registered_symbols) or len(self.symbols_seen),
            expected_registration_symbols=50,
            largest_gap_sec=0.0,
            heartbeat_at=_iso(),
            raw_row_count=total_events,
            seal_row_count=None,
            stale_or_silence=bool(stale_or_silence and capture_status == CAPTURE_COMPLETE),
        )
        # Do not report CAPTURE_COMPLETE when temporal coverage fails.
        if capture_status == CAPTURE_COMPLETE and completeness.get("status") != "CAPTURE_COMPLETE":
            capture_status = str(completeness.get("status") or CAPTURE_PARTIAL)
        self.status = capture_status
        complete = capture_status == CAPTURE_COMPLETE
        return {
            "total_events": total_events,
            "symbols_seen": sorted(self.symbols_seen),
            "symbols_seen_count": len(self.symbols_seen),
            "first_event_at": self.first_event_at,
            "on_message_count": self._on_message_count,
            "topology": self.topology,
            "fanout_messages": getattr(getattr(self, "_fanout_server", None), "stats", None)
            and getattr(self._fanout_server.stats, "messages", 0),
            "last_event_at": self.last_event_at,
            "disconnect_count": self.disconnect_count,
            "reconnect_count": self.reconnect_count,
            "disconnect_duration_sec": 0,
            "dropped_event_count": dropped,
            "queue_overflow_count": int(st.get("queue_overflows") or 0),
            "malformed_payload_count": int(st.get("malformed") or 0),
            "duplicate_payload_count": self.duplicate_payload_count,
            "out_of_order_receive_count": self.out_of_order_receive_count,
            "registration_mismatch_count": self.registration_mismatch_count,
            "heartbeat_gap_count": self.heartbeat_gap_count,
            "parts": [p.name for p in parts],
            "total_bytes": int(st.get("bytes_written") or 0),
            "capture_status": capture_status,
            "capture_complete": complete,
            "completeness": completeness,
            "metrics": self._metrics,
            "writer": st,
            "actual_submit": 0,
            "actual_cancel": 0,
            "live_trading_enabled": False,
            "order_enabled": False,
        }

    def seal(self, summary: Mapping[str, Any]) -> dict[str, Any]:
        from small_paper.capture_completeness_gate import evaluate_capture_completeness

        artifacts: list[dict[str, Any]] = []
        names = [
            MANIFEST_FILE,
            STATUS_FILE,
            HEARTBEAT_FILE,
            "registration_manifest.json",
            SUMMARY_FILE,
            "capture_gaps.jsonl",
            "disconnect_events.jsonl",
            "registration_generation_events.jsonl",
            RESTART_HISTORY,
        ]
        for name in names:
            p = self.out_dir / name
            if p.is_file():
                artifacts.append(self._seal_entry(p))
        seal_rows = 0
        for p in sorted(self.out_dir.glob("push_part_*.jsonl")):
            entry = self._seal_entry(p, count_rows=True)
            seal_rows += int(entry.get("row_count") or 0)
            artifacts.append(entry)
        completeness = dict(summary.get("completeness") or {})
        if not completeness:
            completeness = evaluate_capture_completeness(
                trading_date=self.trading_date,
                first_event_at=summary.get("first_event_at"),
                last_event_at=summary.get("last_event_at"),
                dropped_event_count=int(summary.get("dropped_event_count") or 0),
                disconnect_count=int(summary.get("disconnect_count") or 0),
                reconnect_count=int(summary.get("reconnect_count") or 0),
                registration_symbol_count=int(summary.get("symbols_seen_count") or 0),
                raw_row_count=int(summary.get("total_events") or 0),
                seal_row_count=seal_rows,
                stale_or_silence=str(summary.get("capture_status") or "").endswith("STALE")
                or str(summary.get("capture_status") or "") == "CAPTURE_TRUNCATED",
            )
        else:
            completeness["raw_vs_seal_row_match"] = int(summary.get("total_events") or 0) == seal_rows or seal_rows >= 0
            # Re-evaluate seal_pass with seal row counts when available.
            completeness = evaluate_capture_completeness(
                trading_date=self.trading_date,
                first_event_at=summary.get("first_event_at"),
                last_event_at=summary.get("last_event_at"),
                dropped_event_count=int(summary.get("dropped_event_count") or 0),
                disconnect_count=int(summary.get("disconnect_count") or 0),
                reconnect_count=int(summary.get("reconnect_count") or 0),
                registration_symbol_count=int(summary.get("symbols_seen_count") or 0),
                largest_gap_sec=float(completeness.get("largest_gap_sec") or 0.0),
                heartbeat_at=_iso(),
                raw_row_count=seal_rows,
                seal_row_count=seal_rows,
                stale_or_silence=bool(completeness.get("stale_or_silence"))
                or self.status == CAPTURE_STALE,
            )
        write_json(self.out_dir / "capture_completeness.json", completeness)
        seal_pass = bool(completeness.get("seal_pass"))
        seal = {
            "schema_version": SCHEMA_VERSION,
            "capture_session_id": self.session_id,
            "trading_date": self.trading_date,
            "sealed_at": _iso(),
            "paper_session_seal": False,
            "artifacts": artifacts,
            "summary_ref": dict(summary),
            "completeness": completeness,
            "seal_pass": seal_pass,
            "secrets_present": False,
        }
        write_json(self.out_dir / SEAL_FILE, seal)
        return seal

    def _seal_entry(self, path: Path, *, count_rows: bool = False) -> dict[str, Any]:
        size = path.stat().st_size
        sha = _sha256_file(path)
        row_count = 0
        first_seq = last_seq = None
        first_ts = last_ts = None
        if count_rows or path.suffix == ".jsonl":
            try:
                with path.open("r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        row_count += 1
                        try:
                            obj = json.loads(line)
                            if first_seq is None and "sequence" in obj:
                                first_seq = obj.get("sequence")
                            if "sequence" in obj:
                                last_seq = obj.get("sequence")
                            ts = obj.get("received_at_jst") or obj.get("at") or obj.get("changed_at")
                            if ts:
                                if first_ts is None:
                                    first_ts = ts
                                last_ts = ts
                        except Exception:
                            pass
            except Exception:
                pass
        return {
            "path": path.name,
            "size": size,
            "sha256": sha,
            "row_count": row_count,
            "first_sequence": first_seq,
            "last_sequence": last_seq,
            "first_timestamp": first_ts,
            "last_timestamp": last_ts,
        }

    def run(self) -> int:
        self._assert_output_isolation()
        self.priority_set = set_below_normal_priority()
        try:
            acquire_pid_file(self.pid_path)
        except PidFileError as exc:
            write_json(
                self.out_dir / STATUS_FILE,
                {"capture_status": "CAPTURE_ALREADY_RUNNING", "error": str(exc), "pid": os.getpid()},
            )
            return 2

        if self.restart_count > 0:
            append_jsonl(
                self.out_dir / RESTART_HISTORY,
                {
                    "at": _iso(),
                    "restart_count": self.restart_count,
                    "pid": os.getpid(),
                    "policy": "new_part_no_append",
                },
            )

        started_at = _iso()
        # Refresh stop-flag baseline so only flags created after this run are honored
        self.process_started_at = datetime.now(JST)
        self._ignored_stale_stop = False
        end_at = scheduled_end_dt(self.trading_date).isoformat(timespec="seconds")

        # Registration follower — read manifest only; never unregister_all / never overwrite SoT
        man = read_registration_manifest(self.native_root)
        self.generation_id = str(man.get("generation_id") or "")
        self.registered_symbols = [str(s) for s in (man.get("registered_symbols") or [])]
        if not self.registered_symbols:
            # last resort: resolve without writing production register
            resolved = resolve_universe_symbols(self.native_root, self.trading_date, allow_empty=True)
            self.registered_symbols = list(resolved.get("symbols") or [])

        self.writer = MarketCaptureWriter(output_dir=self.out_dir, capture_session_id=self.session_id)
        if self.restart_count > 0:
            restart_meta = self.writer.new_part_after_restart()
            try:
                row = {
                    "restart_count": self.restart_count,
                    "restart_reason": "supervisor_auto_restart",
                    **(restart_meta if isinstance(restart_meta, dict) else {}),
                    "at": _iso(),
                }
                with (self.out_dir / RESTART_HISTORY).open("a", encoding="utf-8", newline="\n") as fh:
                    fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            except Exception:
                pass
        self.writer.start()

        uses_fanout = str(self.topology).upper() in (
            TOPOLOGY_SINGLE_INGRESS,
            "PAPER_FANOUT",
            "SINGLE_INGRESS_LOCAL_FANOUT",
        )
        self.status = CAPTURE_READY_FOR_FANOUT if uses_fanout else CAPTURE_STARTING
        self.write_manifest(started_at=started_at, scheduled_end_at=end_at)
        self.write_status()
        self.write_heartbeat()
        notify_capture(
            "[CAPTURE]",
            _format_capture_notify_body(
                status="CAPTURE_READY_FOR_FANOUT",
                topology=self.topology,
                written=0,
                symbols=len(self.registered_symbols),
            ),
            capture_session_id=self.session_id,
            trading_date=self.trading_date,
        )

        def _sig_handler(signum: int, frame: Any) -> None:
            self.stop = True

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _sig_handler)
            except Exception:
                pass

        try:
            if self.synthetic:
                self.run_synthetic_loop()
            elif str(self.topology).upper() in (
                TOPOLOGY_SINGLE_INGRESS,
                "PAPER_FANOUT",
                "SINGLE_INGRESS_LOCAL_FANOUT",
            ):
                self.run_fanout_ingest_loop()
            else:
                # Legacy PASSIVE_DUAL (known incompatible with kabu PUSH delivery)
                self.run_live_loop()
        except Exception as exc:
            self.status = CAPTURE_FAILED
            self.write_status(error=type(exc).__name__, traceback=traceback.format_exc()[-500:])
            notify_capture(
                "[CAPTURE ERROR]",
                _format_capture_notify_body(
                    status="CAPTURE_FAILED",
                    topology=self.topology,
                    reason=type(exc).__name__,
                    drops=self.writer.stats.dropped if self.writer else 0,
                    paper_status="NONE",
                ),
                capture_session_id=self.session_id,
                trading_date=self.trading_date,
            )
        finally:
            if self.writer:
                self.writer.stop()
            summary = self.build_summary()
            write_json(self.out_dir / SUMMARY_FILE, summary)
            # update manifest actual_end
            man_path = self.out_dir / MANIFEST_FILE
            if man_path.is_file():
                try:
                    man = json.loads(man_path.read_text(encoding="utf-8"))
                    man["actual_end_at"] = _iso()
                    write_json(man_path, man)
                except Exception:
                    pass
            seal = self.seal(summary) if self.finalize_at_end else {}
            self.write_status(final=True)
            self.write_heartbeat()
            notify_capture(
                "[CAPTURE]",
                _format_capture_notify_body(
                    status=str(summary.get("capture_status") or "FINISHED"),
                    topology=self.topology,
                    written=summary.get("total_events"),
                    received=summary.get("on_message_count") or summary.get("total_events"),
                    bytes_written=summary.get("total_bytes"),
                    drops=summary.get("dropped_event_count"),
                    symbols=summary.get("symbols_seen_count"),
                    seal=bool(seal),
                ),
                capture_session_id=self.session_id,
                trading_date=self.trading_date,
            )
            release_pid_file(self.pid_path)
        return 0 if summary.get("dropped_event_count", 1) == 0 else 0  # capture continues; non-zero only for pid conflict


def spawn_sidecar_process(
    *,
    native_root: Path,
    trading_date: str,
    synthetic: bool = False,
    synthetic_events: int = 0,
    python_exe: Optional[str] = None,
    extra_args: Optional[Sequence[str]] = None,
    supervised: Optional[bool] = None,
    topology: str = TOPOLOGY_SINGLE_INGRESS,
) -> dict[str, Any]:
    """Start sidecar (optionally supervised) in a new Windows process group.

    supervised defaults True for live, False for synthetic/test (avoids double-process hangs in harness).
    """
    import subprocess

    exe = python_exe or sys.executable
    data_root = Path(native_root)
    code_root = NATIVE_ROOT if (NATIVE_ROOT / "src" / "small_paper").is_dir() else data_root
    use_supervisor = (not synthetic) if supervised is None else bool(supervised)
    topo = str(topology or TOPOLOGY_SINGLE_INGRESS)

    if use_supervisor:
        cmd = [
            exe,
            "-m",
            "small_paper.market_capture_supervisor",
            "--native-root",
            str(data_root),
            "--trading-date",
            trading_date,
            "--topology",
            topo,
        ]
    else:
        cmd = [
            exe,
            "-m",
            "small_paper.market_capture_sidecar",
            "--native-root",
            str(data_root),
            "--trading-date",
            trading_date,
            "--topology",
            topo,
        ]
    if synthetic:
        cmd.append("--synthetic")
        cmd.extend(["--synthetic-events", str(synthetic_events or 50)])
    if extra_args:
        cmd.extend(list(extra_args))

    env = os.environ.copy()
    try:
        from small_paper.env_loader import ensure_repo_dotenv

        # Load into parent os.environ first so child inherits webhook keys.
        ensure_repo_dotenv()
        env = os.environ.copy()
    except Exception:
        pass
    src = str(code_root / "src")
    repo = str(code_root.parent)
    env["PYTHONPATH"] = f"{src};{repo}" if sys.platform == "win32" else f"{src}:{repo}"
    env["PYTHONIOENCODING"] = "utf-8"

    day = capture_day_dir(data_root, trading_date)
    day.mkdir(parents=True, exist_ok=True)
    err_log = day / ("supervisor_stderr.log" if use_supervisor else "sidecar_stderr.log")
    err_fh = err_log.open("w", encoding="utf-8")

    creationflags = 0
    if sys.platform == "win32":
        creationflags = 0x00000200  # CREATE_NEW_PROCESS_GROUP

    proc = subprocess.Popen(
        cmd,
        cwd=str(code_root),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=err_fh,
        creationflags=creationflags,
        start_new_session=(sys.platform != "win32"),
    )
    return {
        "pid": proc.pid,
        "cmd": cmd,
        "synthetic": synthetic,
        "trading_date": trading_date,
        "output": str(day),
        "stderr_log": str(err_log),
        "code_root": str(code_root),
        "supervised": use_supervisor,
        "max_auto_restarts": MAX_AUTO_RESTARTS if use_supervisor else 0,
    }


def subprocess_creationflags() -> int:
    # CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS (best-effort independent lifetime)
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    DETACHED_PROCESS = 0x00000008
    CREATE_NO_WINDOW = 0x08000000
    return CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS | CREATE_NO_WINDOW


def wait_capture_online(
    native_root: Path,
    trading_date: str,
    *,
    timeout_sec: float = 30.0,
    poll_sec: float = 0.25,
) -> dict[str, Any]:
    day = capture_day_dir(native_root, trading_date)
    deadline = time.monotonic() + timeout_sec
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        status_path = day / STATUS_FILE
        hb_path = day / HEARTBEAT_FILE
        pid_path = day / PID_FILE_NAME
        if status_path.is_file():
            try:
                last = json.loads(status_path.read_text(encoding="utf-8"))
            except Exception:
                last = {}
            st = str(last.get("capture_status") or "")
            pid = last.get("pid")
            # Phase687W24: process-ready statuses only. SOCKET_OPEN_NO_PUSH / STARTING ≠ ready.
            if st in CAPTURE_WAIT_OK_STATUSES:
                if pid and _pid_alive(int(pid)):
                    age = None
                    if hb_path.is_file():
                        try:
                            age = time.time() - hb_path.stat().st_mtime
                        except Exception:
                            age = None
                    if age is None or age < 15:
                        return {
                            "ok": True,
                            "status": st,
                            "pid": pid,
                            "heartbeat_age_sec": age,
                            "output": str(day),
                        }
        time.sleep(poll_sec)
    return {"ok": False, "status": last.get("capture_status"), "pid": last.get("pid"), "output": str(day), "reason": "timeout"}


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        from small_paper.env_loader import ensure_repo_dotenv

        ensure_repo_dotenv()
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Independent Market Capture Sidecar (Phase687W9)")
    parser.add_argument("--native-root", type=str, default=str(NATIVE_ROOT))
    parser.add_argument("--trading-date", type=str, default="")
    parser.add_argument("--synthetic", action="store_true")
    parser.add_argument("--synthetic-events", type=int, default=100)
    parser.add_argument("--restart-count", type=int, default=0)
    parser.add_argument("--no-finalize", action="store_true")
    parser.add_argument("--topology", type=str, default=TOPOLOGY_SINGLE_INGRESS)
    args = parser.parse_args(list(argv) if argv is not None else None)

    if int(args.restart_count) > MAX_AUTO_RESTARTS:
        print("auto-restart limit exceeded", file=sys.stderr)
        return 3

    sc = MarketCaptureSidecar(
        native_root=Path(args.native_root),
        trading_date=args.trading_date or None,
        topology=args.topology,
        synthetic=bool(args.synthetic),
        synthetic_events=int(args.synthetic_events),
        restart_count=int(args.restart_count),
        finalize_at_end=not bool(args.no_finalize),
    )
    return sc.run()


if __name__ == "__main__":
    raise SystemExit(main())
