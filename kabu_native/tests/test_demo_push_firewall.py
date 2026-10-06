"""Demo-push recovery isolation. Production recovery behavior stays on the historical gate."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

NATIVE = Path(__file__).resolve().parents[1]
MANIFEST = (
    NATIVE
    / "results"
    / "small_paper"
    / "20260910"
    / "live_session_122536"
    / "live_order_safety"
    / "session_manifest.json"
)
RECON = (
    NATIVE
    / "results"
    / "small_paper"
    / "20260910"
    / "live_session_122536"
    / "live_order_safety"
    / "broker_reconciliation.jsonl"
)
FROZEN_SUMMARY = NATIVE / "runtime" / "frozen_am_universe_summary.json"
REGISTRATION = NATIVE / "runtime" / "market_registration_manifest.json"
ACTIVATION = (
    NATIVE
    / "results"
    / "research"
    / "v1r_exit_v2_prospective_activation"
    / "FIXED_ENTRY_SUPPORT_PAPER_PRIMARY_ACTIVATION_V2.json"
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def armed_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TRADEBOT_DEMO_PUSH_E2E", "1")
    monkeypatch.setattr(sys, "argv", ["paper_trade_checked_runner.py", "--no-pause", "--demo-push-e2e"])
    from small_paper.demo_push_firewall import _PARENT_CACHE

    _PARENT_CACHE.clear()
    yield
    _PARENT_CACHE.clear()


def test_half_flags_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from small_paper.demo_push_firewall import demo_fully_armed

    monkeypatch.delenv("TRADEBOT_DEMO_PUSH_E2E", raising=False)
    assert demo_fully_armed(cli_flag=True, env_flag=False, consult_parent=False) is False
    monkeypatch.setenv("TRADEBOT_DEMO_PUSH_E2E", "1")
    assert demo_fully_armed(cli_flag=False, env_flag=True, consult_parent=False) is False
    assert demo_fully_armed(cli_flag=True, env_flag=True, consult_parent=False) is True


def test_normal_recovery_still_blocks_on_20260910_history() -> None:
    from small_paper.check_live_order_recovery_readiness import main
    from small_paper.demo_push_firewall import _PARENT_CACHE

    before = {str(p): _sha(p) for p in (MANIFEST, RECON, FROZEN_SUMMARY, REGISTRATION, ACTIVATION)}
    _PARENT_CACHE.clear()
    old_env = os.environ.pop("TRADEBOT_DEMO_PUSH_E2E", None)
    argv = list(sys.argv)
    sys.argv = ["check_live_order_recovery_readiness.py"]
    try:
        code = main(["--native-root", str(NATIVE)])
    finally:
        sys.argv = argv
        if old_env is not None:
            os.environ["TRADEBOT_DEMO_PUSH_E2E"] = old_env
        _PARENT_CACHE.clear()
    assert code == 2
    after = {str(p): _sha(p) for p in (MANIFEST, RECON, FROZEN_SUMMARY, REGISTRATION, ACTIVATION)}
    assert after == before


def test_env_only_and_cli_only_stay_on_production_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    from small_paper.check_live_order_recovery_readiness import main
    from small_paper.demo_push_firewall import _PARENT_CACHE

    before = _sha(MANIFEST)
    _PARENT_CACHE.clear()
    monkeypatch.setenv("TRADEBOT_DEMO_PUSH_E2E", "1")
    monkeypatch.setattr(sys, "argv", ["check_live_order_recovery_readiness.py"])
    assert main(["--native-root", str(NATIVE)]) == 2

    monkeypatch.delenv("TRADEBOT_DEMO_PUSH_E2E", raising=False)
    monkeypatch.setattr(sys, "argv", ["paper_trade_checked_runner.py", "--demo-push-e2e"])
    _PARENT_CACHE.clear()
    assert main(["--native-root", str(NATIVE)]) == 2
    assert _sha(MANIFEST) == before


def test_both_flags_use_isolated_clean_authority_and_leave_history(armed_env: None) -> None:
    from small_paper.check_live_order_recovery_readiness import main

    before = {str(p): _sha(p) for p in (MANIFEST, RECON, FROZEN_SUMMARY, REGISTRATION)}
    code = main(["--native-root", str(NATIVE)])
    assert code == 0
    after = {str(p): _sha(p) for p in (MANIFEST, RECON, FROZEN_SUMMARY, REGISTRATION)}
    assert after == before


def test_demo_freeze_does_not_write_production_universe(armed_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import small_paper.day_fixed_am_registration as registration
    from small_paper.demo_push_firewall import install_inprocess_isolation

    monkeypatch.setenv("TRADEBOT_DEMO_PUSH_ISOLATED_ROOT", str(tmp_path / "isolated"))
    setattr(install_inprocess_isolation, "_installed", False)
    assert install_inprocess_isolation() is True
    prod = tmp_path / "production_native"
    prod.mkdir()
    symbols = [f"{7200 + i}" for i in range(50)]
    real_summary = _sha(FROZEN_SUMMARY) if FROZEN_SUMMARY.is_file() else ""
    real_reg = _sha(REGISTRATION)
    result = registration.freeze_same_day_am_universe(
        prod,
        "20260714",
        symbols=symbols,
        write_from_symbols=True,
    )
    assert result.get("ok") is True
    assert not (prod / "runtime").exists()
    assert (tmp_path / "isolated" / "runtime" / "same_day_am_frozen_universe_20260714.json").is_file()
    assert not (NATIVE / "runtime" / "same_day_am_frozen_universe_20260714.json").exists()
    assert not (NATIVE / "results" / "reports" / "same_day_am_frozen_universe_20260714.csv").exists()
    assert _sha(FROZEN_SUMMARY) == real_summary
    assert _sha(REGISTRATION) == real_reg
    body = json.loads(
        (tmp_path / "isolated" / "runtime" / "same_day_am_frozen_universe_20260714.json").read_text(
            encoding="utf-8"
        )
    )
    assert body["canonical_symbols"] == symbols
