"""X1 Paper operational gates that were missing on 20261005.

Existing PREOPEN AUTH_READY / freeze_valid50 / capture-ready checks stay where
they already live. This module only covers the holes: X1 admission membership
must be the day-fixed AM freeze, latched-without-admission must surface, and
PM Discord identity must name its source session.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence


def _bare(symbol: str) -> str:
    text = str(symbol or "").strip()
    if text.endswith(".T"):
        text = text[:-2]
    if "@" in text:
        text = text.split("@", 1)[0]
    return text.strip()


def x1_universe_parity_gate(
    freeze_symbols: Sequence[str],
    admission_symbols: Sequence[str],
) -> dict[str, Any]:
    freeze = {_bare(s) for s in freeze_symbols if _bare(s)}
    admitted = {_bare(s) for s in admission_symbols if _bare(s)}
    ok = freeze == admitted and len(freeze) == 50 and "9223" not in freeze
    return {
        "ok": ok,
        "AM_UNIVERSE_N": len(freeze),
        "PM_ADMISSION_N": len(admitted),
        "SET_EQUAL": freeze == admitted,
        "has_9223": "9223" in freeze or "9223" in admitted,
        "has_4166": "4166" in freeze,
        "reason": "" if ok else "X1_ADMISSION_MEMBERSHIP_NOT_FROZEN_AM50",
        "submit_cancel_live": "0/0/0",
    }


def x1_admission_pipeline_gate(counters: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    book = counters or {}
    latched = int(book.get("x1_full_latched_n") or 0)
    admission = int(book.get("x1_admission_n") or 0)
    denied = int(book.get("x1_admission_denied_n") or 0)
    market = int(book.get("x1_market_event_n") or 0)
    stall = latched > 0 and admission == 0 and (denied > 0 or market >= 1000)
    return {
        "ok": not stall,
        "reason": "X1_ADMISSION_STALL" if stall else "",
        "x1_full_latched_n": latched,
        "x1_admission_n": admission,
        "x1_admission_denied_n": denied,
        "x1_market_event_n": market,
        "submit_cancel_live": "0/0/0",
    }


def x1_end_summary_identity_gate(summary: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    blob = summary or {}
    source = str(blob.get("summary_source_session_id") or blob.get("session_id") or "")
    kind = ""
    am_pm = blob.get("am_pm_session") if isinstance(blob.get("am_pm_session"), Mapping) else {}
    kind = str(am_pm.get("kind") or blob.get("session_kind") or "").strip().lower()
    full = blob.get("full_pm_summary")
    if kind != "pm":
        return {
            "ok": bool(source),
            "reason": "" if source else "SUMMARY_SOURCE_SESSION_MISSING",
            "summary_source_session_id": source,
            "full_pm_summary": True,
            "submit_cancel_live": "0/0/0",
        }
    ok = bool(source) and full is not None
    return {
        "ok": ok,
        "reason": "" if ok else "SUMMARY_SOURCE_IDENTITY_INCOMPLETE",
        "summary_source_session_id": source,
        "full_pm_summary": bool(full),
        "pm_segment_n": int(((blob.get("pm_day_aggregate") or {}) if isinstance(blob.get("pm_day_aggregate"), Mapping) else {}).get("segment_n") or 0),
        "submit_cancel_live": "0/0/0",
    }


def x1_pm_evaluation_alive_gate(heartbeat: Optional[Mapping[str, Any]], *, previous: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    hb = heartbeat or {}
    prev = previous or {}
    market = int(((hb.get("x1_executor") or {}) if isinstance(hb.get("x1_executor"), Mapping) else hb).get("x1_market_event_n") or 0)
    prev_market = int(((prev.get("x1_executor") or {}) if isinstance(prev.get("x1_executor"), Mapping) else prev).get("x1_market_event_n") or 0)
    push = int(hb.get("push_messages") or 0)
    prev_push = int(prev.get("push_messages") or 0)
    alive = bool(hb.get("x1_executor_alive")) or market > 0
    advancing = (not prev) or market > prev_market or push > prev_push
    ok = alive and advancing
    return {
        "ok": ok,
        "reason": "" if ok else "X1_PM_EVALUATION_NOT_ADVANCING",
        "x1_market_event_n": market,
        "push_messages": push,
        "submit_cancel_live": "0/0/0",
    }
