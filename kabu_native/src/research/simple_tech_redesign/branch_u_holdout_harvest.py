"""Holdout harvest: frozen V1 B1 signals + frozen Branch U + occupancy. No date PnL filter."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research.am_entry_profit_improvement.publish import json_sanitize
from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import _peek_times, find_capture_dir
from research.simple_tech_entry_family.harvest import (
    _parse_iso,
    load_day_cache,
    process_day,
    sealed_day_caps,
)
from research.simple_tech_redesign.branch_u_bb_harvest import replay_branch_u_day, save_branch_u_day_cache
from research.simple_tech_redesign.branch_u_holdout_spec import DEV_PERIOD_END
from research.simple_tech_redesign.isolation import RESEARCH_CACHE, TODAY
from small_paper.v1r_live_dual_lane import session_end_for_position

HOLDOUT_CACHE = RESEARCH_CACHE / "branch_u_temporal_holdout_v1"
CAPTURE_ROOT = Path(__file__).resolve().parents[3] / "data" / "market_capture"
LOCKED_SERIES_DAYS = ("20260828", "20260831", "20260901", "20260902")


def _b1_from_opps(opps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in opps:
        if not r.get("s3"):
            continue
        out.append(
            {
                "date": r.get("date"),
                "session": "AM",
                "symbol": str(r.get("symbol") or "").replace(".T", ""),
                "t0": r.get("t0"),
                "trend": bool(r.get("s1")),
                "pullback": bool(r.get("s2")),
                "rci": bool(r.get("s3")),
                "board_ok": bool(r.get("s6")),
                "s1": bool(r.get("s1")),
                "s2": bool(r.get("s2")),
                "s3": bool(r.get("s3")),
            }
        )
    out.sort(key=lambda e: (str(e.get("date") or ""), float(e.get("t0") or 0.0), str(e.get("symbol") or "")))
    return out


def _am_complete(day: str, last_iso: str) -> bool:
    last_t = _parse_iso(last_iso)
    if last_t is None:
        return False
    am_start = float(hm_epoch(day, 9, 0))
    am_end = float(session_end_for_position(date=day, session="AM", fill_time=am_start + 60.0))
    return float(last_t) + 1e-12 >= float(am_end)


def list_capture_days_after(dev_end: str) -> list[str]:
    if not CAPTURE_ROOT.is_dir():
        return []
    days = []
    for p in CAPTURE_ROOT.iterdir():
        if p.is_dir() and p.name.isdigit() and p.name > str(dev_end):
            days.append(p.name)
    return sorted(days)


def discover_holdout_days(*, today: str = TODAY, dev_end: str = DEV_PERIOD_END) -> dict[str, Any]:
    """All complete sealed captures after development end. No PnL filter. Today excluded."""
    scanned = list_capture_days_after(dev_end)
    kept: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for day in scanned:
        cap = find_capture_dir(day)
        rec = {"date": day, "capture_path": str(cap) if cap is not None else ""}
        if day == str(today):
            rec["reason"] = "ACTIVE_OR_INCOMPLETE_TODAY"
            excluded.append(rec)
            continue
        if cap is None:
            rec["reason"] = "CAPTURE_MISSING"
            excluded.append(rec)
            continue
        first, last, n_ev, size = _peek_times(cap)
        rec["first_event_at"] = first
        rec["last_event_at"] = last
        rec["capture_bytes"] = int(size)
        rec["summary_event_n"] = int(n_ev)
        if int(size) <= 0:
            rec["reason"] = "CAPTURE_EMPTY"
            excluded.append(rec)
            continue
        if not _am_complete(day, last):
            rec["reason"] = "AM_SESSION_INCOMPLETE"
            excluded.append(rec)
            continue
        rec["reason"] = "COMPLETE_SEALED_AM"
        rec["ok"] = True
        kept.append(rec)
    days = [str(r["date"]) for r in kept]
    caps = sealed_day_caps(days, str(today)) if days else []
    by_day = {str(c.get("date") or ""): c for c in caps}
    usable = []
    for rec in kept:
        day = str(rec["date"])
        cap = dict(by_day.get(day) or {})
        rec["universe_n"] = int(cap.get("universe_n") or 0)
        rec["universe_source"] = cap.get("universe_source")
        rec["universe_ok"] = bool(cap.get("ok"))
        if not cap.get("ok"):
            rec["reason"] = "UNIVERSE_INCOMPLETE"
            excluded.append(rec)
            continue
        rec["capture_path"] = str(cap.get("capture_path") or rec.get("capture_path") or "")
        rec["universe_symbols"] = list(cap.get("universe_symbols") or [])
        usable.append(rec)
    return {
        "scanned_days": scanned,
        "holdout_days": [str(r["date"]) for r in usable],
        "usable": usable,
        "excluded": excluded,
        "today": str(today),
        "dev_period_end": str(dev_end),
        "date_cherry_pick": False,
    }


def inspect_capture_day(day: str, *, today: str) -> dict[str, Any]:
    rec: dict[str, Any] = {"date": day, "capture_path": ""}
    if day == str(today):
        rec["reason"] = "ACTIVE_OR_INCOMPLETE_TODAY"
        rec["complete"] = False
        return rec
    cap = find_capture_dir(day)
    rec["capture_path"] = str(cap) if cap is not None else ""
    if cap is None:
        rec["reason"] = "CAPTURE_MISSING"
        rec["complete"] = False
        return rec
    first, last, n_ev, size = _peek_times(cap)
    rec["first_event_at"] = first
    rec["last_event_at"] = last
    rec["capture_bytes"] = int(size)
    rec["summary_event_n"] = int(n_ev)
    if int(size) <= 0:
        rec["reason"] = "CAPTURE_EMPTY"
        rec["complete"] = False
        return rec
    if not _am_complete(day, last):
        rec["reason"] = "AM_SESSION_INCOMPLETE"
        rec["complete"] = False
        rec["am_complete"] = False
        return rec
    rec["reason"] = "COMPLETE_SEALED_AM"
    rec["complete"] = True
    rec["am_complete"] = True
    rec["ok"] = True
    return rec


def plan_append(*, today: str = TODAY, locked: tuple[str, ...] = LOCKED_SERIES_DAYS) -> dict[str, Any]:
    """Keep locked series days. Append oldest complete captures after last locked day. Do not skip an incomplete next capture."""
    locked_days = [str(d) for d in locked]
    last = locked_days[-1] if locked_days else DEV_PERIOD_END
    discovered = discover_holdout_days(today=str(today))
    usable_by = {str(r.get("date") or ""): r for r in list(discovered.get("usable") or [])}
    later = [d for d in list_capture_days_after(DEV_PERIOD_END) if d > last]
    appended: list[str] = []
    blocked: dict[str, Any] | None = None
    for day in later:
        rec = inspect_capture_day(day, today=str(today))
        if str(day) == str(today) or not rec.get("complete"):
            blocked = rec
            break
        if day not in usable_by:
            blocked = {**rec, "reason": "UNIVERSE_INCOMPLETE", "complete": False}
            break
        appended.append(day)
    holdout_days = locked_days + appended
    missing_locked = [d for d in locked_days if d not in usable_by]
    expected_prefix: list[str] = []
    for day in later:
        if blocked and day == str(blocked.get("date") or ""):
            break
        expected_prefix.append(day)
    cherry = appended != expected_prefix
    return {
        "locked_days": locked_days,
        "appended_days": appended,
        "holdout_days": holdout_days,
        "usable": [usable_by[d] for d in holdout_days if d in usable_by],
        "blocked": blocked,
        "later_capture_days": later,
        "missing_locked": missing_locked,
        "date_cherry_pick": bool(cherry),
        "today": str(today),
        "discovery": discovered,
    }


def _signal_cache_path(day: str) -> Path:
    return HOLDOUT_CACHE / f"b1_day_{day}.json"


def _u_cache_path(day: str) -> Path:
    return HOLDOUT_CACHE / f"branch_u_day_{day}.json"


def harvest_b1_day(cap: dict[str, Any], *, v1_sha: str) -> dict[str, Any]:
    day = str(cap["date"])
    path = _signal_cache_path(day)
    cached = load_day_cache(path, v1_sha)
    if cached and cached.get("ok"):
        return cached
    body = process_day(
        {
            "date": day,
            "capture_path": cap["capture_path"],
            "universe": list(cap.get("universe_symbols") or []),
            "spec_sha": v1_sha,
        }
    )
    if not body.get("ok"):
        return body
    rows = _b1_from_opps(list(body.get("opps") or []))
    slim = json_sanitize(
        {
            "ok": True,
            "date": day,
            "spec_sha": v1_sha,
            "events_n": body.get("events_n"),
            "last_et": body.get("last_et"),
            "integ_fail_n": body.get("integ_fail_n"),
            "leak": body.get("leak"),
            "elapsed_sec": body.get("elapsed_sec"),
            "opp_n": len(list(body.get("opps") or [])),
            "signal_n": len(rows),
            "rows": rows,
            "blocker": None,
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(slim, ensure_ascii=False, default=str), encoding="utf-8")
    return slim


def harvest_branch_u_day(cap: dict[str, Any], signals: list[dict[str, Any]], *, u_sha: str) -> dict[str, Any]:
    day = str(cap["date"])
    path = _u_cache_path(day)
    cached = load_day_cache(path, u_sha)
    if cached and int(cached.get("signal_n") or 0) == len(signals) and len(list(cached.get("rows") or [])) == len(signals):
        return cached
    body = replay_branch_u_day(
        {"date": day, "capture_path": cap["capture_path"], "signals": signals, "spec_sha": u_sha}
    )
    if body.get("ok"):
        save_branch_u_day_cache(path, body)
        return load_day_cache(path, u_sha) or body
    return body


def provenance_row(day: str, *, studies: list[dict[str, Any]]) -> dict[str, Any]:
    used_by = []
    reasons = []
    for st in studies:
        if day in set(st.get("input_days") or []):
            used_by.append(str(st.get("name") or ""))
            reasons.append(str(st.get("if_used_reason") or "research_input_day"))
        if day in set(st.get("preflight_today_or_active_capture_days") or []):
            reasons.append(f"{st.get('name')}:preflight_active_or_today_snapshot_only")
    previously = bool(used_by)
    if previously:
        reason = "rule_or_input_day:" + ",".join(used_by)
    elif reasons:
        reason = (
            "not_an_input_day_of_V26_V29_lifecycle_U_one_shot_U_causal;"
            + ";".join(reasons)
            + ";TRUE_OOS_not_claimed"
        )
    else:
        reason = "not_an_input_day_of_V26_V29_lifecycle_U_one_shot_U_causal"
    return {
        "date": day,
        "previously_used": previously,
        "used_by": used_by,
        "reason": reason,
    }
