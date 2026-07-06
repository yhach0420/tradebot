#!/usr/bin/env python3
"""Phase639: Live Discord webhook smoke test (real HTTP POST, no ENTRY/EXIT changes)."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

SCRIPT = Path(__file__).resolve()
NATIVE_ROOT = SCRIPT.parents[1]
REPO_ROOT = NATIVE_ROOT.parent
REPORT_DIR = NATIVE_ROOT / "results" / "reports" / "phase639_discord_live_smoke"
JST = ZoneInfo("Asia/Tokyo")
PHASE639_VERDICT_DONE = "phase639_discord_live_smoke_done"
PHASE639_VERDICT_FAIL = "phase639_discord_live_smoke_failed"

TRADE_NOTIFY_ENV = "KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL"
CAP_BLOCKED_ENV = "KABU_SMALL_PAPER_CAP_BLOCKED_WEBHOOK_URL"
LEGACY_ENV = "KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL"
PROD_YAML = (
    NATIVE_ROOT
    / "configs"
    / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
)


def _bootstrap() -> None:
    for p in (NATIVE_ROOT / "src", REPO_ROOT):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _mask_url(url: str) -> str:
    u = (url or "").strip()
    if len(u) <= 48:
        return u or "(empty)"
    return f"{u[:40]}…{u[-6:]}"


def _fixture_summary() -> dict[str, Any]:
    from small_paper.canonical_summary import build_canonical_summary, collect_canonical_trades

    events = [
        {
            "event_type": "observer_exit",
            "symbol": "6976.T",
            "entry_price": 1000.0,
            "exit_price": 1010.0,
            "pnl_pct": 1.0,
            "mfe_pct": 1.5,
            "mae_pct": -0.2,
            "hold_minutes": 12.0,
            "exit_reason": "trailing_mfe_exit",
            "pnl_yen_100": 1000.0,
            "entry_type": "PBV2",
        }
    ]
    canonical = build_canonical_summary(
        collect_canonical_trades(events),
        peak_open_slots=2,
        max_concurrent_positions=5,
    )
    return {
        "canonical_summary": canonical,
        "session_bucket": "PM",
        "pbv2_count": 1,
        "or_count": 0,
        "accepted_count": 1,
        "observer_exit_count_with_pnl": 1,
        "pbv2_rise5_shadow_enabled": True,
        "pbv2_rise5_shadow_threshold_pct": 1.84,
        "pbv2_rise5_shadow_block_count": 2,
        "pbv2_rise5_shadow_kept_count": 8,
        "pbv2_rise5_shadow_target_count": 10,
        "pbv2_rise5_shadow_blocked_winners": 1,
        "pbv2_rise5_shadow_blocked_losers": 1,
        "pbv2_rise5_shadow_blocked_pnl_yen_100": 500.0,
        "pbv2_rise5_shadow_net_effect_yen": -500.0,
        "gate_dominance_alert_level": "critical",
        "gate_dominance_top_reason": "high_drift_pullback",
        "gate_dominance_top_share_pct": 96.5,
        "gate_dominance_total_rejects": 1200,
        "freshness_semantics_v2_enabled": True,
        "event_stale_reject_count": 0,
        "board_stale_reject_count": 3,
        "trade_stale_tag_count": 10,
        "entry_cluster_guard_enabled": True,
        "cluster_guard_reject_count": 0,
        "entry_quality_guard_enabled": True,
        "entry_quality_guard_reject_count": 2,
        "entry_quality_guard_spread_reject_count": 1,
        "entry_quality_guard_update_reject_count": 1,
        "exit_shadow_monitor_enabled": True,
        "exit_mfe_capture_ratio": 0.91,
        "exit_opportunity_loss_avg": 0.05,
        "exit_early_profit_take_count": 0,
        "pullback_misread_guard_shadow_enabled": True,
        "pullback_misread_guard_shadow_delta_yen": 1000.0,
        "pullback_misread_guard_shadow_blocked_count": 1,
        "board_dynamic_shadow_enabled": True,
        "board_dynamic_shadow_total_delta_yen": 200.0,
        "peak_open_slots": 2,
        "max_concurrent_positions": 5,
        "api_error_count": 0,
        "stale_tick_count": 0,
        "data_gap_count": 0,
        "live_feature_complete_rate_pct": 99.0,
        "discord_error_count": 1,
        "cap_blocked_notify_attempt_count": 1,
        "cap_blocked_notify_sent_count": 0,
        "cap_blocked_webhook_configured": True,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
    }


class _HttpTrace:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def install(self) -> Callable[[], None]:
        import small_paper.discord_notifier as dn

        original = dn.requests.post

        def traced(url: str, *args: Any, **kwargs: Any) -> Any:
            t0 = time.monotonic()
            exc: Optional[str] = None
            status: Optional[int] = None
            try:
                resp = original(url, *args, **kwargs)
                status = int(resp.status_code)
                return resp
            except Exception as e:
                exc = str(e)
                raise
            finally:
                self.calls.append(
                    {
                        "url_raw": str(url),
                        "url_masked": _mask_url(str(url)),
                        "status_code": status,
                        "exception": exc,
                        "elapsed_ms": round((time.monotonic() - t0) * 1000, 1),
                    }
                )

        dn.requests.post = traced  # type: ignore[assignment]

        def restore() -> None:
            dn.requests.post = original  # type: ignore[assignment]

        return restore


def _channel_for_call(
    *,
    call_idx: int,
    http_trace: _HttpTrace,
    trade_notify_url: str,
    cap_blocked_url: str,
    legacy_url: str,
) -> str:
    if call_idx >= len(http_trace.calls):
        return "unknown"
    raw = str(http_trace.calls[call_idx].get("url_raw") or "")
    if cap_blocked_url and raw == cap_blocked_url:
        return "cap_blocked"
    if trade_notify_url and raw == trade_notify_url:
        return "trade_notify"
    if legacy_url and raw == legacy_url:
        return "legacy"
    return "unknown"


def run_smoke(*, dry_run: bool = False) -> dict[str, Any]:
    _bootstrap()
    http_trace = _HttpTrace()
    restore_http = http_trace.install()
    import os

    from api.rest_client import load_kabu_env
    from small_paper.config import load_pilot_config
    from small_paper.discord_message_builder import (
        build_operator_status_embed_fields,
        format_gate_dominance_alert_lines,
    )
    from small_paper.discord_notifier import (
        SmallPaperDiscordNotifier,
        discord_config_from_pilot,
        discord_notify_summary_fields,
    )
    from small_paper.reject_reasons import (
        REJECT_MAX_CONCURRENT,
        REJECT_MAX_ENTRIES_PER_SCAN,
        REJECT_SAME_SYMBOL_OPEN_OVERLAP,
    )

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    load_kabu_env(repo_root=REPO_ROOT)
    trade_notify_url = (os.environ.get(TRADE_NOTIFY_ENV) or "").strip()
    cap_blocked_url = (os.environ.get(CAP_BLOCKED_ENV) or "").strip()
    legacy_url = (os.environ.get(LEGACY_ENV) or "").strip()

    env_check = {
        LEGACY_ENV: bool(legacy_url),
        CAP_BLOCKED_ENV: bool(cap_blocked_url),
        TRADE_NOTIFY_ENV: bool(trade_notify_url),
    }

    cfg = load_pilot_config(PROD_YAML)
    dcfg = replace(
        discord_config_from_pilot(cfg),
        cooldown_sec=0.0,
        entry_deferred_cooldown_sec=0.0,
        send_daily_summary=True,
        send_entry_cap_blocked=True,
    )
    notifier = SmallPaperDiscordNotifier(
        dcfg,
        profile=cfg.profile,
        entry_profile=cfg.entry_profile,
    )
    notifier._last_sent_mono.clear()

    trace_rows: list[dict[str, Any]] = []
    messages_on_discord: list[str] = []
    fail_notifier: Any = None
    summary_fields: dict[str, Any] = {}

    def detect_channel(url: str) -> str:
        u = (url or "").strip()
        if cap_blocked_url and u == cap_blocked_url:
            return "cap_blocked"
        if trade_notify_url and u == trade_notify_url:
            return "trade_notify"
        if legacy_url and u == legacy_url:
            return "legacy"
        return "unknown"

    def send_and_record(
        test_id: str,
        fn: Callable[[], bool],
        *,
        expected_channel: str,
        event_tag: str = "",
        note: str = "",
    ) -> None:
        before = len(http_trace.calls)
        ok = fn()
        http = http_trace.calls[before] if before < len(http_trace.calls) else {}
        channel = expected_channel
        if http.get("url_raw"):
            channel = detect_channel(str(http.get("url_raw")))
        status = http.get("status_code")
        if ok and status is not None and int(status) >= 400:
            ok = False
        record(
            test_id,
            attempted=True,
            sent=ok,
            target_channel=channel,
            event_tag=event_tag,
            note=note,
            http=http,
        )

    def record(
        test_id: str,
        *,
        attempted: bool,
        sent: bool,
        target_channel: str,
        event_tag: str = "",
        note: str = "",
        http: Optional[dict[str, Any]] = None,
    ) -> None:
        http = http or {}
        row = {
            "test_id": test_id,
            "attempted": attempted,
            "sent": sent,
            "failed": attempted and not sent,
            "status_code": http.get("status_code"),
            "exception": http.get("exception"),
            "target_channel": target_channel,
            "event_tag": event_tag,
            "url_masked": http.get("url_masked", ""),
            "note": note,
        }
        trace_rows.append(row)
        if sent and event_tag:
            messages_on_discord.append(f"[{event_tag}] {test_id}")

    try:
        # 0) error count probe (isolated notifier, no real webhook pollution)
        fail_cfg = replace(dcfg, trade_cap_blocked_webhook_env="PHASE639_MISSING_CAP_WEBHOOK")
        fail_notifier = SmallPaperDiscordNotifier(
            fail_cfg,
            profile=cfg.profile,
            entry_profile=cfg.entry_profile,
        )
        os.environ.pop("PHASE639_MISSING_CAP_WEBHOOK", None)
        fail_ok = fail_notifier.notify_entry_cap_blocked(
            event={"symbol": "PHASE639_ERR", "gate_reject_reason": REJECT_MAX_CONCURRENT},
            payload={},
            trade_data={},
            open_slots=5,
            block_reason=REJECT_MAX_CONCURRENT,
        )
        record(
            "discord_error_count_probe",
            attempted=True,
            sent=fail_ok,
            target_channel="cap_blocked_missing",
            event_tag="CAP BLOCKED",
            note=f"discord_error_count={fail_notifier.discord_error_count}",
        )

        if dry_run:
            restore_http()
            report = {
                "phase": "phase639_discord_live_smoke",
                "verdict": PHASE639_VERDICT_FAIL,
                "dry_run": True,
                "env_check": env_check,
            }
            return report

        # 1) trade-notify heartbeat
        send_and_record(
            "trade_notify_heartbeat",
            lambda: notifier.notify_heartbeat(
                summary={
                    "runtime_sec": 1,
                    "accepted_count": 0,
                    "rejected_count": 0,
                    "observer_entry_count": 0,
                    "observer_holding_count": 0,
                    "observer_exit_count": 0,
                    "peak_open_slots": 0,
                    "api_error_count": 0,
                    "stale_tick_count": 0,
                    "data_gap_count": 0,
                    "live_feature_complete_rate_pct": 99.0,
                    "quality_distribution": {},
                    "session_bucket_summary": {},
                    "top_symbols": "PHASE639",
                }
            ),
            expected_channel="trade_notify",
            event_tag="HEARTBEAT",
        )

        # 2) operator daily summary (PM)
        summary = _fixture_summary()
        events = [
            {
                "event_type": "observer_exit",
                "symbol": "6976.T",
                "entry_price": 1000.0,
                "exit_price": 1010.0,
                "pnl_pct": 1.0,
                "mfe_pct": 1.5,
                "mae_pct": -0.2,
                "hold_minutes": 12.0,
                "exit_reason": "trailing_mfe_exit",
                "pnl_yen_100": 1000.0,
            }
        ]
        send_and_record(
            "operator_daily_summary",
            lambda: notifier.notify_daily_summary(events=events, summary=summary),
            expected_channel="trade_notify",
            event_tag="PM Summary",
        )

        # 3-5) cap-blocked variants
        cap_cases = [
            ("cap_blocked_max_concurrent", REJECT_MAX_CONCURRENT, "PHASE639_CAP"),
            ("cap_blocked_overlap", REJECT_SAME_SYMBOL_OPEN_OVERLAP, "PHASE639_OVL"),
            ("cap_blocked_max_scan", REJECT_MAX_ENTRIES_PER_SCAN, "PHASE639_SCAN"),
        ]
        for test_id, reason, sym in cap_cases:
            send_and_record(
                test_id,
                lambda reason=reason, sym=sym: notifier.notify_entry_cap_blocked(
                    event={
                        "symbol": sym,
                        "event_time": datetime.now(JST).isoformat(timespec="seconds"),
                        "gate_reject_reason": reason,
                        "entry_expectancy_score_v2": 4,
                    },
                    payload={"CurrentPrice": 1200.0, "TradingValue": 500000},
                    trade_data={
                        "entry_expectancy_score_v2": 4,
                        "trading_value": 500000,
                        "entry_rise_5min_pct": 0.5,
                    },
                    open_slots=5,
                    block_reason=reason,
                ),
                expected_channel="cap_blocked",
                event_tag="CAP BLOCKED",
                note="must be cap_blocked channel only",
            )

        # 6) Rise5 shadow standalone section
        rise_fields = build_operator_status_embed_fields(events=events, summary=summary)
        rise_block = next((f for f in rise_fields if f["name"] == "Rise5 Shadow Summary"), None)
        if rise_block:
            send_and_record(
                "rise5_shadow_summary",
                lambda: notifier._post(
                    event_tag="PHASE639 Rise5 Shadow",
                    title_line="Phase639 Rise5 Shadow Smoke",
                    fields=[rise_block, {"name": "probe", "value": "phase639", "inline": True}],
                    color=0xDD6B20,
                    dedupe_key=None,
                    trade_notify=True,
                ),
                expected_channel="trade_notify",
                event_tag="PHASE639 Rise5 Shadow",
            )

        # 7) Gate dominance critical
        dom_lines = format_gate_dominance_alert_lines(summary)
        send_and_record(
            "gate_dominance_critical",
            lambda: notifier._post(
                event_tag="Gate Dominance Alert",
                title_line="Phase639 Gate Dominance CRITICAL",
                fields=[{"name": "Alert", "value": "\n".join(dom_lines), "inline": False}],
                color=0xE53E3E,
                dedupe_key=None,
                trade_notify=True,
            ),
            expected_channel="trade_notify",
            event_tag="Gate Dominance Alert",
        )

        # 8) system health with discord_error_count
        health_fields = build_operator_status_embed_fields(events=events, summary=summary)
        health_block = next((f for f in health_fields if f["name"] == "System Health"), None)
        if health_block:
            send_and_record(
                "discord_health_summary",
                lambda: notifier._post(
                    event_tag="PHASE639 System Health",
                    title_line="Phase639 Discord Health",
                    fields=[health_block],
                    color=0x3182CE,
                    dedupe_key=None,
                    trade_notify=True,
                ),
                expected_channel="trade_notify",
                event_tag="PHASE639 System Health",
            )

        summary_fields = discord_notify_summary_fields(notifier)
    finally:
        restore_http()

    cap_rows = [r for r in trace_rows if r["test_id"].startswith("cap_blocked")]
    cap_used_cap_channel = bool(cap_rows) and all(
        r.get("target_channel") == "cap_blocked" for r in cap_rows if r["sent"]
    )
    cap_no_trade_notify = all(
        r.get("target_channel") != "trade_notify" for r in cap_rows if r["sent"]
    )
    trade_sent = any(
        r["sent"] and r.get("target_channel") == "trade_notify"
        for r in trace_rows
        if r["test_id"] != "discord_error_count_probe"
    )
    cap_sent = any(r["sent"] for r in cap_rows)

    all_required_sent = all(
        r["sent"]
        for r in trace_rows
        if r["test_id"]
        not in ("discord_error_count_probe",)  # probe intentionally fails
    )

    report = {
        "phase": "phase639_discord_live_smoke",
        "verdict": PHASE639_VERDICT_DONE if all_required_sent and cap_used_cap_channel else PHASE639_VERDICT_FAIL,
        "timestamp": datetime.now(JST).isoformat(timespec="seconds"),
        "env_check": env_check,
        "answers": {
            "1_trade_notify_sent": trade_sent,
            "2_cap_blocked_sent": cap_sent,
            "3_cap_blocked_used_dedicated_webhook_only": cap_used_cap_channel and cap_no_trade_notify,
            "4_no_fallback_to_trade_notify_for_cap_blocked": cap_no_trade_notify,
            "5_discord_error_count_recorded": (fail_notifier.discord_error_count >= 1 if fail_notifier else False),
            "6_messages_on_discord": messages_on_discord,
        },
        "notifier_stats": summary_fields,
        "trace_summary": {
            "attempted": sum(1 for r in trace_rows if r["attempted"]),
            "sent": sum(1 for r in trace_rows if r["sent"]),
            "failed": sum(1 for r in trace_rows if r["failed"]),
        },
    }

    with (REPORT_DIR / "phase639_discord_send_trace.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(trace_rows[0].keys()) if trace_rows else ["test_id"])
        w.writeheader()
        w.writerows(trace_rows)

    (REPORT_DIR / "phase639_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase639 Discord live smoke test")
    parser.add_argument("--dry-run", action="store_true", help="Skip HTTP sends")
    args = parser.parse_args()
    report = run_smoke(dry_run=bool(args.dry_run))
    print(json.dumps(report.get("answers", report), ensure_ascii=False, indent=2))
    print(f"verdict={report.get('verdict')}")
    print(f"report -> {REPORT_DIR / 'phase639_report.json'}")
    return 0 if report.get("verdict") == PHASE639_VERDICT_DONE else 1


if __name__ == "__main__":
    raise SystemExit(main())
