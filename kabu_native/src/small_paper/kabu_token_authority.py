"""Single Kabu API token authority for MARKET_INGRESS_V2 live sessions.

MARKET_INGRESS_SERVICE is the only live token issuer while Ingress is active.
Child components (daily/pilot/safety/observer/PBv2/recovery) reuse the owned
token and must not POST /token.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

OWNER_INGRESS = "MARKET_INGRESS_SERVICE"
AUTHORITY_JSON = "kabu_token_authority.json"
TOKEN_FILE = ".kabu_session_token"
ENV_AUTHORITY_DIR = "KABU_TOKEN_AUTHORITY_DIR"

AUTH_INVALID = "AUTH_INVALID"
RATE_LIMIT = "RATE_LIMIT"
OTHER = "OTHER"

REGISTER_BACKOFF_SEC = (1.0, 2.0, 4.0, 8.0, 16.0, 30.0)


class ChildTokenIssueBlocked(RuntimeError):
    """Non-owner attempted POST /token while Ingress owns the live session."""


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


def authority_day_dir(
    native_root: Optional[Path] = None,
    trading_date: Optional[str] = None,
) -> Path:
    env = str(os.environ.get(ENV_AUTHORITY_DIR) or "").strip()
    if env:
        return Path(env)
    root = Path(native_root) if native_root else native_root_default()
    day = str(trading_date or datetime.now(JST).strftime("%Y%m%d"))
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
    _authority_path(day_dir).write_text(
        json.dumps(safe, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


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
        "last_issue_at": "",
        "last_issue_caller": "",
        "updated_at": _now_iso(),
    }


def ingress_owner_active(
    native_root: Optional[Path] = None,
    trading_date: Optional[str] = None,
) -> bool:
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
    try:
        yield
    finally:
        _tls.owner = None
        _tls.caller = ""


def current_owner_context() -> str:
    return str(getattr(_tls, "owner", "") or "")


def publish_owned_token(
    token: str,
    *,
    native_root: Path,
    trading_date: str,
    caller: str,
) -> dict[str, Any]:
    day_dir = authority_day_dir(native_root, trading_date)
    day_dir.mkdir(parents=True, exist_ok=True)
    key = str(token or "").strip()
    if not key:
        raise ValueError("empty token")
    _token_path(day_dir).write_text(key, encoding="utf-8")
    try:
        os.chmod(_token_path(day_dir), 0o600)
    except OSError:
        pass
    body = load_authority(native_root, trading_date) or empty_authority()
    gen = int(body.get("token_generation") or 0) + 1
    body.update(
        {
            "kabu_token_authority": OWNER_INGRESS,
            "owner": OWNER_INGRESS,
            "token_generation": gen,
            "token_issue_count": int(body.get("token_issue_count") or 0) + 1,
            "token_issue_owner": OWNER_INGRESS,
            "last_issue_at": _now_iso(),
            "last_issue_caller": str(caller),
            "updated_at": _now_iso(),
        }
    )
    _write_authority(day_dir, body)
    return body


def read_shared_token(
    native_root: Optional[Path] = None,
    trading_date: Optional[str] = None,
) -> str:
    path = _token_path(authority_day_dir(native_root, trading_date))
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _record_blocked_child_issue(caller: str) -> None:
    day_dir = authority_day_dir()
    body = load_authority() or empty_authority()
    body["blocked_child_issue_count"] = int(body.get("blocked_child_issue_count") or 0) + 1
    body["last_blocked_caller"] = str(caller)
    body["updated_at"] = _now_iso()
    _write_authority(day_dir, body)


def gate_token_issue(*, caller: str = "rest_client.issue_token") -> None:
    """Fail-closed for non-owner POST /token while Ingress owns the live session."""
    if current_owner_context() == OWNER_INGRESS:
        return
    if not ingress_owner_active():
        return
    _record_blocked_child_issue(caller)
    raise ChildTokenIssueBlocked(
        f"CHILD_TOKEN_ISSUE_BLOCKED caller={caller} owner={OWNER_INGRESS}"
    )


def acquire_token_for_readonly(
    *,
    native_root: Path,
    trading_date: str,
    caller: str,
    rest: Any = None,
) -> dict[str, Any]:
    """Board/probe access. Reuses Ingress token when owner is active; never mutates register."""
    if ingress_owner_active(native_root, trading_date):
        token = read_shared_token(native_root, trading_date)
        body = load_authority(native_root, trading_date)
        if not token:
            raise ChildTokenIssueBlocked(
                f"INGRESS_TOKEN_UNAVAILABLE caller={caller}"
            )
        return {
            "token": token,
            "issued": False,
            "reused": True,
            "owner": OWNER_INGRESS,
            "token_generation": int(body.get("token_generation") or 0),
            "caller": caller,
            "may_issue_token": False,
        }
    if rest is None:
        from api.rest_client import KabuNativeRestClient, default_base_url

        rest = KabuNativeRestClient(default_base_url())
    token = rest.issue_token_from_env()
    return {
        "token": token,
        "issued": True,
        "reused": False,
        "owner": "",
        "token_generation": 0,
        "caller": caller,
        "may_issue_token": True,
    }


def audit_snapshot(
    native_root: Optional[Path] = None,
    trading_date: Optional[str] = None,
) -> dict[str, Any]:
    body = load_authority(native_root, trading_date) or empty_authority()
    return {
        "kabu_token_authority": body.get("kabu_token_authority") or OWNER_INGRESS,
        "token_issue_count": int(body.get("token_issue_count") or 0),
        "token_issue_owner": str(body.get("token_issue_owner") or ""),
        "token_generation": int(body.get("token_generation") or 0),
        "unexpected_token_issue_count": int(body.get("unexpected_token_issue_count") or 0),
        "blocked_child_issue_count": int(body.get("blocked_child_issue_count") or 0),
        "owner_active": ingress_owner_active(native_root, trading_date),
        "owner_pid": int(body.get("pid") or 0),
        "session_id": str(body.get("session_id") or ""),
    }


def next_backoff_sec(backoff_count: int) -> float:
    idx = max(0, min(int(backoff_count), len(REGISTER_BACKOFF_SEC) - 1))
    return float(REGISTER_BACKOFF_SEC[idx])
