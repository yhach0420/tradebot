"""Kabu registration ownership + actual-RegistList drift recovery (V6).

MARKET_INGRESS_V2 + websocket_owner=MARKET_INGRESS_SERVICE:
  mutation owner is MARKET_INGRESS_SERVICE only after Ingress spawn.
  Legacy/safety/daily/pilot unregister/all is forbidden post-commit.

Strategy / Precommit / ENTRY / EXIT / Model / Universe / Anchor are untouched.
submit/cancel/live remains 0/0/0.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, time as dt_time
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from small_paper.day_fixed_am_registration import (
    EXPECTED_SYMBOLS,
    canonical_membership_sha,
    canonical_symbols,
    load_am_canonical_50,
)
from small_paper.market_ingress_protocol import market_ingress_v2_enabled, now_iso

JST = ZoneInfo("Asia/Tokyo")

OWNER_MARKET_INGRESS = "MARKET_INGRESS_SERVICE"
OWNER_FILE_REL = Path("runtime") / "kabu_registration_owner.json"
ACTUAL_SNAPSHOT_REL = Path("runtime") / "kabu_actual_regist_snapshot.json"
AUDIT_NAME = "registration_authority_audit.jsonl"
SUMMARY_NAME = "registration_authority_summary.json"

POST_INGRESS_COMMIT_UNREGISTER_ALL = "POST_INGRESS_COMMIT_UNREGISTER_ALL"
REGISTRATION_DRIFT_DETECTED = "REGISTRATION_DRIFT_DETECTED"
REGISTRATION_DRIFT_REPUT = "REGISTRATION_DRIFT_REPUT"

PRE_WARMUP_CONNECTIVITY_PASS = "PRE_WARMUP_CONNECTIVITY_PASS"
PRE_WARMUP_CONNECTIVITY_FAIL = "PRE_WARMUP_CONNECTIVITY_FAIL"
LIVE_RUNTIME_FLOW_PASS = "LIVE_RUNTIME_FLOW_PASS"
LIVE_RUNTIME_FLOW_FAIL = "LIVE_RUNTIME_FLOW_FAIL"
EXPECTED_PRE_WARMUP = "EXPECTED_PRE_WARMUP"
PREMATURE_PRE_WARMUP_EXIT = "PREMATURE_PRE_WARMUP_EXIT"
PREMATURE_PRE_WARMUP_EXIT_CODE = 4
KABU_PROBE_SYMBOL_EVENT = "KABU_SAFETY_PROBE_SYMBOL"
LEGACY_BOARD_PROBE_SYMBOL = "9984@1"
NATIVE_HEARTBEAT_FRESH_SEC = 20.0

DEFAULT_AM_WARMUP_START = "08:50"


def owner_path(native_root: Path) -> Path:
    return Path(native_root) / OWNER_FILE_REL


def actual_snapshot_path(native_root: Path) -> Path:
    return Path(native_root) / ACTUAL_SNAPSHOT_REL


def _day_dir(native_root: Path, trading_date: str) -> Path:
    return Path(native_root) / "data" / "market_capture" / str(trading_date)


def _pid_alive(pid: int) -> bool:
    if int(pid or 0) <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.OpenProcess(0x1000, 0, int(pid))
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return False
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False
    except Exception:
        return False


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(dict(payload), ensure_ascii=False, indent=2, default=str) + "\n"
    fd, tmp = tempfile.mkstemp(prefix="reg_auth_", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_registration_owner(
    native_root: Path,
    *,
    trading_date: str,
    pid: int,
    ingress_session_id: str = "",
    committed: bool = False,
    synthetic: bool = False,
) -> dict[str, Any]:
    body = {
        "owner": OWNER_MARKET_INGRESS,
        "websocket_owner": OWNER_MARKET_INGRESS,
        "kabu_registration_mutation_owner": OWNER_MARKET_INGRESS,
        "MARKET_INGRESS_V2": True,
        "trading_date": str(trading_date),
        "pid": int(pid or 0),
        "ingress_session_id": str(ingress_session_id or ""),
        "committed": bool(committed),
        "synthetic": bool(synthetic),
        "updated_at": now_iso(),
        "submit_cancel_live": "0/0/0",
    }
    _atomic_write(owner_path(native_root), body)
    return body


def write_actual_regist_snapshot(
    native_root: Path,
    *,
    trading_date: str,
    symbols: Sequence[Any],
    source: str,
    generation: int = 0,
    extra: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    canon = canonical_symbols(list(symbols))
    body = {
        "trading_date": str(trading_date),
        "symbols": canon,
        "symbol_count": len(canon),
        "canonical_membership_sha": canonical_membership_sha(canon),
        "source": str(source),
        "generation": int(generation or 0),
        "fetched_at": now_iso(),
        "submit_cancel_live": "0/0/0",
    }
    if extra:
        body.update(dict(extra))
    _atomic_write(actual_snapshot_path(native_root), body)
    return body


def append_authority_audit(
    native_root: Path,
    trading_date: str,
    event: str,
    payload: Optional[Mapping[str, Any]] = None,
) -> None:
    row = {
        "at": now_iso(),
        "event": str(event),
        "trading_date": str(trading_date),
        **dict(payload or {}),
        "submit_cancel_live": "0/0/0",
    }
    path = _day_dir(native_root, trading_date) / AUDIT_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass
    _refresh_authority_summary(native_root, trading_date)


def _refresh_authority_summary(native_root: Path, trading_date: str) -> dict[str, Any]:
    path = _day_dir(native_root, trading_date) / AUDIT_NAME
    executed = 0
    blocked = 0
    drift = 0
    reput = 0
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            ev = str(row.get("event") or "")
            if ev == POST_INGRESS_COMMIT_UNREGISTER_ALL and row.get("executed"):
                executed += 1
            if ev == POST_INGRESS_COMMIT_UNREGISTER_ALL and row.get("blocked"):
                blocked += 1
            if ev == REGISTRATION_DRIFT_DETECTED:
                drift += 1
            if ev == REGISTRATION_DRIFT_REPUT:
                reput += 1
    summary = {
        POST_INGRESS_COMMIT_UNREGISTER_ALL: executed,
        "POST_INGRESS_COMMIT_UNREGISTER_ALL_BLOCKED": blocked,
        REGISTRATION_DRIFT_DETECTED: drift,
        REGISTRATION_DRIFT_REPUT: reput,
        "trading_date": str(trading_date),
        "updated_at": now_iso(),
    }
    try:
        (_day_dir(native_root, trading_date) / SUMMARY_NAME).write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except OSError:
        pass
    return summary


def post_ingress_unregister_executed_count(native_root: Path, trading_date: str) -> int:
    summary = _read_json(_day_dir(native_root, trading_date) / SUMMARY_NAME)
    if summary:
        return int(summary.get(POST_INGRESS_COMMIT_UNREGISTER_ALL) or 0)
    return int(_refresh_authority_summary(native_root, trading_date).get(POST_INGRESS_COMMIT_UNREGISTER_ALL) or 0)


def ingress_owns_kabu_registration(
    native_root: Path,
    trading_date: str,
    *,
    environ: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    """True only after Ingress spawn/commit — env flag alone is not ownership."""
    day = str(trading_date)
    v2 = market_ingress_v2_enabled(environ=environ)
    owner = _read_json(owner_path(native_root))
    owner_day = str(owner.get("trading_date") or "")
    owner_name = str(owner.get("owner") or owner.get("websocket_owner") or "")
    pid = int(owner.get("pid") or 0)
    committed = bool(owner.get("committed"))
    alive = _pid_alive(pid)
    live: list[dict[str, Any]] = []
    try:
        from small_paper.v1r_pbv2_duplicate_runtime import list_live_ingress

        live = list_live_ingress(trading_date=day, native_root=Path(native_root))
    except Exception:
        live = []

    session_man = _read_json(_day_dir(native_root, day) / "ingress_active_session.json")
    ws_owner = str(session_man.get("websocket_owner") or owner_name or "")
    session_pid_alive = _pid_alive(int(session_man.get("pid") or 0))

    owned = False
    reason = "not_owned"
    if owner_day == day and owner_name == OWNER_MARKET_INGRESS and (alive or committed):
        owned = True
        reason = "owner_file"
    elif v2 and live:
        owned = True
        reason = "live_ingress_process"
    elif v2 and ws_owner == OWNER_MARKET_INGRESS and session_pid_alive:
        owned = True
        reason = "session_manifest_websocket_owner"

    return {
        "owned": owned,
        "reason": reason,
        "MARKET_INGRESS_V2": v2,
        "websocket_owner": ws_owner or (OWNER_MARKET_INGRESS if owned else ""),
        "kabu_registration_mutation_owner": OWNER_MARKET_INGRESS if owned else "",
        "trading_date": day,
        "pid": pid,
        "committed": committed,
        "live_ingress_n": len(live),
    }


def forbid_post_ingress_unregister_all(
    native_root: Path,
    trading_date: str,
    *,
    caller: str,
    environ: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    """Block PUT /unregister/all after Ingress owns registration. Executed count stays 0."""
    info = ingress_owns_kabu_registration(native_root, trading_date, environ=environ)
    if not info.get("owned"):
        return {
            "blocked": False,
            "allow": True,
            "reason": "pre_ingress_or_not_owned",
            **info,
        }
    append_authority_audit(
        native_root,
        trading_date,
        POST_INGRESS_COMMIT_UNREGISTER_ALL,
        {
            "blocked": True,
            "executed": False,
            "caller": str(caller),
            "owner": info.get("reason"),
        },
    )
    return {
        "blocked": True,
        "allow": False,
        "reason": "INGRESS_OWNS_KABU_REGISTRATION",
        "caller": str(caller),
        POST_INGRESS_COMMIT_UNREGISTER_ALL: 0,
        **info,
    }


def extract_regist_symbols(resp: Any) -> list[str]:
    if isinstance(resp, Mapping):
        if resp.get("symbols") and not isinstance(resp.get("symbols"), (str, bytes)):
            raw = resp.get("symbols")
            if isinstance(raw, list) and raw and not isinstance(raw[0], dict):
                return canonical_symbols(raw)
        raw = resp.get("RegistList") or resp.get("Symbols") or resp.get("symbols") or []
        return canonical_symbols(list(raw) if isinstance(raw, list) else [])
    if isinstance(resp, (list, tuple)):
        return canonical_symbols(list(resp))
    return []


def fetch_kabu_regist_list(
    push: Any = None,
    *,
    rest_base_url: str = "",
    token: str = "",
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Readonly current Kabu RegistList. Never PUT / unregister."""
    if push is not None:
        fn = getattr(push, "fetch_regist_list", None)
        if callable(fn):
            try:
                out = fn()
            except Exception as exc:
                return {
                    "ok": False,
                    "readonly": True,
                    "reason": f"push_fetch_exc:{type(exc).__name__}",
                    "symbols": [],
                    "symbol_count": 0,
                }
            if isinstance(out, dict):
                if out.get("ok") is False:
                    return {
                        "ok": False,
                        "readonly": True,
                        "reason": str(out.get("reason") or "fetch_failed"),
                        "http_status": out.get("http_status"),
                        "symbols": extract_regist_symbols(out),
                        "symbol_count": 0,
                    }
                if "symbols" in out:
                    symbols = canonical_symbols(list(out.get("symbols") or []))
                else:
                    symbols = extract_regist_symbols(out)
                return {
                    "ok": True,
                    "readonly": True,
                    "reason": str(out.get("reason") or "push_fetch_regist_list"),
                    "http_status": out.get("http_status") or 200,
                    "symbols": symbols,
                    "symbol_count": len(symbols),
                }
        regist_attr = getattr(push, "regist", None)
        if isinstance(regist_attr, list):
            symbols = canonical_symbols(regist_attr)
            return {
                "ok": True,
                "readonly": True,
                "reason": "push_regist_attr",
                "symbols": symbols,
                "symbol_count": len(symbols),
            }

    base = str(rest_base_url or "").rstrip("/")
    if not base and push is not None:
        base = str(getattr(push, "_base_url", "") or getattr(getattr(push, "_rest", None), "base_url", "") or "").rstrip("/")
    key = str(token or getattr(push, "_token", "") or "")
    if not base or not key:
        return {
            "ok": False,
            "readonly": True,
            "reason": "no_readonly_endpoint",
            "symbols": [],
            "symbol_count": 0,
        }
    try:
        import requests

        url = f"{base}/register"
        resp = requests.get(url, headers={"X-API-KEY": key, "Content-Type": "application/json"}, timeout=float(timeout))
        http = int(resp.status_code)
        try:
            body = resp.json()
        except Exception:
            body = {}
        if http == 405:
            return {
                "ok": False,
                "readonly": True,
                "reason": "GET_NOT_SUPPORTED",
                "http_status": 405,
                "symbols": [],
                "symbol_count": 0,
            }
        if http >= 400 or not isinstance(body, dict):
            return {
                "ok": False,
                "readonly": True,
                "reason": f"GET_HTTP_{http}",
                "http_status": http,
                "symbols": [],
                "symbol_count": 0,
            }
        symbols = extract_regist_symbols(body)
        return {
            "ok": True,
            "readonly": True,
            "reason": "GET_register",
            "http_status": http,
            "symbols": symbols,
            "symbol_count": len(symbols),
            "response": body,
        }
    except Exception as exc:
        return {
            "ok": False,
            "readonly": True,
            "reason": f"GET_exc:{type(exc).__name__}",
            "symbols": [],
            "symbol_count": 0,
        }


