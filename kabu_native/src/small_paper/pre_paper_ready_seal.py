"""Atomic pre-paper ready seal.

Startup fail-close only. Does not implement FULL, ENTRY, EXIT, or universe policy.
A seal authorizes Paper only for the same startup that just proved every gate.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

FAIL_SEAL_INVALID = "PRE_PAPER_READY_SEAL_INVALID"
FAIL_UNIVERSE_UNAVAILABLE = "PRODUCTION_UNIVERSE_UNAVAILABLE"
FAIL_BINDING = "PAPER_EXECUTOR_BINDING_CONTRADICTION"
CLASS_CERT_ONLY = "CERT_ONLY"
CLASS_PAPER_START = "PAPER_START"
OWNER = "FixedSupportX1SessionExecutor"
FAMILY = "X1_IMMEDIATE_ASK"
CLEAN = "CURRENT_CLEAN"
SYNTHETIC_CODES = frozenset(str(n) for n in range(7200, 7250))
# Six incidental real listings in this series have occurred together.
# Ten or more means the placeholder series itself is present.
FIXTURE_FRAGMENT_MIN = 10
SYNTHETIC_PROVENANCE_REASONS = frozenset(
    {"synthetic_universe", "synthetic_fallback", "synthetic_skip"}
)


def seal_path(native_root: Path, trading_date: str) -> Path:
    return Path(native_root) / "runtime" / f"pre_paper_ready_{trading_date}.json"


def bare_codes(symbols: Sequence[Any]) -> list[str]:
    out: list[str] = []
    for raw in symbols:
        text = str(raw or "").replace(".T", "").split("@", 1)[0].strip()
        if text:
            out.append(text)
    return out


def synthetic_fixture_overlap(symbols: Sequence[Any]) -> set[str]:
    return set(bare_codes(symbols)) & SYNTHETIC_CODES


def is_synthetic_fixture_identity(symbols: Sequence[Any]) -> bool:
    """True for the generated 7200+i fixture, a placeholder-only fragment, or a malformed near-copy.

    A real Core10+Dynamic40 universe that also contains 7220.T is not this identity.
    """
    codes = set(bare_codes(symbols))
    overlap = codes & SYNTHETIC_CODES
    if not overlap:
        return False
    if codes <= SYNTHETIC_CODES:
        return True
    return len(overlap) >= FIXTURE_FRAGMENT_MIN


def is_synthetic_fixture(symbols: Sequence[Any]) -> bool:
    """Exact placeholder set 7200..7249."""
    return set(bare_codes(symbols)) == SYNTHETIC_CODES


def synthetic_codes(symbols: Sequence[Any]) -> list[str]:
    if not is_synthetic_fixture_identity(symbols):
        return []
    return sorted(synthetic_fixture_overlap(symbols))


def production_universe_unavailable(
    symbols: Sequence[Any],
    *,
    reason: str = "",
    synthetic_provenance: bool = False,
) -> str:
    if synthetic_provenance or str(reason or "") in SYNTHETIC_PROVENANCE_REASONS:
        return FAIL_UNIVERSE_UNAVAILABLE
    if is_synthetic_fixture_identity(symbols):
        return FAIL_UNIVERSE_UNAVAILABLE
    return ""


def _pid_alive(pid: int) -> bool:
    if int(pid or 0) <= 0:
        return False
    try:
        from small_paper.kabu_token_authority import _pid_alive as alive

        return bool(alive(int(pid)))
    except Exception:
        return False


def load_seal(native_root: Path, trading_date: str) -> Optional[dict[str, Any]]:
    path = seal_path(native_root, trading_date)
    if not path.is_file():
        return None
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return body if isinstance(body, dict) else None


def _int(value: Any, default: Optional[int] = None) -> Optional[int]:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def seal_conditions_pass(body: Mapping[str, Any]) -> bool:
    """True only when every recorded gate is an explicit pass. Unreadable is not zero."""
    if body.get("synthetic_universe") or body.get("synthetic_fallback_used"):
        return False
    if synthetic_codes(list(body.get("universe_symbols") or [])):
        return False
    counts = (
        _int(body.get("universe_n")),
        _int(body.get("desired_n")),
        _int(body.get("registered_n")),
    )
    if counts != (50, 50, 50):
        return False
    if not body.get("symbol_set_match") or not body.get("exact50"):
        return False
    if str(body.get("current_reconciliation") or "") != CLEAN:
        return False
    if body.get("recovery_ready") is not True:
        return False
    if body.get("recovery_readable") is not True:
        return False
    broker_counts = (
        body.get("broker_position_n"),
        body.get("active_order_n"),
        body.get("local_only_n"),
        body.get("quantity_mismatch_n"),
        body.get("broker_only_n"),
    )
    if any(item is None for item in broker_counts):
        return False
    if any(_int(item) != 0 for item in broker_counts):
        return False
    if str(body.get("session_owner_class") or "") != OWNER:
        return False
    if str(body.get("admission_owner_class") or "") != OWNER:
        return False
    if str(body.get("portfolio_owner_class") or "") != OWNER:
        return False
    if str(body.get("execution_family") or "") != FAMILY:
        return False
    if not body.get("ledger_path_bound") or not body.get("notifier_bound") or not body.get("x1_counters_bound"):
        return False
    if not body.get("same_persistent_executor"):
        return False
    if _int(body.get("boot_v1r_native_entry_n"), 1) != 0:
        return False
    if _int(body.get("passive_fill_n"), 1) != 0:
        return False
    if _int(body.get("v1r_pending_n"), 1) != 0:
        return False
    if (_int(body.get("real_submit"), 1), _int(body.get("real_cancel"), 1), _int(body.get("live_order_calls"), 1)) != (0, 0, 0):
        return False
    if not body.get("token_issued") or not body.get("token_authority_valid"):
        return False
    if not str(body.get("ingress_owner") or "") :
        return False
    if _int(body.get("ingress_pid"), 0) <= 0:
        return False
    if _int(body.get("token_generation"), 0) <= 0:
        return False
    if not str(body.get("activation_id") or "") or not str(body.get("activation_sha") or ""):
        return False
    if not str(body.get("universe_membership_sha") or "") or not str(body.get("startup_run_id") or ""):
        return False
    if str(body.get("entry_sha") or "") == "" or str(body.get("exit_sha") or "") == "":
        return False
    return True


def write_seal(native_root: Path, body: Mapping[str, Any]) -> dict[str, Any]:
    """Write the seal. ready=true is refused unless every condition passes."""
    payload = dict(body)
    classification = str(payload.get("classification") or "")
    if classification == CLASS_CERT_ONLY:
        payload["ready"] = False
        payload["invalid_for_paper_start"] = True
    elif classification == CLASS_PAPER_START and seal_conditions_pass(payload):
        payload["ready"] = True
        payload["invalid_for_paper_start"] = False
    else:
        payload["ready"] = False
        payload["invalid_for_paper_start"] = True
        payload["classification"] = classification or "NOT_READY"
    day = str(payload.get("trading_date") or "")
    path = seal_path(native_root, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    payload["path"] = str(path)
    return payload


def authorize_paper_launch(
    seal: Optional[Mapping[str, Any]],
    current: Mapping[str, Any],
    *,
    pid_alive: Optional[Callable[[int], bool]] = None,
) -> tuple[bool, str]:
    """Paper may start only when this startup's seal still matches the live authority."""
    alive = pid_alive or _pid_alive
    if not seal:
        return False, FAIL_SEAL_INVALID
    if seal.get("ready") is not True or seal.get("invalid_for_paper_start") or str(seal.get("classification") or "") != CLASS_PAPER_START:
        return False, FAIL_SEAL_INVALID
    if not seal_conditions_pass(seal):
        return False, FAIL_SEAL_INVALID
    checks = (
        ("trading_date", seal.get("trading_date"), current.get("trading_date")),
        ("startup_run_id", seal.get("startup_run_id"), current.get("startup_run_id")),
        ("activation_id", seal.get("activation_id"), current.get("activation_id")),
        ("activation_sha", seal.get("activation_sha"), current.get("activation_sha")),
        ("universe_membership_sha", seal.get("universe_membership_sha"), current.get("universe_membership_sha")),
        ("ingress_pid", _int(seal.get("ingress_pid")), _int(current.get("ingress_pid"))),
        ("token_generation", _int(seal.get("token_generation")), _int(current.get("token_generation"))),
    )
    for _name, left, right in checks:
        if left != right or left in ("", None):
            return False, FAIL_SEAL_INVALID
    if not alive(int(seal.get("ingress_pid") or 0)):
        return False, FAIL_SEAL_INVALID
    if _int(seal.get("registered_n")) != 50 or not seal.get("exact50"):
        return False, FAIL_SEAL_INVALID
    if str(seal.get("current_reconciliation") or "") != CLEAN or seal.get("recovery_ready") is not True:
        return False, FAIL_SEAL_INVALID
    if (_int(current.get("real_submit"), 1), _int(current.get("real_cancel"), 1), _int(current.get("live_order_calls"), 1)) != (0, 0, 0):
        return False, FAIL_SEAL_INVALID
    return True, ""


