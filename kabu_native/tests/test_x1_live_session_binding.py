"""The normal launcher must own trades with FixedSupportX1SessionExecutor.

boot_v1r_native_entry is instrumented to fail if the X1 live path calls it.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from small_paper.paper_session_executor import (
    BINDING_CONTRADICTION,
    PaperExecutorBindingContradiction,
    X1_OWNER_ENV,
    resolve_paper_session_executor,
)
from small_paper.v1r_native_entry_live import boot_v1r_native_entry, reset_native_entry_for_tests
from tests.test_fixed_support_x1_executor import _impulse

NATIVE = Path(__file__).resolve().parents[1]


def _clear_owner_env() -> None:
    os.environ.pop(X1_OWNER_ENV, None)


def test_normal_launcher_does_not_boot_v1r(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import small_paper.v1r_native_entry_live as native
    import small_paper.v1r_paper_primary_launcher as launcher
    calls = {"n": 0}

    def boom(*_a, **_k):
        calls["n"] += 1
        raise AssertionError("boot_v1r_native_entry")

    monkeypatch.setattr(native, "boot_v1r_native_entry", boom)
    monkeypatch.setattr(
        "small_paper.kabu_registration_authority.verify_exact50_membership",
        lambda *_a, **_k: {"ok": True, "actual_n": 50},
    )

    class _Proc:
        def poll(self):
            return 0

    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *_a, **_k: _Proc())
    assertion = SimpleNamespace()
    try:
        launcher._run_daily_live(tmp_path, tmp_path / "hb.jsonl", assertion)
        owner = json.loads((tmp_path / "live_session_owner.json").read_text(encoding="utf-8"))
    finally:
        _clear_owner_env()
    assert calls["n"] == 0
    assert owner["session_owner_class"] == "FixedSupportX1SessionExecutor"
    assert owner["admission_owner_class"] == "FixedSupportX1SessionExecutor"
    assert owner["portfolio_owner_class"] == "FixedSupportX1SessionExecutor"
    assert owner["execution_family"] == "X1_IMMEDIATE_ASK"
    assert owner["boot_v1r_native_entry"] == 0


def test_pilot_dispatch_uses_the_bound_x1_executor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import small_paper.v1r_native_entry_live as native
    from small_paper.pilot_runner import _dispatch_primary_push, _init_v1r_native_entry_for_live

    calls = {"n": 0}

    def boom(*_a, **_k):
        calls["n"] += 1
        raise AssertionError("boot_v1r_native_entry")

    monkeypatch.setattr(native, "boot_v1r_native_entry", boom)
    os.environ[X1_OWNER_ENV] = "1"
    state = SimpleNamespace(
        trading_date="20260928",
        v1r_day_fixed_universe=["7203"],
        v1r_native_entry_blocked=False,
        v1r_native_block_reason="",
        paper_session_executor=None,
    )
    writer = SimpleNamespace(output_dir=tmp_path)
    try:
        wiring = _init_v1r_native_entry_for_live(
            state=state,
            writer=writer,
            native_root=NATIVE,
            trading_date="20260928",
            session_symbols=["7203"],
        )
        assert wiring["x1_owner"] is True
        assert wiring["boot_v1r_native_entry"] == 0
        exe = state.paper_session_executor
        assert type(exe).__name__ == "FixedSupportX1SessionExecutor"
        exe.start_session(
            day="20260801",
            session="AM",
            sess_start=0.0,
            sess_end=100000.0,
            admission=lambda _s, _t: (True, "PASS"),
        )
        ctx = SimpleNamespace(state=state, native_root=NATIVE)
        for row in _impulse(after=[(260.0, 90.0)]):
            out = _dispatch_primary_push(ctx, row, symbol=row["symbol"])
            assert out["session_executor"] == "FixedSupportX1SessionExecutor"
            assert out["legacy_primary_admission_mutations"] == 0
        exe.on_session_boundary()
    finally:
        _clear_owner_env()
    assert calls["n"] == 0
    assert exe.admission_count >= 1
    assert exe.trades
    trade = exe.trades[0]
    assert trade["entry_t"] == pytest.approx(200.0)
    assert trade["entry_px"] == pytest.approx(110.0)
    assert trade["entry_px"] == pytest.approx(trade["entry_ask"] if "entry_ask" in trade else trade["entry_px"])
    assert len(exe.open_pos) == 0


def test_armed_x1_boot_fails_closed() -> None:
    os.environ[X1_OWNER_ENV] = "1"
    try:
        with pytest.raises(PaperExecutorBindingContradiction, match=BINDING_CONTRADICTION):
            boot_v1r_native_entry(universe=["7203"], universe_source="test")
    finally:
        _clear_owner_env()
        reset_native_entry_for_tests()


def test_legacy_v1r_resolver_still_boots_when_x1_owner_is_not_armed() -> None:
    _clear_owner_env()
    exe = resolve_paper_session_executor(
        {
            "execution_family": "PASSIVE_FILL_ENTRY_V1",
            "session_executor": "V1r passiveSessionExecutor",
        }
    )
    assert type(exe).__name__ == "V1rPassiveSessionExecutor"
    assert exe.execution_family == "PASSIVE_FILL_ENTRY_V1"
    try:
        eng = boot_v1r_native_entry(universe=["7203"], universe_source="legacy_regression")
        assert eng.identity()["entry"] == "PASSIVE_FILL_ENTRY_V1"
    finally:
        reset_native_entry_for_tests()
