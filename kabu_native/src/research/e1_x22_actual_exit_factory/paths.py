"""Phase B: post-entry CurrentPrice path reconstruction + Benchmark parity."""
from __future__ import annotations

import hashlib
import pickle
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import numpy as np

from . import ALL_DAYS, AM_SESSION_CLOSE_HM, BENCHMARK_EXITS, FORBIDDEN_DAY, PM_SESSION_CLOSE_HM

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "e1_x22_actual_exit_factory"


def session_end_epoch(day: str, session: str) -> float:
    y, m, d = int(day[:4]), int(day[4:6]), int(day[6:])
    hm = AM_SESSION_CLOSE_HM if session == "AM" else PM_SESSION_CLOSE_HM
    return datetime(y, m, d, hm[0], hm[1], tzinfo=JST).timestamp()


def _dash(day: str) -> str:
    return f"{day[:4]}-{day[4:6]}-{day[6:]}"


def _load_price_events(day: str, symbol: str) -> tuple[np.ndarray, np.ndarray]:
    """Slim loader: recorded_at epoch + CurrentPrice only (board ignored)."""
    try:
        import orjson as jsonlib
        loads = jsonlib.loads
    except Exception:
        import json as jsonlib
        loads = jsonlib.loads

    fp = NATIVE / "data" / "push_jsonl" / _dash(day) / f"{symbol}.T.jsonl"
    if not fp.exists():
        fp = NATIVE / "data" / "push_jsonl" / _dash(day) / f"{symbol}.jsonl"
    if not fp.exists():
        return np.empty(0, dtype=np.float64), np.empty(0, dtype=np.float64)

    ts_list: list[float] = []
    px_list: list[float] = []
    for line in fp.open("rb"):
        if not line.strip():
            continue
        try:
            d = loads(line)
        except Exception:
            continue
        recv = d.get("recorded_at")
        if not recv:
            continue
        try:
            dt = datetime.fromisoformat(str(recv).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=JST)
            t = dt.astimezone(JST).timestamp()
        except Exception:
            continue
        p = (d.get("payload") or {}).get("CurrentPrice")
        try:
            px = float(p)
        except (TypeError, ValueError):
            continue
        if px <= 0:
            continue
        ts_list.append(t)
        px_list.append(px)
    if not ts_list:
        return np.empty(0, dtype=np.float64), np.empty(0, dtype=np.float64)
    times = np.asarray(ts_list, dtype=np.float64)
    prices = np.asarray(px_list, dtype=np.float64)
    order = np.argsort(times, kind="mergesort")
    return times[order], prices[order]


def _worker_load(args: tuple[str, str]) -> tuple[str, str, np.ndarray, np.ndarray]:
    day, symbol = args
    t, p = _load_price_events(day, symbol)
    return day, symbol, t, p


