"""20260914 combined run tests. No sendorder. No 20260911 mining. No large grid."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from research.market_breadth_leadership_acquisition_v1 import RANKING_TYPES
from research.market_breadth_leadership_acquisition_v1.collector import next_cadence
from research.run_20260914_day2_futures_plus_first_live_breadth_v1 import (
    BREADTH_METRICS,
    ENTRY,
    EXIT,
    Q1_STATE_METRIC,
    Q2_SELECTOR,
    TRADING_DATE,
    VERDICT_AWAITING,
    VERDICT_C,
    VERDICT_TRANSPORT_FAIL,
)
from research.run_20260914_day2_futures_plus_first_live_breadth_v1.analyze import refuse_day1_mining, run_eod
from research.run_20260914_day2_futures_plus_first_live_breadth_v1.recon import evaluate_q1, evaluate_q2
from research.run_20260914_day2_futures_plus_first_live_breadth_v1.transport import evaluate_transport
from research.market_breadth_leadership_acquisition_v1.writer import refuse_historical_pseudosync

JST = ZoneInfo("Asia/Tokyo")
PKG = Path(__file__).resolve().parents[2] / "src" / "research" / "run_20260914_day2_futures_plus_first_live_breadth_v1"
DAY2_PKG = Path(__file__).resolve().parents[2] / "src" / "research" / "futures_x_stock_state_day2_confirmation_v1"


def test_pin_frozen():
    assert TRADING_DATE == "20260914"
    assert Q1_STATE_METRIC == "LEADERSHIP_DIRECTION"
    assert Q2_SELECTOR == "OBSERVED_TRADE_N_180S"
    assert RANKING_TYPES == (1, 2, 5, 6, 7, 14, 15)
    assert "SECTOR.RANK_PERSISTENCE" in BREADTH_METRICS
    assert ENTRY is False
    assert EXIT is False
    assert VERDICT_TRANSPORT_FAIL == "MARKET_BREADTH_LIVE_CAPTURE_NOT_PROVEN_V1"
    assert VERDICT_C == "BREADTH_NO_INCREMENTAL_INFORMATION_DAY1_V1"


def test_day1_mining_closed():
    with pytest.raises(ValueError, match="CLOSED"):
        refuse_day1_mining("20260911")
    with pytest.raises(ValueError, match="historical reconstruct forbidden"):
        refuse_historical_pseudosync("20260911", now=datetime(2026, 9, 14, 10, 0, tzinfo=JST))


def test_live_ranking_not_today_refused():
    with pytest.raises(ValueError, match="pseudo-sync"):
        refuse_historical_pseudosync("20260914", now=datetime(2026, 9, 12, 19, 33, tzinfo=JST))


def test_q1_incremental_within_both_down():
    rows = []
    for i in range(3):
        rows.append({"agreement_180": "BOTH_DOWN", "LEADERSHIP_DIRECTION": 1, "futures_sign": -1, "EW_MID_10m": 20.0, "EW_LONG_10m": 10.0})
    for i in range(3):
        rows.append({"agreement_180": "BOTH_DOWN", "LEADERSHIP_DIRECTION": -1, "futures_sign": -1, "EW_MID_10m": -10.0, "EW_LONG_10m": -8.0})
    for i in range(3):
        rows.append({"agreement_180": "BOTH_UP", "LEADERSHIP_DIRECTION": 1, "futures_sign": 1, "EW_MID_10m": 5.0, "EW_LONG_10m": 4.0})
    q1 = evaluate_q1(rows)
    assert q1["incremental"] is True
    assert q1["leadership_within_BOTH_DOWN"]["material"] is True


def test_q1_collinear_not_incremental():
    rows = []
    for _ in range(5):
        rows.append({"agreement_180": "BOTH_DOWN", "LEADERSHIP_DIRECTION": -1, "futures_sign": -1, "EW_MID_10m": -8.0, "EW_LONG_10m": -7.0})
    for _ in range(5):
        rows.append({"agreement_180": "BOTH_UP", "LEADERSHIP_DIRECTION": 1, "futures_sign": 1, "EW_MID_10m": 8.0, "EW_LONG_10m": 7.0})
    q1 = evaluate_q1(rows)
    assert q1["incremental"] is False


def test_q2_changes_top_bottom():
    rows = []
    for _ in range(3):
        rows.append({"agreement_180": "BOTH_DOWN", "LEADERSHIP_DIRECTION": 1, "TOP_BOTTOM_MID_10m": 40.0, "TOP_BOTTOM_LONG_10m": 20.0})
    for _ in range(3):
        rows.append({"agreement_180": "BOTH_DOWN", "LEADERSHIP_DIRECTION": -1, "TOP_BOTTOM_MID_10m": -10.0, "TOP_BOTTOM_LONG_10m": -12.0})
    q2 = evaluate_q2(rows)
    assert q2["changed"] is True


def test_transport_full_fixture(tmp_path: Path):
    day = "20260914"
    dest = tmp_path / "data" / "market_breadth_capture" / day
    dest.mkdir(parents=True)
    times = [
        "2026-09-14T09:05:00+09:00",
        "2026-09-14T10:15:00+09:00",
        "2026-09-14T11:22:00+09:00",
    ]
    for typ in RANKING_TYPES:
        path = dest / f"ranking_type{typ}.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            for ts in times:
                fh.write(
                    json.dumps(
                        {
                            "received_at": ts,
                            "requested_type": int(typ),
                            "ExchangeDivision": "T",
                            "http_status": 200,
                            "raw": {"Ranking": [{"Symbol": "A", "ChangePercentage": 1.0}]},
                        }
                    )
                    + "\n"
                )
    (dest / "breadth_collector.pid").write_text("999999999\n", encoding="utf-8")
    (dest / "breadth_collector_exit.json").write_text(
        json.dumps({"pid": 999999999, "clean_exit": True, "cycles": 3, "http_429_n": 0}) + "\n",
        encoding="utf-8",
    )
    got = evaluate_transport(native_root=tmp_path, trading_date=day)
    assert got["seven_types_present"] is True
    assert got["received_at_advancing"] is True
    assert got["schema_drift_n"] == 0
    assert got["coverage_0905_1125"] is True
    assert got["FULL"] is True


def test_saturday_eod_is_awaiting_not_a_research_claim():
    body = run_eod(now=datetime(2026, 9, 12, 19, 33, tzinfo=JST))
    d = dict(body.get("decision") or {})
    a = dict(body.get("answers") or {})
    assert a["14_secondary_rescue_used"] is False
    assert a["26_ENTRY_built"] is False
    assert a["27_EXIT_built"] is False
    assert a["30_submit_cancel_live"] == "0/0/0"
    assert d["VERDICT"] in (VERDICT_AWAITING, VERDICT_TRANSPORT_FAIL)
    assert body.get("live_start", {}).get("would_start_live") is False
    assert body.get("day1_mining_closed") is True


def test_no_large_grid_or_orders():
    for folder in (PKG, DAY2_PKG):
        for p in folder.glob("*.py"):
            txt = p.read_text(encoding="utf-8")
            assert "send" + "order(" not in txt
            assert "/" + "sendorder" not in txt
            assert "optuna" not in txt
            assert "for sel in SELECTORS" not in txt
    recon = (PKG / "recon.py").read_text(encoding="utf-8")
    assert "CONTEXT_SPECS" not in recon
    assert "for sel in SELECTORS" not in recon
    assert "for family in CONTEXT" not in recon
    assert next_cadence(60, saw_429=True) == 120
    assert next_cadence(120, saw_429=False) == 120
