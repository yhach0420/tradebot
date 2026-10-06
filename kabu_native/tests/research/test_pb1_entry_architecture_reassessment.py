from pathlib import Path

from research.pb1_entry_architecture_reassessment import CASE_FOUND, CASE_NONE
from research.pb1_entry_architecture_reassessment.isolation import OUT, write_overlap_n
from research.pb1_entry_architecture_reassessment.precommit import FEATURES, contract, precommit_sha256


def test_precommit_forbids_future_and_ml():
    c = contract()
    assert c["ml_grid_forbidden"] is True
    assert c["future_mfe_as_feature_forbidden"] is True
    assert c["V5_CREATED"] is False
    assert c["feature_available_at_le_entry_allowed_at"] is True
    assert "MFE" not in "".join(FEATURES)
    a = precommit_sha256()
    assert a == precommit_sha256()
    assert len(a) == 64


def test_isolation_and_verdicts():
    assert write_overlap_n("", "") == 0
    assert "pb1_entry_architecture_reassessment" in str(OUT).replace("\\", "/")
    assert CASE_FOUND.startswith("PB1_ENTRY_ARCHITECTURE_CAUSAL_EDGE_FOUND")
    assert CASE_NONE.startswith("PB1_ENTRY_ARCHITECTURE_INFORMATION_INSUFFICIENT")
    src = Path("src/research/pb1_entry_architecture_reassessment")
    text = (src / "features.py").read_text(encoding="utf-8")
    assert "MFE_bps" not in text.split("return")[0] or "path_extrema" not in text
    assert "RandomForest" not in (src / "study.py").read_text(encoding="utf-8")
