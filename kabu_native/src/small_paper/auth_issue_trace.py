"""Structured AUTH issuance traces. Never records passwords or token bytes."""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

TRACE_JSONL = "auth_issue_trace.jsonl"
LAST_FAILURE_JSON = "auth_issue_last_failure.json"

OWNER_CONTEXT_ENTER = "OWNER_CONTEXT_ENTER"
CLAIM_OWNER_BEGIN = "CLAIM_OWNER_BEGIN"
CLAIM_OWNER_OK = "CLAIM_OWNER_OK"
CLAIM_OWNER_FAIL = "CLAIM_OWNER_FAIL"
ISSUE_PERMISSION_BEGIN = "ISSUE_PERMISSION_BEGIN"
ISSUE_PERMISSION_RESULT = "ISSUE_PERMISSION_RESULT"
API_PASSWORD_RESOLVE_BEGIN = "API_PASSWORD_RESOLVE_BEGIN"
API_PASSWORD_RESOLVE_RESULT = "API_PASSWORD_RESOLVE_RESULT"
ISSUE_STATION_TOKEN_ENTER = "ISSUE_STATION_TOKEN_ENTER"
STATION_LOCK_BEGIN = "STATION_LOCK_BEGIN"
STATION_LOCK_ACQUIRED = "STATION_LOCK_ACQUIRED"
STATION_LOCK_FAIL = "STATION_LOCK_FAIL"
POST_TOKEN_HTTP_BEGIN = "POST_TOKEN_HTTP_BEGIN"
POST_TOKEN_HTTP_RESULT = "POST_TOKEN_HTTP_RESULT"
PUBLISH_TOKEN_BEGIN = "PUBLISH_TOKEN_BEGIN"
PUBLISH_TOKEN_OK = "PUBLISH_TOKEN_OK"
AUTH_READY = "AUTH_READY"
EXCEPTION = "EXCEPTION"

ISSUE_ATTEMPT_BEGIN = "ISSUE_ATTEMPT_BEGIN"
ISSUE_PERMISSION_ALLOW = "ISSUE_PERMISSION_ALLOW"
ISSUE_PERMISSION_BLOCK = "ISSUE_PERMISSION_BLOCK"
HTTP_ATTEMPT = "HTTP_ATTEMPT"
HTTP_RESULT = "HTTP_RESULT"
PUBLISH_SUCCESS = "PUBLISH_SUCCESS"
ISSUE_EXCEPTION = "ISSUE_EXCEPTION"

_SECRET_KEY_RE = re.compile(
    r"(token|password|api_password|apipassword|authorization|x-api-key|credential)",
    re.IGNORECASE,
)
_HEX32_RE = re.compile(r"\b[0-9a-fA-F]{32}\b")
_BEARER_RE = re.compile(r"(Bearer\s+)\S+", re.IGNORECASE)

_tls = threading.local()
_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="milliseconds")


def password_present(*, environ: Optional[Mapping[str, str]] = None) -> bool:
    env = environ if environ is not None else os.environ
    return bool(str(env.get("KABU_API_PASSWORD") or "").strip())


def sanitize_text(text: Any, *, extra_secrets: Optional[list[str]] = None) -> str:
    raw = str(text or "")
    out = _BEARER_RE.sub(r"\1<REDACTED>", raw)
    out = _HEX32_RE.sub("<REDACTED_SECRET>", out)
    for sec in extra_secrets or []:
        if sec:
            out = out.replace(str(sec), "<REDACTED_SECRET>")
    out = re.sub(
        r"(APIPassword|password|token|Token|X-API-KEY)\s*[:=]\s*\S+",
        r"\1=<REDACTED>",
        out,
        flags=re.IGNORECASE,
    )
    return out[:2000]


