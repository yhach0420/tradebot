"""Build legacy CANDIDATE_SYMBOL_POOL panel and AM day-fixed panel."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from research.e1_x31_population_direction.identity import reproduce_population
from research.e1_x32_upstream_attribution.eval_stages import load_boards_for_symbols
from research.e1_x32_upstream_attribution.membership import load_captured_symbols
from research.e1_x33b_neutral_anchor.neutral import (
    candidate_symbols_by_day,
    planned_neutral_anchors,
)
from research.e1_x34c_passive_deployability.events import build_events
from research.e1_x36_joint_allocator import FORBIDDEN_FROM, OUTER_BLOCKS
from research.e1_x36_joint_allocator.panel import enrich_events

from . import UNIVERSE_CONTRACT

NATIVE = Path(__file__).resolve().parents[3]


def _norm(s: str) -> str:
    s = str(s).strip()
    return s[:-2] if s.endswith(".T") else s


def load_am_universe(day: str) -> set[str]:
    assert day < FORBIDDEN_FROM
    fp = (
        NATIVE / "results" / "daily" / day / "runtime"
        / f"universe_core10_dynamic40_price_risk_am_{day}.csv"
    )
    if not fp.exists():
        raise FileNotFoundError(f"FAIL_CLOSED missing AM universe: {fp}")
    out: set[str] = set()
    with fp.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            sym = _norm(row.get("symbol") or row.get("symbol_key") or "")
            if not sym:
                continue
            passed = row.get("passed")
            if passed is not None and str(passed).lower() in ("false", "0", "no"):
                continue
            out.add(sym)
    if not out:
        raise RuntimeError(f"FAIL_CLOSED empty AM universe: {day}")
    return out


def am_pool_by_day() -> dict[str, set[str]]:
    days = sorted({d for ds in OUTER_BLOCKS.values() for d in ds})
    return {d: load_am_universe(d) for d in days}


def build_legacy_panel() -> dict[str, Any]:
    """Exact X36 path: population → day pool → planned → events → enrich."""
    rows_pop, _, _ = reproduce_population()
    pool = candidate_symbols_by_day(rows_pop)
    planned = planned_neutral_anchors(pool)
    boards = load_boards_for_symbols(sorted({(a["date"], a["symbol"]) for a in planned}))
    raw = build_events(planned, boards)
    panel = enrich_events(raw, boards)
    return {
        "kind": "CANDIDATE_SYMBOL_POOL",
        "pool": {d: sorted(v) for d, v in pool.items()},
        "pool_counts": {d: len(v) for d, v in pool.items()},
        "planned_n": len(planned),
        "signals": len(panel),
        "fills": sum(1 for e in panel if e.get("filled")),
        "panel": panel,
        "boards_keys": list(boards.keys()),
    }


def build_am_panel() -> dict[str, Any]:
    """DAY_FIXED_AM_RUNTIME_UNIVERSE_V1 — same AM membership all 16 anchors."""
    pool = am_pool_by_day()
    # fail-closed already in load
    planned = planned_neutral_anchors(pool)
    # load boards for planned; capture-miss → no board → skipped by build_events
    pairs = sorted({(a["date"], a["symbol"]) for a in planned})
    boards = load_boards_for_symbols(pairs)
    capture_miss: list[dict[str, Any]] = []
    for day, sym in pairs:
        b = boards.get((day, sym))
        if b is None or b["t"].size == 0:
            capture_miss.append({
                "date": day,
                "symbol": sym,
                "universe_member": True,
                "data_available": False,
                "classification": "CAPTURE_OR_BOARD_UNAVAILABLE",
            })
    raw = build_events(planned, boards)
    panel = enrich_events(raw, boards)
    return {
        "kind": UNIVERSE_CONTRACT,
        "pool": {d: sorted(v) for d, v in pool.items()},
        "pool_counts": {d: len(v) for d, v in pool.items()},
        "planned_n": len(planned),
        "signals": len(panel),
        "fills": sum(1 for e in panel if e.get("filled")),
        "panel": panel,
        "capture_miss": capture_miss,
        "capture_miss_n": len(capture_miss),
        "no_refresh_switching": True,
        "same_day_only": True,
        "fail_closed": True,
    }


def universe_delta(legacy_pool: dict[str, list[str]], am_pool: dict[str, list[str]]) -> dict[str, Any]:
    days = sorted(set(legacy_pool) | set(am_pool))
    rows = []
    added_symbol_days = []
    for d in days:
        old = set(legacy_pool.get(d, []))
        am = set(am_pool.get(d, []))
        added = sorted(am - old)
        removed = sorted(old - am)  # should be empty if cand ⊆ am
        rows.append({
            "date": d,
            "old_candidate_count": len(old),
            "am_universe_count": len(am),
            "added_n": len(added),
            "removed_n": len(removed),
            "added": added,
            "removed": removed,
        })
        for s in added:
            added_symbol_days.append({"date": d, "symbol": s})
    return {
        "daily": rows,
        "added_symbol_days": added_symbol_days,
        "added_symbol_day_n": len(added_symbol_days),
        "old_total_symbol_days": sum(len(v) for v in legacy_pool.values()),
        "am_total_symbol_days": sum(len(v) for v in am_pool.values()),
    }
