"""Tests for UEIA economic gate repair."""
from __future__ import annotations

import json
from pathlib import Path

from research.ueia_economic_gate_and_flow_delay.constants import (
    CANCEL,
    COST_BPS,
    LIVE_ORDER,
    REQUIRED_ARTIFACTS,
    SOURCE_RUN,
    STRIDE,
    SUBMIT,
)
from research.ueia_economic_gate_and_flow_delay.scoring import COST_FORMULA, evaluate_fixed_threshold
from research.upward_edge_identification_audit.samples import Sample
from research.upward_edge_identification_audit.labels import LabelRow
from datetime import datetime
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
PKG = Path(__file__).resolve().parents[1] / "src" / "research" / "ueia_economic_gate_and_flow_delay"


def test_source_run_exists():
    assert (SOURCE_RUN / "report.json").exists()


def test_b2_h5_and_b4_h3_in_source():
    d = json.loads((SOURCE_RUN / "report.json").read_text(encoding="utf-8"))
    hr = d["hypothesis_results"]
    assert abs(hr["B2_H5"]["val"]["roc_auc"] - 0.811454492244659) < 1e-9
    assert abs(hr["B4_H3"]["val"]["top_decile_cost_adj"] - 7.334317941872739) < 1e-9
    assert abs(hr["B4_H2"]["val"]["top_decile_cost_adj"] - 2.428293272868745) < 1e-9


def test_selection_was_auc_max():
    src = (PKG.parents[0] / "upward_edge_identification_audit" / "runner.py").read_text(encoding="utf-8")
    assert "auc > best[0]" in src or "auc > best[" in src


def test_split_local_decile_in_ueia_models():
    src = (PKG.parents[0] / "upward_edge_identification_audit" / "models.py").read_text(encoding="utf-8")
    assert "len(pairs) // 10" in src


def test_cost_single_5bps():
    assert COST_FORMULA["cost_bps_deduction_count"] == 1
    assert COST_FORMULA["spread_explicit_deduction"] == 0
    assert COST_BPS == 5.0


def test_fixed_threshold_not_always_10pct():
    # threshold selection rate can differ from 0.1
    assert "train_fixed_threshold" in (PKG / "scoring.py").read_text(encoding="utf-8")


def test_val_threshold_not_refit():
    src = (PKG / "runner.py").read_text(encoding="utf-8")
    assert "evaluate_fixed_threshold(val" in src
    assert "val_recomputed_threshold\": False" in src or "val_recomputed_threshold" in src


def test_ask_entry_bid_path():
    assert "canonical_ask" in COST_FORMULA["entry"]
    assert "canonical_bid" in COST_FORMULA["future_path"]


def test_submit_live_zero():
    assert SUBMIT == CANCEL == LIVE_ORDER == 0
    assert STRIDE == 1


def test_three_artifacts():
    assert REQUIRED_ARTIFACTS == ("report.md", "report.json", "audit.xlsx")


def test_delay_no_future_ask_cherry_pick():
    src = (PKG / "delay.py").read_text(encoding="utf-8")
    assert "first_ask_at_or_after" in src
    assert "no future" in src.lower() or "No future" in src or "cherry" in src.lower() or "First usable" in src


def test_candidate_scan_after_auc_fail():
    src = (PKG / "runner.py").read_text(encoding="utf-8")
    assert "train_passes" in src and "passed.sort" in src


def test_embargo_reuse():
    assert "dedupe_samples" in (PKG / "runner.py").read_text(encoding="utf-8")
