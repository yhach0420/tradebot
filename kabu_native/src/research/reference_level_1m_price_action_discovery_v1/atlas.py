"""REFERENCE_LEVEL_RESPONSE_ATLAS_V1 aggregates. Counts only; no strategy promotion."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.reference_level_1m_price_action_discovery_v1.levels import FAMILY_IDS, LEVEL_DEFS

BUCKETS = ("approach", "touch", "break", "accept", "reject", "retest", "reclaim", "break_failure", "gap_open", "gap_hold", "gap_partial", "gap_full", "other")


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _acc() -> dict[str, Any]:
    return {
        "event_n": 0,
        "days": set(),
        "symbols": set(),
        "continuation_n": 0,
        "reversal_n": 0,
        "stall_n": 0,
        "favorable_first_n": 0,
        "adverse_first_n": 0,
        "mfe": [],
        "mae": [],
        "by_block": defaultdict(int),
        "by_kind": defaultdict(int),
        **{f"{b}_n": 0 for b in BUCKETS},
    }


def _finish(acc: dict[str, Any], *, family: str | None = None, level_id: str | None = None, kind: str | None = None) -> dict[str, Any]:
    n = int(acc["event_n"])
    mfe = [float(x) for x in acc["mfe"] if _finite(x)]
    mae = [float(x) for x in acc["mae"] if _finite(x)]
    path_n = int(acc["continuation_n"] + acc["reversal_n"] + acc["stall_n"])
    return {
        "family": family,
        "level_id": level_id,
        "event_kind": kind,
        "event_n": n,
        "day_n": len(acc["days"]),
        "symbol_n": len(acc["symbols"]),
        "approach_n": acc["approach_n"],
        "touch_n": acc["touch_n"],
        "break_n": acc["break_n"],
        "accept_n": acc["accept_n"],
        "reject_n": acc["reject_n"],
        "retest_n": acc["retest_n"],
        "reclaim_n": acc["reclaim_n"],
        "break_failure_n": acc["break_failure_n"],
        "continuation_n": acc["continuation_n"],
        "reversal_n": acc["reversal_n"],
        "stall_n": acc["stall_n"],
        "continuation_rate": (acc["continuation_n"] / path_n) if path_n else None,
        "reversal_rate": (acc["reversal_n"] / path_n) if path_n else None,
        "stall_rate": (acc["stall_n"] / path_n) if path_n else None,
        "favorable_first_rate": (acc["favorable_first_n"] / n) if n else None,
        "adverse_first_rate": (acc["adverse_first_n"] / n) if n else None,
        "median_mfe": float(np.median(mfe)) if mfe else None,
        "median_mae": float(np.median(mae)) if mae else None,
        "by_block": dict(acc["by_block"]),
        "by_kind": dict(acc["by_kind"]),
    }


def _bump(acc: dict[str, Any], h: dict[str, Any]) -> None:
    acc["event_n"] += 1
    acc["days"].add(str(h.get("date")))
    acc["symbols"].add(str(h.get("symbol")))
    acc["by_block"][str(h.get("block") or "")] += 1
    kind = str(h.get("event_kind") or "")
    acc["by_kind"][kind] += 1
    b = str(h.get("bucket") or "other")
    key = f"{b}_n"
    if key in acc:
        acc[key] += 1
    else:
        acc["other_n"] += 1
    if h.get("continuation"):
        acc["continuation_n"] += 1
    if h.get("reversal"):
        acc["reversal_n"] += 1
    if h.get("stall"):
        acc["stall_n"] += 1
    if h.get("favorable_first"):
        acc["favorable_first_n"] += 1
    if h.get("adverse_first"):
        acc["adverse_first_n"] += 1
    if _finite(h.get("mfe_bps")):
        acc["mfe"].append(float(h["mfe_bps"]))
    if _finite(h.get("mae_bps")):
        acc["mae"].append(float(h["mae_bps"]))


def build_atlas(hits: list[dict[str, Any]]) -> dict[str, Any]:
    fam = {f: _acc() for f in FAMILY_IDS}
    lvl: dict[str, dict[str, Any]] = defaultdict(_acc)
    kind: dict[tuple[str, str], dict[str, Any]] = defaultdict(_acc)
    for h in hits:
        f = str(h.get("family") or "")
        lid = str(h.get("level_id") or "")
        k = str(h.get("event_kind") or "")
        if f in fam:
            _bump(fam[f], h)
        _bump(lvl[lid], h)
        _bump(kind[(lid, k)], h)
    family_rows = [_finish(fam[f], family=f) for f in FAMILY_IDS]
    level_rows = [_finish(lvl[d["level_id"]], family=d["family"], level_id=d["level_id"]) for d in LEVEL_DEFS if d["level_id"] in lvl]
    for lid, acc in lvl.items():
        if lid not in {d["level_id"] for d in LEVEL_DEFS}:
            level_rows.append(_finish(acc, level_id=lid))
    kind_rows = [_finish(acc, level_id=lid, kind=k) for (lid, k), acc in sorted(kind.items())]
    strongest = sorted(
        [r for r in kind_rows if (r.get("event_n") or 0) >= 80 and r.get("continuation_rate") is not None],
        key=lambda r: (-abs(float(r["continuation_rate"]) - float(r.get("reversal_rate") or 0.0)), -int(r["event_n"])),
    )[:12]
    return {
        "atlas_id": "REFERENCE_LEVEL_RESPONSE_ATLAS_V1",
        "event_n": len(hits),
        "day_n": len({h["date"] for h in hits}),
        "symbol_n": len({h["symbol"] for h in hits}),
        "families": family_rows,
        "levels": level_rows,
        "level_kinds": kind_rows,
        "strongest_path_separation": strongest,
    }
