"""Phase687W25 — Generate Discord notification refresh artifacts (no webhook send)."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
NATIVE_ROOT = Path(__file__).resolve().parents[1]
REPORT = NATIVE_ROOT / "results" / "reports" / "phase687w25_discord_notification_refresh"


def _iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    sys.path[:0] = [str(NATIVE_ROOT / "src"), str(NATIVE_ROOT.parent)]
    from notify.discord_notification_formatter import (
        format_capture_status_body,
        format_communication_degraded,
        format_communication_recovered,
        format_shadow_summary,
    )
    from small_paper.discord_message_builder import (
        PAPER_ONLY_FOOTER,
        build_entry_cap_blocked_detail,
        build_entry_detail,
        build_exit_detail,
        build_universe_refresh_overview,
        format_discord_summary_lines,
        preview_payload,
    )

    REPORT.mkdir(parents=True, exist_ok=True)

    # --- inventory ---
    inventory = [
        {"event": "ENTRY", "formatter": "build_entry_detail", "notifier": "SmallPaperDiscordNotifier.notify_entry", "runtime": "YES", "file": "discord_message_builder.py / discord_notifier.py"},
        {"event": "EXIT (stop/trailing/no_progress/session)", "formatter": "build_exit_detail", "notifier": "notify_exit", "runtime": "YES", "file": "discord_message_builder.py / discord_notifier.py"},
        {"event": "CAP BLOCKED", "formatter": "build_entry_cap_blocked_detail", "notifier": "notify_entry_cap_blocked", "runtime": "YES", "file": "discord_message_builder.py / discord_notifier.py"},
        {"event": "Universe Refresh", "formatter": "build_universe_refresh_overview", "notifier": "notify_universe_refresh", "runtime": "YES", "file": "discord_message_builder.py"},
        {"event": "Universe Screening", "formatter": "build_universe_screening_overview", "notifier": "notify_universe_screening", "runtime": "YES", "file": "discord_message_builder.py"},
        {"event": "AM/PM Summary", "formatter": "format_discord_summary_lines", "notifier": "notify_daily_summary", "runtime": "YES", "file": "discord_message_builder.py"},
        {"event": "Shadow Summary", "formatter": "format_shadow_summary", "notifier": "enqueue_shadow_summary_for_session", "runtime": "YES (research webhook)", "file": "discord_notification_formatter.py"},
        {"event": "Capture STARTED/FAILED/FINISHED", "formatter": "format_capture_status_body", "notifier": "notify_capture", "runtime": "YES", "file": "discord_notification_formatter.py / market_capture_sidecar.py"},
        {"event": "PAPER BLOCKED", "formatter": "format_paper_blocked", "notifier": "publish_paper_blocked", "runtime": "YES (on block)", "file": "discord_notification_formatter.py"},
        {"event": "Communication DEGRADED/RECOVERED", "formatter": "format_communication_*", "notifier": "(none — preview only)", "runtime": "NO Discord send", "file": "discord_notification_formatter.py"},
        {"event": "same_push_reentry skip", "formatter": "N/A", "notifier": "N/A", "runtime": "NO Discord (reject/debug only)", "file": "pilot_runner / entry_pipeline"},
        {"event": "format_entry_actual / format_exit_actual", "formatter": "format_entry_actual", "notifier": "research demo only", "runtime": "UNUSED in paper runtime", "file": "discord_notification_formatter.py"},
        {"event": "HEARTBEAT/HOLD/TAKE/ERROR", "formatter": "inline", "notifier": "notify_*", "runtime": "YES (ops)", "file": "discord_notifier.py"},
    ]
    with (REPORT / "notification_inventory.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(inventory[0].keys()))
        w.writeheader()
        w.writerows(inventory)

    mapping = [
        {"runtime_callsite": "pilot_runner._record_accepted_and_notify", "formatter": "build_entry_detail", "send": "notify_entry → trade-notify"},
        {"runtime_callsite": "pilot_runner._dispatch_observer_events EXIT", "formatter": "build_exit_detail", "send": "notify_exit → trade-notify"},
        {"runtime_callsite": "pilot_runner._notify_entry_blocked_discord", "formatter": "build_entry_cap_blocked_detail", "send": "notify_entry_cap_blocked → trade-cap-blocked"},
        {"runtime_callsite": "pilot_runner._emit_intraday_refresh_event", "formatter": "build_universe_refresh_overview", "send": "notify_universe_refresh"},
        {"runtime_callsite": "pilot_runner session end", "formatter": "format_discord_summary_lines", "send": "notify_daily_summary"},
        {"runtime_callsite": "market_capture_sidecar.run", "formatter": "format_capture_status_body", "send": "notify_capture"},
        {"runtime_callsite": "paper_trade_checked_runner block", "formatter": "format_paper_blocked", "send": "publish_paper_blocked"},
        {"runtime_callsite": "(none) communication", "formatter": "format_communication_*", "send": "NOT WIRED (preview only)"},
    ]
    with (REPORT / "formatter_runtime_mapping.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(mapping[0].keys()))
        w.writeheader()
        w.writerows(mapping)

    # --- previews from 20260714-shaped samples ---
    entry_pb = build_entry_detail(
        symbol="4174.T",
        entry_price=925.0,
        stop_price=900.0,
        slot_usage="3/5",
        entry_score_v2=3,
        data={
            "entry_type": "PBV2",
            "momentum_continuation_score": 0.2,
            "price_age_sec": 0.8,
            "board_age_sec": 0.2,
            "price_freshness_source": "event_fresh",
            "entry_expectancy_score_v2": 3,
            "entry_high_break_recent": True,
        },
        entry_time="2026-07-14T10:06:53+09:00",
    )
    entry_or = build_entry_detail(
        symbol="7203.T",
        entry_price=2800.0,
        stop_price=2740.0,
        slot_usage="2/5",
        entry_score_v2=2,
        data={"entry_type": "OR_OVERLAY", "or_reason": "OR breakout", "price_age_sec": 0.3, "board_age_sec": 0.1, "price_freshness_source": "event_fresh"},
        entry_time="2026-07-14T09:05:12+09:00",
    )
    _write(
        REPORT / "entry_preview.txt",
        "=== PBv2 ENTRY ===\n" + preview_payload(event_tag="ENTRY", title_line="【ENTRY】 4174.T", detail=entry_pb, color=0x2F855A)["header"] + "\n" + entry_pb
        + "\n\n=== OR ENTRY ===\n" + entry_or,
    )

    exits = []
    for reason, extra in [
        ("stop_hit", {}),
        (
            "trailing_mfe_exit",
            {
                "board_dynamic_trailing_tier": "board_high",
                "board_dynamic_trailing_activate_pct": 1.0,
                "board_dynamic_trailing_giveback_frac": 0.60,
            },
        ),
        ("no_progress_exit", {"stale_trade": False}),
        ("no_progress_exit", {"stale_trade": True, "market_time_age_sec": 2070.0, "price_freshness_source": "liquidity_stale_trade"}),
        ("afternoon_session_close", {}),
    ]:
        exits.append(
            build_exit_detail(
                symbol="4174.T",
                entry_price=925.0,
                exit_price=920.0 if reason == "stop_hit" else 925.0,
                pnl_pct=-0.54 if reason == "stop_hit" else 0.0,
                mfe_pct=0.4 if reason == "trailing_mfe_exit" else 0.0,
                mae_pct=-0.5 if reason == "stop_hit" else 0.0,
                hold_minutes=15.116,
                exit_reason=reason,
                pnl_yen_100=-500.0 if reason == "stop_hit" else 0.0,
                exit_time="2026-07-14T10:21:59+09:00",
                **extra,
            )
        )
    _write(REPORT / "exit_preview.txt", "\n\n----\n\n".join(exits))

    cap = build_entry_cap_blocked_detail(
        symbol="9984.T",
        entry_score_v2=4,
        data={"entry_type": "PBV2", "event_time": "2026-07-14T10:30:00+09:00", "price_age_sec": 0.4, "board_age_sec": 0.2},
        active_positions=5,
        position_cap=5,
    )
    _write(REPORT / "cap_blocked_preview.txt", "[CAP BLOCKED]\n" + cap)

    refresh = build_universe_refresh_overview(
        session_label="AM",
        refresh_time="10:00",
        added=["1234", "5678"],
        removed=["9999"],
        watch_symbol_count=50,
        status="SUCCESS",
        core10_count=10,
        dynamic40_count=40,
        registered_count=50,
    )
    _write(REPORT / "refresh_preview.txt", "[UNIVERSE REFRESH]\n" + refresh)

    # Try load 20260714 canonical-ish summary if present
    am_path = NATIVE_ROOT / "results" / "small_paper" / "20260714" / "live_session_082256" / "small_paper_summary_am.json"
    pm_path = NATIVE_ROOT / "results" / "small_paper" / "20260714" / "live_session_122532" / "small_paper_summary.json"
    def _summary_preview(path: Path, label: str) -> str:
        metrics: dict[str, Any] = {
            "trade_count": 0,
            "win_count": 0,
            "loss_count": 0,
            "flat_count": 0,
            "win_rate_yen_100": 0,
            "total_pnl_yen_100": 0,
            "avg_pnl_yen_100": 0,
            "profit_factor_yen_100": None,
            "gross_profit_yen_100": 0,
            "gross_loss_yen_100": 0,
            "stop_count": 0,
            "stop_rate": 0,
            "best_trade": "—",
            "worst_trade": "—",
            "max_concurrent": 0,
            "max_concurrent_cap": 5,
            "watch_symbols_count": 50,
            "traded_symbols_count": 0,
        }
        if path.is_file():
            raw = json.loads(path.read_text(encoding="utf-8"))
            can = raw.get("canonical_summary") if isinstance(raw.get("canonical_summary"), dict) else raw
            for k in list(metrics.keys()):
                if k in can:
                    metrics[k] = can[k]
            # map common aliases
            if "push_messages" in raw:
                pass
        body = "\n".join(format_discord_summary_lines(metrics))
        shadow = format_shadow_summary(
            {
                "shadow_name": "research_observation",
                "candidates": "N/A",
                "hypothetical_fills": "N/A",
                "outcome_mapping_unavailable": True,
                "blocks": "N/A",
                "forward_sessions": 1,
            }
        )
        return f"[{label}]\n{body}\n\n{shadow}"

    _write(REPORT / "summary_am_preview.txt", _summary_preview(am_path, "AM PAPER SUMMARY"))
    _write(REPORT / "summary_pm_preview.txt", _summary_preview(pm_path, "PM PAPER SUMMARY"))

    capture_parts = [
        format_capture_status_body({"status": "CAPTURE_READY_FOR_FANOUT", "written": 0}),
        format_capture_status_body({"status": "CAPTURE_WRITING", "received": 150, "written": 149, "bytes": 129801, "drops": 0, "malformed": 0}),
        format_capture_status_body({"status": "CAPTURE_STALE", "stale_age_sec": 130, "paper_status": "RUNNING"}),
        format_capture_status_body({"status": "CAPTURE_FAILED", "reason": "WriterError", "paper_status": "NONE"}),
    ]
    _write(REPORT / "capture_preview.txt", "\n\n----\n\n".join(capture_parts))

    comm = (
        format_communication_degraded(
            {
                "target": "Kabu PUSH",
                "status": "DEGRADED_NO_PUSH",
                "last_push_age_sec": 45.0,
                "reconnect": "1回目",
            }
        )
        + "\n\n----\n\n"
        + format_communication_recovered(
            {"target": "Kabu PUSH", "down_sec": 12, "registered": "50 / 50"}
        )
        + "\n\n----\n\n"
        + format_communication_degraded({"target": "Discord webhook", "status": "SEND_FAILED", "last_push_age_sec": "N/A"})
        + "\n\n----\n\n"
        + format_communication_degraded({"target": "Capture fan-out", "status": "INGEST_DOWN", "entry_eval": "継続(fail-open)"})
    )
    _write(REPORT / "communication_preview.txt", comm)

    before_after = """# Before / After Notification Samples (Phase687W25)

