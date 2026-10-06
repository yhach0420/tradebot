"""Recapture success mechanism discovery. No Holdout. No V5 rescue."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from research.e1_x34a_execution_policy.executable_board import SIGN_GENERAL
from research.prior_close_recapture_sustained_mechanism_v1 import (
    CASE_FOUND,
    CASE_NONE,
    FEATURE_IDS,
    LABEL_FAILED,
    LABEL_SUSTAINED,
    NEXT_NONE,
    REQUIRED_PARENT_VERDICT,
)
from research.prior_close_recapture_sustained_mechanism_v1.analyze import decide
from research.prior_close_recapture_sustained_mechanism_v1.features import freeze_features
from research.prior_close_recapture_sustained_mechanism_v1.fsm import FeatureFsm
from research.prior_close_recapture_sustained_mechanism_v1.harvest import _label
from research.prior_close_recapture_sustained_mechanism_v1.publish import SHEET_ORDER
from research.prior_close_recapture_sustained_mechanism_v1.spec import pin_parent
from research.simple_full_strategy_discovery_v1 import BURNED_HOLDOUT_DAYS, STRESS_DAYS

JST = ZoneInfo("Asia/Tokyo")


def ts(h: int, m: int, s: int = 0) -> float:
    return datetime(2026, 7, 22, h, m, s, tzinfo=JST).timestamp()


def iso(h: int, m: int, s: int = 0) -> str:
    return datetime(2026, 7, 22, h, m, s, tzinfo=JST).isoformat()


def pay(*, px: float, vol: float, prev: float = 1010.0, calc: float | None = 1012.0, val: float | None = 1e7) -> dict:
    return {
        "OpeningPrice": 1000.0,
        "OpeningPriceTime": iso(9, 0, 0),
        "CurrentPrice": px,
        "CurrentPriceStatus": 1,
        "TradingVolume": vol,
        "TradingValue": val,
        "PreviousClose": prev,
        "CalcPrice": calc,
        "UnderBuyQty": 0,
        "OverSellQty": 0,
        "BidSign": SIGN_GENERAL,
        "AskSign": SIGN_GENERAL,
        "Buy1": {"Price": px - 1, "Qty": 200, "Sign": SIGN_GENERAL, "Time": iso(9, 1, 0)},
        "Sell1": {"Price": px + 1, "Qty": 200, "Sign": SIGN_GENERAL, "Time": iso(9, 1, 0)},
        "received_at": iso(9, 1, 0),
    }


def test_parent_pin():
    parent = pin_parent()
    assert parent["ok"] is True
    assert parent["VERDICT"] == REQUIRED_PARENT_VERDICT
    assert parent["V5_STATUS"] == "CLOSED"
    assert parent["V5_RESCUE"] is False
    assert parent["KIND"] == "MECHANISM_DISCOVERY_ONLY"


def test_labels_not_mixed():
    a, u = _label(would_fill=True, fire_t=None, exit_t=1.0, exit_reason="SESSION_CLOSE")
    assert a == LABEL_SUSTAINED
    assert u is False
    b, u2 = _label(would_fill=True, fire_t=2.0, exit_t=3.0, exit_reason="Z_PRIOR_CLOSE_LOSS")
    assert b == LABEL_FAILED
    c, u3 = _label(would_fill=True, fire_t=None, exit_t=None, exit_reason="EXIT_MISS")
    assert c == LABEL_SUSTAINED
    assert u3 is True
    none, _ = _label(would_fill=False, fire_t=None, exit_t=None, exit_reason=None)
    assert none is None


def test_features_stop_at_recapture():
    fsm = FeatureFsm(symbol="1000", date="20260722", flatten_t=ts(11, 29), am_start=ts(9, 0))
    events = [
        (ts(9, 1, 0), pay(px=1000, vol=50, prev=1010)),
        (ts(9, 1, 5), pay(px=990, vol=80, prev=1010)),
        (ts(9, 1, 10), pay(px=1015, vol=120, prev=1010)),
        (ts(9, 1, 20), pay(px=980, vol=200, prev=1010)),
    ]
    for t, p in events:
        p = dict(p)
        p["received_at"] = datetime.fromtimestamp(t, tz=JST).isoformat()
        fsm.process(ingress_t=t, payload=p, rec={"payload": p})
    assert len(fsm.frozen) == 1
    row = fsm.frozen[0]
    assert row["BELOW_OTU_N"] == 2
    assert float(row["MAX_DEPTH_BELOW_PCLOSE_BPS"]) > 0
    assert abs(float(row["MAX_DEPTH_BELOW_PCLOSE_BPS"]) - (1010 - 990) / 1010 * 10000) < 1e-6
    assert float(row["RECAPTURE_OVERSHOOT_BPS"]) > 0
    assert row["BELOW_TRADING_VOLUME_DELTA"] == 70
    assert row["PRIOR_RECAPTURE_N"] == 0
    assert all(k in row for k in FEATURE_IDS)
    fire = fsm.first_exit_fire(fill_t=ts(9, 1, 10), anchor=1010.0)
    assert fire is not None


def test_freeze_no_future_fields():
    feat = freeze_features(
        sig={"anchor": 1010.0, "px": 1015.0, "below_t": ts(9, 1, 0), "t0": ts(9, 1, 10)},
        acc={"vol0": 50.0, "val0": 1e7, "min_px": 990.0, "prices": {1000.0, 990.0}, "otu_n": 2},
        payload=pay(px=1015, vol=120, prev=1010),
        rec=None,
        ingress_t=ts(9, 1, 10),
        am_start=ts(9, 0),
        prior_n=0,
        time_since=None,
    )
    assert feat["availability_clock"] == "INGRESS"
    assert feat["CURRENT_PRICE_TIME_AS_FRESH"] is False
    assert len(FEATURE_IDS) == 20


def test_decide_none_without_stable():
    d = decide([], tree={"ok": False}, lobo_pack={"direction_flip_n": 0, "stable_across_folds": []})
    assert d["VERDICT"] == CASE_NONE
    assert d["NEXT"] == NEXT_NONE
    assert d["PREVIOUS_CLOSE_LINE_EXHAUSTED"] is True
    assert CASE_FOUND != CASE_NONE


def test_sheet_order():
    assert SHEET_ORDER[0] == "Summary"
    assert SHEET_ORDER[-1] == "Next_Strategy"
    assert "Univariate" in SHEET_ORDER
    assert STRESS_DAYS and BURNED_HOLDOUT_DAYS
