"""Session / scheduler clock (domain B).

Production default: JST wall clock.
Certification: env-injected accelerated session clock shared by all processes
via inherited env (same V0 / REAL_T0 / SPEED). Does not patch time.monotonic
or time.perf_counter (domain C). Market/causal event-time (domain A) stays on
Ingress received_at / payload timestamps.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

ENV_ENABLED = "TRADEBOT_SESSION_CLOCK"
ENV_V0 = "TRADEBOT_SESSION_CLOCK_V0"
ENV_T0 = "TRADEBOT_SESSION_CLOCK_REAL_T0"
ENV_SPEED = "TRADEBOT_SESSION_CLOCK_SPEED"
ENV_STOP = "TRADEBOT_SESSION_CLOCK_STOP"
ENV_ARM_FILE = "TRADEBOT_SESSION_CLOCK_ARM_FILE"
ENV_REPLAY_PATH = "TRADEBOT_INGRESS_REPLAY_PATH"
ENV_REPLAY_NOT_BEFORE = "TRADEBOT_INGRESS_REPLAY_NOT_BEFORE"
ENV_REPLAY_EPS = "TRADEBOT_INGRESS_REPLAY_MAX_EPS"
ENV_REPLAY_MAX_LAG = "TRADEBOT_INGRESS_REPLAY_MAX_LAG"
ENV_REPLAY_CLOCK_LEAD = "TRADEBOT_REPLAY_CLOCK_LEAD_SEC"
ENV_CERT_MODE = "TRADEBOT_CERTIFICATION_MODE"
ENV_CONSUMER_DELAY = "TRADEBOT_CERT_CONSUMER_EXTRA_DELAY_SEC"
ENV_SKIP_CERT_GATE = "TRADEBOT_SKIP_CERT_GATE"
ENV_MARKET_INPUT_MODE = "MARKET_INPUT_MODE"
ENV_KABU_AUTH_MODE = "KABU_AUTH_MODE"
ENV_TOKEN_PREFLIGHT = "KABU_TOKEN_PREFLIGHT"
ENV_CERT_PROBE = "KABU_CERTIFICATION_PROBE"

MARKET_INPUT_LIVE = "LIVE"
MARKET_INPUT_REPLAY = "REPLAY"
MARKET_INPUT_SYNTHETIC = "SYNTHETIC"
KABU_AUTH_LIVE = "LIVE"
KABU_AUTH_SHARED = "SHARED"
KABU_AUTH_NONE = "NONE"

# Certification-only keys that must not leak into research/preflight helpers.
CERTIFICATION_STRIP_KEYS: tuple[str, ...] = (
    ENV_ENABLED,
    ENV_V0,
    ENV_T0,
    ENV_SPEED,
    ENV_STOP,
    ENV_ARM_FILE,
    ENV_REPLAY_PATH,
    ENV_REPLAY_NOT_BEFORE,
    ENV_REPLAY_EPS,
    ENV_REPLAY_MAX_LAG,
    ENV_REPLAY_CLOCK_LEAD,
    ENV_CERT_MODE,
    ENV_CONSUMER_DELAY,
    ENV_SKIP_CERT_GATE,
)

_TRUE = frozenset({"1", "true", "yes", "on"})
DEFAULT_REPLAY_MAX_PUBLISH_LAG = 128
DEFAULT_REPLAY_CLOCK_LEAD_SEC = 2.0
REANCHOR_MIN_SKEW_SEC = 1.0
ARM_WRITE_RETRIES = 8
_ARM_DOC_CACHE: Optional[tuple[str, int, dict[str, Any]]] = None
_LAST_REANCHOR_VIRT: Optional[datetime] = None
_LAST_WATERMARK_WRITE_MONO: float = 0.0


def _clear_t0_file_cache() -> None:
    global _ARM_DOC_CACHE, _LAST_REANCHOR_VIRT, _LAST_WATERMARK_WRITE_MONO
    _ARM_DOC_CACHE = None
    _LAST_REANCHOR_VIRT = None
    _LAST_WATERMARK_WRITE_MONO = 0.0


def _parse_iso_dt(raw: Any) -> Optional[datetime]:
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JST)
    return dt.astimezone(JST)


def _read_arm_doc(path: str) -> dict[str, Any]:
    global _ARM_DOC_CACHE
    p = Path(path)
    if not p.is_file():
        cached = _ARM_DOC_CACHE
        if cached is not None and cached[0] == path:
            return dict(cached[2])
        return {}
    try:
        mtime = int(p.stat().st_mtime_ns)
        cached = _ARM_DOC_CACHE
        if cached is not None and cached[0] == path and cached[1] == mtime:
            return dict(cached[2])
        body = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(body, dict):
            body = {}
        _ARM_DOC_CACHE = (path, mtime, body)
        return dict(body)
    except Exception:
        cached = _ARM_DOC_CACHE
        if cached is not None and cached[0] == path:
            return dict(cached[2])
        return {}


def _arm_spec_from_env(*, environ: Optional[Mapping[str, str]] = None) -> dict[str, Any]:
    env = environ if environ is not None else os.environ
    return {
        "v0": str(env.get(ENV_V0) or "").strip(),
        "speed": str(env.get(ENV_SPEED) or "").strip(),
        "stop": str(env.get(ENV_STOP) or "").strip(),
        "certification_run_id": str(env.get("TRADEBOT_CERTIFICATION_RUN_ID") or "").strip(),
        "stage_run_id": str(env.get("TRADEBOT_CERT_STAGE_RUN_ID") or "").strip(),
    }


def _arm_spec_matches_env(doc: Mapping[str, Any], *, environ: Optional[Mapping[str, str]] = None) -> bool:
    spec = _arm_spec_from_env(environ=environ)
    want_v0 = spec["v0"]
    if not want_v0:
        return True
    got_v0 = str(doc.get("v0") or "").strip()
    if not got_v0 or got_v0 != want_v0:
        return False
    if spec["stop"] and str(doc.get("stop") or "").strip() != spec["stop"]:
        return False
    if spec["speed"] and str(doc.get("speed") or "").strip() != spec["speed"]:
        return False
    return True


def _replace_write_arm_doc(path: str, body: Mapping[str, Any]) -> None:
    """Overwrite ARM JSON (does not merge previous-stage watermarks)."""
    global _ARM_DOC_CACHE
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(dict(body), ensure_ascii=False) + "\n"
    last_exc: Optional[BaseException] = None
    for _ in range(ARM_WRITE_RETRIES):
        tmp = ""
        try:
            fd, tmp = tempfile.mkstemp(prefix="session_clock_arm_", suffix=".json", dir=str(p.parent))
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, p)
            try:
                mtime = int(p.stat().st_mtime_ns)
            except OSError:
                mtime = 0
            _ARM_DOC_CACHE = (path, mtime, dict(body))
            return
        except OSError as exc:
            last_exc = exc
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            time.sleep(0.015)
        except Exception as exc:
            last_exc = exc
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            time.sleep(0.015)
    _ = last_exc


def reset_stage_mutable_clock_state(*, environ: Optional[dict[str, str]] = None) -> dict[str, Any]:
    """Drop previous-stage ARM/T0/watermark/EOF. Call on every new clock bind.

    Mutable replay/clock state is derived only from the current V0/STOP/speed
    (and current cert/stage ids when present). Not a PM_DIRECT special-case.
    """
    env = environ if environ is not None else os.environ
    env.pop(ENV_T0, None)
    os.environ.pop(ENV_T0, None)
    _clear_t0_file_cache()
    path = str(env.get(ENV_ARM_FILE) or os.environ.get(ENV_ARM_FILE) or "").strip()
    spec = _arm_spec_from_env(environ=env)
    spec["replay_eof"] = False
    if path:
        try:
            _replace_write_arm_doc(path, spec)
        except Exception:
            try:
                Path(path).unlink(missing_ok=True)
            except Exception:
                pass
    return spec


def _merge_write_arm_doc(path: str, updates: Mapping[str, Any]) -> None:
    """Merge ARM JSON. PermissionError/WinError 5 is retried then swallowed.

    Replay must not die because Paper and Ingress share this file.
    """
    global _ARM_DOC_CACHE
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    patch = {k: v for k, v in dict(updates).items() if v is not None}
    if not patch:
        return
    last_exc: Optional[BaseException] = None
    for _ in range(ARM_WRITE_RETRIES):
        tmp = ""
        try:
            current = _read_arm_doc(path)
            if p.is_file():
                try:
                    raw = json.loads(p.read_text(encoding="utf-8"))
                    if isinstance(raw, dict):
                        current = raw
                except Exception:
                    pass
            if not _arm_spec_matches_env(current):
                current = _arm_spec_from_env()
            current.update(patch)
            payload = json.dumps(current, ensure_ascii=False) + "\n"
            fd, tmp = tempfile.mkstemp(prefix="session_clock_arm_", suffix=".json", dir=str(p.parent))
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, p)
            try:
                mtime = int(p.stat().st_mtime_ns)
            except OSError:
                mtime = 0
            _ARM_DOC_CACHE = (path, mtime, dict(current))
            return
        except OSError as exc:
            last_exc = exc
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            time.sleep(0.015)
        except Exception as exc:
            last_exc = exc
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            time.sleep(0.015)
    _ = last_exc


def _atomic_write_arm_file(path: str, t0: str, v0: str) -> None:
    _merge_write_arm_doc(path, {"t0": t0, "v0": v0})


def _flag(name: str, *, environ: Optional[dict[str, str]] = None) -> bool:
    env = environ if environ is not None else os.environ
    return str(env.get(name, "") or "").strip().lower() in _TRUE


def session_clock_enabled(*, environ: Optional[dict[str, str]] = None) -> bool:
    return _flag(ENV_ENABLED, environ=environ)


def certification_mode(*, environ: Optional[dict[str, str]] = None) -> bool:
    return _flag(ENV_CERT_MODE, environ=environ)


def skip_cert_gate(*, environ: Optional[dict[str, str]] = None) -> bool:
    return _flag(ENV_SKIP_CERT_GATE, environ=environ)


def market_input_mode(*, environ: Optional[dict[str, str]] = None) -> str:
    """LIVE | REPLAY | SYNTHETIC. Independent of Kabu token issuance."""
    env = environ if environ is not None else os.environ
    raw = str(env.get(ENV_MARKET_INPUT_MODE, "") or "").strip().upper()
    if raw in {MARKET_INPUT_LIVE, MARKET_INPUT_REPLAY, MARKET_INPUT_SYNTHETIC}:
        return raw
    if ingress_replay_path(environ=env):
        return MARKET_INPUT_REPLAY
    return MARKET_INPUT_LIVE


def kabu_auth_mode(*, environ: Optional[dict[str, str]] = None) -> str:
    """LIVE | SHARED | NONE. Replay path must not imply LIVE token POST."""
    env = environ if environ is not None else os.environ
    if _flag(ENV_TOKEN_PREFLIGHT, environ=env) or _flag(ENV_CERT_PROBE, environ=env):
        return KABU_AUTH_NONE
    raw = str(env.get(ENV_KABU_AUTH_MODE, "") or "").strip().upper()
    if raw in {KABU_AUTH_LIVE, KABU_AUTH_SHARED, KABU_AUTH_NONE}:
        return raw
    return KABU_AUTH_LIVE


def apply_non_issuer_env(environ: Optional[dict[str, str]] = None) -> dict[str, str]:
    """Strip certification clock/replay env and force consumer-only Kabu auth.

    Use for research/preflight helpers that must not inherit TRADEBOT_* and
    must never POST /token.
    """
    env = environ if environ is not None else os.environ
    for key in CERTIFICATION_STRIP_KEYS:
        env.pop(key, None)
    env[ENV_KABU_AUTH_MODE] = KABU_AUTH_NONE
    env[ENV_MARKET_INPUT_MODE] = MARKET_INPUT_SYNTHETIC
    env[ENV_TOKEN_PREFLIGHT] = "1"
    return env


def official_cert_child_env(source: Optional[dict[str, str]] = None) -> dict[str, str]:
    """Env for the official checked-BAT certification graph (Ingress may issue)."""
    env = dict(source if source is not None else os.environ)
    env[ENV_KABU_AUTH_MODE] = KABU_AUTH_LIVE
    if env.get(ENV_REPLAY_PATH):
        env[ENV_MARKET_INPUT_MODE] = MARKET_INPUT_REPLAY
    env.pop(ENV_TOKEN_PREFLIGHT, None)
    env.pop(ENV_CERT_PROBE, None)
    if session_clock_enabled(environ=env) and not str(env.get("TRADEBOT_TRADING_DATE") or "").strip():
        v0 = str(env.get(ENV_V0) or "").strip()
        if v0:
            try:
                dt = datetime.fromisoformat(v0)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=JST)
                env["TRADEBOT_TRADING_DATE"] = dt.astimezone(JST).strftime("%Y%m%d")
            except Exception:
                pass
    return env


def ingress_replay_path(*, environ: Optional[dict[str, str]] = None) -> str:
    env = environ if environ is not None else os.environ
    return str(env.get(ENV_REPLAY_PATH, "") or "").strip()


def replay_not_before_hhmm(*, environ: Optional[dict[str, str]] = None) -> str:
    env = environ if environ is not None else os.environ
    return str(env.get(ENV_REPLAY_NOT_BEFORE, "") or "").strip()


def replay_max_eps(*, environ: Optional[dict[str, str]] = None) -> float:
    env = environ if environ is not None else os.environ
    try:
        return max(1.0, float(env.get(ENV_REPLAY_EPS) or "200"))
    except (TypeError, ValueError):
        return 200.0


def replay_max_publish_lag(*, environ: Optional[dict[str, str]] = None) -> int:
    """Max publisher-ack lag while cert replay may run ahead of Paper.

    Live WS receive is not blocked (local_market_bus). Replay input may wait.
    """
    env = environ if environ is not None else os.environ
    try:
        return max(1, int(float(env.get(ENV_REPLAY_MAX_LAG) or DEFAULT_REPLAY_MAX_PUBLISH_LAG)))
    except (TypeError, ValueError):
        return DEFAULT_REPLAY_MAX_PUBLISH_LAG


def replay_clock_lead_sec(*, environ: Optional[dict[str, str]] = None) -> float:
    env = environ if environ is not None else os.environ
    try:
        return max(0.0, float(env.get(ENV_REPLAY_CLOCK_LEAD) or DEFAULT_REPLAY_CLOCK_LEAD_SEC))
    except (TypeError, ValueError):
        return DEFAULT_REPLAY_CLOCK_LEAD_SEC


def replay_clock_bind_enabled(*, environ: Optional[dict[str, str]] = None) -> bool:
    """Certification replay tape is present: session now is bound to watermarks."""
    env = environ if environ is not None else os.environ
    return bool(ingress_replay_path(environ=env))


def record_replay_progress(
    *,
    source_event_time: Optional[datetime] = None,
    replay_read_watermark: Optional[datetime] = None,
    ingress_publish_watermark: Optional[datetime] = None,
    consumer_ack_watermark: Optional[datetime] = None,
    paper_last_processed_event_time: Optional[datetime] = None,
    replay_eof: Optional[bool] = None,
    force: bool = False,
    environ: Optional[dict[str, str]] = None,
) -> None:
    """Persist replay watermarks into ARM_FILE. Debounced. Never raises."""
    global _LAST_WATERMARK_WRITE_MONO
    env = environ if environ is not None else os.environ
    path = str(env.get(ENV_ARM_FILE) or os.environ.get(ENV_ARM_FILE) or "").strip()
    if not path:
        return
    now_mono = time.monotonic()
    if not force and replay_eof is not True and (now_mono - _LAST_WATERMARK_WRITE_MONO) < 0.2:
        return
    updates: dict[str, Any] = {}

    def _iso(dt: Optional[datetime]) -> Optional[str]:
        if dt is None:
            return None
        v = dt if dt.tzinfo is not None else dt.replace(tzinfo=JST)
        return v.astimezone(JST).isoformat(timespec="milliseconds")

    if source_event_time is not None:
        updates["source_event_time"] = _iso(source_event_time)
    if replay_read_watermark is not None:
        updates["replay_read_watermark"] = _iso(replay_read_watermark)
    if ingress_publish_watermark is not None:
        updates["ingress_publish_watermark"] = _iso(ingress_publish_watermark)
    if consumer_ack_watermark is not None:
        updates["consumer_ack_watermark"] = _iso(consumer_ack_watermark)
    if paper_last_processed_event_time is not None:
        updates["paper_last_processed_event_time"] = _iso(paper_last_processed_event_time)
    if replay_eof is not None:
        updates["replay_eof"] = bool(replay_eof)
    stop = session_stop(environ=env)
    if stop is not None:
        updates["session_stop"] = stop.isoformat(timespec="milliseconds")
    if not updates:
        return
    try:
        _merge_write_arm_doc(path, updates)
        _LAST_WATERMARK_WRITE_MONO = now_mono
    except Exception:
        return


def load_replay_watermarks(*, environ: Optional[dict[str, str]] = None) -> dict[str, Any]:
    env = environ if environ is not None else os.environ
    path = str(env.get(ENV_ARM_FILE) or os.environ.get(ENV_ARM_FILE) or "").strip()
    if not path:
        return {}
    doc = _read_arm_doc(path)
    return {
        "source_event_time": doc.get("source_event_time"),
        "replay_read_watermark": doc.get("replay_read_watermark"),
        "ingress_publish_watermark": doc.get("ingress_publish_watermark"),
        "consumer_ack_watermark": doc.get("consumer_ack_watermark"),
        "paper_last_processed_event_time": doc.get("paper_last_processed_event_time"),
        "session_now": None,
        "session_stop": doc.get("session_stop"),
        "replay_eof": bool(doc.get("replay_eof")),
    }


def _clock_cap_dt(*, environ: Optional[dict[str, str]] = None) -> Optional[datetime]:
    env = environ if environ is not None else os.environ
    path = str(env.get(ENV_ARM_FILE) or os.environ.get(ENV_ARM_FILE) or "").strip()
    if not path:
        return None
    doc = _read_arm_doc(path)
    pub = _parse_iso_dt(doc.get("ingress_publish_watermark"))
    cons = _parse_iso_dt(doc.get("consumer_ack_watermark"))
    read = _parse_iso_dt(doc.get("replay_read_watermark"))
    src = _parse_iso_dt(doc.get("source_event_time"))
    if pub and cons:
        return min(pub, cons)
    return pub or cons or read or src


def consumer_extra_delay_sec(*, environ: Optional[dict[str, str]] = None) -> float:
    env = environ if environ is not None else os.environ
    try:
        return max(0.0, float(env.get(ENV_CONSUMER_DELAY) or "0"))
    except (TypeError, ValueError):
        return 0.0


def speed(*, environ: Optional[dict[str, str]] = None) -> float:
    env = environ if environ is not None else os.environ
    if not session_clock_enabled(environ=env):
        return 1.0
    try:
        return max(0.001, float(env.get(ENV_SPEED) or "1"))
    except (TypeError, ValueError):
        return 1.0


def bind_session_clock(
    *,
    virtual_start: datetime,
    speed_mult: float = 1.0,
    stop: Optional[datetime] = None,
    environ: Optional[dict[str, str]] = None,
    arm_now: bool = True,
    arm_file: Optional[Path] = None,
) -> dict[str, str]:
    """Write session-clock env into os.environ (and optional dict). Inherited by children.

    arm_now=False parks virtual time at V0 until arm_session_clock() (launcher
    start). Ingress reads T0 from ENV_ARM_FILE so a sibling process can sync.
    """
    env = environ if environ is not None else os.environ
    v0 = virtual_start
    if v0.tzinfo is None:
        v0 = v0.replace(tzinfo=JST)
    else:
        v0 = v0.astimezone(JST)
    env[ENV_ENABLED] = "1"
    env[ENV_V0] = v0.isoformat(timespec="milliseconds")
    env[ENV_SPEED] = str(float(speed_mult))
    if arm_file is not None:
        env[ENV_ARM_FILE] = str(arm_file)
    if stop is not None:
        s = stop if stop.tzinfo is not None else stop.replace(tzinfo=JST)
        env[ENV_STOP] = s.astimezone(JST).isoformat(timespec="milliseconds")
    elif ENV_STOP in env:
        env.pop(ENV_STOP, None)
    reset_stage_mutable_clock_state(environ=env)
    if arm_now:
        arm_session_clock(environ=env)
    return {
        ENV_ENABLED: env[ENV_ENABLED],
        ENV_V0: env[ENV_V0],
        ENV_T0: str(env.get(ENV_T0) or ""),
        ENV_SPEED: env[ENV_SPEED],
        ENV_STOP: str(env.get(ENV_STOP) or ""),
        ENV_ARM_FILE: str(env.get(ENV_ARM_FILE) or ""),
    }


def arm_session_clock(*, environ: Optional[dict[str, str]] = None) -> str:
    """Start virtual elapsed time at current wall T0. Safe to call twice (first wins)."""
    env = environ if environ is not None else os.environ
    existing = _t0_value(environ=env)
    if existing is not None:
        t0 = f"{existing:.6f}"
        env[ENV_T0] = t0
        os.environ[ENV_T0] = t0
        return t0
    t0 = f"{time.time():.6f}"
    env[ENV_T0] = t0
    os.environ[ENV_T0] = t0
    path = str(env.get(ENV_ARM_FILE) or os.environ.get(ENV_ARM_FILE) or "")
    if path:
        _atomic_write_arm_file(
            path, t0, str(env.get(ENV_V0) or os.environ.get(ENV_V0) or "")
        )
    _clear_t0_file_cache()
    return t0


def reanchor_session_clock(
    virtual_now: datetime,
    *,
    environ: Optional[dict[str, str]] = None,
    min_skew_sec: float = REANCHOR_MIN_SKEW_SEC,
) -> Optional[str]:
    """Move REAL_T0 so projected now equals virtual_now (cert replay backpressure).

    Used when replay tape is behind the wall*speed projection so Paper warmup
    / session close cannot run hours of domain-B time while still chewing
    08:50 payloads. Does not freeze now_jst() at STOP. ARM write failures
    are swallowed so Ingress replay cannot die on WinError 5.
    """
    global _LAST_REANCHOR_VIRT
    env = environ if environ is not None else os.environ
    if not session_clock_enabled(environ=env):
        return None
    raw_v0 = str(env.get(ENV_V0) or "").strip()
    if not raw_v0:
        return None
    v0 = datetime.fromisoformat(raw_v0)
    if v0.tzinfo is None:
        v0 = v0.replace(tzinfo=JST)
    else:
        v0 = v0.astimezone(JST)
    vn = virtual_now
    if vn.tzinfo is None:
        vn = vn.replace(tzinfo=JST)
    else:
        vn = vn.astimezone(JST)
    if vn < v0:
        vn = v0
    stop = session_stop(environ=env)
    if stop is not None and vn > stop:
        vn = stop
    prev = _LAST_REANCHOR_VIRT
    if prev is not None and abs((vn - prev).total_seconds()) < float(min_skew_sec):
        return str(env.get(ENV_T0) or "") or None
    sp = speed(environ=env)
    elapsed_virt = max(0.0, (vn - v0).total_seconds())
    t0 = time.time() - (elapsed_virt / sp)
    t0s = f"{t0:.6f}"
    env[ENV_T0] = t0s
    os.environ[ENV_T0] = t0s
    path = str(env.get(ENV_ARM_FILE) or os.environ.get(ENV_ARM_FILE) or "")
    if path:
        try:
            _atomic_write_arm_file(
                path, t0s, str(env.get(ENV_V0) or os.environ.get(ENV_V0) or "")
            )
        except Exception:
            pass
    _LAST_REANCHOR_VIRT = vn
    return t0s


def _t0_from_arm_file(path: str, *, environ: Optional[Mapping[str, str]] = None) -> Optional[float]:
    doc = _read_arm_doc(path)
    if not _arm_spec_matches_env(doc, environ=environ):
        return None
    raw = doc.get("t0")
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _t0_value(*, environ: Optional[dict[str, str]] = None) -> Optional[float]:
    """Shared T0. ARM_FILE is SoT so Ingress reanchor is visible to Paper.

    When ARM_FILE is configured, never fall back to process ENV_T0 — a torn
    read plus stale ENV_T0 would let 48x wall time skip the session.
    """
    env = environ if environ is not None else os.environ
    path = str(env.get(ENV_ARM_FILE) or os.environ.get(ENV_ARM_FILE) or "").strip()
    if path:
        return _t0_from_arm_file(path, environ=env)
    raw = str(env.get(ENV_T0) or "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            return None
    return None


def session_clock_armed(*, environ: Optional[dict[str, str]] = None) -> bool:
    if not session_clock_enabled(environ=environ):
        return True
    return _t0_value(environ=environ) is not None


def _projected_session_now(*, environ: Optional[dict[str, str]] = None) -> datetime:
    """Uncapped wall*speed projection. Ingress pacing / AM→PM wait use this."""
    env = environ if environ is not None else os.environ
    if not session_clock_enabled(environ=env):
        return datetime.now(JST)
    raw_v0 = str(env.get(ENV_V0) or "").strip()
    if not raw_v0:
        return datetime.now(JST)
    v0 = datetime.fromisoformat(raw_v0)
    if v0.tzinfo is None:
        v0 = v0.replace(tzinfo=JST)
    else:
        v0 = v0.astimezone(JST)
    t0 = _t0_value(environ=env)
    if t0 is None:
        return v0
    elapsed = max(0.0, time.time() - t0)
    return v0 + timedelta(seconds=elapsed * speed(environ=env))


def projected_session_now(*, environ: Optional[dict[str, str]] = None) -> datetime:
    return _projected_session_now(environ=environ)


def now_jst(*, environ: Optional[dict[str, str]] = None) -> datetime:
    """Domain B session/scheduler now. Production = wall JST.

    Certification replay: causally capped to replay watermarks so STOP /
    morning_session_close cannot fire while the consumer is still on warmup
    tape. Does not freeze at STOP.
    """
    env = environ if environ is not None else os.environ
    if not session_clock_enabled(environ=env):
        return datetime.now(JST)
    projected = _projected_session_now(environ=env)
    if not replay_clock_bind_enabled(environ=env):
        return projected
    raw_v0 = str(env.get(ENV_V0) or "").strip()
    v0 = datetime.now(JST)
    if raw_v0:
        try:
            v0 = datetime.fromisoformat(raw_v0)
            if v0.tzinfo is None:
                v0 = v0.replace(tzinfo=JST)
            else:
                v0 = v0.astimezone(JST)
        except Exception:
            pass
    cap = _clock_cap_dt(environ=env)
    if cap is None:
        return v0
    lead = replay_clock_lead_sec(environ=env)
    capped = cap + timedelta(seconds=lead)
    if projected > capped:
        return capped
    return projected


def iso(*, timespec: str = "milliseconds") -> str:
    return now_jst().isoformat(timespec=timespec)


def trading_date() -> str:
    return now_jst().strftime("%Y%m%d")


def session_stop(*, environ: Optional[dict[str, str]] = None) -> Optional[datetime]:
    env = environ if environ is not None else os.environ
    raw = str(env.get(ENV_STOP) or "").strip()
    if not raw:
        return None
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JST)
    return dt.astimezone(JST)


def session_clock_v0(*, environ: Optional[dict[str, str]] = None) -> Optional[datetime]:
    env = environ if environ is not None else os.environ
    raw = str(env.get(ENV_V0) or "").strip()
    if not raw:
        return None
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JST)
    return dt.astimezone(JST)


def session_clock_window(
    *, environ: Optional[dict[str, str]] = None
) -> tuple[Optional[datetime], Optional[datetime]]:
    return session_clock_v0(environ=environ), session_stop(environ=environ)


def session_clock_stop_reached(*, environ: Optional[dict[str, str]] = None) -> bool:
    """True when domain-B now has reached TRADEBOT_SESSION_CLOCK_STOP.

    Certification replay also requires the replay watermark to have reached
    STOP (or EOF). Does not freeze now_jst() at STOP.
    """
    env = environ if environ is not None else os.environ
    if not session_clock_enabled(environ=env):
        return False
    stop = session_stop(environ=env)
    if stop is None:
        return False
    if replay_clock_bind_enabled(environ=env):
        path = str(env.get(ENV_ARM_FILE) or os.environ.get(ENV_ARM_FILE) or "").strip()
        eof = False
        if path:
            eof = bool(_read_arm_doc(path).get("replay_eof"))
        cap = _clock_cap_dt(environ=env)
        if cap is None and not eof:
            return False
        if now_jst(environ=env) < stop:
            return False
        if eof:
            return True
        return cap is not None and cap >= stop
    return now_jst(environ=env) >= stop


def ensure_session_clock_armed(*, environ: Optional[dict[str, str]] = None) -> Optional[str]:
    """Arm parked certification clock. First call wins; no-op if already armed or disabled."""
    if not session_clock_enabled(environ=environ):
        return None
    return arm_session_clock(environ=environ)


def scheduled_end_passed(
    trading_date: str,
    *,
    finalize_hour: int = 15,
    finalize_minute: int = 35,
    environ: Optional[dict[str, str]] = None,
) -> bool:
    y, m, d = int(trading_date[:4]), int(trading_date[4:6]), int(trading_date[6:8])
    end = datetime(y, m, d, finalize_hour, finalize_minute, tzinfo=JST)
    extra = session_stop(environ=environ)
    if extra is not None and extra < end:
        end = extra
    return now_jst(environ=environ) >= end


def sleep_until(
    target: datetime,
    *,
    sleep_fn: Callable[[float], None] = time.sleep,
    poll_sec: float = 1.0,
) -> None:
    """Sleep real time until session now >= target (scales with SPEED)."""
    tgt = target if target.tzinfo is not None else target.replace(tzinfo=JST)
    tgt = tgt.astimezone(JST)
    sp = speed()
    poll = 0.05 if session_clock_enabled() and sp > 1.01 else float(poll_sec)
    while projected_session_now() < tgt:
        remaining_virt = (tgt - projected_session_now()).total_seconds()
        real = remaining_virt / sp
        sleep_fn(min(poll, max(0.01, real)))
