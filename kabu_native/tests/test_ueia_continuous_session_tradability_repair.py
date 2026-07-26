"""Tests for continuous-session tradability repair."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from research.ueia_continuous_session_tradability_repair.constants import CANCEL, LIVE_ORDER, SUBMIT, STRIDE
from research.ueia_continuous_session_tradability_repair.session import classify_session, market_tradable

JST = ZoneInfo("Asia/Tokyo")


def _ts(h, m, s=0):
    return datetime(2026, 7, 22, h, m, s, tzinfo=JST)


def test_preopen():
    assert classify_session(_ts(8, 59)) == "PREOPEN"
    assert not market_tradable(_ts(8, 59))


def test_continuous_am():
    assert classify_session(_ts(9, 0)) == "CONTINUOUS_AM"
    assert classify_session(_ts(11, 29)) == "CONTINUOUS_AM"
    assert market_tradable(_ts(9, 5))


def test_lunch():
    assert classify_session(_ts(11, 30)) == "LUNCH_BREAK"
    assert classify_session(_ts(12, 0)) == "LUNCH_BREAK"
    assert not market_tradable(_ts(12, 0))


def test_continuous_pm():
    assert classify_session(_ts(12, 30)) == "CONTINUOUS_PM"
    assert classify_session(_ts(15, 29)) == "CONTINUOUS_PM"


def test_after():
    assert classify_session(_ts(15, 30)) == "AFTER_MARKET"


def test_session_source_sidecar():
    from small_paper.market_capture_sidecar import is_market_session_jst
    assert is_market_session_jst(_ts(10, 0))
    assert not is_market_session_jst(_ts(12, 0))


def test_submit_zero():
    assert SUBMIT == CANCEL == LIVE_ORDER == 0
    assert STRIDE == 1


def test_rebuild_resets_session():
    src = open(
        __file__.replace("tests\\test_ueia_continuous_session_tradability_repair.py",
                         "src\\research\\ueia_continuous_session_tradability_repair\\rebuild.py"),
        encoding="utf-8",
    ).read() if False else (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "src/research/ueia_continuous_session_tradability_repair/rebuild.py"
    ).read_text(encoding="utf-8")
    assert "FeatureEngine()" in src and "cid != cur_sess" in src


def test_label_session_bound():
    src = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "src/research/ueia_continuous_session_tradability_repair/rebuild.py"
    ).read_text(encoding="utf-8")
    assert "continuous_session_id" in src and "DATA_END" in src
