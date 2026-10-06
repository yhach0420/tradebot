"""C1 ENTRY×EXIT Full Strategy V2 tests. No harvest. Frozen 40 pairs. Stream invariance."""
from __future__ import annotations

from research.c1_multi_timeframe_entry_exit_full_strategy_v2 import (
    CASE_A,
    CASE_B,
    CASE_C,
    CASE_CANARY,
    CASE_E,
    EXECUTION_ID,
    FROZEN_PAIRS,
    KEPT_EXIT_IDS,
    NEXT_IF_A,
    NEXT_IF_B,
    REQUIRED_V2_HASHES,
)
from research.c1_multi_timeframe_entry_exit_full_strategy_v2.analyze import canary_parity, decide, exclude_symbol, rank_pass
from research.c1_multi_timeframe_entry_exit_full_strategy_v2.prior_pin import live_required_hashes
from research.c1_multi_timeframe_entry_exit_full_strategy_v2.spec import canonical_spec, candidate_ids, parse_pair_id
from research.c1_multi_timeframe_entry_exit_full_strategy_v2.streams import prove_raw_entry_stream_invariance, stream_sha256
from research.c1_multi_timeframe_entry_exit_pair_precommit_v1.spec import pair_id
from research.c1_multi_timeframe_precommit_v1 import RAW_CANDIDATE_IDS


def test_frozen_40_pairs_no_z4_and_hashes():
    spec = canonical_spec()
    ids = candidate_ids()
    assert spec["FINAL_EXIT_N"] == 4
    assert spec["FINAL_PAIR_N"] == 40
    assert spec["PAIR_RERUN_N"] == 40
    assert spec["PRIOR_Z3_METRIC_REUSE_N"] == 0
    assert spec["Z4_TRAILING_STRUCTURE_PRESENT"] is False
    assert spec["NEW_EXIT_CREATED"] is False
    assert spec["EXIT6_CREATED"] is False
    assert spec["TRUE_OOS"] is False
    assert len(ids) == 40 == len(FROZEN_PAIRS)
    assert all(EXECUTION_ID in p for p in ids)
    assert all("Z4_TRAILING_STRUCTURE" not in p for p in ids)
    assert set(spec["FINAL_EXIT_IDS"]) == set(KEPT_EXIT_IDS)
    for e in RAW_CANDIDATE_IDS:
        for z in KEPT_EXIT_IDS:
            assert pair_id(e, z) in ids
            meta = parse_pair_id(pair_id(e, z))
            assert meta["ENTRY_ID"] == e
            assert meta["EXIT_ID"] == z
            assert meta["EXECUTION"] == EXECUTION_ID
    live = live_required_hashes()
    for k, v in live.items():
        assert v == REQUIRED_V2_HASHES[k], (k, v, REQUIRED_V2_HASHES[k])


def test_raw_entry_stream_invariance_and_integrity_cases():
    rows_by = {}
    for e in RAW_CANDIDATE_IDS:
        shared = [
            {"symbol": "7203", "signal_t0": 1.0, "ENTRY_ID": e, "HTF_ID": "HTF_3M"},
            {"symbol": "6758", "signal_t0": 2.0, "ENTRY_ID": e, "HTF_ID": "HTF_3M"},
        ]
        sha0 = stream_sha256(shared, entry_id=e, htf_id="HTF_3M")
        for z in KEPT_EXIT_IDS:
            clone = [dict(r, EXIT_ID=z, fill_n=1 if z == "Z1_VWAP_LOSS" else 0) for r in shared]
            rows_by[pair_id(e, z)] = clone
            assert stream_sha256(clone, entry_id=e, htf_id="HTF_3M") == sha0
    pack = prove_raw_entry_stream_invariance(rows_by)
    assert pack["ENTRY_RAW_STREAM_INVARIANCE_PASS"] is True
    assert pack["PASS_N"] == 10
    broken = dict(rows_by)
    broken[pair_id(RAW_CANDIDATE_IDS[0], KEPT_EXIT_IDS[1])] = [
        {"symbol": "7203", "signal_t0": 9.0, "ENTRY_ID": RAW_CANDIDATE_IDS[0], "HTF_ID": "HTF_3M"}
    ]
    bad = prove_raw_entry_stream_invariance(broken)
    assert bad["ENTRY_RAW_STREAM_INVARIANCE_PASS"] is False
    d_e = decide(
        integrity=True,
        exec_integ=True,
        htf_ok=True,
        precommit_ok=True,
        stream_ok=False,
        canary_ok=True,
        economics_opened=False,
        coverage_pass_n=0,
        pass_n=0,
        winner_id=None,
        stability=None,
        pair_rerun_n=40,
        prior_z3_reuse_n=0,
    )
    assert d_e["VERDICT"] == CASE_E
    assert d_e["ECONOMICS_OPENED"] is False
    dropped = exclude_symbol(
        [{"symbol": "7203.T", "WOULD_FILL": True}, {"symbol": "6758", "WOULD_FILL": True}],
        "7203",
    )
    assert [r["symbol"] for r in dropped] == ["6758"]


