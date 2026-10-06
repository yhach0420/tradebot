"""Deterministic USDJPY-day shuffle. No identity mapping. Seed 20251127. N=1000."""
from __future__ import annotations

import random
from collections import defaultdict
from datetime import datetime
from typing import Any

from research.causal_driver_pb1.identity.ids import sha256_obj
from research.causal_driver_pb1.phase2_precommit import DAY_SHUFFLE_N, DAY_SHUFFLE_SEED
from research.causal_driver_pb1.phase2_precommit_v1_1 import DAY_SHUFFLE_METHOD


def _weekday(yyyymmdd: str) -> int:
    return datetime.strptime(yyyymmdd, "%Y%m%d").weekday()


def sattolo_derange(items: list[str], rng: random.Random) -> list[str]:
    """Sattolo cycle: permutation of n>=2 with no fixed points."""
    arr = list(items)
    n = len(arr)
    if n < 2:
        raise ValueError("sattolo_requires_n_ge_2")
    for i in range(n - 1, 0, -1):
        j = rng.randrange(0, i)
        arr[i], arr[j] = arr[j], arr[i]
    if any(arr[k] == items[k] for k in range(n)):
        raise RuntimeError("sattolo_fixed_point")
    return arr


def build_shuffle_plan(eligible_dates: list[str]) -> dict[str, Any]:
    dates = sorted(eligible_dates)
    primary: dict[tuple[str, int], list[str]] = defaultdict(list)
    for d in dates:
        primary[(d[:6], _weekday(d))].append(d)
    primary_groups: list[list[str]] = []
    leftovers: list[str] = []
    for key in sorted(primary):
        grp = sorted(primary[key])
        if len(grp) >= 2:
            primary_groups.append(grp)
        else:
            leftovers.extend(grp)
    month_fb: dict[str, list[str]] = defaultdict(list)
    for d in leftovers:
        month_fb[d[:6]].append(d)
    fallback_groups: list[list[str]] = []
    unshufflable: list[str] = []
    for month in sorted(month_fb):
        grp = sorted(month_fb[month])
        if len(grp) >= 2:
            fallback_groups.append(grp)
        else:
            unshufflable.extend(grp)
    primary_n = sum(len(g) for g in primary_groups)
    fallback_n = sum(len(g) for g in fallback_groups)
    return {
        "method": DAY_SHUFFLE_METHOD,
        "primary_groups": primary_groups,
        "fallback_month_groups": fallback_groups,
        "unshufflable": sorted(unshufflable),
        "shuffle_primary_n": primary_n,
        "shuffle_fallback_month_n": fallback_n,
        "shuffle_unshufflable_n": len(unshufflable),
        "eligible_n": len(dates),
        "note": "unshufflable dates remain in primary causal analysis; excluded from day-shuffle placebo only",
    }


def _one_mapping(plan: dict[str, Any], rng: random.Random) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for grp in list(plan["primary_groups"]) + list(plan["fallback_month_groups"]):
        der = sattolo_derange(grp, rng)
        for a, b in zip(grp, der):
            mapping[a] = b
    if any(k == v for k, v in mapping.items()):
        raise RuntimeError("shuffle_identity_mapping")
    return mapping


def generate_shuffle_permutations(
    eligible_dates: list[str],
    *,
    n: int = DAY_SHUFFLE_N,
    seed: int = DAY_SHUFFLE_SEED,
    return_maps: bool = False,
) -> dict[str, Any]:
    plan = build_shuffle_plan(eligible_dates)
    rng = random.Random(seed)
    perms: list[dict[str, str]] = []
    for _ in range(int(n)):
        perms.append(_one_mapping(plan, rng))
    payload = [{"i": i, "map": perms[i]} for i in range(len(perms))]
    out = {
        **{k: plan[k] for k in (
            "method",
            "shuffle_primary_n",
            "shuffle_fallback_month_n",
            "shuffle_unshufflable_n",
            "unshufflable",
            "eligible_n",
            "note",
        )},
        "n": int(n),
        "seed": int(seed),
        "identity_mapping_forbidden": True,
        "permutation_sha256": sha256_obj(payload),
        "primary_group_n": len(plan["primary_groups"]),
        "fallback_group_n": len(plan["fallback_month_groups"]),
    }
    if return_maps:
        out["maps"] = perms
    return out


def shuffle_reproducible(eligible_dates: list[str]) -> bool:
    a = generate_shuffle_permutations(eligible_dates)
    b = generate_shuffle_permutations(eligible_dates)
    return a["permutation_sha256"] == b["permutation_sha256"]
