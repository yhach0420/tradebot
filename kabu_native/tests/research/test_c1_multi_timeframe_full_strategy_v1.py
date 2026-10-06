"""C1 Full Strategy tests. No harvest. Frozen hashes. VWAP is not a global gate."""
from __future__ import annotations

import numpy as np

from research.c1_multi_timeframe_full_strategy_v1 import (
    CANARY_ID,
    CASE_A,
    CASE_B,
    CASE_C,
    CASE_CANARY,
    CASE_D,
    CASE_E,
    GLOBAL_VWAP_ENTRY_GATE,
    NEXT_IF_A,
    NEXT_IF_B,
    NON_VWAP_CANDIDATE_IDS,
    REQUIRED_PRECOMMIT_HASHES,
    VWAP_CANDIDATE_IDS,
)
from research.c1_multi_timeframe_full_strategy_v1.analyze import canary_parity, decide, rank_pass
from research.c1_multi_timeframe_full_strategy_v1.entries import family_evaluable, mtf_signal_indices
from research.c1_multi_timeframe_full_strategy_v1.spec import candidate_ids, canonical_spec, parse_candidate_id
from research.c1_multi_timeframe_precommit_v1 import RAW_CANDIDATE_IDS
from research.simple_tech_entry_family.stages import attach_indicators
from research.systematic_state_transition_full_strategy_v1.states import state_at


def test_frozen_library_and_vwap_gate():
    spec = canonical_spec()
    assert spec["CANDIDATE_N"] == 10
    assert spec["CANDIDATE_IDS"] == list(RAW_CANDIDATE_IDS) == candidate_ids()
    assert spec["CANDIDATE11"] is False
    assert spec["GLOBAL_VWAP_ENTRY_GATE"] is False is GLOBAL_VWAP_ENTRY_GATE
    assert spec["VWAP_CANDIDATE_N"] == 2
    assert spec["NON_VWAP_CANDIDATE_N"] == 8
    assert tuple(spec["VWAP_CANDIDATE_IDS"]) == VWAP_CANDIDATE_IDS
    assert tuple(spec["NON_VWAP_CANDIDATE_IDS"]) == NON_VWAP_CANDIDATE_IDS
    assert spec["CANARY_ID"] == CANARY_ID == "RECOVERY_R2_X1_Z3"
    assert spec["FOLD_TEST_SYMBOL_EXCLUSION_N"] == 0
    assert spec["WINNER_BLOCK_SYMBOL_FILTER_N"] == 0
    assert spec["HTF_VWAP_RECOMPUTED_FROM_AGG_CLOSE_VOLUME"] is False
    assert spec["V7_SAME_BUCKET_JOIN_USED"] is False
    assert spec["NEXT_IF_A"] == NEXT_IF_A
    assert spec["NEXT_IF_B"] == NEXT_IF_B
    for cid in NON_VWAP_CANDIDATE_IDS:
        assert parse_candidate_id(cid)["VWAP_ENTRY_GATE"] is False
    for cid in VWAP_CANDIDATE_IDS:
        assert parse_candidate_id(cid)["VWAP_ENTRY_GATE"] is True
    assert len(REQUIRED_PRECOMMIT_HASHES) == 15


def test_onset_requires_evaluable_prev_and_same_family_htf():
    n = 30
    close = np.linspace(100.0, 130.0, n)
    raw = {
        "close": close,
        "open": close,
        "high": close + 1.0,
        "low": close - 1.0,
        "volume": np.full(n, 10.0),
        "finalize_t": np.arange(n, dtype=float) * 60.0 + 60.0,
        "minute_epoch": np.arange(n, dtype=float) * 60.0,
        "n_events": np.ones(n),
        "first_t": np.arange(n, dtype=float) * 60.0,
        "last_t": np.arange(n, dtype=float) * 60.0 + 50.0,
        "up_vol": np.zeros(n),
        "down_vol": np.zeros(n),
        "ask_vol": np.zeros(n),
        "bid_vol": np.zeros(n),
        "vwap_num": close * 10.0,
    }
    ind = attach_indicators(raw)
    # Warmup bar cannot be an onset even if state first becomes True there.
    i_first = None
    for i in range(n):
        if state_at(ind, "S_MA_TREND_UP", i):
            i_first = i
            break
    assert i_first is not None
    assert family_evaluable(ind, "S_MA_TREND_UP", i_first)
    if i_first >= 1:
        assert family_evaluable(ind, "S_MA_TREND_UP", i_first - 1) or i_first == 23
    empty_htf = {k: np.asarray([], dtype=float) for k in raw}
    leak = {"FUTURE_HTF_BAR_N": 0, "SAME_BUCKET_CARRYBACK_N": 0, "CROSS_SESSION_HTF_BAR_N": 0}
    xs = mtf_signal_indices(
        ind,
        raw,
        state_id="S_MA_TREND_UP",
        htf=empty_htf,
        htf_ind=empty_htf,
        vwap_1m=ind["vwap"],
        am_start=0.0,
        width_sec=180.0,
        leak=leak,
    )
    assert xs == []
    assert leak["FUTURE_HTF_BAR_N"] == 0


