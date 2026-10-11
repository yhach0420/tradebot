"""Discord-staged base capital and leverage.

Paper G is BASE_CAPITAL_YEN * MAX_LEVERAGE_MULTIPLIER.
A stored direct G stays legacy until both inputs exist.
This module does not write Paper, Ingress, Capture, or a live session.
"""
from __future__ import annotations

import json
import msvcrt
import os
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional

from research.daytrade50_capital_efficient_selector_v2.policy import (
    MAX_CONCURRENT,
    QTY,
    canonical_leverage,
    derived_requested_gross_yen,
    legacy_direct_g_instance_sha,
    paper_effective_g,
    policy_instance_sha,
    policy_spec_sha,
)

JST = timezone(timedelta(hours=9))
PARAMETER = "SYSTEM_GROSS_EXPOSURE_BUDGET_YEN"
MODEL = "BASE_CAPITAL_X_LEVERAGE"
SCHEMA_VERSION_LEGACY = 1
SCHEMA_VERSION = 2
CURRENT_V16_USES_G = False
STORE_NAME = "system_gross_exposure_budget.json"
AUDIT_NAME = "system_gross_exposure_budget_audit.jsonl"
AWAIT_CAPITAL_LEVERAGE = "CAPITAL_LEVERAGE_MODEL_AWAIT_USER_INPUT"
DIRECT_G_SET_DISABLED = "DIRECT_G_SET_DISABLED_USE_CAPITAL_AND_LEVERAGE"
_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


class DuplicateCommand(Exception):
    """This Discord message was already given its one terminal result."""

_DIGITS = re.compile(r"^[1-9]\d*$")
_COMMAS = re.compile(r"^\d{1,3}(,\d{3})+$")
_LEVERAGE = re.compile(r"^(?:0|[1-9]\d*)(?:\.\d+)?$")


def default_budget_paths(repo_root: Path) -> tuple[Path, Path]:
    runtime = Path(repo_root) / "kabu_native" / "runtime"
    return runtime / STORE_NAME, runtime / AUDIT_NAME


def parse_budget_yen(text: str) -> Optional[int]:
    """Positive whole yen. Commas are grouping only. Units and exponents are rejected."""
    raw = (text or "").strip()
    if not raw:
        return None
    if _COMMAS.fullmatch(raw):
        raw = raw.replace(",", "")
    if not _DIGITS.fullmatch(raw):
        return None
    value = int(raw)
    if value <= 0:
        return None
    return value


def parse_leverage(text: str) -> Optional[str]:
    raw = (text or "").strip()
    if not raw or not _LEVERAGE.fullmatch(raw):
        return None
    try:
        return canonical_leverage(raw)
    except ValueError:
        return None


def parse_allowlist(raw: Optional[str]) -> Optional[tuple[str, ...]]:
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    parts = [part.strip() for part in text.split(",")]
    if any(not part or not part.isdigit() for part in parts):
        return None
    return tuple(parts)


def parse_control_channel(raw: Optional[str]) -> Optional[int]:
    text = (raw or "").strip()
    if not text or not text.isdigit():
        return None
    return int(text)


def authorize(
    *,
    channel_id: Optional[int],
    user_id: Optional[int],
    control_channel_id: Optional[str],
    allowlist_raw: Optional[str],
) -> str:
    if parse_allowlist(allowlist_raw) is None:
        return "CAPITAL_CONTROL_NOT_CONFIGURED"
    control = parse_control_channel(control_channel_id)
    if control is None:
        return "CONTROL_CHANNEL_NOT_CONFIGURED"
    if channel_id is None or int(channel_id) != control:
        return "CONTROL_CHANNEL_MISMATCH"
    allowed = parse_allowlist(allowlist_raw) or ()
    if user_id is None or str(int(user_id)) not in allowed:
        return "CAPITAL_CONTROL_DENIED"
    return "OK"


