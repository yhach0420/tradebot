"""Valid continuous-board path metrics after fill. Exact-compatible filters. No EXIT rewrite."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.canonical_entry_performance_rebase.analyze import _f
from research.e1_x35_passive_exit.paths import _valid_bid
from research.entry_sequence_representation.sequence import itayose_mask, special_mask
from replay.pnl_yen import compute_pnl_yen_100


def _yen(fill_px: float, bid: float) -> float:
    return float(compute_pnl_yen_100(float(fill_px), float(bid)))


def _bps(fill_px: float, bid: float) -> Optional[float]:
    if fill_px <= 0:
        return None
    return (float(bid) / float(fill_px) - 1.0) * 10000.0


def walk_quotes(
    board: dict[str, np.ndarray],
    *,
    fill_t: float,
    fill_px: float,
    eval_end: float,
    sess_end: float,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    leak = {
        "ITAYOSE_PATH_USE_N": 0,
        "SPECIAL_BOARD_PATH_USE_N": 0,
        "SESSION_CARRY_N": 0,
        "INVALID_QUOTE_USE_N": 0,
        "ITAYOSE_SKIP_N": 0,
        "SPECIAL_SKIP_N": 0,
        "INVALID_SKIP_N": 0,
    }
    t = board.get("t")
    if t is None or int(getattr(t, "size", 0) or 0) == 0:
        return [], leak
    itay = itayose_mask(board)
    spec = special_mask(board)
    n = int(t.size)
    i0 = int(np.searchsorted(t, float(fill_t), side="left"))
    quotes: list[dict[str, Any]] = []
    for i in range(i0, n):
        ti = float(t[i])
        if ti + 1e-12 < float(fill_t):
            continue
        if ti > float(sess_end) + 1e-12:
            break
        if ti > float(eval_end) + 1e-12:
            break
        if i < itay.size and bool(itay[i]):
            leak["ITAYOSE_SKIP_N"] += 1
            continue
        if i < spec.size and bool(spec[i]):
            leak["SPECIAL_SKIP_N"] += 1
            continue
        if not _valid_bid(board, i):
            leak["INVALID_SKIP_N"] += 1
            continue
        bid = float(board["bid"][i])
        quotes.append(
            {
                "t": ti,
                "bid": bid,
                "yen": _yen(fill_px, bid),
                "bps": _bps(fill_px, bid),
                "off": ti - float(fill_t),
            }
        )
    return quotes, leak


def _slice(quotes: list[dict[str, Any]], *, lo: float, hi: float, lo_inclusive: bool) -> list[dict[str, Any]]:
    out = []
    for q in quotes:
        t = float(q["t"])
        if lo_inclusive:
            if t + 1e-12 < lo:
                continue
        elif t <= lo + 1e-12:
            continue
        if t > hi + 1e-12:
            continue
        out.append(q)
    return out


def _extrema(quotes: list[dict[str, Any]]) -> dict[str, Any]:
    if not quotes:
        return {
            "mfe_yen": None,
            "mae_yen": None,
            "mfe_bps": None,
            "mae_bps": None,
            "mfe_t": None,
            "mae_t": None,
            "mfe_off": None,
            "mae_off": None,
            "last_yen": None,
            "last_t": None,
            "n": 0,
        }
    yen = [float(q["yen"]) for q in quotes]
    i_mfe = int(np.argmax(yen))
    i_mae = int(np.argmin(yen))
    last = quotes[-1]
    return {
        "mfe_yen": float(yen[i_mfe]),
        "mae_yen": float(yen[i_mae]),
        "mfe_bps": quotes[i_mfe].get("bps"),
        "mae_bps": quotes[i_mae].get("bps"),
        "mfe_t": float(quotes[i_mfe]["t"]),
        "mae_t": float(quotes[i_mae]["t"]),
        "mfe_off": float(quotes[i_mfe]["off"]),
        "mae_off": float(quotes[i_mae]["off"]),
        "last_yen": float(last["yen"]),
        "last_t": float(last["t"]),
        "n": len(quotes),
    }


def path_metrics(
    board: dict[str, np.ndarray],
    *,
    fill_t: float,
    fill_px: float,
    exit_t: float,
    realized_pnl: float,
    eval_end: float,
    sess_end: float,
) -> dict[str, Any]:
    quotes, leak = walk_quotes(
        board,
        fill_t=float(fill_t),
        fill_px=float(fill_px),
        eval_end=float(eval_end),
        sess_end=float(sess_end),
    )
    pre = _slice(quotes, lo=float(fill_t), hi=float(exit_t), lo_inclusive=True)
    full = _slice(quotes, lo=float(fill_t), hi=float(eval_end), lo_inclusive=True)
    post_available = float(eval_end) > float(exit_t) + 1e-12
    post = _slice(quotes, lo=float(exit_t), hi=float(eval_end), lo_inclusive=False) if post_available else []
    pre_x = _extrema(pre)
    full_x = _extrema(full)
    post_x = _extrema(post) if post else None
    mfe_pre = pre_x["mfe_yen"]
    giveback = (float(mfe_pre) - float(realized_pnl)) if mfe_pre is not None else None
    post_max = post_x["mfe_yen"] if post_x else None
    post_min = post_x["mae_yen"] if post_x else None
    recovery = (float(post_max) - float(realized_pnl)) if post_max is not None else None
    return {
        "ok": bool(pre_x["n"] > 0),
        "quote_n_pre_exit": pre_x["n"],
        "quote_n_to_eval_end": full_x["n"],
        "quote_n_post_exit": int(post_x["n"] if post_x else 0),
        "POST_EXIT_AVAILABLE": bool(post_available and post),
        "CANONICAL_EXIT_EVAL_END": float(eval_end),
        "MFE_PRE_EXIT_YEN_100": mfe_pre,
        "MAE_PRE_EXIT_YEN_100": pre_x["mae_yen"],
        "MFE_PRE_EXIT_BPS": pre_x["mfe_bps"],
        "MAE_PRE_EXIT_BPS": pre_x["mae_bps"],
        "TIME_TO_MFE_SEC": pre_x["mfe_off"],
        "TIME_TO_MAE_SEC": pre_x["mae_off"],
        "PNL_PEAK_TIME": pre_x["mfe_t"],
        "GIVEBACK_FROM_MFE_TO_EXIT": giveback,
        "MFE_TO_EVAL_END_YEN_100": full_x["mfe_yen"],
        "MAE_TO_EVAL_END_YEN_100": full_x["mae_yen"],
        "PNL_AT_EVAL_END_YEN_100": full_x["last_yen"],
        "POST_EXIT_MAX_PNL_YEN_100": post_max,
        "POST_EXIT_MIN_PNL_YEN_100": post_min,
        "POST_EXIT_RECOVERY_VS_EXIT": recovery,
        "integrity": leak,
    }