def build_path_cache(
    rows: list[dict[str, Any]],
    horizon_s: float = 300.0,
    *,
    use_disk: bool = True,
    max_workers: int = 6,
) -> dict[str, Any]:
    """Build or load post-anchor event paths for all population rows."""
    assert FORBIDDEN_DAY not in ALL_DAYS
    OUT.mkdir(parents=True, exist_ok=True)
    cache_path = OUT / "_path_cache.pkl"
    meta_key = {
        "population_n": len(rows),
        "horizon_s": horizon_s,
        "days": list(ALL_DAYS),
        "cluster_head": rows[0]["cluster_id"] if rows else None,
        "cluster_tail": rows[-1]["cluster_id"] if rows else None,
    }
    if use_disk and cache_path.exists():
        with cache_path.open("rb") as f:
            cached = pickle.load(f)
        if cached.get("meta_key") == meta_key:
            print(f"  loaded path cache from disk paths_ok={cached['meta']['paths_ok']}", flush=True)
            return cached

    by_key: dict[tuple[str, str], list[int]] = {}
    for i, r in enumerate(rows):
        by_key.setdefault((r["date"], r["symbol"]), []).append(i)

    jobs = sorted(by_key.keys())
    tick_map: dict[tuple[str, str], tuple[np.ndarray, np.ndarray]] = {}
    print(f"  loading {len(jobs)} symbol-days with {max_workers} workers...", flush=True)
    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = [ex.submit(_worker_load, j) for j in jobs]
        for fut in as_completed(futs):
            day, sym, tarr, parr = fut.result()
            tick_map[(day, sym)] = (tarr, parr)
            done += 1
            if done % 50 == 0 or done == len(jobs):
                print(f"    ticks {done}/{len(jobs)}", flush=True)

    offsets: list[np.ndarray] = [np.empty(0, dtype=np.float64) for _ in rows]
    prices_out: list[np.ndarray] = [np.empty(0, dtype=np.float64) for _ in rows]
    times_out: list[np.ndarray] = [np.empty(0, dtype=np.float64) for _ in rows]
    coverage = []
    source_files = set()

    for (day, sym), idxs in by_key.items():
        tarr, parr = tick_map[(day, sym)]
        src = f"data/push_jsonl/{_dash(day)}/{sym}.T.jsonl"
        source_files.add(src)
        if tarr.size == 0:
            for i in idxs:
                coverage.append({"cluster_id": rows[i]["cluster_id"], "ok": False, "reason": "no_ticks"})
            continue
        for i in idxs:
            r = rows[i]
            g = float(r["grid_epoch"])
            sess = r["session"]
            sess_end = session_end_epoch(day, sess)
            lim_t = min(g + horizon_s, sess_end)
            i0 = int(np.searchsorted(tarr, g, side="right") - 1)
            if i0 < 0:
                coverage.append({
                    "cluster_id": r["cluster_id"], "ok": False,
                    "reason": "no_price_at_or_before_anchor",
                })
                continue
            i1 = int(np.searchsorted(tarr, lim_t, side="right") - 1)
            if i1 < i0:
                coverage.append({
                    "cluster_id": r["cluster_id"], "ok": False, "reason": "no_path_events",
                })
                continue
            sl_t = tarr[i0: i1 + 1]
            sl_p = parr[i0: i1 + 1]
            keep = sl_t <= sess_end + 1e-9
            sl_t = sl_t[keep]
            sl_p = sl_p[keep]
            if sl_t.size == 0:
                coverage.append({
                    "cluster_id": r["cluster_id"], "ok": False,
                    "reason": "empty_after_session_filter",
                })
                continue
            times_out[i] = sl_t
            prices_out[i] = sl_p
            offsets[i] = sl_t - g
            coverage.append({
                "cluster_id": r["cluster_id"],
                "ok": True,
                "n_events": int(sl_t.size),
                "max_offset_sec": float(offsets[i][-1]),
                "source_file": src,
            })

    ok_n = sum(1 for c in coverage if c.get("ok"))
    meta = {
        "population_n": len(rows),
        "paths_ok": ok_n,
        "paths_fail": len(rows) - ok_n,
        "horizon_s": horizon_s,
        "as_of_only": True,
        "future_backfill": False,
        "session_cross": False,
        "interpolation": False,
        "source_files_n": len(source_files),
        "days": list(ALL_DAYS),
        "forbidden_day_opened": False,
    }
    out = {
        "times": times_out,
        "prices": prices_out,
        "offsets": offsets,
        "coverage": coverage,
        "meta": meta,
        "meta_key": meta_key,
    }
    if use_disk:
        with cache_path.open("wb") as f:
            pickle.dump(out, f, protocol=pickle.HIGHEST_PROTOCOL)
        print(f"  wrote path cache {cache_path}", flush=True)
    return out


def _px_at_or_before(times: np.ndarray, prices: np.ndarray, tgt: float) -> Optional[float]:
    if times.size == 0:
        return None
    i = int(np.searchsorted(times, tgt, side="right") - 1)
    if i < 0:
        return None
    return float(prices[i])


