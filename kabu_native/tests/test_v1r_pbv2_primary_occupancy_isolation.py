"""PBv2 SHADOW_ONLY occupancy — must not mutate Arch E Primary state."""
from __future__ import annotations

from pathlib import Path

from small_paper.v1r_live_dual_lane import V1RLiveDualLane, reset_dual_lane_for_tests
from small_paper.v1r_native_entry_live import ShadowPBv2State, V1RNativeEntryLive


def _eng() -> V1RNativeEntryLive:
    return V1RNativeEntryLive(
        universe=["1001"],
        score_fn=lambda _f: 0.0,
        model_ser={},
        ready=True,
    )


def test_shadow_accept_does_not_mutate_primary_open_pending():
    eng = _eng()
    before = (eng.open_n, eng.pending_n, eng.exposure(), eng.primary_fills)
    snap = eng.note_pbv2_shadow_accept(
        symbol="9999", entry_price=100.0, entry_time="2026-08-12T09:00:00+09:00"
    )
    assert snap["primary_unchanged"] is True
    assert snap["affects_arch_e_occupancy"] is False
    assert snap["shadow_admit"]["admitted"] is True
    assert (eng.open_n, eng.pending_n, eng.exposure(), eng.primary_fills) == before
    assert eng.shadow_pbv2.open_n == 1
    assert eng.shadow_pbv2.snapshot()["affects_dual_primary"] is False


def test_shadow_cap_blocks_without_touching_primary():
    eng = _eng()
    eng.shadow_pbv2 = ShadowPBv2State(cap=2)
    for i, sym in enumerate(["1", "2", "3"]):
        snap = eng.note_pbv2_shadow_accept(
            symbol=sym, entry_price=float(i), entry_time="t"
        )
        if i < 2:
            assert snap["shadow_admit"]["admitted"] is True
        else:
            assert snap["shadow_admit"]["admitted"] is False
            assert snap["shadow_admit"]["reason"] == "shadow_cap"
    assert eng.open_n == 0
    assert eng.shadow_pbv2.open_n == 2
    assert eng.shadow_pbv2.blocked == 1
    assert eng.shadow_pbv2.accepts == 2


def test_shadow_exit_only_shadow_book():
    eng = _eng()
    eng.note_pbv2_shadow_accept(symbol="7777", entry_price=1.0, entry_time="t0")
    out = eng.note_pbv2_shadow_exit(symbol="7777", exit_reason="test")
    assert out["primary_unchanged"] is True
    assert out["shadow_exit"]["closed"] is True
    assert eng.shadow_pbv2.open_n == 0
    assert eng.shadow_pbv2.exits == 1
    assert eng.open_n == 0


def test_dual_rejects_pbv2_and_empty_source(monkeypatch):
    monkeypatch.setenv("V1R_EXIT_V2_LIVE_PRIMARY", "1")
    reset_dual_lane_for_tests()
    dual = V1RLiveDualLane()
    rej = dual.try_admit_fill(symbol="9999", fill_price=1.0, source="pbv2_gate_accept")
    assert rej["rejected"] is True
    assert dual.open_n("primary") == 0
    empty = dual.try_admit_fill(symbol="9999", fill_price=1.0)  # default source=""
    assert empty["rejected"] is True
    assert dual.open_n("primary") == 0
    ok = dual.try_admit_fill(symbol="1001", fill_price=10.0, source="v1r_native")
    assert ok["primary_admitted"] is True
    assert dual.open_n("primary") == 1


def test_pilot_source_has_no_hitchhike_after_register_entry():
    src = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "small_paper"
        / "pilot_runner.py"
    ).read_text(encoding="utf-8")
    idx = src.find("ctx.observer.register_entry(")
    assert idx > 0
    window = src[idx : idx + 1200]
    between = window.split("from small_paper.observer_entry_time", 1)[0]
    live_calls = [
        ln
        for ln in between.splitlines()
        if "try_admit_fill(" in ln and not ln.lstrip().startswith("#")
    ]
    assert live_calls == []
    assert "note_pbv2_shadow_accept" in src
    assert "When V1R is PAPER_PRIMARY, PBv2 gate_accept is SHADOW_ONLY" in src
