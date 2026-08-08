"""TAER smoke tests — anchor cross and profile ordering."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from research.e1_x6_taer.anchor import SymHist, detect_anchors_at_eval
from research.e1_x6_taer.classify import profile_pass
from research.e1_x6_taer.config import PROFILE_STRICTNESS, PROFILES, p1_taer_precommit_body


def test_precommit_locks_profiles_and_no_se_state():
    b = p1_taer_precommit_body()
    assert b["selling_exhausted_state_required"] is False
    assert b["fcrr_se_thresholds_relaxed"] is False
    assert b["profiles"] == list(PROFILES)
    assert b["p0_diagnostic_only"] is True
    assert PROFILE_STRICTNESS["TAER_P3"] > PROFILE_STRICTNESS["TAER_P1"]


def test_range_high_cross_detected():
    h = SymHist()
    t0 = 1_000_000.0
    # flat range then breakout
    for i in range(40):
        t = t0 + i
        mid = 100.0 + (0.1 if i % 2 == 0 else 0.0)
        h.push(t, mid, mid - 0.05)
    # breakout tick
    t = t0 + 40
    h.push(t, 101.0, 100.95)
    found = detect_anchors_at_eval(
        h, t=t, mid=101.0, bid=100.95, ask=101.05, spread_bps=10.0,
    )
    assert any(a["anchor_kind"] == "RANGE_HIGH" for a in found)


def test_p0_requires_setup_only():
    exh = {"n_passed": 0, "flags": {}}
    dyn = {"n_passed": 0, "flags": {}}
    assert profile_pass("TAER_P0", True, exh, dyn, {}, {}) is True
    assert profile_pass("TAER_P0", False, exh, dyn, {}, {}) is False
    assert profile_pass("TAER_P1", True, exh, dyn, {}, {}) is False