def sets_exact(a: Sequence[Any], b: Sequence[Any]) -> bool:
    sa = set(canonical_symbols(a))
    sb = set(canonical_symbols(b))
    return sa == sb and len(sa) == len(canonical_symbols(a)) and len(sb) == len(canonical_symbols(b))


def verify_exact50_membership(
    native_root: Path,
    trading_date: str,
    *,
    actual_symbols: Optional[Sequence[Any]] = None,
    push: Any = None,
    require_actual_kabu: bool = True,
    allow_self_record_only: bool = False,
) -> dict[str, Any]:
    """AM == desired == manifest == actual Kabu. Self-record 50 is not READY if actual is empty."""
    day = str(trading_date)
    am = load_am_canonical_50(Path(native_root), day)
    from small_paper.day_fixed_am_registration import (
        FROZEN_AM_UNIVERSE_MISMATCH,
        load_frozen_am_universe,
    )
    from small_paper.ingress_control_channel import read_desired_universe
    from small_paper.market_capture_registration import read_registration_manifest

    desired_payload = read_desired_universe(Path(native_root), requested_trading_date=day) or {}
    man = read_registration_manifest(Path(native_root))
    am_set = set(canonical_symbols(list(am.get("symbols") or [])))
    des_set = set(canonical_symbols(list(desired_payload.get("symbols") or [])))
    man_set = set(canonical_symbols(list(man.get("registered_symbols") or man.get("actual_symbols") or [])))
    frozen = load_frozen_am_universe(Path(native_root), day)
    frozen_set = set(canonical_symbols(list(frozen.get("canonical_symbols") or [])))

    actual_src = "injected"
    if actual_symbols is None:
        fetched = fetch_kabu_regist_list(push) if push is not None else {"ok": False, "symbols": []}
        if fetched.get("ok"):
            actual_symbols = list(fetched.get("symbols") or [])
            actual_src = str(fetched.get("reason") or "kabu_readonly_get")
        else:
            snap = _read_json(actual_snapshot_path(native_root))
            snap_day = str(snap.get("trading_date") or "")
            snap_src = str(snap.get("source") or "")
            if (
                snap_day == day
                and snap_src in ("kabu_readonly_get", "kabu_put_response", "push_fetch_regist_list", "push_regist_attr")
            ):
                actual_symbols = list(snap.get("symbols") or [])
                actual_src = f"snapshot:{snap_src}"
            elif allow_self_record_only:
                actual_symbols = list(man_set)
                actual_src = "ingress_self_record"
            else:
                actual_symbols = []
                actual_src = str(fetched.get("reason") or "actual_unavailable")

    act_set = set(canonical_symbols(list(actual_symbols or [])))
    intersection = am_set & des_set & man_set & act_set
    am_only = sorted(am_set - act_set)
    kabu_only = sorted(act_set - am_set)
    self_record_n = int(man.get("registered_count") or man.get("actual_count") or len(man_set) or 0)

    exact = (
        len(am_set) == EXPECTED_SYMBOLS
        and am_set == set(des_set) == set(man_set) == set(act_set)
        and len(intersection) == EXPECTED_SYMBOLS
        and not am_only
        and not kabu_only
        and bool(am.get("ok"))
        and str(desired_payload.get("trading_date") or "") == day
        and str(desired_payload.get("source_trading_date") or day) == day
    )
    if frozen.get("present") and frozen.get("ok"):
        if am_set != frozen_set or (des_set and des_set != frozen_set):
            exact = False
    fail_reason = ""
    if frozen.get("present") and frozen.get("ok") and des_set and des_set != frozen_set:
        fail_reason = FROZEN_AM_UNIVERSE_MISMATCH
        exact = False
    elif require_actual_kabu and self_record_n == EXPECTED_SYMBOLS and not act_set:
        fail_reason = "actual_kabu_empty_self_record_mismatch"
        exact = False
    elif require_actual_kabu and not exact:
        fail_reason = "canonical_membership_mismatch"
    elif not require_actual_kabu and not (
        len(am_set) == EXPECTED_SYMBOLS and am_set == set(des_set)
    ):
        fail_reason = "am_desired_mismatch"

    return {
        "ok": bool(exact) if require_actual_kabu else (fail_reason == ""),
        "reason": fail_reason,
        "trading_date": day,
        "am_n": len(am_set),
        "desired_n": len(des_set),
        "manifest_n": len(man_set),
        "actual_n": len(act_set),
        "intersection": len(intersection),
        "am_only": am_only,
        "kabu_only": kabu_only,
        "actual_source": actual_src,
        "self_record_n": self_record_n,
        "canonical_membership_sha": canonical_membership_sha(am_set) if am_set else "",
        "authority": str(am.get("authority") or ""),
        "source_drift": bool(am.get("source_drift")),
        "submit_cancel_live": "0/0/0",
    }


