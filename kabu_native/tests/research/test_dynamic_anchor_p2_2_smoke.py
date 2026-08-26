"""P2-2 smoke: incremental T1 matches P2-1 batch; CLOCK_GRID disabled; binding helpers."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np

from research.dynamic_anchor_p2_1.engine import build_day_features, run_prepared_day
from research.dynamic_anchor_p2_1.publish import CONFIRM_SHA_KEYS, TRIGGER_SHA_KEYS, ledger_sha
from research.dynamic_anchor_p2_2.binding import verify_entry_binding, verify_p2_1_shas
from research.dynamic_anchor_p2_2.engine import DynamicEngine
from research.dynamic_anchor_p2_2.publish import _headline
from research.dynamic_anchor_p2_2.t1_clock import IncrementalT1

JST = ZoneInfo("Asia/Tokyo")
DAY = "20260803"


def _ticks(n_sym: int = 22):
    am0 = datetime(2026, 8, 3, 9, 0, tzinfo=JST)
    out = {}
    for i in range(n_sym):
        sym = f"{1000 + i}"
        rows = []
        prev_vol = 0.0
        t = am0
        while t.hour < 10 or (t.hour == 10 and t.minute <= 20):
            prev_vol += 10 + i
            ts = t.timestamp()
            rows.append({
                "t": ts,
                "price": 1000.0 + i + (t.minute * 0.01),
                "vol": prev_vol,
                "value": prev_vol * 1000.0,
                "vwap": 1000.0,
                "price_t": ts,
                "vol_t": ts,
                "value_t": ts,
                "vwap_t": ts,
                "vol_reset": False,
            })
            t += timedelta(seconds=1)
        out[sym] = rows
    return out


def test_incremental_t1_matches_batch():
    ticks = _ticks()
    grids = build_day_features(DAY, ticks)
    garr = np.asarray(sorted({r["t"] for rows in ticks.values() for r in rows}), dtype=float)
    last = float(garr[-1])
    batch = run_prepared_day(
        day=DAY,
        capture_class="FULL",
        grids=grids,
        ticks_by=ticks,
        global_times=garr,
        last_capture_t=last,
    )
    clock = IncrementalT1(day=DAY, universe=list(ticks.keys()), capture_class="FULL")
    events = []
    for s, rows in ticks.items():
        for r in rows:
            events.append((r["t"], s, r))
    events.sort(key=lambda x: (x[0], x[1]))
    for et, s, r in events:
        iso = datetime.fromtimestamp(et, JST).isoformat()
        clock.note_watermark(et)
        clock.append_tick(symbol=s, et=et, pay={
            "CurrentPrice": r["price"],
            "TradingVolume": r["vol"],
            "TradingValue": r["value"],
            "VWAP": r["vwap"],
            "CurrentPriceTime": iso,
            "TradingVolumeTime": iso,
            "TradingValueTime": iso,
        })
        clock.evaluate_grids_until(et)
        clock.due_confirmed(et)
    clock.finalize(last)
    t1 = ledger_sha(batch["triggers"], TRIGGER_SHA_KEYS)
    t2 = ledger_sha(clock.triggers, TRIGGER_SHA_KEYS)
    c1 = ledger_sha(batch["confirms"], CONFIRM_SHA_KEYS)
    c2 = ledger_sha(clock.confirms, CONFIRM_SHA_KEYS)
    assert t1 == t2, "incremental T1 trigger ledger diverged from P2-1 batch"
    assert c1 == c2, "incremental C1 confirm ledger diverged from P2-1 batch"
    assert clock.persist_refire == 0


def test_maybe_fire_anchor_disabled():
    inst = object.__new__(DynamicEngine)
    inst.clock_grid_blocked = 0
    out = DynamicEngine.maybe_fire_anchor(inst, now_t=1.0)
    assert out == []
    assert inst.clock_grid_blocked == 1


def test_entry_binding_imports():
    b = verify_entry_binding()
    assert b["CURRENT_ENTRY_BINDING"] == "PASS"
    assert b["path"]["rank_pass_gate"] is None


def test_sha_helper_mismatch():
    got = verify_p2_1_shas({
        "TRIGGER_LEDGER_SHA_RUN1": "x",
        "TRIGGER_LEDGER_SHA_RUN2": "x",
        "CONFIRM_LEDGER_SHA_RUN1": "y",
        "CONFIRM_LEDGER_SHA_RUN2": "y",
    })
    assert got["pass"] is False


def test_headline_classes():
    fix = {"pnl": 100.0, "PF": 2.0, "maxDD": -50.0}
    assert _headline({"pnl": 200.0, "PF": 3.0, "maxDD": -40.0}, fix) == "BETTER"
    assert _headline({"pnl": 50.0, "PF": 1.0, "maxDD": -80.0}, fix) == "WORSE"
    assert _headline({"pnl": 200.0, "PF": 1.0, "maxDD": -40.0}, fix) == "MIXED"
