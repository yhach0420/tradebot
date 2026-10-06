"""Load frozen V1 PRE_CAP pools read-only. Attrition from occupancy traces. No capture restream."""
from __future__ import annotations

import copy
import json
from collections import Counter
from typing import Any, Optional

from research.simple_tech_redesign.isolation import TODAY
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_harvest import (
    DAY_CACHE as V1_DAY_CACHE,
    assert_research_day,
    cohort_for,
    in_pre_cap_pool,
)
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_r1_spec import (
    BLOCK_A_DISCOVERY,
    BLOCK_B_INTERNAL_STABILITY,
    BLOCK_C_BURNED_STRESS,
    EXPECTED_ATTRITION_N,
    EXPECTED_OUTCOME_EVALUABLE_N,
    EXPECTED_POOL_N,
    FEATURES,
    FORBIDDEN_INPUT_DAYS,
    MAX_RESEARCH_DATE,
    all_research_days,
    block_of,
)
from research.simple_tech_redesign.slot_release_marginal_admission_quality_rca_harvest import (
    _f,
    _tid,
    _tid_s,
    load_frozen_day,
)

BLOCK_KEYS = (
    "BLOCK_A_DISCOVERY",
    "BLOCK_B_INTERNAL_STABILITY",
    "BLOCK_C_BURNED_STRESS",
)
REASON_CODES = (
    "HAS_OUTCOME",
    "NO_FILL",
    "EXECUTION_UNEVALUABLE",
    "NO_SESSION_CLOSE_QUOTE",
    "MISSING_OUTCOME",
    "OTHER",
    "UNKNOWN_NO_OCCUPANCY_ROW",
    "UNKNOWN",
)


def classify_outcome(candidate: dict[str, Any], occupancy_row: Optional[dict[str, Any]]) -> dict[str, Any]:
    pnl = _f(candidate.get("session_close_pnl"))
    if pnl is not None:
        return {
            "reason": "HAS_OUTCOME",
            "explained": True,
            "executable_signal": None if occupancy_row is None else occupancy_row.get("executable_signal"),
            "actual_filled": bool(candidate.get("actual_filled") or (occupancy_row or {}).get("actual_filled")),
            "fill_price": candidate.get("fill_price") if candidate.get("fill_price") is not None else (occupancy_row or {}).get("fill_price"),
            "fill_t": candidate.get("fill_t") if candidate.get("fill_t") is not None else (occupancy_row or {}).get("fill_t"),
            "control_exit_present": bool((occupancy_row or {}).get("control_exit")),
        }
    rec = occupancy_row
    if rec is None:
        return {
            "reason": "UNKNOWN_NO_OCCUPANCY_ROW",
            "explained": False,
            "executable_signal": None,
            "actual_filled": bool(candidate.get("actual_filled")),
            "fill_price": candidate.get("fill_price"),
            "fill_t": candidate.get("fill_t"),
            "control_exit_present": False,
        }
    exec_ok = rec.get("executable_signal")
    filled = bool(rec.get("actual_filled"))
    fill_px = _f(rec.get("fill_price"))
    fill_t = _f(rec.get("fill_t"))
    ce = dict(rec.get("control_exit") or {})
    ce_pnl = _f(ce.get("pnl_yen_100"))
    fresh = dict(ce.get("freshness_evidence") or {})
    if exec_ok is False:
        reason = "EXECUTION_UNEVALUABLE"
        explained = True
    elif (not filled) and fill_px is None and fill_t is None:
        reason = "NO_FILL"
        explained = True
    elif filled and ce_pnl is None:
        no_quote = (not ce) or bool(ce.get("miss")) or (fresh and fresh.get("executable") is False)
        reason = "NO_SESSION_CLOSE_QUOTE" if no_quote else "MISSING_OUTCOME"
        explained = True
    elif filled and ce_pnl is not None:
        reason = "MISSING_OUTCOME"
        explained = True
    else:
        reason = "OTHER"
        explained = True
    return {
        "reason": reason,
        "explained": explained,
        "executable_signal": exec_ok,
        "actual_filled": filled,
        "fill_price": rec.get("fill_price"),
        "fill_t": rec.get("fill_t"),
        "control_exit_present": bool(ce),
    }


