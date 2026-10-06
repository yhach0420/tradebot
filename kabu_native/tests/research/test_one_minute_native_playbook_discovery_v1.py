"""Native 1-minute playbook discovery tests. Frozen Validation closed. No paid data. No 5-minute grid."""
from __future__ import annotations

import inspect

from research.one_minute_native_playbook_discovery_v1 import (
    ATLAS_ID,
    CASE_ATLAS,
    CASE_NONE,
    CASE_PLAYBOOKS,
    FIVE_MINUTE_GRID,
    FROZEN_VALIDATION_OPENED,
    HM1_TUNED,
    KABU_50_APPLIED,
    NEW_PAID_DATA,
    PARENT_VERDICT,
    PURCHASE_REQUESTED,
)
from research.one_minute_native_playbook_discovery_v1.analyze import decide
from research.one_minute_native_playbook_discovery_v1.episodes import classify
from research.one_minute_native_playbook_discovery_v1.isolation import CME_OUT, OUT, write_overlap_n
from research.one_minute_native_playbook_discovery_v1.publish import SHEET_ORDER
from research.one_minute_native_playbook_discovery_v1.states import clock_ret, entry_ok


def test_constants():
    assert PARENT_VERDICT == "TRUE_CME_NQ_ES_SOURCE_BLOCKED_V1"
    assert ATLAS_ID == "ONE_MINUTE_BEHAVIOR_ATLAS_V1"
    assert FROZEN_VALIDATION_OPENED is False
    assert KABU_50_APPLIED is False
    assert PURCHASE_REQUESTED is False
    assert HM1_TUNED is False
    assert NEW_PAID_DATA is False
    assert FIVE_MINUTE_GRID is False
    assert OUT.name == "one_minute_native_playbook_discovery_v1"
    assert CME_OUT.name == "true_cme_nq_es_cost_and_coverage_audit_v1"
    assert write_overlap_n("", "") == 0
    assert SHEET_ORDER[0] == "Binding"
    assert "Symbol_Behavior_Profile" in SHEET_ORDER
    assert "Complete_Strategy" in SHEET_ORDER
    assert "HM1_Reference" in SHEET_ORDER
    assert SHEET_ORDER[-1] == "Safety"


def test_decide_classify_and_safety():
    assert decide(bind_ok=False, stable_seq_n=3, promoted_n=1)["VERDICT"].endswith("BIND_FAILED_V1")
    assert decide(bind_ok=True, stable_seq_n=4, promoted_n=2)["VERDICT"] == CASE_PLAYBOOKS
    assert decide(bind_ok=True, stable_seq_n=2, promoted_n=0)["VERDICT"] == CASE_ATLAS
    assert decide(bind_ok=True, stable_seq_n=0, promoted_n=0)["VERDICT"] == CASE_NONE
    assert classify(["PULLBACK_START", "VWAP_RECLAIM"]) == "PULLBACK_THEN_RECLAIM"
    assert classify(["COMPRESSION", "BREAKOUT20"]) == "COMPRESSION_THEN_BREAKOUT"
    assert classify(["LAG_CATCHUP"]) == "LAG_THEN_CATCHUP"
    assert entry_ok("12:00") is False
    assert entry_ok("09:31") is True
    import numpy as np

    times = ["09:30", "09:31", "09:32"]
    close = np.array([100.0, 101.0, 102.0])
    idx = {t: i for i, t in enumerate(times)}
    r = clock_ret(times, close, idx, 1, 1)
    assert abs(r - 0.01) < 1e-9
    import research.one_minute_native_playbook_discovery_v1.analyze as a
    import research.one_minute_native_playbook_discovery_v1.panel as p

    src_a = inspect.getsource(a)
    src_p = inspect.getsource(p)
    assert "import yfinance" not in src_a.lower()
    assert "import yfinance" not in src_p.lower()
    assert "grid_clocks(" not in src_a
    assert "grid_clocks(" not in src_p
    assert FROZEN_VALIDATION_OPENED is False
    assert FIVE_MINUTE_GRID is False
