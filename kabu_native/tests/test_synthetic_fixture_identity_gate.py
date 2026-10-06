"""Production universe gate: real 72xx listings pass; the 7200+i fixture does not."""
from __future__ import annotations

from pathlib import Path

from small_paper.paper_trade_checked_runner import PaperTradeCheckedRunner
from small_paper.pre_paper_ready_seal import (
    FAIL_UNIVERSE_UNAVAILABLE,
    is_synthetic_fixture_identity,
    production_universe_unavailable,
)

REAL_72XX = ["7220.T", "7211.T", "7214.T", "7218.T", "7245.T", "7246.T"]


def _real(n: int, *, extra: list[str] | None = None) -> list[str]:
    symbols = [f"{1000 + i}.T" for i in range(n)]
    if extra:
        symbols.extend(extra)
    return symbols


def test_real_7220_allowed() -> None:
    symbols = _real(49, extra=["7220.T"])
    assert len(symbols) == 50
    assert production_universe_unavailable(symbols, reason="") == ""


def test_multiple_real_72xx_allowed() -> None:
    symbols = _real(44, extra=REAL_72XX)
    assert len(symbols) == 50
    assert production_universe_unavailable(symbols, reason="") == ""


def test_exact_synthetic_fixture_rejected() -> None:
    fixture = [str(7200 + i) for i in range(50)]
    assert production_universe_unavailable(fixture, reason="") == FAIL_UNIVERSE_UNAVAILABLE


def test_synthetic_fallback_provenance_rejected() -> None:
    symbols = _real(50)
    assert (
        production_universe_unavailable(symbols, reason="synthetic_universe", synthetic_provenance=True)
        == FAIL_UNIVERSE_UNAVAILABLE
    )
    assert production_universe_unavailable([], reason="synthetic_fallback") == FAIL_UNIVERSE_UNAVAILABLE


def test_normal_real_universe_allowed() -> None:
    assert production_universe_unavailable(_real(50), reason="") == ""
    assert is_synthetic_fixture_identity(_real(49, extra=["7220.T"])) is False


def test_malformed_ambiguous_universe_fail_closed() -> None:
    partial = [str(7200 + i) for i in range(30)]
    replaced = [str(7200 + i) for i in range(49)] + ["6758.T"]
    fragment = [str(7200 + i) for i in range(10)] + _real(40)
    assert production_universe_unavailable(partial, reason="") == FAIL_UNIVERSE_UNAVAILABLE
    assert production_universe_unavailable(replaced, reason="") == FAIL_UNIVERSE_UNAVAILABLE
    assert production_universe_unavailable(fragment, reason="") == FAIL_UNIVERSE_UNAVAILABLE
    assert production_universe_unavailable(["7200"], reason="") == FAIL_UNIVERSE_UNAVAILABLE


def _runner(tmp_path: Path) -> PaperTradeCheckedRunner:
    return PaperTradeCheckedRunner(
        repo_root=tmp_path,
        native_root=tmp_path,
        paper_bat=tmp_path / "run_paper_trade.bat",
        skip_w4s=True,
    )


def test_checked_launcher_resolve_allows_real_7220(monkeypatch, tmp_path: Path) -> None:
    symbols = _real(49, extra=["7220.T"])
    monkeypatch.setattr(
        "small_paper.day_fixed_am_registration.load_am_canonical_50",
        lambda *_a, **_k: {"ok": True, "symbols": symbols, "symbol_count": 50, "reason": ""},
    )
    monkeypatch.setattr(
        "small_paper.pre_freeze_kabu_validation.freeze_valid50_after_kabu_validation",
        lambda *_a, **_k: {"ok": True, "frozen": {}, "reason": "fixture"},
    )
    runner = _runner(tmp_path)
    assert runner.capture_synthetic is False
    assert runner.step_universe_resolve() is True
    assert runner.capture["universe"]["reason"] == ""


def test_checked_launcher_resolve_rejects_synthetic_fixture(monkeypatch, tmp_path: Path) -> None:
    fixture = [str(7200 + i) for i in range(50)]
    monkeypatch.setattr(
        "small_paper.day_fixed_am_registration.load_am_canonical_50",
        lambda *_a, **_k: {"ok": True, "symbols": fixture, "symbol_count": 50, "reason": ""},
    )
    runner = _runner(tmp_path)
    assert runner.step_universe_resolve() is False
    assert runner.blocked["reason"] == FAIL_UNIVERSE_UNAVAILABLE
    assert runner.paper_call_count == 0


def test_production_missing_csv_does_not_inject_fixture(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "small_paper.day_fixed_am_registration.load_am_canonical_50",
        lambda *_a, **_k: {"ok": False, "symbols": [], "symbol_count": 0, "reason": "am_csv_missing"},
    )
    runner = _runner(tmp_path)
    assert runner.step_universe_resolve() is False
    assert runner.capture["universe"]["symbols"] == []
    assert runner.capture["universe"]["reason"] == "am_csv_missing"
    assert runner.paper_call_count == 0
