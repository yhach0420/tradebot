"""Phase687W30: classify Paper session validity (strategy vs operational)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

VALID_SESSION = "VALID_SESSION"
INVALID_REGISTER_FAILED = "INVALID_REGISTER_FAILED"
INVALID_NO_PUSH = "INVALID_NO_PUSH"
INVALID_NO_GATE = "INVALID_NO_GATE"
INVALID_EARLY_ABORT = "INVALID_EARLY_ABORT"
INVALID_ARTIFACT_INCOMPLETE = "INVALID_ARTIFACT_INCOMPLETE"
INVALID_BOUNDED_STOP = "INVALID_BOUNDED_STOP"
INVALID_ABNORMAL_STOP = "INVALID_ABNORMAL_STOP"

SESSION_CLOCK_STOP = "session_clock_stop"
WAITING_MARKET = "WAITING_MARKET"

JST = ZoneInfo("Asia/Tokyo")

# Ordinary production/live stops only. session_clock_stop is NOT a member:
# it is NORMAL only when is_valid_session_clock_stop() proves the causal
# bounded-certification contract. Do not add it here.
_NORMAL_STOP = frozenset(
    {
        "session_end",
        "duration_elapsed",
        "max_polls",
        "operator_stop",
        "keyboard_interrupt",
        "morning_session_close",
        "afternoon_session_close",
        "recovery_session_close",
        "",
    }
)

_IDENTITY_KEYS = (
    "certification_run_id",
    "stage_run_id",
    "activation_id",
    "activation_sha",
    "runtime_run_id",
    "trading_date",
    "session_id",
)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return default


def _parse_dt(raw: Any) -> Optional[datetime]:
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


def _hhmm_on_day(day: str, hhmm: str) -> Optional[datetime]:
    token = str(hhmm or "").strip()
    if not token or ":" not in token:
        return None
    parts = token.split(":")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
        second = int(parts[2]) if len(parts) > 2 else 0
    except (TypeError, ValueError):
        return None
    ds = str(day or "").replace("-", "")[:8]
    if len(ds) != 8 or not ds.isdigit():
        return None
    try:
        return datetime(
            int(ds[:4]),
            int(ds[4:6]),
            int(ds[6:8]),
            hour,
            minute,
            second,
            tzinfo=JST,
        )
    except ValueError:
        return None


def _summary_identity(summary: Mapping[str, Any], evidence: Mapping[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key in _IDENTITY_KEYS:
        val = str(summary.get(key) or evidence.get(key) or "").strip()
        if val and val.lower() not in {"none", "null"}:
            out[key] = val
    return out


def _session_id_match(got: str, want: str) -> bool:
    g = str(got or "").strip()
    w = str(want or "").strip()
    if not w:
        return True
    if not g:
        return False
    return g == w or g.endswith(w) or w.endswith(g)


def _identity_matches(artifact: Mapping[str, str], expected: Mapping[str, Any]) -> bool:
    """Compare required current-run keys only.

    daily_run_id and other collector extras are ignored here. session_id
    accepts folder-id vs composite AM/PM id (live_session_081045 vs
    20260816_am_live_session_081045).
    """
    compared = False
    for key in _IDENTITY_KEYS:
        want = str(dict(expected or {}).get(key) or "").strip()
        if not want:
            continue
        compared = True
        got = str(artifact.get(key) or "").strip()
        if key == "session_id":
            if not _session_id_match(got, want):
                return False
            continue
        if got != want:
            return False
    return compared


def _warmup_end(summary: Mapping[str, Any], evidence: Mapping[str, Any]) -> Optional[datetime]:
    day = str(summary.get("trading_date") or evidence.get("trading_date") or "").replace("-", "")[:8]
    am = summary.get("am_pm_session") if isinstance(summary.get("am_pm_session"), Mapping) else {}
    windows = summary.get("allowed_trading_windows")
    start = (
        str(summary.get("session_start") or "").strip()
        or str((am or {}).get("allowed_entry_start") or "").strip()
        or str((am or {}).get("session_start") or "").strip()
    )
    if not start and isinstance(windows, list) and windows:
        first = windows[0] if isinstance(windows[0], Mapping) else {}
        start = str(first.get("start") or "").strip()
    parsed = _hhmm_on_day(day, start) if start else None
    if parsed is not None:
        return parsed
    nb = str(evidence.get("replay_not_before") or "").strip()
    return _hhmm_on_day(day, nb) if nb else None


def _best_watermark(summary: Mapping[str, Any], evidence: Mapping[str, Any], watermarks: Mapping[str, Any]) -> Optional[datetime]:
    raws = (
        evidence.get("replay_watermark"),
        evidence.get("paper_last_processed_event_time"),
        evidence.get("consumer_ack_watermark"),
        evidence.get("ingress_publish_watermark"),
        evidence.get("replay_read_watermark"),
        watermarks.get("paper_last_processed_event_time"),
        watermarks.get("consumer_ack_watermark"),
        watermarks.get("ingress_publish_watermark"),
        watermarks.get("replay_read_watermark"),
        summary.get("paper_last_processed_event_time"),
    )
    parsed = [dt for dt in (_parse_dt(x) for x in raws) if dt is not None]
    return max(parsed) if parsed else None


def _configured_stop(evidence: Mapping[str, Any], watermarks: Mapping[str, Any], environ: Mapping[str, str]) -> Optional[datetime]:
    from small_paper.runtime_clock import ENV_STOP, session_stop

    return (
        _parse_dt(evidence.get("configured_stop"))
        or _parse_dt(watermarks.get("session_stop"))
        or session_stop(environ=dict(environ))
        or _parse_dt(environ.get(ENV_STOP))
    )


def _still_waiting_market(summary: Mapping[str, Any], gate_n: int) -> bool:
    if str(summary.get("session_validity") or "") == WAITING_MARKET:
        return True
    if str(summary.get("stop_reason") or "") == WAITING_MARKET:
        return True
    v1r = summary.get("v1r_exit_v2")
    if isinstance(v1r, Mapping) and str(v1r.get("runtime_state") or "") == WAITING_MARKET and gate_n <= 0:
        return True
    return False


def attach_session_clock_evidence(
    summary: dict[str, Any],
    *,
    environ: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    """Stamp independently checkable bounded-stop evidence. Does not set valid=true."""
    import os

    from small_paper.runtime_clock import (
        ENV_REPLAY_NOT_BEFORE,
        ENV_V0,
        certification_mode,
        ingress_replay_path,
        load_replay_watermarks,
        session_clock_enabled,
        session_stop,
    )
    from small_paper.session_runtime_identity import expected_current_run_scope

    env = dict(environ if environ is not None else os.environ)
    wm = load_replay_watermarks(environ=env)
    stop = session_stop(environ=env)
    scope = expected_current_run_scope(trading_date=str(summary.get("trading_date") or "") or None, environ=env)
    ident = _summary_identity(summary, scope)
    consumer = (
        wm.get("paper_last_processed_event_time")
        or wm.get("consumer_ack_watermark")
        or summary.get("paper_last_processed_event_time")
    )
    replay_wm = (
        wm.get("consumer_ack_watermark")
        or wm.get("paper_last_processed_event_time")
        or wm.get("ingress_publish_watermark")
        or wm.get("replay_read_watermark")
    )
    evidence = {
        "certification_mode": bool(certification_mode(environ=env)),
        "session_clock_enabled": bool(session_clock_enabled(environ=env)),
        "replay_path_present": bool(ingress_replay_path(environ=env)),
        "configured_stop": stop.isoformat(timespec="milliseconds") if stop is not None else str(wm.get("session_stop") or ""),
        "v0": str(env.get(ENV_V0) or ""),
        "replay_not_before": str(env.get(ENV_REPLAY_NOT_BEFORE) or ""),
        "replay_watermark": replay_wm,
        "paper_last_processed_event_time": consumer,
        "consumer_ack_watermark": wm.get("consumer_ack_watermark"),
        "ingress_publish_watermark": wm.get("ingress_publish_watermark"),
        "replay_read_watermark": wm.get("replay_read_watermark"),
        "replay_eof": bool(wm.get("replay_eof")),
        **ident,
    }
    summary["session_clock_evidence"] = evidence
    return summary


def is_valid_session_clock_stop(
    summary: Mapping[str, Any] | None = None,
    *,
    environ: Optional[Mapping[str, str]] = None,
    watermarks: Optional[Mapping[str, Any]] = None,
    expected_scope: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """True only when a bounded certification session satisfied the causal STOP contract.

    Live Paper, spoofed stop_reason, premature warmup STOP, and stale/wrong
    identity are fail-closed INVALID. Never treat the string session_clock_stop
    as NORMAL by itself.
    """
    import os

    from small_paper.runtime_clock import (
        certification_mode,
        ingress_replay_path,
        load_replay_watermarks,
        session_clock_enabled,
    )
    from small_paper.session_runtime_identity import expected_current_run_scope

    s = dict(summary or {})
    env = dict(environ if environ is not None else os.environ)
    evidence = s.get("session_clock_evidence") if isinstance(s.get("session_clock_evidence"), Mapping) else {}
    wm = dict(watermarks or {})
    if not wm:
        try:
            wm = load_replay_watermarks(environ=env)
        except Exception:
            wm = {}
    out = {
        "ok": False,
        "reason": "not_session_clock_stop",
        "evidence": dict(evidence),
    }
    reason = str(s.get("stop_reason") or "")
    if reason != SESSION_CLOCK_STOP:
        return out

    push_n = _as_int(s.get("push_messages"))
    gate_n = _as_int(s.get("gate_evaluations"))
    runtime_sec = _as_float(s.get("runtime_sec"))
    heartbeat_n = _as_int(s.get("heartbeat_count"))

    stop_dt = _configured_stop(evidence, wm, env)
    if stop_dt is None:
        out["reason"] = "bounded_stop_not_configured"
        return out

    cert = bool(evidence.get("certification_mode")) or bool(certification_mode(environ=env))
    clock_on = bool(evidence.get("session_clock_enabled")) or bool(session_clock_enabled(environ=env))
    replay = bool(evidence.get("replay_path_present")) or bool(ingress_replay_path(environ=env))
    market = str(s.get("market_input_mode") or "").strip().upper()
    if market == "REPLAY":
        replay = True
    if not cert or not clock_on or not replay:
        out["reason"] = "not_certification_replay_bounded_session"
        return out

    if runtime_sec <= 0.0 and heartbeat_n <= 0 and push_n <= 0:
        out["reason"] = "paper_never_started"
        return out
    processed_dt = _parse_dt(evidence.get("paper_last_processed_event_time")) or _parse_dt(
        s.get("paper_last_processed_event_time")
    ) or _parse_dt(wm.get("paper_last_processed_event_time")) or _parse_dt(wm.get("consumer_ack_watermark"))
    if push_n <= 0 or processed_dt is None:
        out["reason"] = "consumer_did_not_process_market_data"
        return out
    if _still_waiting_market(s, gate_n) or gate_n <= 0:
        out["reason"] = "waiting_market_or_no_target_eval"
        return out

    watermark = _best_watermark(s, evidence, wm)
    eof = bool(evidence.get("replay_eof") or wm.get("replay_eof") or s.get("replay_eof"))
    warmup = _warmup_end(s, evidence)
    if warmup is not None:
        progressed = (processed_dt is not None and processed_dt >= warmup) or (
            watermark is not None and watermark >= warmup
        )
        if not progressed:
            out["reason"] = "stop_preempted_replay_warmup"
            return out

    if not eof and (watermark is None or watermark < stop_dt):
        out["reason"] = "watermark_before_stop_without_eof"
        return out

    ident = _summary_identity(s, evidence)
    if not str(ident.get("session_id") or "").strip():
        out["reason"] = "session_id_missing"
        return out
    expected = dict(expected_scope or {})
    if not expected:
        expected = expected_current_run_scope(
            trading_date=str(s.get("trading_date") or ident.get("trading_date") or "") or None,
            environ=env,
        )
    if not expected:
        out["reason"] = "current_run_identity_not_proven"
        return out
    if not _identity_matches(ident, expected):
        out["reason"] = "current_run_identity_mismatch"
        return out
    evidence_run = str(evidence.get("certification_run_id") or "").strip()
    summary_run = str(s.get("certification_run_id") or "").strip()
    if evidence_run and summary_run and evidence_run != summary_run:
        out["reason"] = "stale_previous_run_stop_evidence"
        return out
    evidence_stage = str(evidence.get("stage_run_id") or "").strip()
    summary_stage = str(s.get("stage_run_id") or "").strip()
    if evidence_stage and summary_stage and evidence_stage != summary_stage:
        out["reason"] = "stale_previous_run_stop_evidence"
        return out

    out["ok"] = True
    out["reason"] = "valid_bounded_session_clock_stop"
    out["evidence"] = {
        **dict(evidence),
        "configured_stop": stop_dt.isoformat(timespec="milliseconds"),
        "replay_watermark": (watermark.isoformat(timespec="milliseconds") if watermark else None),
        "replay_eof": eof,
        "paper_last_processed_event_time": processed_dt.isoformat(timespec="milliseconds"),
    }
    return out


def classify_session_validity(
    summary: Mapping[str, Any] | None = None,
    *,
    stop_reason: Optional[str] = None,
    push_messages: Optional[int] = None,
    gate_evaluations: Optional[int] = None,
    heartbeat_count: Optional[int] = None,
    session_seal_status: Optional[str] = None,
    runtime_sec: Optional[float] = None,
    expected_scope: Optional[Mapping[str, Any]] = None,
    environ: Optional[Mapping[str, str]] = None,
    watermarks: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Return validity class for Discord / PnL exclusion / Recovery."""
    s = dict(summary or {})
    reason = str(stop_reason if stop_reason is not None else s.get("stop_reason") or "")
    push_n = int(push_messages if push_messages is not None else s.get("push_messages") or 0)
    gate_n = int(gate_evaluations if gate_evaluations is not None else s.get("gate_evaluations") or 0)
    hb_n = int(heartbeat_count if heartbeat_count is not None else s.get("heartbeat_count") or 0)
    seal = str(
        session_seal_status
        if session_seal_status is not None
        else s.get("session_seal_status") or s.get("seal_status") or ""
    )
    rt = float(runtime_sec if runtime_sec is not None else s.get("runtime_sec") or 0.0)
    work = dict(s)
    work["stop_reason"] = reason
    work["push_messages"] = push_n
    work["gate_evaluations"] = gate_n
    work["heartbeat_count"] = hb_n
    work["runtime_sec"] = rt
    clock = {
        "ok": False,
        "reason": "not_session_clock_stop",
        "evidence": work.get("session_clock_evidence") if isinstance(work.get("session_clock_evidence"), Mapping) else {},
    }

    if reason == "register_failed":
        klass = INVALID_REGISTER_FAILED
    elif seal == "INCOMPLETE" and push_n == 0 and gate_n == 0 and rt < 5.0:
        klass = INVALID_EARLY_ABORT
    elif seal == "INCOMPLETE":
        klass = INVALID_ARTIFACT_INCOMPLETE
    elif push_n <= 0:
        klass = INVALID_NO_PUSH
    elif gate_n <= 0:
        klass = INVALID_NO_GATE
    elif reason == SESSION_CLOCK_STOP:
        clock = is_valid_session_clock_stop(
            work,
            environ=environ,
            watermarks=watermarks,
            expected_scope=expected_scope,
        )
        klass = VALID_SESSION if clock.get("ok") else INVALID_BOUNDED_STOP
    elif reason and reason not in _NORMAL_STOP and reason not in ("session_end",):
        klass = INVALID_EARLY_ABORT if rt < 30.0 else INVALID_ABNORMAL_STOP
    else:
        klass = VALID_SESSION

    if gate_n > 0 and klass == INVALID_NO_GATE:
        if reason == SESSION_CLOCK_STOP:
            klass = INVALID_BOUNDED_STOP
        else:
            klass = INVALID_EARLY_ABORT if rt < 30.0 else INVALID_ABNORMAL_STOP

    include_in_strategy = klass == VALID_SESSION
    return {
        "session_validity": klass,
        "include_in_strategy_metrics": include_in_strategy,
        "include_in_cumulative_pnl": include_in_strategy,
        "include_in_forward_day_count": include_in_strategy,
        "include_in_live_readiness_streak": include_in_strategy,
        "stop_reason": reason,
        "push_messages": push_n,
        "gate_evaluations": gate_n,
        "heartbeat_count": hb_n,
        "session_seal_status": seal or None,
        "session_clock_stop_valid": bool(clock.get("ok")) if reason == SESSION_CLOCK_STOP else False,
        "session_clock_stop_reason": str(clock.get("reason") or ""),
        "discord_banner": (
            None
            if include_in_strategy
            else {
                "title": "【INVALID PAPER SESSION】",
                "cause": reason or klass,
                "push": push_n,
                "gate_evaluations": gate_n,
                "note": "損益は戦略成績に含めない",
            }
        ),
    }


