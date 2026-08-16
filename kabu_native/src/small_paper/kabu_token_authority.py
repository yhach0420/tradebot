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
from typing import Any, Iterator, Mapping, Optional
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from small_paper.runtime_clock import (
    ENV_CERT_PROBE,
    ENV_TOKEN_PREFLIGHT,
    KABU_AUTH_LIVE,
    KABU_AUTH_NONE,
    KABU_AUTH_SHARED,
    MARKET_INPUT_SYNTHETIC,
    certification_mode,
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


class OwnerIdentityFailClosed(ChildTokenIssueBlocked):
    """PID reuse / unknown / conflict owner. No kill, no token reuse."""


STALE_STAGE_TOKEN_REJECTED = "STALE_STAGE_TOKEN_REJECTED"
CURRENT_STAGE_TOKEN_IDENTITY_NOT_PROVEN = "CURRENT_STAGE_TOKEN_IDENTITY_NOT_PROVEN"
TOKEN_STAGE_MATCH = "TOKEN_STAGE_MATCH"
TOKEN_STAGE_MISMATCH = "TOKEN_STAGE_MISMATCH"
TOKEN_STAGE_MISSING = "TOKEN_STAGE_MISSING"
TOKEN_STAGE_NOT_APPLICABLE = "TOKEN_STAGE_NOT_APPLICABLE"
BUNDLE_SCHEMA_VERSION = "KABU_STATION_TOKEN_BUNDLE_V24"

AUTHORITY_CLAIMED_PENDING_TOKEN = "CLAIMED_PENDING_TOKEN"
AUTHORITY_ACTIVE_TOKEN_OWNER = "ACTIVE_TOKEN_OWNER"
AUTHORITY_RELEASED_DEAD = "RELEASED_DEAD"
AUTHORITY_FAILED_ISSUE = "FAILED_ISSUE"


class StaleStageTokenRejected(TokenUnavailable):
    """Published token belongs to a previous certification stage. Do not board with it."""


class CurrentStageTokenIdentityNotProven(RuntimeError):
    """Certification wants a stage identity but the bundle does not prove it.

    Distinct from previous-stage mismatch. Not a TokenUnavailable — board must
    fail-close; pre-Ingress readonly may defer.
    """



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


def current_stage_token_identity() -> dict[str, str]:
    from small_paper.derived_artifact_contract import ENV_RUNTIME_RUN_ID
    from small_paper.ingress_run_identity import (
        ENV_ACTIVATION_ID,
        ENV_ACTIVATION_SHA,
        ENV_CERTIFICATION_RUN_ID,
        ENV_STAGE_RUN_ID,
        activation_identity,
    )

    aid, ash = activation_identity()
    return {
        "certification_run_id": str(os.environ.get(ENV_CERTIFICATION_RUN_ID) or "").strip(),
        "stage_run_id": str(os.environ.get(ENV_STAGE_RUN_ID) or "").strip(),
        "activation_id": str(os.environ.get(ENV_ACTIVATION_ID) or aid or "").strip(),
        "activation_sha": str(os.environ.get(ENV_ACTIVATION_SHA) or ash or "").strip(),
        "runtime_run_id": str(os.environ.get(ENV_RUNTIME_RUN_ID) or "").strip(),
    }


def cert_live_auth_required(*, synthetic: bool = False) -> bool:
    if synthetic:
        return False
    if not certification_mode():
        return False
    return kabu_auth_mode() in {KABU_AUTH_LIVE, KABU_AUTH_SHARED}


def classify_token_stage(
    bundle: Optional[Mapping[str, Any]] = None,
    *,
    want: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Classify published bundle vs current process stage identity.

    Production (no want_stage) → TOKEN_STAGE_NOT_APPLICABLE.
    Missing stage key is not previous-stage stale.
    """
    ident = dict(want or current_stage_token_identity())
    body = dict(bundle or {})
    want_stage = str(ident.get("stage_run_id") or "").strip()
    want_cert = str(ident.get("certification_run_id") or "").strip()
    want_act = str(ident.get("activation_sha") or "").strip()
    stage_key = "stage_run_id" in body
    got_stage = str(body.get("stage_run_id") or "").strip()
    got_cert = str(body.get("certification_run_id") or "").strip()
    got_act = str(body.get("activation_sha") or "").strip()
    out: dict[str, Any] = {
        "class": TOKEN_STAGE_NOT_APPLICABLE,
        "want_stage": want_stage or None,
        "got_stage": got_stage or ("missing" if (want_stage and not stage_key) else None),
        "stage_key_present": stage_key,
        "want_certification_run_id": want_cert or None,
        "got_certification_run_id": got_cert or None,
        "want_activation_sha": want_act or None,
        "got_activation_sha": got_act or None,
        "code": TOKEN_STAGE_NOT_APPLICABLE,
    }
    if not want_stage:
        return out
    if not stage_key or not got_stage:
        out["class"] = TOKEN_STAGE_MISSING
        out["code"] = CURRENT_STAGE_TOKEN_IDENTITY_NOT_PROVEN
        out["got_stage"] = "missing"
        return out
    mismatch = got_stage != want_stage
    if want_cert and got_cert != want_cert:
        mismatch = True
    if want_act and got_act != want_act:
        mismatch = True
    if mismatch:
        out["class"] = TOKEN_STAGE_MISMATCH
        out["code"] = STALE_STAGE_TOKEN_REJECTED
        return out
    out["class"] = TOKEN_STAGE_MATCH
    out["code"] = TOKEN_STAGE_MATCH
    return out


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
    bundle = load_station_bundle()
    state = str(station.get("authority_state") or bundle.get("authority_state") or "").strip()
    if state in {AUTHORITY_CLAIMED_PENDING_TOKEN, AUTHORITY_FAILED_ISSUE, AUTHORITY_RELEASED_DEAD}:
        return False
    from small_paper.ownership_classifier import CURRENT_VALID, classify_owner, current_identity_from_env

    classified = classify_owner(
        owner=station,
        bundle=bundle,
        current=current_identity_from_env(pid=os.getpid()),
        pid_alive_fn=_pid_alive,
    )
    if classified.get("class") != CURRENT_VALID:
        return False
    if state == AUTHORITY_ACTIVE_TOKEN_OWNER:
        return True
    # Legacy bundles published before authority_state existed.
    return bool(str(bundle.get("token") or "").strip() and (bundle.get("generation") or station.get("token_generation")))


def _owner_identity_fields(pid: int, *, session_id: str = "") -> dict[str, Any]:
    from small_paper.ingress_run_identity import ENV_INGRESS_RUN_ID, ENV_LAUNCH_NONCE, capture_process_start_identity

    start = ""
    try:
        start = str(capture_process_start_identity(int(pid)) or "")
    except Exception:
        start = ""
    stage = current_stage_token_identity()
    return {
        "pid": int(pid),
        "owner_pid": int(pid),
        "owner_process_start": start,
        "owner_process_start_identity": start,
        "component_role": OWNER_INGRESS,
        "owner_role": OWNER_INGRESS,
        "owner": OWNER_INGRESS,
        "kabu_token_authority": OWNER_INGRESS,
        "session_id": str(session_id or ""),
        "ingress_run_id": str(
            getattr(_tls, "ingress_run_id", "") or os.environ.get(ENV_INGRESS_RUN_ID) or ""
        ).strip(),
        "launch_nonce": str(
            getattr(_tls, "launch_nonce", "") or os.environ.get(ENV_LAUNCH_NONCE) or ""
        ).strip(),
        "stage_id": str(stage.get("stage_run_id") or "").strip() or None,
        "stage_run_id": stage.get("stage_run_id") or None,
        "certification_run_id": stage.get("certification_run_id") or None,
        "activation_id": stage.get("activation_id") or None,
        "activation_sha": stage.get("activation_sha") or None,
        "runtime_run_id": stage.get("runtime_run_id") or None,
    }


def _append_previous_owner_history(station: dict[str, Any], *, reason: str) -> list[dict[str, Any]]:
    history = list(station.get("previous_owner_history") or [])
    old_pid = int(station.get("pid") or station.get("owner_pid") or 0)
    if old_pid <= 0:
        return history
    history.append(
        {
            "pid": old_pid,
            "owner_pid": old_pid,
            "owner_process_start_identity": str(
                station.get("owner_process_start_identity") or station.get("owner_process_start") or ""
            ),
            "component_role": str(station.get("component_role") or station.get("owner_role") or station.get("owner") or ""),
            "ingress_run_id": str(station.get("ingress_run_id") or ""),
            "launch_nonce": str(station.get("launch_nonce") or ""),
            "stage_id": str(station.get("stage_id") or station.get("stage_run_id") or ""),
            "generation": int(station.get("token_generation") or station.get("generation") or 0),
            "authority_state": str(station.get("authority_state") or ""),
            "released_at": _now_iso(),
            "reason": str(reason),
        }
    )
    return history


def _classify_station_owner(*, pid: int = 0) -> dict[str, Any]:
    from small_paper.ownership_classifier import classify_owner, current_identity_from_env

    return classify_owner(
        owner=load_station_owner(),
        bundle=load_station_bundle(),
        current=current_identity_from_env(pid=int(pid or getattr(_tls, "pid", 0) or os.getpid())),
        pid_alive_fn=_pid_alive,
    )


def claim_owner(
    *,
    native_root: Path,
    trading_date: str,
    pid: int,
    session_id: str,
) -> dict[str, Any]:
    from small_paper.auth_issue_trace import (
        CLAIM_OWNER_BEGIN,
        CLAIM_OWNER_FAIL,
        CLAIM_OWNER_OK,
        bind_trace_context,
        record_auth_issue_event,
    )
    from small_paper.ownership_classifier import (
        CONFLICT,
        CURRENT_VALID,
        PID_REUSED,
        UNKNOWN,
    )

    bind_trace_context(native_root=native_root, trading_date=trading_date)
    record_auth_issue_event(CLAIM_OWNER_BEGIN, result="begin")
    try:
        classified = _classify_station_owner(pid=int(pid))
        cls = str(classified.get("class") or "")
        owner_pid = int(classified.get("pid") or 0)
        if cls == CURRENT_VALID and owner_pid not in {0, int(pid)}:
            err = TokenSecondIssuerBlocked(
                f"{BLOCKED_REASON} claim_owner reason=CURRENT_VALID owner_pid={owner_pid}"
            )
            record_auth_issue_event(CLAIM_OWNER_FAIL, result="CURRENT_VALID", exception=err)
            raise err
        if cls == PID_REUSED or cls == CONFLICT or (cls == UNKNOWN and classified.get("process_alive")):
            err = OwnerIdentityFailClosed(
                f"OWNER_IDENTITY_FAIL_CLOSED class={cls} reason={classified.get('reason')} "
                f"owner_pid={owner_pid} wrong_process_kill=0"
            )
            record_auth_issue_event(CLAIM_OWNER_FAIL, result=cls, exception=err)
            raise err
        day_dir = authority_day_dir(native_root, trading_date)
        os.environ[ENV_AUTHORITY_DIR] = str(day_dir)
        ident = _owner_identity_fields(int(pid), session_id=str(session_id))
        body = load_authority(native_root, trading_date) or empty_authority()
        body.update(ident)
        body["authority_state"] = AUTHORITY_CLAIMED_PENDING_TOKEN
        body["updated_at"] = _now_iso()
        # Claim does not stamp token generation as current MATCH / AUTH_READY.
        _write_authority(day_dir, body)
        station_dir = station_authority_dir()
        station = load_station_owner() or empty_authority()
        history = _append_previous_owner_history(station, reason="claim_pending")
        station_out = {k: v for k, v in station.items() if k != "token"}
        station_out.update(ident)
        station_out["authority_state"] = AUTHORITY_CLAIMED_PENDING_TOKEN
        station_out["previous_owner_history"] = history
        station_out["updated_at"] = body["updated_at"]
        station_out["claimed_at"] = body["updated_at"]
        _atomic_write_json(station_dir / STATION_OWNER_JSON, station_out)
        record_auth_issue_event(
            CLAIM_OWNER_OK,
            result="CLAIMED_PENDING_TOKEN",
            extra={"claimed_pid": int(pid), "session_id": str(session_id), "authority_state": AUTHORITY_CLAIMED_PENDING_TOKEN},
        )
        return body
    except Exception as exc:
        if not isinstance(exc, (TokenSecondIssuerBlocked, OwnerIdentityFailClosed)):
            record_auth_issue_event(CLAIM_OWNER_FAIL, result="error", exception=exc)
        raise


@contextmanager
def owner_issue_context(
    *,
    native_root: Path,
    trading_date: str,
    pid: int,
    session_id: str,
    caller: str,
) -> Iterator[None]:
    from small_paper.auth_issue_trace import OWNER_CONTEXT_ENTER, bind_trace_context, record_auth_issue_event

    bind_trace_context(native_root=native_root, trading_date=trading_date)
    record_auth_issue_event(OWNER_CONTEXT_ENTER, result="enter", extra={"caller": str(caller)})
    from small_paper.ingress_run_identity import ENV_INGRESS_RUN_ID, ENV_LAUNCH_NONCE

    _tls.owner = OWNER_INGRESS
    _tls.caller = str(caller)
    _tls.native_root = Path(native_root)
    _tls.trading_date = str(trading_date)
    _tls.pid = int(pid)
    _tls.session_id = str(session_id)
    _tls.launch_nonce = str(os.environ.get(ENV_LAUNCH_NONCE) or "").strip()
    _tls.ingress_run_id = str(os.environ.get(ENV_INGRESS_RUN_ID) or "").strip()
    try:
        claim_owner(
            native_root=native_root,
            trading_date=trading_date,
            pid=pid,
            session_id=session_id,
        )
        yield
    finally:
        _tls.owner = None
        _tls.caller = ""
        _tls.pid = 0
        _tls.session_id = ""
        _tls.launch_nonce = ""
        _tls.ingress_run_id = ""


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
    from small_paper.ownership_classifier import (
        CONFLICT,
        CURRENT_VALID,
        DEAD_OWNER,
        PID_REUSED,
        STALE_PROVEN_OWNED,
        UNKNOWN,
    )

    allowed_auth, auth_reason = live_kabu_auth_allowed(synthetic=synthetic)
    tls_owner = current_owner_context()
    my_pid = int(getattr(_tls, "pid", 0) or os.getpid())
    owner = load_station_owner(base_url)
    owner_pid = int(owner.get("pid") or 0)
    owner_live = _station_owner_live(owner)
    old_gen = int(owner.get("token_generation") or load_station_bundle(base_url).get("generation") or 0)
    ownership = _classify_station_owner(pid=my_pid)
    result: dict[str, Any] = {
        "allowed": False,
        "reason": "",
        "code": BLOCKED_REASON,
        "caller": caller,
        "pid": my_pid,
        "role": tls_owner or "TOKEN_CONSUMER_ONLY",
        "owner_pid": owner_pid,
        "owner_live": owner_live,
        "ownership_class": ownership.get("class"),
        "process_alive": bool(ownership.get("process_alive")),
        "old_generation": old_gen,
        "station_endpoint": station_endpoint_id(base_url),
        "trading_date": str(getattr(_tls, "trading_date", "") or session_now().strftime("%Y%m%d")),
        "auth_mode": kabu_auth_mode(),
        "input_mode": market_input_mode(),
        "wrong_process_kill": 0,
    }
    if not allowed_auth:
        result["reason"] = auth_reason
        return result
    if tls_owner != OWNER_INGRESS:
        result["reason"] = "not_ingress_owner_context"
        return result
    ocls = str(ownership.get("class") or "")
    if ocls == PID_REUSED:
        result["reason"] = PID_REUSED
        return result
    if ocls == CONFLICT:
        result["reason"] = CONFLICT
        return result
    if ocls == UNKNOWN and ownership.get("process_alive"):
        result["reason"] = UNKNOWN
        return result
    stage = current_stage_token_identity()
    bundle = load_station_bundle(base_url)
    classified = classify_token_stage(bundle, want=stage)
    result["token_stage_class"] = classified.get("class")
    leftover_unscoped = classified.get("class") in {TOKEN_STAGE_MISSING, TOKEN_STAGE_MISMATCH}
    if ocls == CURRENT_VALID and owner_pid not in {0, my_pid}:
        result["reason"] = "station_owner_pid_alive"
        return result
    result["allowed"] = True
    result["code"] = "ALLOWED"
    result["reason"] = "authorized_ingress" if owner_pid in {0, my_pid} else "stale_owner_takeover"
    if owner_pid in {0, my_pid}:
        result["reason"] = "authorized_ingress"
    elif ocls == DEAD_OWNER:
        result["reason"] = "stale_owner_takeover"
    elif leftover_unscoped or ocls == STALE_PROVEN_OWNED:
        result["reason"] = "stale_stage_takeover"
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
    from small_paper.auth_issue_trace import (
        AUTH_READY,
        PUBLISH_SUCCESS,
        PUBLISH_TOKEN_BEGIN,
        PUBLISH_TOKEN_OK,
        record_auth_issue_event,
    )

    record_auth_issue_event(PUBLISH_TOKEN_BEGIN, result="begin")
    fp = token_fingerprint(key)
    existing = load_station_bundle(base_url)
    stage = current_stage_token_identity()
    same_token = str(existing.get("token") or "") == key and str(existing.get("fingerprint") or "") == fp
    classified = classify_token_stage(existing, want=stage)
    same_owner = int(existing.get("pid") or 0) == int(getattr(_tls, "pid", 0) or os.getpid())
    # Never restamp an unscoped leftover as current-stage. Idempotent republish
    # only when Paper has no stage identity, or this process already MATCH-owns it.
    if same_token and same_owner:
        if not stage.get("stage_run_id") or classified.get("class") == TOKEN_STAGE_MATCH:
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
    process_start = ""
    try:
        from small_paper.ingress_run_identity import capture_process_start_identity

        process_start = str(capture_process_start_identity(pid) or "")
    except Exception:
        process_start = ""
    ident_fields = _owner_identity_fields(pid, session_id=session_id)
    if process_start:
        ident_fields["owner_process_start"] = process_start
        ident_fields["owner_process_start_identity"] = process_start
    bundle = {
        "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
        "generation": gen,
        "token_generation": gen,
        "fingerprint": fp,
        "token": key,
        "issued_at": issued_at,
        "owner": OWNER_INGRESS,
        "owner_role": OWNER_INGRESS,
        "pid": pid,
        "owner_pid": pid,
        "owner_process_start": process_start or ident_fields.get("owner_process_start") or "",
        "owner_process_start_identity": process_start or ident_fields.get("owner_process_start_identity") or "",
        "component_role": OWNER_INGRESS,
        "ingress_run_id": ident_fields.get("ingress_run_id") or None,
        "launch_nonce": ident_fields.get("launch_nonce") or None,
        "stage_id": stage.get("stage_run_id") or None,
        "session_id": session_id,
        "caller": str(caller),
        "trading_date": str(trading_date),
        "station_endpoint": station_endpoint_id(base_url),
        "endpoint": station_endpoint_id(base_url),
        "issue_reason": str(caller),
        "kabu_token_authority": OWNER_INGRESS,
        "certification_run_id": stage.get("certification_run_id") or None,
        "stage_run_id": stage.get("stage_run_id") or None,
        "activation_id": stage.get("activation_id") or None,
        "activation_sha": stage.get("activation_sha") or None,
        "runtime_run_id": stage.get("runtime_run_id") or None,
        "authority_state": AUTHORITY_ACTIVE_TOKEN_OWNER,
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
            "authority_state": AUTHORITY_ACTIVE_TOKEN_OWNER,
            "previous_owner_history": list(station.get("previous_owner_history") or []),
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
            "authority_state": AUTHORITY_ACTIVE_TOKEN_OWNER,
            "owner_process_start_identity": process_start or ident_fields.get("owner_process_start_identity") or "",
            "component_role": OWNER_INGRESS,
            "ingress_run_id": ident_fields.get("ingress_run_id") or "",
            "launch_nonce": ident_fields.get("launch_nonce") or "",
        }
    )
    _write_authority(day_dir, body)
    _append_audit(
        station_dir,
        {
            "at": issued_at,
            "event": "PUBLISH_SUCCESS",
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
    record_auth_issue_event(
        PUBLISH_TOKEN_OK,
        result="ok",
        old_generation=gen - 1,
        new_generation=gen,
    )
    record_auth_issue_event(
        AUTH_READY,
        result="ok",
        old_generation=gen - 1,
        new_generation=gen,
    )
    record_auth_issue_event(
        PUBLISH_SUCCESS,
        result="ok",
        old_generation=gen - 1,
        new_generation=gen,
        audit=True,
        allowed=True,
    )
    return body


def mark_issue_failed(*, base_url: Optional[str] = None, reason: str = "") -> None:
    """Claimed issuer failed POST/publish. Do not treat generation as current AUTH_READY."""
    station_dir = station_authority_dir(base_url)
    station = load_station_owner(base_url) or empty_authority()
    if str(station.get("authority_state") or "") == AUTHORITY_ACTIVE_TOKEN_OWNER:
        return
    station["authority_state"] = AUTHORITY_FAILED_ISSUE
    station["last_issue_failure_reason"] = str(reason or "ISSUE_FAILED")[:500]
    station["updated_at"] = _now_iso()
    _atomic_write_json(station_dir / STATION_OWNER_JSON, {k: v for k, v in station.items() if k != "token"})
    try:
        body = load_authority() or empty_authority()
        if str(body.get("authority_state") or "") != AUTHORITY_ACTIVE_TOKEN_OWNER:
            body["authority_state"] = AUTHORITY_FAILED_ISSUE
            body["updated_at"] = station["updated_at"]
            _write_authority(authority_day_dir(), body)
    except Exception:
        pass


def reclaim_dead_station_owner(
    *,
    native_root: Path,
    trading_date: str,
    base_url: Optional[str] = None,
) -> dict[str, Any]:
    """Release current authority for a proven dead managed Ingress. No PID kill, no token delete."""
    from small_paper.ownership_classifier import DEAD_OWNER, classify_owner, current_identity_from_env

    classified = classify_owner(
        owner=load_station_owner(base_url),
        bundle=load_station_bundle(base_url),
        current=current_identity_from_env(pid=os.getpid()),
        pid_alive_fn=_pid_alive,
    )
    out: dict[str, Any] = {
        "ok": True,
        "reclaimed": False,
        "killed_pid": None,
        "wrong_process_kill": 0,
        "bundle_deleted": False,
        "token_bytes_deleted": False,
        "ownership_class": classified.get("class"),
        "reason": classified.get("reason"),
        "previous_owner_pid": int(classified.get("pid") or 0) or None,
    }
    if classified.get("class") != DEAD_OWNER:
        out["reason"] = f"not_dead_owner:{classified.get('class')}"
        return out
    if not classified.get("managed_previous_ingress"):
        out["reason"] = "managed_previous_ingress_not_proven"
        out["ok"] = False
        return out
    if not classified.get("reclaim_allowed"):
        out["reason"] = "reclaim_not_allowed"
        out["ok"] = False
        return out
    station_dir = station_authority_dir(base_url)
    station = load_station_owner(base_url) or empty_authority()
    day_dir = authority_day_dir(native_root, trading_date)
    history = _append_previous_owner_history(station, reason="DEAD_OWNER_RECLAIM")
    released_at = _now_iso()
    station_out = {k: v for k, v in station.items() if k != "token"}
    station_out["previous_owner_history"] = history
    station_out["authority_state"] = AUTHORITY_RELEASED_DEAD
    station_out["pid"] = 0
    station_out["owner_pid"] = 0
    station_out["owner"] = ""
    station_out["reclaimed_at"] = released_at
    station_out["updated_at"] = released_at
    _atomic_write_json(station_dir / STATION_OWNER_JSON, station_out)
    bundle = load_station_bundle(base_url)
    if bundle:
        bundle_out = dict(bundle)
        bundle_out["pid"] = 0
        bundle_out["owner_pid"] = 0
        bundle_out["authority_state"] = AUTHORITY_RELEASED_DEAD
        bundle_out["updated_at"] = released_at
        _atomic_write_json(station_dir / BUNDLE_JSON, bundle_out)
    body = load_authority(native_root, trading_date) or empty_authority()
    body["previous_owner_history"] = list(body.get("previous_owner_history") or []) + (
        history[-1:] if history else []
    )
    body["authority_state"] = AUTHORITY_RELEASED_DEAD
    body["pid"] = 0
    body["owner_pid"] = 0
    body["owner"] = ""
    body["updated_at"] = released_at
    _write_authority(day_dir, body)
    _append_audit(
        station_dir,
        {
            "at": released_at,
            "event": "DEAD_OWNER_RECLAIM",
            "allowed": False,
            "code": "RELEASED_DEAD",
            "pid": 0,
            "role": OWNER_INGRESS,
            "reason": "DEAD_OWNER",
            "previous_owner_pid": out["previous_owner_pid"],
            "wrong_process_kill": 0,
            "bundle_deleted": False,
        },
    )
    out["reclaimed"] = True
    out["reason"] = "RELEASED_DEAD"
    return out


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


def _owned_matching_token(base_url: Optional[str] = None) -> str:
    """Return existing token only when this Ingress process already MATCH-owns it."""
    if not certification_mode():
        return ""
    my_pid = int(getattr(_tls, "pid", 0) or os.getpid())
    if current_owner_context() != OWNER_INGRESS:
        return ""
    bundle = load_station_bundle(base_url)
    classified = classify_token_stage(bundle)
    if classified.get("class") != TOKEN_STAGE_MATCH:
        return ""
    if int(bundle.get("pid") or bundle.get("owner_pid") or 0) != my_pid:
        return ""
    tok = str(bundle.get("token") or "").strip()
    return tok


def issue_station_token(
    client: Any,
    api_password: str,
    *,
    caller: str = "rest_client.issue_token",
    synthetic: bool = False,
) -> str:
    """Sole Station POST /token entry. Blocks second issuers before HTTP."""
    from small_paper.auth_issue_trace import (
        EXCEPTION,
        ISSUE_ATTEMPT_BEGIN,
        ISSUE_EXCEPTION,
        ISSUE_PERMISSION_ALLOW,
        ISSUE_PERMISSION_BEGIN,
        ISSUE_PERMISSION_BLOCK,
        ISSUE_PERMISSION_RESULT,
        ISSUE_STATION_TOKEN_ENTER,
        STATION_LOCK_ACQUIRED,
        STATION_LOCK_BEGIN,
        STATION_LOCK_FAIL,
        record_auth_issue_event,
    )

    base = str(getattr(client, "base_url", "") or _default_base_url())
    tls_caller = current_issue_caller() or caller
    record_auth_issue_event(ISSUE_STATION_TOKEN_ENTER, result="enter", extra={"caller": str(tls_caller)})
    record_auth_issue_event(ISSUE_ATTEMPT_BEGIN, result="begin", audit=True, extra={"caller": str(tls_caller)})
    record_auth_issue_event(STATION_LOCK_BEGIN, result="begin")
    lock_cm = None
    try:
        lock_cm = station_issue_lock(base_url=base)
        lock_cm.__enter__()
    except Exception as exc:
        record_auth_issue_event(STATION_LOCK_FAIL, result="error", exception=exc)
        record_auth_issue_event(ISSUE_EXCEPTION, result="lock_fail", exception=exc, audit=True, allowed=False)
        record_auth_issue_event(EXCEPTION, result="lock_fail", exception=exc)
        raise
    record_auth_issue_event(STATION_LOCK_ACQUIRED, result="ok")
    try:
        reused = _owned_matching_token(base)
        if reused:
            record_auth_issue_event(
                ISSUE_PERMISSION_RESULT,
                result="reuse_owned_match",
                extra={"reused": True},
            )
            return reused
        record_auth_issue_event(ISSUE_PERMISSION_BEGIN, result="begin")
        decision = evaluate_issue_permission(caller=tls_caller, base_url=base, synthetic=synthetic)
        record_auth_issue_event(
            ISSUE_PERMISSION_RESULT,
            result=str(decision.get("reason") or decision.get("code") or ""),
            authority_decision=decision,
            old_generation=decision.get("old_generation"),
        )
        if not decision.get("allowed"):
            record_auth_issue_event(
                ISSUE_PERMISSION_BLOCK,
                result=str(decision.get("reason") or ""),
                authority_decision=decision,
                old_generation=decision.get("old_generation"),
                audit=True,
                allowed=False,
            )
            _record_blocked(caller=tls_caller, decision=decision, base_url=base)
            reason = str(decision.get("reason") or "")
            if reason in {"PID_REUSED", "UNKNOWN", "CONFLICT"}:
                raise OwnerIdentityFailClosed(
                    f"OWNER_IDENTITY_FAIL_CLOSED caller={tls_caller} reason={reason} "
                    f"owner_pid={decision.get('owner_pid')} wrong_process_kill=0"
                )
            raise TokenSecondIssuerBlocked(
                f"{BLOCKED_REASON} caller={tls_caller} reason={decision.get('reason')} "
                f"owner_pid={decision.get('owner_pid')}"
            )
        record_auth_issue_event(
            ISSUE_PERMISSION_ALLOW,
            result=str(decision.get("reason") or "ALLOWED"),
            authority_decision=decision,
            old_generation=decision.get("old_generation"),
            audit=True,
            allowed=True,
        )
        post = getattr(client, "post_token_http", None)
        if post is None:
            err = ChildTokenIssueBlocked("TOKEN_HTTP_ENTRY_MISSING")
            record_auth_issue_event(ISSUE_EXCEPTION, result="http_entry_missing", exception=err, audit=True, allowed=False)
            raise err
        token = str(post(api_password) or "").strip()
        if not token:
            err = ChildTokenIssueBlocked("TOKEN_RESPONSE_EMPTY")
            record_auth_issue_event(ISSUE_EXCEPTION, result="empty_token", exception=err, audit=True, allowed=False)
            raise err
        native = Path(getattr(_tls, "native_root", None) or native_root_default())
        try:
            from small_paper.session_runtime_identity import resolve_runtime_trading_date

            day = str(getattr(_tls, "trading_date", "") or resolve_runtime_trading_date())
        except Exception:
            day = str(getattr(_tls, "trading_date", "") or session_now().strftime("%Y%m%d"))
        publish_owned_token(
            token,
            native_root=native,
            trading_date=day,
            caller=tls_caller,
            base_url=base,
        )
        return token
    except Exception as exc:
        if not isinstance(exc, (TokenSecondIssuerBlocked, OwnerIdentityFailClosed)):
            record_auth_issue_event(ISSUE_EXCEPTION, result="error", exception=exc, audit=True, allowed=False)
            record_auth_issue_event(EXCEPTION, result="error", exception=exc)
            try:
                mark_issue_failed(base_url=base, reason=type(exc).__name__)
            except Exception:
                pass
        raise
    finally:
        if lock_cm is not None:
            try:
                lock_cm.__exit__(None, None, None)
            except Exception:
                pass


def acquire_token_for_readonly(
    *,
    native_root: Path,
    trading_date: str,
    caller: str,
    rest: Any = None,
) -> dict[str, Any]:
    """Board/probe access. Reuses published token only. Never POSTs /token.

    Decision is phase-explicit (auth_lifecycle). PRE_INGRESS missing/unscoped/
    previous-stage defers until current Ingress issues. POST_INGRESS_PRE_BOARD
    and later fail-close. Never POSTs /token.
    """
    from small_paper.auth_lifecycle import (
        DECISION_PASS,
        consumer_auth_outcome,
        raise_for_consumer_decision,
    )

    outcome = consumer_auth_outcome(
        native_root=native_root,
        trading_date=trading_date,
        caller=str(caller),
    )
    decision = outcome["decision"]
    if str(decision.get("decision") or "") != DECISION_PASS:
        raise_for_consumer_decision(decision, caller=str(caller))
    owner_active = ingress_owner_active(native_root, trading_date)
    stage = current_stage_token_identity()
    bundle = load_station_bundle()
    classified = classify_token_stage(bundle, want=stage)
    stage_class = str(classified.get("class") or TOKEN_STAGE_NOT_APPLICABLE)
    got_stage = str(bundle.get("stage_run_id") or "").strip()
    if str(stage.get("stage_run_id") or "").strip() and stage_class == TOKEN_STAGE_MATCH and not owner_active:
        raise TokenUnavailable(
            f"INGRESS_TOKEN_UNAVAILABLE caller={caller} owner_active={owner_active} "
            f"token_stage_class={stage_class}"
        )
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
            "stage_run_id": got_stage or None,
            "certification_run_id": str(bundle.get("certification_run_id") or "") or None,
            "token_stage_class": stage_class,
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
    call_attempts = 0
    http_attempts = 0
    exceptions = 0
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
            ev = str(row.get("event") or "")
            if ev == "ISSUE_ATTEMPT_BEGIN":
                call_attempts += 1
            if ev == "HTTP_ATTEMPT":
                http_attempts += 1
            if ev in {"ISSUE_EXCEPTION"}:
                exceptions += 1
            if ev in {"PUBLISH_SUCCESS", "ALLOWED"} or (not ev and row.get("allowed") is True):
                allowed += 1
            elif ev in {"ISSUE_PERMISSION_BLOCK", "TOKEN_SECOND_ISSUER_BLOCKED"} or (
                not ev and row.get("allowed") is False
            ):
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
        "post_token_call_attempt_count": call_attempts,
        "post_token_http_attempt_count": http_attempts,
        "post_token_success_count": allowed,
        "post_token_exception_count": exceptions,
    }


def next_backoff_sec(backoff_count: int) -> float:
    idx = max(0, min(int(backoff_count), len(REGISTER_BACKOFF_SEC) - 1))
    return float(REGISTER_BACKOFF_SEC[idx])
