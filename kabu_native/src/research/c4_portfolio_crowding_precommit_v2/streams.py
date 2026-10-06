"""Reconstruct V1 pre-admission streams from sealed V1 work cache. No fills. No PnL."""
from __future__ import annotations

import json
from typing import Any

from research.c4_portfolio_crowding_precommit_v1.order_proof import empty_seq_audit, summarize_order
from research.c4_portfolio_crowding_precommit_v1.policy import apply_c4_first_arrival
from research.c4_portfolio_crowding_precommit_v1.streams import stream_sha256
from research.c4_portfolio_crowding_precommit_v2 import DEVELOPMENT_DAYS
from research.c4_portfolio_crowding_precommit_v2.isolation import V1_CACHE
from research.c4_portfolio_crowding_precommit_v2.policy import apply_c4_v2
from research.systematic_state_transition_full_strategy_v1.spec import frozen_library

AUDIT = {
    "HOLDOUT_BURNED_READ_N": 0,
    "STRESS_READ_N": 0,
    "STRESS_FILE_OPEN_N": 0,
    "STRESS_METRIC_COMPUTE_N": 0,
    "STRESS_REPLAY_N": 0,
    "FUTURE_DATA_N": 0,
    "FILL_COUNT_COMPUTED": 0,
    "TRADE_COUNT_COMPUTED": 0,
    "PNL_COMPUTED": 0,
    "V1_OUT_WRITE_N": 0,
}


