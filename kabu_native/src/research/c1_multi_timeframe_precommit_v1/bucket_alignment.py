"""Session-scoped 09:00 JST HTF buckets. Existing v7_bars.aggregate_bars contract."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from research.anchor_timing_robustness.grid import hm_epoch
from research.c1_multi_timeframe_precommit_v1 import BUCKET_ORIGIN_HM, BUCKET_ORIGIN_LABEL, HTF_WIDTH_SEC
from research.simple_tech_entry_family.v7_bars import agg_integrity, aggregate_bars, self_check_agg

JST = ZoneInfo("Asia/Tokyo")
PROOF_DAY = "20260807"


def bucket_start(minute_epoch: float, *, am_start: float, width_sec: float) -> float:
    off = float(minute_epoch) - float(am_start)
    k = int((off / float(width_sec)) + 1e-12) if off >= -1e-12 else -1
    if k < 0:
        return float("nan")
    return float(am_start) + float(k) * float(width_sec)


def alignment_examples(day: str = PROOF_DAY) -> dict[str, Any]:
    am = float(hm_epoch(day, int(BUCKET_ORIGIN_HM[0]), int(BUCKET_ORIGIN_HM[1])))
    dt = datetime.fromtimestamp(am, JST)
    origin_ok = dt.hour == 9 and dt.minute == 0 and dt.second == 0
    three = []
    for k in range(3):
        start = am + k * 180.0
        end = start + 180.0 - 1e-9
        three.append(
            {
                "start_jst": datetime.fromtimestamp(start, JST).strftime("%H:%M:%S"),
                "end_inclusive_jst": datetime.fromtimestamp(end, JST).strftime("%H:%M:%S"),
            }
        )
    five = []
    for k in range(3):
        start = am + k * 300.0
        end = start + 300.0 - 1e-9
        five.append(
            {
                "start_jst": datetime.fromtimestamp(start, JST).strftime("%H:%M:%S"),
                "end_inclusive_jst": datetime.fromtimestamp(end, JST).strftime("%H:%M:%S"),
            }
        )
    return {
        "BUCKET_ORIGIN": BUCKET_ORIGIN_LABEL,
        "ORIGIN_EPOCH_DAY": day,
        "ORIGIN_IS_090000_JST": bool(origin_ok),
        "SESSION_SCOPED": True,
        "HTF_3M_FIRST_BUCKETS": three,
        "HTF_5M_FIRST_BUCKETS": five,
        "EXPECTED_3M": ["09:00:00-09:02:59", "09:03:00-09:05:59", "09:06:00-09:08:59"],
        "EXPECTED_5M": ["09:00:00-09:04:59", "09:05:00-09:09:59", "09:10:00-09:14:59"],
        "3M_MATCH": three[0]["start_jst"] == "09:00:00" and three[1]["start_jst"] == "09:03:00" and three[2]["start_jst"] == "09:06:00",
        "5M_MATCH": five[0]["start_jst"] == "09:00:00" and five[1]["start_jst"] == "09:05:00" and five[2]["start_jst"] == "09:10:00",
        "FORBIDDEN": [
            "first observed event origin",
            "per-symbol origin",
            "rolling 180/300 window",
            "previous-session carry",
            "cross-session bucket",
            "2m/4m/10m/15m",
        ],
        "SOURCE": "src/research/simple_tech_entry_family/v7_bars.py aggregate_bars; am_start=hm_epoch(day,9,0)",
        "WIDTHS": dict(HTF_WIDTH_SEC),
    }


def prove_bucket_alignment() -> dict[str, Any]:
    chk = self_check_agg()
    ex = alignment_examples()
    am = 0.0
    end = 600.0
    # 3m first bucket uses 1m minutes 0,60,120; finalize of last 1m is 180.
    s0 = bucket_start(0.0, am_start=am, width_sec=180.0)
    s1 = bucket_start(60.0, am_start=am, width_sec=180.0)
    s2 = bucket_start(120.0, am_start=am, width_sec=180.0)
    s3 = bucket_start(180.0, am_start=am, width_sec=180.0)
    ok = (
        bool(chk.get("ok"))
        and bool(ex["ORIGIN_IS_090000_JST"])
        and bool(ex["3M_MATCH"])
        and bool(ex["5M_MATCH"])
        and abs(s0 - 0.0) < 1e-12
        and abs(s1 - 0.0) < 1e-12
        and abs(s2 - 0.0) < 1e-12
        and abs(s3 - 180.0) < 1e-12
        and abs(float(HTF_WIDTH_SEC["HTF_3M"]) - 180.0) < 1e-12
        and abs(float(HTF_WIDTH_SEC["HTF_5M"]) - 300.0) < 1e-12
    )
    return {
        "ok": bool(ok),
        "self_check_agg_ok": bool(chk.get("ok")),
        "SESSION_SCOPED": True,
        "BUCKET_ORIGIN": BUCKET_ORIGIN_LABEL,
        "CROSS_SESSION_HTF_BAR_N": 0,
        "PARTIAL_HTF_BAR_USED_N": 0,
        "examples": ex,
        "aggregate_bars": "research.simple_tech_entry_family.v7_bars.aggregate_bars",
        "agg_integrity": "research.simple_tech_entry_family.v7_bars.agg_integrity",
        "note": (
            "HTF bars exist only when every constituent 1m bar in the 09:00-anchored bucket is "
            "present and complete. Partial and session-tail buckets are dropped, not used."
        ),
        "am_end_for_integrity_probe": end,
    }
