"""Freeze date-block bootstrap index identities. No outcome refits."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.causal_driver_pb1.identity.ids import sha256_bytes, sha256_obj
from research.causal_driver_pb1.sector_breadth_precommit_v1_1.inference import draw_date_block_indices, rng_version
from research.causal_driver_pb1.sector_state_transmission_precommit import (
    BOOTSTRAP_N,
    DISCOVERY_BOOTSTRAP_SEED,
    FV_BOOTSTRAP_SEED,
)


def _freeze(*, period: str, dates: list[str], seed: int) -> dict[str, Any]:
    idx = draw_date_block_indices(n_day=len(dates), n_boot=BOOTSTRAP_N, seed=int(seed))
    index_sha = sha256_bytes(np.ascontiguousarray(idx, dtype="<i8").tobytes())
    payload = {
        "bootstrap_method": "DATE_BLOCK_BOOTSTRAP",
        "sampling_unit": "trading date",
        "with_replacement": True,
        "duplicate_date_repeats_entire_intraday_block": True,
        "no_independent_minute_resample": True,
        "refit_MODEL1_each_replicate": True,
        "rng_algorithm": "MT19937",
        "rng_library": "numpy.random.RandomState",
        "bootstrap_n": int(BOOTSTRAP_N),
        "bootstrap_seed": int(seed),
        "period": period,
        "n_day": len(dates),
        "dates": list(dates),
        "index_dtype": "int64_little_endian",
        "index_sha256": index_sha,
        "draw": "RandomState.randint(low=0, high=n_day, size=n_day) once per replicate",
    }
    return {
        "bootstrap_index_sha256": sha256_obj(payload),
        "index_sha256": index_sha,
        "n_day": len(dates),
        "bootstrap_n": int(BOOTSTRAP_N),
        "bootstrap_seed": int(seed),
        "period": period,
        "first_date": dates[0] if dates else None,
        "last_date": dates[-1] if dates else None,
        "rng_version": rng_version(),
        "outcomes_used": False,
    }


def freeze_bootstraps(*, discovery_dates: list[str], fv_dates: list[str]) -> dict[str, Any]:
    return {
        "discovery": _freeze(period="TRANSMISSION_DISCOVERY_EXPOSED", dates=list(discovery_dates), seed=DISCOVERY_BOOTSTRAP_SEED),
        "fv": _freeze(period="TRANSMISSION_FROZEN_VALIDATION", dates=list(fv_dates), seed=FV_BOOTSTRAP_SEED),
    }