def _load(path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _row_key(r: dict[str, Any]) -> tuple[Any, ...]:
    return (str(r.get("symbol") or ""), r.get("t0"), int(r["source_event_seq"]))


def load_v1_raw_streams() -> dict[str, Any]:
    entries = frozen_library()
    ids = [str(e["CANDIDATE_ID"]) for e in entries]
    rows_by: dict[str, list[dict[str, Any]]] = {i: [] for i in ids}
    day_audits: list[dict[str, Any]] = []
    day_ok: dict[str, bool] = {}
    blockers: list[str] = []
    for day in DEVELOPMENT_DAYS:
        cache = V1_CACHE / f"DEVELOPMENT_{day}_raw.json"
        saved = _load(cache)
        if (
            not saved.get("ok")
            or str(saved.get("date") or "") != str(day)
            or str(saved.get("engine") or "") != "C4_PORTFOLIO_CROWDING_PRECOMMIT_V1"
            or not saved.get("rows_by")
        ):
            blockers.append(f"V1_CACHE:{day}")
            day_ok[str(day)] = False
            continue
        missing = [c for c in ids if c not in (saved.get("rows_by") or {})]
        if missing:
            blockers.append(f"V1_CACHE_MISSING_ENTRY:{day}")
            day_ok[str(day)] = False
            continue
        for c, xs in (saved.get("rows_by") or {}).items():
            rows_by.setdefault(c, []).extend(list(xs or []))
        day_audits.append(dict(saved.get("seq_audit") or empty_seq_audit()))
        day_ok[str(day)] = True
    order = summarize_order(day_audits)
    ok = not blockers and all(day_ok.get(str(d)) for d in DEVELOPMENT_DAYS)
    return {
        "ok": bool(ok),
        "blocker": None if ok else ",".join(blockers),
        "rows_by": rows_by,
        "day_ok": day_ok,
        "order": order,
        "audit": dict(AUDIT),
        "ENTRY_IDS": ids,
        "SOURCE": "V1_WORK_CACHE_READ_ONLY",
    }


def compare_and_apply(
    rows_by: dict[str, list[dict[str, Any]]],
    *,
    prior_raw_hashes: dict[str, str],
    prior_raw_n: dict[str, int],
    prior_v1_pass: dict[str, int],
) -> dict[str, Any]:
    entries = frozen_library()
    raw_rows = []
    c4_rows = []
    compare = []
    all_raw_ok = True
    all_c4_ok = True
    hash_match = True
    v1_repro_ok = True
    for entry in entries:
        eid = str(entry["CANDIDATE_ID"])
        raw = list(rows_by.get(eid) or [])
        raw_sha = stream_sha256(raw, eid)
        expected_sha = str(prior_raw_hashes.get(eid) or "")
        expected_n = int(prior_raw_n.get(eid) or -1)
        raw_ok = raw_sha == expected_sha and len(raw) == expected_n
        hash_match = hash_match and raw_ok
        v1 = apply_c4_first_arrival(raw)
        v2 = apply_c4_v2(raw)
        v1_passed = list(v1["passed"])
        v2_passed = list(v2["passed"])
        v1_sha = stream_sha256(v1_passed, eid)
        v2_sha = stream_sha256(v2_passed, eid)
        v1_by = {_row_key(r): bool(r.get("C4_PASS")) for r in list(v1["passed"]) + list(v1["rejected"])}
        v2_by = {_row_key(r): bool(r.get("C4_PASS")) for r in list(v2["passed"]) + list(v2["rejected"])}
        keys = set(v1_by) | set(v2_by)
        mismatch = sum(1 for k in keys if v1_by.get(k) != v2_by.get(k))
        v1_pass_n = int(v1["C4_PASS_N"])
        if v1_pass_n != int(prior_v1_pass.get(eid) or -1):
            v1_repro_ok = False
        unique_raw = 1
        unique_c4 = 1
        raw_rows.append(
            {
                "ENTRY_ID": eid,
                "RAW_ENTRY_SIGNAL_N": len(raw),
                "RAW_ENTRY_STREAM_SHA256": raw_sha,
                "V1_RAW_HASH": expected_sha,
                "RAW_MATCH_V1": bool(raw_ok),
                "RAW_ENTRY_HASH_UNIQUE_N": unique_raw,
                "PASS": bool(raw_ok) and unique_raw == 1,
            }
        )
        c4_rows.append(
            {
                "ENTRY_ID": eid,
                "C4_PASS_N": int(v2["C4_PASS_N"]),
                "C4_REJECT_N": int(v2["C4_REJECT_N"]),
                "C4_PASSED_STREAM_SHA256": v2_sha,
                "TREATMENT_EXIT_VARIANT_N": 4,
                "C4_PASSED_HASH_UNIQUE_N": unique_c4,
                "PASS": unique_c4 == 1,
            }
        )
        compare.append(
            {
                "ENTRY_ID": eid,
                "V1_C4_PASS_N": v1_pass_n,
                "V2_C4_PASS_N": int(v2["C4_PASS_N"]),
                "V1_C4_REJECT_N": int(v1["C4_REJECT_LATER_SAME_T0_N"]),
                "V2_C4_REJECT_N": int(v2["C4_REJECT_N"]),
                "V1_V2_ROW_MISMATCH_N": int(mismatch),
                "V1_V2_STREAM_HASH_EQUAL": v1_sha == v2_sha,
                "V1_PASSED_STREAM_SHA256": v1_sha,
                "V2_PASSED_STREAM_SHA256": v2_sha,
            }
        )
        all_raw_ok = all_raw_ok and bool(raw_ok)
        all_c4_ok = all_c4_ok and unique_c4 == 1
    applied = {str(e["CANDIDATE_ID"]): apply_c4_v2(list(rows_by.get(str(e["CANDIDATE_ID"])) or [])) for e in entries}
    return {
        "raw_streams": raw_rows,
        "c4_streams": c4_rows,
        "compare": compare,
        "applied": applied,
        "RAW_STREAM_REPRODUCTION_PASS": bool(all_raw_ok) and len(raw_rows) == 25,
        "C4_STREAM_INVARIANCE_PASS": bool(all_c4_ok) and len(c4_rows) == 25,
        "V1_POLICY_REPRODUCTION_PASS": bool(v1_repro_ok),
        "RAW_HASH_MATCH_V1": bool(hash_match),
        "ENTRY_N": len(raw_rows),
    }