def recompute_benchmark_from_path(
    rows: list[dict[str, Any]],
    cache: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    ledgers: dict[str, list[dict[str, Any]]] = {eid: [] for eid in BENCHMARK_EXITS}
    for i, r in enumerate(rows):
        g = float(r["grid_epoch"])
        px0 = float(r["CurrentPrice"]) if r.get("CurrentPrice") is not None else None
        sess_end = session_end_epoch(r["date"], r["session"])
        tarr = cache["times"][i]
        parr = cache["prices"][i]
        if px0 is None or tarr.size == 0:
            continue
        cid = r["cluster_id"]

        for h, eid in ((60, "BX_H60"), (180, "BX_H180"), (300, "BX_H300")):
            tgt = g + h
            if tgt > sess_end:
                continue
            i_tgt = int(np.searchsorted(tarr, tgt, side="right") - 1)
            # path[0] == original i0; i_tgt==0 means i1<=i0 on full series → null
            if i_tgt <= 0:
                continue
            px1 = float(parr[i_tgt])
            ret = px1 / px0 - 1.0
            ledgers[eid].append({
                "cluster_id": cid,
                "exit_time_offset_sec": h,
                "exit_price": px1,
                "exit_reason": f"horizon_{h}s",
                "hold_sec": float(h),
                "gross_return": ret,
                "gross_pnl_yen_100": px0 * ret * 100.0,
            })

        lim_t = min(g + 300.0, sess_end)
        t_up = t_dn = None
        px_up = px_dn = None
        for j in range(tarr.size):
            if tarr[j] > lim_t + 1e-12:
                break
            ret = float(parr[j] / px0 - 1.0)
            if t_up is None and ret >= 0.01:
                t_up = float(tarr[j] - g)
                px_up = float(parr[j])
            if t_dn is None and ret <= -0.01:
                t_dn = float(tarr[j] - g)
                px_dn = float(parr[j])
            if t_up is not None and t_dn is not None:
                break
        if t_up is not None and (t_dn is None or t_up <= t_dn):
            ledgers["BX_TOUCH_10_10"].append({
                "cluster_id": cid,
                "exit_time_offset_sec": t_up,
                "exit_price": px_up,
                "exit_reason": "touch_plus10",
                "hold_sec": t_up,
                "gross_return": 0.0010,
                "gross_pnl_yen_100": px0 * 0.0010 * 100.0,
            })
        elif t_dn is not None and (t_up is None or t_dn < t_up):
            ledgers["BX_TOUCH_10_10"].append({
                "cluster_id": cid,
                "exit_time_offset_sec": 300.0,  # X21 convention: time_to_minus not stored
                "exit_price": px_dn,
                "exit_reason": "touch_minus10",
                "hold_sec": 300.0,
                "gross_return": -0.0010,
                "gross_pnl_yen_100": px0 * -0.0010 * 100.0,
            })
        else:
            # Match X19: fallback only when forward_return_300s is evaluable
            # (horizon 300s does not cross session end and i1 > i0).
            if g + 300.0 > sess_end:
                continue
            i_lim = int(np.searchsorted(tarr, lim_t, side="right") - 1)
            if i_lim <= 0:
                continue
            px1 = float(parr[i_lim])
            ret = px1 / px0 - 1.0
            ledgers["BX_TOUCH_10_10"].append({
                "cluster_id": cid,
                "exit_time_offset_sec": 300.0,
                "exit_price": px1,
                "exit_reason": "horizon_300s_fallback",
                "hold_sec": 300.0,
                "gross_return": ret,
                "gross_pnl_yen_100": px0 * ret * 100.0,
            })
    return ledgers


def x19_label_ledgers(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    ledgers: dict[str, list[dict[str, Any]]] = {eid: [] for eid in BENCHMARK_EXITS}
    for r in rows:
        px0 = r.get("CurrentPrice")
        if px0 is None:
            continue
        px0 = float(px0)
        cid = r["cluster_id"]
        for h, eid, key in (
            (60, "BX_H60", "forward_return_60s"),
            (180, "BX_H180", "forward_return_180s"),
            (300, "BX_H300", "forward_return_300s"),
        ):
            ret = r.get(key)
            if ret is None:
                continue
            ret = float(ret)
            ledgers[eid].append({
                "cluster_id": cid,
                "exit_reason": f"horizon_{h}s",
                "hold_sec": float(h),
                "gross_return": ret,
                "gross_pnl_yen_100": px0 * ret * 100.0,
            })
        p10 = r.get("plus10_before_minus10")
        if p10 is not None and float(p10) == 1.0:
            hold = float(r["time_to_plus10"]) if r.get("time_to_plus10") is not None else 300.0
            ledgers["BX_TOUCH_10_10"].append({
                "cluster_id": cid,
                "exit_reason": "touch_plus10",
                "hold_sec": hold,
                "gross_return": 0.0010,
                "gross_pnl_yen_100": px0 * 0.0010 * 100.0,
            })
        elif p10 is not None and float(p10) == 0.0:
            ledgers["BX_TOUCH_10_10"].append({
                "cluster_id": cid,
                "exit_reason": "touch_minus10",
                "hold_sec": 300.0,
                "gross_return": -0.0010,
                "gross_pnl_yen_100": px0 * -0.0010 * 100.0,
            })
        elif r.get("forward_return_300s") is not None:
            ret = float(r["forward_return_300s"])
            ledgers["BX_TOUCH_10_10"].append({
                "cluster_id": cid,
                "exit_reason": "horizon_300s_fallback",
                "hold_sec": 300.0,
                "gross_return": ret,
                "gross_pnl_yen_100": px0 * ret * 100.0,
            })
    return ledgers


def ledger_sha(entries: list[dict[str, Any]]) -> str:
    parts = []
    for e in sorted(entries, key=lambda x: x["cluster_id"]):
        parts.append(
            f"{e['cluster_id']}|{e.get('exit_reason')}|{e.get('hold_sec')}|"
            f"{None if e.get('gross_return') is None else round(float(e['gross_return']), 12)}"
        )
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def compare_parity(
    label_ledgers: dict[str, list[dict[str, Any]]],
    path_ledgers: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    rows = []
    all_ok = True
    for eid in BENCHMARK_EXITS:
        a = label_ledgers[eid]
        b = path_ledgers[eid]
        sha_a = ledger_sha(a)
        sha_b = ledger_sha(b)
        ma = {e["cluster_id"]: e for e in a}
        mb = {e["cluster_id"]: e for e in b}
        only_a = sorted(set(ma) - set(mb))
        only_b = sorted(set(mb) - set(ma))
        mismatch = 0
        max_ret_diff = 0.0
        for cid in set(ma) & set(mb):
            ra = ma[cid].get("gross_return")
            rb = mb[cid].get("gross_return")
            if ra is None or rb is None:
                if ra != rb:
                    mismatch += 1
                continue
            diff = abs(float(ra) - float(rb))
            max_ret_diff = max(max_ret_diff, diff)
            # allow float noise
            if diff > 1e-9:
                mismatch += 1
            if ma[cid].get("exit_reason") != mb[cid].get("exit_reason"):
                mismatch += 1
        ok = (len(only_a) == 0 and len(only_b) == 0 and mismatch == 0)
        if not ok:
            all_ok = False
        rows.append({
            "exit_id": eid,
            "label_trades": len(a),
            "path_trades": len(b),
            "label_sha": sha_a,
            "path_sha": sha_b,
            "sha_match": sha_a == sha_b,
            "only_in_label_n": len(only_a),
            "only_in_path_n": len(only_b),
            "mismatch_n": mismatch,
            "max_ret_diff": max_ret_diff,
            "parity_ok": ok,
            "only_in_label_sample": only_a[:5],
            "only_in_path_sample": only_b[:5],
        })
    return {"all_ok": all_ok, "by_exit": rows}
