"""X1-only Discord routing. Observability. Does not place orders.

When the selected Paper activation is Fixed Entry Support V8 or a successor
and the execution family is X1_IMMEDIATE_ASK, strategy ENTRY/EXIT/Shadow
notifications may leave this process only from FixedSupportX1SessionExecutor.
Every other strategy source is recorded locally and is not published.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Mapping, Optional

FAIL_SUPPRESSED = "OLD_STRATEGY_DISCORD_SUPPRESSED"
SOURCE = "FixedSupportX1SessionExecutor"
FAMILY = "X1_IMMEDIATE_ASK"
ACTIVATION_PREFIX = "FIXED_ENTRY_SUPPORT_PAPER_PRIMARY_ACTIVATION_V"

ALLOWED_EVENTS = frozenset(
    {
        "FIXED_SUPPORT_PAPER_READY",
        "X1_PAPER_ENTRY",
        "X1_PAPER_EXIT",
        "X1_AM_SUMMARY",
        "X1_PM_SUMMARY",
        "X1_DAILY_SUMMARY",
        "X1_CRITICAL",
        "DISCORD_CONNECTIVITY_TEST",
        "HEARTBEAT",
        "UNIVERSE SCREENING",
        "UNIVERSE REFRESH",
        "UNIVERSE READINESS",
    }
)
OPERATIONAL_EVENTS = frozenset(
    {
        "FIXED_SUPPORT_PAPER_READY",
        "DISCORD_CONNECTIVITY_TEST",
        "HEARTBEAT",
        "UNIVERSE SCREENING",
        "UNIVERSE REFRESH",
        "UNIVERSE READINESS",
        "X1_CRITICAL",
    }
)
STRATEGY_EVENTS = frozenset(
    {
        "ENTRY",
        "EXIT",
        "FILL",
        "EXPIRED",
        "PENDING",
        "SUMMARY",
        "DAILY",
        "HOLD",
        "TAKE",
        "INFO",
        "PBV2_SHADOW",
        "ONE_M_SHADOW",
        "PRIMARY_SUMMARY",
        "CAP_BLOCKED",
        "E1_X5",
        "E1_X5_SHADOW",
        "V1R_ENTRY",
        "V1R_EXIT",
        "V1R_FILL",
        "V1R_EXPIRED",
        "V1R_PENDING",
    }
)
CRITICAL_MARKERS = (
    "EVENT_LOOP_STALL",
    "PAPER PROCESS UNEXPECTEDLY STOPPED",
    "PAPER_STOPPED",
    "MARKETBUS",
    "INGRESS STALL",
    "INGRESS_STALL",
    "LEDGER WRITE FAILURE",
    "LEDGER_WRITE",
    "NOTIFIER FAILURE",
    "NOTIFIER_FAILURE",
    "RECOVERY CONTRADICTION",
    "CURRENT_UNREADABLE",
    "EXACT50",
    "SAFETY VIOLATION",
    "SAFETY_VIOLATION",
    "SUBMIT/CANCEL/LIVE",
)

_LOCK = threading.Lock()
_FORCE: Optional[bool] = None
_FORCED_ACTIVATION = ""
_CACHE: Optional[tuple[bool, str]] = None
_COUNTS: dict[str, int] = {
    "send_n": 0,
    "suppressed_n": 0,
    "discord_attempt_n": 0,
    "discord_success_n": 0,
    "discord_failure_n": 0,
}
_BY_KIND: dict[str, int] = {}

NATIVE = Path(__file__).resolve().parents[2]
LOG_DIR = NATIVE / "results" / "operations" / "x1_discord_routing"


def reset_x1_discord_routing_for_tests() -> None:
    global _FORCE, _FORCED_ACTIVATION, _CACHE
    with _LOCK:
        _FORCE = None
        _FORCED_ACTIVATION = ""
        _CACHE = None
        _COUNTS["send_n"] = 0
        _COUNTS["suppressed_n"] = 0
        _COUNTS["discord_attempt_n"] = 0
        _COUNTS["discord_success_n"] = 0
        _COUNTS["discord_failure_n"] = 0
        _BY_KIND.clear()


def force_x1_discord_routing(
    active: Optional[bool],
    *,
    activation_id: str = "FIXED_ENTRY_SUPPORT_PAPER_PRIMARY_ACTIVATION_V9",
) -> None:
    """Test/prod override. None restores selector detection."""
    global _FORCE, _FORCED_ACTIVATION, _CACHE
    with _LOCK:
        _FORCE = active
        _FORCED_ACTIVATION = str(activation_id or "")
        _CACHE = None


def routing_counters() -> dict[str, Any]:
    with _LOCK:
        return {
            "send_n": int(_COUNTS["send_n"]),
            "suppressed_n": int(_COUNTS["suppressed_n"]),
            "discord_attempt_n": int(_COUNTS["discord_attempt_n"]),
            "discord_success_n": int(_COUNTS["discord_success_n"]),
            "discord_failure_n": int(_COUNTS["discord_failure_n"]),
            "by_kind": dict(_BY_KIND),
            "suppressed_code": FAIL_SUPPRESSED,
        }


def _version_of(activation_id: str) -> int:
    if not str(activation_id).startswith(ACTIVATION_PREFIX):
        return -1
    tail = str(activation_id)[len(ACTIVATION_PREFIX) :]
    try:
        return int(tail)
    except ValueError:
        return -1


def _selector_requirement() -> tuple[bool, str]:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    try:
        from small_paper.v1r_activation_binding import (
            load_activation_manifest,
            load_active_selector,
        )

        selector = load_active_selector()
        manifest = load_activation_manifest(selector=selector)
        aid = str(manifest.get("activation_id") or selector.get("activation_id") or "")
        family = str(manifest.get("execution_family") or "")
        required = _version_of(aid) >= 8 and family == FAMILY
        _CACHE = (required, aid if required else "")
    except Exception:
        _CACHE = (False, "")
    return _CACHE


def x1_discord_routing_enforced() -> bool:
    """Production Paper enforces. Pytest does not, unless a test forces it."""
    if _FORCE is True:
        return True
    if _FORCE is False:
        return False
    if str(os.environ.get("TRADEBOT_X1_DISCORD_ROUTING") or "") == "0":
        return False
    if os.environ.get("PYTEST_CURRENT_TEST") and str(os.environ.get("TRADEBOT_X1_DISCORD_ROUTING") or "") != "1":
        return False
    if str(os.environ.get("TRADEBOT_X1_DISCORD_ROUTING") or "") == "1":
        return True
    return _selector_requirement()[0]


def selected_activation_id() -> str:
    if _FORCE is not None and _FORCED_ACTIVATION:
        return _FORCED_ACTIVATION
    return _selector_requirement()[1]


def _record(kind: str, *, suppressed: bool, source: str = "") -> None:
    with _LOCK:
        key = str(kind or "UNKNOWN")
        _BY_KIND[key] = int(_BY_KIND.get(key) or 0) + 1
        if suppressed:
            _COUNTS["suppressed_n"] = int(_COUNTS["suppressed_n"]) + 1
    if not suppressed:
        return
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        row = {
            "code": FAIL_SUPPRESSED,
            "kind": str(kind or ""),
            "source": str(source or ""),
        }
        with (LOG_DIR / "suppressed.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        return


def note_discord_send() -> None:
    with _LOCK:
        _COUNTS["send_n"] = int(_COUNTS["send_n"]) + 1


def authorize_discord(
    *,
    event_tag: str,
    title: str = "",
    source: str = "",
    activation_id: str = "",
    execution_family: str = "",
) -> tuple[bool, str]:
    """Return (allow, reason). A deny never publishes to Discord."""
    if not x1_discord_routing_enforced():
        return True, ""
    tag = str(event_tag or "").strip().upper()
    blob = f"{tag} {title}".upper()
    critical = tag == "X1_CRITICAL" or any(marker in blob for marker in CRITICAL_MARKERS)
    allowed_kind = tag in ALLOWED_EVENTS or critical
    if not allowed_kind:
        _record(tag or "UNTAGGED", suppressed=True, source=source)
        return False, FAIL_SUPPRESSED
    if tag in {"X1_PAPER_ENTRY", "X1_PAPER_EXIT", "X1_AM_SUMMARY", "X1_PM_SUMMARY", "X1_DAILY_SUMMARY"}:
        if source != SOURCE or execution_family != FAMILY or not str(activation_id or "").strip():
            _record(tag, suppressed=True, source=source or "missing")
            return False, FAIL_SUPPRESSED
        selected = selected_activation_id()
        if selected and str(activation_id) != selected:
            _record(tag, suppressed=True, source=source)
            return False, FAIL_SUPPRESSED
    return True, ""


def operational_heartbeat_fields(summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Current operational heartbeat. Does not publish strategy ENTRY or EXIT."""
    book = summary.get("x1_executor") if isinstance(summary.get("x1_executor"), dict) else {}
    session = str(
        summary.get("session")
        or summary.get("am_pm")
        or summary.get("session_bucket")
        or summary.get("session_kind")
        or ""
    ).upper()
    timestamp = str(summary.get("event_time") or summary.get("emitted_at") or "")
    alive = summary.get("x1_executor_alive")
    if alive is None:
        alive = bool(book)
    push = summary.get("push_messages", summary.get("push_event_count", summary.get("push_count", "")))
    ledger = str(summary.get("ledger_status") or "")
    if not ledger:
        ledger = "bound" if book.get("ledger_path") or summary.get("ledger_path_bound") else "unbound"

    def _yn(value: Any) -> str:
        if value is True:
            return "true"
        if value is False:
            return "false"
        return str(value if value is not None else "")

    return [
        {"name": "timestamp", "value": timestamp or "—", "inline": False},
        {"name": "session", "value": session or "—", "inline": True},
        {"name": "ingress_alive", "value": _yn(summary.get("ingress_alive")), "inline": True},
        {"name": "marketbus_alive", "value": _yn(summary.get("marketbus_alive")), "inline": True},
        {"name": "x1_executor_alive", "value": _yn(alive), "inline": True},
        {"name": "push_event_count", "value": str(push), "inline": True},
        {"name": "x1_entry", "value": str(book.get("x1_entry_n", "")), "inline": True},
        {"name": "x1_open", "value": str(book.get("x1_open_n", "")), "inline": True},
        {"name": "x1_exit", "value": str(book.get("x1_exit_n", "")), "inline": True},
        {"name": "ledger_status", "value": ledger or "—", "inline": True},
        {"name": "submit/cancel/live", "value": str(summary.get("submit_cancel_live") or "0/0/0"), "inline": True},
    ]


