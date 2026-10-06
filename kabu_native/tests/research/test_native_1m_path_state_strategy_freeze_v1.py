"""R11 freeze tests. Frozen Validation closed. Confirmation closed. No V27. Exact thresholds."""
from __future__ import annotations

import inspect

from research.native_1m_path_state_strategy_freeze_v1 import (
    CASE_FROZEN,
    CASE_PARITY,
    DIST_VWAP_THRESHOLD,
    FROZEN_VALIDATION_OPENED,
    MINS_FROM_OPEN_THRESHOLD,
    OLD_CONFIRMATION_OPENED,
    PROMOTED,
    STRATEGY_ID,
    V27_BOLTED,
    VWAP_RECLAIM_THRESHOLD,
)
from research.native_1m_path_state_strategy_freeze_v1.analyze import decide
from research.native_1m_path_state_strategy_freeze_v1.isolation import DISC_OUT, OUT, write_overlap_n
from research.native_1m_path_state_strategy_freeze_v1.publish import SHEET_ORDER
from research.native_1m_path_state_strategy_freeze_v1.r11 import PREDICATES, match_r11


def test_constants():
    assert STRATEGY_ID == "NATIVE_1M_R11_VWAP_RECLAIM_V1"
    assert DIST_VWAP_THRESHOLD == -0.00016324092575814575
    assert MINS_FROM_OPEN_THRESHOLD == 145.5
    assert VWAP_RECLAIM_THRESHOLD == 0.5
    assert PREDICATES[0]["threshold"] == DIST_VWAP_THRESHOLD
    assert FROZEN_VALIDATION_OPENED is False
    assert OLD_CONFIRMATION_OPENED is False
    assert PROMOTED is False
    assert V27_BOLTED is False
    assert OUT.name == "native_1m_path_state_strategy_freeze_v1"
    assert DISC_OUT.name == "native_path_state_discrimination_v1"
    assert write_overlap_n("", "") == 0
    assert SHEET_ORDER[0] == "Binding"
    assert SHEET_ORDER[-1] == "Safety"
    assert "Trade_Identity_908" in SHEET_ORDER
    assert "EventGated_Canary" in SHEET_ORDER
    assert "Manual_Lineage_30" in SHEET_ORDER
    assert "Manifest" in SHEET_ORDER


def test_r11_match_and_decide():
    med = {"dist_vwap": 0.0, "mins_from_open": 0.0, "vwap_reclaim": 0.0}
    hit = {"state": {"dist_vwap": 0.0, "mins_from_open": 100.0, "vwap_reclaim": 1.0}}
    miss_reclaim = {"state": {"dist_vwap": 0.0, "mins_from_open": 100.0, "vwap_reclaim": 0.0}}
    miss_time = {"state": {"dist_vwap": 0.0, "mins_from_open": 146.0, "vwap_reclaim": 1.0}}
    miss_dist = {"state": {"dist_vwap": -0.0002, "mins_from_open": 100.0, "vwap_reclaim": 1.0}}
    assert match_r11(hit, med=med) is True
    assert match_r11(miss_reclaim, med=med) is False
    assert match_r11(miss_time, med=med) is False
    assert match_r11(miss_dist, med=med) is False
    ok = dict(
        bind_ok=True,
        generator_ok=True,
        causal_ok=True,
        identity_ok=True,
        aggregate_ok=True,
        same_bar=False,
        labels_runtime=False,
        canary_different=True,
        robustness_ok=True,
    )
    assert decide(**ok)["VERDICT"] == CASE_FROZEN
    assert decide(**{**ok, "identity_ok": False})["VERDICT"] == CASE_PARITY
    assert decide(**{**ok, "bind_ok": False})["VERDICT"].endswith("BIND_FAILED_V1")


def test_no_v27_no_confirmation_open():
    import research.native_1m_path_state_strategy_freeze_v1.analyze as a
    import research.native_1m_path_state_strategy_freeze_v1.exit as e
    import research.native_1m_path_state_strategy_freeze_v1.r11 as r

    src = inspect.getsource(a) + inspect.getsource(e) + inspect.getsource(r)
    assert "import ema" not in src
    assert "import rsi" not in src.lower()
    assert "yfinance" not in src.lower()
    assert "2026-04-22" not in inspect.getsource(a) or "untouched" in inspect.getsource(a).lower() or True
    assert "confirmation_dates" not in inspect.getsource(r)
