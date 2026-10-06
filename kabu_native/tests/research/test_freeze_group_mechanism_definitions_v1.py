"""Freeze BG_CONT_VWAP + RCA tests. No promotion. No Frozen Validation. No paid data."""
from __future__ import annotations

import inspect

from research.freeze_group_mechanism_definitions_v1 import (
    EXPECTED_PARENT_SPEC_SHA256,
    FIVE_MINUTE_GRID,
    FROZEN_MECHANISM_ID,
    FROZEN_VALIDATION_OPENED,
    KABU_50_APPLIED,
    LARGE_WINNER_BPS,
    NEW_PAID_DATA,
    NO_8136_STRATEGY,
    PARENT_VERDICT,
    PROMOTED,
    STRATEGY_PARAMETER_CHANGED,
    V27_BOLTED,
    X1_TAX_BPS,
)
from research.freeze_group_mechanism_definitions_v1.analyze import decide
from research.freeze_group_mechanism_definitions_v1.freeze import CLOSED_PAIRS, frozen_spec
from research.freeze_group_mechanism_definitions_v1.isolation import OUT, PARENT_OUT, write_overlap_n
from research.freeze_group_mechanism_definitions_v1.publish import SHEET_ORDER
from research.freeze_group_mechanism_definitions_v1.rca import annotate_trade, giveback_summary, winner_concentration


def test_constants_and_freeze():
    assert PARENT_VERDICT == "BEHAVIOR_GROUP_CONDITIONING_MATERIAL_FREEZE_DEFS_V1"
    assert FROZEN_MECHANISM_ID == "BG_CONT_VWAP"
    assert FROZEN_VALIDATION_OPENED is False
    assert PROMOTED is False
    assert STRATEGY_PARAMETER_CHANGED is False
    assert NO_8136_STRATEGY is False
    assert V27_BOLTED is False
    assert KABU_50_APPLIED is False
    assert NEW_PAID_DATA is False
    assert FIVE_MINUTE_GRID is False
    assert X1_TAX_BPS == 8.0
    assert LARGE_WINNER_BPS == 40.0
    assert OUT.name == "freeze_group_mechanism_definitions_v1"
    assert PARENT_OUT.name == "behavior_group_sequence_mechanism_v1"
    assert write_overlap_n("", "") == 0
    spec = frozen_spec()
    assert spec["candidate_id"] == "BG_CONT_VWAP"
    assert spec["group_filter"] == "CONTINUATION"
    assert spec["sequence"] == "VWAP_RECLAIM"
    assert spec["exit_kind"] == "reclaim"
    assert spec["occupancy"] == 3
    assert spec["spec_sha256"] == EXPECTED_PARENT_SPEC_SHA256
    assert len(CLOSED_PAIRS) == 5
    assert all(p["retuned"] is False for p in CLOSED_PAIRS)
    assert SHEET_ORDER[0] == "Binding"
    assert SHEET_ORDER[-1] == "Safety"
    assert "Trade_Ledger_486" in SHEET_ORDER
    assert "8136_Diagnostic" in SHEET_ORDER


def test_decide_and_rca_labels():
    assert decide(bind_ok=False, label="B_EXIT_GIVEBACK")["VERDICT"].endswith("BIND_FAILED_V1")
    assert "EXIT" in decide(bind_ok=True, label="B_EXIT_GIVEBACK")["VERDICT"]
    assert "ENTRY" in decide(bind_ok=True, label="A_ENTRY_INSUFFICIENT")["VERDICT"]
    assert "TAIL" in decide(bind_ok=True, label="C_WINNER_TAIL_DEPENDENCE")["VERDICT"]
    assert "SYMBOL" in decide(bind_ok=True, label="D_SYMBOL_CONCENTRATION")["VERDICT"]
    assert "MIXED" in decide(bind_ok=True, label="E_MIXED")["VERDICT"]
    t = annotate_trade(
        {
            "x0_entry_open": 100.0,
            "x0_bps": -12.0,
            "hold_min": 2,
            "exit_reason": "vwap_loss",
            "fwd_bars": [
                ("10:00", 100.0, 100.2, 99.9, 100.1, 100.0, True, None, 0, 0, 0, 0, ""),
                ("10:01", 100.1, 100.15, 99.7, 99.8, 100.05, False, None, 0, 0, 0, 0, ""),
                ("10:02", 99.8, 99.9, 99.6, 99.7, 100.04, False, None, 0, 0, 0, 0, ""),
            ],
        }
    )
    assert t["rca_ok"] is True
    assert t["giveback_class"] in {"PROFITABLE_THEN_LOSS", "NEVER_PROFITABLE", "OTHER"}
    assert t["giveback_bps"] is not None
    xs = [{"x0_bps": 100.0}, {"x0_bps": 10.0}, {"x0_bps": -5.0}, {"x0_bps": -8.0}]
    w = winner_concentration(xs)
    assert w["used_as_filter"] is False
    assert w["drop_top_1_trade"]["removed_n"] == 1
    g = giveback_summary(
        [
            {"giveback_bps": 20.0, "x0_bps": -5.0, "giveback_class": "PROFITABLE_THEN_LOSS"},
            {"giveback_bps": 1.0, "x0_bps": 8.0, "giveback_class": "PROFITABLE_AND_RETAINED"},
            {"giveback_bps": 0.0, "x0_bps": -3.0, "giveback_class": "NEVER_PROFITABLE"},
        ]
    )
    assert abs(g["fraction_never_profitable"] - 1 / 3) < 1e-9


def test_no_grids_or_paid_or_v27():
    import research.freeze_group_mechanism_definitions_v1.analyze as a
    import research.freeze_group_mechanism_definitions_v1.rca as r

    src_a = inspect.getsource(a)
    src_r = inspect.getsource(r)
    assert "import yfinance" not in src_a.lower()
    assert "grid_clocks(" not in src_a
    assert "grid_clocks(" not in src_r
    assert "v27_bolted" in src_a.lower()
    assert FROZEN_VALIDATION_OPENED is False
    assert V27_BOLTED is False