def sanitize_mapping(body: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, val in dict(body).items():
        if _SECRET_KEY_RE.search(str(key or "")):
            continue
        if isinstance(val, Mapping):
            out[str(key)] = sanitize_mapping(val)
        elif isinstance(val, (list, tuple)):
            out[str(key)] = [sanitize_text(x) if isinstance(x, str) else x for x in val]
        elif isinstance(val, str):
            out[str(key)] = sanitize_text(val)
        else:
            out[str(key)] = val
    return out


def _identity() -> dict[str, Any]:
    try:
        from small_paper.kabu_token_authority import current_stage_token_identity

        ident = dict(current_stage_token_identity())
    except Exception:
        ident = {}
    from small_paper.ingress_run_identity import (
        ENV_INGRESS_RUN_ID,
        ENV_LAUNCH_NONCE,
    )

    ident.setdefault("stage_id", ident.get("stage_run_id") or "")
    ident["pid"] = int(os.getpid())
    ident["launch_nonce"] = str(os.environ.get(ENV_LAUNCH_NONCE) or "").strip()
    ident["ingress_run_id"] = str(os.environ.get(ENV_INGRESS_RUN_ID) or "").strip()
    return ident


def _trace_paths() -> list[Path]:
    paths: list[Path] = []
    try:
        from small_paper.kabu_token_authority import authority_day_dir, station_authority_dir

        day = authority_day_dir()
        paths.append(day / TRACE_JSONL)
        paths.append(station_authority_dir() / TRACE_JSONL)
    except Exception:
        pass
    native = Path(getattr(_tls, "native_root", "") or "")
    trading = str(getattr(_tls, "trading_date", "") or "")
    if native and trading:
        paths.append(Path(native) / "data" / "market_capture" / trading / TRACE_JSONL)
    # unique
    seen: set[str] = set()
    uniq: list[Path] = []
    for p in paths:
        key = str(p.resolve()) if p.parent.exists() or True else str(p)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    return uniq


def bind_trace_context(*, native_root: Path, trading_date: str) -> None:
    _tls.native_root = Path(native_root)
    _tls.trading_date = str(trading_date)


def last_failure() -> dict[str, Any]:
    return dict(getattr(_tls, "last_failure", None) or {})


def last_event_name() -> str:
    return str(getattr(_tls, "last_event", "") or "")


def record_auth_issue_event(
    event: str,
    *,
    result: str = "",
    exception: Optional[BaseException] = None,
    exception_type: str = "",
    exception_message: str = "",
    http_status: Any = None,
    authority_decision: Any = None,
    old_generation: Any = None,
    new_generation: Any = None,
    extra: Optional[Mapping[str, Any]] = None,
    audit: bool = False,
    allowed: Optional[bool] = None,
) -> dict[str, Any]:
    ident = _identity()
    exc_type = exception_type or (type(exception).__name__ if exception is not None else "")
    raw_msg = exception_message or (str(exception) if exception is not None else "")
    status = None
    if http_status is not None:
        try:
            status = int(http_status)
        except (TypeError, ValueError):
            status = None
    if status is None and exception is not None:
        try:
            status = int(getattr(exception, "http_status", None) or 0) or None
        except (TypeError, ValueError):
            status = None
    row: dict[str, Any] = {
        "at": _now_iso(),
        "timestamp": _now_iso(),
        "event": str(event),
        "result": str(result or ""),
        "stage_id": ident.get("stage_run_id") or ident.get("stage_id") or "",
        "certification_run_id": ident.get("certification_run_id") or "",
        "activation_id": ident.get("activation_id") or "",
        "activation_sha": ident.get("activation_sha") or "",
        "runtime_run_id": ident.get("runtime_run_id") or "",
        "pid": ident.get("pid"),
        "launch_nonce": ident.get("launch_nonce") or "",
        "ingress_run_id": ident.get("ingress_run_id") or "",
        "exception_type": exc_type,
        "sanitized_exception_message": sanitize_text(raw_msg),
        "http_status": status,
        "authority_decision": (
            sanitize_mapping(authority_decision)
            if isinstance(authority_decision, Mapping)
            else authority_decision
        ),
        "old_generation": old_generation,
        "new_generation": new_generation,
        "password_present": password_present(),
    }
    if extra:
        row.update(sanitize_mapping(extra))
    if allowed is not None:
        row["allowed"] = bool(allowed)
    _tls.last_event = str(event)
    if exc_type or str(event) in {EXCEPTION, ISSUE_EXCEPTION, CLAIM_OWNER_FAIL, STATION_LOCK_FAIL}:
        _tls.last_failure = dict(row)
    _append_jsonl(_trace_paths(), row)
    if audit:
        _append_station_audit(row)
    return row


def _append_jsonl(paths: list[Path], row: dict[str, Any]) -> None:
    line = json.dumps(row, ensure_ascii=False, default=str) + "\n"
    with _lock:
        for path in paths:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8", newline="\n") as fh:
                    fh.write(line)
            except OSError:
                continue
        try:
            from small_paper.kabu_token_authority import authority_day_dir

            fail = last_failure()
            if fail:
                dest = authority_day_dir() / LAST_FAILURE_JSON
                tmp = dest.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(fail, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                os.replace(tmp, dest)
        except Exception:
            pass


def _append_station_audit(row: dict[str, Any]) -> None:
    try:
        from small_paper.kabu_token_authority import _append_audit, station_authority_dir

        _append_audit(station_authority_dir(), dict(row))
    except Exception:
        pass


def failure_fields_for_status() -> dict[str, Any]:
    fail = last_failure()
    if not fail:
        return {}
    return {
        "auth_failure_step": str(fail.get("event") or ""),
        "auth_failure_type": str(fail.get("exception_type") or ""),
        "auth_failure_code": str(fail.get("result") or fail.get("event") or ""),
        "auth_failure_message_sanitized": str(fail.get("sanitized_exception_message") or ""),
        "auth_failure_at": str(fail.get("at") or ""),
        "last_error": str(fail.get("sanitized_exception_message") or fail.get("exception_type") or ""),
        "last_error_type": str(fail.get("exception_type") or ""),
        "auth_failure_http_status": fail.get("http_status"),
        "password_present": bool(fail.get("password_present")),
    }
