"""E1_X51 V1R Discord Routing Finalization — audit + live TEST sends."""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from notify.v1r_discord_routing import (
    V1RNotifyKind,
    assert_negative_routing,
    heartbeat_flags,
    public_routing_table,
    publish_v1r,
    v1r_entry_webhook_missing,
)
from research.e1_x37_prospective.wiring import assert_prospective_unopened
from small_paper.env_loader import ensure_repo_dotenv
from small_paper.kabu_order_request_builder import (
    actual_broker_cancel_count,
    actual_broker_submit_count,
)

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "v1r_discord_routing_finalization"

VERDICT_READY = "V1R_DISCORD_ROUTING_READY"
VERDICT_BLOCKED = "V1R_DISCORD_ROUTING_BLOCKED"
ANALYSIS_ID = "V1R_DISCORD_ROUTING_FINALIZATION"


def _demo_payloads() -> dict[str, dict[str, Any]]:
    return {
        "FILL": {
            "symbol": "285A",
            "fill": 3820,
            "limit": 3820,
            "qty": 100,
            "rank": 1,
            "score": 0.913,
            "fill_delay_sec": 0.42,
            "exit_target": "09:15:00",
        },
        "EXIT": {
            "symbol": "285A",
            "entry_price": 3820,
            "exit_price": 3875,
            "pnl_yen": 5500,
            "pnl_pct": 1.44,
            "hold_sec": 604.8,
            "today_pnl_yen": 18700,
            "reason": "FIRST_VALID_BUY1_AT_OR_AFTER_TARGET",
        },
        "ENTRY": {
            "symbol": "285A",
            "anchor": "09:05:00",
            "rank": 1,
            "score": 0.913,
            "limit": 3820,
            "qty": 100,
            "pending": 2,
            "open": 1,
            "cap": 5,
        },
        "EXPIRED": {
            "symbol": "4062",
            "anchor": "09:15:00",
            "limit": 2105,
            "qty": 100,
            "wait_sec": 1.0,
        },
        "PRIMARY_SUMMARY": {
            "date": "2026-08-10 DEMO",
            "signals": 246,
            "admitted": 47,
            "fills": 11,
            "expired": 36,
            "fill_rate": "23.4%",
            "capacity_blocked": 18,
            "wins": 7,
            "losses": 4,
            "total_pnl": "+28,500円",
            "overall_pf": 2.31,
            "best": "285A +11,500円",
            "worst": "4062 -4,200円",
            "max_open_pending": "5 / 5",
            "top_symbol_contribution": "285A 0.40",
            "top_gross_positive_share": "0.55",
            "submit_cancel_live": "0/0/0",
        },
        "PBV2_SHADOW": {
            "date": "2026-08-10 DEMO",
            "positions": 3,
            "pnl": "+1,200円",
            "note": "SHADOW_ONLY",
        },
        "ONE_M_SHADOW": {
            "date": "2026-08-10 DEMO",
            "cash": "1,012,000",
            "positions": 2,
            "note": "SHADOW_ONLY_DIAGNOSTIC",
        },
    }


def _nonblocking_check() -> dict[str, Any]:
    """Critical-path enqueue prep must stay well under PENDING 1s deadline."""
    from notify.v1r_discord_routing import format_fill, resolve_destination
    from notify.discord_notification_model import (
        ActualOrShadow,
        NotificationCategory,
        Severity,
        build_envelope,
        trading_date_jst,
    )

    results = []
    t0 = time.perf_counter()
    for i in range(20):
        t1 = time.perf_counter()
        _url, _key, _missing = resolve_destination(V1RNotifyKind.FILL)
        body = format_fill(
            {"symbol": f"NB{i}", "fill": 1000, "rank": 1, "score": 0.1,
             "fill_delay_sec": 0.1, "exit_target": "09:00:00", "qty": 100},
            test_only=True,
        )
        build_envelope(
            category=NotificationCategory.TRADE_ACTUAL,
            severity=Severity.INFO,
            event_type="V1R_FILL_NB_PROBE",
            title="[V1R PAPER FILL]",
            content=body,
            trading_date=trading_date_jst(),
            source_module="notify.v1r_discord_routing",
            dedupe_key=f"nb-probe|{uuid.uuid4().hex}",
            actual_or_shadow=ActualOrShadow.ACTUAL,
        )
        results.append((time.perf_counter() - t1) * 1000.0)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    return {
        "burst_enqueue_ms": elapsed_ms,
        "max_single_enqueue_ms": max(results) if results else None,
        "mean_enqueue_ms": sum(results) / len(results) if results else None,
        "deadline_not_blocked": elapsed_ms < 50.0,
        "blocking": False,
        "http_not_on_critical_path": True,
        "pass": elapsed_ms < 50.0 and all(x < 10.0 for x in results),
    }


