"""Phase687W57 — summary rebuild consistency."""

from __future__ import annotations

import json
from pathlib import Path

from small_paper.pullback_volume_forward_logger import (
    PullbackVolumeForwardState,
    VOL_PERSISTENCE_HIGH_THR,
    append_jsonl_day,
    build_entry_row,
    load_day_rows,
    rebuild_cumulative,
    write_day_summary,
)


def test_cumulative_rebuild_matches_day_files(tmp_path: Path):
    st = PullbackVolumeForwardState(enabled=True, trading_date="20260718", out_dir=tmp_path)
    for i, vol in enumerate([VOL_PERSISTENCE_HIGH_THR, 0.05, 0.20]):
        build_entry_row(
            st,
            {
                "symbol": f"100{i}.T",
                "entry_time": f"2026-07-18T09:1{i}:00+09:00",
                "entry_rise_5min_pct": -0.2,
                "entry_vwap_dev_pct": -0.1,
                "universe_slot": "dynamic",
                "entry_price": 1000,
                "vol_persistence_300s": vol,
                "imbalance_chg_60s": 0.1 if i == 0 else -0.1,
            },
            official_entry=True,
            official_reject=False,
        )
        # mark labels complete for aggregation
        key = list(st.rows.keys())[-1]
        st.rows[key]["healthy_pullback_flag"] = vol >= VOL_PERSISTENCE_HIGH_THR
        st.rows[key]["collapse_flag"] = vol <= 0.05
        st.rows[key]["runtime_pnl_pct"] = 0.2 if vol >= VOL_PERSISTENCE_HIGH_THR else -0.3
        st.rows[key]["label_complete"] = True

    append_jsonl_day(st, day="20260718")
    rows = load_day_rows(tmp_path, "20260718")
    assert len(rows) == 3
    day_sum = write_day_summary(tmp_path, "20260718", rows)
    assert day_sum["total_pullback_hits"] == 3
    cum1 = rebuild_cumulative(tmp_path)
    cum2 = rebuild_cumulative(tmp_path)
    assert cum1["total_pullback_hits"] == cum2["total_pullback_hits"] == 3
    assert cum1["volume_high_n"] == cum2["volume_high_n"]
    # json files exist
    assert (tmp_path / "pullback_volume_forward_summary_20260718.json").is_file()
    assert (tmp_path / "pullback_volume_forward_cumulative.json").is_file()
    loaded = json.loads((tmp_path / "pullback_volume_forward_cumulative.json").read_text(encoding="utf-8"))
    assert loaded["total_pullback_hits"] == 3
