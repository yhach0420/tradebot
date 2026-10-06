from research.causal_driver_pb1.phase1.dates import assert_ingest_date_allowed as _warm

_warm("20240917")
from research.causal_driver_pb1.identity.ids import sha256_obj
from research.causal_driver_pb1.sector_state_alpha_precommit.bind import GLOBAL_BOUNDARY_SHA, GLOBAL_Q, SECTOR_BOUNDARY_SHA, SECTOR_Q, bind
from research.causal_driver_pb1.sector_state_alpha_precommit.contract import contract


def test_runtime_capacity_and_frozen_quintiles():
    bound = bind()
    assert bound["pass"]
    assert bound["register_limit"] == 50
    assert bound["m1_status"] == "VALIDATED_RESEARCH_ONLY_DATA_SOURCE_UNRESOLVED"
    assert bound["m2_status"] == bound["m1_status"]
    assert bound["m3_status"] == "EXACT_RUNTIME_OBSERVABILITY_PROVEN"
    assert bound["active_runtime_parent_set"] == ["M3"]
    assert bound["driver_identity"]["driver_substitution_used"] is False
    assert sha256_obj(GLOBAL_Q) == GLOBAL_BOUNDARY_SHA
    assert sha256_obj(SECTOR_Q) == SECTOR_BOUNDARY_SHA
    frozen = contract(bound)
    assert frozen["ready"] is True
    assert frozen["precommit_sha256"]
    assert "20260924" not in json_dates(frozen)


def json_dates(doc):
    return str(doc)
