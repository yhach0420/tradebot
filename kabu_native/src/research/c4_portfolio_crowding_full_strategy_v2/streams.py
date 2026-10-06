"""Raw/C4 stream invariance across 4 EXITs. Control vs Treatment. No backfill."""
from __future__ import annotations

from typing import Any

from research.c1_multi_timeframe_precommit_v1.spec import dumps_sha256
from research.c4_portfolio_crowding_full_strategy_v2 import FROZEN_ELIGIBLE_IDS, KEPT_EXIT_IDS
from research.c4_portfolio_crowding_precommit_v1.streams import stream_sha256
from research.c4_portfolio_crowding_precommit_v2 import CONTROL_POLICY_ID, TREATMENT_POLICY_ID
from research.c4_portfolio_crowding_precommit_v2.policy import apply_c4_v2
from research.c4_portfolio_crowding_precommit_v2.spec import arm_id


def _signal_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "date": r.get("date"),
            "symbol": r.get("symbol"),
            "t0": r.get("signal_t0") if r.get("signal_t0") is not None else r.get("t0"),
            "ENTRY_ID": r.get("ENTRY_ID") or r.get("entry_id"),
            "source_event_seq": r.get("source_event_seq"),
        }
        for r in rows
    ]


def prove_raw_entry_stream_invariance(rows_by: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    variants: list[dict[str, Any]] = []
    all_pass = True
    fail_ids: list[str] = []
    for entry_id in FROZEN_ELIGIBLE_IDS:
        ctrl_hashes: list[str] = []
        treat_hashes: list[str] = []
        ctrl_ns: list[int] = []
        treat_ns: list[int] = []
        for exit_id in KEPT_EXIT_IDS:
            cid_c = arm_id(entry_id, CONTROL_POLICY_ID, exit_id)
            cid_t = arm_id(entry_id, TREATMENT_POLICY_ID, exit_id)
            raw_c = _signal_rows(list(rows_by.get(cid_c) or []))
            raw_t = _signal_rows(list(rows_by.get(cid_t) or []))
            sha_c = stream_sha256(raw_c, entry_id)
            sha_t = stream_sha256(raw_t, entry_id)
            ctrl_hashes.append(sha_c)
            treat_hashes.append(sha_t)
            ctrl_ns.append(len(raw_c))
            treat_ns.append(len(raw_t))
            variants.append(
                {
                    "ENTRY_ID": entry_id,
                    "EXIT_ID": exit_id,
                    "ARM": "CONTROL",
                    "CANDIDATE_ID": cid_c,
                    "SIGNAL_N": len(raw_c),
                    "STREAM_SHA256": sha_c,
                }
            )
            variants.append(
                {
                    "ENTRY_ID": entry_id,
                    "EXIT_ID": exit_id,
                    "ARM": "TREATMENT",
                    "CANDIDATE_ID": cid_t,
                    "SIGNAL_N": len(raw_t),
                    "STREAM_SHA256": sha_t,
                }
            )
        z1_c = _signal_rows(list(rows_by.get(arm_id(entry_id, CONTROL_POLICY_ID, KEPT_EXIT_IDS[0])) or []))
        applied = apply_c4_v2(z1_c)
        expected_t = stream_sha256(list(applied.get("passed") or []), entry_id)
        ctrl_ok = len(set(ctrl_hashes)) == 1 and len(set(ctrl_ns)) == 1
        treat_ok = len(set(treat_hashes)) == 1 and len(set(treat_ns)) == 1
        c4_match = treat_hashes[0] == expected_t if treat_hashes else False
        ok = bool(ctrl_ok and treat_ok and c4_match)
        if not ok:
            all_pass = False
            fail_ids.append(entry_id)
        entries.append(
            {
                "ENTRY_ID": entry_id,
                "CONTROL_HASH_UNIQUE_N": len(set(ctrl_hashes)),
                "TREATMENT_HASH_UNIQUE_N": len(set(treat_hashes)),
                "CONTROL_SIGNAL_N": ctrl_ns[0] if ctrl_ns else 0,
                "TREATMENT_SIGNAL_N": treat_ns[0] if treat_ns else 0,
                "CONTROL_STREAM_SHA256": ctrl_hashes[0] if ctrl_hashes else "",
                "TREATMENT_STREAM_SHA256": treat_hashes[0] if treat_hashes else "",
                "C4_V2_APPLIED_TO_CONTROL_SHA256": expected_t,
                "TREATMENT_EQ_C4_V2_CONTROL": bool(c4_match),
                "EXIT_INVARIANCE_PASS": bool(ok),
                "SAME_T0_BACKFILL": False,
            }
        )
    return {
        "ENTRY_RAW_STREAM_INVARIANCE_PASS": bool(all_pass),
        "PASS_N": sum(1 for r in entries if r.get("EXIT_INVARIANCE_PASS")),
        "FAIL_ENTRY_IDS": fail_ids,
        "entries": entries,
        "variants": variants,
        "SAME_T0_BACKFILL": False,
        "FUTURE_SAME_T0_COUNT_REQUIRED": False,
        "SPEC_SHA_UNUSED": dumps_sha256({"ok": all_pass}),
    }