def parse_hhmm(hhmm: str) -> dt_time:
    raw = str(hhmm or DEFAULT_AM_WARMUP_START).strip()
    parts = raw.split(":")
    return dt_time(int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)


def is_pre_warmup(
    *,
    now: Optional[datetime] = None,
    warmup_hhmm: str = DEFAULT_AM_WARMUP_START,
) -> bool:
    n = now or datetime.now(JST)
    if n.tzinfo is None:
        n = n.replace(tzinfo=JST)
    else:
        n = n.astimezone(JST)
    return n.time() < parse_hhmm(warmup_hhmm)


def evaluate_pre_warmup_connectivity(
    *,
    kabu_token_ok: bool,
    same_day_am50: bool,
    actual_kabu_exact50: bool,
    ingress_resident: bool,
    receiver_resident: bool,
    registration_drift: bool,
    post_registration_unregister: int,
    submit_cancel_live: str = "0/0/0",
    consumer_connected: bool = False,
    paper_consumer_last_ack: int = 0,
    wait_until_session: bool = False,
    paper_exit_code: int = 0,
    paper_ok: bool = True,
) -> dict[str, Any]:
    """07:xx connectivity. Pilot consumer ACK is not required."""
    consumer_note = ""
    if wait_until_session and not consumer_connected and int(paper_consumer_last_ack or 0) == 0:
        consumer_note = EXPECTED_PRE_WARMUP
    blockers: list[str] = []
    if int(paper_exit_code or 0) != 0 or not paper_ok:
        blockers.append("paper_exit_nonzero")
    if not kabu_token_ok:
        blockers.append("kabu_token")
    if not same_day_am50:
        blockers.append("same_day_am50")
    if not actual_kabu_exact50:
        blockers.append("actual_kabu_exact50")
    if not ingress_resident:
        blockers.append("ingress_resident")
    if not receiver_resident:
        blockers.append("receiver_resident")
    if registration_drift:
        blockers.append("registration_drift")
    if int(post_registration_unregister or 0) != 0:
        blockers.append(POST_INGRESS_COMMIT_UNREGISTER_ALL)
    if str(submit_cancel_live) != "0/0/0":
        blockers.append("submit_cancel_live")
    ok = not blockers
    return {
        "ok": ok,
        "verdict": PRE_WARMUP_CONNECTIVITY_PASS if ok else PRE_WARMUP_CONNECTIVITY_FAIL,
        "blockers": blockers,
        "consumer_connected": bool(consumer_connected),
        "paper_consumer_last_ack": int(paper_consumer_last_ack or 0),
        "consumer_status": consumer_note or ("connected" if consumer_connected else "not_connected"),
        "submit_cancel_live": str(submit_cancel_live),
    }


