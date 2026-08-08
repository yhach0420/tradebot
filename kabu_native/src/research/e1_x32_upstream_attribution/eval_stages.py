"""Common-clock LONG evaluation (same ask→bid contract as X28/X30/X31)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from research.e1_x28_executable_joint.board import load_board_events
from research.e1_x31_population_direction.controls import evaluate_long_at_signal

from . import CLOCK_POINTS_HM, FORBIDDEN_FROM, HISTORICAL_DAYS, HORIZONS_SEC, SAMPLING_SEED
from .membership import load_captured_symbols, load_universe_symbols

JST = ZoneInfo("Asia/Tokyo")


def clock_epochs_for_day(day: str) -> list[tuple[float, str]]:
    """Fixed precommitted clock grid → (epoch, session)."""
    y, m, d = int(day[:4]), int(day[4:6]), int(day[6:8])
    out = []
    for hh, mm in CLOCK_POINTS_HM:
        dt = datetime(y, m, d, hh, mm, 0, tzinfo=JST)
        sess = "AM" if hh < 12 else "PM"
        out.append((dt.timestamp(), sess))
    return out


def load_boards_for_symbols(
    day_symbol_pairs: list[tuple[str, str]],
) -> dict[tuple[str, str], dict[str, np.ndarray]]:
    keys = sorted(set(day_symbol_pairs))
    # guard
    for d, _ in keys:
        assert d < FORBIDDEN_FROM
    cache: dict[tuple[str, str], dict[str, np.ndarray]] = {}
    print(f"  boards {len(keys)}...", flush=True)

    def _one(k):
        return k, load_board_events(k[0], k[1])

    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = [ex.submit(_one, k) for k in keys]
        done = 0
        for fut in as_completed(futs):
            k, b = fut.result()
            cache[k] = b
            done += 1
            if done % 80 == 0 or done == len(keys):
                print(f"    {done}/{len(keys)}", flush=True)
    return cache


def evaluate_symbol_pool_at_clock(
    *,
    day: str,
    symbols: set[str],
    board_by_key: dict[tuple[str, str], dict[str, np.ndarray]],
) -> list[dict[str, Any]]:
    """Evaluate all symbols at fixed clock points (LONG ask→bid)."""
    rows = []
    for epoch, sess in clock_epochs_for_day(day):
        for sym in symbols:
            board = board_by_key.get((day, sym))
            if board is None or board["t"].size == 0:
                continue
            ep = evaluate_long_at_signal(
                board, signal_t=epoch, date=day, session=sess
            )
            if not ep.get("ok"):
                continue
            rec = {
                "date": day,
                "symbol": sym,
                "session": sess,
                "signal_t": epoch,
                "ok": True,
                "mfe": ep.get("mfe"),
                "mae": ep.get("mae"),
            }
            for H in HORIZONS_SEC:
                rec[f"return_{H}"] = ep.get(f"return_{H}")
                rec[f"return_{H}_valid"] = ep.get(f"return_{H}_valid")
            rows.append(rec)
    return rows


def summarize_evals(evals: list[dict[str, Any]]) -> dict[str, Any]:
    if not evals:
        return {
            "episodes": 0, "symbols": 0, "symbol_days": 0,
            **{f"ret{H}": None for H in HORIZONS_SEC},
            "positive_rate_300": None, "positive_rate_600": None,
            "mfe": None, "mae": None,
        }
    syms = {(e["date"], e["symbol"]) for e in evals}
    out: dict[str, Any] = {
        "episodes": len(evals),
        "symbols": len({e["symbol"] for e in evals}),
        "symbol_days": len(syms),
    }
    for H in HORIZONS_SEC:
        rs = [float(e[f"return_{H}"]) for e in evals if e.get(f"return_{H}_valid")]
        out[f"ret{H}"] = float(np.mean(rs)) if rs else None
        if H in (300, 600):
            out[f"positive_rate_{H}"] = float(np.mean(np.asarray(rs) > 0)) if rs else None
    mfes = [float(e["mfe"]) for e in evals if e.get("mfe") is not None and np.isfinite(e["mfe"])]
    maes = [float(e["mae"]) for e in evals if e.get("mae") is not None and np.isfinite(e["mae"])]
    out["mfe"] = float(np.mean(mfes)) if mfes else None
    out["mae"] = float(np.mean(maes)) if maes else None
    return out


def build_stage_evals(
    *,
    cand_rows: list[dict[str, Any]],
    cand_labels: dict[str, np.ndarray],
) -> dict[str, Any]:
    """
    Build performance for attribution stages under common clock (except anchors use episode times).
    """
    rng = np.random.default_rng(SAMPLING_SEED)  # reserved; clock is fixed grid
    _ = rng

    # symbol sets
    captured: dict[str, set[str]] = {}
    universe: dict[str, set[str]] = {}
    cand_syms: dict[str, set[str]] = {d: set() for d in HISTORICAL_DAYS}
    for r in cand_rows:
        if r["date"] in cand_syms:
            cand_syms[r["date"]].add(str(r["symbol"]))

    pairs: list[tuple[str, str]] = []
    for d in HISTORICAL_DAYS:
        captured[d] = load_captured_symbols(d)
        uni = load_universe_symbols(d)
        universe[d] = uni & captured[d]  # board-usable universe
        if not universe[d]:
            universe[d] = uni  # fallback
        for s in captured[d] | universe[d] | cand_syms[d]:
            pairs.append((d, s))

    board_by_key = load_boards_for_symbols(pairs)

    stage_evals: dict[str, list[dict[str, Any]]] = {
        "CAPTURED_MARKET_PROXY": [],
        "RUNTIME_UNIVERSE_SELECTED": [],
        "CANDIDATE_SYMBOL_POOL": [],
    }
    for d in HISTORICAL_DAYS:
        print(f"  clock-eval {d}...", flush=True)
        stage_evals["CAPTURED_MARKET_PROXY"].extend(
            evaluate_symbol_pool_at_clock(day=d, symbols=captured[d], board_by_key=board_by_key)
        )
        stage_evals["RUNTIME_UNIVERSE_SELECTED"].extend(
            evaluate_symbol_pool_at_clock(day=d, symbols=universe[d], board_by_key=board_by_key)
        )
        stage_evals["CANDIDATE_SYMBOL_POOL"].extend(
            evaluate_symbol_pool_at_clock(day=d, symbols=cand_syms[d], board_by_key=board_by_key)
        )

    # Candidate anchors: reuse X30 labels at episode times
    anchor_evals = []
    for i, r in enumerate(cand_rows):
        if not cand_labels["valid"][i]:
            continue
        rec = {
            "date": r["date"],
            "symbol": r["symbol"],
            "session": r["session"],
            "signal_t": float(r["grid_epoch"]),
            "ok": True,
            "mfe": float(cand_labels["mfe"][i]) if np.isfinite(cand_labels["mfe"][i]) else None,
            "mae": float(cand_labels["mae"][i]) if np.isfinite(cand_labels["mae"][i]) else None,
        }
        for H in HORIZONS_SEC:
            ok = bool(cand_labels[f"return_{H}_valid"][i])
            rec[f"return_{H}_valid"] = ok
            rec[f"return_{H}"] = float(cand_labels[f"return_{H}"][i]) if ok else None
        # first-touch from X30
        rec["primary_ft"] = bool(cand_labels["primary"][i])
        anchor_evals.append(rec)
    stage_evals["CANDIDATE_CLUSTER_ANCHORS"] = anchor_evals

    summaries = {k: summarize_evals(v) for k, v in stage_evals.items()}
    # attach FT rates for anchors
    if anchor_evals:
        summaries["CANDIDATE_CLUSTER_ANCHORS"]["primary_ft_rate"] = float(
            np.mean([e.get("primary_ft") for e in anchor_evals])
        )

    return {
        "evals": stage_evals,
        "summaries": summaries,
        "sampling_seed": SAMPLING_SEED,
        "clock_points_hm": list(CLOCK_POINTS_HM),
        "board_keys_n": len(board_by_key),
        "symbol_sets": {
            d: {
                "captured": sorted(captured[d]),
                "universe": sorted(universe[d]),
                "candidate": sorted(cand_syms[d]),
            }
            for d in HISTORICAL_DAYS
        },
    }
