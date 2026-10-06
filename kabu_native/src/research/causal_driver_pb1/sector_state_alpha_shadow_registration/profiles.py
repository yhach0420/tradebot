"""Candidate shadow profiles. Reject a profile over 50. Never truncate or rotate."""
from __future__ import annotations

from typing import Any, Sequence

from research.causal_driver_pb1.identity.ids import sha256_obj
from research.causal_driver_pb1.sector_state_alpha_shadow_registration import OWNER, REGISTER_LIMIT


def _uniq(symbols: Sequence[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in symbols:
        sym = str(raw)
        if sym in seen:
            continue
        seen.add(sym)
        out.append(sym)
    return out


def measure(*, m3: Sequence[str], core: Sequence[str], futures: Sequence[str]) -> dict[str, Any]:
    m3s = set(m3)
    cores = set(core)
    futs = set(futures)
    union = m3s | cores | futs
    union_n = len(union)
    return {
        "M3_n": len(m3s),
        "core_n": len(cores),
        "futures_n": len(futs),
        "M3_core_overlap_n": len(m3s & cores),
        "M3_futures_overlap_n": len(m3s & futs),
        "core_futures_overlap_n": len(cores & futs),
        "union_n": union_n,
        "remaining_slots": int(REGISTER_LIMIT) - union_n,
        "symbols": sorted(union),
    }


def freeze_profile(profile_id: str, measured: dict[str, Any], *, m3: Sequence[str]) -> dict[str, Any]:
    symbols = list(measured["symbols"])
    over = int(measured["union_n"]) > int(REGISTER_LIMIT)
    m3_missing = sorted(set(m3) - set(symbols))
    rotated = False
    valid = (not over) and (not m3_missing) and len(set(m3)) == 30
    return {
        "profile_id": profile_id,
        "valid": valid,
        "rejected": over or bool(m3_missing),
        "truncated": False,
        "rotation": rotated,
        "union_n": int(measured["union_n"]),
        "remaining_slots": int(measured["remaining_slots"]),
        "M3_n": int(measured["M3_n"]),
        "core_n": int(measured["core_n"]),
        "futures_n": int(measured["futures_n"]),
        "M3_core_overlap_n": int(measured["M3_core_overlap_n"]),
        "M3_futures_overlap_n": int(measured["M3_futures_overlap_n"]),
        "core_futures_overlap_n": int(measured["core_futures_overlap_n"]),
        "symbols": symbols if valid else symbols,
        "m3_missing_n": len(m3_missing),
        "limit": int(REGISTER_LIMIT),
    }


def build_candidates(*, m3: Sequence[str], core: Sequence[str], futures: Sequence[str]) -> dict[str, dict[str, Any]]:
    m3_list = _uniq(m3)
    if len(m3_list) != 30:
        raise RuntimeError("m3_identity")
    return {
        "PROFILE_A": freeze_profile("PROFILE_A", measure(m3=m3_list, core=[], futures=[]), m3=m3_list),
        "PROFILE_B": freeze_profile("PROFILE_B", measure(m3=m3_list, core=[], futures=_uniq(futures)), m3=m3_list),
        "PROFILE_C": freeze_profile("PROFILE_C", measure(m3=m3_list, core=_uniq(core), futures=_uniq(futures)), m3=m3_list),
    }


def select_profile(candidates: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """C, then B, then A. An invalid profile is never truncated into a smaller one."""
    for key in ("PROFILE_C", "PROFILE_B", "PROFILE_A"):
        row = candidates[key]
        if row.get("valid") and int(row["union_n"]) <= int(REGISTER_LIMIT) and not row.get("truncated"):
            return row
    raise RuntimeError("no_valid_shadow_profile")


def plan_sha(profile: dict[str, Any]) -> str:
    return sha256_obj({
        "namespace": "ALPHA_SHADOW_PROSPECTIVE_AM_PLAN_V1",
        "owner": OWNER,
        "profile_id": profile["profile_id"],
        "symbols": sorted(profile["symbols"]),
    })


def reject_rotation(plan: dict[str, Any]) -> None:
    if plan.get("rotation") or plan.get("batches") or int(plan.get("m3_missing_n") or 0) > 0:
        raise RuntimeError("rotation_or_partial_sector_invalid")
    if len(set(plan.get("symbols") or [])) < 30:
        raise RuntimeError("rotation_or_partial_sector_invalid")
