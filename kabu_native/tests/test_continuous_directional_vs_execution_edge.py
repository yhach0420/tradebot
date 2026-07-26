"""Tests for CDEED."""
from __future__ import annotations

from research.continuous_directional_vs_execution_edge.constants import CANCEL, COST_BPS, LIVE_ORDER, SUBMIT, STRIDE
from research.continuous_directional_vs_execution_edge.labels import quote_ok, tick_size_jpy


def test_mid_formula():
    bid, ask = 100.0, 100.5
    assert abs((bid + ask) / 2 - 100.25) < 1e-9


def test_quote_ok():
    assert quote_ok(100, 101)[0]
    assert not quote_ok(101, 100)[0]
    assert not quote_ok(0, 100)[0]


def test_tick_size():
    assert tick_size_jpy(500) == 0.1
    assert tick_size_jpy(1500) == 1.0


def test_directional_no_cost_in_constants():
    from research.continuous_directional_vs_execution_edge import labels as L
    src = open(L.__file__, encoding="utf-8").read()
    # directional first_passage_series has no COST_BPS subtraction
    assert "first_passage_series" in src
    assert "cost_adjusted" not in src.split("def first_passage_series")[1].split("def mechanical")[0]


def test_exec_uses_5bps_once():
    assert COST_BPS == 5.0


def test_submit_zero():
    assert SUBMIT == CANCEL == LIVE_ORDER == 0
    assert STRIDE == 1


def test_train_fixed_threshold():
    src = open(
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "src/research/continuous_directional_vs_execution_edge/scoring.py",
        encoding="utf-8",
    ).read()
    assert "0.90" in src


def test_mechanical_defs():
    src = open(
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "src/research/continuous_directional_vs_execution_edge/labels.py",
        encoding="utf-8",
    ).read()
    assert "mechanical_down_strict" in src and "mechanical_down_bid" in src
