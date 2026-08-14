"""Station-global Kabu API token authority.

MARKET_INGRESS_SERVICE is the only process that may POST /token to a given
Kabu Station endpoint. Enforcement is a Windows named mutex + exclusive file
lock (not TLS / JSON existence). Child components reuse the published token.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from small_paper.runtime_clock import (
    ENV_CERT_PROBE,
    ENV_TOKEN_PREFLIGHT,
    KABU_AUTH_LIVE,
    KABU_AUTH_NONE,
    MARKET_INPUT_SYNTHETIC,
    kabu_auth_mode,
    market_input_mode,
    now_jst as session_now,
)

JST = ZoneInfo("Asia/Tokyo")

OWNER_INGRESS = "MARKET_INGRESS_SERVICE"
AUTHORITY_JSON = "kabu_token_authority.json"
TOKEN_FILE = ".kabu_session_token"
BUNDLE_JSON = "kabu_station_token_bundle.json"
STATION_OWNER_JSON = "kabu_station_owner.json"
AUDIT_JSONL = "token_issue_audit.jsonl"
ISSUE_LOCK = "issue.lock"
ENV_AUTHORITY_DIR = "KABU_TOKEN_AUTHORITY_DIR"
ENV_STATION_AUTHORITY_DIR = "KABU_STATION_AUTHORITY_DIR"

AUTH_INVALID = "AUTH_INVALID"
RATE_LIMIT = "RATE_LIMIT"
OTHER = "OTHER"
BLOCKED_REASON = "TOKEN_SECOND_ISSUER_BLOCKED"

REGISTER_BACKOFF_SEC = (1.0, 2.0, 4.0, 8.0, 16.0, 30.0)

WAIT_OBJECT_0 = 0
WAIT_ABANDONED = 128
WAIT_TIMEOUT = 258
INFINITE = 0xFFFFFFFF


class ChildTokenIssueBlocked(RuntimeError):
    """Non-owner attempted POST /token while Ingress owns the live session."""


class TokenSecondIssuerBlocked(ChildTokenIssueBlocked):
    """Second process attempted POST /token; blocked before Station generation advanced."""


class TokenUnavailable(ChildTokenIssueBlocked):
    """Consumer requested a token but Ingress has not published one."""


_tls = threading.local()


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="milliseconds")


def _pid_alive(pid: int) -> bool:
    n = int(pid or 0)
    if n <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, n)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return False
    try:
        os.kill(n, 0)
        return True
    except OSError:
        return False


def classify_kabu_api_error(message: Any = "", http_status: Any = None) -> str:
    msg = str(message or "")
    http = None
    try:
        if http_status is not None:
            http = int(http_status)
    except (TypeError, ValueError):
        http = None
    if http == 401 or "4001009" in msg or "APIキー不一致" in msg or "HTTP 401" in msg:
        return AUTH_INVALID
    if http == 429 or "4001006" in msg or "API実行回数" in msg or "HTTP 429" in msg:
        return RATE_LIMIT
    low = msg.lower()
    if "get_http_401" in low or "unauthorized" in low:
        return AUTH_INVALID
    if "get_http_429" in low or "too many" in low:
        return RATE_LIMIT
    return OTHER


def native_root_default() -> Path:
    return Path(__file__).resolve().parents[2]


def token_fingerprint(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()[:16]


def station_endpoint_id(base_url: Optional[str] = None) -> str:
    raw = str(base_url or "").strip() or _default_base_url()
    parsed = urlparse(raw if "://" in raw else f"http://{raw}")
    host = (parsed.hostname or "localhost").lower()
    if host in {"127.0.0.1", "::1"}:
        host = "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not parsed.port and "18080" in raw:
        port = 18080
    return f"{host}_{port}"


def _default_base_url() -> str:
    try:
        from api.rest_client import default_base_url

        return str(default_base_url() or "http://localhost:18080/kabusapi")
    except Exception:
        return str(os.environ.get("KABU_API_BASE") or "http://localhost:18080/kabusapi")


def station_authority_dir(base_url: Optional[str] = None) -> Path:
    env = str(os.environ.get(ENV_STATION_AUTHORITY_DIR) or "").strip()
    if env:
        return Path(env)
    eid = station_endpoint_id(base_url)
    local = os.environ.get("LOCALAPPDATA") or os.environ.get("HOME") or str(Path.home())
    return Path(local) / "tradebot" / "kabu_station_token_authority" / eid


def authority_day_dir(
    native_root: Optional[Path] = None,
    trading_date: Optional[str] = None,
) -> Path:
    env = str(os.environ.get(ENV_AUTHORITY_DIR) or "").strip()
    if env:
        return Path(env)
    root = Path(native_root) if native_root else native_root_default()
    day = str(trading_date or session_now().strftime("%Y%m%d"))
    return root / "data" / "market_capture" / day


def _authority_path(day_dir: Path) -> Path:
    return day_dir / AUTHORITY_JSON


def _token_path(day_dir: Path) -> Path:
    return day_dir / TOKEN_FILE


def load_authority(
    native_root: Optional[Path] = None,
    trading_date: Optional[str] = None,
) -> dict[str, Any]:
    path = _authority_path(authority_day_dir(native_root, trading_date))
    if not path.is_file():
        return {}
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return body if isinstance(body, dict) else {}


def _write_authority(day_dir: Path, body: dict[str, Any]) -> None:
    day_dir.mkdir(parents=True, exist_ok=True)
    safe = {k: v for k, v in body.items() if k not in ("token", "Token", "api_password")}
    _atomic_write_json(_authority_path(day_dir), safe)


def _atomic_write_json(path: Path, body: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(body, ensure_ascii=False, indent=2, default=str) + "\n"
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def empty_authority() -> dict[str, Any]:
    return {
        "kabu_token_authority": OWNER_INGRESS,
        "owner": "",
        "pid": 0,
        "session_id": "",
        "token_generation": 0,
        "token_issue_count": 0,
        "token_issue_owner": "",
        "unexpected_token_issue_count": 0,
        "blocked_child_issue_count": 0,
        "blocked_second_issuer_count": 0,
        "last_issue_at": "",
        "last_issue_caller": "",
        "updated_at": _now_iso(),
    }


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return body if isinstance(body, dict) else {}


def load_station_owner(base_url: Optional[str] = None) -> dict[str, Any]:
    return _load_json(station_authority_dir(base_url) / STATION_OWNER_JSON)


def load_station_bundle(base_url: Optional[str] = None) -> dict[str, Any]:
    return _load_json(station_authority_dir(base_url) / BUNDLE_JSON)


def ingress_owner_active(
    native_root: Optional[Path] = None,
    trading_date: Optional[str] = None,
) -> bool:
    station = load_station_owner()
    if str(station.get("owner") or "") == OWNER_INGRESS and _pid_alive(int(station.get("pid") or 0)):
        return True
    body = load_authority(native_root, trading_date)
    if str(body.get("owner") or "") != OWNER_INGRESS:
        return False
    return _pid_alive(int(body.get("pid") or 0))


def claim_owner(
    *,
    native_root: Path,
    trading_date: str,
    pid: int,
    session_id: str,
) -> dict[str, Any]:
    day_dir = authority_day_dir(native_root, trading_date)
    os.environ[ENV_AUTHORITY_DIR] = str(day_dir)
    body = load_authority(native_root, trading_date) or empty_authority()
    body.update(
        {
            "kabu_token_authority": OWNER_INGRESS,
            "owner": OWNER_INGRESS,
            "pid": int(pid),
            "session_id": str(session_id),
            "updated_at": _now_iso(),
        }
    )
    _write_authority(day_dir, body)
    return body


@contextmanager
def owner_issue_context(
    *,
    native_root: Path,
    trading_date: str,
    pid: int,
    session_id: str,
    caller: str,
) -> Iterator[None]:
    claim_owner(
        native_root=native_root,
        trading_date=trading_date,
        pid=pid,
        session_id=session_id,
    )
    _tls.owner = OWNER_INGRESS
    _tls.caller = str(caller)
    _tls.native_root = Path(native_root)
    _tls.trading_date = str(trading_date)
    _tls.pid = int(pid)
    _tls.session_id = str(session_id)
    try:
        yield
    finally:
        _tls.owner = None
        _tls.caller = ""
        _tls.pid = 0
        _tls.session_id = ""


def current_owner_context() -> str:
    return str(getattr(_tls, "owner", "") or "")


def current_issue_caller() -> str:
    return str(getattr(_tls, "caller", "") or "")


def live_kabu_auth_allowed(*, synthetic: bool = False) -> tuple[bool, str]:
    if synthetic:
        return False, "synthetic"
    if market_input_mode() == MARKET_INPUT_SYNTHETIC and kabu_auth_mode() != KABU_AUTH_LIVE:
        return False, "market_input_synthetic"
    mode = kabu_auth_mode()
    if mode == KABU_AUTH_NONE:
        return False, "kabu_auth_mode_none"
    if mode != KABU_AUTH_LIVE:
        return False, f"kabu_auth_mode_{mode.lower()}"
    if str(os.environ.get(ENV_TOKEN_PREFLIGHT) or "").strip() in {"1", "true", "yes"}:
        return False, "preflight"
    if str(os.environ.get(ENV_CERT_PROBE) or "").strip() in {"1", "true", "yes"}:
        return False, "certification_probe"
    return True, "ok"


class _NamedMutex:
    def __init__(self, name: str) -> None:
        self.name = name
        self._handle = None
        if sys.platform != "win32":
            return
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        handle = kernel32.CreateMutexW(None, False, name)
        if not handle:
            handle = kernel32.CreateMutexW(None, False, name.replace("Global\\", "Local\\", 1))
        self._handle = handle

    def acquire(self, timeout_sec: float = 30.0) -> None:
        if self._handle is None:
            return
        import ctypes

        ms = int(max(1.0, timeout_sec) * 1000.0)
        rc = int(ctypes.windll.kernel32.WaitForSingleObject(self._handle, ms))
        if rc not in (WAIT_OBJECT_0, WAIT_ABANDONED):
            raise TimeoutError(f"station token mutex timeout name={self.name} rc={rc}")

    def release(self) -> None:
        if self._handle is None:
            return
        import ctypes

        ctypes.windll.kernel32.ReleaseMutex(self._handle)


class _FileLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh: Any = None

    def acquire(self, timeout_sec: float = 30.0) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a+b")
        deadline = time.monotonic() + max(0.1, float(timeout_sec))
        while True:
            try:
                self._lock_once()
                return
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"station token file lock timeout path={self.path}")
                time.sleep(0.02)

    def _lock_once(self) -> None:
        fh = self._fh
        if fh is None:
            raise OSError("lock file closed")
        fh.seek(0)
        if fh.read(1) == b"":
            fh.write(b"\0")
            fh.flush()
        fh.seek(0)
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def release(self) -> None:
        fh = self._fh
        self._fh = None
        if fh is None:
            return
        try:
            fh.seek(0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            fh.close()
        except OSError:
            pass


@contextmanager
def station_issue_lock(*, base_url: Optional[str] = None, timeout_sec: float = 30.0) -> Iterator[Path]:
    eid = station_endpoint_id(base_url)
    day_dir = station_authority_dir(base_url)
    mutex = _NamedMutex(f"Global\\KabuNativeTokenIssue_{eid}")
    flock = _FileLock(day_dir / ISSUE_LOCK)
    mutex.acquire(timeout_sec)
    try:
        flock.acquire(timeout_sec)
        try:
            yield day_dir
        finally:
            flock.release()
    finally:
        mutex.release()


def _append_audit(day_dir: Path, row: dict[str, Any]) -> None:
    safe = {k: v for k, v in row.items() if str(k).lower() not in {"token", "password", "api_password"}}
    path = day_dir / AUDIT_JSONL
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(safe, ensure_ascii=False, default=str) + "\n")


def _station_owner_live(owner: dict[str, Any]) -> bool:
    if str(owner.get("owner") or "") != OWNER_INGRESS:
        return False
    return _pid_alive(int(owner.get("pid") or 0))


def evaluate_issue_permission(
    *,
    caller: str,
    base_url: Optional[str] = None,
    synthetic: bool = False,
) -> dict[str, Any]:
    """Decide POST /token allow/block without touching Station. Call under lock."""
    allowed_auth, auth_reason = live_kabu_auth_allowed(synthetic=synthetic)
    tls_owner = current_owner_context()
    my_pid = int(getattr(_tls, "pid", 0) or os.getpid())
    owner = load_station_owner(base_url)
    owner_pid = int(owner.get("pid") or 0)
    owner_live = _station_owner_live(owner)
    old_gen = int(owner.get("token_generation") or load_station_bundle(base_url).get("generation") or 0)
    result: dict[str, Any] = {
        "allowed": False,
        "reason": "",
        "code": BLOCKED_REASON,
        "caller": caller,
        "pid": my_pid,
        "role": tls_owner or "TOKEN_CONSUMER_ONLY",
        "owner_pid": owner_pid,
        "owner_live": owner_live,
        "old_generation": old_gen,
        "station_endpoint": station_endpoint_id(base_url),
        "trading_date": str(getattr(_tls, "trading_date", "") or session_now().strftime("%Y%m%d")),
        "auth_mode": kabu_auth_mode(),
        "input_mode": market_input_mode(),
    }
    if not allowed_auth:
        result["reason"] = auth_reason
        return result
    if tls_owner != OWNER_INGRESS:
        result["reason"] = "not_ingress_owner_context"
        return result
    if owner_live and owner_pid != my_pid:
        result["reason"] = "station_owner_pid_alive"
        return result
    result["allowed"] = True
    result["code"] = "ALLOWED"
    result["reason"] = "authorized_ingress" if owner_pid in {0, my_pid} else "stale_owner_takeover"
    if owner_live and owner_pid == my_pid:
        result["reason"] = "authorized_ingress"
    elif owner_pid and not owner_live:
        result["reason"] = "stale_owner_takeover"
    return result


def gate_token_issue(*, caller: str = "rest_client.issue_token") -> None:
    """Fail-closed before POST /token. TLS owner is not sufficient by itself."""
    base = _default_base_url()
    with station_issue_lock(base_url=base):
        decision = evaluate_issue_permission(caller=caller, base_url=base)
        if decision.get("allowed"):
            return
        _record_blocked(caller=caller, decision=decision, base_url=base)
        raise TokenSecondIssuerBlocked(
            f"{BLOCKED_REASON} caller={caller} reason={decision.get('reason')} "
            f"owner_pid={decision.get('owner_pid')}"
        )


def _record_blocked(*, caller: str, decision: dict[str, Any], base_url: Optional[str] = None) -> None:
    day_dir = station_authority_dir(base_url)
    owner = load_station_owner(base_url) or empty_authority()
    owner["blocked_second_issuer_count"] = int(owner.get("blocked_second_issuer_count") or 0) + 1
    owner["blocked_child_issue_count"] = int(owner.get("blocked_child_issue_count") or 0) + 1
    owner["last_blocked_caller"] = str(caller)
    owner["last_blocked_reason"] = str(decision.get("reason") or BLOCKED_REASON)
    owner["updated_at"] = _now_iso()
    _atomic_write_json(day_dir / STATION_OWNER_JSON, {k: v for k, v in owner.items() if k != "token"})
    try:
        body = load_authority() or empty_authority()
        body["blocked_child_issue_count"] = int(body.get("blocked_child_issue_count") or 0) + 1
        body["blocked_second_issuer_count"] = int(body.get("blocked_second_issuer_count") or 0) + 1
        body["last_blocked_caller"] = str(caller)
        body["updated_at"] = _now_iso()
        _write_authority(authority_day_dir(), body)
    except Exception:
        pass
    _append_audit(
        day_dir,
        {
            "at": _now_iso(),
            "allowed": False,
            "code": BLOCKED_REASON,
            "pid": os.getpid(),
            "process": Path(sys.argv[0]).name if sys.argv else "",
            "role": decision.get("role"),
            "reason": decision.get("reason"),
            "caller": caller,
            "session_trading_date": decision.get("trading_date"),
            "old_generation": decision.get("old_generation"),
            "new_generation": decision.get("old_generation"),
            "fingerprint": "",
            "station_endpoint": decision.get("station_endpoint"),
        },
    )


def publish_owned_token(
    token: str,
    *,
    native_root: Path,
    trading_date: str,
    caller: str,
    base_url: Optional[str] = None,
) -> dict[str, Any]:
    """Atomic bundle replace: generation + fingerprint + token in one file, then day-dir copy."""
    key = str(token or "").strip()
    if not key:
        raise ValueError("empty token")
    fp = token_fingerprint(key)
    existing = load_station_bundle(base_url)
    if str(existing.get("token") or "") == key and str(existing.get("fingerprint") or "") == fp:
        body = load_authority(native_root, trading_date) or empty_authority()
        return body
    day_dir = authority_day_dir(native_root, trading_date)
    station_dir = station_authority_dir(base_url)
    body = load_authority(native_root, trading_date) or empty_authority()
    station = load_station_owner(base_url) or empty_authority()
    gen = max(int(body.get("token_generation") or 0), int(station.get("token_generation") or 0)) + 1
    issued_at = _now_iso()
    pid = int(getattr(_tls, "pid", 0) or os.getpid())
    session_id = str(getattr(_tls, "session_id", "") or body.get("session_id") or "")
    bundle = {
        "generation": gen,
        "token_generation": gen,
        "fingerprint": fp,
        "token": key,
        "issued_at": issued_at,
        "owner": OWNER_INGRESS,
        "pid": pid,
        "session_id": session_id,
        "caller": str(caller),
        "trading_date": str(trading_date),
        "station_endpoint": station_endpoint_id(base_url),
        "issue_reason": str(caller),
        "kabu_token_authority": OWNER_INGRESS,
    }
    _atomic_write_json(station_dir / BUNDLE_JSON, bundle)
    meta = {k: v for k, v in bundle.items() if k != "token"}
    meta.update(
        {
            "token_generation": gen,
            "token_issue_count": int(station.get("token_issue_count") or 0) + 1,
            "token_issue_owner": OWNER_INGRESS,
            "last_issue_at": issued_at,
            "last_issue_caller": str(caller),
            "updated_at": issued_at,
            "blocked_child_issue_count": int(station.get("blocked_child_issue_count") or 0),
            "blocked_second_issuer_count": int(station.get("blocked_second_issuer_count") or 0),
        }
    )
    _atomic_write_json(station_dir / STATION_OWNER_JSON, meta)
    _atomic_write_text(_token_path(day_dir), key)
    try:
        os.chmod(_token_path(day_dir), 0o600)
    except OSError:
        pass
    body.update(
        {
            "kabu_token_authority": OWNER_INGRESS,
            "owner": OWNER_INGRESS,
            "pid": pid,
            "session_id": session_id,
            "token_generation": gen,
            "token_issue_count": int(body.get("token_issue_count") or 0) + 1,
            "token_issue_owner": OWNER_INGRESS,
            "last_issue_at": issued_at,
            "last_issue_caller": str(caller),
            "updated_at": issued_at,
            "token_fingerprint": fp,
            "station_endpoint": station_endpoint_id(base_url),
        }
    )
    _write_authority(day_dir, body)
    _append_audit(
        station_dir,
        {
            "at": issued_at,
            "allowed": True,
            "code": "ALLOWED",
            "pid": pid,
            "process": Path(sys.argv[0]).name if sys.argv else "",
            "role": OWNER_INGRESS,
            "reason": str(caller),
            "caller": caller,
            "session_trading_date": str(trading_date),
            "old_generation": gen - 1,
            "new_generation": gen,
            "fingerprint": fp,
            "station_endpoint": station_endpoint_id(base_url),
        },
    )
    return body


def read_shared_token(
    native_root: Optional[Path] = None,
    trading_date: Optional[str] = None,
) -> str:
    bundle = load_station_bundle()
    tok = str(bundle.get("token") or "").strip()
    if tok:
        return tok
    path = _token_path(authority_day_dir(native_root, trading_date))
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def read_shared_generation(
    native_root: Optional[Path] = None,
    trading_date: Optional[str] = None,
) -> int:
    bundle = load_station_bundle()
    if bundle.get("generation") is not None:
        return int(bundle.get("generation") or 0)
    body = load_authority(native_root, trading_date)
    return int(body.get("token_generation") or 0)


def issue_station_token(
    client: Any,
    api_password: str,
    *,
    caller: str = "rest_client.issue_token",
    synthetic: bool = False,
) -> str:
    """Sole Station POST /token entry. Blocks second issuers before HTTP."""
    base = str(getattr(client, "base_url", "") or _default_base_url())
    tls_caller = current_issue_caller() or caller
    with station_issue_lock(base_url=base):
        decision = evaluate_issue_permission(caller=tls_caller, base_url=base, synthetic=synthetic)
        if not decision.get("allowed"):
            _record_blocked(caller=tls_caller, decision=decision, base_url=base)
            raise TokenSecondIssuerBlocked(
                f"{BLOCKED_REASON} caller={tls_caller} reason={decision.get('reason')} "
                f"owner_pid={decision.get('owner_pid')}"
            )
        post = getattr(client, "post_token_http", None)
        if post is None:
            raise ChildTokenIssueBlocked("TOKEN_HTTP_ENTRY_MISSING")
        token = str(post(api_password) or "").strip()
        if not token:
            raise ChildTokenIssueBlocked("TOKEN_RESPONSE_EMPTY")
        native = Path(getattr(_tls, "native_root", None) or native_root_default())
        day = str(getattr(_tls, "trading_date", "") or session_now().strftime("%Y%m%d"))
        publish_owned_token(
            token,
            native_root=native,
            trading_date=day,
            caller=tls_caller,
            base_url=base,
        )
        return token


def acquire_token_for_readonly(
    *,
    native_root: Path,
    trading_date: str,
    caller: str,
    rest: Any = None,
) -> dict[str, Any]:
    """Board/probe access. Reuses published token only. Never POSTs /token.

    Leftover day-dir tokens from a dead Ingress are not a live shared token.
    Pre-Ingress consumers must wait; they must not probe Station with a stale key.
    """
    owner_active = ingress_owner_active(native_root, trading_date)
    token = read_shared_token(native_root, trading_date) if owner_active else ""
    gen = read_shared_generation(native_root, trading_date) if owner_active else 0
    if token and owner_active:
        return {
            "token": token,
            "issued": False,
            "reused": True,
            "owner": OWNER_INGRESS,
            "token_generation": gen,
            "fingerprint": token_fingerprint(token),
            "caller": caller,
            "may_issue_token": False,
        }
    raise TokenUnavailable(
        f"INGRESS_TOKEN_UNAVAILABLE caller={caller} owner_active={owner_active}"
    )


def audit_snapshot(
    native_root: Optional[Path] = None,
    trading_date: Optional[str] = None,
) -> dict[str, Any]:
    body = load_authority(native_root, trading_date) or empty_authority()
    station = load_station_owner() or {}
    return {
        "kabu_token_authority": body.get("kabu_token_authority") or OWNER_INGRESS,
        "token_issue_count": int(station.get("token_issue_count") or body.get("token_issue_count") or 0),
        "token_issue_owner": str(station.get("token_issue_owner") or body.get("token_issue_owner") or ""),
        "token_generation": int(station.get("token_generation") or body.get("token_generation") or 0),
        "unexpected_token_issue_count": int(body.get("unexpected_token_issue_count") or 0),
        "blocked_child_issue_count": int(
            station.get("blocked_child_issue_count") or body.get("blocked_child_issue_count") or 0
        ),
        "blocked_second_issuer_count": int(
            station.get("blocked_second_issuer_count") or body.get("blocked_second_issuer_count") or 0
        ),
        "owner_active": ingress_owner_active(native_root, trading_date),
        "owner_pid": int(station.get("pid") or body.get("pid") or 0),
        "session_id": str(station.get("session_id") or body.get("session_id") or ""),
        "station_endpoint": station_endpoint_id(),
        "active_token_issuer_count": 1 if ingress_owner_active(native_root, trading_date) else 0,
        "active_token_issuer_role": OWNER_INGRESS if ingress_owner_active(native_root, trading_date) else "",
    }


def station_issue_audit_summary(base_url: Optional[str] = None) -> dict[str, Any]:
    path = station_authority_dir(base_url) / AUDIT_JSONL
    allowed = 0
    blocked = 0
    rows: list[dict[str, Any]] = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            rows.append(row)
            if row.get("allowed"):
                allowed += 1
            else:
                blocked += 1
    station = load_station_owner(base_url)
    return {
        "station_token_issue_count": int(station.get("token_issue_count") or allowed),
        "authorized_issue_count": allowed,
        "blocked_second_issuer_count": int(station.get("blocked_second_issuer_count") or blocked),
        "audit_rows": len(rows),
        "station_endpoint": station_endpoint_id(base_url),
        "owner_pid": int(station.get("pid") or 0),
        "token_generation": int(station.get("token_generation") or 0),
        "fingerprint": str(station.get("fingerprint") or ""),
    }


def next_backoff_sec(backoff_count: int) -> float:
    idx = max(0, min(int(backoff_count), len(REGISTER_BACKOFF_SEC) - 1))
    return float(REGISTER_BACKOFF_SEC[idx])
