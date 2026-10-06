"""Build EXIT behavior equivalence classes. Drop only source-proven + mismatch_n=0 pairs."""
from __future__ import annotations

from typing import Any

from research.c1_multi_timeframe_entry_exit_pair_precommit_v1.spec import pair_id
from research.c1_multi_timeframe_entry_exit_pair_precommit_v2 import EXIT_IDS, PAIRWISE_EXIT_PAIRS, RAW_CANDIDATE_IDS
from research.c1_multi_timeframe_precommit_v1.library import build_raw_library
from research.c1_multi_timeframe_precommit_v1.spec import execution_contract, portfolio_contract
from research.c1_multi_timeframe_entry_exit_pair_precommit_v2 import EXECUTION_ID, POSITION_CAP, SHARES


def _mismatch_n(pairwise: list[dict[str, Any]], a: str, b: str) -> int | None:
    for r in pairwise:
        if r["EXIT_A"] == a and r["EXIT_B"] == b:
            return int(r["FIRE_INDEX_MISMATCH_N"])
        if r["EXIT_A"] == b and r["EXIT_B"] == a:
            return int(r["FIRE_INDEX_MISMATCH_N"])
    return None


def source_pair_proven(a: str, b: str, *, z2z4: dict[str, Any], predicates: dict[str, Any]) -> tuple[bool, str]:
    pair = {a, b}
    if pair == {"Z2_STRUCTURE_LOSS", "Z4_TRAILING_STRUCTURE"}:
        return bool(z2z4.get("proven")), str(z2z4.get("WHY") or "")
    if "Z5_HYBRID_SIMPLE" in pair:
        other = (pair - {"Z5_HYBRID_SIMPLE"}).pop()
        if other in {"Z1_VWAP_LOSS", "Z2_STRUCTURE_LOSS"}:
            return False, (
                "Z5 is earliest(Z1,Z2) from source, which is not identity with "
                f"{other} unless a further invariant is proven. No such invariant."
            )
        return False, "Z5 hybrid is a different predicate from this EXIT."
    return False, "Distinct source predicates; DEV coincidence is not a global duplicate."


