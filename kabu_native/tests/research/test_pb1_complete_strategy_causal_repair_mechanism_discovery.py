from pathlib import Path

from research.pb1_complete_strategy_causal_repair_mechanism_discovery import CASE_FOUND, CASE_NONE
from research.pb1_complete_strategy_causal_repair_mechanism_discovery.isolation import OUT, write_overlap_n
from research.pb1_complete_strategy_causal_repair_mechanism_discovery.precommit import EXIT_CANDIDATES, contract, precommit_sha256
from research.pb1_v4_complete_strategy_economic_confirmation1 import FV_FIRST, FV_LAST, PROSPECTIVE_FROM


def test_precommit_stable_and_forbids_mfe_trailing():
    c = contract()
    assert c["mfe_trailing_forbidden"] is True
    assert c["loser_only_oracle_forbidden"] is True
    assert c["full_sample_best_threshold_forbidden"] is True
    assert c["V5_CREATED"] is False
    assert c["frozen_validation_economic_open"] is False
    assert "MFE" not in "".join(EXIT_CANDIDATES)
    assert "TRAIL" not in "".join(EXIT_CANDIDATES)
    a = precommit_sha256()
    b = precommit_sha256()
    assert a == b
    assert len(a) == 64


def test_isolation_does_not_write_holdouts():
    assert write_overlap_n("", "") == 0
    assert "frozen_validation" not in str(OUT)
    assert FV_FIRST == "20260422"
    assert FV_LAST == "20260911"
    assert PROSPECTIVE_FROM == "20260924"


def test_verdict_enum():
    assert CASE_FOUND.startswith("PB1_COMPLETE_STRATEGY_CAUSAL_REPAIR_MECHANISM_FOUND")
    assert CASE_NONE.startswith("PB1_COMPLETE_STRATEGY_NO_ROBUST_CAUSAL_REPAIR_FOUND")
    src = Path("src/research/pb1_complete_strategy_causal_repair_mechanism_discovery")
    text = (src / "study.py").read_text(encoding="utf-8")
    assert "best_threshold" not in text
    assert "drop_from_mfe" not in text.lower()
