"""NEW_FULL_STRATEGY_ARCHITECTURE_INVENTORY_AND_FREEZE_V3. No PnL. No candidate economics."""
from __future__ import annotations

from research.new_full_strategy_architecture_inventory_and_freeze_v3 import (
    ANALYSIS_ID,
    ANOTHER_PRECOMMIT_RUN,
    CASE_NONE,
    NEW_CANDIDATE_ECONOMICS_RUN,
    NEW_CANDIDATE_PNL_READ_N,
    NEXT_IF_NONE,
    V4_RCA_RUN,
    V4_RETUNE,
    WITHDRAWN_V5_ARCHITECTURE_ID,
)
from research.new_full_strategy_architecture_inventory_and_freeze_v3.analyze import PNL_KEYS, decide
from research.new_full_strategy_architecture_inventory_and_freeze_v3.inventory import inventory_ids
from research.new_full_strategy_architecture_inventory_and_freeze_v3.pullback_family import pullback_family
from research.new_full_strategy_architecture_inventory_and_freeze_v3.v4_pin import pin_v4


def _walk_pnl(obj) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            assert str(k) not in PNL_KEYS
            _walk_pnl(v)
    elif isinstance(obj, list):
        for v in obj:
            _walk_pnl(v)


def test_v4_pin_and_withdrawn_pullback():
    pin = pin_v4()
    assert pin["ok"] is True
    assert pin["V4_RCA_RUN"] is False
    assert pin["V4_RETUNE"] is False
    pb = pullback_family()
    ids = inventory_ids()
    assert "SIMPLE_TECH_PULLBACK_V1" in ids
    assert "SIMPLE_TECH_V6_TREND_PULLBACK_STAGE_RCA" in ids
    assert "E1_X6_FCRR_PULLBACK_ACTIVE" in ids
    assert pb["SIMPLE_TECH_PULLBACK_V1_INVENTORIED"] is True
    assert WITHDRAWN_V5_ARCHITECTURE_ID == "BB_VOL_IMPULSE_MA_PULLBACK_X1_Z_SUPPORT_FAIL"


def test_decide_no_forced_architecture_no_pnl():
    pack = decide()
    _walk_pnl(pack)
    d = pack["decision"]
    assert ANALYSIS_ID == "NEW_FULL_STRATEGY_ARCHITECTURE_INVENTORY_AND_FREEZE_V3"
    assert d["VERDICT"] == CASE_NONE
    assert d["NEXT"] == NEXT_IF_NONE
    assert d["ELIGIBLE_PROPOSAL_N"] == 0
    assert d["PROPOSAL_N"] == 0
    assert d["FULL_STRATEGY_SPEC_SHA256_V5"] is None
    assert d["PREVIOUS_V5_PROPOSAL_WITHDRAWN"] is True
    assert V4_RCA_RUN is False
    assert V4_RETUNE is False
    assert ANOTHER_PRECOMMIT_RUN is False
    assert NEW_CANDIDATE_ECONOMICS_RUN is False
    assert NEW_CANDIDATE_PNL_READ_N == 0
    assert pack["eligibility"]["ANY_PULLBACK_RESCUE"] is False
    assert pack["architecture_proposals"] == []
