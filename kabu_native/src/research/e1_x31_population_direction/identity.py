"""X30 population identity + LONG label reuse."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from research.e1_x30_absolute_rise_entry_v2.population import load_population

from . import (
    EXPECTED_POP_N,
    EXPECTED_RET300,
    EXPECTED_RET600,
    EXPECTED_VALID_N,
    FORBIDDEN_FROM,
    SOURCE_X30_RUN,
)

NATIVE = Path(__file__).resolve().parents[3]
X30 = NATIVE / "results" / "research" / "e1_x30_absolute_rise_entry_v2"


def load_x30_report() -> dict[str, Any]:
    return json.loads((X30 / "report.json").read_text(encoding="utf-8"))


def load_x30_labels() -> dict[str, np.ndarray]:
    z = np.load(X30 / "_labels_cache.npz")
    return {k: z[k] for k in z.files}


def episode_key_fingerprint(rows: list[dict[str, Any]]) -> str:
    parts = [
        f"{r['date']}|{r['symbol']}|{r['session']}|{r['grid_epoch']}"
        for r in rows
    ]
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def reproduce_population() -> tuple[list[dict[str, Any]], dict[str, np.ndarray], dict[str, Any]]:
    x30 = load_x30_report()
    assert x30.get("run_id") == SOURCE_X30_RUN, x30.get("run_id")
    rows = load_population()
    labels = load_x30_labels()
    assert len(rows) == EXPECTED_POP_N == int(x30["population_n"])
    assert len(labels["valid"]) == len(rows)
    valid_n = int(labels["valid"].sum())
    assert valid_n == EXPECTED_VALID_N
    r300 = float(np.nanmean(labels["return_300"][labels["return_300_valid"]]))
    r600 = float(np.nanmean(labels["return_600"][labels["return_600_valid"]]))
    assert abs(r300 - EXPECTED_RET300) < 1e-6, (r300, EXPECTED_RET300)
    assert abs(r600 - EXPECTED_RET600) < 1e-6, (r600, EXPECTED_RET600)
    assert not any(str(r.get("date") or "") >= FORBIDDEN_FROM for r in rows)
    fp = episode_key_fingerprint(rows)
    identity = {
        "source_x30_run_id": SOURCE_X30_RUN,
        "population_n": len(rows),
        "valid_n": valid_n,
        "mean_return_300": r300,
        "mean_return_600": r600,
        "episode_keys_sha256": fp,
        "identity_ok": True,
    }
    return rows, labels, identity


def ab_identity(
    rows: list[dict[str, Any]],
    labels: dict[str, np.ndarray],
    identity: dict[str, Any],
) -> dict[str, Any]:
    """Deterministic A/B: reload labels + recompute fingerprint."""
    labels_b = load_x30_labels()
    rows_b = load_population()
    keys_a = [(r["date"], r["symbol"], r["session"], float(r["grid_epoch"])) for r in rows]
    keys_b = [(r["date"], r["symbol"], r["session"], float(r["grid_epoch"])) for r in rows_b]
    fp_b = episode_key_fingerprint(rows_b)
    ok = (
        keys_a == keys_b
        and int(labels["valid"].sum()) == int(labels_b["valid"].sum()) == EXPECTED_VALID_N
        and bool(np.allclose(labels["return_300"], labels_b["return_300"], equal_nan=True))
        and bool(np.allclose(labels["return_600"], labels_b["return_600"], equal_nan=True))
        and fp_b == identity["episode_keys_sha256"]
    )
    return {
        "population_n_match": len(rows) == len(rows_b) == EXPECTED_POP_N,
        "episode_identity_match": keys_a == keys_b,
        "valid_n_match": int(labels["valid"].sum()) == EXPECTED_VALID_N,
        "returns_match": bool(np.allclose(labels["return_300"], labels_b["return_300"], equal_nan=True)),
        "fingerprint_match": fp_b == identity["episode_keys_sha256"],
        "ok": ok,
    }
