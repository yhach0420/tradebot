"""Combined historical population for X28E (no 20260810+)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research.e1_x19_outcome_pre_path.population import attach_derived
from research.e1_x22_actual_exit_factory.registry import load_population_checked
from research.e1_x28d_additional_stress.population import load_or_build_stress_population

from . import ADDITIONAL_STRESS, CONSUMED_DAY, FORBIDDEN_FROM

NATIVE = Path(__file__).resolve().parents[3]
X23_04 = NATIVE / "results" / "research" / "e1_x23_diversified_bundle" / "_clusters_20260804.jsonl"


def _load_0804() -> list[dict[str, Any]]:
    if not X23_04.exists():
        raise FileNotFoundError(str(X23_04))
    raw = [json.loads(l) for l in X23_04.read_text(encoding="utf-8").splitlines() if l.strip()]
    for r in raw:
        r["date"] = CONSUMED_DAY
    return attach_derived(raw)


def load_combined_population() -> list[dict[str, Any]]:
    base = load_population_checked()  # disc+eval+0803
    rows04 = _load_0804()
    rows_add = load_or_build_stress_population()  # 0805-07 cached
    rows = list(base) + list(rows04) + list(rows_add)
    assert not any(str(r.get("date") or "") >= FORBIDDEN_FROM for r in rows)
    assert all(
        r["date"] < FORBIDDEN_FROM for r in rows
    )
    # role tags
    for r in rows:
        d = r["date"]
        if d in ADDITIONAL_STRESS:
            r["x28e_role"] = "CONSUMED_ADDITIONAL_HISTORICAL_STRESS"
        elif d == CONSUMED_DAY:
            r["x28e_role"] = "CONSUMED_PROSPECTIVE_DIAGNOSTIC"
        elif d == "20260803":
            r["x28e_role"] = "STRESS"
        elif d >= "20260728":
            r["x28e_role"] = "EVALUATION"
        else:
            r["x28e_role"] = "DISCOVERY"
    print(
        f"=== X28E population n={len(rows)} "
        f"base={len(base)} 0804={len(rows04)} add={len(rows_add)} ===",
        flush=True,
    )
    return rows