def notify_ready_once(seal: Mapping[str, Any], *, notifier: Any = None) -> bool:
    """One READY notice. Failure does not change the seal or the strategy gate."""
    if seal.get("ready") is not True or str(seal.get("classification") or "") != CLASS_PAPER_START:
        return False
    if seal.get("ready_notified"):
        return False
    lines = [
        "[FIXED SUPPORT PAPER READY]",
        f"date {seal.get('trading_date')}",
        f"activation {seal.get('activation_id')}",
        "Universe 50/50",
        "EXACT50 PASS",
        "Recovery CURRENT_CLEAN",
        "X1_IMMEDIATE_ASK",
        "ledger bound",
        "notifier bound",
        "submit/cancel/live 0/0/0",
        "PROSPECTIVE_DAY1_CANDIDATE",
    ]
    try:
        note = notifier
        if note is None:
            return False
        if hasattr(note, "_post"):
            return bool(
                note._post(
                    event_tag="FIXED_SUPPORT_PAPER_READY",
                    title_line="[FIXED SUPPORT PAPER READY]",
                    fields=[{"name": "status", "value": "\n".join(lines)[:900], "inline": False}],
                    color=0x2F855A,
                    dedupe_key=f"fixed-support-ready|{seal.get('trading_date')}|{seal.get('startup_run_id')}",
                    route_source="FixedSupportX1SessionExecutor",
                    route_activation_id=str(seal.get("activation_id") or ""),
                    route_family="X1_IMMEDIATE_ASK",
                )
            )
    except Exception:
        return False
    return False