def _slot_counts(csv_path: Path) -> tuple[Optional[int], Optional[int]]:
    if not csv_path.is_file():
        return None, None
    import csv

    core = 0
    dynamic = 0
    with csv_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        slot_key = ""
        if reader.fieldnames:
            for name in reader.fieldnames:
                if str(name).strip().lower() in {"universe_slot", "slot"}:
                    slot_key = name
                    break
        if not slot_key:
            return None, None
        for row in reader:
            slot = str(row.get(slot_key) or "").strip().lower()
            if slot == "core":
                core += 1
            elif slot:
                dynamic += 1
    return core, dynamic


def universe_readiness_fields(*, trading_date: str = "") -> list[dict[str, Any]]:
    """Frozen-universe facts for the operational screening notice. Does not rebuild membership."""
    day = str(trading_date or "").replace("-", "")[:8]
    core: Optional[int] = None
    dynamic: Optional[int] = None
    n: Optional[int] = None
    membership = ""
    synthetic: Optional[bool] = None
    registered: Optional[int] = None
    exact: Optional[bool] = None
    try:
        frozen = NATIVE / "runtime" / f"same_day_am_frozen_universe_{day}.json"
        if day and frozen.is_file():
            body = json.loads(frozen.read_text(encoding="utf-8"))
            symbols = [str(item) for item in list(body.get("canonical_symbols") or [])]
            n = len(symbols)
            membership = str(body.get("runtime_membership_sha") or body.get("canonical_membership_sha") or "")
            from small_paper.pre_paper_ready_seal import is_synthetic_fixture_identity

            synthetic = is_synthetic_fixture_identity(symbols)
            csv_path = Path(str(body.get("frozen_csv_path") or body.get("source_csv_path") or ""))
            core, dynamic = _slot_counts(csv_path)
    except Exception:
        pass
    try:
        seal_path = NATIVE / "runtime" / f"pre_paper_ready_{day}.json"
        if day and seal_path.is_file():
            seal = json.loads(seal_path.read_text(encoding="utf-8"))
            if seal.get("registered_n") is not None:
                registered = int(seal.get("registered_n"))
            if seal.get("exact50") is True:
                exact = True
            elif seal.get("exact50") is False:
                exact = False
            if synthetic is None and seal.get("synthetic_universe") is not None:
                synthetic = bool(seal.get("synthetic_universe"))
            if n is None and seal.get("universe_n") is not None:
                n = int(seal.get("universe_n"))
            if not membership:
                membership = str(seal.get("universe_membership_sha") or "")
    except Exception:
        pass
    exact_label = "PASS" if exact is True else ("FAIL" if exact is False else "—")
    registered_label = f"{registered}/50" if registered is not None else "—"
    return [
        {"name": "trading_date", "value": day or "—", "inline": True},
        {"name": "Core10", "value": str(core if core is not None else "—"), "inline": True},
        {"name": "Dynamic40", "value": str(dynamic if dynamic is not None else "—"), "inline": True},
        {"name": "universe_n", "value": str(n if n is not None else "—"), "inline": True},
        {"name": "membership_sha", "value": (membership or "—")[:80], "inline": False},
        {"name": "registered", "value": registered_label, "inline": True},
        {"name": "EXACT50", "value": exact_label, "inline": True},
        {"name": "synthetic", "value": "false" if synthetic is False else ("true" if synthetic is True else "—"), "inline": True},
    ]


