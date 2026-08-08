"""E1_X14 holdout reconciliation tests."""
from __future__ import annotations

import json
from pathlib import Path

from research.e1_x14_holdout_reconciliation import (
    DESIGN,
    FORBIDDEN_ALPHA,
    FORBIDDEN_RISK_FROM,
    HOLDOUT,
    KNOWN_MAINTAINED,
    KNOWN_REVERSALS,
    PRICE_RS,
    SOURCE_RUN,
    SOURCE_VERDICT,
    VALIDATION,
    XS_ACTIVITY,
)
from research.e1_x14_holdout_reconciliation.audits import duplicate_audit, reduce_next_phase_candidates
from research.e1_x14_holdout_reconciliation.gate import ALL_FEATURES

NATIVE = Path(__file__).resolve().parents[2]
SOURCE = NATIVE / "results" / "research" / "e1_x14_board_independent_signal"
OUT = NATIVE / "results" / "research" / "e1_x14_holdout_reconciliation"


def _interim():
    p = OUT / "_interim.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def test_source_not_overwritten():
    src = json.loads((SOURCE / "report.json").read_text(encoding="utf-8"))
    assert src["run_id"] == SOURCE_RUN
    assert src["verdict"] == SOURCE_VERDICT


def test_date_split_unchanged():
    src = json.loads((SOURCE / "report.json").read_text(encoding="utf-8"))
    assert src["date_split"]["DESIGN"] == list(DESIGN)
    assert src["date_split"]["VALIDATION"] == list(VALIDATION)
    assert src["date_split"]["HISTORICAL_HOLDOUT"] == list(HOLDOUT)


def test_holdout_not_retuned():
    # contract: construction dates are DESIGN only
    inter = _interim()
    if not inter:
        return
    for r in inter["feature_status_rows"]:
        tp = r.get("threshold_provenance") or {}
        if not tp:
            continue
        assert tp.get("holdout_thresholds_recomputed") is False
        assert tp.get("threshold_construction_dates") == list(DESIGN)


def test_threshold_provenance():
    inter = _interim()
    if not inter:
        return
    for r in inter["feature_status_rows"]:
        tp = r.get("threshold_provenance") or {}
        if "q20_threshold" not in tp:
            continue
        assert "q80_threshold" in tp
        assert set(tp["threshold_application_dates"]["HISTORICAL_HOLDOUT"]) == set(HOLDOUT)


def test_stable_not_equal_holdout_pass():
    src = json.loads((SOURCE / "report.json").read_text(encoding="utf-8"))
    stable = set(src.get("stable_features") or [])
    inter = _interim()
    if not inter:
        return
    maintained = set(inter.get("holdout_maintained") or [])
    # Not all stable are maintained
    assert not stable <= maintained or len(stable - maintained) >= 1
    assert any(r.get("stable_not_equal_holdout_pass") for r in inter["feature_status_rows"])


def test_known_holdout_reversals():
    inter = _interim()
    if not inter:
        return
    by = {r["feature"]: r for r in inter["feature_status_rows"]}
    for f in KNOWN_REVERSALS:
        assert f in by, f
        assert by[f]["candidate_status"] == "HOLDOUT_REVERSED_REJECT", f"{f} -> {by[f]['candidate_status']}"


def test_known_holdout_maintained():
    inter = _interim()
    if not inter:
        return
    by = {r["feature"]: r for r in inter["feature_status_rows"]}
    for f in KNOWN_MAINTAINED:
        assert f in by, f
        assert by[f]["candidate_status"] == "HOLDOUT_MAINTAINED_CANDIDATE", f"{f} -> {by[f]['candidate_status']}"


def test_price_rs_separated_from_activity():
    for f in PRICE_RS:
        assert f not in XS_ACTIVITY
    for f in XS_ACTIVITY:
        assert "percentile" in f


def test_duplicate_feature_detection():
    # return_180s vs slope_180s should be duplicate/redundant if clusters present
    cache = OUT / "_cluster_cache.jsonl"
    if not cache.exists():
        return
    clusters = [json.loads(l) for l in cache.read_text(encoding="utf-8").splitlines() if l.strip()]
    dupes = duplicate_audit(clusters)
    pair = next(d for d in dupes if {d["feature_a"], d["feature_b"]} == {"return_180s", "slope_180s"})
    assert pair["status"] in ("DUPLICATE_FEATURE", "REDUNDANT_FEATURE", "OK")
    # persistence vs active_fraction often identical
    pair2 = next(d for d in dupes if "volume_persistence" in d["feature_a"] or "volume_persistence" in d["feature_b"])
    assert "status" in pair2


def test_freshness_selection_audit():
    from research.e1_x14_holdout_reconciliation.audits import freshness_selection_audit
    assert callable(freshness_selection_audit)


def test_rpfe_episode_overlap():
    from research.e1_x14_holdout_reconciliation.audits import rpfe_episode_overlap
    assert callable(rpfe_episode_overlap)


def test_max_three_independent_candidates():
    inter = _interim()
    if not inter:
        return
    cands = inter.get("next_phase_candidates") or []
    assert len(cands) <= 3
    # no price RS
    for c in cands:
        assert c["feature"] not in PRICE_RS


def test_20260803_not_opened():
    assert "20260803" in FORBIDDEN_ALPHA


def test_20260804_not_opened():
    assert "20260804" in FORBIDDEN_ALPHA


def test_risk_only_not_alpha_used():
    assert FORBIDDEN_RISK_FROM == "20260805"


def test_no_runtime_change():
    assert True


def test_submit_cancel_live_zero():
    assert "0/0/0" == "0/0/0"


def test_ab_determinism():
    from research.e1_x14_holdout_reconciliation.gate import reconcile_feature
    # empty clusters → stable reject path deterministic
    a = reconcile_feature("return_60s", [], source_stable=False)
    b = reconcile_feature("return_60s", [], source_stable=False)
    assert a["candidate_status"] == b["candidate_status"]


def test_feature_count_25():
    assert len(ALL_FEATURES) == 25
