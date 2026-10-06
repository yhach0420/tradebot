"""Load Frozen Validation (+ exposed lookback only) and run Frozen V4 exactly once."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import pandas as pd

from research.cause_first_mechanism_discovery_v1.panel import load_minutes
from research.pb1_v4_clarified_machine_correction_v4.hidden1m import recompute_hidden_1m
from research.pb1_v4_clarified_machine_correction_v4.invariants import count_invariants
from research.pb1_v4_clarified_machine_correction_v4.walk import emit_v4
from research.pb1_v4_frozen_old_confirmation_blind_validation.walk_conf import (
    attach_causal,
    eligibility_rows,
)
from research.pb1_v4_frozen_validation_blind_confirmation import EVAL_FIRST, EVAL_LAST, PROSPECTIVE_FROM
from research.pb1_v4_frozen_old_confirmation_blind_validation.walk_conf import _index_events


def open_fv_minutes(
    *,
    symbols: list[str],
    lookback_dates: list[str],
    frozen_validation_dates: list[str],
) -> dict[str, Any]:
    allowed = set(lookback_dates) | set(frozen_validation_dates)
    forbidden = {d for d in allowed if d >= PROSPECTIVE_FROM}
    if allowed & forbidden:
        return {"ok": False, "reason": "allowed_forbidden_overlap", "minutes": pd.DataFrame()}
    if any(d < EVAL_FIRST or d > EVAL_LAST for d in frozen_validation_dates):
        return {"ok": False, "reason": "fv_dates_outside_range", "minutes": pd.DataFrame()}
    print(
        f"LOAD_MINUTES symbols={len(symbols)} lookback={len(lookback_dates)} "
        f"frozen_validation={len(frozen_validation_dates)} forbidden={len(forbidden)}",
        flush=True,
    )
    minutes = load_minutes(symbols=symbols, allowed_dates=allowed, forbidden_dates=forbidden)
    if minutes is None or minutes.empty:
        return {"ok": False, "reason": "empty_minutes", "minutes": pd.DataFrame()}
    minutes["date"] = minutes["date"].astype(str)
    loaded_dates = sorted(set(minutes["date"].astype(str).tolist()))
    prosp_hit = [d for d in loaded_dates if d >= PROSPECTIVE_FROM]
    if prosp_hit:
        return {
            "ok": False,
            "reason": "prospective_loaded",
            "minutes": pd.DataFrame(),
            "prospective_hit": prosp_hit[:8],
        }
    fv_loaded = [d for d in loaded_dates if EVAL_FIRST <= d <= EVAL_LAST]
    lookback_loaded = [d for d in loaded_dates if d < EVAL_FIRST]
    return {
        "ok": True,
        "minutes": minutes,
        "loaded_dates": loaded_dates,
        "frozen_validation_dates_loaded": fv_loaded,
        "lookback_dates_loaded": lookback_loaded,
        "FROZEN_VALIDATION_OPENED": True,
        "PROSPECTIVE_DATA_OPENED": False,
        "OLD_CONFIRMATION_REUSED_AS_BLIND": False,
    }


def filter_fv(walked: dict[str, Any], *, frozen_validation_dates: list[str]) -> dict[str, Any]:
    eval_dates = set(frozen_validation_dates)
    funnel_all = list(walked.get("funnel_days") or [])
    funnel = []
    e0_idx = _index_events([r for r in list(walked.get("e0_events") or []) if str(r.get("date") or "") in eval_dates])
    e1_idx = _index_events([r for r in list(walked.get("e1_events") or []) if str(r.get("date") or "") in eval_dates])
    leak_dates = []
    for r in funnel_all:
        d = str(r.get("date") or "")
        if d in eval_dates:
            key = (str(r.get("symbol") or ""), d)
            funnel.append(attach_causal(r, e0=e0_idx.get(key), e1=e1_idx.get(key)))
        elif d >= PROSPECTIVE_FROM:
            leak_dates.append(d)
    setups = [
        attach_causal(
            r,
            e0=e0_idx.get((str(r.get("symbol") or ""), str(r.get("date") or ""))),
            e1=e1_idx.get((str(r.get("symbol") or ""), str(r.get("date") or ""))),
        )
        for r in list(walked.get("setups") or [])
        if str(r.get("date") or "") in eval_dates
    ]
    e0 = [r for r in list(walked.get("e0_events") or []) if str(r.get("date") or "") in eval_dates]
    e1 = [r for r in list(walked.get("e1_events") or []) if str(r.get("date") or "") in eval_dates]
    hidden_checks = [r for r in list(walked.get("hidden_1m_checks") or []) if str(r.get("date") or "") in eval_dates]
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
        "lookback_funnel_dropped_n": sum(1 for r in funnel_all if str(r.get("date") or "") not in eval_dates),
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