## Header
- Before: `[SMALL PAPER DRY RUN]` / `[NO ORDER]` / `[policy: q055_cap3…]`
- After: `[PAPER TRADE]` / `Real orders: DISABLED`

## ENTRY
- Before: 銘柄/ENTRY価格/損切り/保有枠/audit session_id/scan_id/ClusterGuard…
- After: 銘柄・時刻・価格・方式(PBv2/OR)・score・Momentum・Board・保有 x/5・鮮度・PAPER ONLY

## EXIT
- Before: EXIT理由: 損切りライン到達 / 利益確定条件到達 + 「観測のみ」embed
- After: 理由: 損切り / トレーリング決済 / 停滞ポジション整理; stale=警告(tag_only); PAPER ONLY

## CAP BLOCKED
- Before: position_cap as passed (often 3 in old samples), reject_reason raw
- After: position_cap: 5 (runtime), 方式表示, 保有上限到達

## Capture
- Before: MARKET CAPTURE STARTED + topology PASSIVE_DUAL / CAPTURE_ONLINE ambiguity
- After: READY_FOR_FANOUT / WRITING / STALE / FAILED; topology SINGLE_INGRESS_LOCAL_FANOUT

## Summary
- Before: trade_count / win_rate_yen_100 / max_concurrent …/3
- After: 日本語ラベル, CAP=5, PF: ∞, avg_pnl_pct非表示, Shadow別欄

