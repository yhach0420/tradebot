from research.causal_driver_pb1.phase1.dates import assert_ingest_date_allowed as _warm

_warm("20240917")
from research.causal_driver_pb1.sector_state_alpha_complete_precommit.contract import contract


def test_complete_strategy_precommit_freezes_without_pnl():
    frozen = contract()
    assert frozen["ready"] is True
    assert frozen["supersedes"] == "3788d1e9201e3754d40de7386dffa52d1cdabce18260aa2ccfe09518b45a71d6"
    assert frozen["NEW_ALPHA_COMPLETE_STRATEGY_SHA256"] != frozen["supersedes"]
    assert frozen["alpha_position_invalidation"]["family_sha256"] == "2829a8be6441d4b437d46edb36a27c455ac21c352248e548a9b35d4070329424"
    assert frozen["observability"]["window_end_classification"] == "FAIL_CLOSE"
    assert frozen["observability"]["last_entry_admission_clock"] == "11:24"
    assert frozen["observability"]["alpha_position_pm_carry"] is False
    assert frozen["economic_replay"] == "NOT_RUN"
    assert frozen["adapter"]["E0_ID"] == "E0_5M_CONFIRMED_EXECUTION"
    assert frozen["adapter"]["E1_ID"] == "E1_1M_TIMED_EXECUTION"
    assert frozen["ECONOMIC_REPLAY_RUNNER_SHA256"]
