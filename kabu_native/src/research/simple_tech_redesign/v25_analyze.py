"""V25 reproduce V24 corrected identity, audit six special cases. No PnL gate. No legacy 126/38/88 gate."""
from __future__ import annotations

from collections import Counter
from typing import Any, Optional

from research.simple_tech_entry_family.v13_analyze import set_hash
from research.simple_tech_redesign.v22_analyze import fill_tuples_e4
from research.simple_tech_redesign.v25_spec import (
    B1_SIGNAL_N_EXPECTED,
    CANONICAL_FRESHNESS_SEC,
    CORRECTED_E4_FILLED_N_EXPECTED,
    CORRECTED_E4_NONFILLED_N_EXPECTED,
    CORRECTED_EVALUABLE_N_EXPECTED,
    CORRECTED_FILL_HASH_EXPECTED,
    CORRECTED_UNEVALUABLE_N_EXPECTED,
    SPECIAL_CASES,
)


def _finite(v: Any) -> bool:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return False
    return x == x


def _fresh_ok(age: Any) -> bool:
    return _finite(age) and float(age) <= float(CANONICAL_FRESHNESS_SEC) + 1e-12


def _find(rows: list[dict[str, Any]], date: str, symbol: str) -> Optional[dict[str, Any]]:
    want = str(symbol).replace(".T", "")
    for r in rows:
        if str(r.get("date") or "") == str(date) and str(r.get("symbol") or "").replace(".T", "") == want:
            return r
    return None