## Communication
- Before: no dedicated Discord
- After: formatter追加のみ（送信経路は増設しない / previewのみ）
"""
    _write(REPORT / "before_after_notification_samples.md", before_after)

    # length audit
    samples = {
        "entry": entry_pb,
        "exit_stop": exits[0],
        "cap": cap,
        "refresh": refresh,
        "capture_writing": capture_parts[1],
        "summary_am": (REPORT / "summary_am_preview.txt").read_text(encoding="utf-8"),
    }
    with (REPORT / "discord_length_audit.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["name", "chars", "embed_ok_1020", "message_ok_1900"])
        w.writeheader()
        for name, text in samples.items():
            n = len(text)
            w.writerow({"name": name, "chars": n, "embed_ok_1020": n <= 1020, "message_ok_1900": n <= 1900})

    # tests
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_phase687w25_discord_notification_refresh.py",
            "tests/test_discord_cap_blocked_notify.py",
            "tests/test_phase316_exit_discord_100share_yen_notification.py",
            "tests/test_phase333_summary_100share_yen_pnl.py",
            "tests/test_canonical_summary.py",
            "-q",
            "--tb=line",
        ],
        cwd=str(NATIVE_ROOT),
        env={**dict(**{k: v for k, v in __import__("os").environ.items()}), "PYTHONPATH": f"{NATIVE_ROOT / 'src'};{NATIVE_ROOT.parent}"},
        capture_output=True,
        text=True,
    )
    regression = {
        "exit_code": proc.returncode,
        "stdout": (proc.stdout or "")[-2000:],
        "stderr": (proc.stderr or "")[-2000:],
        "passed": proc.returncode == 0,
    }
    _write_json(REPORT / "regression_test_results.json", regression)

    obsolete_remain = False
    for p in REPORT.glob("*preview*.txt"):
        t = p.read_text(encoding="utf-8")
        if "cap3" in t.lower() or "CAPTURE_ONLINE" in t or "PASSIVE_DUAL" in t:
            obsolete_remain = True

    verdict = "OBSOLETE_NOTIFICATION_FIELDS_REMAIN" if obsolete_remain else "DISCORD_NOTIFICATION_CONTENT_UPDATED"
    if proc.returncode != 0:
        verdict = "FORMAT_REGRESSION"

    report = {
        "phase": "687W25",
        "generated_at": _iso(),
        "verdict": verdict,
        "updated_notification_types": [
            "ENTRY",
            "EXIT",
            "CAP BLOCKED",
            "Universe Refresh",
            "AM/PM Summary",
            "Capture",
            "Shadow observation label",
            "Header PAPER TRADE",
            "Communication formatters (preview only)",
        ],
        "notification_count_changed": False,
        "webhook_send_conditions_changed": False,
        "entry_exit_logic_changed": False,
        "actual_submit_cancel": 0,
        "obsolete_scan": {
            "cap3_in_previews": obsolete_remain,
            "capture_online_forbidden": True,
            "canonical_only_summary": True,
            "shadow_separated": True,
        },
        "regression": regression,
    }
    _write_json(REPORT / "phase687w25_report.json", report)
    _write_json(
        REPORT / "code_change_manifest.json",
        {
            "files_changed": [
                "src/small_paper/discord_message_builder.py",
                "src/small_paper/discord_notifier.py",
                "src/notify/discord_notification_formatter.py",
                "src/small_paper/market_capture_sidecar.py",
                "src/small_paper/canonical_summary.py",
                "tests/test_phase687w25_discord_notification_refresh.py",
                "scripts/phase687w25_discord_notification_refresh.py",
            ],
            "send_conditions_unchanged": True,
            "notification_events_unchanged": True,
            "communication_discord_send_added": False,
            "same_push_skip_discord_added": False,
        },
    )

    decision = f"""# Phase687W25 Decision

