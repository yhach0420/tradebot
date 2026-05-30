from __future__ import annotations

from pathlib import Path

from research.phase188_pre_adverse_entry_feature_review import (
    FEATURE_DEFS,
    evaluate_pre_adverse_entry_feature_review,
)


def test_phase188_feature_defs_count():
    assert len(FEATURE_DEFS) == 12


def test_phase188_evaluate_smoke(tmp_path: Path):
    repo = tmp_path
    (repo / "kabu_native" / "results" / "reports").mkdir(parents=True)
    out = evaluate_pre_adverse_entry_feature_review(repo_root=repo)
    assert out["phase"] == 188
    assert "feature_importance_ranking" in out
    assert "top_3_adverse_cluster_features" in out
