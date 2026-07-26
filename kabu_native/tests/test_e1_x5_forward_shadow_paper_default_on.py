"""E1_X5 Forward Shadow — Paper default ON enablement tests."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from small_paper.e1_x5_forward_shadow import (
    CAP,
    ENV_KEY,
    GIVEBACK,
    MAX_HOLD_SEC,
    SPREAD_MAX_BPS,
    STOP_BPS,
    TARGET_BPS,
    THRESHOLD,
    TRAIL_ARM_BPS,
    E1X5ForwardShadowSession,
    emit_e1_x5_forward_shadow_startup_once,
    format_e1_x5_forward_shadow_startup_lines,
    resolve_e1_x5_forward_shadow_enabled,
    resolve_e1_x5_forward_shadow_from_runtime,
)
from small_paper.forward_observer_defaults import PAPER_RUNTIME_ENV

REPO = Path(__file__).resolve().parents[2].parent
if not (REPO / "run_paper_trade.bat").is_file():
    REPO = Path(r"C:\Users\yhach\Documents\tradebotfile")


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv(ENV_KEY, raising=False)
    monkeypatch.delenv(PAPER_RUNTIME_ENV, raising=False)
    monkeypatch.delenv("LIVE_TRADING", raising=False)
    monkeypatch.delenv("KABU_LIVE_RUNTIME", raising=False)
    import small_paper.e1_x5_forward_shadow as mod

    monkeypatch.setattr(mod, "_startup_emitted", False)


def test_1_paper_unset_default_on():
    d = resolve_e1_x5_forward_shadow_enabled(is_paper_runtime=True, env_value=None)
    assert d.enabled is True
    assert d.reason == "PAPER_DEFAULT_ON"
    assert d.paper_runtime is True


def test_2_paper_empty_default_on():
    d = resolve_e1_x5_forward_shadow_enabled(is_paper_runtime=True, env_value="")
    assert d.enabled is True
    assert d.reason == "PAPER_DEFAULT_ON"


def test_3_paper_env_1():
    d = resolve_e1_x5_forward_shadow_enabled(is_paper_runtime=True, env_value="1")
    assert d.enabled is True
    assert d.reason == "PAPER_ENV_ON"


def test_4_paper_env_true():
    d = resolve_e1_x5_forward_shadow_enabled(is_paper_runtime=True, env_value="true")
    assert d.enabled is True
    assert d.reason == "PAPER_ENV_ON"


def test_5_paper_env_0():
    d = resolve_e1_x5_forward_shadow_enabled(is_paper_runtime=True, env_value="0")
    assert d.enabled is False
    assert d.reason == "PAPER_ENV_OFF"


def test_6_paper_env_false():
    d = resolve_e1_x5_forward_shadow_enabled(is_paper_runtime=True, env_value="false")
    assert d.enabled is False
    assert d.reason == "PAPER_ENV_OFF"


def test_7_paper_invalid_forced_off():
    d = resolve_e1_x5_forward_shadow_enabled(is_paper_runtime=True, env_value="maybe")
    assert d.enabled is False
    assert d.reason == "INVALID_ENV_FORCED_OFF"


def test_8_non_paper_unset():
    d = resolve_e1_x5_forward_shadow_enabled(is_paper_runtime=False, env_value=None)
    assert d.enabled is False
    assert d.reason == "NON_PAPER_FORCED_OFF"


def test_9_non_paper_env_1():
    d = resolve_e1_x5_forward_shadow_enabled(is_paper_runtime=False, env_value="1")
    assert d.enabled is False
    assert d.reason == "NON_PAPER_FORCED_OFF"


def test_10_enabled_order_api_zero(monkeypatch):
    monkeypatch.setenv(PAPER_RUNTIME_ENV, "1")
    sess = E1X5ForwardShadowSession.maybe_create(emit_startup=False)
    assert sess.enabled is True
    s = sess.summary()
    assert s["submit"] == 0 and s["cancel"] == 0 and s["live_order"] == 0
    assert s["order_api"] == "disabled"
    # Session never imports/calls order APIs
    assert not hasattr(sess, "submit_order")
    assert not hasattr(sess, "cancel_order")


def test_11_pbv2_cap_impact_none(monkeypatch):
    monkeypatch.setenv(PAPER_RUNTIME_ENV, "1")
    sess = E1X5ForwardShadowSession.maybe_create(emit_startup=False)
    assert sess.summary()["pbv2_cap_impact"] == "none"
    assert CAP == 5  # independent CAP5 constant; not PBv2


def test_12_fixed_entry_exit_spec_unchanged():
    assert abs(THRESHOLD - 0.48256067040851486) < 1e-15
    assert SPREAD_MAX_BPS == 5.0
    assert STOP_BPS == -15.0
    assert TRAIL_ARM_BPS == 20.0
    assert GIVEBACK == 0.40
    assert TARGET_BPS == 50.0
    assert MAX_HOLD_SEC == 300.0


def test_13_runtime_uses_kabu_paper_runtime(monkeypatch):
    monkeypatch.setenv(PAPER_RUNTIME_ENV, "1")
    d = resolve_e1_x5_forward_shadow_from_runtime()
    assert d.enabled is True and d.reason == "PAPER_DEFAULT_ON"
    monkeypatch.setenv(ENV_KEY, "1")
    monkeypatch.delenv(PAPER_RUNTIME_ENV, raising=False)
    d2 = resolve_e1_x5_forward_shadow_from_runtime()
    assert d2.enabled is False and d2.reason == "NON_PAPER_FORCED_OFF"


def test_live_force_off_even_with_paper_flag(monkeypatch):
    monkeypatch.setenv(PAPER_RUNTIME_ENV, "1")
    monkeypatch.setenv(ENV_KEY, "1")
    monkeypatch.setenv("LIVE_TRADING", "1")
    d = resolve_e1_x5_forward_shadow_from_runtime()
    assert d.enabled is False
    assert d.reason == "NON_PAPER_FORCED_OFF"


def test_startup_log_enabled_and_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv(PAPER_RUNTIME_ENV, "1")
    buf = io.StringIO()
    path = tmp_path / "e1x5_startup.txt"
    d_on = resolve_e1_x5_forward_shadow_from_runtime()
    lines = emit_e1_x5_forward_shadow_startup_once(d_on, stream=buf, save_path=path, force=True)
    text = "\n".join(lines)
    assert "E1_X5_FORWARD_SHADOW: ENABLED" in text
    assert "reason: PAPER_DEFAULT_ON" in text
    assert "order_api: disabled" in text
    assert "pbv2_cap_impact: none" in text
    assert path.read_text(encoding="utf-8").startswith("E1_X5_FORWARD_SHADOW: ENABLED")

    monkeypatch.setenv(ENV_KEY, "0")
    d_off = resolve_e1_x5_forward_shadow_from_runtime()
    lines_off = format_e1_x5_forward_shadow_startup_lines(d_off)
    assert lines_off[0] == "E1_X5_FORWARD_SHADOW: DISABLED"
    assert "PAPER_ENV_OFF" in lines_off[1]


def test_paper_mock_preflight_default_on_then_off(monkeypatch, tmp_path):
    """Safe mock runtime: unset → ENABLED PAPER_DEFAULT_ON; =0 → DISABLED PAPER_ENV_OFF."""
    monkeypatch.setenv(PAPER_RUNTIME_ENV, "1")
    monkeypatch.delenv(ENV_KEY, raising=False)
    save = tmp_path / "on.txt"
    sess = E1X5ForwardShadowSession.maybe_create(save_path=save)
    assert sess.enabled is True
    assert sess.enable_decision is not None
    assert sess.enable_decision.reason == "PAPER_DEFAULT_ON"
    assert "ENABLED" in save.read_text(encoding="utf-8")
    assert "order_api: disabled" in save.read_text(encoding="utf-8")

    monkeypatch.setenv(ENV_KEY, "0")
    import small_paper.e1_x5_forward_shadow as mod

    monkeypatch.setattr(mod, "_startup_emitted", False)
    save2 = tmp_path / "off.txt"
    sess2 = E1X5ForwardShadowSession.maybe_create(save_path=save2)
    assert sess2.enabled is False
    assert sess2.enable_decision.reason == "PAPER_ENV_OFF"
    assert "DISABLED" in save2.read_text(encoding="utf-8")


def test_bat_documents_paper_default_on_without_forcing_env():
    bat = REPO / "run_paper_trade.bat"
    text = bat.read_text(encoding="utf-8", errors="ignore")
    assert "KABU_PAPER_RUNTIME" in text
    assert "E1_X5_FORWARD_SHADOW" in text
    # Must not force-set to 1 (would change reason to PAPER_ENV_ON)
    assert "if not defined E1_X5_FORWARD_SHADOW set E1_X5_FORWARD_SHADOW=1" not in text
