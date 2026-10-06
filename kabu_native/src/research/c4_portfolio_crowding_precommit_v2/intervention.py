"""Structural C4 intervention and attribution-eligibility. No PnL."""
from __future__ import annotations

from typing import Any

from research.c4_portfolio_crowding_precommit_v1.streams import stream_sha256
from research.c4_portfolio_crowding_precommit_v2 import MIN_INFORMATIVE_BLOCK_N
from research.systematic_state_transition_full_strategy_v1.spec import frozen_library
from research.systematic_state_transition_library_precommit_v1 import FOLD_BLOCKS


def _block_of(day: str) -> str | None:
    d = str(day)
    for bid, days in FOLD_BLOCKS.items():
        if d in days:
            return str(bid)
    return None


def classify_intervention(
    applied: dict[str, dict[str, Any]],
    raw_by: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    block_rows: list[dict[str, Any]] = []
    eligible: list[str] = []
    ineligible: list[str] = []
    no_int = 0
    active = 0
    for entry in frozen_library():
        eid = str(entry["CANDIDATE_ID"])
        body = dict(applied.get(eid) or {})
        raw = list(raw_by.get(eid) or [])
        passed = list(body.get("passed") or [])
        rejected = list(body.get("rejected") or [])
        reject_n = int(body.get("C4_REJECT_N") or 0)
        raw_sha = stream_sha256(raw, eid)
        passed_sha = stream_sha256(passed, eid)
        active_flag = reject_n > 0
        if active_flag != (raw_sha != passed_sha):
            raise RuntimeError(f"INTERVENTION_HASH_INCONSISTENT:{eid}")
        by_block = {b: 0 for b in FOLD_BLOCKS}
        for r in rejected:
            bid = _block_of(str(r.get("date") or ""))
            if bid is None:
                raise RuntimeError(f"REJECT_DAY_NOT_IN_BLOCKS:{eid}:{r.get('date')}")
            by_block[bid] = int(by_block[bid]) + 1
        informative = {b: int(n) > 0 for b, n in by_block.items()}
        informative_n = sum(1 for v in informative.values() if v)
        classification = "C4_INTERVENTION_ACTIVE" if active_flag else "C4_NO_INTERVENTION"
        attr = bool(active_flag and informative_n >= MIN_INFORMATIVE_BLOCK_N)
        if active_flag:
            active += 1
        else:
            no_int += 1
        if attr:
            eligible.append(eid)
        else:
            ineligible.append(eid)
        rows.append(
            {
                "ENTRY_ID": eid,
                "RAW_ENTRY_SIGNAL_N": len(raw),
                "C4_PASS_N": int(body.get("C4_PASS_N") or 0),
                "C4_REJECT_N": reject_n,
                "RAW_ENTRY_STREAM_SHA256": raw_sha,
                "C4_PASSED_STREAM_SHA256": passed_sha,
                "C4_INTERVENTION_ACTIVE": bool(active_flag),
                "CLASSIFICATION": classification,
                "C4_INFORMATIVE_BLOCK_N": int(informative_n),
                "C4_ATTRIBUTION_ELIGIBLE": bool(attr),
                "BACKFILL": False,
            }
        )
        block_rows.append(
            {
                "ENTRY_ID": eid,
                "C4_REJECT_N": reject_n,
                **{f"C4_REJECT_N_{b}": by_block[b] for b in FOLD_BLOCKS},
                **{f"C4_INFORMATIVE_{b}": informative[b] for b in FOLD_BLOCKS},
                "C4_INFORMATIVE_BLOCK_N": int(informative_n),
            }
        )
    if len(eligible) + len(ineligible) != 25:
        raise RuntimeError("ELIGIBILITY_N")
    if len(eligible) > 25:
        raise RuntimeError("BACKFILL")
    return {
        "rows": rows,
        "block_rows": block_rows,
        "eligible_ids": eligible,
        "ineligible_ids": ineligible,
        "ENTRY_N": 25,
        "NO_INTERVENTION_ENTRY_N": int(no_int),
        "ACTIVE_ENTRY_N": int(active),
        "C4_ATTRIBUTION_ELIGIBLE_ENTRY_N": len(eligible),
        "C4_ATTRIBUTION_INELIGIBLE_ENTRY_N": len(ineligible),
        "BACKFILL": False,
        "MIN_INFORMATIVE_BLOCK_N": int(MIN_INFORMATIVE_BLOCK_N),
    }