def _denial_reply(code: str, user_id: Optional[int]) -> str:
    caller = "null" if user_id is None else str(int(user_id))
    if code == "CAPITAL_CONTROL_NOT_CONFIGURED":
        return f"CAPITAL_CONTROL_NOT_CONFIGURED\ncaller_user_id: {caller}"
    if code == "CAPITAL_CONTROL_DENIED":
        return f"CAPITAL_CONTROL_DENIED\ncaller_user_id: {caller}"
    return code


@dataclass(frozen=True)
class BudgetView:
    status: str
    g_yen: Optional[int]
    base_capital_yen: Optional[int]
    max_leverage_multiplier: Optional[str]
    legacy_direct_g_yen: Optional[int]
    configured_at: Optional[str]
    source: Optional[str]
    policy_spec_sha: str
    policy_instance_sha: Optional[str]
    discord_user_id: Optional[str]
    discord_channel_id: Optional[str]
    discord_message_id: Optional[str]


def _blank(**overrides: Any) -> BudgetView:
    view = BudgetView(
        status="UNSET",
        g_yen=None,
        base_capital_yen=None,
        max_leverage_multiplier=None,
        legacy_direct_g_yen=None,
        configured_at=None,
        source=None,
        policy_spec_sha=policy_spec_sha(),
        policy_instance_sha=None,
        discord_user_id=None,
        discord_channel_id=None,
        discord_message_id=None,
    )
    if not overrides:
        return view
    data = view.__dict__.copy()
    data.update(overrides)
    return BudgetView(**data)


def _invalid_view() -> BudgetView:
    return _blank(status="BUDGET_STORE_INVALID")


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    rendered = str(value)
    return rendered or None


def _positive_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def load_budget(store_path: Path) -> BudgetView:
    """Broken JSON is not an official budget. A direct G is not split into capital and leverage."""
    path = Path(store_path)
    if not path.is_file():
        return _blank()
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return _invalid_view()
    if not isinstance(body, dict):
        return _invalid_view()
    if body.get("schema_version") == SCHEMA_VERSION_LEGACY and body.get("parameter") == PARAMETER:
        return _load_legacy(body)
    if body.get("schema_version") == SCHEMA_VERSION and body.get("model") == MODEL:
        return _load_model(body)
    return _invalid_view()


def _load_legacy(body: Mapping[str, Any]) -> BudgetView:
    g_yen = _positive_int(body.get("g_yen"))
    if g_yen is None:
        return _invalid_view()
    if body.get("policy_spec_sha") != policy_spec_sha():
        return _invalid_view()
    if body.get("policy_instance_sha") != legacy_direct_g_instance_sha(g_yen):
        return _invalid_view()
    return _blank(
        status=AWAIT_CAPITAL_LEVERAGE,
        legacy_direct_g_yen=g_yen,
        configured_at=_text(body.get("configured_at")),
        source=_text(body.get("source")),
        discord_user_id=_text(body.get("discord_user_id")),
        discord_channel_id=_text(body.get("discord_channel_id")),
        discord_message_id=_text(body.get("discord_message_id")),
    )


def _load_model(body: Mapping[str, Any]) -> BudgetView:
    if body.get("derived") not in (True, False):
        return _invalid_view()
    if body.get("policy_spec_sha") != policy_spec_sha():
        return _invalid_view()
    base = body.get("BASE_CAPITAL_YEN")
    leverage = body.get("MAX_LEVERAGE_MULTIPLIER")
    requested = body.get("REQUESTED_GROSS_EXPOSURE_BUDGET_YEN")
    legacy = body.get("legacy_direct_g_yen")
    if base is not None:
        base = _positive_int(base)
        if base is None:
            return _invalid_view()
    if leverage is not None:
        parsed = parse_leverage(str(leverage))
        if parsed is None or parsed != str(leverage):
            return _invalid_view()
        leverage = parsed
    if legacy is not None and _positive_int(legacy) is None:
        return _invalid_view()
    if base is None or leverage is None:
        if body.get("derived") is not False or requested is not None or body.get("policy_instance_sha") is not None:
            return _invalid_view()
        return _blank(
            status=AWAIT_CAPITAL_LEVERAGE,
            base_capital_yen=base,
            max_leverage_multiplier=leverage,
            legacy_direct_g_yen=legacy,
            configured_at=_text(body.get("configured_at")),
            source=_text(body.get("source")),
            discord_user_id=_text(body.get("discord_user_id")),
            discord_channel_id=_text(body.get("discord_channel_id")),
            discord_message_id=_text(body.get("discord_message_id")),
        )
    try:
        derived = derived_requested_gross_yen(base, leverage)
    except ValueError:
        return _invalid_view()
    instance = policy_instance_sha(base, leverage)
    if requested != derived or body.get("derived") is not True or body.get("policy_instance_sha") != instance:
        return _invalid_view()
    if paper_effective_g(derived) != derived:
        return _invalid_view()
    return _blank(
        status="STAGED",
        g_yen=derived,
        base_capital_yen=base,
        max_leverage_multiplier=leverage,
        legacy_direct_g_yen=legacy,
        configured_at=_text(body.get("configured_at")),
        source=_text(body.get("source")),
        policy_instance_sha=instance,
        discord_user_id=_text(body.get("discord_user_id")),
        discord_channel_id=_text(body.get("discord_channel_id")),
        discord_message_id=_text(body.get("discord_message_id")),
    )


