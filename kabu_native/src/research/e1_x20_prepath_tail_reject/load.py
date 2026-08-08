"""Load X19 population, classify, strata, Discovery thresholds."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Optional

import numpy as np

from research.e1_x19_outcome_pre_path.analyze import (
    assign_strata,
    classify_population,
    design_terciles,
)
from research.e1_x19_outcome_pre_path.population import attach_derived

from . import DISCOVERY, EXPECTED_POP_N, SOURCE_RUN

NATIVE = Path(__file__).resolve().parents[3]
X19_POP = NATIVE / "results" / "research" / "e1_x19_outcome_pre_path" / "_population.jsonl"
X19_REPORT = NATIVE / "results" / "research" / "e1_x19_outcome_pre_path" / "report.json"
OUT = NATIVE / "results" / "research" / "e1_x20_prepath_tail_reject"


def load_prepared() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    x19 = json.loads(X19_REPORT.read_text(encoding="utf-8"))
    assert x19["run_id"] == SOURCE_RUN, x19.get("run_id")
    assert x19["population_n"] == EXPECTED_POP_N

    raw = [json.loads(l) for l in X19_POP.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(raw) == EXPECTED_POP_N

    rows = attach_derived(raw)
    rows = classify_population(rows)
    disc = [r for r in rows if r["date"] in DISCOVERY]
    cuts = design_terciles(disc)
    rows = assign_strata(rows, cuts)
    return rows, {"source_run": SOURCE_RUN, "population_n": len(rows), "tercile_cuts": cuts}


def discovery_thresholds(rows: list[dict[str, Any]]) -> dict[str, Any]:
    disc = [r for r in rows if r["date"] in DISCOVERY]
    out = {}
    for feat in ("slope_60s", "rebound_from_recent_low_bps"):
        xs = [float(r[feat]) for r in disc if r.get(feat) is not None]
        qs = {
            "q20": float(np.quantile(xs, 0.20)),
            "q40": float(np.quantile(xs, 0.40)),
            "q60": float(np.quantile(xs, 0.60)),
            "q80": float(np.quantile(xs, 0.80)),
        }
        body = {"feature": feat, "discovery_support": len(xs), **qs}
        body["threshold_sha256"] = hashlib.sha256(
            json.dumps(body, sort_keys=True, default=str).encode()
        ).hexdigest()
        out[feat] = body
    return {
        "SLOPE_UPPER_LIMIT": out["slope_60s"]["q80"],
        "REBOUND_UPPER_LIMIT_BPS": out["rebound_from_recent_low_bps"]["q80"],
        "by_feature": out,
        "discovery_only": True,
        "no_retune": True,
    }


def assign_variants(rows: list[dict[str, Any]], thr: dict[str, Any]) -> list[dict[str, Any]]:
    slope_lim = thr["SLOPE_UPPER_LIMIT"]
    reb_lim = thr["REBOUND_UPPER_LIMIT_BPS"]
    out = []
    for r in rows:
        m = dict(r)
        slope = m.get("slope_60s")
        reb = m.get("rebound_from_recent_low_bps")
        both = slope is not None and reb is not None
        m["in_B0"] = both
        m["in_B1"] = bool(both and float(slope) <= slope_lim)
        m["in_B2"] = bool(both and float(reb) <= reb_lim)
        m["in_B3"] = bool(m["in_B1"] and m["in_B2"])
        m["in_B1_Rejected"] = bool(both and float(slope) > slope_lim)
        m["in_B2_Rejected"] = bool(both and float(reb) > reb_lim)
        out.append(m)
    return out