def classify(
    pairwise: list[dict[str, Any]],
    *,
    z2z4: dict[str, Any],
    predicates: dict[str, Any],
) -> dict[str, Any]:
    parent = {z: z for z in EXIT_IDS}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        if list(EXIT_IDS).index(ra) <= list(EXIT_IDS).index(rb):
            parent[rb] = ra
        else:
            parent[ra] = rb

    pair_rows = []
    unproven_dev_eq = []
    contradiction = []
    for a, b in PAIRWISE_EXIT_PAIRS:
        mm = _mismatch_n(pairwise, a, b)
        src_ok, why = source_pair_proven(a, b, z2z4=z2z4, predicates=predicates)
        b_ok = mm is not None and int(mm) == 0
        semantic = bool(src_ok and b_ok)
        status = "DISTINCT"
        if mm is None:
            status = "AUDIT_INCOMPLETE"
        elif semantic:
            status = "SEMANTIC_DUPLICATE"
            union(a, b)
        elif b_ok and not src_ok:
            status = "DEV_DOMAIN_EQUIVALENT_BUT_GLOBAL_SEMANTICS_UNPROVEN"
            unproven_dev_eq.append({"EXIT_A": a, "EXIT_B": b, "WHY": why})
        elif src_ok and not b_ok:
            status = "SOURCE_PROVEN_BUT_DEV_MISMATCH"
            contradiction.append({"EXIT_A": a, "EXIT_B": b, "MISMATCH_N": mm})
        pair_rows.append(
            {
                "EXIT_A": a,
                "EXIT_B": b,
                "FIRE_INDEX_MISMATCH_N": mm,
                "SOURCE_DOMAIN_EQUIVALENCE_PROVEN": src_ok,
                "SUFFIX_MISMATCH_N_EQ_0": b_ok,
                "SEMANTIC_DUPLICATE": semantic,
                "STATUS": status,
                "WHY": why,
            }
        )
    classes_map: dict[str, list[str]] = {}
    for z in EXIT_IDS:
        classes_map.setdefault(find(z), []).append(z)
    classes = []
    kept: list[str] = []
    dropped: list[str] = []
    for rep in EXIT_IDS:
        members = classes_map.get(rep)
        if not members:
            continue
        classes.append({"REPRESENTATIVE": rep, "MEMBERS": members, "N": len(members)})
        kept.append(rep)
        for m in members:
            if m != rep:
                dropped.append(m)
    return {
        "pair_rows": pair_rows,
        "classes": classes,
        "kept_exit_ids": kept,
        "dropped_exit_ids": dropped,
        "FINAL_EXIT_N": len(kept),
        "unproven_dev_equivalent": unproven_dev_eq,
        "contradiction": contradiction,
        "Z5_DISTINCT_FROM_Z1": not any(
            r["SEMANTIC_DUPLICATE"] and {r["EXIT_A"], r["EXIT_B"]} == {"Z1_VWAP_LOSS", "Z5_HYBRID_SIMPLE"}
            for r in pair_rows
        ),
        "Z5_DISTINCT_FROM_Z2": not any(
            r["SEMANTIC_DUPLICATE"] and {r["EXIT_A"], r["EXIT_B"]} == {"Z2_STRUCTURE_LOSS", "Z5_HYBRID_SIMPLE"}
            for r in pair_rows
        ),
        "Z2_Z4_MISMATCH_N": _mismatch_n(pairwise, "Z2_STRUCTURE_LOSS", "Z4_TRAILING_STRUCTURE"),
        "Z2_Z4_SOURCE_DOMAIN_EQUIVALENCE_PROVEN": bool(z2z4.get("proven")),
        "Z2_Z4_SEMANTIC_DUPLICATE": any(
            r["SEMANTIC_DUPLICATE"] and {r["EXIT_A"], r["EXIT_B"]} == {"Z2_STRUCTURE_LOSS", "Z4_TRAILING_STRUCTURE"}
            for r in pair_rows
        ),
    }


def build_final_pairs(kept_exits: list[str]) -> list[dict[str, Any]]:
    entries = build_raw_library()
    exec_id = str(execution_contract()["EXEC_ID"])
    if exec_id != EXECUTION_ID:
        raise RuntimeError("EXECUTION_ID_MISMATCH")
    port = portfolio_contract()
    out: list[dict[str, Any]] = []
    for entry in entries:
        if str(entry["CANDIDATE_ID"]) not in RAW_CANDIDATE_IDS:
            raise RuntimeError("ENTRY_DRIFT")
        for exit_id in kept_exits:
            cid = pair_id(str(entry["CANDIDATE_ID"]), exit_id)
            out.append(
                {
                    "CANDIDATE_ID": cid,
                    "ENTRY_ID": entry["CANDIDATE_ID"],
                    "FAMILY": entry["FAMILY"],
                    "STATE_ID": entry["STATE_ID"],
                    "HTF_ID": entry["HTF_ID"],
                    "EXECUTION": EXECUTION_ID,
                    "EXIT": exit_id,
                    "SHARES": int(SHARES),
                    "CAP": int(POSITION_CAP),
                    "same_symbol": True,
                    "occupancy": True,
                    "slot_release": True,
                    "reentry": True,
                    "CROSS_FAMILY": False,
                    "VWAP_GLOBAL_GATE": False,
                    "BACKFILL": False,
                }
            )
    if int(port["CAP"]) != int(POSITION_CAP):
        raise RuntimeError("CAP_DRIFT")
    if len(out) != 10 * len(kept_exits):
        raise RuntimeError("FINAL_PAIR_N")
    if any("__X1__" in r["CANDIDATE_ID"] for r in out):
        raise RuntimeError("BARE_X1")
    return out
