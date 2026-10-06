from research.causal_driver_pb1.phase1.dates import assert_ingest_date_allowed as _warm

_warm("20240917")
from research.causal_driver_pb1.sector_state_alpha_shadow_registration.checks import run_checks


def test_shadow_registration_profiles_and_hashes():
    result = run_checks()
    assert result["pass"], result["rows"]