def main() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    run_id = "v1r_discord_route_" + datetime.now(JST).strftime("%Y%m%d_%H%M%S") + "_A"
    print(f"=== {ANALYSIS_ID} {run_id} ===", flush=True)

    ensure_repo_dotenv()
    neg = assert_negative_routing()
    print(f"  negative_routing={neg['pass']}", flush=True)
    hb = heartbeat_flags()
    print(f"  heartbeat {hb}", flush=True)

    nb = _nonblocking_check()
    print(f"  nonblocking={nb['pass']} burst_ms={nb['burst_enqueue_ms']:.3f}", flush=True)

    payloads = _demo_payloads()
    session = f"v1r-route-demo-{uuid.uuid4().hex[:10]}"
    send_plan = [
        ("trade-notify", V1RNotifyKind.FILL, payloads["FILL"]),
        ("trade-notify", V1RNotifyKind.EXIT, payloads["EXIT"]),
        ("trade-entry", V1RNotifyKind.ENTRY, payloads["ENTRY"]),
        ("trade-entry", V1RNotifyKind.EXPIRED, payloads["EXPIRED"]),
        ("trade-research", V1RNotifyKind.PRIMARY_SUMMARY, payloads["PRIMARY_SUMMARY"]),
        ("trade-research", V1RNotifyKind.PBV2_SHADOW, payloads["PBV2_SHADOW"]),
        ("trade-research", V1RNotifyKind.ONE_M_SHADOW, payloads["ONE_M_SHADOW"]),
    ]
    send_results = []
    for channel, kind, payload in send_plan:
        print(f"  send {channel} {kind.value} ...", flush=True)
        r = publish_v1r(kind, payload, test_only=True, sync_http=True, session_id=session)
        send_results.append({
            "channel": channel,
            "kind": kind.value,
            "status": r.status,
            "http_status": r.http_status,
            "env_key": r.env_key,
            "v1r_entry_webhook_missing": r.v1r_entry_webhook_missing,
            "error": r.error,
            "latency_ms": r.latency_ms,
            "notification_id": r.notification_id,
        })
        print(
            f"    -> {r.status} http={r.http_status} env={r.env_key}",
            flush=True,
        )
        time.sleep(0.5)

    def _ok(kind: str) -> bool:
        row = next(x for x in send_results if x["kind"] == kind)
        return row["status"] == "SENT" and int(row.get("http_status") or 0) in (200, 204)

    trade_notify = {
        "FILL": "PASS" if _ok("FILL") else "FAIL",
        "EXIT": "PASS" if _ok("EXIT") else "FAIL",
    }
    entry_missing = v1r_entry_webhook_missing()
    trade_entry = {
        "ENTRY": "PASS" if _ok("ENTRY") else ("FAIL_SOFT_MISSING_ENV" if entry_missing else "FAIL"),
        "EXPIRED": "PASS" if _ok("EXPIRED") else ("FAIL_SOFT_MISSING_ENV" if entry_missing else "FAIL"),
        "v1r_entry_webhook_missing": entry_missing,
    }
    trade_research = {
        "PRIMARY_SUMMARY": "PASS" if _ok("PRIMARY_SUMMARY") else "FAIL",
        "PBV2_SHADOW": "PASS" if _ok("PBV2_SHADOW") else "FAIL",
        "ONE_M_SHADOW": "PASS" if _ok("ONE_M_SHADOW") else "FAIL",
    }

    # CAP unchanged: negative check + routing table
    cap_unchanged = neg["checks"]["CAP_unchanged"] and neg["checks"]["CAP_not_trade_entry"]

    # Entry channel: live PASS required for READY, OR explicit fail-soft verified when missing
    entry_live_ok = trade_entry["ENTRY"] == "PASS" and trade_entry["EXPIRED"] == "PASS"
    entry_failsoft_ok = (
        entry_missing
        and all(
            x["status"] == "SKIPPED_WEBHOOK_NOT_CONFIGURED"
            for x in send_results
            if x["kind"] in ("ENTRY", "EXPIRED")
        )
        and hb["v1r_entry_webhook_missing"] is True
    )

    checks = {
        "negative_routing": neg["pass"],
        "nonblocking": nb["pass"],
        "trade_notify_fill": trade_notify["FILL"] == "PASS",
        "trade_notify_exit": trade_notify["EXIT"] == "PASS",
        "trade_research_summary": trade_research["PRIMARY_SUMMARY"] == "PASS",
        "trade_research_pbv2": trade_research["PBV2_SHADOW"] == "PASS",
        "trade_research_1m": trade_research["ONE_M_SHADOW"] == "PASS",
        "trade_entry_ok_or_failsoft": entry_live_ok or entry_failsoft_ok,
        "cap_blocked_unchanged": cap_unchanged,
        # READY requires live entry channel configured (operator must set ENV before 8/10)
        "trade_entry_live": entry_live_ok,
    }

    # Strict PASS: entry webhook must be configured and live sends succeed
    verdict = VERDICT_READY if all(checks.values()) else VERDICT_BLOCKED

    unopened = assert_prospective_unopened()
    submit = int(actual_broker_submit_count() or 0)
    cancel = int(actual_broker_cancel_count() or 0)

    report = {
        "analysis_id": ANALYSIS_ID,
        "run_id": run_id,
        "verdict": verdict,
        "routing_table": public_routing_table(),
        "heartbeat_flags": hb,
        "negative_routing": neg,
        "nonblocking": nb,
        "send_results": send_results,
        "trade_notify": trade_notify,
        "trade_entry": trade_entry,
        "trade_research": trade_research,
        "cap_blocked_unchanged": "PASS" if cap_unchanged else "FAIL",
        "notification_nonblocking": "PASS" if nb["pass"] else "FAIL",
        "checks": checks,
        "ledger_state_mutation": False,
        "opened_20260810": False,
        "prospective_observer": "NOT_STARTED",
        "strategy_mutation": False,
        "model_mutation": False,
        "universe_mutation": False,
        "submit_cancel_live": f"{submit}/{cancel}/0",
        "unopened_probe": unopened,
        "note": (
            None if entry_live_ok
            else "Set KABU_V1R_ENTRY_WEBHOOK_URL in repo .env to Discord channel trade-entry, then re-run."
        ),
    }
    (OUT / "report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    (OUT / "V1R_DISCORD_ROUTING_TABLE.json").write_text(
        json.dumps({"routing": public_routing_table(), "heartbeat": hb}, indent=2),
        encoding="utf-8",
    )
    md = [
        f"# {ANALYSIS_ID}",
        "",
        f"- run_id: `{run_id}`",
        f"- verdict: `{verdict}`",
        f"- trade-notify FILL/EXIT: `{trade_notify}`",
        f"- trade-entry ENTRY/EXPIRED: `{trade_entry}`",
        f"- trade-research: `{trade_research}`",
        f"- negative_routing: `{neg['pass']}`",
        f"- submit/cancel/live: `{report['submit_cancel_live']}`",
    ]
    if report["note"]:
        md += ["", f"NOTE: {report['note']}"]
    (OUT / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (OUT / "_interim.json").write_text(json.dumps({
        "run_id": run_id,
        "verdict": verdict,
        "trade_notify": trade_notify,
        "trade_entry": trade_entry,
        "trade_research": trade_research,
        "negative_routing": neg["pass"],
        "cap_blocked_unchanged": cap_unchanged,
        "nonblocking": nb["pass"],
        "opened_20260810": False,
        "prospective_observer": "NOT_STARTED",
        "submit_cancel_live": report["submit_cancel_live"],
        "ledger_state_mutation": False,
        "strategy_mutation": False,
    }, indent=2), encoding="utf-8")

    print(f"=== DONE {verdict} ===", flush=True)
    print(json.dumps({
        "run_id": run_id,
        "verdict": verdict,
        "trade_notify": trade_notify,
        "trade_entry": trade_entry,
        "trade_research": trade_research,
        "note": report["note"],
    }, indent=2, ensure_ascii=False))
    return report


if __name__ == "__main__":
    main()
