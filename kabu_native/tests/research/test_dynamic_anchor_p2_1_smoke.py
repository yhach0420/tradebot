"""Synthetic pipeline smoke for P2-1 (no Capture)."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from research.dynamic_anchor_p2_0b.contract import t1_raw
from research.dynamic_anchor_p2_1.engine import build_day_features, run_prepared_day
import numpy as np

JST = ZoneInfo("Asia/Tokyo")
DAY = "20260803"


def _ticks(n_sym: int = 22) -> dict:
    am0 = datetime(2026, 8, 3, 9, 0, tzinfo=JST)
    out = {}
    for i in range(n_sym):
        sym = f"{1000+i}"
        rows = []
        prev_vol = 0.0
        t = am0
        while t.hour < 10 or (t.hour == 10 and t.minute <= 20):
            prev_vol += 10 + i
            ts = t.timestamp()
            rows.append({
                "t": ts, "price": 1000.0 + i + (t.minute * 0.01),
                "vol": prev_vol, "value": prev_vol * 1000.0, "vwap": 1000.0,
                "price_t": ts, "vol_t": ts, "value_t": ts, "vwap_t": ts, "vol_reset": False,
            })
            t += timedelta(seconds=1)
        out[sym] = rows
    return out


def test_synthetic_features_and_no_xs_leak():
    ticks = _ticks()
    grids = build_day_features(DAY, ticks)
    garr = np.asarray(sorted({r["t"] for rows in ticks.values() for r in rows}), dtype=float)
    last = float(garr[-1])
    out = run_prepared_day(
        day=DAY,
        capture_class="FULL",
        grids=grids,
        ticks_by=ticks,
        global_times=garr,
        last_capture_t=last,
    )
    assert out["cross_section_future_leak_count"] == 0
    assert out["TRUE_PERSISTENCE_REFIRE"] == 0
    assert out["duplicate_edge_fires"] == 0
    assert out["grid_evaluations"] > 0
    # some rows should be T1-evaluable with 22 symbols
    assert any(t1_raw(r) or r.get("volume_percentile_60s") is not None for rows in grids.values() for r in rows)
