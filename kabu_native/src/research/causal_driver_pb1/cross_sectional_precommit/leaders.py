"""Outcome-free leader eligibility and freeze. Development inputs only. No future returns."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np
import pyarrow.dataset as ds

from research.causal_driver_pb1 import C1_LAST, DEV_FIRST, DEV_LAST, FV_FIRST
from research.causal_driver_pb1.cross_sectional_precommit import (
    DEV_AM_COVERAGE_MIN,
    LEADER_N,
    SECTOR_MIN_CONSTITUENTS,
)
from research.causal_driver_pb1.cross_sectional_precommit.clock import (
    AM_END_MIN,
    AM_START_MIN,
    N_AM,
    N_CLOCK,
    N_DECISION_MIN_OK,
    CLOCK_MINS,
    am_index,
    hhmm_to_min,
    leader_fresh_at_t,
)
from research.causal_driver_pb1.identity.ids import sha256_obj
from research.causal_driver_pb1.phase2_precommit.stock_semantics import minute_parquet_path


def _hhmm_ok(tl: str) -> bool:
    try:
        t = hhmm_to_min(tl)
    except Exception:
        return False
    return AM_START_MIN <= t <= AM_END_MIN


def load_symbol_dev_inputs(symbol: str) -> dict[str, Any]:
    path = minute_parquet_path(symbol)
    if not path.is_file():
        return {
            "symbol": symbol,
            "parquet_ok": False,
            "trading_value_field": False,
            "daily_tv": {},
            "am_dates": set(),
            "listing_start": None,
            "first_am_date": None,
        }
    dataset = ds.dataset(str(path), format="parquet")
    names = list(dataset.schema.names)
    has_tv = "trading_value" in names
    cols = ["date", "time_label", "close"]
    if has_tv:
        cols.append("trading_value")
    filt = (ds.field("date") >= DEV_FIRST) & (ds.field("date") <= DEV_LAST)
    table = dataset.to_table(columns=cols, filter=filt)
    days = [str(x) for x in table.column("date").to_pylist()]
    times = [str(x) for x in table.column("time_label").to_pylist()]
    closes = table.column("close").to_pylist()
    tvs = table.column("trading_value").to_pylist() if has_tv else [None] * len(days)
    if any(d >= FV_FIRST for d in days):
        raise RuntimeError("fv_stock_row_materialized")
    daily_tv: dict[str, float] = defaultdict(float)
    am_dates: set[str] = set()
    tv_pos = 0
    for day, tl, px, va in zip(days, times, closes, tvs):
        if px is None or float(px) <= 0:
            continue
        if va is not None and float(va) > 0:
            daily_tv[day] += float(va)
            tv_pos += 1
        if _hhmm_ok(tl):
            am_dates.add(day)
    listing = min(am_dates) if am_dates else None
    return {
        "symbol": symbol,
        "parquet_ok": True,
        "schema_names": names,
        "trading_value_field": has_tv,
        "trading_value_positive_n": tv_pos,
        "daily_tv": dict(daily_tv),
        "am_dates": am_dates,
        "listing_start": listing,
        "first_am_date": listing,
        "dev_row_n": len(days),
    }


def load_leader_am_presence(*, symbol: str, date_lo: str, date_hi: str, dates: list[str]) -> dict[str, np.ndarray]:
    """Boolean AM grid by date. close used only as px>0 presence."""
    path = minute_parquet_path(symbol)
    index = {d: i for i, d in enumerate(dates)}
    n_d = len(dates)
    present = np.zeros((n_d, N_AM), dtype=np.bool_)
    if not path.is_file():
        return {"present": present}
    dataset = ds.dataset(str(path), format="parquet")
    filt = (
        (ds.field("date") >= date_lo)
        & (ds.field("date") <= date_hi)
        & (ds.field("time_label") >= "09:00")
        & (ds.field("time_label") <= "11:30")
    )
    table = dataset.to_table(columns=["date", "time_label", "close"], filter=filt)
    days = [str(x) for x in table.column("date").to_pylist()]
    times = [str(x) for x in table.column("time_label").to_pylist()]
    closes = table.column("close").to_pylist()
    if any(d >= FV_FIRST for d in days):
        raise RuntimeError("fv_stock_row_materialized")
    if date_hi >= FV_FIRST:
        raise RuntimeError("presence_would_open_fv")
    for day, tl, px in zip(days, times, closes):
        di = index.get(day)
        if di is None or px is None or float(px) <= 0:
            continue
        try:
            tmin = hhmm_to_min(tl)
        except Exception:
            continue
        gi = am_index(tmin)
        if 0 <= gi < N_AM:
            present[di, gi] = True
    return {"present": present}


def day_leader_fresh_n(present_row: np.ndarray) -> int:
    n = 0
    for t in CLOCK_MINS:
        if leader_fresh_at_t(present_row, int(t)):
            n += 1
    return n


def day_leader_ok(present_row: np.ndarray) -> bool:
    return (day_leader_fresh_n(present_row) / float(N_CLOCK)) >= 0.95


def freeze_leaders(*, sectors: dict[str, Any], tse_dev_days: list[str]) -> dict[str, Any]:
    rows = list(sectors.get("rows") or [])
    symbols = [r["symbol"] for r in rows]
    by_sec: dict[str, list[str]] = defaultdict(list)
    sec_meta = {s["sector_id"]: s for s in (sectors.get("eligible_sectors") or []) + (sectors.get("ineligible_sectors") or [])}
    for r in rows:
        by_sec[str(r["sector_id"])].append(r["symbol"])
    inputs: dict[str, dict[str, Any]] = {}
    for i, sym in enumerate(symbols, start=1):
        inputs[sym] = load_symbol_dev_inputs(sym)
        if i % 21 == 0:
            print(f"LEADER_INPUT {i}/{len(symbols)}", flush=True)
    n_tse = len(tse_dev_days)
    elig_rows = []
    for r in rows:
        sym = r["symbol"]
        sid = str(r["sector_id"])
        meta = sec_meta.get(sid) or {}
        rec = inputs[sym]
        am = rec.get("am_dates") or set()
        cov = (sum(1 for d in tse_dev_days if d in am) / float(n_tse)) if n_tse else 0.0
        listed = rec.get("listing_start") is not None and rec["listing_start"] <= DEV_FIRST
        tv_ok = bool(rec.get("trading_value_field")) and int(rec.get("trading_value_positive_n") or 0) > 0
        sector_ok = int(meta.get("constituent_n") or 0) >= SECTOR_MIN_CONSTITUENTS
        eligible = bool(rec.get("parquet_ok") and listed and cov >= DEV_AM_COVERAGE_MIN and tv_ok and sector_ok)
        scores = [float(rec["daily_tv"].get(d, 0.0)) for d in tse_dev_days]
        med = float(np.median(scores)) if scores else None
        elig_rows.append(
            {
                "symbol": sym,
                "sector_id": sid,
                "sector_name": r.get("sector_name"),
                "listing_start": rec.get("listing_start"),
                "dev_am_coverage": cov,
                "trading_value_field": tv_ok,
                "sector_constituent_n": int(meta.get("constituent_n") or 0),
                "symbol_liquidity_score": med,
                "eligible": eligible,
                "fail_listed": not listed,
                "fail_coverage": cov < DEV_AM_COVERAGE_MIN,
                "fail_tv": not tv_ok,
                "fail_sector_n": not sector_ok,
            }
        )
    eligible_syms = {r["symbol"] for r in elig_rows if r["eligible"]}
    sector_liq = []
    for sid, members in sorted(by_sec.items()):
        meta = sec_meta.get(sid) or {}
        if int(meta.get("constituent_n") or 0) < SECTOR_MIN_CONSTITUENTS:
            continue
        daily = []
        for d in tse_dev_days:
            s = 0.0
            for m in members:
                s += float((inputs[m].get("daily_tv") or {}).get(d, 0.0))
            daily.append(s)
        score = float(np.median(daily)) if daily else None
        sector_liq.append(
            {
                "sector_id": sid,
                "sector_name": meta.get("sector_name"),
                "constituent_n": int(meta.get("constituent_n") or 0),
                "sector_liquidity_score": score,
                "eligible_leader_n": sum(1 for m in members if m in eligible_syms),
            }
        )
    sector_liq.sort(key=lambda x: (-(x["sector_liquidity_score"] or -1.0), str(x["sector_id"])))
    ranked = [s for s in sector_liq if int(s.get("constituent_n") or 0) >= SECTOR_MIN_CONSTITUENTS]
    top = ranked[:LEADER_N]
    frozen = []
    used = set()
    missing_leader_sectors = []
    for s in top:
        members = by_sec[s["sector_id"]]
        cands = [r for r in elig_rows if r["symbol"] in members and r["eligible"]]
        cands.sort(key=lambda x: (-(x["symbol_liquidity_score"] or -1.0), str(x["symbol"])))
        if not cands:
            missing_leader_sectors.append(s["sector_id"])
            continue
        pick = cands[0]
        used.add(pick["symbol"])
        frozen.append(
            {
                "sector_id": s["sector_id"],
                "sector_name": s["sector_name"],
                "leader_symbol": pick["symbol"],
                "sector_liquidity_score": s["sector_liquidity_score"],
                "symbol_liquidity_score": pick["symbol_liquidity_score"],
                "listing_start": pick["listing_start"],
                "coverage": pick["dev_am_coverage"],
            }
        )
    frozen.sort(key=lambda x: str(x["sector_id"]))
    universe = [r["symbol"] for r in rows]
    targets = [s for s in universe if s not in used]
    disjoint = used.isdisjoint(set(targets)) and len(used) == len(frozen)
    leader_set_sha = sha256_obj(frozen)
    target_set_sha = sha256_obj(targets)
    return {
        "pass": len(frozen) == LEADER_N and disjoint and len(targets) == len(universe) - LEADER_N and not missing_leader_sectors,
        "missing_leader_sectors": missing_leader_sectors,
        "eligibility_rows": elig_rows,
        "sector_liquidity": sector_liq,
        "top_sectors": top[:LEADER_N],
        "frozen_leaders": frozen,
        "leader_symbols": [r["leader_symbol"] for r in frozen],
        "leader_set_sha256": leader_set_sha,
        "target_symbols": targets,
        "target_set_sha256": target_set_sha,
        "target_n": len(targets),
        "leader_n": len(frozen),
        "disjoint": disjoint,
        "LEADER_SET_FROZEN_BEFORE_OUTCOME": True,
        "future_return_used": False,
        "predictive_beta_used": False,
        "old_peer_names_used": False,
        "N_DECISION_MIN_OK": N_DECISION_MIN_OK,
    }
