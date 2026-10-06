"""C1 EXIT semantic uniqueness. No candidate economics. No new EXIT."""
from __future__ import annotations

import numpy as np

from research.c1_multi_timeframe_entry_exit_pair_precommit_v2 import (
    ANALYSIS_ID,
    CASE_A,
    CASE_B,
    CASE_E,
    DEV_CLASSIFICATION,
    EXPECTED_EXIT_FILE_SHA256,
    EXIT_IDS,
    PAIRWISE_EXIT_PAIRS,
)
from research.c1_multi_timeframe_entry_exit_pair_precommit_v2.analyze import build_answers, decide
from research.c1_multi_timeframe_entry_exit_pair_precommit_v2.bar_contract import prove_valid_bar_contract
from research.c1_multi_timeframe_entry_exit_pair_precommit_v2.equivalence import classify
from research.c1_multi_timeframe_entry_exit_pair_precommit_v2.exits_proof import prove_exit_predicates, z2_z4_source_domain_equivalence
from research.c1_multi_timeframe_entry_exit_pair_precommit_v2.spec import canonical_spec
from research.c1_multi_timeframe_entry_exit_pair_precommit_v2.suffix_audit import audit_suffix_domain
from research.simple_full_strategy_discovery_v1.exits import technical_fire_i


def _row() -> dict:
    # Finite OHLC. Z2/Z4 fire at 2 on the full suffix. Z1 fires later at 5 so Z5 ≠ Z1.
    # Suffix start>=3: Z2/Z4 none, Z1 fires at 5 so Z5 ≠ Z2.
    open_ = np.asarray([10.0, 10.0, 10.0, 10.0, 10.0, 10.0], dtype=float)
    high = np.asarray([11.0, 11.0, 11.0, 11.0, 11.0, 11.0], dtype=float)
    low = np.asarray([9.0, 9.5, 8.0, 9.0, 6.0, 6.0], dtype=float)
    close = np.asarray([10.0, 10.0, 9.0, 10.0, 10.0, 7.0], dtype=float)
    vwap = np.asarray([8.0, 8.0, 8.0, 8.0, 8.0, 8.0], dtype=float)
    finalize = np.asarray([60.0, 120.0, 180.0, 240.0, 300.0, 360.0], dtype=float)
    return {
        "day": "20260722",
        "symbol": "TEST",
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "vwap": vwap,
        "finalize_t": finalize,
        "bar_n": 6,
    }


def test_spec_bans_economics_and_new_exit():
    spec = canonical_spec()
    assert spec["ANALYSIS_ID"] == ANALYSIS_ID
    assert spec["CANDIDATE_ECONOMICS_RUN"] is False
    assert spec["CANDIDATE_SIGNAL_COUNT_COMPUTED"] is False
    assert spec["CANDIDATE_FILL_COUNT_COMPUTED"] is False
    assert spec["CANDIDATE_TRADE_COUNT_COMPUTED"] is False
    assert spec["CANDIDATE_PNL_COMPUTED"] is False
    assert spec["NEW_EXIT"] is False
    assert spec["EXIT6_CREATED"] is False
    assert spec["ENTRY_MODIFIED"] is False
    assert spec["PRECOMMIT_ALL_PAIRS_PRE_ECONOMICS"] is False
    assert spec["DEV_CLASSIFICATION"] == DEV_CLASSIFICATION
    assert spec["Z3_SLICE_PREVIOUSLY_OBSERVED"] is True
    assert len(PAIRWISE_EXIT_PAIRS) == 10


def test_valid_completed_bar_requires_finite_ohlc():
    c = prove_valid_bar_contract()
    assert c["ok"] is True
    assert c["VALID_COMPLETED_BAR_REQUIRES_FINITE_OPEN"] is True
    assert c["VALID_COMPLETED_BAR_REQUIRES_FINITE_HIGH"] is True
    assert c["VALID_COMPLETED_BAR_REQUIRES_FINITE_LOW"] is True
    assert c["VALID_COMPLETED_BAR_REQUIRES_FINITE_CLOSE"] is True
    assert c["INVALID_BARS_NOT_PUBLISHED"] is True
    assert "slot[\"high\"] == slot[\"high\"]" in c["FINISH_SOURCE"]


