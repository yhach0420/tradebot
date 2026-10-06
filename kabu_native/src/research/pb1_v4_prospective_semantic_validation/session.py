"""Session eligibility + completeness. Incomplete => INELIGIBLE_SESSION, not semantic FAIL."""
from __future__ import annotations

from typing import Any

import pandas as pd

from research.one_minute_native_playbook_discovery_v1.states import prep_symbol
from research.pb1_opening_range_continuation_face_valid_v1.or15 import session_idx_of
from research.pb1_v4_prospective_semantic_validation import MIN_ACTIVE_SYMBOLS
from research.pb1_v4_prospective_semantic_validation_preflight.completeness import quality_gate
from research.pb1_v4_prospective_semantic_validation_preflight.eligibility import resolve_jp_cash_session


def _rec_from_group(sg: pd.DataFrame) -> dict[str, Any]:
    rec = prep_symbol(sg)
    rec["session_idx"] = session_idx_of(rec["t"])
    return rec


def quality_for_minutes(minutes: pd.DataFrame, *, symbols: list[str], session: str) -> dict[str, Any]:
    if minutes is None or getattr(minutes, "empty", True):
        return {
            "ok": False,
            "reason": "no_minutes",
            "complete_symbol_n": 0,
            "incomplete_symbol_n": 0,
            "min_active": MIN_ACTIVE_SYMBOLS,
            "INELIGIBLE_SESSION": False,
        }
    g = minutes[minutes["date"].astype(str) == str(session)]
    by = {str(s): sg for s, sg in g.groupby("symbol", sort=False)}
    complete = []
    incomplete = []
    for sym in symbols:
        sg = by.get(str(sym))
        if sg is None or sg.empty:
            incomplete.append({"symbol": str(sym), "reason": "missing_symbol"})
            continue
        rec = _rec_from_group(sg)
        q = quality_gate(rec)
        if q.get("ok"):
            complete.append(str(sym))
        else:
            incomplete.append({"symbol": str(sym), "reason": "incomplete_am"})
    ok = len(complete) >= int(MIN_ACTIVE_SYMBOLS)
    return {
        "ok": ok,
        "reason": None if ok else "insufficient_complete_symbols",
        "complete_symbol_n": len(complete),
        "incomplete_symbol_n": len(incomplete),
        "complete_symbols": complete,
        "incomplete_sample": incomplete[:12],
        "min_active": MIN_ACTIVE_SYMBOLS,
        "INELIGIBLE_SESSION": not ok,
        "semantic_fail": False,
        "counts_as_session": ok,
    }


def session_gate(*, session: str, symbols: list[str], ledger_keys: set[tuple[str, str]], minutes: pd.DataFrame | None, allow_open: bool) -> dict[str, Any]:
    q = quality_for_minutes(minutes if minutes is not None else pd.DataFrame(), symbols=symbols, session=session)
    cal = resolve_jp_cash_session(
        session,
        ledger_keys=ledger_keys,
        completeness={"ok": bool(q.get("ok"))},
        allow_open_prospective=allow_open,
    )
    data_complete = bool(cal.get("is_tse_cash_session")) and bool(q.get("ok")) and allow_open
    return {
        "session": session,
        "calendar": cal,
        "quality": q,
        "data_complete": data_complete,
        "semantic_fail": False,
    }