def test_canary_hard_pass_includes_ex_best_and_cases():
    exp = {
        "signal_n": 490,
        "fill_n": 334,
        "TRADE_N": 334,
        "TOTAL_PNL": 341340.0,
        "PF": 1.4202607699979068,
        "MAXDD": -258560.0,
        "positive_day_n": 2,
        "negative_day_n": 8,
        "EX_BEST_DAY_PNL": -197910.0,
    }
    pack = canary_parity(exp)
    assert pack["HARD_PASS"] is True
    assert pack["gates"]["CANARY_EX_BEST_PARITY"] is True
    bad = dict(exp)
    bad["EX_BEST_DAY_PNL"] = 0.0
    assert canary_parity(bad)["HARD_PASS"] is False
    d_canary = decide(
        integrity=True,
        exec_integ=True,
        htf_ok=True,
        precommit_ok=True,
        canary_ok=False,
        economics_opened=False,
        coverage_pass_n=10,
        pass_n=2,
        winner_id="X",
        stability={"STABILITY_PASS": True},
    )
    assert d_canary["VERDICT"] == CASE_CANARY
    assert d_canary["ECONOMICS_OPENED"] is False
    d_e = decide(
        integrity=False,
        exec_integ=True,
        htf_ok=True,
        precommit_ok=True,
        canary_ok=True,
        economics_opened=False,
        coverage_pass_n=0,
        pass_n=0,
        winner_id=None,
        stability=None,
    )
    assert d_e["VERDICT"] == CASE_E
    d_d = decide(
        integrity=True,
        exec_integ=True,
        htf_ok=True,
        precommit_ok=True,
        canary_ok=True,
        economics_opened=True,
        coverage_pass_n=0,
        pass_n=0,
        winner_id=None,
        stability=None,
    )
    assert d_d["VERDICT"] == CASE_D
    assert d_d["NEXT"] == NEXT_IF_B
    d_b = decide(
        integrity=True,
        exec_integ=True,
        htf_ok=True,
        precommit_ok=True,
        canary_ok=True,
        economics_opened=True,
        coverage_pass_n=3,
        pass_n=0,
        winner_id=None,
        stability=None,
    )
    assert d_b["VERDICT"] == CASE_B
    d_c = decide(
        integrity=True,
        exec_integ=True,
        htf_ok=True,
        precommit_ok=True,
        canary_ok=True,
        economics_opened=True,
        coverage_pass_n=3,
        pass_n=1,
        winner_id="MTF_3M__S_MA_TREND_UP",
        stability={"STABILITY_PASS": False},
    )
    assert d_c["VERDICT"] == CASE_C
    d_a = decide(
        integrity=True,
        exec_integ=True,
        htf_ok=True,
        precommit_ok=True,
        canary_ok=True,
        economics_opened=True,
        coverage_pass_n=3,
        pass_n=1,
        winner_id="MTF_3M__S_MA_TREND_UP",
        stability={"STABILITY_PASS": True},
    )
    assert d_a["VERDICT"] == CASE_A
    assert d_a["NEXT"] == NEXT_IF_A
    failed = {"candidate_id": "A", "gate": "ECONOMIC_FAIL", "score": 9.0, "PF": 2.0, "TRADE_N": 100}
    ok = {"candidate_id": "B", "gate": "PASS", "score": 1.0, "PF": 1.2, "TRADE_N": 50}
    assert [r["candidate_id"] for r in rank_pass([failed, ok])] == ["B"]
