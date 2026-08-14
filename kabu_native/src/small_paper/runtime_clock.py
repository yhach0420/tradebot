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
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional
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
    ENV_CERT_MODE,
    ENV_CONSUMER_DELAY,
    ENV_SKIP_CERT_GATE,
)

_TRUE = frozenset({"1", "true", "yes", "on"})


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
    if arm_now:
        arm_session_clock(environ=env)
    else:
        env.pop(ENV_T0, None)
        os.environ.pop(ENV_T0, None)
        path = str(env.get(ENV_ARM_FILE) or "")
        if path:
            try:
                Path(path).unlink(missing_ok=True)
            except Exception:
                pass
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
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps({"t0": t0, "v0": env.get(ENV_V0) or os.environ.get(ENV_V0) or ""}) + "\n",
            encoding="utf-8",
        )
    return t0


def _t0_value(*, environ: Optional[dict[str, str]] = None) -> Optional[float]:
    env = environ if environ is not None else os.environ
    raw = str(env.get(ENV_T0) or "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            return None
    path = str(env.get(ENV_ARM_FILE) or os.environ.get(ENV_ARM_FILE) or "").strip()
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    try:
        body = json.loads(p.read_text(encoding="utf-8"))
        return float(body.get("t0"))
    except Exception:
        return None


def session_clock_armed(*, environ: Optional[dict[str, str]] = None) -> bool:
    if not session_clock_enabled(environ=environ):
        return True
    return _t0_value(environ=environ) is not None


def now_jst(*, environ: Optional[dict[str, str]] = None) -> datetime:
    """Domain B session/scheduler now. Production = wall JST."""
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
    while now_jst() < tgt:
        remaining_virt = (tgt - now_jst()).total_seconds()
        real = remaining_virt / sp
        sleep_fn(min(poll, max(0.01, real)))