def test_z4_peak_is_finite_high_guard_not_threshold():
    pred = prove_exit_predicates()
    assert pred["HASH_MATCH"] is True
    assert pred["SOURCE_FILE_SHA256"] == EXPECTED_EXIT_FILE_SHA256
    assert pred["Z4_PEAK_VALUE_USED_IN_EXIT_THRESHOLD"] is False
    assert pred["Z4_PEAK_ONLY_ACTS_AS_FINITE_HIGH_SEEN_GUARD"] is True
    assert pred["Z5_IS_EARLIEST_OF_Z1_AND_Z2"] is True
    bar = prove_valid_bar_contract()
    z2z4 = z2_z4_source_domain_equivalence(bar, pred)
    assert z2z4["proven"] is True


def test_z2_equals_z4_on_canonical_finite_bars_not_on_nan_high():
    rec = _row()
    idxs = [0, 1, 2, 3, 4, 5]
    z2 = technical_fire_i("Z2", idxs=idxs, open_=rec["open"], high=rec["high"], low=rec["low"], close=rec["close"], vwap=rec["vwap"])
    z4 = technical_fire_i("Z4", idxs=idxs, open_=rec["open"], high=rec["high"], low=rec["low"], close=rec["close"], vwap=rec["vwap"])
    assert z2 == z4
    high_nan = rec["high"].copy()
    high_nan[0:3] = np.nan
    z2b = technical_fire_i("Z2", idxs=idxs, open_=rec["open"], high=high_nan, low=rec["low"], close=rec["close"], vwap=rec["vwap"])
    z4b = technical_fire_i("Z4", idxs=idxs, open_=rec["open"], high=high_nan, low=rec["low"], close=rec["close"], vwap=rec["vwap"])
    assert z2b != z4b


def test_suffix_audit_and_equivalence_drop_z4_only():
    suf = audit_suffix_domain([_row()])
    assert suf["ALL_10_PAIRS_COMPLETED"] is True
    assert suf["PAIR_COMPARISON_N"] == 10
    by = {(r["EXIT_A"], r["EXIT_B"]): r for r in suf["pairwise"]}
    assert by[("Z2_STRUCTURE_LOSS", "Z4_TRAILING_STRUCTURE")]["FIRE_INDEX_MISMATCH_N"] == 0
    pred = prove_exit_predicates()
    z2z4 = z2_z4_source_domain_equivalence(prove_valid_bar_contract(), pred)
    eq = classify(suf["pairwise"], z2z4=z2z4, predicates=pred)
    assert eq["Z2_Z4_SEMANTIC_DUPLICATE"] is True
    assert "Z4_TRAILING_STRUCTURE" in eq["dropped_exit_ids"]
    assert eq["kept_exit_ids"] == [
        "Z1_VWAP_LOSS",
        "Z2_STRUCTURE_LOSS",
        "Z3_TWO_BAR_WEAKNESS",
        "Z5_HYBRID_SIMPLE",
    ]
    assert eq["Z5_DISTINCT_FROM_Z1"] is True
    assert eq["Z5_DISTINCT_FROM_Z2"] is True
    pack = decide(rows=[_row()], skip_ohlc_load=True)
    assert pack["CANDIDATE_ECONOMICS_RUN"] is False
    assert pack["CANDIDATE_SIGNAL_COUNT_COMPUTED"] is False
    assert pack["CANDIDATE_PNL_COMPUTED"] is False
    answers = build_answers(pack)
    assert answers["6"] is False
    assert answers["8"] is True
    assert answers["9"] is True
    assert answers["15"] is False
    assert answers["16"] is True
    assert answers["27"] is False
    assert answers["28"] is False
    assert answers["33"] is False
    assert answers["39"] == "0/0/0"
    assert pack["VERDICT"] in {CASE_A, CASE_B, CASE_E}
    if pack["CASE_NAME"] == CASE_A:
        assert pack["equivalence"]["FINAL_EXIT_N"] == 4
        assert len(pack["final_pairs"]) == 40
        assert "Z4_TRAILING_STRUCTURE" in pack["equivalence"]["dropped_exit_ids"]
    assert answers["42"] == pack["VERDICT"]
    assert answers["43"] == pack["NEXT"]
    assert len(EXIT_IDS) == 5