def identity_fields(
    *,
    activation_id: str,
    execution_family: str = FAMILY,
    source: str = SOURCE,
) -> list[dict[str, Any]]:
    return [
        {"name": "activation_id", "value": str(activation_id or "")[:256], "inline": False},
        {"name": "execution_family", "value": str(execution_family or "")[:80], "inline": True},
        {"name": "source", "value": str(source or "")[:80], "inline": True},
    ]


def x1_summary_event(summary: Mapping[str, Any]) -> str:
    stop = str(summary.get("stop_reason") or "").lower()
    bucket = str(summary.get("session_bucket") or summary.get("session") or "").upper()
    if "morning" in stop or bucket in {"AM", "MORNING"}:
        return "X1_AM_SUMMARY"
    if "afternoon" in stop or bucket in {"PM", "AFTERNOON"}:
        return "X1_PM_SUMMARY"
    return "X1_DAILY_SUMMARY"


def x1_summary_title(event: str, *, summary_class: str = "FINAL") -> str:
    if summary_class != "FINAL":
        return "[X1 PAPER PRE-CLOSE SNAPSHOT]"
    return {
        "X1_AM_SUMMARY": "[X1 PAPER AM SUMMARY]",
        "X1_PM_SUMMARY": "[X1 PAPER PM SUMMARY]",
        "X1_DAILY_SUMMARY": "[X1 PAPER DAILY SUMMARY]",
    }.get(event, "[X1 PAPER DAILY SUMMARY]")