def atomic_write_text(path: Path, text: str) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f"{destination.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _thread_lock(store_path: Path) -> threading.Lock:
    key = str(Path(store_path))
    with _THREAD_LOCKS_GUARD:
        lock = _THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _THREAD_LOCKS[key] = lock
        return lock


@contextmanager
def exclusive_budget_lock(store_path: Path) -> Iterator[None]:
    """One writer across threads and processes. The budget JSON itself is not the lock."""
    thread_lock = _thread_lock(store_path)
    thread_lock.acquire()
    handle = None
    try:
        lock_path = Path(store_path).with_name(Path(store_path).name + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+b")
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        if handle is not None:
            handle.close()
        thread_lock.release()


def message_already_recorded(audit_path: Path, message_id: Optional[str]) -> bool:
    if not message_id or not Path(audit_path).is_file():
        return False
    target = str(message_id)
    for line in Path(audit_path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(row.get("message_id") or "") == target:
            return True
    return False


def _restore_bytes(path: Path, previous: Optional[bytes]) -> None:
    destination = Path(path)
    if previous is None:
        destination.unlink(missing_ok=True)
        return
    temporary = destination.with_name(f"{destination.name}.{os.getpid()}.{threading.get_ident()}.restore.tmp")
    try:
        temporary.write_bytes(previous)
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _readback_matches(
    loaded: BudgetView,
    *,
    base_capital_yen: Optional[int],
    max_leverage_multiplier: Optional[str],
    g_yen: Optional[int],
    instance: Optional[str],
    status: str,
) -> bool:
    return (
        loaded.status == status
        and loaded.base_capital_yen == base_capital_yen
        and loaded.max_leverage_multiplier == max_leverage_multiplier
        and loaded.g_yen == g_yen
        and loaded.policy_instance_sha == instance
    )


def _append_audit(audit_path: Path, record: Mapping[str, Any]) -> None:
    path = Path(audit_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(dict(record), ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def _now_jst() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _compose(base: Optional[int], leverage: Optional[str]) -> tuple[Optional[int], Optional[str], str]:
    if base is None or leverage is None:
        return None, None, AWAIT_CAPITAL_LEVERAGE
    g_yen = derived_requested_gross_yen(base, leverage)
    return g_yen, policy_instance_sha(base, leverage), "STAGED"


def _failure_audit(audit: Mapping[str, Any]) -> dict[str, Any]:
    failed = dict(audit)
    failed["result"] = "BUDGET_UPDATE_FAILED"
    failed["policy_instance_sha"] = None
    return failed


def stage_model(
    *,
    base_capital_yen: Optional[int],
    max_leverage_multiplier: Optional[str],
    legacy_direct_g_yen: Optional[int],
    user_id: str,
    channel_id: str,
    message_id: str,
    store_path: Path,
    audit_path: Path,
    configured_at: Optional[str] = None,
    touch: str = "",
) -> BudgetView:
    """Write, read back, then audit. A failed step keeps the previous official file."""
    with exclusive_budget_lock(store_path):
        if message_already_recorded(audit_path, message_id):
            raise DuplicateCommand(str(message_id))
        previous = load_budget(store_path)
        if touch == "capital":
            max_leverage_multiplier = previous.max_leverage_multiplier
            legacy_direct_g_yen = previous.legacy_direct_g_yen
        elif touch == "leverage":
            base_capital_yen = previous.base_capital_yen
            legacy_direct_g_yen = previous.legacy_direct_g_yen
        previous_bytes = Path(store_path).read_bytes() if Path(store_path).is_file() else None
        stamp = configured_at or _now_jst()
        try:
            g_yen, instance, status = _compose(base_capital_yen, max_leverage_multiplier)
        except Exception as exc:
            _append_audit(
                audit_path,
                _failure_audit(
                    {
                        "timestamp": stamp,
                        "old_base_capital_yen": previous.base_capital_yen,
                        "new_base_capital_yen": base_capital_yen,
                        "old_max_leverage_multiplier": previous.max_leverage_multiplier,
                        "new_max_leverage_multiplier": max_leverage_multiplier,
                        "old_requested_g": previous.g_yen,
                        "new_requested_g": None,
                        "legacy_direct_g_yen": legacy_direct_g_yen,
                        "user_id": str(user_id),
                        "channel_id": str(channel_id),
                        "message_id": str(message_id),
                        "policy_instance_sha": None,
                        "result": "BUDGET_UPDATE_FAILED",
                    }
                ),
            )
            raise OSError("BUDGET_UPDATE_FAILED") from exc
        document = {
            "schema_version": SCHEMA_VERSION,
            "model": MODEL,
            "derived": status == "STAGED",
            "BASE_CAPITAL_YEN": base_capital_yen,
            "MAX_LEVERAGE_MULTIPLIER": max_leverage_multiplier,
            "REQUESTED_GROSS_EXPOSURE_BUDGET_YEN": g_yen,
            "legacy_direct_g_yen": legacy_direct_g_yen,
            "configured_at": stamp,
            "source": "DISCORD",
            "discord_user_id": str(user_id),
            "discord_channel_id": str(channel_id),
            "discord_message_id": str(message_id),
            "policy_spec_sha": policy_spec_sha(),
            "policy_instance_sha": instance,
        }
        audit = {
            "timestamp": stamp,
            "old_base_capital_yen": previous.base_capital_yen,
            "new_base_capital_yen": base_capital_yen,
            "old_max_leverage_multiplier": previous.max_leverage_multiplier,
            "new_max_leverage_multiplier": max_leverage_multiplier,
            "old_requested_g": previous.g_yen,
            "new_requested_g": g_yen,
            "legacy_direct_g_yen": legacy_direct_g_yen,
            "user_id": str(user_id),
            "channel_id": str(channel_id),
            "message_id": str(message_id),
            "policy_instance_sha": instance,
            "result": status,
        }
        try:
            atomic_write_text(store_path, json.dumps(document, ensure_ascii=True, indent=2) + "\n")
            loaded = load_budget(store_path)
        except OSError:
            _restore_bytes(store_path, previous_bytes)
            _append_audit(audit_path, _failure_audit(audit))
            raise
        if not _readback_matches(
            loaded,
            base_capital_yen=base_capital_yen,
            max_leverage_multiplier=max_leverage_multiplier,
            g_yen=g_yen,
            instance=instance,
            status=status,
        ):
            _restore_bytes(store_path, previous_bytes)
            _append_audit(audit_path, _failure_audit(audit))
            raise OSError("BUDGET_UPDATE_FAILED")
        try:
            _append_audit(audit_path, audit)
        except OSError:
            _restore_bytes(store_path, previous_bytes)
            raise
        return loaded


def format_yen(g_yen: int) -> str:
    return f"¥{int(g_yen):,}"


def _show_amount(value: Optional[int], *, await_status: bool) -> str:
    if value is None:
        return "AWAIT" if await_status else "UNSET"
    return format_yen(value)


def format_show(view: BudgetView, session_snapshot: Optional[Mapping[str, Any]] = None) -> str:
    waiting = view.status == AWAIT_CAPITAL_LEVERAGE
    derived = _show_amount(view.g_yen if view.status == "STAGED" else None, await_status=waiting)
    base = _show_amount(view.base_capital_yen, await_status=waiting and view.base_capital_yen is None)
    leverage = view.max_leverage_multiplier or ("AWAIT" if waiting else "UNSET")
    legacy = "null" if view.legacy_direct_g_yen is None else format_yen(view.legacy_direct_g_yen)
    instance = view.policy_instance_sha or "null"
    session_g = None
    if session_snapshot:
        raw = session_snapshot.get("SESSION_EFFECTIVE_G")
        if isinstance(raw, int) and not isinstance(raw, bool):
            session_g = raw
    session_text = "null" if session_g is None else str(session_g)
    pending = "null" if view.status != "STAGED" or view.g_yen is None else str(view.g_yen)
    v16 = "YES" if CURRENT_V16_USES_G else "NO"
    return (
        "【TradeBot Capital Budget】\n"
        "\n"
        "BASE_CAPITAL_YEN:\n"
        f"{base}\n"
        "\n"
        "MAX_LEVERAGE_MULTIPLIER:\n"
        f"{leverage}\n"
        "\n"
        "REQUESTED_GROSS_EXPOSURE_BUDGET_YEN:\n"
        f"{derived}\n"
        "\n"
        "status:\n"
        f"{view.status}\n"
        "\n"
        "legacy direct G:\n"
        f"{legacy}\n"
        "\n"
        "POLICY_SPEC_SHA:\n"
        f"{view.policy_spec_sha}\n"
        "\n"
        "POLICY_INSTANCE_SHA:\n"
        f"{instance}\n"
        "\n"
        "configured_at:\n"
        f"{view.configured_at or 'null'}\n"
        "\n"
        "source:\n"
        f"{view.source or 'null'}\n"
        "\n"
        "current-session effective G:\n"
        f"{session_text}\n"
        "\n"
        "pending G:\n"
        f"{pending}\n"
        "\n"
        "CURRENT V16 uses G:\n"
        f"{v16}\n"
        "\n"
        "runtime:\n"
        "NOT_USED_BY_CURRENT_RUNTIME\n"
    )


def format_model_set(view: BudgetView) -> str:
    derived = "AWAIT" if view.g_yen is None else format_yen(view.g_yen)
    base = "AWAIT" if view.base_capital_yen is None else format_yen(view.base_capital_yen)
    leverage = view.max_leverage_multiplier or "AWAIT"
    instance = view.policy_instance_sha or "null"
    return (
        "【TradeBot Capital Budget】\n"
        "\n"
        "BASE_CAPITAL_YEN:\n"
        f"{base}\n"
        "\n"
        "MAX_LEVERAGE_MULTIPLIER:\n"
        f"{leverage}\n"
        "\n"
        "SYSTEM_GROSS_EXPOSURE_BUDGET_YEN:\n"
        f"{derived}\n"
        "\n"
        "status:\n"
        f"{view.status}\n"
        "\n"
        "qty:\n"
        f"{QTY}\n"
        "\n"
        "MAX_CONCURRENT:\n"
        f"{MAX_CONCURRENT}\n"
        "\n"
        "per-position G/5:\n"
        "DISABLED\n"
        "\n"
        "effective:\n"
        "NEXT_SELECTOR_OR_SESSION_START\n"
        "\n"
        "current running session:\n"
        "UNCHANGED\n"
        "\n"
        "POLICY_INSTANCE_SHA:\n"
        f"{instance}\n"
    )


def _effective_parts(view: BudgetView) -> tuple[Optional[int], Optional[str], Optional[int], Optional[str]]:
    if view.status != "STAGED":
        return None, None, None, None
    return view.base_capital_yen, view.max_leverage_multiplier, view.g_yen, view.policy_instance_sha


def freeze_selector_effective(store_path: Path) -> dict[str, Any]:
    """One read at selection start. Later Discord writes stay out of this dict."""
    view = load_budget(store_path)
    base, leverage, g_yen, instance = _effective_parts(view)
    return {
        "SELECTOR_BASE_CAPITAL": base,
        "SELECTOR_LEVERAGE": leverage,
        "SELECTOR_EFFECTIVE_G": g_yen,
        "POLICY_INSTANCE_SHA": instance,
        "status": "EFFECTIVE_IN_SELECTOR" if g_yen is not None else view.status,
    }


def freeze_session_effective(store_path: Path) -> dict[str, Any]:
    """One read before a future session. Discord set does not call this."""
    view = load_budget(store_path)
    base, leverage, g_yen, instance = _effective_parts(view)
    return {
        "SESSION_BASE_CAPITAL": base,
        "SESSION_LEVERAGE": leverage,
        "SESSION_EFFECTIVE_G": g_yen,
        "POLICY_INSTANCE_SHA": instance,
        "status": "EFFECTIVE_IN_SESSION" if g_yen is not None else view.status,
        "immutable_after_start": True,
    }


def _auth_or_reply(
    *,
    channel_id: Optional[int],
    user_id: Optional[int],
    control_channel_id: Optional[str],
    allowlist_raw: Optional[str],
) -> Optional[dict[str, Any]]:
    code = authorize(
        channel_id=channel_id,
        user_id=user_id,
        control_channel_id=control_channel_id,
        allowlist_raw=allowlist_raw,
    )
    if code == "OK":
        return None
    return {"ok": False, "code": code, "reply": _denial_reply(code, user_id)}


def _ids(user_id: Optional[int], channel_id: Optional[int], message_id: Optional[str]) -> tuple[str, str, str]:
    return str(int(user_id)), str(int(channel_id)), str(message_id or "")


def handle_budget_command(
    action: str,
    amount: Optional[str] = None,
    *,
    channel_id: Optional[int],
    user_id: Optional[int],
    message_id: Optional[str],
    control_channel_id: Optional[str],
    allowlist_raw: Optional[str],
    store_path: Path,
    audit_path: Path,
    session_snapshot: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    denied = _auth_or_reply(
        channel_id=channel_id,
        user_id=user_id,
        control_channel_id=control_channel_id,
        allowlist_raw=allowlist_raw,
    )
    if denied is not None:
        return denied
    if action == "show":
        view = load_budget(store_path)
        return {"ok": True, "code": view.status, "reply": format_show(view, session_snapshot), "g_yen": view.g_yen}
    if action == "set":
        try:
            with exclusive_budget_lock(store_path):
                if message_already_recorded(audit_path, message_id):
                    raise DuplicateCommand(str(message_id or ""))
                _append_audit(
                    audit_path,
                    {
                        "timestamp": _now_jst(),
                        "message_id": str(message_id or ""),
                        "user_id": None if user_id is None else str(int(user_id)),
                        "channel_id": None if channel_id is None else str(int(channel_id)),
                        "result": DIRECT_G_SET_DISABLED,
                        "policy_instance_sha": None,
                    },
                )
        except DuplicateCommand:
            return {"ok": True, "code": "DUPLICATE_SUPPRESSED", "reply": None}
        return {"ok": False, "code": DIRECT_G_SET_DISABLED, "reply": DIRECT_G_SET_DISABLED}
    return {"ok": False, "code": "BUDGET_INPUT_REJECTED", "reply": "BUDGET_INPUT_REJECTED"}


def _stage_input(
    *,
    kind: str,
    amount: Optional[str],
    channel_id: Optional[int],
    user_id: Optional[int],
    message_id: Optional[str],
    store_path: Path,
    audit_path: Path,
) -> dict[str, Any]:
    previous = load_budget(store_path)
    if previous.status == "BUDGET_STORE_INVALID":
        return {"ok": False, "code": "BUDGET_STORE_INVALID", "reply": "BUDGET_STORE_INVALID"}
    base = previous.base_capital_yen
    leverage = previous.max_leverage_multiplier
    if kind == "capital":
        parsed = parse_budget_yen(amount or "")
        if parsed is None:
            return {"ok": False, "code": "BUDGET_INPUT_REJECTED", "reply": "BUDGET_INPUT_REJECTED"}
        base = parsed
    elif kind == "leverage":
        parsed_leverage = parse_leverage(amount or "")
        if parsed_leverage is None:
            return {"ok": False, "code": "BUDGET_INPUT_REJECTED", "reply": "BUDGET_INPUT_REJECTED"}
        leverage = parsed_leverage
    else:
        return {"ok": False, "code": "BUDGET_INPUT_REJECTED", "reply": "BUDGET_INPUT_REJECTED"}
    if base is not None and leverage is not None:
        try:
            derived_requested_gross_yen(base, leverage)
        except ValueError:
            return {"ok": False, "code": "DERIVED_G_NOT_WHOLE_YEN", "reply": "DERIVED_G_NOT_WHOLE_YEN"}
    actor, channel, message = _ids(user_id, channel_id, message_id)
    try:
        view = stage_model(
            base_capital_yen=base,
            max_leverage_multiplier=leverage,
            legacy_direct_g_yen=previous.legacy_direct_g_yen,
            user_id=actor,
            channel_id=channel,
            message_id=message,
            store_path=store_path,
            audit_path=audit_path,
            touch=kind,
        )
    except DuplicateCommand:
        return {"ok": True, "code": "DUPLICATE_SUPPRESSED", "reply": None}
    except OSError:
        return {"ok": False, "code": "BUDGET_UPDATE_FAILED", "reply": "BUDGET_UPDATE_FAILED"}
    return {
        "ok": True,
        "code": view.status,
        "reply": format_model_set(view),
        "g_yen": view.g_yen,
        "base_capital_yen": view.base_capital_yen,
        "max_leverage_multiplier": view.max_leverage_multiplier,
        "policy_instance_sha": view.policy_instance_sha,
    }


def handle_capital_command(
    action: str,
    amount: Optional[str] = None,
    *,
    channel_id: Optional[int],
    user_id: Optional[int],
    message_id: Optional[str],
    control_channel_id: Optional[str],
    allowlist_raw: Optional[str],
    store_path: Path,
    audit_path: Path,
    session_snapshot: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    denied = _auth_or_reply(
        channel_id=channel_id,
        user_id=user_id,
        control_channel_id=control_channel_id,
        allowlist_raw=allowlist_raw,
    )
    if denied is not None:
        return denied
    if action == "show":
        view = load_budget(store_path)
        return {"ok": True, "code": view.status, "reply": format_show(view, session_snapshot)}
    if action != "set":
        return {"ok": False, "code": "BUDGET_INPUT_REJECTED", "reply": "BUDGET_INPUT_REJECTED"}
    return _stage_input(
        kind="capital",
        amount=amount,
        channel_id=channel_id,
        user_id=user_id,
        message_id=message_id,
        store_path=store_path,
        audit_path=audit_path,
    )


def handle_leverage_command(
    action: str,
    amount: Optional[str] = None,
    *,
    channel_id: Optional[int],
    user_id: Optional[int],
    message_id: Optional[str],
    control_channel_id: Optional[str],
    allowlist_raw: Optional[str],
    store_path: Path,
    audit_path: Path,
    session_snapshot: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    denied = _auth_or_reply(
        channel_id=channel_id,
        user_id=user_id,
        control_channel_id=control_channel_id,
        allowlist_raw=allowlist_raw,
    )
    if denied is not None:
        return denied
    if action == "show":
        view = load_budget(store_path)
        return {"ok": True, "code": view.status, "reply": format_show(view, session_snapshot)}
    if action != "set":
        return {"ok": False, "code": "BUDGET_INPUT_REJECTED", "reply": "BUDGET_INPUT_REJECTED"}
    return _stage_input(
        kind="leverage",
        amount=amount,
        channel_id=channel_id,
        user_id=user_id,
        message_id=message_id,
        store_path=store_path,
        audit_path=audit_path,
    )
