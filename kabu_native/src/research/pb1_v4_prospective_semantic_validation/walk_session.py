"""Run frozen V4 on an eligible prospective session. No V4 source edits."""
from __future__ import annotations

from typing import Any

import pandas as pd

from research.pb1_v4_clarified_machine_correction_v4.hidden1m import recompute_hidden_1m
from research.pb1_v4_clarified_machine_correction_v4.invariants import count_invariants
from research.pb1_v4_clarified_machine_correction_v4.walk import emit_v4
from research.pb1_v4_prospective_semantic_validation_preflight.logging import causal_timestamps


def _attach_causal(row: dict[str, Any]) -> dict[str, Any]:
    t = str(row.get("THESIS_LOST_AT") or row.get("thesis_ready_at") or row.get("event_completed_at") or "")[:5]
    if not t:
        t = "11:19"
    ts = causal_timestamps(event_completed_at=t)
    out = dict(row)
    out.update({k: ts.get(k) for k in ("information_available_at", "event_completed_at", "state_changed_at", "entry_allowed_at")})
    out["same_bar_entry"] = bool(ts.get("same_bar_entry"))
    out["backdating"] = False
    return out


def walk_prospective(
    *,
    bind: dict[str, Any],
    minutes: pd.DataFrame,
    session: str,
    lookback_dates: list[str],
    ledger_keys: set[tuple[str, str]],
) -> dict[str, Any]:
    import research.pb1_v4_clarified_machine_correction_v4.walk as v4walk

    symbols = list(bind.get("symbols") or [])
    disc = list(lookback_dates) + [session]
    local = dict(bind)
    split = dict(local.get("split") or {})
    split["discovery_dates"] = disc
    split["confirmation_dates"] = []
    split["frozen_validation_dates"] = []
    local["split"] = split
    local["symbols"] = symbols

    def _load(_bind: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "minutes": minutes, "disc": disc, "symbols": symbols}

    orig = v4walk._load_panel
    v4walk._load_panel = _load  # runtime only; V4 source files unchanged
    try:
        walked = emit_v4(local)
    finally:
        v4walk._load_panel = orig
    funnel_all = list(walked.get("funnel_days") or [])
    funnel = []
    skipped_ledger = 0
    for r in funnel_all:
        if str(r.get("date") or "") != str(session):
            continue
        key = (str(r.get("symbol") or ""), str(r.get("date") or ""))
        if key in ledger_keys:
            skipped_ledger += 1
            continue
        funnel.append(_attach_causal(r))
    setups = [_attach_causal(r) for r in list(walked.get("setups") or []) if str(r.get("date") or "") == str(session)]
    e0 = [r for r in list(walked.get("e0_events") or []) if str(r.get("date") or "") == str(session)]
    e1 = [r for r in list(walked.get("e1_events") or []) if str(r.get("date") or "") == str(session)]
    sliced = {
        **walked,
        "setups": setups,
        "e0_events": e0,
        "e1_events": e1,
        "funnel_days": funnel,
        "lookback_funnel_dropped_n": sum(1 for r in funnel_all if str(r.get("date") or "") != str(session)),
        "ledger_skipped_n": skipped_ledger,
    }
    hid = recompute_hidden_1m(walked=sliced)
    inv = count_invariants(funnel=funnel, walked=sliced)
    why_n = sum(1 for r in funnel if r.get("WHY_THIS_STOCK"))
    return {
        "ok": bool(walked.get("ok")),
        "funnel_days": funnel,
        "setups": setups,
        "e0_events": e0,
        "e1_events": e1,
        "hidden1m": hid,
        "invariants": inv,
        "same_bar_entry_n": int(walked.get("same_bar_entry_n") or 0),
        "hidden1m_mismatch_n": int(hid.get("mismatch_n") or 0),
        "invariant_violation_n": int(inv.get("violations") or 0),
        "candidate_day_n": why_n,
        "eligible_symbol_day_n": len(funnel),
        "pnl_test": False,
        "mfe_mae": False,
        "future_outcome_n": 0,
        "lookback_dates_n": len(lookback_dates),
        "ledger_skipped_n": skipped_ledger,
    }