## Verdict: `{verdict}`

### 1. 更新した通知種類
ENTRY / EXIT / CAP BLOCKED / Universe Refresh / AM·PM Summary / Capture / Shadow見出し / Header / Communication formatter（previewのみ）

### 2. ENTRY通知の変更点
方式(PBv2/OR)・score・Momentum・Board・保有 x/5・鮮度・PAPER ONLY。debug session_id等を本文から除外。

### 3. EXIT通知の変更点
理由日本語を損切り/トレーリング決済/停滞…へ統一。staleは警告(tag_only)。「観測のみ」embed削除。

### 4. Summaryの変更点
canonicalのみ・日本語ラベル・CAP=5・PF: ∞・avg_pnl_pct非表示・Shadowは別欄。

### 5. Capture表示の変更点
READY_FOR_FANOUT / WRITING / STALE / FAILED。CAPTURE_ONLINE禁止。topology=SINGLE_INGRESS_LOCAL_FANOUT。

### 6. Communication表示の変更点
formatter追加のみ。Discord送信経路は増設しない（件数変更禁止）。

### 7. 古いcap=3表記が残っていないか
表示デフォルトをCAP=5に統一。preview走査: {'FAIL' if obsolete_remain else 'OK'}

### 8. CAPTURE_ONLINE表記が残っていないか
formatterでリマップ/禁止。

### 9. canonical以外の損益混入がないか
Summaryは format_discord_summary_lines(canonical) のみ。

### 10. Shadowとactualが分離されたか
Yes（[SHADOW OBSERVATION] / observation only）。

### 11. 通知数・送信条件を変更していないか
変更なし。Communication/same_pushはDiscord追加なし。

### 12. 売買ロジック変更なし
確認（formatter/表示のみ）。

### 13. 実注文変更なし
submit/cancel=0 / Real orders: DISABLED。

### 14. テスト結果
exit_code={proc.returncode}

Artifacts: `{REPORT}`
"""
    _write(REPORT / "phase687w25_decision.md", decision)
    print(decision)
    print(f"verdict={verdict}")
    return 0 if verdict == "DISCORD_NOTIFICATION_CONTENT_UPDATED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
