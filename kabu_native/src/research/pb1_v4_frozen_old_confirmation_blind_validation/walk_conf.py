"""Load Old Confirmation (+ Discovery lookback only) and run Frozen V4 exactly once.

V4 source files are not edited. `_load_panel` is patched at runtime for this harness.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import pandas as pd

from research.cause_first_mechanism_discovery_v1.panel import load_minutes
from research.pb1_v4_clarified_machine_correction_v4.hidden1m import recompute_hidden_1m
from research.pb1_v4_clarified_machine_correction_v4.invariants import count_invariants
from research.pb1_v4_clarified_machine_correction_v4.walk import emit_v4
from research.pb1_v4_frozen_old_confirmation_blind_validation import (
    EVAL_FIRST,
    EVAL_LAST,
    FV_FIRST,
    FV_LAST,
    PROSPECTIVE_FROM,
)
from research.pb1_v4_prospective_semantic_validation.session import quality_for_minutes
from research.pb1_v4_prospective_semantic_validation_preflight.logging import causal_timestamps


def _plus_min(hhmm: str, minutes: int) -> str:
    h = int(hhmm[:2])
    m = int(hhmm[3:5])
    tot = h * 60 + m + int(minutes)
    return f"{tot // 60:02d}:{tot % 60:02d}"


def open_confirmation_minutes(
    *,
    symbols: list[str],
    lookback_dates: list[str],
    confirmation_dates: list[str],
    frozen_validation_dates: list[str],
) -> dict[str, Any]:
    allowed = set(lookback_dates) | set(confirmation_dates)
    forbidden = set(frozen_validation_dates) | {d for d in allowed if d >= PROSPECTIVE_FROM}
    forbidden |= {d for d in allowed if FV_FIRST <= d <= FV_LAST}
    if allowed & forbidden:
        return {"ok": False, "reason": "allowed_forbidden_overlap", "minutes": pd.DataFrame()}
    if any(d > EVAL_LAST for d in confirmation_dates):
        return {"ok": False, "reason": "confirmation_dates_exceed_eval_last", "minutes": pd.DataFrame()}
    if any(d < EVAL_FIRST or d > EVAL_LAST for d in confirmation_dates):
        return {"ok": False, "reason": "confirmation_dates_outside_old_confirmation", "minutes": pd.DataFrame()}
    print(
        f"LOAD_MINUTES symbols={len(symbols)} lookback={len(lookback_dates)} "
        f"confirmation={len(confirmation_dates)} forbidden={len(forbidden)}",
        flush=True,
    )
    minutes = load_minutes(symbols=symbols, allowed_dates=allowed, forbidden_dates=forbidden)
    if minutes is None or minutes.empty:
        return {"ok": False, "reason": "empty_minutes", "minutes": pd.DataFrame()}
    minutes["date"] = minutes["date"].astype(str)
    loaded_dates = sorted(set(minutes["date"].astype(str).tolist()))
    fv_hit = [d for d in loaded_dates if FV_FIRST <= d <= FV_LAST]
    prosp_hit = [d for d in loaded_dates if d >= PROSPECTIVE_FROM]
    if fv_hit or prosp_hit:
        return {
            "ok": False,
            "reason": "sealed_holdout_loaded",
            "minutes": pd.DataFrame(),
            "fv_hit": fv_hit[:8],
            "prospective_hit": prosp_hit[:8],
        }
    conf_loaded = [d for d in loaded_dates if EVAL_FIRST <= d <= EVAL_LAST]
    lookback_loaded = [d for d in loaded_dates if d < EVAL_FIRST]
    return {
        "ok": True,
        "minutes": minutes,
        "loaded_dates": loaded_dates,
        "confirmation_dates_loaded": conf_loaded,
        "lookback_dates_loaded": lookback_loaded,
        "FROZEN_VALIDATION_OPENED": False,
        "PROSPECTIVE_DATA_OPENED": False,
        "OLD_CONFIRMATION_OPENED": True,
    }


def eligibility_rows(*, minutes: pd.DataFrame, symbols: list[str], confirmation_dates: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for session in confirmation_dates:
        q = quality_for_minutes(minutes, symbols=symbols, session=session)
        complete_n = int(q.get("complete_symbol_n") or 0)
        rows.append(
            {
                "date": session,
                "ok": complete_n >= 1,
                "complete_symbol_n": complete_n,
                "incomplete_symbol_n": int(q.get("incomplete_symbol_n") or 0),
                "INELIGIBLE_SESSION": complete_n < 1,
                "reason": None if complete_n >= 1 else "no_complete_symbol_day",
                "semantic_fail": False,
                "candidate_unit": "symbol-date",
                "prospective_min_active_not_applied": True,
            }
        )
    return rows


def _index_events(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        key = (str(r.get("symbol") or ""), str(r.get("date") or ""))
        if key not in out:
            out[key] = r
    return out


def attach_causal(row: dict[str, Any], *, e0: dict[str, Any] | None, e1: dict[str, Any] | None) -> dict[str, Any]:
    ev = e1 or e0
    lost_at = str(row.get("THESIS_LOST_AT") or "")[:5]
    if ev:
        entry = str(ev.get("entry_t") or "")[:5]
        done = _plus_min(entry, -1) if entry else (lost_at or "11:19")
        ts = causal_timestamps(event_completed_at=done)
        ts["entry_allowed_at"] = entry or ts.get("entry_allowed_at")
        ts["same_bar_entry"] = bool(ev.get("same_bar_entry")) or (entry == done)
        if ts["same_bar_entry"]:
            ts["entry_allowed_at"] = None
    elif lost_at:
        ts = causal_timestamps(event_completed_at=lost_at)
        if not (row.get("E0") or row.get("E1")):
            ts["entry_allowed_at"] = None
            ts["same_bar_entry"] = False
    else:
        ts = causal_timestamps(event_completed_at="11:19")
        ts["entry_allowed_at"] = None
        ts["same_bar_entry"] = False
    out = dict(row)
    out.update({k: ts.get(k) for k in ("information_available_at", "event_completed_at", "state_changed_at", "entry_allowed_at")})
    out["same_bar_entry"] = bool(ts.get("same_bar_entry"))
    out["backdating"] = False
    if out.get("entry_allowed_at") and out.get("event_completed_at"):
        if str(out["entry_allowed_at"])[:5] < str(out["event_completed_at"])[:5]:
            out["backdating"] = True
        if str(out["entry_allowed_at"])[:5] == str(out["event_completed_at"])[:5]:
            out["same_bar_entry"] = True
    return out


def filter_confirmation(walked: dict[str, Any], *, confirmation_dates: list[str]) -> dict[str, Any]:
    conf = set(confirmation_dates)
    funnel_all = list(walked.get("funnel_days") or [])
    funnel = []
    e0_idx = _index_events([r for r in list(walked.get("e0_events") or []) if str(r.get("date") or "") in conf])
    e1_idx = _index_events([r for r in list(walked.get("e1_events") or []) if str(r.get("date") or "") in conf])
    leak_dates = []
    for r in funnel_all:
        d = str(r.get("date") or "")
        if d in conf:
            key = (str(r.get("symbol") or ""), d)
            funnel.append(attach_causal(r, e0=e0_idx.get(key), e1=e1_idx.get(key)))
        elif EVAL_FIRST <= d <= EVAL_LAST:
            leak_dates.append(d)
        elif FV_FIRST <= d <= FV_LAST or d >= PROSPECTIVE_FROM:
            leak_dates.append(d)
    setups = [
        attach_causal(r, e0=e0_idx.get((str(r.get("symbol") or ""), str(r.get("date") or ""))), e1=e1_idx.get((str(r.get("symbol") or ""), str(r.get("date") or ""))))
        for r in list(walked.get("setups") or [])
        if str(r.get("date") or "") in conf
    ]
    e0 = [r for r in list(walked.get("e0_events") or []) if str(r.get("date") or "") in conf]
    e1 = [r for r in list(walked.get("e1_events") or []) if str(r.get("date") or "") in conf]
    hidden_checks = [r for r in list(walked.get("hidden_1m_checks") or []) if str(r.get("date") or "") in conf]
    counts: dict[str, Any] = defaultdict(int)
    for r in funnel:
        if r.get("WHY_THIS_STOCK"):
            counts["WHY_THIS_STOCK"] += 1
        seed = r.get("OPENING_DRIVE_SEED")
        if seed not in (None, "", "NO_VALID_DRIVE_SEED"):
            counts["OPENING_DRIVE_SEED"] += 1
        if r.get("OPENING_DRIVE_REACHED"):
            counts["OPENING_DRIVE_REACHED"] += 1
        if r.get("OPENING_DRIVE_LIVE") or r.get("OPENING_DRIVE_ACTIVE"):
            counts["OPENING_DRIVE_LIVE"] += 1
            counts["OPENING_DRIVE_ACTIVE"] += 1
        if r.get("LOCATION_IDENTIFIED"):
            counts["LOCATION_IDENTIFIED"] += 1
        if r.get("THESIS_REACHED"):
            counts["THESIS_REACHED"] += 1
        if r.get("THESIS_LIVE") or r.get("THESIS_READY"):
            counts["THESIS_LIVE"] += 1
        if r.get("THESIS_LOST"):
            counts["THESIS_LOST"] += 1
        loc = str(r.get("location_id") or "")
        th = str(r.get("thesis_id") or "")
        if "IX1M" in loc:
            counts["1M_created_location_n"] += 1
        if "IX1M" in th:
            counts["1M_created_thesis_n"] += 1
        if r.get("same_bar_entry"):
            counts["SAME_BAR_ENTRY"] += 1
        if r.get("backdating"):
            counts["backdating_n"] += 1
        reason = str(r.get("THESIS_LOST_REASON") or r.get("death") or "")
        if reason:
            counts[reason] += 1
    counts["E0"] = len(e0)
    counts["E1"] = len(e1)
    counts["setup_n"] = len(setups)
    counts["symbol_days"] = len(funnel)
    counts["HIDDEN_1M_THESIS_PARITY_FAIL"] = sum(1 for h in hidden_checks if not h.get("ok"))
    src_counts = dict(walked.get("counts") or {})
    counts["1M_created_seed_n"] = int(src_counts.get("1M_created_seed_n") or 0)
    counts["1M_created_active_n"] = int(src_counts.get("1M_created_active_n") or 0)
    counts["1M_changed_direction_n"] = int(src_counts.get("1M_changed_direction_n") or 0)
    counts["1M_changed_opening_drive_n"] = int(src_counts.get("1M_changed_opening_drive_n") or 0)
    counts["THESIS_REVIVED_BY_EXECUTION_n"] = int(src_counts.get("THESIS_REVIVED_BY_EXECUTION_n") or 0)
    counts["1M_created_location_n"] = int(counts["1M_created_location_n"])
    counts["E0_EMIT_FAIL_CLOSED_NOT_LIVE"] = int(src_counts.get("E0_EMIT_FAIL_CLOSED_NOT_LIVE") or 0)
    counts["identity_mismatch_n"] = int(src_counts.get("identity_mismatch_n") or 0)
    same_bar_events = sum(1 for r in list(e0) + list(e1) if r.get("same_bar_entry")) + int(counts["SAME_BAR_ENTRY"])
    sliced = {
        "ok": bool(walked.get("ok")),
        "setups": setups,
        "e0_events": e0,
        "e1_events": e1,
        "funnel_days": funnel,
        "hidden_1m_checks": hidden_checks,
        "counts": dict(counts),
        "same_bar_entry_n": int(same_bar_events),
        "HIDDEN_1M_THESIS_PARITY": (not hidden_checks) or all(bool(x.get("ok")) for x in hidden_checks),
        "lookback_funnel_dropped_n": sum(1 for r in funnel_all if str(r.get("date") or "") not in conf),
        "leak_dates": sorted(set(leak_dates)),
        "future_outcome_n": 0,
        "pnl_test": False,
        "mfe_mae": False,
    }
    hid = recompute_hidden_1m(walked=sliced)
    sliced["HIDDEN_1M_THESIS_PARITY"] = bool(hid.get("HIDDEN_1M_THESIS_PARITY_recomputed"))
    inv = count_invariants(funnel=funnel, walked=sliced)
    sliced["hidden1m"] = hid
    sliced["invariants_raw"] = inv
    return sliced


def walk_frozen(*, bind: dict[str, Any], minutes: pd.DataFrame, walk_dates: list[str], symbols: list[str]) -> dict[str, Any]:
    import research.pb1_v4_clarified_machine_correction_v4.walk as v4walk

    disc = list(walk_dates)
    local = dict(bind)
    split = dict(local.get("split") or {})
    split["discovery_dates"] = disc
    split["confirmation_dates"] = []
    split["frozen_validation_dates"] = []
    local["split"] = split
    local["symbols"] = list(symbols)

    def _load(_bind: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "minutes": minutes, "disc": disc, "symbols": list(symbols)}

    orig = v4walk._load_panel
    v4walk._load_panel = _load
    try:
        print(f"EMIT_V4_FROZEN walk_dates={len(disc)} symbols={len(symbols)}", flush=True)
        walked = emit_v4(local)
    finally:
        v4walk._load_panel = orig
    return walked
