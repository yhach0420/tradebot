"""Control population sampling (non-candidate, mechanical, no future selection)."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from research.e1_x22_actual_exit_factory.paths import session_end_epoch
from research.e1_x28_executable_joint.board import first_valid_quote
from research.e1_x30_absolute_rise_entry_v2.labels import _scan_episode

from . import (
    CLOCK_BUCKET_SEC,
    CONTROL_EXCLUDE_SEC,
    CONTROL_SEED,
    HORIZONS_SEC,
    MAX_CONTROLS_PER_CAND,
    MAX_MARKET_PER_BUCKET,
)

JST = ZoneInfo("Asia/Tokyo")


def clock_bucket(epoch: float) -> int:
    dt = datetime.fromtimestamp(float(epoch), tz=JST)
    sec = dt.hour * 3600 + dt.minute * 60 + dt.second
    return int(sec // CLOCK_BUCKET_SEC) * CLOCK_BUCKET_SEC


def _horizon_from_scan(ep: dict[str, Any]) -> dict[str, Any]:
    out = {"ok": bool(ep.get("ok")), "mfe": ep.get("mfe"), "mae": ep.get("mae")}
    for H in HORIZONS_SEC:
        out[f"return_{H}"] = ep.get(f"return_{H}", np.nan)
        out[f"return_{H}_valid"] = bool(ep.get(f"return_{H}_valid"))
    return out


def evaluate_long_at_signal(
    board: dict[str, np.ndarray],
    *,
    signal_t: float,
    date: str,
    session: str,
) -> dict[str, Any]:
    empty = {"ok": False}
    sess_end = session_end_epoch(date, session)
    if signal_t > sess_end + 1e-9:
        return empty
    q = first_valid_quote(board, signal_t, side="ask")
    if q["status"] != "OK":
        return empty
    ep = _scan_episode(
        board, ask=float(q["price"]), ask_t=float(q["event_time"]), sess_end=sess_end
    )
    return _horizon_from_scan(ep)


def build_candidate_index(rows: list[dict[str, Any]]) -> dict[tuple, set[float]]:
    idx: dict[tuple, set[float]] = defaultdict(set)
    for r in rows:
        idx[(r["date"], r["symbol"], r["session"])].add(float(r["grid_epoch"]))
    return idx


def _sample_control_times(
    board: dict[str, np.ndarray],
    *,
    cand_epochs: set[float],
    bucket_sec: int,
    session: str,
    date: str,
    rng: np.random.Generator,
    n: int,
) -> list[float]:
    t = board["t"]
    if t.size == 0:
        return []
    sess_end = session_end_epoch(date, session)
    candidates_arr = np.asarray(sorted(cand_epochs), dtype=float)
    pool = []
    for ti in t:
        ti = float(ti)
        if ti > sess_end:
            break
        if clock_bucket(ti) != bucket_sec:
            continue
        if candidates_arr.size:
            j = int(np.searchsorted(candidates_arr, ti))
            near = False
            for k in (j - 1, j, j + 1):
                if 0 <= k < candidates_arr.size and abs(candidates_arr[k] - ti) < CONTROL_EXCLUDE_SEC:
                    near = True
                    break
            if near:
                continue
        pool.append(int(ti))
    pool = sorted(set(pool))
    if not pool:
        return []
    if len(pool) <= n:
        return [float(p) for p in pool]
    pick = rng.choice(len(pool), size=n, replace=False)
    return [float(pool[i]) for i in sorted(pick)]


def build_controls(
    *,
    rows: list[dict[str, Any]],
    labels: dict[str, np.ndarray],
    board_by_key: dict[tuple[str, str], dict[str, np.ndarray]],
) -> dict[str, Any]:
    rng = np.random.default_rng(CONTROL_SEED)
    cand_idx = build_candidate_index(rows)
    valid = labels["valid"]

    # Group valid candidates by (date, symbol, session, bucket)
    groups: dict[tuple, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        if not valid[i]:
            continue
        bsec = clock_bucket(float(r["grid_epoch"]))
        groups[(r["date"], r["symbol"], r["session"], bsec)].append(i)

    sym_ctrl_rets: dict[int, list[dict[str, Any]]] = {H: [] for H in HORIZONS_SEC}
    n_ctrl = 0
    done_g = 0
    for (day, sym, sess, bsec), idxs in groups.items():
        done_g += 1
        if done_g % 200 == 0:
            print(f"    same-sym groups {done_g}/{len(groups)}", flush=True)
        board = board_by_key.get((day, sym))
        if board is None or board["t"].size == 0:
            continue
        key = (day, sym, sess)
        times = _sample_control_times(
            board,
            cand_epochs=cand_idx[key],
            bucket_sec=bsec,
            session=sess,
            date=day,
            rng=rng,
            n=MAX_CONTROLS_PER_CAND,
        )
        for st in times:
            ep = evaluate_long_at_signal(
                board, signal_t=st, date=day, session=sess
            )
            if not ep.get("ok"):
                continue
            n_ctrl += 1
            # attach to each candidate in group (matched weight)
            for ci in idxs:
                for H in HORIZONS_SEC:
                    if ep.get(f"return_{H}_valid"):
                        sym_ctrl_rets[H].append({
                            "date": day,
                            "symbol": sym,
                            "bucket": bsec,
                            "ret": float(ep[f"return_{H}"]),
                            "cand_i": ci,
                        })

    # Market control per (date, bucket)
    symbols_by_day: dict[str, set[str]] = defaultdict(set)
    for (d, s), b in board_by_key.items():
        if b["t"].size:
            symbols_by_day[d].add(s)

    by_day_bucket: dict[tuple[str, int], list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        if not valid[i]:
            continue
        by_day_bucket[(r["date"], clock_bucket(float(r["grid_epoch"])))].append(i)

    mkt_ctrl_rets: dict[int, list[dict[str, Any]]] = {H: [] for H in HORIZONS_SEC}
    mkt_keys = list(by_day_bucket.keys())
    for mi, (day, bsec) in enumerate(mkt_keys):
        if mi % 50 == 0:
            print(f"    market buckets {mi}/{len(mkt_keys)}", flush=True)
        cand_is = by_day_bucket[(day, bsec)]
        sess = rows[cand_is[0]]["session"]
        ref_t = float(rows[cand_is[0]]["grid_epoch"])
        dt = datetime.fromtimestamp(ref_t, tz=JST)
        midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        signal_t = midnight + bsec + CLOCK_BUCKET_SEC / 2.0
        cand_syms = {rows[i]["symbol"] for i in cand_is}
        others = sorted(symbols_by_day[day] - cand_syms) or sorted(symbols_by_day[day])
        rng2 = np.random.default_rng(CONTROL_SEED + int(bsec) + int(day))
        if len(others) > MAX_MARKET_PER_BUCKET:
            others = list(rng2.choice(others, size=MAX_MARKET_PER_BUCKET, replace=False))
        for sym in others:
            board = board_by_key.get((day, sym))
            if board is None:
                continue
            for sess_try in (sess, "AM", "PM"):
                ep = evaluate_long_at_signal(
                    board, signal_t=signal_t, date=day, session=sess_try
                )
                if ep.get("ok"):
                    for H in HORIZONS_SEC:
                        if ep.get(f"return_{H}_valid"):
                            mkt_ctrl_rets[H].append({
                                "date": day, "symbol": sym, "bucket": bsec,
                                "ret": float(ep[f"return_{H}"]),
                            })
                    break

    def _agg(items: list[dict[str, Any]]) -> dict[str, Any]:
        if not items:
            return {"n": 0, "mean": None, "median": None, "positive_rate": None}
        # unique control episodes for aggregate mean (avoid candidate-multiplicity inflation)
        # For same-symbol: weight by unique (date,symbol,bucket,ret) — use all matched rows mean
        # Spec: candidate weight ≈ control weight — matched per candidate is OK
        rs = np.asarray([x["ret"] for x in items], dtype=float)
        return {
            "n": int(rs.size),
            "mean": float(np.mean(rs)),
            "median": float(np.median(rs)),
            "positive_rate": float(np.mean(rs > 0)),
        }

    # For headline same-symbol mean, use one row per control evaluation (dedupe by collapsing
    # candidate multiplicity): average first per cand_i then across cands — fairer vs candidate mean
    def _cand_matched_mean(items: list[dict[str, Any]]) -> dict[str, Any]:
        by_c: dict[int, list[float]] = defaultdict(list)
        for x in items:
            by_c[x["cand_i"]].append(x["ret"])
        if not by_c:
            return {"n": 0, "mean": None, "median": None, "positive_rate": None, "n_candidates_matched": 0}
        per = np.asarray([float(np.mean(v)) for v in by_c.values()], dtype=float)
        return {
            "n": int(sum(len(v) for v in by_c.values())),
            "n_candidates_matched": int(len(by_c)),
            "mean": float(np.mean(per)),
            "median": float(np.median(per)),
            "positive_rate": float(np.mean(per > 0)),
        }

    sym_summary = {H: _cand_matched_mean(sym_ctrl_rets[H]) for H in HORIZONS_SEC}
    mkt_summary = {H: _agg(mkt_ctrl_rets[H]) for H in HORIZONS_SEC}

    return {
        "same_symbol_control": sym_summary,
        "market_time_control": mkt_summary,
        "same_symbol_rows": sym_ctrl_rets,
        "market_rows": mkt_ctrl_rets,
        "n_same_symbol_episodes": n_ctrl,
        "control_seed": CONTROL_SEED,
        "clock_bucket_sec": CLOCK_BUCKET_SEC,
    }