def prove_exact50(
    native_root: Path,
    trading_date: str,
    desired_symbols: Sequence[Any],
) -> dict[str, Any]:
    """Readonly recheck after PUT. Count 50 is not enough; the symbol sets must match."""
    from api.rest_client import default_base_url
    from small_paper.kabu_registration_authority import (
        canonical_symbols,
        fetch_kabu_regist_list,
        verify_exact50_membership,
    )
    from small_paper.operational_recovery import acquire_published_readonly_token

    desired = canonical_symbols(list(desired_symbols))
    token, err, _waited = acquire_published_readonly_token(
        native_root=Path(native_root),
        trading_date=str(trading_date),
    )
    if not token:
        return {
            "ok": False,
            "reason": err or "TokenUnavailable",
            "http_status": None,
            "desired_n": len(desired),
            "registered_n": None,
            "symbol_set_match": False,
            "exact50": False,
        }
    fetched = fetch_kabu_regist_list(rest_base_url=default_base_url(), token=token)
    source = "GET_register"
    if not fetched.get("ok") and str(fetched.get("reason") or "") == "GET_NOT_SUPPORTED":
        trace_path = Path(native_root) / "data" / "market_capture" / str(trading_date) / "ingress_register_api_trace.json"
        trace: dict[str, Any] = {}
        if trace_path.is_file():
            try:
                loaded = json.loads(trace_path.read_text(encoding="utf-8"))
                trace = loaded if isinstance(loaded, dict) else {}
            except Exception:
                trace = {}
        body = trace.get("response_body") if isinstance(trace.get("response_body"), dict) else {}
        fetched = {
            "ok": bool(trace.get("put_executed") and int(trace.get("http_status") or 0) == 200),
            "reason": "PUT_response_recheck",
            "http_status": trace.get("http_status"),
            "symbols": canonical_symbols(
                [
                    str((row.get("Symbol") or row.get("symbol") or ""))
                    if isinstance(row, dict)
                    else str(row)
                    for row in list(body.get("RegistList") or [])
                ]
            ),
        }
        source = "PUT_response_http_200"
    actual = canonical_symbols(list(fetched.get("symbols") or []))
    match = len(desired) == 50 and len(actual) == 50 and set(actual) == set(desired) and len(set(actual)) == 50
    membership = verify_exact50_membership(
        Path(native_root),
        str(trading_date),
        actual_symbols=actual,
        require_actual_kabu=True,
        allow_self_record_only=False,
    )
    http = fetched.get("http_status")
    ok = bool(fetched.get("ok") and match and membership.get("ok") and http == 200)
    return {
        "ok": ok,
        "reason": "" if ok else str(fetched.get("reason") or membership.get("reason") or "exact50_failed"),
        "http_status": http,
        "desired_n": len(desired),
        "registered_n": len(actual) if fetched.get("ok") else None,
        "symbol_set_match": match,
        "exact50": bool(membership.get("ok") and match and http == 200),
        "registered_symbols": actual,
        "actual_source": source,
    }


