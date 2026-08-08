"""Build 20260805–07 cluster populations with X19 semantics (stress-only)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research.e1_x19_outcome_pre_path.population import _build_day, attach_derived

from . import STRESS_DAYS

NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "e1_x28d_additional_stress"


def load_or_build_stress_population(*, force: bool = False) -> list[dict[str, Any]]:
    OUT.mkdir(parents=True, exist_ok=True)
    all_rows: list[dict[str, Any]] = []
    for day in STRESS_DAYS:
        cache = OUT / f"_clusters_{day}.jsonl"
        if cache.exists() and not force:
            rows = [json.loads(l) for l in cache.read_text(encoding="utf-8").splitlines() if l.strip()]
            print(f"=== loaded stress clusters {day} n={len(rows)} ===", flush=True)
        else:
            raw = _build_day(day)
            for r in raw:
                r["date"] = day
            with cache.open("w", encoding="utf-8") as f:
                for r in raw:
                    f.write(json.dumps(r, default=str) + "\n")
            rows = raw
            print(f"=== built stress clusters {day} n={len(rows)} ===", flush=True)
        all_rows.extend(rows)
    # attach_derived needs cross-section within each day — OK across combined list
    derived = attach_derived(all_rows)
    # Guard: only stress days
    assert all(r["date"] in STRESS_DAYS for r in derived)
    assert not any(r["date"] >= "20260810" for r in derived)
    print(f"=== stress population total n={len(derived)} ===", flush=True)
    return derived
