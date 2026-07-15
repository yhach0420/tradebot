"""Phase687W10A — Shadow Summary runtime hook (AM/PM finalize)."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from notify.discord_notification_model import NotificationCategory
from notify.discord_notification_router import reset_router_for_tests
from small_paper.discord_notifier import notify_discord_session_end
from small_paper.shadow_summary_runtime_hook import (
    build_shadow_summary_content,
    enqueue_shadow_summary_for_session,
    np_logger_band,
    session_kind_am_pm,
)


@pytest.fixture(autouse=True)
def _reset_router():
    reset_router_for_tests()
    yield
    reset_router_for_tests()


def _am_summary(**extra):
    base = {
        "trading_date": "20260711",
        "session_id": "sess-am-1",
        "am_pm_session": {"kind": "am"},
        "canonical_summary": {"total_pnl_yen": 1234, "entry_count": 2, "exit_count": 1},
        "ihc_union_shadow_block_count": 3,
        "np_pre_entry_feature_logger_enabled": True,
        "forward_sessions": 3,
        "execution_policy_shadow_count": 1,
        # Phase687W25C-R2: Discord posts only enabled shadows with count > 0
        "pbv2_rise5_shadow_enabled": True,
        "pbv2_rise5_shadow_block_count": 3,
        "pbv2_rise5_shadow_net_effect_yen": -100,
    }
    base.update(extra)
    return base


def _pm_summary(**extra):
    s = _am_summary(**extra)
    s["am_pm_session"] = {"kind": "pm"}
    s["session_id"] = "sess-pm-1"
    s["forward_sessions"] = 7
    return s


def test_session_kind_am_pm():
    assert session_kind_am_pm({"am_pm_session": {"kind": "am"}}) == "am"
    assert session_kind_am_pm({"am_pm_session": {"kind": "pm"}}) == "pm"
    assert session_kind_am_pm({}) == ""


def test_np_logger_bands():
    assert np_logger_band(0) == "DATA COLLECTION ONLY"
    assert np_logger_band(4) == "DATA COLLECTION ONLY"
    assert np_logger_band(5) == "RULE DISCOVERY NOT ALLOWED"
    assert np_logger_band(9) == "RULE DISCOVERY NOT ALLOWED"
    assert np_logger_band(10) == "RULE DISCOVERY REVIEW ALLOWED"


def test_content_has_required_fields_and_no_adoption():
    text = build_shadow_summary_content(_am_summary(), am_pm="am", artifact_path="/tmp/out", artifact_hash="abc")
    assert text.startswith("[SHADOW SUMMARY - AM]")
    for needle in (
        "名称:",
        "対象件数:",
        "hypothetical fills:",
        "hypothetical PnL yen_100:",
        "actual overlap:",
        "data completeness:",
        "forward sessions:",
        "source artifact:",
        "ADOPTION STATUS: NOT ADOPTED",
        "DATA COLLECTION ONLY",
        "observation only",
    ):
        assert needle in text
    assert "採用可能" not in text
    assert "--- I/H/C ---" in text or "ihc" in text.lower() or "IHC" in text
    assert "--- Phase687 NP Logger ---" in text


def test_inactive_shadow_skipped(tmp_path: Path):
    summary = _am_summary(
        pbv2_rise5_shadow_enabled=False,
        pbv2_rise5_shadow_block_count=0,
    )
    out = enqueue_shadow_summary_for_session(summary, native_root=tmp_path, output_dir=tmp_path)
    assert out.get("status") == "SKIPPED_NO_ACTIVE_SHADOW"
    assert out.get("queued") is False


def test_am_enqueue_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KABU_DISCORD_RESEARCH_WEBHOOK_URL", "https://discord.example/research")
    monkeypatch.setenv("KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL", "")
    monkeypatch.setenv("KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL", "")
    with patch("notify.discord_notification_worker.requests.post") as post:
        post.return_value = MagicMock(status_code=204, text="", headers={})
        out1 = enqueue_shadow_summary_for_session(_am_summary(), native_root=tmp_path, output_dir=tmp_path)
        assert out1.get("queued") is True or out1.get("status") in ("QUEUED", "SENT", "OK")
        out2 = enqueue_shadow_summary_for_session(_am_summary(), native_root=tmp_path, output_dir=tmp_path)
        assert out2.get("status") == "DEDUPED"
        assert out2.get("queued") is False
        time.sleep(0.3)
        reset_router_for_tests()


def test_pm_enqueue_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KABU_DISCORD_RESEARCH_WEBHOOK_URL", "https://discord.example/research")
    with patch("notify.discord_notification_worker.requests.post") as post:
        post.return_value = MagicMock(status_code=204, text="", headers={})
        out1 = enqueue_shadow_summary_for_session(_pm_summary(), native_root=tmp_path, output_dir=tmp_path)
        assert out1.get("queued") is True or out1.get("status") in ("QUEUED", "SENT", "OK")
        assert out1.get("am_pm") == "pm"
        out2 = enqueue_shadow_summary_for_session(_pm_summary(), native_root=tmp_path, output_dir=tmp_path)
        assert out2.get("status") == "DEDUPED"
        time.sleep(0.3)
        reset_router_for_tests()


def test_daily_suppressed(tmp_path: Path):
    summary = _am_summary()
    summary["am_pm_session"] = {"kind": "daily"}
    out = enqueue_shadow_summary_for_session(summary, native_root=tmp_path)
    assert out.get("status") == "SKIPPED_DAILY"
    assert out.get("queued") is False


def test_webhook_missing_no_trade_notify_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KABU_DISCORD_RESEARCH_WEBHOOK_URL", "")
    monkeypatch.setenv("KABU_SHADOW_DISCORD_WEBHOOK_URL", "")
    monkeypatch.setenv("KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL", "https://discord.example/trade-notify")
    out = enqueue_shadow_summary_for_session(_am_summary(), native_root=tmp_path, output_dir=tmp_path)
    assert out.get("status") == "SKIPPED_WEBHOOK_NOT_CONFIGURED"
    assert out.get("queued") is False


def test_artifact_missing_does_not_block(tmp_path: Path):
    summary = {
        "trading_date": "20260711",
        "session_id": "x",
        "am_pm_session": {"kind": "am"},
    }
    out = enqueue_shadow_summary_for_session(summary, native_root=tmp_path)
    assert out.get("status") == "SHADOW_SUMMARY_ARTIFACT_NOT_READY"
    assert out.get("queued") is False


def test_hash_update_no_auto_resend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KABU_DISCORD_RESEARCH_WEBHOOK_URL", "https://discord.example/research")
    with patch("notify.discord_notification_worker.requests.post") as post:
        post.return_value = MagicMock(status_code=204, text="", headers={})
        s1 = _am_summary(ihc_union_shadow_block_count=3)
        out1 = enqueue_shadow_summary_for_session(s1, native_root=tmp_path, output_dir=tmp_path)
        assert out1.get("queued") is True or out1.get("status") in ("QUEUED", "SENT", "OK")
        s2 = _am_summary(ihc_union_shadow_block_count=99)
        out2 = enqueue_shadow_summary_for_session(s2, native_root=tmp_path, output_dir=tmp_path)
        assert out2.get("status") == "UPDATE_NO_AUTO_RESEND"
        assert out2.get("queued") is False
        time.sleep(0.3)
        reset_router_for_tests()


def test_notify_session_end_calls_hook_fail_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KABU_DISCORD_RESEARCH_WEBHOOK_URL", "")
    monkeypatch.setenv("KABU_SHADOW_DISCORD_WEBHOOK_URL", "")
    # Discord timeout / hang must not block Paper finalize
    with patch(
        "small_paper.shadow_summary_runtime_hook.enqueue_shadow_summary_for_session",
        side_effect=lambda *a, **k: (_ for _ in ()).throw(TimeoutError("discord hang")),
    ):
        # Should not raise
        notify_discord_session_end(
            None,
            events=[],
            summary=_am_summary(),
            native_root=tmp_path,
            output_dir=tmp_path,
        )


def test_actual_summary_fields_exclude_hypothetical(monkeypatch: pytest.MonkeyPatch):
    from small_paper.discord_notifier import SmallPaperDiscordConfig, SmallPaperDiscordNotifier

    monkeypatch.setattr(
        "small_paper.discord_notifier.get_cached_symbol_name_map",
        lambda *a, **k: {},
    )
    cfg = SmallPaperDiscordConfig(enabled=True, observer_only=True, send_daily_summary=True)
    notifier = SmallPaperDiscordNotifier(cfg, profile="p", entry_profile="e")
    summary = _am_summary()
    summary["total_pnl_yen"] = 999
    fields = notifier._production_summary_fields(events=[], summary=summary)
    assert fields is not None
    blob = "\n".join(f"{f.get('name')}\n{f.get('value')}" for f in fields)
    assert "Research Shadow" not in blob
    assert "hypothetical PnL" not in blob.lower()
    assert "採用可能" not in blob


def test_shadow_content_excludes_actual_total_pnl():
    text = build_shadow_summary_content(_am_summary(), am_pm="pm")
    assert "actual total" not in text.lower()
    assert "canonical total" not in text.lower()
    assert "total_pnl_yen" not in text
    assert NotificationCategory.RESEARCH_SHADOW.value == "RESEARCH_SHADOW"


def test_ownership_single_module():
    assert enqueue_shadow_summary_for_session.__module__.endswith("shadow_summary_runtime_hook")
