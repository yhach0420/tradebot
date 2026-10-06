"""Reference-level 1m discovery tests. No Confirmation. R11 not repaired. Causal first-event only."""
from __future__ import annotations

import inspect

from research.cause_first_mechanism_discovery_v1.clock import hhmm_add, in_lunch
from research.reference_level_1m_price_action_discovery_v1 import (
    FROZEN_VALIDATION_OPENED,
    IMPLICIT_1130_TRUNCATION,
    LUNCH_POLICY,
    OLD_CONFIRMATION_OPENED,
    R11_REPAIRED,
    R11_STATUS,
    SESSION_FLAT,
    TIME_STOP_USED,
)
from research.reference_level_1m_price_action_discovery_v1.analyze import decide
from research.reference_level_1m_price_action_discovery_v1.isolation import OUT, R11_AUDIT_OUT, write_overlap_n
from research.reference_level_1m_price_action_discovery_v1.levels import gap_side
from research.reference_level_1m_price_action_discovery_v1.machine import new_state, step_static
from research.reference_level_1m_price_action_discovery_v1.outcomes import attach_fwd
from research.reference_level_1m_price_action_discovery_v1.publish import SHEET_ORDER


def test_constants_and_sheets():
    assert FROZEN_VALIDATION_OPENED is False
    assert OLD_CONFIRMATION_OPENED is False
    assert R11_REPAIRED is False
    assert R11_STATUS == "INVALID_AS_CAUSAL_STRATEGY"
    assert LUNCH_POLICY == "HOLD_THROUGH_LUNCH_RESUME_PM"
    assert SESSION_FLAT == "15:20"
    assert TIME_STOP_USED is False
    assert IMPLICIT_1130_TRUNCATION is False
    assert OUT.name == "reference_level_1m_price_action_discovery_v1"
    assert R11_AUDIT_OUT.name == "r11_online_episode_causality_audit_v1"
    assert write_overlap_n("", "") == 0
    assert SHEET_ORDER[0] == "Binding"
    assert SHEET_ORDER[-1] == "Safety"
    assert "Session_Semantics" in SHEET_ORDER
    assert "Candidate_Playbooks" in SHEET_ORDER
    assert in_lunch("11:30") is True
    assert in_lunch("12:30") is False


def test_accept2_not_moved_to_break():
    st = new_state("PDH", "previous_day", 100.0)
    times = [f"09:{i:02d}" for i in range(0, 8)]
    closes = [99.0, 99.5, 100.1, 100.2, 100.3, 100.4, 100.2, 100.1]
    highs = [c + 0.05 for c in closes]
    lows = [c - 0.05 for c in closes]
    prev = 99.0
    kinds = []
    for i, t in enumerate(times):
        c = closes[i]
        evs = step_static(st, c=c, h=highs[i], l=lows[i], prev_c=prev, tm=9 * 60 + i, feature_bar=t)
        kinds.extend([(e["event_kind"], e["feature_bar"], e.get("break_feature_bar")) for e in evs])
        prev = c
    brk = [k for k in kinds if k[0] == "BREAK_ABOVE"]
    acc2 = [k for k in kinds if k[0] == "ACCEPT2_ABOVE"]
    assert brk and brk[0][1] == "09:02"
    assert acc2 and acc2[0][1] == "09:04"
    assert acc2[0][1] != brk[0][1]
    assert hhmm_add(acc2[0][1], 1) == "09:05"


def test_or_availability_and_gap():
    assert gap_side(101.0, 100.0) == "GAP_UP"
    assert gap_side(99.0, 100.0) == "GAP_DOWN"
    assert gap_side(100.05, 100.0) is None
    st = new_state("OR5H", "opening_range", 100.0)
    st["available"] = False
    evs = step_static(st, c=101.0, h=101.0, l=100.5, prev_c=99.0, tm=9 * 60 + 3, feature_bar="09:03")
    assert evs == []
    st["available"] = True
    evs = step_static(st, c=101.0, h=101.0, l=100.5, prev_c=99.0, tm=9 * 60 + 6, feature_bar="09:06")
    assert any(e["event_kind"] == "BREAK_ABOVE" for e in evs)


def test_fwd_holds_through_lunch():
    times = [f"11:{m:02d}" for m in range(20, 30)] + [f"12:{m:02d}" for m in range(30, 40)] + ["15:20"]
    n = len(times)
    rec = {
        "t": times,
        "idx": {t: i for i, t in enumerate(times)},
        "n": n,
        "o": [100.0] * n,
        "h": [100.2] * n,
        "l": [99.8] * n,
        "c": [100.1] * n,
        "vw": [100.0] * n,
    }
    ep = {"event_time": "11:20", "level_value": 100.0}
    attach_fwd(ep, rec)
    last = str(ep.get("fwd_last_hh") or "")
    assert ep.get("fwd_crosses_lunch") is True
    assert last >= "12:30"
    assert last != "11:29"
    assert not any(in_lunch(str(r[0])) for r in ep["fwd_bars"])


def test_decide_and_no_r11_repair():
    assert decide(bind_ok=False, causal_ok=True, selected_n=5, complete_ids=[], atlas_n=10)["VERDICT"].endswith("BIND_FAILED_V1")
    ok = decide(bind_ok=True, causal_ok=True, selected_n=5, complete_ids=["PB_PDH_BREAK_ACCEPT2"], atlas_n=100)
    assert "COMPLETE_STRATEGY" in ok["VERDICT"] or "DESIGN_EVIDENCE" in ok["VERDICT"]
    import research.reference_level_1m_price_action_discovery_v1.walk as w
    import research.reference_level_1m_price_action_discovery_v1.analyze as a

    src = inspect.getsource(w) + inspect.getsource(a)
    assert "cluster[-1]" not in src
    assert "yfinance" not in src.lower()
    assert "DIST_VWAP_THRESHOLD" not in src
