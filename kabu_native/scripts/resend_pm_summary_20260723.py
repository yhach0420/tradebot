"""Resend 2026-07-23 PM Paper Summary + Shadow Summary with flush (HTTP-confirmed).

Canonical Summary is NOT recomputed. Paper / observe-only. orders=0.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NATIVE / "src"))

DAY = "20260723"
SESSION = "live_session_122522"
SESSION_DIR = NATIVE / "results" / "small_paper" / DAY / SESSION
REPORT_DIR = NATIVE / "results" / "reports" / "phase_723_discord_session_end_flush"


def _latest(dedupe_key: str) -> dict:
    path = NATIVE / "results" / "notifications" / DAY / "notification_events.jsonl"
    last = {}
    if not path.is_file():
        return last
    for line in path.open(encoding="utf-8"):
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("dedupe_key") == dedupe_key:
            last = row
    return last


def main() -> int:
    from notify.discord_notification_router import get_router, reset_router_for_tests
    from small_paper.discord_notifier import SmallPaperDiscordConfig, SmallPaperDiscordNotifier
    from small_paper.session_end_discord_delivery import (
        deliver_session_end_discord,
        expected_session_end_dedupe_keys,
        resolve_session_id,
    )

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    summary_path = SESSION_DIR / "small_paper_summary_pm.json"
    if not summary_path.is_file():
        summary_path = SESSION_DIR / "small_paper_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    # Do not recompute — only ensure session_id for dedupe
    sid = resolve_session_id(summary, output_dir=SESSION_DIR)
    summary = dict(summary)
    summary["session_id"] = sid
    expected = expected_session_end_dedupe_keys(summary, output_dir=SESSION_DIR, session_id=sid)

    # Skip if already HTTP SENT
    already = {}
    need_send = False
    for label, key in expected.items():
        row = _latest(key)
        st = str(row.get("status") or "")
        http = int(row.get("http_status") or 0)
        already[label] = {"dedupe_key": key, "status": st, "http_status": row.get("http_status")}
        if not (st == "SENT" and 200 <= http < 300):
            need_send = True
    # Also treat legacy empty-session shadow QUEUED as needing the corrected key send
    legacy_shadow = _latest(f"{DAY}||PM|forward_shadow_bundle")
    if str(legacy_shadow.get("status") or "") == "QUEUED":
        need_send = True

    if not need_send:
        report = {
            "verdict": "DISCORD_SESSION_END_FLUSH_FIXED",
            "action": "skipped_already_sent",
            "already": already,
            "submit": 0,
            "cancel": 0,
            "live_order": 0,
        }
        (REPORT_DIR / "pm_resend.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    reset_router_for_tests()
    cfg = SmallPaperDiscordConfig(enabled=True, send_daily_summary=True, observer_only=True)
    discord = SmallPaperDiscordNotifier(
        cfg,
        profile=str(summary.get("profile") or "session_end"),
        entry_profile=str(summary.get("entry_profile") or "session_end"),
        policy_label=str(summary.get("policy_label") or "paper"),
        min_continuation_quality=float(summary.get("min_continuation_quality") or 0.0),
    )
    delivery = deliver_session_end_discord(
        discord=discord,
        events=[],
        summary=summary,
        native_root=NATIVE,
        output_dir=SESSION_DIR,
        flush_sec=25.0,
        session_id=sid,
    )
    # Re-read audit for final HTTP proof
    final = {label: _latest(key) for label, key in expected.items()}
    router = get_router(NATIVE)
    worker_status = router.worker.status()
    report = {
        "verdict": (
            "DISCORD_SESSION_END_FLUSH_FIXED"
            if delivery.get("ok") and delivery.get("discord") == "sent"
            else "DISCORD_SESSION_END_STILL_BROKEN"
        ),
        "delivery": delivery,
        "final_audit": {
            k: {
                "status": v.get("status"),
                "http_status": v.get("http_status"),
                "dedupe_key": v.get("dedupe_key"),
                "notification_id": v.get("notification_id"),
                "at": v.get("at"),
            }
            for k, v in final.items()
        },
        "already_before": already,
        "legacy_shadow_queued": legacy_shadow.get("status"),
        "worker_status": worker_status,
        "seal_status": json.loads((SESSION_DIR / "session_seal.json").read_text(encoding="utf-8")).get(
            "session_seal_status"
        )
        if (SESSION_DIR / "session_seal.json").is_file()
        else None,
        "submit": 0,
        "cancel": 0,
        "live_order": 0,
        "canonical_recomputed": False,
    }
    (REPORT_DIR / "pm_resend.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report["verdict"] == "DISCORD_SESSION_END_FLUSH_FIXED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