def special_pack(row: dict[str, Any] | None, *, kind: str, case_id: str) -> dict[str, Any]:
    if row is None:
        return {"id": case_id, "kind": kind, "found": False, "explained": False, "reason": "IDENTITY_NOT_FOUND"}
    e4 = dict(row.get("e4") or {})
    v22 = dict(row.get("v22_e4") or {})
    board_age = row.get("board_age_t0_sec")
    price_age = row.get("price_age_t0_sec")
    explained = False
    reason = str(row.get("funnel_reason") or row.get("ask_reason") or "")
    if kind == "BOARD_STALE_PRICE_FRESH":
        explained = (
            (not bool(row.get("executable_signal")))
            and (not _fresh_ok(board_age))
            and _fresh_ok(price_age)
            and str(row.get("ask_reason") or "") == "STALE"
        )
        reason = (
            f"board_age_t0={board_age} price_age_t0={price_age} "
            f"source={row.get('BOARD_CLOCK_SOURCE')} ask_reason={row.get('ask_reason')}"
        )
    elif kind == "LEGACY_NONFILL_TO_CORRECTED_FILL":
        explained = (not bool(v22.get("filled"))) and bool(row.get("e4_filled"))
        reason = (
            f"v22_nonfill={v22.get('nonfill_class')} v24_fill_t={e4.get('fill_t')} "
            f"limit={e4.get('limit_price')} fill_price={e4.get('fill_price')}"
        )
    elif kind == "SAME_PRICE_EARLIER_FILL":
        dt = None
        if _finite(v22.get("fill_t")) and _finite(e4.get("fill_t")):
            dt = float(e4["fill_t"]) - float(v22["fill_t"])
        same_px = _finite(v22.get("fill_price")) and _finite(e4.get("fill_price")) and abs(float(v22["fill_price"]) - float(e4["fill_price"])) <= 1e-12
        explained = bool(v22.get("filled")) and bool(row.get("e4_filled")) and same_px and dt is not None and dt < -1e-12
        reason = f"fill_price={e4.get('fill_price')} v22_fill_t={v22.get('fill_t')} v24_fill_t={e4.get('fill_t')} delta_sec={dt}"
    return {
        "id": case_id,
        "kind": kind,
        "found": True,
        "explained": bool(explained),
        "reason": reason,
        "date": row.get("date"),
        "symbol": str(row.get("symbol") or "").replace(".T", ""),
        "t0": row.get("t0"),
        "BOARD_CLOCK_SOURCE": row.get("BOARD_CLOCK_SOURCE"),
        "board_timestamp": row.get("board_timestamp"),
        "price_timestamp": row.get("price_timestamp"),
        "ingress_timestamp": row.get("ingress_timestamp"),
        "board_age_t0_sec": board_age,
        "price_age_t0_sec": price_age,
        "ingress_age_t0_sec": row.get("ingress_age_t0_sec"),
        "limit_price": e4.get("limit_price"),
        "fill_timestamp": e4.get("fill_t"),
        "fill_price": e4.get("fill_price"),
        "v22_filled": bool(v22.get("filled")),
        "v24_filled": bool(row.get("e4_filled")),
        "executable_signal": bool(row.get("executable_signal")),
        "funnel_reason": row.get("funnel_reason"),
        "board_raw_after_t0": bool(row.get("board_raw_after_t0")),
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    evaluable = [r for r in rows if r.get("executable_signal")]
    filled = [r for r in evaluable if r.get("e4_filled")]
    nonfill = [r for r in evaluable if not r.get("e4_filled")]
    uneval = [r for r in rows if not r.get("executable_signal")]
    sources = Counter(str(r.get("BOARD_CLOCK_SOURCE") or r.get("BOARD_FRESHNESS_CLOCK_SOURCE") or "UNRESOLVED") for r in rows)
    specials = {}
    all_explained = True
    for spec in SPECIAL_CASES:
        pack = special_pack(_find(rows, str(spec["date"]), str(spec["symbol"])), kind=str(spec["kind"]), case_id=str(spec["id"]))
        specials[str(spec["id"])] = pack
        if not pack.get("explained"):
            all_explained = False
    fill_hash = set_hash(fill_tuples_e4(rows))
    return {
        "SIGNAL_N": len(rows),
        "CORRECTED_EXECUTION_EVALUABLE_N": len(evaluable),
        "CORRECTED_EXECUTION_UNEVALUABLE_N": len(uneval),
        "CORRECTED_E4_FILLED_N": len(filled),
        "CORRECTED_E4_NONFILLED_N": len(nonfill),
        "CORRECTED_FILL_HASH": fill_hash,
        "BOARD_CLOCK_SOURCE_COUNTS": dict(sources),
        "specials": specials,
        "ALL_SPECIAL_CASES_EXPLAINED": bool(all_explained and len(specials) == 6),
        "UNEVAL_SPLIT": dict(Counter(str(r.get("uneval_class") or "other") for r in uneval)),
    }


def decide(
    summary: dict[str, Any],
    *,
    signal_parity: bool,
    leak_ok: bool,
    ni_ok: bool,
    future_board_n: int,
    future_ts_n: int,
    future_quote_n: int,
    queue_n: int,
    touch_n: int,
) -> dict[str, Any]:
    counts_ok = (
        int(summary.get("SIGNAL_N") or 0) == int(B1_SIGNAL_N_EXPECTED)
        and int(summary.get("CORRECTED_EXECUTION_EVALUABLE_N") or 0) == int(CORRECTED_EVALUABLE_N_EXPECTED)
        and int(summary.get("CORRECTED_EXECUTION_UNEVALUABLE_N") or 0) == int(CORRECTED_UNEVALUABLE_N_EXPECTED)
        and int(summary.get("CORRECTED_E4_FILLED_N") or 0) == int(CORRECTED_E4_FILLED_N_EXPECTED)
        and int(summary.get("CORRECTED_E4_NONFILLED_N") or 0) == int(CORRECTED_E4_NONFILLED_N_EXPECTED)
    )
    hash_ok = str(summary.get("CORRECTED_FILL_HASH") or "") == CORRECTED_FILL_HASH_EXPECTED
    specials_ok = bool(summary.get("ALL_SPECIAL_CASES_EXPLAINED"))
    clock_ok = (
        int(future_board_n) == 0
        and int(future_ts_n) == 0
        and int(future_quote_n) == 0
        and int(queue_n) == 0
        and int(touch_n) == 0
    )
    if (not clock_ok) or (not leak_ok) or (not ni_ok):
        case = "C"
        verdict = "SIMPLE_TECH_V25_BOARD_CLOCK_CAUSALITY_UNRESOLVED"
        next_step = "STOP. Board clock causality unresolved. Do not accept a new execution baseline."
    elif (not signal_parity) or (not counts_ok) or (not hash_ok):
        case = "B"
        verdict = "SIMPLE_TECH_V25_CORRECTED_EXECUTION_NOT_REPRODUCIBLE"
        next_step = "STOP. V24 corrected identity was not reproduced."
    elif not specials_ok:
        case = "B"
        verdict = "SIMPLE_TECH_V25_CORRECTED_EXECUTION_NOT_REPRODUCIBLE"
        next_step = "STOP. Six reconciliation identities were not all explained."
    else:
        case = "A"
        verdict = "SIMPLE_TECH_V25_CORRECTED_EXECUTION_BASELINE_ACCEPTED"
        next_step = (
            "New development execution baseline: SIGNAL_N=275, EXECUTION_EVALUABLE_N=273, E4_FILL_N=52. "
            "Timestamp/freshness research closes. Next: joint ENTRY coverage + technical EXIT. "
            "Do not return to ENTRY-only precision maximization. EXIT must not use elapsed seconds. "
            "221 corrected E4 nonfills remain the coverage expansion surface. 52 fills/day is not final coverage."
        )
    return {
        "CASE": case,
        "VERDICT": verdict,
        "NEXT": next_step,
        "STRATEGY_RULE_PARITY": bool(signal_parity),
        "CORRECTED_FILL_HASH_PARITY": bool(hash_ok),
        "COUNTS_PARITY": bool(counts_ok),
        "BOARD_CLOCK_CAUSALITY_PASS": bool(clock_ok),
        "SPECIAL_CASES_EXPLAINED": bool(specials_ok),
        "ENTRY_CHANGED": False,
        "E4_CHANGED": False,
        "FRESHNESS_THRESHOLD_CHANGED": False,
        "TRUE_OOS": False,
        "PNL_USED_FOR_ACCEPT": False,
        "LEGACY_126_38_88_REQUIRED": False,
        "NEW_BASELINE_SIGNAL_N": 275 if case == "A" else None,
        "NEW_BASELINE_EXECUTION_EVALUABLE_N": 273 if case == "A" else None,
        "NEW_BASELINE_E4_FILL_N": 52 if case == "A" else None,
    }