def evaluate_live_runtime_flow(
    *,
    consumer_connected: bool,
    consumer_ready: bool,
    transport: str,
    raw_forward: bool,
    publisher_forward: bool,
    ack_forward_or_catchup: bool,
    heartbeat_continuous: bool,
    native_ready: bool,
    primary_resident: bool,
    submit_cancel_live: str = "0/0/0",
) -> dict[str, Any]:
    """08:50+ runtime flow. Consumer TCP + ACK + heartbeat are required."""
    blockers: list[str] = []
    if not consumer_connected:
        blockers.append("consumer_connected")
    if not consumer_ready:
        blockers.append("consumer_ready")
    if str(transport or "").upper() != "TCP":
        blockers.append("transport_tcp")
    if not raw_forward:
        blockers.append("raw_sequence_forward")
    if not publisher_forward:
        blockers.append("publisher_forward")
    if not ack_forward_or_catchup:
        blockers.append("ack_forward")
    if not heartbeat_continuous:
        blockers.append("heartbeat_continuous")
    if not native_ready:
        blockers.append("native_ready")
    if not primary_resident:
        blockers.append("primary_resident")
    if str(submit_cancel_live) != "0/0/0":
        blockers.append("submit_cancel_live")
    ok = not blockers
    return {
        "ok": ok,
        "verdict": LIVE_RUNTIME_FLOW_PASS if ok else LIVE_RUNTIME_FLOW_FAIL,
        "blockers": blockers,
        "submit_cancel_live": str(submit_cancel_live),
    }


