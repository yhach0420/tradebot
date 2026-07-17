"""Phase687W34 — PM session not started / AM Summary DEDUPE root-fix tests."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from api.kabu_register import (
    clear_paper_register_state,
    clear_register_before_session,
    load_paper_register_state,
    register_symbols_cleared,
    resolve_native_root_for_register_state,
    save_paper_register_state,
)
from small_paper.discord_message_builder import summary_notification_labels
from small_paper.discord_notifier import SmallPaperDiscordConfig, SmallPaperDiscordNotifier
from small_paper.shadow_summary_runtime_hook import session_kind_am_pm


def test_resolve_native_root_from_repo(tmp_path: Path):
    repo = tmp_path / "tradebotfile"
    native = repo / "kabu_native"
    (native / "src" / "api").mkdir(parents=True)
    assert resolve_native_root_for_register_state(repo) == native
    assert resolve_native_root_for_register_state(native) == native


def test_clear_paper_register_state_blocks_reuse(tmp_path: Path):
    specs = [(f"{1000 + i}", 1) for i in range(50)]
    save_paper_register_state(tmp_path, symbols_spec=specs, regist_num=50, trading_date="20990716")
    clear_paper_register_state(tmp_path, reason="test")
    st = load_paper_register_state(tmp_path)
    assert st.get("symbol_count") == 0
    assert st.get("cleared") is True

    class Push:
        def __init__(self) -> None:
            self.puts = 0

        def unregister_all(self):
            return {"RegistNum": 0}

        def register(self, specs_in):
            self.puts += 1
            return {
                "RegistNum": len(specs_in),
                "Symbols": [{"Symbol": s, "Exchange": ex} for s, ex in specs_in],
            }

    push = Push()
    out = register_symbols_cleared(
        push,
        specs,
        native_root=tmp_path,
        trading_date="20990716",
        settle_sec=0.0,
        allow_reuse_if_match=True,
        clear_first=True,
    )
    assert out.get("ok")
    assert not out.get("reused_existing")
    assert push.puts == 1


def test_clear_register_before_session_invalidates_sot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    native = tmp_path / "kabu_native"
    (native / "src" / "api").mkdir(parents=True)
    (native / "runtime").mkdir(parents=True)
    specs = [(f"{1000 + i}", 1) for i in range(50)]
    save_paper_register_state(native, symbols_spec=specs, regist_num=50, trading_date="20990716")

    class FakePush:
        def unregister_all(self):
            return {"RegistNum": 0}

    monkeypatch.setattr(
        "api.kabu_register.push_client_from_repo",
        lambda _root: (FakePush(), None, "http://x"),
    )
    monkeypatch.setattr(
        "api.kabu_register.unregister_all_until_zero",
        lambda _push, **_kw: {"ok": True, "regist_num": 0},
    )
    out = clear_register_before_session(tmp_path)
    assert out.get("ok")
    assert out.get("paper_register_state_cleared")
    st = load_paper_register_state(native)
    assert int(st.get("symbol_count", -1)) == 0
    assert st.get("cleared") is True


def test_summary_labels_need_am_pm_session():
    tag, title = summary_notification_labels({"am_pm_session": {"kind": "am"}})
    assert tag == "AM Summary"
    assert "AM" in title
    tag2, _ = summary_notification_labels({})
    assert tag2 == "Daily Summary"
    assert session_kind_am_pm({"am_pm_session": {"kind": "pm"}}) == "pm"
    assert session_kind_am_pm({}) == ""


def test_notify_daily_summary_day_scoped_dedupe_key(monkeypatch: pytest.MonkeyPatch):
    posted: list[dict] = []

    cfg = SmallPaperDiscordConfig(enabled=True, send_daily_summary=True)
    n = SmallPaperDiscordNotifier(
        cfg,
        profile="momentum_volume_v13_combined",
        entry_profile="momentum_volume_v2",
    )
    n._trade_webhook_url = "https://example.invalid/webhook"
    n._legacy_webhook_url = "https://example.invalid/webhook"

    def _fake_post(self, **kwargs):
        posted.append(kwargs)
        return True

    monkeypatch.setattr(SmallPaperDiscordNotifier, "_post", _fake_post)
    monkeypatch.setattr(
        SmallPaperDiscordNotifier,
        "_production_summary_embed",
        lambda self, **kw: {
            "title": "【AM Summary】",
            "fields": [{"name": "x", "value": "y"}],
            "description": "d",
            "footer": "f",
            "color": 0x805AD5,
        },
    )
    ok = n.notify_daily_summary(
        events=[],
        summary={"am_pm_session": {"kind": "am"}, "trading_date": "20260716", "accepted_count": 1},
    )
    assert ok is True
    assert posted
    assert posted[0]["dedupe_key"] == "am_summary|20260716"
    assert posted[0]["event_tag"] == "AM Summary"


def test_w34_false_reuse_scenario_reproduction(tmp_path: Path):
    """Reproduce 20260716: after AM clear, identical PM desired must NOT reuse."""
    specs = [(f"{1000 + i}", 1) for i in range(50)]
    # AM success saved SoT
    save_paper_register_state(tmp_path, symbols_spec=specs, regist_num=50, trading_date="20260716")
    # Orchestrator clear after AM / before PM
    clear_paper_register_state(tmp_path, reason="after_am_session")

    class Push:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def unregister_all(self):
            self.calls.append("unregister")
            return {"RegistNum": 0}

        def register(self, specs_in):
            self.calls.append(f"register:{len(specs_in)}")
            return {
                "RegistNum": len(specs_in),
                "Symbols": [{"Symbol": s, "Exchange": ex} for s, ex in specs_in],
            }

    push = Push()
    out = register_symbols_cleared(
        push,
        specs,
        native_root=tmp_path,
        trading_date="20260716",
        settle_sec=0.0,
        allow_reuse_if_match=True,
    )
    assert out.get("reused_existing") is not True
    assert any(c.startswith("register:50") for c in push.calls)