def format_invalid_session_discord_lines(validity: Mapping[str, Any]) -> list[str]:
    banner = validity.get("discord_banner")
    if not isinstance(banner, Mapping):
        return []
    return [
        str(banner.get("title") or "【INVALID PAPER SESSION】"),
        f"原因: {banner.get('cause')}",
        f"PUSH: {banner.get('push')}",
        f"評価: {banner.get('gate_evaluations')}",
        str(banner.get("note") or "損益は戦略成績に含めない"),
    ]


def format_paper_not_running_discord_lines(
    *,
    stop_point: str = "register",
    push: int = 0,
    gate: int = 0,
    capture_status: str = "待機中",
) -> list[str]:
    return [
        "【PAPER NOT RUNNING】",
        f"停止点: {stop_point}",
        f"PUSH: {push}",
        f"ENTRY評価: {gate}",
        "Paper損益: 無効",
        f"Capture: {capture_status}",
        "operator action: Kabu登録状態を確認",
    ]


def format_register_recovered_discord_lines(
    *,
    registered: int = 50,
    expected: int = 50,
    push_receiving: bool = False,
) -> list[str]:
    """Only call when register readback OK; push_receiving gates RECOVERED wording."""
    if not push_receiving:
        return [
            "【REGISTER RETRY OK — WAITING PUSH】",
            f"登録: {registered}/{expected}",
            "PUSH: not yet receiving",
            "Paper評価: waiting",
        ]
    return [
        "【REGISTER RECOVERED】",
        f"登録: {registered}/{expected}",
        "PUSH: receiving",
        "Paper評価: running",
    ]