def test_canary_hard_pass_and_verdicts():
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
    bad = dict(exp)
    bad["EX_BEST_DAY_PNL"] = 0.0
    assert canary_parity(bad)["HARD_PASS"] is False
    d_canary = decide(
        integrity=True,
        exec_integ=True,
        htf_ok=True,
        precommit_ok=True,
        stream_ok=True,
        canary_ok=False,
        economics_opened=False,
        coverage_pass_n=40,
        pass_n=2,
        winner_id="X",
        stability={"STABILITY_PASS": True},
        pair_rerun_n=40,
        prior_z3_reuse_n=0,
    )
    assert d_canary["VERDICT"] == CASE_CANARY
    assert d_canary["ECONOMICS_OPENED"] is False
    d_b = decide(
        integrity=True,
        exec_integ=True,
        htf_ok=True,
        precommit_ok=True,
        stream_ok=True,
        canary_ok=True,
        economics_opened=True,
        coverage_pass_n=0,
        pass_n=0,
        winner_id=None,
        stability=None,
        pair_rerun_n=40,
        prior_z3_reuse_n=0,
    )
    assert d_b["VERDICT"] == CASE_B
    assert d_b["NEXT"] == NEXT_IF_B
    d_c = decide(
        integrity=True,
        exec_integ=True,
        htf_ok=True,
        precommit_ok=True,
        stream_ok=True,
        canary_ok=True,
        economics_opened=True,
        coverage_pass_n=3,
        pass_n=1,
        winner_id=FROZEN_PAIRS[0],
        stability={"STABILITY_PASS": False},
        pair_rerun_n=40,
        prior_z3_reuse_n=0,
    )
    assert d_c["VERDICT"] == CASE_C
    d_a = decide(
        integrity=True,
        exec_integ=True,
        htf_ok=True,
        precommit_ok=True,
        stream_ok=True,
        canary_ok=True,
        economics_opened=True,
        coverage_pass_n=3,
        pass_n=1,
        winner_id=FROZEN_PAIRS[0],
        stability={"STABILITY_PASS": True},
        pair_rerun_n=40,
        prior_z3_reuse_n=0,
    )
    assert d_a["VERDICT"] == CASE_A
    assert d_a["NEXT"] == NEXT_IF_A
    d_reuse = decide(
        integrity=True,
        exec_integ=True,
        htf_ok=True,
        precommit_ok=True,
        stream_ok=True,
        canary_ok=True,
        economics_opened=True,
        coverage_pass_n=3,
        pass_n=1,
        winner_id=FROZEN_PAIRS[0],
        stability={"STABILITY_PASS": True},
        pair_rerun_n=40,
        prior_z3_reuse_n=1,
    )
    assert d_reuse["VERDICT"] == CASE_E
    failed = {"candidate_id": "A", "gate": "ECONOMIC_FAIL", "score": 9.0, "PF": 2.0, "TRADE_N": 100}
    ok = {"candidate_id": "B", "gate": "PASS", "score": 1.0, "PF": 1.2, "TRADE_N": 50}
    assert [r["candidate_id"] for r in rank_pass([failed, ok])] == ["B"]