def _load_v1_day(day: str) -> dict[str, Any]:
    path = V1_DAY_CACHE / f"day_{day}.json"
    if not path.is_file():
        return {"ok": False, "blocker": "V1_DAY_CACHE_MISSING", "date": day}
    body = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(body, dict) or not body.get("ok"):
        return {"ok": False, "blocker": "V1_DAY_CACHE_INVALID", "date": day}
    return body


def _occ_index(day: str) -> tuple[Optional[dict[tuple[str, str, float], dict[str, Any]]], Optional[str]]:
    occ = load_frozen_day(str(day), cohort=cohort_for(day))
    if not occ.get("ok"):
        return None, str(occ.get("blocker") or "OCCUPANCY_CACHE_MISSING")
    idx: dict[tuple[str, str, float], dict[str, Any]] = {}
    for r in list(occ.get("rows") or []):
        key = _tid(r)
        if key is not None:
            idx[key] = r
    return idx, None


def annotate_pool_day(day: str, body: dict[str, Any]) -> dict[str, Any]:
    idx, occ_block = _occ_index(day)
    pool = [copy.deepcopy(c) for c in list(body.get("pool") or []) if in_pre_cap_pool(c)]
    attrition: list[dict[str, Any]] = []
    unexplained = 0
    for c in pool:
        key = _tid(c)
        rec = idx.get(key) if idx is not None and key is not None else None
        pack = classify_outcome(c, rec)
        if occ_block and _f(c.get("session_close_pnl")) is None and rec is None:
            pack = {
                "reason": "UNKNOWN_NO_OCCUPANCY_ROW",
                "explained": False,
                "executable_signal": None,
                "actual_filled": bool(c.get("actual_filled")),
                "fill_price": c.get("fill_price"),
                "fill_t": c.get("fill_t"),
                "control_exit_present": False,
                "occupancy_blocker": occ_block,
            }
        c["outcome_reason"] = pack["reason"]
        c["outcome_explained"] = bool(pack.get("explained"))
        if _f(c.get("session_close_pnl")) is None:
            if not pack.get("explained"):
                unexplained += 1
            attrition.append(
                {
                    "date": c.get("date") or day,
                    "symbol": c.get("symbol"),
                    "trade_id": c.get("trade_id") or (_tid_s(key) if key else None),
                    "block": block_of(day),
                    "control_admitted": bool(c.get("control_admitted")),
                    "cap_only_blocked": bool(c.get("cap_only_blocked")),
                    "fill_role": c.get("fill_role"),
                    "arrival_third": c.get("arrival_third"),
                    "reason": pack["reason"],
                    "explained": bool(pack.get("explained")),
                    "executable_signal": pack.get("executable_signal"),
                    "actual_filled": pack.get("actual_filled"),
                    "fill_price": pack.get("fill_price"),
                    "fill_t": pack.get("fill_t"),
                    "control_exit_present": pack.get("control_exit_present"),
                    "occupancy_blocker": pack.get("occupancy_blocker"),
                }
            )
    out = copy.deepcopy(body)
    out["pool"] = pool
    out["pool_n"] = len(pool)
    out["attrition_rows"] = attrition
    out["attrition_n"] = len(attrition)
    out["attrition_unexplained_n"] = int(unexplained)
    out["occupancy_join_ok"] = occ_block is None
    out["loaded_from"] = "V1_DAY_CACHE_READONLY"
    out["capture_restreamed"] = False
    return out


