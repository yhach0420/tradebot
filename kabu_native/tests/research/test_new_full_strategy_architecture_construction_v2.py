"""NEW_FULL_STRATEGY_ARCHITECTURE_CONSTRUCTION_V2. No PnL. No CSB RCA."""
from __future__ import annotations

from research.new_full_strategy_architecture_construction_v2 import (
    ANALYSIS_ID,
    ANOTHER_PRECOMMIT_RUN,
    ARCHITECTURE_ID,
    CSB_RCA_RUN,
    CSB_RETUNE,
    HTF_TREND_BOOLEAN_USED,
    NEW_CANDIDATE_ECONOMICS_RUN,
    NEW_CANDIDATE_PNL_READ_N,
    OLD_ST_RCA_CONTINUED,
    VOLUME_THRESHOLD_SEARCH,
)
from research.new_full_strategy_architecture_construction_v2.analyze import PNL_KEYS
from research.new_full_strategy_architecture_construction_v2.closed_lineage import closed_lineage_audit
from research.new_full_strategy_architecture_construction_v2.csb_pin import csb_closure
from research.new_full_strategy_architecture_construction_v2.level_semantics import prove_level_preexists_test_bar
from research.new_full_strategy_architecture_construction_v2.spec import dumps_sha256, frozen_strategy
from research.new_full_strategy_architecture_construction_v2.volume_identity import volume_identity


def _walk_pnl(obj) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            assert str(k) not in PNL_KEYS
            _walk_pnl(v)
    elif isinstance(obj, list):
        for v in obj:
            _walk_pnl(v)


def test_csb_closed_no_rca():
    csb = csb_closure()
    assert csb["CSB_CLOSED"] is True
    assert csb["CSB_COVERAGE_PASS"] is True
    assert csb["CSB_G1_PASS"] is False
    assert csb["CSB_G2_PASS"] is False
    assert csb["CSB_G3_PASS"] is False
    assert csb["CSB_G4_PASS"] is False
    assert csb["CSB_G5_PASS"] is False
    assert csb["CSB_G6_PASS"] is False
    assert CSB_RCA_RUN is False
    assert CSB_RETUNE is False
    assert csb["CSB_PNL_USED_TO_TUNE_V2"] is False
    _walk_pnl(csb)


def test_level_preexists_hard_gate():
    p = prove_level_preexists_test_bar()
    _walk_pnl(p)
    assert p["HARD_GATE_PASS"] is True
    assert p["LEVEL_PREEXISTS_TEST_BAR"] is True
    assert p["SAME_BAR_CONTRIBUTES_TO_TEST_LEVEL"] is False
    assert p["C1_FINALIZE_T0_WOULD_SEE_OWN_5M"] is True
    assert p["TEST_ASOF_AT_START_T_SEES_OWN_5M"] is False


def test_volume_identity_pinned():
    v = volume_identity()
    _walk_pnl(v)
    assert v["IDENTITY_PASS"] is True
    assert v["VOLUME_MULT"] == 1.5
    assert v["VOLUME_MEDIAN_BARS"] == 5
    assert v["THRESHOLD_SEARCH"] is False
    assert VOLUME_THRESHOLD_SEARCH is False


def test_closed_lineage_distinct():
    lin = closed_lineage_audit()
    _walk_pnl(lin)
    assert lin["AUDIT_DONE"] is True
    assert lin["CLOSED_LINEAGE_MATCH"] is False
    assert lin["STRUCTURALLY_DISTINCT"] is True
    assert lin["DISTINCT_FROM_BREAKOUT"] is True
    assert lin["DISTINCT_FROM_C1"] is True
    assert lin["DISTINCT_FROM_RECOVERY"] is True
    assert lin["DISTINCT_FROM_ST"] is True
    assert lin["CLOSEST_PRIOR_ARCHITECTURE"] == "C1_MULTI_TIMEFRAME"


def test_frozen_spec_no_pnl_no_precommit():
    frozen = frozen_strategy()
    _walk_pnl(frozen)
    sha = dumps_sha256(frozen)
    assert len(sha) == 64
    assert frozen["ARCHITECTURE_ID"] == ARCHITECTURE_ID
    assert frozen["HTF_LEVEL"]["HTF_TREND_BOOLEAN_USED"] is False
    assert HTF_TREND_BOOLEAN_USED is False
    assert frozen["VOLUME"]["VOLUME_THRESHOLD_SEARCH"] is False
    assert frozen["BREAK_LEVEL"]["DYNAMIC_EMA_AFTER_ENTRY_CHANGES_THESIS"] is False
    assert frozen["ANOTHER_PRECOMMIT_RUN"] is False
    assert ANOTHER_PRECOMMIT_RUN is False
    assert OLD_ST_RCA_CONTINUED is False
    assert NEW_CANDIDATE_ECONOMICS_RUN is False
    assert NEW_CANDIDATE_PNL_READ_N == 0
    assert ANALYSIS_ID == "NEW_FULL_STRATEGY_ARCHITECTURE_CONSTRUCTION_V2"
    assert frozen["LEVEL_ASOF"]["LEVEL_PREEXISTS_TEST_BAR"] is True
    assert frozen["LEVEL_ASOF"]["SAME_BAR_CONTRIBUTES_TO_TEST_LEVEL"] is False
    assert frozen["PORTFOLIO"]["CAP"] == 5
    assert frozen["PORTFOLIO"]["SHARES"] == 100
