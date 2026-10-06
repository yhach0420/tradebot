"""Walk every native 1-minute bar on Discovery dates. No 5-minute grid. Frozen Validation never loaded."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np
import pandas as pd

from research.cause_first_mechanism_discovery_v1.clock import hhmm_add
from research.cause_first_mechanism_discovery_v1.panel import load_minutes
from research.one_minute_native_playbook_discovery_v1 import ENTRY_CUTOFF, MIN_SECTOR_N
from research.one_minute_native_playbook_discovery_v1.states import (
    entry_ok,
    families_at,
    features_at,
    prep_symbol,
    to_min,
)


def _rank_desc(vals: list[tuple[str, float]]) -> dict[str, int]:
    ordered = sorted(vals, key=lambda kv: (-kv[1], kv[0]))
    return {sym: i + 1 for i, (sym, _) in enumerate(ordered)}


def process_day(
    *,
    date: str,
    g: pd.DataFrame,
    sector_of: dict[str, str],
    date_to_block: dict[str, str],
    prev_close: dict[str, float],
) -> dict[str, Any]:
    per: dict[str, dict[str, Any]] = {}
    for sym, sg in g.groupby("symbol", sort=False):
        rec = prep_symbol(sg)
        if rec["n"] < 20:
            continue
        rec["symbol"] = str(sym)
        rec["sector"] = sector_of.get(str(sym)) or ""
        per[str(sym)] = rec
    if len(per) < 20:
        return {"events": [], "opening": [], "lead_min": [], "last_close": {}}

    times = sorted({t for rec in per.values() for t in rec["t"]}, key=lambda x: to_min(x) or 0)
    last_imp_up: dict[str, int] = {}
    last_leader_imp: dict[str, int] = {}
    prev_flag: dict[str, dict[str, bool]] = {}
    events: list[dict[str, Any]] = []
    opening: list[dict[str, Any]] = []
    lead_acc: dict[str, dict[str, Any]] = {}
    block = date_to_block.get(date) or ""

    present_at: dict[str, list[str]] = defaultdict(list)
    for rec in per.values():
        for t in rec["t"]:
            present_at[t].append(rec["symbol"])

    for t in times:
        tm = to_min(t)
        if tm is None:
            continue
        rows = []
        for sym in present_at.get(t) or []:
            rec = per[sym]
            i = rec["idx"].get(t)
            if i is None:
                continue
            feat = features_at(rec, i)
            feat["symbol"] = sym
            feat["sector"] = rec["sector"]
            feat["sess_ret"] = rec["sess"][i]
            rows.append(feat)
        if len(rows) < 20:
            continue
        by_sec: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in rows:
            if r.get("sector"):
                by_sec[str(r["sector"])].append(r)
        ranks: dict[str, int] = {}
        sec_n: dict[str, int] = {}
        leaders: dict[str, str] = {}
        for sec, xs in by_sec.items():
            finite = [(str(x["symbol"]), float(x["sess_ret"])) for x in xs if np.isfinite(x.get("sess_ret"))]
            sec_n[sec] = len(finite)
            if len(finite) < MIN_SECTOR_N:
                continue
            rk = _rank_desc(finite)
            ranks.update(rk)
            leaders[sec] = finite and sorted(finite, key=lambda kv: (-kv[1], kv[0]))[0][0]
        for r in rows:
            rec = per[r["symbol"]]
            i = rec["idx"][t]
            sec = r["sector"]
            nsec = sec_n.get(sec) or 0
            rk = ranks.get(r["symbol"])
            lagging = bool(rk is not None and nsec >= MIN_SECTOR_N and rk > int(np.ceil(0.70 * nsec)))
            leading = bool(rk == 1 and nsec >= MIN_SECTOR_N)
            if nsec >= MIN_SECTOR_N and rk is not None:
                acc = lead_acc.setdefault(
                    r["symbol"],
                    {"date": date, "block": block, "symbol": r["symbol"], "sector": sec, "present_n": 0, "leading_n": 0, "lagging_n": 0},
                )
                acc["present_n"] += 1
                if leading:
                    acc["leading_n"] += 1
                if lagging:
                    acc["lagging_n"] += 1
            if leading and r.get("impulse_up"):
                last_leader_imp[sec] = tm
            recent_imp = bool(r["symbol"] in last_imp_up and tm - last_imp_up[r["symbol"]] <= 10)
            leader_recent = bool(sec in last_leader_imp and 0 < tm - last_leader_imp[sec] <= 5)
            prev = prev_flag.get(r["symbol"]) or {}
            r_on = dict(r)
            r_on["vol_expand"] = bool(r.get("vol_expand") and not prev.get("vol_expand"))
            r_on["va_expand"] = bool(r.get("va_expand") and not prev.get("va_expand"))
            r_on["range_expand"] = bool(r.get("range_expand") and not prev.get("range_expand"))
            r_on["compression"] = bool(r.get("compression") and not prev.get("compression"))
            r_on["pullback"] = bool(r.get("pullback") and not prev.get("pullback"))
            r_on["breakout20"] = bool(r.get("breakout20") and not prev.get("breakout20"))
            r_on["breakdown20"] = bool(r.get("breakdown20") and not prev.get("breakdown20"))
            fams = families_at(r_on, recent_impulse_up=recent_imp, lagging=lagging, leader_impulse_recent=leader_recent)
            prev_flag[r["symbol"]] = {
                "vol_expand": bool(r.get("vol_expand")),
                "va_expand": bool(r.get("va_expand")),
                "range_expand": bool(r.get("range_expand")),
                "compression": bool(r.get("compression")),
                "pullback": bool(r.get("pullback")),
                "breakout20": bool(r.get("breakout20")),
                "breakdown20": bool(r.get("breakdown20")),
            }
            if r.get("impulse_up"):
                last_imp_up[r["symbol"]] = tm
            if not fams:
                continue
            entry = r.get("entry_bar")
            if not entry_ok(entry) or str(entry) > ENTRY_CUTOFF:
                continue
            for fam in fams:
                events.append(
                    {
                        "date": date,
                        "block": block,
                        "symbol": r["symbol"],
                        "sector": sec,
                        "feature_bar": t,
                        "available_at": r.get("available_at"),
                        "event_time": entry,
                        "event_family": fam,
                        "layer": "STOCK",
                        "leading": leading,
                        "lagging": lagging,
                        "sector_rank": rk,
                        "sector_n": nsec,
                        "hi20": r.get("hi20"),
                        "lo20": r.get("lo20"),
                        "ret_1m": r.get("ret_1m"),
                        "ret_5m": r.get("ret_5m"),
                        "ret_15m": r.get("ret_15m"),
                        "dist_vwap": r.get("dist_vwap"),
                        "vol_rel20": r.get("vol_rel20"),
                        "rng_rel20": r.get("rng_rel20"),
                        "sess_ret": r.get("sess_ret"),
                        "rec": rec,
                    }
                )

        if t == "09:00":
            for r in rows:
                rec = per[r["symbol"]]
                pc = prev_close.get(r["symbol"])
                o0 = rec["o"][0] if rec["t"][0] == "09:00" else float("nan")
                gap = float(o0 / pc - 1.0) if pc and np.isfinite(pc) and pc != 0 and np.isfinite(o0) else float("nan")
                opening.append(
                    {
                        "date": date,
                        "block": block,
                        "symbol": r["symbol"],
                        "sector": r["sector"],
                        "gap": gap,
                        "gap_up": bool(np.isfinite(gap) and gap > 0),
                        "gap_down": bool(np.isfinite(gap) and gap < 0),
                    }
                )

    # complete opening hold using 09:14 if present
    open_map = {(x["symbol"]): x for x in opening}
    for rec in per.values():
        row = open_map.get(rec["symbol"])
        if not row:
            continue
        i14 = rec["idx"].get("09:14")
        i15 = rec["idx"].get("09:15")
        pc = prev_close.get(rec["symbol"])
        hold = None
        if i14 is not None and pc and np.isfinite(pc) and pc != 0 and np.isfinite(row.get("gap")):
            cl = rec["c"][i14]
            if np.isfinite(cl):
                if row["gap"] > 0:
                    hold = bool(cl >= pc)
                elif row["gap"] < 0:
                    hold = bool(cl <= pc)
        row["hold_0914"] = hold
        row["has_0915"] = i15 is not None
        if hold is True and bool(row.get("gap_up")) and i15 is not None and entry_ok("09:15"):
            events.append(
                {
                    "date": date,
                    "block": block,
                    "symbol": rec["symbol"],
                    "sector": rec["sector"],
                    "feature_bar": "09:14",
                    "available_at": "09:15",
                    "event_time": "09:15",
                    "event_family": "OPENING_GAP_HOLD",
                    "layer": "STOCK",
                    "gap": row.get("gap"),
                    "rec": rec,
                }
            )

    last_close = {}
    for rec in per.values():
        if rec["n"] and np.isfinite(rec["c"][-1]) and rec["c"][-1] > 0:
            last_close[rec["symbol"]] = float(rec["c"][-1])
    return {"events": events, "opening": list(open_map.values()), "lead_min": list(lead_acc.values()), "last_close": last_close}


def build_native_events(
    *,
    symbols: list[str],
    sector_of: dict[str, str],
    allowed: set[str],
    forbidden: set[str],
    date_to_block: dict[str, str],
) -> dict[str, Any]:
    print(f"LOAD_MINUTES symbols={len(symbols)} discovery_days={len(allowed)}", flush=True)
    minutes = load_minutes(symbols=symbols, allowed_dates=allowed, forbidden_dates=forbidden)
    if minutes.empty:
        return {"ok": False, "events": [], "opening": [], "lead_min": []}
    if minutes["date"].isin(list(forbidden)).any():
        raise RuntimeError("forbidden_partition_loaded")
    minutes["date"] = minutes["date"].astype(str)
    minutes["time_label"] = minutes["time_label"].astype(str).str.slice(0, 5)
    minutes = minutes.sort_values(["date", "symbol", "time_label"])
    events: list[dict[str, Any]] = []
    opening: list[dict[str, Any]] = []
    lead_min: list[dict[str, Any]] = []
    prev_close: dict[str, float] = {}
    n_days = int(minutes["date"].nunique())
    for i, (day, g) in enumerate(minutes.groupby("date", sort=True), start=1):
        pack = process_day(date=str(day), g=g, sector_of=sector_of, date_to_block=date_to_block, prev_close=prev_close)
        events.extend(pack["events"])
        opening.extend(pack["opening"])
        lead_min.extend(pack["lead_min"])
        prev_close.update(pack["last_close"])
        if i % 20 == 0 or i == n_days:
            print(f"DAY {i}/{n_days} events={len(events)}", flush=True)
    return {
        "ok": True,
        "day_n": n_days,
        "event_n": len(events),
        "events": events,
        "opening": opening,
        "lead_min": lead_min,
        "used_5m_grid": False,
        "bar_start": True,
    }
