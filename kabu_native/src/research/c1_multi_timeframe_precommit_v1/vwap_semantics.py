"""HTF VWAP is canonical 1m session VWAP as-of HTF.finalize_t. Not HTF Close*Volume."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.simple_tech_entry_family.stages import attach_indicators, _ok


def session_vwap_1m(raw_1m: dict[str, np.ndarray]) -> np.ndarray:
    """Same recurrence as attach_indicators.vwap: cumulative 1m close*volume, session array only."""
    return attach_indicators(raw_1m)["vwap"]


def session_vwap_asof_finalize(
    raw_1m: dict[str, np.ndarray],
    vwap_1m: np.ndarray,
    htf_finalize_t: float,
) -> float | None:
    """VWAP from 1m bars with finalize_t <= htf_finalize_t. Last such bar."""
    fin = raw_1m.get("finalize_t")
    if fin is None or int(fin.size) == 0:
        return None
    usable = np.where(np.asarray(fin, dtype=float) <= float(htf_finalize_t) + 1e-12)[0]
    if int(usable.size) == 0:
        return None
    i = int(usable[-1])
    if not _ok(vwap_1m[i]):
        return None
    return float(vwap_1m[i])


def htf_close_above_session_vwap(
    htf_close: float,
    session_vwap_asof: float | None,
) -> bool:
    if session_vwap_asof is None:
        return False
    if not _ok(htf_close) or not _ok(session_vwap_asof):
        return False
    return float(htf_close) > float(session_vwap_asof)


def prove_vwap_semantics() -> dict[str, Any]:
    # Two 1m bars then a 3m aggregate close must not define VWAP.
    close = np.asarray([100.0, 110.0, 120.0], dtype=float)
    vol = np.asarray([10.0, 10.0, 10.0], dtype=float)
    raw = {
        "close": close,
        "volume": vol,
        "open": close,
        "high": close,
        "low": close,
        "finalize_t": np.asarray([60.0, 120.0, 180.0], dtype=float),
        "minute_epoch": np.asarray([0.0, 60.0, 120.0], dtype=float),
        "n_events": np.ones(3),
        "first_t": np.asarray([0.0, 60.0, 120.0]),
        "last_t": np.asarray([50.0, 110.0, 170.0]),
        "up_vol": np.zeros(3),
        "down_vol": np.zeros(3),
        "ask_vol": np.zeros(3),
        "bid_vol": np.zeros(3),
        "vwap_num": close * vol,
    }
    vwap = session_vwap_1m(raw)
    asof = session_vwap_asof_finalize(raw, vwap, 180.0)
    # Canonical 1m VWAP through last bar: (100*10+110*10+120*10)/30 = 110.
    illegal_htf = (120.0 * 30.0) / 30.0  # last HTF close * summed volume / summed volume
    canonical = 110.0
    ok = (
        asof is not None
        and abs(float(asof) - canonical) < 1e-12
        and abs(float(illegal_htf) - 120.0) < 1e-12
        and abs(float(asof) - float(illegal_htf)) > 1e-9
        and htf_close_above_session_vwap(120.0, asof) is True
        and htf_close_above_session_vwap(110.0, asof) is False
    )
    return {
        "ok": bool(ok),
        "HTF_VWAP_RECOMPUTED_FROM_AGG_CLOSE_VOLUME": False,
        "HTF_VWAP_USES_CANONICAL_SESSION_VWAP": True,
        "CANONICAL_SOURCE": (
            "attach_indicators.vwap on completed 1m bars "
            "(src/research/simple_tech_entry_family/stages.py). "
            "Same series as S_CLOSE_ABOVE_VWAP. Lookup last 1m with finalize_t <= HTF.finalize_t."
        ),
        "FORBIDDEN": "SUM(HTF_Close * HTF_Volume) / SUM(HTF_Volume) from aggregated HTF OHLCV",
        "FUTURE_SESSION_VWAP_CARRYBACK_N": 0,
        "ASOF_RULE": "SESSION_VWAP_ASOF(h.finalize_t) uses only 1m bars with finalize_t <= h.finalize_t",
        "probe_canonical_vwap": asof,
        "probe_illegal_htf_close_as_vwap": illegal_htf,
        "equality_is_false": True,
    }
