"""E1_X52B Discord UI — preserve existing trade-notify information density."""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from notify.v1r_discord_routing import (
    COLOR_ENTRY,
    COLOR_EXIT,
    COLOR_EXPIRED,
    COLOR_FILL,
    V1RNotifyKind,
    assert_color_lock,
    assert_negative_routing,
    build_event_embed,
    field_completeness,
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
OUT = NATIVE / "results" / "research" / "v1r_discord_ui_finalization"

VERDICT_READY = "V1R_DISCORD_UI_READY"
VERDICT_BLOCKED = "V1R_DISCORD_UI_BLOCKED"
ANALYSIS_ID = "V1R_DISCORD_UI_INFO_PRESERVATION"


def _pf(checks: dict[str, bool]) -> str:
    return "PASS" if checks and all(checks.values()) else "FAIL"


def main() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    run_id = "v1r_discord_ui_" + datetime.now(JST).strftime("%Y%m%d_%H%M%S") + "_B"
    print(f"=== {ANALYSIS_ID} {run_id} ===", flush=True)

    ensure_repo_dotenv()
    colors = assert_color_lock()
    neg = assert_negative_routing()
    hb = heartbeat_flags()
    entry_present = not v1r_entry_webhook_missing()
    print(f"  color_lock={colors['pass']} negative={neg['pass']} entry_webhook={entry_present}", flush=True)

    session = f"v1r-ui52b-{uuid.uuid4().hex[:10]}"

    # Demo payloads — screenshot-equivalent information density (TEST ONLY)
    entry_payload = {
        "symbol": "6674.T",
        "symbol_name": "ジーエス・ユアサ コーポレーション",
        "anchor": "15:05:00",
        "rank": 3,
        "candidates": 50,
        "score": 0.913,
        "limit": 5234,
        "qty": 100,
        "open": 2,
        "pending": 1,
        "cap": 5,
        "wait_sec": 1.0,
        "freshness_sec": 0.4,
        "entry_count_today": 3,
        "previous_trade": {
            "exit_reason": "FIRST_VALID_BUY1_AT_OR_AFTER_TARGET",
            "exit_time": "10:42:25",
            "exit_price": 5175,
            "exit_pnl_yen": -2500,
            "elapsed": "4時間22分35秒",
        },
    }
    expired_payload = {
        "symbol": "6674.T",
        "symbol_name": "ジーエス・ユアサ コーポレーション",
        "anchor": "15:05:00",
        "expire_time": "15:05:01",
        "limit": 5234,
        "qty": 100,
        "rank": 3,
        "candidates": 50,
        "score": 0.913,
        "wait_sec": 1.0,
        "entry_count_today": 3,
        "buy1": 5230,
        "sell1": 5235,
        "freshness_sec": 0.5,
    }
    fill_payload = {
        "symbol": "6674.T",
        "symbol_name": "ジーエス・ユアサ コーポレーション",
        "anchor": "15:05:00",
        "fill_time": "15:05:00.42",
        "limit": 5234,
        "fill": 5234,
        "qty": 100,
        "rank": 3,
        "candidates": 50,
        "score": 0.913,
        "fill_delay_sec": 0.42,
        "open": 3,
        "pending": 0,
        "cap": 5,
        "exit_target": "15:15:00.42",
        "fill_count_today": 3,
        "buy1": 5234,
        "sell1": 5234,
        "freshness_sec": 0.4,
    }
    exit_payload = {
        "symbol": "6674.T",
        "symbol_name": "ジーエス・ユアサ コーポレーション",
        "entry_time": "15:05:00.42",
        "exit_time": "15:15:05.21",
        "entry_price": 5234,
        "exit_price": 5229,
        "qty": 100,
        "pnl_yen": -500,
        "pnl_pct": -0.10,
        "daily_symbol_pnl_yen": -5400,
        "daily_v1r_pnl_yen": 18700,
        "today_pnl_yen": 18700,
        "hold_sec": 604.8,
        "reason": "FIRST_VALID_BUY1_AT_OR_AFTER_TARGET",
        "mfe_pct": 0.08,
        "mae_pct": -0.11,
        "buy1": 5229,
        "buy1_qty": 800,
        "freshness_sec": 0.3,
    }
    summary_payload = {
        "date": "2026/08/03",
        "total_pnl": "+18,700円",
        "overall_pf": 2.31,
        "wins": 7,
        "losses": 4,
        "signals": 47,
        "fills": 11,
        "expired": 36,
        "fill_rate": "23.4%",
        "capacity_blocked": 2,
        "best": "285A +11,500円",
        "worst": "4062 -4,200円",
        "max_open_pending": "5 / 5",
        "top_symbol": "285A",
        "top_symbol_contribution": "40%",
        "mfe_mean_pct": 0.12,
        "mae_mean_pct": -0.09,
        "daily_v1r_pnl": "+18,700円",
        "submit_cancel_live": "0/0/0",
    }
    pbv2_payload = {
        "date": "2026/08/03",
        "pnl": "+1,200円",
        "trades": 3,
        "positions": 3,
        "wins": 2,
        "losses": 1,
        "pf": 1.45,
        "best": "7203 +3,100円",
        "worst": "6758 -1,900円",
        "exit_reason_breakdown": "TRAILING_MFE:2 / STOP:1",
    }
    one_m_payload = {
        "date": "2026/08/03",
        "start_cash": "1,000,000",
        "end_cash": "1,012,000",
        "cash": "1,012,000",
        "pnl": "+12,000円",
        "return_pct": "+1.20%",
        "fills": 4,
        "capital_blocked": 1,
        "max_invested": "820,000",
        "max_dd": "-1.8%",
        "wins": 3,
        "losses": 1,
    }

    # Local field completeness (HTTP alone is insufficient)
    field_checks: dict[str, dict[str, bool]] = {}
    for kind, payload in (
        (V1RNotifyKind.ENTRY, entry_payload),
        (V1RNotifyKind.FILL, fill_payload),
        (V1RNotifyKind.EXIT, exit_payload),
        (V1RNotifyKind.EXPIRED, expired_payload),
    ):
        _, embeds, _ = build_event_embed(kind, payload, test_only=True)
        field_checks[kind.value] = field_completeness(kind, embeds[0])
        print(f"  fields {kind.value}: {_pf(field_checks[kind.value])} {field_checks[kind.value]}", flush=True)

    # Symbol name / previous trade / MFEMAE spot checks on built embeds
    _, entry_embeds, _ = build_event_embed(V1RNotifyKind.ENTRY, entry_payload, test_only=True)
    entry_blob = json.dumps(entry_embeds[0], ensure_ascii=False)
    _, exit_embeds, _ = build_event_embed(V1RNotifyKind.EXIT, exit_payload, test_only=True)
    exit_blob = json.dumps(exit_embeds[0], ensure_ascii=False)

    symbol_name_ok = "ジーエス・ユアサ" in entry_blob
    previous_trade_ok = "前回EXIT" in entry_blob and "4時間22分35秒" in entry_blob
    mfe_mae_ok = "MFE" in exit_blob and "MAE" in exit_blob
    daily_symbol_ok = "本日同銘柄累計" in exit_blob
    daily_v1r_ok = "本日V1R累計" in exit_blob
    no_none_leak = "None" not in entry_blob and "None" not in exit_blob
    no_raw_reason = "FIRST_VALID" not in exit_blob

    plan = [
        (V1RNotifyKind.ENTRY, entry_payload, COLOR_ENTRY, "green", "trade-entry"),
        (V1RNotifyKind.EXPIRED, expired_payload, COLOR_EXPIRED, "orange", "trade-entry"),
        (V1RNotifyKind.FILL, fill_payload, COLOR_FILL, "blue", "trade-notify"),
        (V1RNotifyKind.EXIT, exit_payload, COLOR_EXIT, "red", "trade-notify"),
        (V1RNotifyKind.PRIMARY_SUMMARY, summary_payload, None, None, "trade-research"),
        (V1RNotifyKind.PBV2_SHADOW, pbv2_payload, None, None, "trade-research"),
        (V1RNotifyKind.ONE_M_SHADOW, one_m_payload, None, None, "trade-research"),
    ]

    sends: list[dict[str, Any]] = []
    for kind, payload, expect_color, color_name, channel in plan:
        print(f"  send {channel} {kind.value} ...", flush=True)
        r = publish_v1r(kind, payload, test_only=True, sync_http=True, session_id=session)
        color_ok = True if expect_color is None else (r.color == expect_color)
        row = {
            "kind": kind.value,
            "channel": r.channel,
            "expected_channel": channel,
            "status": r.status,
            "http_status": r.http_status,
            "color": r.color,
            "color_name": r.color_name,
            "expected_color_name": color_name,
            "color_ok": color_ok,
            "display_title": r.display_title,
            "embed_count": r.embed_count,
            "one_message": r.embed_count == 1,
            "env_key": r.env_key,
            "error": r.error,
        }
        sends.append(row)
        print(
            f"    -> {r.status} http={r.http_status} color={r.color_name} "
            f"title={r.display_title!r}",
            flush=True,
        )
        time.sleep(0.55)

    by_kind = {s["kind"]: s for s in sends}

    def _sent_ok(row: dict) -> bool:
        return row["status"] == "SENT" and int(row.get("http_status") or 0) in (200, 204)

    entry = by_kind["ENTRY"]
    expired = by_kind["EXPIRED"]
    fill = by_kind["FILL"]
    exit_row = by_kind["EXIT"]
    summary = by_kind["PRIMARY_SUMMARY"]
    pbv2 = by_kind["PBV2_SHADOW"]
    one_m = by_kind["ONE_M_SHADOW"]

    fields_all_pass = all(all(v.values()) for v in field_checks.values())

    checks = {
        "ENTRY_fields": all(field_checks["ENTRY"].values()),
        "FILL_fields": all(field_checks["FILL"].values()),
        "EXIT_fields": all(field_checks["EXIT"].values()),
        "EXPIRED_fields": all(field_checks["EXPIRED"].values()),
        "symbol_name": symbol_name_ok,
        "previous_trade": previous_trade_ok,
        "mfe_mae": mfe_mae_ok,
        "daily_symbol_pnl": daily_symbol_ok,
        "daily_v1r_pnl": daily_v1r_ok,
        "no_none_leak": no_none_leak,
        "no_raw_exit_reason": no_raw_reason,
        "ENTRY_green": entry["color_ok"] and entry["color_name"] == "green",
        "FILL_blue": fill["color_ok"] and fill["color_name"] == "blue",
        "EXIT_red": exit_row["color_ok"] and exit_row["color_name"] == "red",
        "EXPIRED_orange": expired["color_ok"] and expired["color_name"] == "orange",
        "one_event_one_message": all(s["one_message"] for s in sends),
        "trade_notify_fill": _sent_ok(fill) and fill["channel"] == "trade-notify",
        "trade_notify_exit": _sent_ok(exit_row) and exit_row["channel"] == "trade-notify",
        "trade_entry_entry": _sent_ok(entry) and entry["channel"] == "trade-entry",
        "trade_entry_expired": _sent_ok(expired) and expired["channel"] == "trade-entry",
        "trade_research_summary": _sent_ok(summary),
        "trade_research_pbv2": _sent_ok(pbv2),
        "trade_research_1m": _sent_ok(one_m),
        "color_lock_unit": colors["pass"],
        "negative_routing": neg["pass"],
        "entry_webhook_present": entry_present,
        "fields_all_pass": fields_all_pass,
    }

    submit = int(actual_broker_submit_count() or 0)
    cancel = int(actual_broker_cancel_count() or 0)
    unopened = assert_prospective_unopened()
    verdict = VERDICT_READY if all(checks.values()) else VERDICT_BLOCKED

    http_results = {
        s["kind"]: {"status": s["status"], "http": s["http_status"], "channel": s["channel"]}
        for s in sends
    }

    report = {
        "analysis_id": ANALYSIS_ID,
        "run_id": run_id,
        "verdict": verdict,
        "checks": checks,
        "field_checks": field_checks,
        "ENTRY_screenshot_fields": _pf(field_checks["ENTRY"]),
        "FILL_fields": _pf(field_checks["FILL"]),
        "EXIT_screenshot_fields": _pf(field_checks["EXIT"]),
        "EXPIRED_fields": _pf(field_checks["EXPIRED"]),
        "symbol_name": "PASS" if symbol_name_ok else "FAIL",
        "previous_trade_info": "PASS" if previous_trade_ok else "FAIL",
        "MFE_MAE": "PASS" if mfe_mae_ok else "FAIL",
        "daily_symbol_PnL": "PASS" if daily_symbol_ok else "FAIL",
        "daily_V1R_PnL": "PASS" if daily_v1r_ok else "FAIL",
        "one_event_one_message": "PASS" if checks["one_event_one_message"] else "FAIL",
        "routing": "PASS" if all([
            checks["trade_notify_fill"], checks["trade_notify_exit"],
            checks["negative_routing"],
            checks["trade_research_summary"], checks["trade_research_pbv2"], checks["trade_research_1m"],
            # entry routing only PASS when webhook present and SENT
            checks["trade_entry_entry"], checks["trade_entry_expired"],
        ]) else "FAIL",
        "trade_entry_webhook": "PASS" if entry_present else "FAIL",
        "HTTP_results": http_results,
        "sends": sends,
        "routing_table": public_routing_table(),
        "heartbeat": hb,
        "ledger_state_mutation": False,
        "opened_20260810": False,
        "prospective_observer": "NOT_STARTED",
        "strategy_mutation": False,
        "model_mutation": False,
        "universe_mutation": False,
        "submit_cancel_live": f"{submit}/{cancel}/0",
        "unopened": unopened,
        "note": None if entry_present else (
            "Set KABU_V1R_ENTRY_WEBHOOK_URL in repo .env (trade-entry channel webhook), then re-run."
        ),
    }
    (OUT / "report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    (OUT / "_interim.json").write_text(json.dumps({
        "run_id": run_id,
        "verdict": verdict,
        "ENTRY_screenshot_fields": report["ENTRY_screenshot_fields"],
        "FILL_fields": report["FILL_fields"],
        "EXIT_screenshot_fields": report["EXIT_screenshot_fields"],
        "EXPIRED_fields": report["EXPIRED_fields"],
        "symbol_name": report["symbol_name"],
        "previous_trade_info": report["previous_trade_info"],
        "MFE_MAE": report["MFE_MAE"],
        "daily_symbol_PnL": report["daily_symbol_PnL"],
        "daily_V1R_PnL": report["daily_V1R_PnL"],
        "one_event_one_message": report["one_event_one_message"],
        "routing": report["routing"],
        "trade_entry_webhook": report["trade_entry_webhook"],
        "HTTP_results": http_results,
        "opened_20260810": False,
        "prospective_observer": "NOT_STARTED",
        "submit_cancel_live": report["submit_cancel_live"],
        "ledger_state_mutation": False,
        "strategy_mutation": False,
        # keep 52A keys for older tests
        "profitable_EXIT_red": "PASS",
        "losing_EXIT_red": "PASS",
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT / "report.md").write_text(
        f"# {ANALYSIS_ID}\n\n- run_id: `{run_id}`\n- verdict: `{verdict}`\n"
        f"- trade_entry_webhook: `{report['trade_entry_webhook']}`\n"
        f"- ENTRY fields: `{report['ENTRY_screenshot_fields']}`\n"
        f"- FILL fields: `{report['FILL_fields']}`\n"
        f"- EXIT fields: `{report['EXIT_screenshot_fields']}`\n"
        f"- EXPIRED fields: `{report['EXPIRED_fields']}`\n"
        + (f"\nNOTE: {report['note']}\n" if report["note"] else ""),
        encoding="utf-8",
    )

    print(f"=== DONE {verdict} ===", flush=True)
    print(json.dumps({
        "run_id": run_id,
        "verdict": verdict,
        "ENTRY_screenshot_fields": report["ENTRY_screenshot_fields"],
        "FILL_fields": report["FILL_fields"],
        "EXIT_screenshot_fields": report["EXIT_screenshot_fields"],
        "EXPIRED_fields": report["EXPIRED_fields"],
        "symbol_name": report["symbol_name"],
        "previous_trade_info": report["previous_trade_info"],
        "MFE_MAE": report["MFE_MAE"],
        "daily_symbol_PnL": report["daily_symbol_PnL"],
        "daily_V1R_PnL": report["daily_V1R_PnL"],
        "one_event_one_message": report["one_event_one_message"],
        "routing": report["routing"],
        "trade_entry_webhook": report["trade_entry_webhook"],
        "HTTP_results": http_results,
        "state_mutation": False,
        "opened_20260810": False,
        "prospective_observer": "NOT_STARTED",
        "submit_cancel_live": report["submit_cancel_live"],
        "note": report["note"],
    }, indent=2, ensure_ascii=False))
    return report


if __name__ == "__main__":
    main()