def _summary_number(book: Mapping[str, Any], perf: Mapping[str, Any], book_key: str, perf_key: str) -> int:
    if book_key in book and book.get(book_key) not in (None, ""):
        return int(book.get(book_key) or 0)
    return int(perf.get(perf_key) or 0)


def x1_summary_is_final(summary: Mapping[str, Any]) -> bool:
    if str(summary.get("x1_boundary_error") or ""):
        return False
    if str(summary.get("x1_summary_class") or "") == "PRE_CLOSE_SNAPSHOT":
        return False
    book = summary.get("x1_executor") if isinstance(summary.get("x1_executor"), dict) else {}
    perf = summary.get("x1_performance") if isinstance(summary.get("x1_performance"), dict) else {}
    entry_n = _summary_number(book, perf, "x1_entry_n", "entry_n")
    exit_n = _summary_number(book, perf, "x1_exit_n", "exit_n")
    open_n = _summary_number(book, perf, "x1_open_n", "open_n")
    pending_n = _summary_number(book, perf, "x1_exit_pending_n", "exit_pending_n")
    return entry_n == exit_n and open_n == 0 and pending_n == 0


def x1_summary_fields(summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    book = summary.get("x1_executor") if isinstance(summary.get("x1_executor"), dict) else {}
    perf = summary.get("x1_performance") if isinstance(summary.get("x1_performance"), dict) else {}
    reasons = perf.get("exit_reasons") if isinstance(perf.get("exit_reasons"), dict) else {}

    def reason_line(name: str) -> str:
        row = reasons.get(name) if isinstance(reasons.get(name), dict) else {}
        count = int(row.get("n") or 0)
        pnl = row.get("pnl_yen", 0)
        return f"{count} / {pnl}"

    pf = perf.get("pf")
    pf_text = "n/a" if pf is None else f"{float(pf):.4f}"
    return [
        {"name": "Trades", "value": str(perf.get("trades_n", book.get("x1_exit_n", ""))), "inline": True},
        {"name": "ENTRY", "value": str(book.get("x1_entry_n", perf.get("entry_n", ""))), "inline": True},
        {"name": "EXIT", "value": str(book.get("x1_exit_n", perf.get("exit_n", ""))), "inline": True},
        {"name": "OPEN", "value": str(book.get("x1_open_n", perf.get("open_n", ""))), "inline": True},
        {"name": "EXIT_PENDING", "value": str(book.get("x1_exit_pending_n", perf.get("exit_pending_n", 0))), "inline": True},
        {"name": "Net PnL", "value": str(perf.get("net_pnl_yen", "")), "inline": True},
        {"name": "Gross Profit", "value": str(perf.get("gross_profit_yen", "")), "inline": True},
        {"name": "Gross Loss", "value": str(perf.get("gross_loss_yen", "")), "inline": True},
        {"name": "PF", "value": pf_text, "inline": True},
        {
            "name": "Win / Loss / Draw",
            "value": f"{perf.get('win_n', 0)} / {perf.get('loss_n', 0)} / {perf.get('draw_n', 0)}",
            "inline": False,
        },
        {"name": "Avg PnL", "value": str(perf.get("avg_pnl_yen", "")), "inline": True},
        {"name": "Median PnL", "value": str(perf.get("median_pnl_yen", "")), "inline": True},
        {"name": "BREAK_SUPPORT_FAILURE", "value": reason_line("BREAK_SUPPORT_FAILURE"), "inline": False},
        {"name": "IMPULSE_EXHAUSTED", "value": reason_line("IMPULSE_EXHAUSTED"), "inline": False},
        {"name": "SESSION_FLAT", "value": reason_line("SESSION_FLAT"), "inline": False},
        {"name": "CAP blocked", "value": str(perf.get("cap_blocked_n", book.get("x1_cap_blocked_n", ""))), "inline": True},
        {
            "name": "same-symbol blocked",
            "value": str(perf.get("same_symbol_blocked_n", book.get("x1_same_symbol_blocked_n", ""))),
            "inline": True,
        },
        {"name": "slot release n", "value": str(perf.get("slot_release_n", book.get("x1_slot_release_n", ""))), "inline": True},
        {"name": "activation_id", "value": str(summary.get("activation_id") or perf.get("activation_id") or "")[:256], "inline": False},
        {"name": "execution_family", "value": str(summary.get("execution_family") or perf.get("execution_family") or "")[:80], "inline": True},
        {"name": "submit/cancel/live", "value": str(perf.get("submit_cancel_live") or "0/0/0"), "inline": True},
        {
            "name": "Source session",
            "value": str(summary.get("summary_source_session_id") or summary.get("session_id") or "")[:256],
            "inline": False,
        },
        {
            "name": "FULL_PM",
            "value": "true" if summary.get("full_pm_summary") is True else ("false" if summary.get("full_pm_summary") is False else "n/a"),
            "inline": True,
        },
        {
            "name": "SUMMARY_SCOPE",
            "value": str(summary.get("SUMMARY_SCOPE") or ("FULL_PM" if summary.get("full_pm_summary") is True else "SEGMENT")),
            "inline": True,
        },
        {
            "name": "FULL_PM_VALID",
            "value": "true" if summary.get("FULL_PM_VALID") is True else "false",
            "inline": True,
        },
        {
            "name": "OPERATIONALLY_INVALID",
            "value": "true" if summary.get("OPERATIONALLY_INVALID") is True else "false",
            "inline": True,
        },
        {
            "name": "PM segments",
            "value": str(((summary.get("pm_day_aggregate") or {}) if isinstance(summary.get("pm_day_aggregate"), dict) else {}).get("segment_n") or 1),
            "inline": True,
        },
        {
            "name": "AGG PM ENTRY",
            "value": str(((summary.get("pm_day_aggregate") or {}) if isinstance(summary.get("pm_day_aggregate"), dict) else {}).get("entry") or book.get("x1_entry_n") or 0),
            "inline": True,
        },
        {
            "name": "admission denied",
            "value": str(book.get("x1_admission_denied_n", 0)),
            "inline": True,
        },
    ]


def stamp_x1_summary_identity(summary: dict[str, Any], executor: Any) -> None:
    """Copy X1 identity onto the session summary. Does not change the book."""
    if executor is None:
        return
    counters = {}
    if hasattr(executor, "x1_counters"):
        try:
            counters = dict(executor.x1_counters())
        except Exception:
            counters = {}
    summary["x1_executor"] = counters
    summary["discord_source"] = SOURCE
    summary["execution_family"] = FAMILY
    aid = str(getattr(executor, "activation_id", "") or "")
    if aid:
        summary["activation_id"] = aid
    perf = {}
    if hasattr(executor, "x1_performance"):
        try:
            perf = dict(executor.x1_performance())
        except Exception:
            perf = {}
    summary["x1_performance"] = perf
    err = str(getattr(executor, "boundary_error", "") or "")
    if err:
        summary["x1_boundary_error"] = err
    final_ok = bool(perf.get("final_ok")) and not err
    summary["x1_summary_class"] = "FINAL" if final_ok else "PRE_CLOSE_SNAPSHOT"
    if not summary.get("summary_source_session_id"):
        summary["summary_source_session_id"] = str(summary.get("session_id") or "")
    kind = str(
        ((summary.get("am_pm_session") or {}) if isinstance(summary.get("am_pm_session"), dict) else {}).get("kind")
        or summary.get("session_kind")
        or getattr(executor, "session", "")
        or ""
    ).lower()
    if kind == "pm":
        session_dir = None
        ledger = getattr(executor, "ledger_path", None)
        if ledger is not None:
            try:
                session_dir = Path(ledger).parent
            except Exception:
                session_dir = None
        try:
            from small_paper.x1_pm_session_identity import attach_pm_summary_identity

            attach_pm_summary_identity(summary, session_dir)
        except Exception:
            summary.setdefault("full_pm_summary", False)
            summary.setdefault("SUMMARY_SCOPE", "SEGMENT")
            summary.setdefault("FULL_PM_VALID", False)
            summary.setdefault("OPERATIONALLY_INVALID", True)


def suppress_legacy_strategy_discord(kind: str, *, source: str) -> bool:
    """True when this legacy strategy publish must not reach Discord."""
    if not x1_discord_routing_enforced():
        return False
    _record(str(kind or "LEGACY"), suppressed=True, source=source)
    return True


def send_connectivity_test_once(*, activation_id: str) -> dict[str, Any]:
    """One production-webhook connectivity post. Does not invent ENTRY or EXIT."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    marker = LOG_DIR / "connectivity_test.json"
    if marker.is_file():
        prior = json.loads(marker.read_text(encoding="utf-8"))
        if int(prior.get("discord_success_n") or 0) >= 1:
            prior["skipped_duplicate"] = True
            return prior
    attempt = 1
    success = 0
    failure = 0
    http_status = None
    error = ""
    try:
        from notify.discord_notification_router import resolve_webhook_url

        url, _key = resolve_webhook_url(
            ("KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL", "KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL")
        )
        if not url:
            raise RuntimeError("webhook_missing")
        import requests

        content = "\n".join(
            [
                "[FIXED SUPPORT DISCORD CONNECTIVITY TEST]",
                f"activation={activation_id}",
                "execution_family=X1_IMMEDIATE_ASK",
                "source=FixedSupportX1SessionExecutor",
                "orders=DISABLED",
                "submit/cancel/live=0/0/0",
            ]
        )
        payload = {
            "content": content[:1800],
            "embeds": [
                {
                    "title": "[FIXED SUPPORT DISCORD CONNECTIVITY TEST]",
                    "description": content[:1800],
                    "color": 0x2F855A,
                    "fields": identity_fields(activation_id=activation_id),
                }
            ],
        }
        response = requests.post(url, json=payload, timeout=15)
        http_status = int(response.status_code)
        if http_status in (200, 204):
            success = 1
        else:
            failure = 1
            error = f"http_{http_status}"
    except Exception as exc:
        failure = 1
        error = type(exc).__name__
    body = {
        "title": "[FIXED SUPPORT DISCORD CONNECTIVITY TEST]",
        "activation_id": activation_id,
        "execution_family": FAMILY,
        "source": SOURCE,
        "orders": "DISABLED",
        "submit_cancel_live": "0/0/0",
        "discord_attempt_n": attempt,
        "discord_success_n": success,
        "discord_failure_n": failure,
        "http_status": http_status,
        "error": error,
        "entry_exit_fabricated": False,
    }
    marker.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    with _LOCK:
        _COUNTS["discord_attempt_n"] = attempt
        _COUNTS["discord_success_n"] = success
        _COUNTS["discord_failure_n"] = failure
        if success:
            _COUNTS["send_n"] = int(_COUNTS["send_n"]) + 1
    return body
