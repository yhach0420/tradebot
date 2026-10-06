"""Frozen occupancy replay that keeps the trade ledger. Exit/entry imported unchanged."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.behavior_group_sequence_mechanism_v1 import OCCUPANCY, SESSION_FLAT
from research.behavior_group_sequence_mechanism_v1.replay import _exit_from_fwd, _select, replay


def replay_ledger(events: list[dict[str, Any]], spec: dict[str, Any]) -> dict[str, Any]:
    rows = _select(events, spec)
    traded_today: dict[str, set[str]] = defaultdict(set)
    occ: dict[str, list[dict[str, Any]]] = defaultdict(list)
    trades = []
    skipped = {"cutoff": 0, "occupancy": 0, "same_symbol": 0, "no_exit": 0}
    for e in rows:
        day = str(e["date"])
        hh = str(e["event_time"])
        occ[day] = [p for p in occ[day] if str(p["exit_hh"]) > hh]
        if e["symbol"] in traded_today[day]:
            skipped["same_symbol"] += 1
            continue
        if len(occ[day]) >= OCCUPANCY:
            skipped["occupancy"] += 1
            continue
        got = _exit_from_fwd(e, spec)
        if not got.get("ok"):
            skipped["no_exit"] += 1
            continue
        fwd = list(e.get("fwd_bars") or [])
        hold = int(got["hold_min"])
        exit_hh = str(fwd[min(hold + 1, len(fwd) - 1)][0]) if fwd else SESSION_FLAT
        trade = {
            "date": day,
            "symbol": e["symbol"],
            "sector": e.get("sector"),
            "event_time": hh,
            "block": e.get("block"),
            "preq_group": e.get("preq_group"),
            "sequence": spec["sequence"],
            **got,
            "exit_hh": exit_hh,
            "favor_first": e.get("favor_first"),
            "time_to_favorable": e.get("time_to_favorable"),
            "time_to_failure": e.get("time_to_failure"),
            "mfe_path_bps": e.get("mfe_bps"),
            "mae_path_bps": e.get("mae_bps"),
            "fwd_bars": fwd,
            "x0_entry_open": e.get("x0_entry_open"),
            "feature_bar": e.get("feature_bar"),
            "available_at": e.get("available_at"),
        }
        trades.append(trade)
        occ[day].append(trade)
        traded_today[day].add(str(e["symbol"]))
    econ = replay(events, spec)
    econ["trades"] = trades
    econ["skipped"] = skipped
    if trades and econ.get("trade_n"):
        x0_ledger = float(np.mean([float(t["x0_bps"]) for t in trades]))
        econ["ledger_mean_x0_matches_replay"] = abs(x0_ledger - float(econ["mean_x0_bps"])) < 1e-9
        econ["ledger_n_matches_replay"] = len(trades) == int(econ["trade_n"])
    return econ