def _bare_board_symbol(symbol: Any) -> str:
    got = canonical_symbols([symbol])
    key = got[0] if got else ""
    if "@" in key:
        key = key.split("@", 1)[0]
    return key


def board_symbol_key(symbol: Any) -> str:
    bare = _bare_board_symbol(symbol)
    return f"{bare}@1" if bare else ""


def select_registration_safe_probe_symbol(
    native_root: Path,
    trading_date: str,
    *,
    actual_symbols: Optional[Sequence[Any]] = None,
    proposed_symbol: Optional[str] = None,
    push: Any = None,
    environ: Optional[Mapping[str, str]] = None,
    write_audit: bool = True,
) -> dict[str, Any]:
    """Pick a readonly board probe symbol from actual registered ∩ AM canonical50.

    Never registers a 51st symbol. Never unregisters. Forbidden if proposed
    symbol is outside the actual registered canonical set.
    """
    day = str(trading_date)
    info = ingress_owns_kabu_registration(native_root, day, environ=environ)
    owned = bool(info.get("owned"))
    mutations = 0
    am = load_am_canonical_50(Path(native_root), day)
    am_set = {_bare_board_symbol(s) for s in (am.get("symbols") or []) if _bare_board_symbol(s)}

    actual_src = "injected"
    if actual_symbols is None:
        fetched = fetch_kabu_regist_list(push) if push is not None else {"ok": False, "symbols": []}
        if fetched.get("ok"):
            actual_symbols = list(fetched.get("symbols") or [])
            actual_src = str(fetched.get("reason") or "kabu_readonly_get")
        else:
            snap = _read_json(actual_snapshot_path(native_root))
            if str(snap.get("trading_date") or "") == day:
                actual_symbols = list(snap.get("symbols") or [])
                actual_src = f"snapshot:{snap.get('source') or 'file'}"
            else:
                actual_symbols = []
                actual_src = "actual_unavailable"

    act_set = {_bare_board_symbol(s) for s in (actual_symbols or []) if _bare_board_symbol(s)}
    exact50 = (
        len(am_set) == EXPECTED_SYMBOLS
        and len(act_set) == EXPECTED_SYMBOLS
        and am_set == act_set
        and bool(am.get("ok"))
    )

    proposed_bare = _bare_board_symbol(proposed_symbol) if proposed_symbol else ""
    if proposed_bare:
        if proposed_bare not in act_set:
            out = {
                "ok": False,
                "reason": "probe_symbol_not_in_actual_registered_set",
                "kabu_probe_symbol": board_symbol_key(proposed_bare),
                "kabu_probe_symbol_registered": False,
                "owned": owned,
                "exact50": exact50,
                "actual_n": len(act_set),
                "am_n": len(am_set),
                "actual_source": actual_src,
                "registration_mutation": mutations,
                "submit_cancel_live": "0/0/0",
            }
            if write_audit:
                append_authority_audit(native_root, day, KABU_PROBE_SYMBOL_EVENT, out)
            return out

    use_legacy = not owned

    if use_legacy and not proposed_bare:
        out = {
            "ok": True,
            "reason": "legacy_empty_register_probe",
            "symbol_key": LEGACY_BOARD_PROBE_SYMBOL,
            "kabu_probe_symbol": LEGACY_BOARD_PROBE_SYMBOL,
            "kabu_probe_symbol_registered": False,
            "owned": owned,
            "exact50": exact50,
            "actual_n": len(act_set),
            "am_n": len(am_set),
            "actual_source": actual_src,
            "registration_mutation": mutations,
            "submit_cancel_live": "0/0/0",
        }
        if write_audit:
            append_authority_audit(native_root, day, KABU_PROBE_SYMBOL_EVENT, out)
        return out

    if not exact50:
        out = {
            "ok": False,
            "reason": "actual_registered_exact50_required",
            "kabu_probe_symbol": "",
            "kabu_probe_symbol_registered": False,
            "owned": owned,
            "exact50": exact50,
            "actual_n": len(act_set),
            "am_n": len(am_set),
            "actual_source": actual_src,
            "registration_mutation": mutations,
            "submit_cancel_live": "0/0/0",
        }
        if write_audit:
            append_authority_audit(native_root, day, KABU_PROBE_SYMBOL_EVENT, out)
        return out

    pick_bare = proposed_bare or next(
        (_bare_board_symbol(s) for s in (am.get("symbols") or []) if _bare_board_symbol(s) in act_set),
        "",
    )
    if not pick_bare or pick_bare not in act_set:
        out = {
            "ok": False,
            "reason": "probe_symbol_not_in_actual_registered_set",
            "kabu_probe_symbol": board_symbol_key(pick_bare) if pick_bare else "",
            "kabu_probe_symbol_registered": False,
            "owned": owned,
            "exact50": exact50,
            "actual_n": len(act_set),
            "am_n": len(am_set),
            "actual_source": actual_src,
            "registration_mutation": mutations,
            "submit_cancel_live": "0/0/0",
        }
        if write_audit:
            append_authority_audit(native_root, day, KABU_PROBE_SYMBOL_EVENT, out)
        return out

    symbol_key = board_symbol_key(pick_bare)
    out = {
        "ok": True,
        "reason": "",
        "symbol_key": symbol_key,
        "kabu_probe_symbol": symbol_key,
        "kabu_probe_symbol_registered": True,
        "owned": owned,
        "exact50": True,
        "actual_n": len(act_set),
        "am_n": len(am_set),
        "actual_source": actual_src,
        "registration_mutation": mutations,
        "submit_cancel_live": "0/0/0",
    }
    if write_audit:
        append_authority_audit(native_root, day, KABU_PROBE_SYMBOL_EVENT, out)
    return out


