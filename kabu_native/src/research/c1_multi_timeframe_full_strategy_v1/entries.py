"""1m FALSE→TRUE onset plus last completed same-family HTF standing context. No global VWAP."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.c1_multi_timeframe_full_strategy_v1 import GLOBAL_VWAP_ENTRY_GATE, VWAP_CANDIDATE_IDS
from research.c1_multi_timeframe_full_strategy_v1.spec import parse_candidate_id
from research.c1_multi_timeframe_precommit_v1.asof_semantics import asof_htf_index
from research.c1_multi_timeframe_precommit_v1.bucket_alignment import bucket_start
from research.c1_multi_timeframe_precommit_v1.vwap_semantics import (
    htf_close_above_session_vwap,
    session_vwap_asof_finalize,
)
from research.recovery_sequence_full_strategy_architecture_v1.entries import signal_indices as recovery_signal_indices
from research.simple_tech_entry_family import BB_PERIOD, RCI_PERIOD, VOLUME_MEDIAN_BARS, WARMUP_BARS
from research.simple_tech_entry_family.stages import _ok, attach_indicators, evaluable
from research.simple_tech_entry_family.v7_bars import agg_integrity, aggregate_bars
from research.systematic_state_transition_full_strategy_v1.states import STATE_FNS, state_at


def family_evaluable(ind: dict[str, np.ndarray], state_id: str, i: int) -> bool:
    n = int(ind["close"].size)
    if i < 0 or i >= n:
        return False
    if state_id == "S_MA_TREND_UP":
        if not evaluable(int(i), n):
            return False
        j = int(i) - 3
        return bool(_ok(ind["ema9"][i]) and _ok(ind["ema21"][i]) and j >= 0 and _ok(ind["ema21"][j]))
    if state_id == "S_BB_ABOVE_MID":
        return int(i) >= int(BB_PERIOD) - 1 and _ok(ind["close"][i]) and _ok(ind["bb_mid"][i])
    if state_id == "S_RCI_ABOVE_NEG80":
        return int(i) >= int(RCI_PERIOD) - 1 and _ok(ind["rci9"][i])
    if state_id == "S_VOL_CONFIRM_1M":
        w = int(VOLUME_MEDIAN_BARS)
        if int(i) < w:
            return False
        base = ind["volume"][i - w : i]
        return bool(np.all(np.isfinite(base)) and _ok(ind["volume"][i]))
    if state_id == "S_CLOSE_ABOVE_VWAP":
        return bool(_ok(ind["close"][i]) and _ok(ind["vwap"][i]))
    raise RuntimeError(f"UNKNOWN_STATE:{state_id}")


def htf_vwap_state(htf: dict[str, np.ndarray], raw_1m: dict[str, np.ndarray], vwap_1m: np.ndarray, h: int) -> bool:
    if h < 0 or h >= int(htf["close"].size):
        return False
    asof = session_vwap_asof_finalize(raw_1m, vwap_1m, float(htf["finalize_t"][h]))
    return htf_close_above_session_vwap(float(htf["close"][h]), asof)


def htf_vwap_evaluable(htf: dict[str, np.ndarray], raw_1m: dict[str, np.ndarray], vwap_1m: np.ndarray, h: int) -> bool:
    if h < 0 or h >= int(htf["close"].size):
        return False
    asof = session_vwap_asof_finalize(raw_1m, vwap_1m, float(htf["finalize_t"][h]))
    return asof is not None and _ok(htf["close"][h])


def build_htf(
    raw_1m: dict[str, np.ndarray],
    *,
    width_sec: float,
    am_start: float,
    am_end: float,
) -> tuple[dict[str, np.ndarray], dict[str, Any], dict[str, np.ndarray]]:
    htf, leak = aggregate_bars(raw_1m, width_sec=float(width_sec), am_start=float(am_start), am_end=float(am_end))
    integ = agg_integrity(htf, width_sec=float(width_sec), am_start=float(am_start), am_end=float(am_end))
    if int(htf["close"].size) == 0:
        return htf, leak, {"close": htf["close"]}
    ind = attach_indicators(htf)
    # attach_indicators.vwap on aggregated HTF OHLCV is not canonical session VWAP. Do not use it.
    ind["vwap"] = np.full(int(htf["close"].size), np.nan, dtype=float)
    return htf, {**leak, "integ": integ}, ind


def htf_family_true(
    *,
    state_id: str,
    htf: dict[str, np.ndarray],
    htf_ind: dict[str, np.ndarray],
    raw_1m: dict[str, np.ndarray],
    vwap_1m: np.ndarray,
    h: int,
) -> tuple[bool, bool]:
    """Return (evaluable, value). VWAP family uses 1m session VWAP as-of HTF.finalize_t."""
    if state_id == "S_CLOSE_ABOVE_VWAP":
        ev = htf_vwap_evaluable(htf, raw_1m, vwap_1m, h)
        if not ev:
            return False, False
        return True, htf_vwap_state(htf, raw_1m, vwap_1m, h)
    if not family_evaluable(htf_ind, state_id, h):
        return False, False
    return True, bool(state_at(htf_ind, state_id, h))


def mtf_signal_indices(
    ind_1m: dict[str, np.ndarray],
    raw_1m: dict[str, np.ndarray],
    *,
    state_id: str,
    htf: dict[str, np.ndarray],
    htf_ind: dict[str, np.ndarray],
    vwap_1m: np.ndarray,
    am_start: float,
    width_sec: float,
    leak: dict[str, int],
) -> list[int]:
    if GLOBAL_VWAP_ENTRY_GATE:
        raise RuntimeError("GLOBAL_VWAP_ENTRY_GATE")
    n = int(ind_1m["close"].size)
    out: list[int] = []
    fin_1m = raw_1m["finalize_t"]
    for i in range(1, n):
        if not family_evaluable(ind_1m, state_id, i - 1):
            continue
        if not family_evaluable(ind_1m, state_id, i):
            continue
        prev = bool(state_at(ind_1m, state_id, i - 1))
        now = bool(state_at(ind_1m, state_id, i))
        if not (now and not prev):
            continue
        t0 = float(fin_1m[i])
        idx = asof_htf_index(htf["finalize_t"], t0)
        if idx is None:
            continue
        htf_fin = float(htf["finalize_t"][idx])
        if htf_fin > t0 + 1e-12:
            leak["FUTURE_HTF_BAR_N"] = int(leak.get("FUTURE_HTF_BAR_N") or 0) + 1
            continue
        htf_start = float(htf["minute_epoch"][idx])
        if htf_start + 1e-12 < float(am_start):
            leak["CROSS_SESSION_HTF_BAR_N"] = int(leak.get("CROSS_SESSION_HTF_BAR_N") or 0) + 1
            continue
        one_start = float(raw_1m["minute_epoch"][i])
        bucket = bucket_start(one_start, am_start=float(am_start), width_sec=float(width_sec))
        if abs(htf_start - float(bucket)) <= 1e-9 and htf_fin > t0 + 1e-12:
            leak["SAME_BUCKET_CARRYBACK_N"] = int(leak.get("SAME_BUCKET_CARRYBACK_N") or 0) + 1
            continue
        ev, val = htf_family_true(
            state_id=state_id,
            htf=htf,
            htf_ind=htf_ind,
            raw_1m=raw_1m,
            vwap_1m=vwap_1m,
            h=int(idx),
        )
        if not ev or not val:
            continue
        out.append(int(i))
    return out


def candidate_signal_indices(
    cid: str,
    ind_1m: dict[str, np.ndarray],
    raw_1m: dict[str, np.ndarray],
    *,
    htf_by: dict[str, tuple[dict[str, np.ndarray], dict[str, np.ndarray]]],
    vwap_1m: np.ndarray,
    am_start: float,
    leak: dict[str, int],
) -> list[int]:
    meta = parse_candidate_id(cid)
    if bool(meta["VWAP_ENTRY_GATE"]) != (cid in VWAP_CANDIDATE_IDS):
        raise RuntimeError("VWAP_GATE_DRIFT")
    htf_id = str(meta["HTF_ID"])
    htf, htf_ind = htf_by[htf_id]
    width = 180.0 if htf_id == "HTF_3M" else 300.0
    return mtf_signal_indices(
        ind_1m,
        raw_1m,
        state_id=str(meta["STATE_ID"]),
        htf=htf,
        htf_ind=htf_ind,
        vwap_1m=vwap_1m,
        am_start=am_start,
        width_sec=width,
        leak=leak,
    )


def canary_r2_indices(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    vwap: np.ndarray,
) -> list[int]:
    return recovery_signal_indices("R2", open_, high, low, close, volume, vwap)


assert int(WARMUP_BARS) == 24
assert GLOBAL_VWAP_ENTRY_GATE is False
