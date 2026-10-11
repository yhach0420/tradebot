"""X1 AM/PM Summary Discord payload cap. Observability only. No orders."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from notify.discord_notification_audit import NotificationAudit
from notify.discord_notification_dedupe import DedupeStore
from notify.discord_notification_model import (
    ActualOrShadow,
    DISCORD_EMBED_FIELD_LIMIT,
    NotificationCategory,
    Severity,
    build_envelope,
    sanitize_discord_embed,
)
from notify.discord_notification_router import reset_router_for_tests
from notify.discord_notification_worker import NotificationWorker
from notify.x1_discord_gate import (
    force_x1_discord_routing,
    reset_x1_discord_routing_for_tests,
    x1_summary_event,
    x1_summary_fields,
)
from small_paper.session_end_discord_delivery import (
    deliver_session_end_discord,
    expected_session_end_dedupe_keys,
)

AID = "FIXED_ENTRY_SUPPORT_PAPER_PRIMARY_ACTIVATION_V16"


@pytest.fixture(autouse=True)
def _reset_x1_and_router():
    reset_x1_discord_routing_for_tests()
    reset_router_for_tests()
    yield
    reset_x1_discord_routing_for_tests()
    reset_router_for_tests()


def _v16_summary(*, kind: str, session_id: str) -> dict:
    stop = "morning_session_close" if kind == "am" else "afternoon_session_close"
    return {
        "trading_date": "20261007",
        "stop_reason": stop,
        "am_pm_session": {"kind": kind},
        "session_id": session_id,
        "activation_id": AID,
        "execution_family": "X1_IMMEDIATE_ASK",
        "discord_source": "FixedSupportX1SessionExecutor",
        "summary_source_session_id": session_id,
        "full_pm_summary": kind == "pm",
        "SUMMARY_SCOPE": "FULL_PM" if kind == "pm" else "SEGMENT",
        "FULL_PM_VALID": kind == "pm",
        "OPERATIONALLY_INVALID": False,
        "x1_summary_class": "FINAL",
        "pm_day_aggregate": {"segment_n": 1, "entry": 12},
        "x1_executor": {
            "x1_entry_n": 12,
            "x1_exit_n": 12,
            "x1_open_n": 0,
            "x1_exit_pending_n": 0,
            "x1_cap_blocked_n": 3,
            "x1_same_symbol_blocked_n": 0,
            "x1_slot_release_n": 12,
            "x1_admission_denied_n": 0,
        },
        "x1_performance": {
            "trades_n": 12,
            "entry_n": 12,
            "exit_n": 12,
            "open_n": 0,
            "exit_pending_n": 0,
            "net_pnl_yen": 1000,
            "gross_profit_yen": 2000,
            "gross_loss_yen": -1000,
            "pf": 2.0,
            "win_n": 6,
            "loss_n": 5,
            "draw_n": 1,
            "avg_pnl_yen": 83.3,
            "median_pnl_yen": 50.0,
            "exit_reasons": {
                "BREAK_SUPPORT_FAILURE": {"n": 8, "pnl_yen": -800},
                "IMPULSE_EXHAUSTED": {"n": 3, "pnl_yen": 1500},
                "SESSION_FLAT": {"n": 1, "pnl_yen": 300},
            },
            "cap_blocked_n": 3,
            "same_symbol_blocked_n": 0,
            "slot_release_n": 12,
            "submit_cancel_live": "0/0/0",
            "activation_id": AID,
            "execution_family": "X1_IMMEDIATE_ASK",
        },
    }


def _raw_and_sanitized(summary: dict) -> tuple[int, int, dict]:
    raw = x1_summary_fields(summary)
    env = build_envelope(
        category=NotificationCategory.SESSION_SUMMARY,
        severity=Severity.INFO,
        event_type=x1_summary_event(summary),
        title="preview",
        embeds=[{"title": "preview", "fields": raw}],
        dedupe_key="preview",
        actual_or_shadow=ActualOrShadow.ACTUAL,
        source_module="test",
        ownership="PAPER_RUNTIME",
    )
    payload = env.discord_payload()
    fields = (payload.get("embeds") or [{}])[0].get("fields") or []
    return len(raw), len(fields), payload


def test_a_x1_am_summary_raw_over_25_discord_payload_at_most_25():
    raw_n, sent_n, payload = _raw_and_sanitized(
        _v16_summary(kind="am", session_id="20261007_am_live_session_081803")
    )
    assert raw_n > DISCORD_EMBED_FIELD_LIMIT
    assert sent_n <= DISCORD_EMBED_FIELD_LIMIT
    assert sent_n == DISCORD_EMBED_FIELD_LIMIT
    assert len((payload.get("embeds") or [{}])[0].get("fields") or []) <= 25


def test_b_x1_pm_summary_raw_over_25_discord_payload_at_most_25():
    raw_n, sent_n, payload = _raw_and_sanitized(
        _v16_summary(kind="pm", session_id="20261007_pm_live_session_122521")
    )
    assert x1_summary_event(_v16_summary(kind="pm", session_id="sid")) == "X1_PM_SUMMARY"
    assert raw_n > DISCORD_EMBED_FIELD_LIMIT
    assert sent_n <= DISCORD_EMBED_FIELD_LIMIT
    assert sent_n == DISCORD_EMBED_FIELD_LIMIT
    assert len((payload.get("embeds") or [{}])[0].get("fields") or []) <= 25


def test_sanitize_drops_fields_past_25_and_fills_empty_values():
    embed = {
        "title": "t",
        "fields": [{"name": f"f{i}", "value": "" if i == 0 else str(i), "inline": True} for i in range(29)],
    }
    out = sanitize_discord_embed(embed)
    assert len(out["fields"]) == 25
    assert out["fields"][0]["value"] == "—"
    assert "f25" not in {f["name"] for f in out["fields"]}


def test_c_http_204_with_sanitized_payload_at_most_25(tmp_path: Path):
    raw = [{"name": f"f{i}", "value": str(i), "inline": True} for i in range(29)]
    env = build_envelope(
        category=NotificationCategory.SESSION_SUMMARY,
        severity=Severity.INFO,
        event_type="X1_PM_SUMMARY",
        title="pm",
        embeds=[{"title": "pm", "fields": raw}],
        dedupe_key="x1-summary|20261007|X1_PM_SUMMARY|sid",
        actual_or_shadow=ActualOrShadow.ACTUAL,
        source_module="test",
        ownership="PAPER_RUNTIME",
    )
    audit = NotificationAudit(tmp_path)
    dedupe = DedupeStore(tmp_path / "d.jsonl")
    worker = NotificationWorker(audit=audit, dedupe=dedupe, max_retries=1)
    worker.start()
    posted: list[dict] = []
    with patch("notify.discord_notification_worker.requests.post") as post:
        resp = MagicMock()
        resp.status_code = 204
        resp.text = ""
        resp.headers = {}
        post.return_value = resp

        def _capture(*_a, **kwargs):
            posted.append(kwargs.get("json") or {})
            return resp

        post.side_effect = _capture
        worker.enqueue(env, "https://example.invalid/hook")
        worker.stop(flush_sec=5.0)
    assert worker.sent == 1
    assert posted
    fields = (posted[0].get("embeds") or [{}])[0].get("fields") or []
    assert len(fields) <= 25
    assert len(raw) > 25
    prev = dedupe.check("x1-summary|20261007|X1_PM_SUMMARY|sid").get("previous") or {}
    assert prev.get("status") == "SENT"


def test_d_verifier_tracks_actual_x1_dedupe_key():
    force_x1_discord_routing(True, activation_id=AID)
    am = expected_session_end_dedupe_keys(
        _v16_summary(kind="am", session_id="20261007_am_live_session_081803")
    )
    pm = expected_session_end_dedupe_keys(
        _v16_summary(kind="pm", session_id="20261007_pm_live_session_122521")
    )
    assert am["paper"] == "x1-summary|20261007|X1_AM_SUMMARY|20261007_am_live_session_081803"
    assert pm["paper"] == "x1-summary|20261007|X1_PM_SUMMARY|20261007_pm_live_session_122521"
    assert am["paper"].startswith("x1-summary|")
    assert pm["paper"].startswith("x1-summary|")
    assert "am_summary|" not in am["paper"]
    assert "pm_summary|" not in pm["paper"]


def _write_sent(path: Path, key: str, nid: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "dedupe_key": key,
                    "status": "SENT",
                    "http_status": 204,
                    "notification_id": nid,
                    "at": "2026-10-07T15:21:00+09:00",
                }
            )
            + "\n"
        )


def test_e_paper_sent_shadow_missing_does_not_fail_paper(tmp_path: Path):
    force_x1_discord_routing(True, activation_id=AID)
    summary = _v16_summary(kind="pm", session_id="20261007_pm_live_session_122521")
    keys = expected_session_end_dedupe_keys(summary)
    paper_key = keys["paper"]
    shadow_key = keys["shadow"]
    events_path = tmp_path / "results" / "notifications" / "20261007" / "notification_events.jsonl"
    _write_sent(events_path, paper_key, "n_paper")
    sess = tmp_path / "live_session_122521"
    sess.mkdir()
    discord = MagicMock()
    discord.active = True
    discord.cfg = MagicMock(send_daily_summary=True)
    with patch("small_paper.session_end_discord_delivery.notify_discord_session_end"):
        with patch("small_paper.session_end_discord_delivery.get_router") as gr:
            router = MagicMock()
            worker = MagicMock()
            worker.stop.return_value = {
                "remaining": 0,
                "timed_out": False,
                "worker_alive": False,
                "status": "STOPPED",
            }
            router.worker = worker
            gr.return_value = router
            out = deliver_session_end_discord(
                discord=discord,
                events=[],
                summary=summary,
                native_root=tmp_path,
                output_dir=sess,
                flush_sec=1.0,
            )
    assert out["expected_keys"]["paper"] == paper_key
    assert out["per_key"]["paper"]["ok"] is True
    assert out["per_key"]["paper"]["status"] == "SENT"
    assert out["per_key"]["paper"]["dedupe_key"] == paper_key
    assert out["per_key"]["shadow"]["status"] == "MISSING"
    assert out["per_key"]["shadow"]["ok"] is False
    assert out["per_key"]["shadow"]["dedupe_key"] == shadow_key
    assert out["ok"] is True
    assert out["discord"] == "sent"


def test_e_shadow_missing_is_separate_status_from_paper_fail(tmp_path: Path):
    force_x1_discord_routing(True, activation_id=AID)
    summary = _v16_summary(kind="am", session_id="20261007_am_live_session_081803")
    sess = tmp_path / "live_session_081803"
    sess.mkdir()
    discord = MagicMock()
    discord.active = True
    discord.cfg = MagicMock(send_daily_summary=True)
    with patch("small_paper.session_end_discord_delivery.notify_discord_session_end"):
        with patch("small_paper.session_end_discord_delivery.get_router") as gr:
            router = MagicMock()
            worker = MagicMock()
            worker.stop.return_value = {
                "remaining": 0,
                "timed_out": False,
                "worker_alive": False,
                "status": "STOPPED",
            }
            router.worker = worker
            gr.return_value = router
            out = deliver_session_end_discord(
                discord=discord,
                events=[],
                summary=summary,
                native_root=tmp_path,
                output_dir=sess,
                flush_sec=1.0,
            )
    assert out["per_key"]["paper"]["status"] == "MISSING"
    assert out["per_key"]["shadow"]["status"] == "MISSING"
    assert out["ok"] is False
    assert out["discord"] != "sent"