def classify_pre_warmup_process_exit(
    exit_code: int,
    *,
    now: Optional[datetime] = None,
    warmup_hhmm: str = DEFAULT_AM_WARMUP_START,
) -> dict[str, Any]:
    """Before warmup/session start, any daily/paper return is FAIL.

    code != 0 → keep that code (startup FAIL).
    code == 0 → PREMATURE_PRE_WARMUP_EXIT (not a normal session).
    Clock vs warmup start is the SoT; elapsed-seconds is not.
    """
    pre = is_pre_warmup(now=now, warmup_hhmm=warmup_hhmm)
    code = int(exit_code)
    if not pre:
        return {
            "fail": False,
            "reason": "",
            "exit_code": code,
            "pre_warmup": False,
            "child_exit_code": code,
        }
    if code == 0:
        return {
            "fail": True,
            "reason": PREMATURE_PRE_WARMUP_EXIT,
            "exit_code": int(PREMATURE_PRE_WARMUP_EXIT_CODE),
            "pre_warmup": True,
            "child_exit_code": 0,
        }
    return {
        "fail": True,
        "reason": "PRE_WARMUP_STARTUP_FAIL",
        "exit_code": code,
        "pre_warmup": True,
        "child_exit_code": code,
    }


def evaluate_native_runtime_ready(
    *,
    native_boot_ready: bool,
    primary_resident: bool,
    heartbeat_fresh: bool,
    heartbeat_age_sec: Optional[float] = None,
) -> dict[str, Any]:
    """Runtime READY is not native_entry_boot.json alone.

    Requires boot ready AND owning Primary resident AND heartbeat fresh.
    After Primary death, a stale ready=true boot file is not READY.
    """
    blockers: list[str] = []
    if not native_boot_ready:
        blockers.append("native_boot_not_ready")
    if not primary_resident:
        blockers.append("primary_not_resident")
    if not heartbeat_fresh:
        blockers.append("heartbeat_stale")
    ok = not blockers
    return {
        "ok": ok,
        "ready": ok,
        "blockers": blockers,
        "native_boot_ready": bool(native_boot_ready),
        "primary_resident": bool(primary_resident),
        "heartbeat_fresh": bool(heartbeat_fresh),
        "heartbeat_age_sec": heartbeat_age_sec,
        "stale_boot_rejected": bool(native_boot_ready) and not ok,
        "submit_cancel_live": "0/0/0",
    }


def heartbeat_is_fresh(
    heartbeat_ts: Optional[datetime],
    *,
    now: Optional[datetime] = None,
    max_age_sec: float = NATIVE_HEARTBEAT_FRESH_SEC,
) -> tuple[bool, Optional[float]]:
    if heartbeat_ts is None:
        return False, None
    n = now or datetime.now(JST)
    ts = heartbeat_ts
    if n.tzinfo is None:
        n = n.replace(tzinfo=JST)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=JST)
    age = (n - ts).total_seconds()
    return 0.0 <= age <= float(max_age_sec), age
