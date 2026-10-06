from research.causal_driver_pb1.phase1.dates import assert_ingest_date_allowed as _warm

_warm("20240917")
from research.causal_driver_pb1.sector_state_alpha_shadow.checks import run_checks


def test_shadow_state_targets_and_order_path():
    result = run_checks()
    assert result["pass"], result
