"""Raw pre-admission ENTRY signal stream freeze and EXIT-invariance proof."""
from __future__ import annotations

from typing import Any

from research.c1_multi_timeframe_entry_exit_full_strategy_v2 import EXECUTION_ID, KEPT_EXIT_IDS
from research.c1_multi_timeframe_entry_exit_pair_precommit_v1.spec import pair_id
from research.c1_multi_timeframe_entry_exit_pair_precommit_v2.spec import dumps_sha256
from research.c1_multi_timeframe_full_strategy_v1.spec import parse_candidate_id
from research.c1_multi_timeframe_precommit_v1 import RAW_CANDIDATE_IDS


def stream_tuples(rows: list[dict[str, Any]], *, entry_id: str, htf_id: str) -> list[list[Any]]:
    body = []
    for r in rows:
        body.append(
            [
                str(r.get("symbol") or ""),
                float(r.get("signal_t0") if r.get("signal_t0") is not None else r.get("t0") or 0.0),
                str(entry_id),
                str(htf_id),
            ]
        )
    body.sort(key=lambda x: (x[0], float(x[1]), x[2], x[3]))
    return body


def stream_sha256(rows: list[dict[str, Any]], *, entry_id: str, htf_id: str) -> str:
    return dumps_sha256(stream_tuples(rows, entry_id=entry_id, htf_id=htf_id))


def prove_raw_entry_stream_invariance(rows_by: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    entry_rows: list[dict[str, Any]] = []
    all_pass = True
    unique_fail = []
    for entry_id in RAW_CANDIDATE_IDS:
        htf_id = str(parse_candidate_id(entry_id)["HTF_ID"])
        hashes = []
        ns = []
        variants = []
        for exit_id in KEPT_EXIT_IDS:
            pid = pair_id(entry_id, exit_id)
            rows = list(rows_by.get(pid) or [])
            sha = stream_sha256(rows, entry_id=entry_id, htf_id=htf_id)
            hashes.append(sha)
            ns.append(len(rows))
            variants.append(
                {
                    "ENTRY_ID": entry_id,
                    "EXIT_ID": exit_id,
                    "CANDIDATE_ID": pid,
                    "EXECUTION": EXECUTION_ID,
                    "RAW_ENTRY_SIGNAL_N": len(rows),
                    "RAW_ENTRY_SIGNAL_STREAM_SHA256": sha,
                }
            )
        unique = sorted(set(hashes))
        n_unique = len(unique)
        n_ok = ns and all(x == ns[0] for x in ns)
        passed = n_unique == 1 and len(hashes) == 4 and bool(n_ok)
        if not passed:
            all_pass = False
            unique_fail.append(entry_id)
        entry_rows.append(
            {
                "ENTRY_ID": entry_id,
                "HTF_ID": htf_id,
                "RAW_ENTRY_SIGNAL_N": int(ns[0]) if ns else 0,
                "RAW_ENTRY_SIGNAL_STREAM_SHA256": unique[0] if n_unique == 1 else ",".join(unique),
                "RAW_ENTRY_STREAM_EXIT_VARIANT_N": len(hashes),
                "RAW_ENTRY_STREAM_HASH_UNIQUE_N": n_unique,
                "PASS": bool(passed),
                "variants": variants,
            }
        )
    return {
        "ok": bool(all_pass) and len(entry_rows) == 10,
        "ENTRY_RAW_STREAM_INVARIANCE_PASS": bool(all_pass) and len(entry_rows) == 10,
        "ENTRY_N": len(entry_rows),
        "PASS_N": sum(1 for r in entry_rows if r.get("PASS")),
        "FAIL_ENTRY_IDS": unique_fail,
        "RAW_ENTRY_STREAM_EXIT_VARIANT_N": 4,
        "RAW_ENTRY_STREAM_HASH_UNIQUE_N_REQUIRED": 1,
        "entries": entry_rows,
        "variants": [v for r in entry_rows for v in r["variants"]],
    }