def _bias(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def _count(key: str) -> dict[str, int]:
        c: Counter[str] = Counter(str(r.get(key) if r.get(key) is not None else "NONE") for r in rows)
        return dict(c)

    return {
        "n": len(rows),
        "by_reason": _count("reason"),
        "by_control_admitted": _count("control_admitted"),
        "by_cap_only_blocked": _count("cap_only_blocked"),
        "by_fill_role": _count("fill_role"),
        "by_arrival_third": _count("arrival_third"),
        "by_day": _count("date"),
        "by_symbol": _count("symbol"),
        "all_cap_only_blocked": bool(rows) and all(bool(r.get("cap_only_blocked")) and not bool(r.get("control_admitted")) for r in rows),
        "all_admitted": bool(rows) and all(bool(r.get("control_admitted")) for r in rows),
    }


def harvest_blocks(*, today: str = TODAY) -> dict[str, Any]:
    bodies: dict[str, list[dict[str, Any]]] = {k: [] for k in BLOCK_KEYS}
    attrition_all: list[dict[str, Any]] = []
    for day in all_research_days():
        bad = assert_research_day(day, today=today)
        if bad:
            return {"ok": False, "blocker": bad, "date": day, "bodies": bodies}
        if str(day) in FORBIDDEN_INPUT_DAYS or str(day) > str(MAX_RESEARCH_DATE):
            return {"ok": False, "blocker": "FAIL_CLOSED_FUTURE_DATA", "date": day, "bodies": bodies}
        raw = _load_v1_day(day)
        if not raw.get("ok"):
            return {"ok": False, "blocker": raw.get("blocker"), "date": day, "bodies": bodies}
        body = annotate_pool_day(day, raw)
        blk = str(body.get("block") or block_of(day) or "")
        bodies[blk].append(body)
        attrition_all.extend(list(body.get("attrition_rows") or []))
        print(
            f"{blk} {day} pool={body.get('pool_n')} attrition={body.get('attrition_n')} "
            f"unexplained={body.get('attrition_unexplained_n')} restream=false",
            flush=True,
        )
    pool_n = {k: sum(int(b.get("pool_n") or 0) for b in bodies[k]) for k in BLOCK_KEYS}
    outcome_n = {}
    for k in BLOCK_KEYS:
        n = 0
        for b in bodies[k]:
            n += sum(1 for r in list(b.get("pool") or []) if _f(r.get("session_close_pnl")) is not None)
        outcome_n[k] = n
    attrition_n = {k: pool_n[k] - outcome_n[k] for k in BLOCK_KEYS}
    pool_ok = pool_n == dict(EXPECTED_POOL_N)
    outcome_ok = outcome_n == dict(EXPECTED_OUTCOME_EVALUABLE_N)
    attrition_ok = attrition_n == dict(EXPECTED_ATTRITION_N)
    unexplained_n = sum(int(b.get("attrition_unexplained_n") or 0) for k in BLOCK_KEYS for b in bodies[k])
    by_block = {}
    for k in BLOCK_KEYS:
        rows = [r for r in attrition_all if r.get("block") == k]
        by_block[k] = _bias(rows)
    return {
        "ok": True,
        "bodies": bodies,
        "capture_restreamed": False,
        "loaded_from": "V1_DAY_CACHE_READONLY",
        "pool_identity": {
            "pool_n": pool_n,
            "outcome_evaluable_n": outcome_n,
            "attrition_n": attrition_n,
            "expected_pool_n": dict(EXPECTED_POOL_N),
            "expected_outcome_evaluable_n": dict(EXPECTED_OUTCOME_EVALUABLE_N),
            "expected_attrition_n": dict(EXPECTED_ATTRITION_N),
            "pool_identity_ok": bool(pool_ok),
            "outcome_n_ok": bool(outcome_ok),
            "attrition_n_ok": bool(attrition_ok),
        },
        "attrition": {
            "rows": attrition_all,
            "n": len(attrition_all),
            "unexplained_n": int(unexplained_n),
            "fail_closed_unexplained": bool(unexplained_n > 0),
            "by_block": by_block,
            "overall": _bias(attrition_all),
        },
    }
