"""Inventory + worker. Uses P1 classify/universe helpers. Does not read P1 ledgers."""
from __future__ import annotations

import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

NATIVE = Path(__file__).resolve().parents[3]
SCRIPTS = NATIVE / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from _p1_inventory import classify, resolve_universe  # noqa: E402
from research.anchor_vs_event_driven.run_comparison import find_capture_dir  # noqa: E402
from research.dynamic_anchor_p2_1 import MISSING_DAYS, PERIOD_END, PERIOD_START
from research.dynamic_anchor_p2_1.capture_ticks import load_capture_symbol_ticks
from research.dynamic_anchor_p2_1.engine import build_day_features, run_prepared_day


def period_days() -> list[str]:
    a = datetime.strptime(PERIOD_START, "%Y%m%d")
    b = datetime.strptime(PERIOD_END, "%Y%m%d")
    out = []
    d = a
    while d <= b:
        out.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)
    return out


def build_inventory() -> list[dict[str, Any]]:
    rows = []
    for day in period_days():
        cap = find_capture_dir(day)
        uni = resolve_universe(day, cap)
        seq = {"line_count": 0}
        if cap is not None:
            from research.anchor_vs_event_driven.run_comparison import _load_json
            summary = _load_json(cap / "capture_summary.json") or _load_json(cap.parent / "capture_summary.json") or {}
            n_ev = int(summary.get("total_events") or summary.get("writer", {}).get("written") or 0)
            if n_ev <= 0:
                n_ev = sum(1 for _ in cap.glob("push_part_*.jsonl") if _.stat().st_size > 0)
            seq = {"line_count": n_ev}
        klass = classify(day, cap, uni, seq)
        elig = bool(
            uni["resolved"]
            and klass.get("usable")
            and klass.get("capture_class") in {"FULL", "PARTIAL", "DEGRADED"}
        )
        if day in MISSING_DAYS:
            elig = False
        rows.append({
            "date": day,
            "capture_path": str(cap) if cap else "",
            "capture_class": klass.get("capture_class"),
            "jpx_trading_day": klass.get("jpx_trading_day"),
            "usable": klass.get("usable"),
            "full": klass.get("full"),
            "first_event": klass.get("first_event"),
            "last_event": klass.get("last_event"),
            "am_coverage": klass.get("am_coverage"),
            "pm_coverage": klass.get("pm_coverage"),
            "universe_resolved": uni["resolved"],
            "universe_source": uni.get("source"),
            "universe_n": uni.get("universe_n"),
            "universe_symbols": uni.get("symbols") or [],
            "replay_eligible": elig,
            "exclusion_reason": klass.get("exclusion_reason"),
        })
    return rows


def process_day(payload: dict[str, Any]) -> dict[str, Any]:
    day = str(payload["date"])
    cap = Path(payload["capture_path"])
    universe = [str(s) for s in payload["universe"]]
    cap_class = str(payload["capture_class"])
    ticks, global_t, last_t, n_rec = load_capture_symbol_ticks(cap, set(universe))
    grids = build_day_features(day, ticks)
    garr = np.asarray(global_t, dtype=float)
    out = run_prepared_day(
        day=day,
        capture_class=cap_class,
        grids=grids,
        ticks_by=ticks,
        global_times=garr,
        last_capture_t=last_t,
    )
    out["events_scanned"] = n_rec
    out["universe_n"] = len(universe)
    out["universe_source"] = payload.get("universe_source")
    out["anchors_per_symbol"] = dict(Counter(t["symbol"] for t in (out.get("triggers") or [])))
    out["ok"] = True
    return out
