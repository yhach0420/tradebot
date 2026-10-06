"""NEW_FULL_STRATEGY_IMPLEMENTATION_AND_DEV_EVAL_V1. Integrity only. No PnL."""
from __future__ import annotations

from research.new_full_strategy_implementation_and_dev_eval_v1 import (
    ANALYSIS_ID,
    ANOTHER_PRECOMMIT_RUN,
    BASE_ECONOMIC_RUN_N,
    ECONOMICS_VISIBLE_BEFORE_INTEGRITY_PASS,
    G6_CAUSAL_RERUN_N_MAX,
    OLD_ST_RCA_CONTINUED,
    PINNED_V3_SHA256,
    POST_RESULT_ARCHITECTURE_CHANGE,
    V3_HASH_CHANGED,
)
from research.new_full_strategy_implementation_and_dev_eval_v1.integrity import PNL_KEYS, run_integrity
from research.new_full_strategy_implementation_and_dev_eval_v1.spec import v3_hash_unchanged, v3_sha256


def _walk_pnl(obj) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            assert str(k) not in PNL_KEYS
            _walk_pnl(v)
    elif isinstance(obj, list):
        for v in obj:
            _walk_pnl(v)


def test_v3_hash_unchanged():
    assert v3_sha256() == PINNED_V3_SHA256
    assert v3_hash_unchanged() is True
    assert V3_HASH_CHANGED is False


def test_integrity_pass_no_economics():
    pack = run_integrity()
    _walk_pnl(pack)
    assert ANALYSIS_ID == "NEW_FULL_STRATEGY_IMPLEMENTATION_AND_DEV_EVAL_V1"
    assert ECONOMICS_VISIBLE_BEFORE_INTEGRITY_PASS is False
    assert pack["ECONOMICS_VISIBLE_BEFORE_INTEGRITY_PASS"] is False
    assert pack["V3_IMPLEMENTATION_MATCH"] is True
    assert pack["SESSION_EXIT_INTEGRITY_PASS"] is True
    assert pack["ALL_INTEGRITY_TESTS_PASS"] is True
    failed = [r["id"] for r in (pack["implementation"] + pack["session_exit"]) if not r.get("pass")]
    assert failed == []
    assert BASE_ECONOMIC_RUN_N == 1
    assert G6_CAUSAL_RERUN_N_MAX == 1
    assert OLD_ST_RCA_CONTINUED is False
    assert ANOTHER_PRECOMMIT_RUN is False
    assert POST_RESULT_ARCHITECTURE_CHANGE is False
