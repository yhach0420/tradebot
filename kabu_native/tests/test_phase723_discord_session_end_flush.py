"""Phase723 — session-end Discord flush: enqueue ≠ sent."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from notify.discord_notification_audit import NotificationAudit
from notify.discord_notification_dedupe import DedupeStore
from notify.discord_notification_model import (
    ActualOrShadow,
    NotificationCategory,
    Severity,
    build_envelope,
)
from notify.discord_notification_router import reset_router_for_tests
from notify.discord_notification_worker import NotificationWorker
from small_paper.session_end_discord_delivery import (
    deliver_session_end_discord,
    resolve_session_id,
)


@pytest.fixture(autouse=True)
def _reset_router():
    reset_router_for_tests()
    yield
    reset_router_for_tests()


def _env(dedupe_key: str, nid: str = "n1"):
    env = build_envelope(
        category=NotificationCategory.SESSION_SUMMARY,
        severity=Severity.INFO,
        event_type="TEST",
        title="t",
        content="c",
        embeds=[],
        dedupe_key=dedupe_key,
        actual_or_shadow=ActualOrShadow.ACTUAL,
        source_module="test",
        ownership="PAPER_RUNTIME",
    )
    env.notification_id = nid
    return env


def test_resolve_session_id_never_empty(tmp_path: Path):
    assert resolve_session_id({}, output_dir=tmp_path / "live_session_122522") == "live_session_122522"
    assert resolve_session_id({"session_id": "sid-1"}) == "sid-1"
    assert "20260723" in resolve_session_id(
        {"trading_date": "20260723", "am_pm_session": {"kind": "pm"}}
    )
    assert resolve_session_id({})  # non-empty fallback


def test_enqueue_alone_is_queued_not_sent(tmp_path: Path):
    audit = NotificationAudit(tmp_path)
    worker = NotificationWorker(audit=audit, dedupe=DedupeStore(tmp_path / "d.jsonl"))
    # do not start worker — enqueue only
    env = _env("pm_summary|20260723")
    out = worker.enqueue(env, "https://example.invalid/webhook")
    assert out["status"] == "QUEUED"
    events = list((tmp_path / "results" / "notifications").rglob("notification_events.jsonl"))
    # NotificationAudit path may differ — check worker counters
    assert worker.sent == 0
    assert worker.queue_depth() == 1
    flush = worker.stop(flush_sec=0.2)
    assert flush["timed_out"] or flush["timed_out_count"] >= 1
    assert worker.sent == 0


def test_flush_completes_http_before_stop(tmp_path: Path):
    audit = NotificationAudit(tmp_path)
    dedupe = DedupeStore(tmp_path / "d.jsonl")
    worker = NotificationWorker(audit=audit, dedupe=dedupe, timeout_sec=2.0, max_retries=1)
    worker.start()
    env = _env("pm_summary|20260723", nid="n_flush")
    with patch("notify.discord_notification_worker.requests.post") as post:
        resp = MagicMock()
        resp.status_code = 204
        resp.text = ""
        resp.headers = {}
        post.return_value = resp
        worker.enqueue(env, "https://example.invalid/hook")
        flush = worker.stop(flush_sec=5.0)
    assert flush["remaining"] == 0
    assert flush["worker_alive"] is False
    assert worker.sent == 1
    assert dedupe.check("pm_summary|20260723")["allow"] is False
    assert dedupe.check("pm_summary|20260723")["previous"]["status"] == "SENT"


def test_two_enqueues_both_sent(tmp_path: Path):
    audit = NotificationAudit(tmp_path)
    worker = NotificationWorker(audit=audit, dedupe=DedupeStore(tmp_path / "d.jsonl"), max_retries=1)
    worker.start()
    with patch("notify.discord_notification_worker.requests.post") as post:
        resp = MagicMock()
        resp.status_code = 204
        resp.text = ""
        resp.headers = {}
        post.return_value = resp
        worker.enqueue(_env("pm_summary|20260723", "a"), "https://example.invalid/h")
        worker.enqueue(_env("20260723|live_session_x|PM|forward_shadow_bundle", "b"), "https://example.invalid/h")
        flush = worker.stop(flush_sec=5.0)
    assert worker.sent == 2
    assert flush["remaining"] == 0


def test_http_failure_is_failed(tmp_path: Path):
    audit = NotificationAudit(tmp_path)
    dedupe = DedupeStore(tmp_path / "d.jsonl")
    worker = NotificationWorker(audit=audit, dedupe=dedupe, max_retries=1, timeout_sec=1.0)
    worker.start()
    with patch("notify.discord_notification_worker.requests.post") as post:
        resp = MagicMock()
        resp.status_code = 500
        resp.text = "err"
        resp.headers = {}
        post.return_value = resp
        worker.enqueue(_env("pm_summary|fail", "f1"), "https://example.invalid/h")
        worker.stop(flush_sec=5.0)
    assert worker.sent == 0
    assert worker.failed >= 1
    assert dedupe.check("pm_summary|fail")["previous"]["status"] == "FAILED"


def test_http_timeout_status(tmp_path: Path):
    audit = NotificationAudit(tmp_path)
    dedupe = DedupeStore(tmp_path / "d.jsonl")
    worker = NotificationWorker(audit=audit, dedupe=dedupe, max_retries=1, timeout_sec=0.2)
    worker.start()
    with patch("notify.discord_notification_worker.requests.post") as post:
        post.side_effect = requests.Timeout("slow")
        worker.enqueue(_env("pm_summary|to", "t1"), "https://example.invalid/h")
        worker.stop(flush_sec=3.0)
    prev = dedupe.check("pm_summary|to").get("previous") or {}
    assert prev.get("status") == "TIMEOUT"
    assert worker.sent == 0


def test_flush_timeout_marks_remaining(tmp_path: Path):
    audit = NotificationAudit(tmp_path)
    dedupe = DedupeStore(tmp_path / "d.jsonl")
    worker = NotificationWorker(audit=audit, dedupe=dedupe, max_retries=1, timeout_sec=30.0)
    worker.start()

    def _slow(*_a, **_k):
        time.sleep(10.0)
        resp = MagicMock()
        resp.status_code = 204
        resp.text = ""
        resp.headers = {}
        return resp

    with patch("notify.discord_notification_worker.requests.post", side_effect=_slow):
        worker.enqueue(_env("pm_summary|slow1", "s1"), "https://example.invalid/h")
        worker.enqueue(_env("pm_summary|slow2", "s2"), "https://example.invalid/h")
        flush = worker.stop(flush_sec=0.3)
    assert flush["timed_out"] or flush["timed_out_count"] >= 1 or flush["remaining"] == 0
    # worker must not remain blocking
    assert flush["worker_alive"] is False or flush["status"] in ("STOPPED", "KILLED")


def test_deliver_does_not_mark_sent_before_http(tmp_path: Path):
    summary = {
        "trading_date": "20260723",
        "stop_reason": "afternoon_session_close",
        "am_pm_session": {"kind": "pm"},
        "session_id": "",
        "pbv2_rise5_shadow_enabled": True,
        "pbv2_rise5_shadow_block_count": 1,
        "pbv2_rise5_shadow_net_effect_yen": -1,
    }
    sess = tmp_path / "live_session_122522"
    sess.mkdir()
    discord = MagicMock()
    discord.active = True
    discord.cfg = MagicMock(send_daily_summary=True)

    with patch("small_paper.session_end_discord_delivery.notify_discord_session_end") as notify:
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
            # no SENT events on disk → must not claim sent
            out = deliver_session_end_discord(
                discord=discord,
                events=[],
                summary=summary,
                native_root=tmp_path,
                output_dir=sess,
                flush_sec=1.0,
            )
    notify.assert_called_once()
    worker.stop.assert_called_once()
    assert out["discord"] != "sent"
    assert out["ok"] is False
    assert out["session_id"] == "live_session_122522"
    assert "||" not in (out.get("expected_keys") or {}).get("shadow", "x")


def test_deliver_sent_only_after_http_audit(tmp_path: Path):
    day = "20260723"
    notif_dir = tmp_path / "results" / "notifications" / day
    notif_dir.mkdir(parents=True)
    events_path = notif_dir / "notification_events.jsonl"
    paper_key = f"pm_summary|{day}"
    shadow_key = f"{day}|live_session_122522|PM|forward_shadow_bundle"
    with events_path.open("w", encoding="utf-8") as fh:
        for key in (paper_key, shadow_key):
            fh.write(
                json.dumps(
                    {
                        "dedupe_key": key,
                        "status": "SENT",
                        "http_status": 204,
                        "notification_id": f"n_{key}",
                    }
                )
                + "\n"
            )
    summary = {
        "trading_date": day,
        "stop_reason": "afternoon_session_close",
        "am_pm_session": {"kind": "pm"},
        "session_id": "live_session_122522",
        "pbv2_rise5_shadow_enabled": True,
        "pbv2_rise5_shadow_block_count": 1,
    }
    sess = tmp_path / "live_session_122522"
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
    assert out["ok"] is True
    assert out["discord"] == "sent"
    assert out["submit"] == 0 and out["cancel"] == 0 and out["live_order"] == 0


def test_worker_flush_logs_start_done(tmp_path: Path):
    audit = NotificationAudit(tmp_path)
    worker = NotificationWorker(audit=audit, max_retries=1)
    worker.start()
    with patch("notify.discord_notification_worker.requests.post") as post:
        resp = MagicMock()
        resp.status_code = 204
        resp.text = ""
        resp.headers = {}
        post.return_value = resp
        worker.enqueue(_env("k|1"), "https://example.invalid/h")
        worker.stop(flush_sec=3.0)
    # find audit events
    files = list(tmp_path.rglob("notification_events.jsonl"))
    assert files
    text = files[0].read_text(encoding="utf-8")
    assert "WORKER_FLUSH_START" in text
    assert "WORKER_FLUSH_DONE" in text


def test_no_empty_session_id_in_shadow_key():
    from small_paper.session_end_discord_delivery import expected_session_end_dedupe_keys

    keys = expected_session_end_dedupe_keys(
        {
            "trading_date": "20260723",
            "am_pm_session": {"kind": "pm"},
            "stop_reason": "afternoon_session_close",
        },
        output_dir=Path("results/small_paper/20260723/live_session_122522"),
    )
    assert keys["shadow"]
    assert "||" not in keys["shadow"]
    assert "live_session_122522" in keys["shadow"]
