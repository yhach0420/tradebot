"""Evaluate OLD / PARENT / CAUSAL / CONTROL arms under same ask→bid contract."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.e1_x22_actual_exit_factory.paths import session_end_epoch
from research.e1_x28_executable_joint.board import load_board_events
from research.e1_x31_population_direction.controls import evaluate_long_at_signal
from research.e1_x32_upstream_attribution import CLOCK_POINTS_HM, HORIZONS_SEC
from research.e1_x32_upstream_attribution.eval_stages import (
    clock_epochs_for_day,
    load_boards_for_symbols,
    summarize_evals,
)

from . import HISTORICAL_DAYS, SAMPLING_SEED


def _ft_from_scan(ep: dict[str, Any]) -> dict[str, Any]:
    # evaluate_long_at_signal returns horizon returns; FT rates need path — approximate via X30-style
    # For arms without FT arrays, leave None unless we extend scan. Use reach via mfe/mae proxy only.
    return {}


def evaluate_timestamps(
    *,
    rows: list[dict[str, Any]],
    board_by_key: dict,
) -> list[dict[str, Any]]:
    """rows need date, symbol, session, grid_epoch."""
    out = []
    for r in rows:
        board = board_by_key.get((r["date"], r["symbol"]))
        if board is None or board["t"].size == 0:
            continue
        ep = evaluate_long_at_signal(
            board,
            signal_t=float(r["grid_epoch"]),
            date=r["date"],
            session=r["session"],
        )
        if not ep.get("ok"):
            continue
        sess_end = session_end_epoch(r["date"], r["session"])
        mins = (sess_end - float(r["grid_epoch"])) / 60.0
        rec = {
            "date": r["date"],
            "symbol": r["symbol"],
            "session": r["session"],
            "signal_t": float(r["grid_epoch"]),
            "minutes_to_session_close": mins,
            "ok": True,
            "mfe": ep.get("mfe"),
            "mae": ep.get("mae"),
        }
        for H in HORIZONS_SEC:
            rec[f"return_{H}"] = ep.get(f"return_{H}")
            rec[f"return_{H}_valid"] = ep.get(f"return_{H}_valid")
        out.append(rec)
    return out


def old_from_labels(
    cand_rows: list[dict[str, Any]],
    labels: dict[str, np.ndarray],
) -> list[dict[str, Any]]:
    out = []
    for i, r in enumerate(cand_rows):
        if not labels["valid"][i]:
            continue
        sess_end = session_end_epoch(r["date"], r["session"])
        mins = (sess_end - float(r["grid_epoch"])) / 60.0
        rec = {
            "date": r["date"],
            "symbol": r["symbol"],
            "session": r["session"],
            "signal_t": float(r["grid_epoch"]),
            "minutes_to_session_close": mins,
            "ok": True,
            "mfe": float(labels["mfe"][i]) if np.isfinite(labels["mfe"][i]) else None,
            "mae": float(labels["mae"][i]) if np.isfinite(labels["mae"][i]) else None,
            "primary_ft": bool(labels["primary"][i]),
            "ft_20_20_300": bool(labels["ft_20_20_300"][i]),
            "ft_30_20_600": bool(labels["ft_30_20_600"][i]),
        }
        for H in HORIZONS_SEC:
            ok = bool(labels[f"return_{H}_valid"][i])
            rec[f"return_{H}_valid"] = ok
            rec[f"return_{H}"] = float(labels[f"return_{H}"][i]) if ok else None
        out.append(rec)
    return out


def parent_fixed_clock(
    cand_rows: list[dict[str, Any]],
    board_by_key: dict,
) -> list[dict[str, Any]]:
    """CANDIDATE_SYMBOL_POOL at X32 fixed clock points."""
    by_day: dict[str, set[str]] = {d: set() for d in HISTORICAL_DAYS}
    for r in cand_rows:
        if r["date"] in by_day:
            by_day[r["date"]].add(str(r["symbol"]))
    out = []
    for d, syms in by_day.items():
        for epoch, sess in clock_epochs_for_day(d):
            for sym in syms:
                board = board_by_key.get((d, sym))
                if board is None or board["t"].size == 0:
                    continue
                ep = evaluate_long_at_signal(
                    board, signal_t=epoch, date=d, session=sess
                )
                if not ep.get("ok"):
                    continue
                sess_end = session_end_epoch(d, sess)
                rec = {
                    "date": d, "symbol": sym, "session": sess,
                    "signal_t": epoch,
                    "minutes_to_session_close": (sess_end - epoch) / 60.0,
                    "ok": True, "mfe": ep.get("mfe"), "mae": ep.get("mae"),
                }
                for H in HORIZONS_SEC:
                    rec[f"return_{H}"] = ep.get(f"return_{H}")
                    rec[f"return_{H}_valid"] = ep.get(f"return_{H}_valid")
                out.append(rec)
    return out


def control_feature_ok_fixed_clock(
    grids: list[dict[str, Any]],
    board_by_key: dict,
) -> list[dict[str, Any]]:
    """FEATURE_OK grids snapped to nearest fixed clock within ±60s, or exact clock eval on feature-ok symbols."""
    # Simpler: for each day, symbols with any FEATURE_OK, evaluate at fixed clocks (same as parent but feat-ok universe)
    by_day: dict[str, set[str]] = defaultdict(set)
    for r in grids:
        if r.get("feature_status") == "OK" and r["date"] in HISTORICAL_DAYS:
            by_day[r["date"]].add(str(r["symbol"]))
    out = []
    rng = np.random.default_rng(SAMPLING_SEED)
    _ = rng
    for d, syms in by_day.items():
        for epoch, sess in clock_epochs_for_day(d):
            for sym in syms:
                board = board_by_key.get((d, sym))
                if board is None or board["t"].size == 0:
                    continue
                ep = evaluate_long_at_signal(
                    board, signal_t=epoch, date=d, session=sess
                )
                if not ep.get("ok"):
                    continue
                sess_end = session_end_epoch(d, sess)
                rec = {
                    "date": d, "symbol": sym, "session": sess,
                    "signal_t": epoch,
                    "minutes_to_session_close": (sess_end - epoch) / 60.0,
                    "ok": True, "mfe": ep.get("mfe"), "mae": ep.get("mae"),
                }
                for H in HORIZONS_SEC:
                    rec[f"return_{H}"] = ep.get(f"return_{H}")
                    rec[f"return_{H}_valid"] = ep.get(f"return_{H}_valid")
                out.append(rec)
    return out


def enrich_summary(evals: list[dict[str, Any]]) -> dict[str, Any]:
    s = summarize_evals(evals)
    # FT rates if present
    if evals and "primary_ft" in evals[0]:
        s["primary_ft_rate"] = float(np.mean([e["primary_ft"] for e in evals]))
    if evals and "ft_20_20_300" in evals[0]:
        s["ft_20_20_300_rate"] = float(np.mean([e["ft_20_20_300"] for e in evals]))
    if evals and "ft_30_20_600" in evals[0]:
        s["ft_30_20_600_rate"] = float(np.mean([e["ft_30_20_600"] for e in evals]))
    return s


def build_board_cache(
    cand_rows: list[dict[str, Any]],
    grids: list[dict[str, Any]],
    anchors: list[dict[str, Any]],
) -> dict:
    pairs = set()
    for r in cand_rows:
        pairs.add((r["date"], r["symbol"]))
    for r in grids:
        if r["date"] in HISTORICAL_DAYS:
            pairs.add((r["date"], str(r["symbol"])))
    for r in anchors:
        pairs.add((r["date"], str(r["symbol"])))
    return load_boards_for_symbols(sorted(pairs))
