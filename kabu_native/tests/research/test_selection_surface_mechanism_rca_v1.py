"""SELECTION_SURFACE_MECHANISM_RCA_V1. Four-gate pin. Evidence-only N1/N2/N3."""
from __future__ import annotations

from research.selection_surface_mechanism_rca_v1 import (
    ANALYSIS_ID,
    FAILING_S1,
    FAILING_S4,
    NEXT_EVIDENCE_GAP,
    ST_WINNER_ID,
    VERDICT_INCONCLUSIVE,
)
from research.selection_surface_mechanism_rca_v1.analyze import decide
from research.selection_surface_mechanism_rca_v1.spec import canonical_spec


def test_spec_pins_four_gates_and_no_replay():
    spec = canonical_spec()
    assert spec["ANALYSIS_ID"] == ANALYSIS_ID == "SELECTION_SURFACE_MECHANISM_RCA_V1"
    assert spec["STABILITY_COMPONENT_N"] == 4
    assert spec["NEW_REPLAY"] is False
    assert spec["NEW_FOLD_RUN"] is False
    assert spec["NEW_FOLD_ECONOMICS"] is False
    assert spec["NO_WINNER_TEST_PNL_NOT_AUTO_ZERO"] is True
    assert spec["ST_SPECIFIC"] is True
    assert spec["L1_WIDE"] is False
    assert spec["CROSS_LINEAGE_SELECTION"] is False


def test_four_gates_no_winner_n3_inconclusive():
    pack = decide()
    g = pack["stability_gates"]
    assert g["S1_TRAIN_TOP3_N"] == 2
    assert g["S1_PASS"] is False
    assert g["S2_FULLDEV_WINNER_POSITIVE_BLOCK_N"] == 3
    assert g["S2_PASS"] is True
    assert g["S3_PASS"] is True
    assert g["S3_FULLDEV_WINNER_EX_BEST_BLOCK_TOTAL_PNL"] > 0
    assert abs(float(g["S3_FULLDEV_WINNER_EX_BEST_BLOCK_TOTAL_PNL"]) - 67580.0) < 1e-6
    assert g["S4_PASS"] is False
    assert pack["decision"]["FIXED_WINNER_BLOCK_ROBUSTNESS_PASS"] is True
    assert pack["decision"]["FAILING_STABILITY_COMPONENT_IDS"] == [FAILING_S1, FAILING_S4]
    assert pack["decision"]["BOTH_IDENTIFIED_FAILING_COMPONENTS_CONFIRMED"] is True
    assert pack["decision"]["ALL_STABILITY_COMPONENTS_FAIL"] is False
    assert pack["NO_WINNER_FOLD_N"] == 2
    assert pack["NO_WINNER_FOLD_IDS"] == ["B1", "B2"]
    by = {f["block"]: f for f in pack["folds"]}
    assert by["B1"]["SELECTED_WINNER_EXISTS"] is False
    assert by["B1"]["SELECTED_WINNER_ID"] is None
    assert by["B1"]["TEST_PNL"] is None
    assert by["B1"]["TEST_EVALUATION_RAN"] is False
    assert by["B1"]["COMPARATOR_STATUS"] == "NOT_APPLICABLE"
    assert by["B2"]["TEST_PNL"] is None
    assert by["B3"]["SELECTED_WINNER_ID"] != ST_WINNER_ID
    assert by["B3"]["TEST_PNL"] == -53000.0
    assert by["B4"]["SELECTED_WINNER_ID"] == ST_WINNER_ID
    assert by["B5"]["SELECTED_WINNER_ID"] == ST_WINNER_ID
    assert pack["TEST_EVALUATED_FOLD_N"] == 3
    assert pack["aggregate"]["SOURCE_CONTRIBUTING_FOLD_IDS"] == ["B3", "B4", "B5"]
    assert pack["aggregate"]["NO_WINNER_TREATED_AS_ZERO_BY_SOURCE"] is False
    assert pack["aggregate"]["HELDOUT_TRANSFER_STATUS"] == "PROVEN"
    assert pack["n_counts"] == {"N1_N": 0, "N2_N": 0, "N3_N": 3}
    assert pack["NOT_TOP3_FOLD_IDS"] == ["B1", "B2", "B3"]
    assert all(v == "N3_UNKNOWN" for v in pack["n_labels"].values())
    assert pack["FOLD_G6_NOT_RUN_MISCLASSIFIED_N"] == 0
    assert pack["FOLD_G6_AVAILABILITY"] == {k: False for k in ("B1", "B2", "B3", "B4", "B5")}
    assert pack["decision"]["RECURRENCE_FAILURE_MODE"] == "INCONCLUSIVE_MISSING_FOLD_EVIDENCE"
    assert pack["decision"]["HELDOUT_TRANSFER_FAILURE_SUPPORTED"] is True
    assert pack["decision"]["ST_SPECIFIC"] is True
    assert pack["decision"]["L1_WIDE"] is False
    assert pack["decision"]["CROSS_LINEAGE"] is False
    assert pack["decision"]["VERDICT"] == VERDICT_INCONCLUSIVE
    assert pack["decision"]["NEXT"] == NEXT_EVIDENCE_GAP
    assert pack["guards"]["MISSING_FIELD_RECONSTRUCTED_BY_NEW_REPLAY"] is False
    assert pack["winner"]["ST_WINNER_ID"] == ST_WINNER_ID