def prove_fresh_recovery(native_root: Path) -> dict[str, Any]:
    """New readonly broker GET. Unreadable stays unreadable and is not counted as zero."""
    from small_paper.operational_recovery import read_current_readonly_broker_book

    book = read_current_readonly_broker_book(native_root=Path(native_root))
    if not book.get("readable"):
        return {
            "readable": False,
            "current_reconciliation": "CURRENT_UNREADABLE",
            "recovery_ready": False,
            "error": str(book.get("error") or "CURRENT_UNREADABLE"),
            "broker_position_n": None,
            "active_order_n": None,
            "broker_only_n": None,
            "local_only_n": None,
            "quantity_mismatch_n": None,
            "real_submit": int(book.get("submit_calls") or 0),
            "real_cancel": int(book.get("cancel_calls") or 0),
            "live_order_calls": int(book.get("live_order_calls") or 0),
            "query_timestamp": now_stamp(),
        }
    positions = book.get("positions") or {}
    pos_n = len([k for k, v in positions.items() if int(v or 0) > 0])
    order_n = len(book.get("active_order_ids") or [])
    clean = pos_n == 0 and order_n == 0
    return {
        "readable": True,
        "current_reconciliation": CLEAN if clean else "CURRENT_UNRESOLVED",
        "recovery_ready": clean,
        "error": "",
        "broker_position_n": pos_n,
        "active_order_n": order_n,
        "broker_only_n": pos_n,
        "local_only_n": 0,
        "quantity_mismatch_n": 0,
        "real_submit": int(book.get("submit_calls") or 0),
        "real_cancel": int(book.get("cancel_calls") or 0),
        "live_order_calls": int(book.get("live_order_calls") or 0),
        "query_timestamp": now_stamp(),
    }


def prove_x1_binding(
    *,
    native_root: Path,
    trading_date: str,
    symbols: Sequence[Any],
    bind_dir: Path,
    notifier: Any,
) -> dict[str, Any]:
    """Same production init the live executor uses. Does not start Paper."""
    from types import SimpleNamespace

    from small_paper.paper_session_executor import arm_x1_live_owner
    from small_paper.pilot_runner import _init_v1r_native_entry_for_live, _resolve_ctx_executor

    os.environ["TRADEBOT_X1_SESSION_OWNER"] = "1"
    arm_x1_live_owner()
    state = SimpleNamespace(
        trading_date=str(trading_date),
        v1r_day_fixed_universe=list(symbols),
        v1r_native_entry_blocked=False,
        v1r_native_block_reason="",
        paper_session_executor=None,
        session_owner_class="",
    )
    writer = SimpleNamespace(output_dir=Path(bind_dir))
    wiring = _init_v1r_native_entry_for_live(
        state=state,
        writer=writer,
        native_root=Path(native_root),
        trading_date=str(trading_date),
        session_symbols=list(symbols),
        notifier=notifier,
    )
    exe = state.paper_session_executor
    ctx = SimpleNamespace(state=state, native_root=Path(native_root))
    same = exe is not None and exe is state.paper_session_executor and exe is _resolve_ctx_executor(ctx)
    family = str(wiring.get("execution_family") or "")
    return {
        "ok": bool(
            same
            and wiring.get("boot_v1r_native_entry") == 0
            and family == FAMILY
            and wiring.get("session_owner_class") == OWNER
            and getattr(exe, "ledger_path", None)
            and getattr(exe, "notifier", None) is not None
            and hasattr(exe, "x1_counters")
        ),
        "session_owner_class": wiring.get("session_owner_class"),
        "admission_owner_class": wiring.get("admission_owner_class"),
        "portfolio_owner_class": wiring.get("portfolio_owner_class"),
        "execution_family": family,
        "ledger_path_bound": bool(getattr(exe, "ledger_path", None)),
        "notifier_bound": getattr(exe, "notifier", None) is not None,
        "x1_counters_bound": bool(exe is not None and hasattr(exe, "x1_counters")),
        "same_persistent_executor": same,
        "boot_v1r_native_entry_n": int(wiring.get("boot_v1r_native_entry") or 0),
        "passive_fill_n": 0 if family == FAMILY else 1,
        "v1r_pending_n": 0,
        "reason": "" if family == FAMILY else FAIL_BINDING,
    }


def now_stamp() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def file_sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(Path(path).read_bytes()).hexdigest() if Path(path).is_file() else ""
