"""Native path-state discrimination tests. Frozen Validation closed. No V27. No random CV."""
from __future__ import annotations

import inspect

from research.native_path_state_discrimination_v1 import (
    CASE_NONE,
    FAVOR_BPS,
    FIVE_MINUTE_GRID,
    FROZEN_VALIDATION_OPENED,
    LARGE_WINNER_BPS,
    PARENT_VERDICT,
    PRED_CUTOFF,
    PROMOTED,
    RANDOM_CV,
    TREE_MAX_DEPTH,
    V27_BOLTED,
    X1_TAX_BPS,
)
from research.native_path_state_discrimination_v1.analyze import decide
from research.native_path_state_discrimination_v1.features import PRIMARY_FEATURES, leakage_tokens_in_features
from research.native_path_state_discrimination_v1.isolation import BG_EXIT_OUT, OUT, write_overlap_n
from research.native_path_state_discrimination_v1.outcomes import classify_path
from research.native_path_state_discrimination_v1.publish import SHEET_ORDER


def _fwd(bars: list[tuple], px: float = 100.0) -> dict:
    rows = []
    t = 10 * 60
    for o, h, l, c, above in bars:
        hh = f"{t // 60:02d}:{t % 60:02d}"
        t += 1
        rows.append((hh, o, h, l, c, 100.0, above, None, 0.0, 0.0))
    return {"fwd_bars": rows, "x0_entry_open": px, "mfe_bps": None, "mae_bps": None, "time_to_mfe_min": None, "time_to_mae_min": None}


def test_constants():
    assert PARENT_VERDICT.endswith("NOT_CAUSALLY_RESCUABLE_V1")
    assert TREE_MAX_DEPTH <= 3
    assert PRED_CUTOFF == 0.5
    assert FAVOR_BPS == 8.0
    assert LARGE_WINNER_BPS == 40.0
    assert X1_TAX_BPS == 8.0
    assert FROZEN_VALIDATION_OPENED is False
    assert PROMOTED is False
    assert V27_BOLTED is False
    assert RANDOM_CV is False
    assert FIVE_MINUTE_GRID is False
    assert OUT.name == "native_path_state_discrimination_v1"
    assert BG_EXIT_OUT.name == "bg_cont_vwap_largest_causal_deficiency_v1"
    assert write_overlap_n("", "") == 0
    assert SHEET_ORDER[0] == "Binding"
    assert "Tree_Rules" in SHEET_ORDER
    assert SHEET_ORDER[-1] == "Safety"
    assert not leakage_tokens_in_features()
    assert "symbol" not in PRIMARY_FEATURES


def test_path_labels_and_decide():
    assert decide(bind_ok=False, found=True, any_strategy=True)["VERDICT"].endswith("BIND_FAILED_V1")
    assert decide(bind_ok=True, found=False, any_strategy=False)["VERDICT"] == CASE_NONE
    stall = _fwd([(100, 100.04, 99.97, 100.01, True)] * 5)
    stall["mfe_bps"] = 4.0
    stall["mae_bps"] = -3.0
    stall["time_to_mfe_min"] = 2
    stall["time_to_mae_min"] = 1
    assert classify_path(stall) == "STALL"
    fail = _fwd([(100, 100.02, 99.90, 99.91, False), (99.91, 99.92, 99.80, 99.85, False)])
    fail["mfe_bps"] = 2.0
    fail["mae_bps"] = -10.0
    fail["time_to_mfe_min"] = 0
    fail["time_to_mae_min"] = 0
    assert classify_path(fail) == "IMMEDIATE_FAILURE"


def test_no_grids_no_boosting():
    import research.native_path_state_discrimination_v1.analyze as a
    import research.native_path_state_discrimination_v1.model as m
    import research.native_path_state_discrimination_v1.features as f

    src = inspect.getsource(a) + inspect.getsource(m) + inspect.getsource(f)
    assert "xgboost" not in src.lower()
    assert "RandomForest" not in src
    assert "GridSearch" not in src
    assert "import yfinance" not in src.lower()
    assert "KFold" not in src
    assert TREE_MAX_DEPTH == 3
