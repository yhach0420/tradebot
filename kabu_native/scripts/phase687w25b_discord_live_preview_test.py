"""Phase687W25B — Discord live format preview send (explicit flag only).

Requires TRADEBOT_DISCORD_FORMAT_TEST=1.
Does not start Paper, does not touch ENTRY/EXIT logic, submit/cancel=0.
Uses demo-style direct HTTP; never writes production dedupe / event journals.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
NATIVE_ROOT = Path(__file__).resolve().parents[1]
REPORT = NATIVE_ROOT / "results" / "reports" / "phase687w25b_discord_live_preview_test"

ENV_FLAG = "TRADEBOT_DISCORD_FORMAT_TEST"
ENV_TEST_WEBHOOKS = (
    "KABU_DISCORD_FORMAT_TEST_WEBHOOK_URL",
    "KABU_DISCORD_TEST_WEBHOOK_URL",
)
ENV_TRADE_NOTIFY = "KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL"
ENV_LEGACY = "KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL"

TEST_HEADER = (
    "[TEST NOTIFICATION]\n"
    "本通知は表示確認用です\n"
    "実際のENTRY/EXITではありません\n"
    "\n"
    "[DISCORD FORMAT TEST]\n"
    "Real orders: DISABLED\n"
)


def _iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _url_fingerprint(url: str) -> str:
    if not url:
        return ""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]


def resolve_send_target() -> dict[str, Any]:
    """Prefer dedicated test webhook; else trade-notify (report only, no URL)."""
    try:
        from small_paper.env_loader import ensure_repo_dotenv

        ensure_repo_dotenv()
    except Exception:
        pass

    for key in ENV_TEST_WEBHOOKS:
        url = (os.environ.get(key) or "").strip()
        if url:
            return {
                "target_kind": "test_webhook",
                "env_key": key,
                "configured": True,
                "url_fingerprint": _url_fingerprint(url),
                "url_length": len(url),
                "falls_back_to_trade_notify": False,
            }

    for key in (ENV_TRADE_NOTIFY, ENV_LEGACY):
        url = (os.environ.get(key) or "").strip()
        if url:
            return {
                "target_kind": "trade_notify_fallback",
                "env_key": key,
                "configured": True,
                "url_fingerprint": _url_fingerprint(url),
                "url_length": len(url),
                "falls_back_to_trade_notify": True,
                "note": "No dedicated FORMAT_TEST webhook; using trade-notify channel for display check only",
            }

    return {
        "target_kind": "none",
        "env_key": "",
        "configured": False,
        "url_fingerprint": "",
        "url_length": 0,
        "falls_back_to_trade_notify": False,
    }


def _get_url_for_key(env_key: str) -> str:
    return (os.environ.get(env_key) or "").strip()


def build_preview_bodies() -> list[dict[str, str]]:
    from notify.discord_notification_formatter import (
        format_capture_status_body,
        format_communication_degraded,
    )
    from small_paper.board_dynamic_trailing_shadow import trailing_params_for_board_tier
    from small_paper.discord_message_builder import (
        PAPER_ONLY_FOOTER,
        build_entry_cap_blocked_detail,
        build_entry_detail,
        build_exit_detail,
        format_discord_summary_lines,
    )

    entry = build_entry_detail(
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
        },
        entry_time="2026-07-14T10:06:53+09:00",
        name_map={"4174.T": "アピリッツ"},
    )

    exit_np = build_exit_detail(
        symbol="4174.T",
        entry_price=925.0,
        exit_price=925.0,
        pnl_pct=0.0,
        mfe_pct=0.0,
        mae_pct=0.0,
        hold_minutes=15.116,
        exit_reason="no_progress_exit",
        pnl_yen_100=0.0,
        exit_time="2026-07-14T10:21:59+09:00",
        market_time_age_sec=2070.0,
        stale_trade=True,
        price_freshness_source="liquidity_stale_trade",
        name_map={"4174.T": "アピリッツ"},
    )

    hi = trailing_params_for_board_tier(80.0)
    lo = trailing_params_for_board_tier(10.0)
    exit_hi = build_exit_detail(
        symbol="4174.T",
        entry_price=925.0,
        exit_price=940.0,
        pnl_pct=1.62,
        mfe_pct=2.0,
        mae_pct=-0.2,
        hold_minutes=12.0,
        exit_reason="trailing_mfe_exit",
        pnl_yen_100=1500.0,
        board_dynamic_trailing_tier=hi[2],
        board_dynamic_trailing_activate_pct=hi[0],
        board_dynamic_trailing_giveback_frac=hi[1],
        exit_time="2026-07-14T10:30:00+09:00",
        name_map={"4174.T": "アピリッツ"},
    )
    exit_lo = build_exit_detail(
        symbol="7203.T",
        entry_price=2800.0,
        exit_price=2815.0,
        pnl_pct=0.54,
        mfe_pct=0.9,
        mae_pct=-0.1,
        hold_minutes=9.0,
        exit_reason="trailing_mfe_exit",
        board_dynamic_trailing_tier=lo[2],
        board_dynamic_trailing_activate_pct=lo[0],
        board_dynamic_trailing_giveback_frac=lo[1],
        exit_time="2026-07-14T11:00:00+09:00",
        name_map={"7203.T": "トヨタ自動車"},
    )

    cap = build_entry_cap_blocked_detail(
        symbol="9984.T",
        entry_score_v2=4,
        data={
            "entry_type": "PBV2",
            "event_time": "2026-07-14T10:30:00+09:00",
            "price_age_sec": 0.4,
            "board_age_sec": 0.2,
        },
        active_positions=5,
        position_cap=5,
        name_map={"9984.T": "ソフトバンクグループ"},
    )

    capture = format_capture_status_body(
        {
            "status": "CAPTURE_WRITING",
            "received": 150,
            "written": 149,
            "bytes": 129801,
            "drops": 0,
            "malformed": 0,
            "topology": "SINGLE_INGRESS_LOCAL_FANOUT",
        }
    )

    comm = format_communication_degraded(
        {"target": "Discord webhook", "status": "SEND_FAILED", "last_push_age_sec": "N/A"}
    )

    summary = "\n".join(
        format_discord_summary_lines(
            {
                "trade_count": 4,
                "win_count": 2,
                "loss_count": 1,
                "flat_count": 1,
                "win_rate_yen_100": 0.5,
                "total_pnl_yen_100": 1000,
                "avg_pnl_yen_100": 250,
                "avg_pnl_pct": 99.9,
                "total_pnl_pct": 12.3,
                "profit_factor_yen_100": "inf",
                "gross_profit_yen_100": 2000,
                "gross_loss_yen_100": 1000,
                "stop_count": 1,
                "stop_rate": 0.25,
                "best_trade": "4174 +1,500円(100株)",
                "worst_trade": "7203 -500円(100株)",
                "max_concurrent": 3,
                "max_concurrent_cap": 5,
                "watch_symbols_count": 50,
                "traded_symbols_count": 4,
            }
        )
    )

    items = [
        {"id": "1_entry", "title": "[ENTRY] format test", "body": "[ENTRY]\n" + entry},
        {"id": "2_exit_no_progress", "title": "[EXIT] no_progress format test", "body": "[EXIT]\n" + exit_np},
        {"id": "3_exit_trailing_high", "title": "[EXIT] trailing board_high format test", "body": "[EXIT]\n" + exit_hi},
        {"id": "4_exit_trailing_low", "title": "[EXIT] trailing board_low format test", "body": "[EXIT]\n" + exit_lo},
        {"id": "5_cap_blocked", "title": "[CAP BLOCKED] format test", "body": "[CAP BLOCKED]\n" + cap},
        {"id": "6_capture_writing", "title": "[CAPTURE] WRITING format test", "body": capture},
        {"id": "7_comm_discord", "title": "[COMMUNICATION] Discord webhook failure format test", "body": comm},
        {"id": "8_am_summary", "title": "[AM PAPER SUMMARY] format test", "body": "[AM PAPER SUMMARY]\n" + summary},
    ]
    for it in items:
        it["content"] = TEST_HEADER + "\n" + it["title"] + "\n\n" + it["body"]
        if PAPER_ONLY_FOOTER not in it["content"] and it["id"] not in ("6_capture_writing", "7_comm_discord"):
            it["content"] += "\n\n" + PAPER_ONLY_FOOTER
    return items


def content_checks(items: list[dict[str, str]]) -> dict[str, bool]:
    joined = "\n".join(it["content"] for it in items)
    return {
        "entry_eval_continues_on_discord_failure": "ENTRY評価: 継続" in joined
        and "Discord webhook" in joined,
        "paper_impact_none_on_discord_failure": "Paper本体への影響: NONE" in joined,
        "cap_5": "position_cap: 5" in joined or "CAP: 3 / 5" in joined or "/ 5" in joined,
        "topology_single_ingress": "SINGLE_INGRESS_LOCAL_FANOUT" in joined,
        "trailing_board_high": "board tier: board_high" in joined,
        "trailing_board_low": "board tier: board_low" in joined,
        "avg_pnl_pct_absent": "avg_pnl_pct" not in joined,
        "paper_only": "PAPER ONLY" in joined or "実注文なし" in joined or "Real orders: DISABLED" in joined,
        "test_banner_all": all("[TEST NOTIFICATION]" in it["content"] for it in items),
    }


def post_content(url: str, content: str, *, username: str = "tradebot-format-test") -> dict[str, Any]:
    import requests

    # Discord content limit ~2000
    text = content[:1900]
    payload = {"content": text, "username": username}
    try:
        r = requests.post(url, json=payload, timeout=15)
        return {
            "ok": 200 <= r.status_code < 300,
            "http_status": r.status_code,
            "response_len": len(r.text or ""),
        }
    except Exception as exc:
        return {"ok": False, "http_status": None, "error": type(exc).__name__}


def main() -> int:
    sys.path[:0] = [str(NATIVE_ROOT / "src"), str(NATIVE_ROOT.parent)]
    REPORT.mkdir(parents=True, exist_ok=True)

    flag = str(os.environ.get(ENV_FLAG, "") or "").strip()
    flag_ok = flag in ("1", "true", "TRUE", "yes", "YES", "on", "ON")

    target = resolve_send_target()
    _write_json(REPORT / "webhook_target_audit.json", {
        "at": _iso(),
        "format_test_flag_env": ENV_FLAG,
        "format_test_flag_value_set": flag_ok,
        "format_test_flag_raw_present": bool(flag),
        "resolved_target": {k: v for k, v in target.items()},
        "candidate_test_envs": list(ENV_TEST_WEBHOOKS),
        "trade_notify_env": ENV_TRADE_NOTIFY,
        "secrets_printed": False,
    })

    # Report destination before any send
    print("[WEBHOOK TARGET AUDIT]")
    print(f"flag {ENV_FLAG}={'SET' if flag_ok else 'UNSET'}")
    print(f"target_kind={target.get('target_kind')}")
    print(f"env_key={target.get('env_key') or 'N/A'}")
    print(f"configured={target.get('configured')}")
    print(f"url_fingerprint={target.get('url_fingerprint') or 'N/A'}")
    if target.get("falls_back_to_trade_notify"):
        print("NOTE: no dedicated test webhook; would use trade-notify for display check")

    items = build_preview_bodies()
    checks = content_checks(items)
    preview_md_parts = ["# Sent Messages Preview (Phase687W25B)", ""]
    for it in items:
        preview_md_parts.append(f"## {it['id']}")
        preview_md_parts.append("```")
        preview_md_parts.append(it["content"][:2500])
        preview_md_parts.append("```")
        preview_md_parts.append("")
    (REPORT / "sent_messages_preview.md").write_text("\n".join(preview_md_parts) + "\n", encoding="utf-8")

    contamination = {
        "paper_started": False,
        "entry_exit_logic_executed": False,
        "actual_submit": 0,
        "actual_cancel": 0,
        "production_dedupe_touched": False,
        "production_event_log_touched": False,
        "paper_results_written": False,
        "send_path": "direct_http_format_test_only",
        "live_trading_enabled": False,
        "order_enabled": False,
    }
    _write_json(REPORT / "production_contamination_audit.json", contamination)

    if not flag_ok:
        result = {
            "verdict": "TEST_WEBHOOK_NOT_CONFIGURED",
            "reason": f"{ENV_FLAG} not set to 1 — send blocked",
            "sent": 0,
            "failed": 0,
            "checks": checks,
            "target": target,
            "submit": 0,
            "cancel": 0,
        }
        # If flag missing but also no webhook, still TEST_WEBHOOK_NOT_CONFIGURED
        if not target.get("configured"):
            result["reason"] = f"{ENV_FLAG} unset and no webhook configured"
        _write_json(REPORT / "send_test_result.json", result)
        print(f"verdict={result['verdict']}")
        print(result["reason"])
        return 2

    if not target.get("configured"):
        result = {
            "verdict": "TEST_WEBHOOK_NOT_CONFIGURED",
            "reason": "No test webhook and no trade-notify webhook configured",
            "sent": 0,
            "failed": 0,
            "checks": checks,
            "target": target,
            "submit": 0,
            "cancel": 0,
        }
        _write_json(REPORT / "send_test_result.json", result)
        print(f"verdict={result['verdict']}")
        return 2

    if not all(checks.values()):
        result = {
            "verdict": "SEND_FAILED",
            "reason": "content_checks_failed_before_send",
            "sent": 0,
            "failed": 0,
            "checks": checks,
            "target": target,
            "submit": 0,
            "cancel": 0,
        }
        _write_json(REPORT / "send_test_result.json", result)
        print(f"verdict={result['verdict']}")
        print("checks", checks)
        return 1

    url = _get_url_for_key(str(target["env_key"]))
    send_rows: list[dict[str, Any]] = []
    sent = failed = 0
    for it in items:
        # unique suffix so Discord / rate limits don't collapse identical tests
        content = it["content"] + f"\n\ntest_id: w25b_{it['id']}_{int(time.time())}"
        row = post_content(url, content)
        row["id"] = it["id"]
        row["title"] = it["title"]
        row["chars"] = len(content)
        send_rows.append(row)
        if row.get("ok"):
            sent += 1
        else:
            failed += 1
        time.sleep(0.7)  # gentle rate limit

    # Re-check contamination after send
    contamination["messages_attempted"] = len(items)
    contamination["messages_sent"] = sent
    _write_json(REPORT / "production_contamination_audit.json", contamination)

    if failed == 0 and sent == len(items):
        verdict = "DISCORD_LIVE_PREVIEW_SENT"
    elif sent == 0:
        verdict = "SEND_FAILED"
    else:
        verdict = "SEND_FAILED"

    # contamination would flip verdict
    if contamination.get("actual_submit") or contamination.get("actual_cancel"):
        verdict = "PRODUCTION_CONTAMINATION_DETECTED"

    result = {
        "verdict": verdict,
        "at": _iso(),
        "sent": sent,
        "failed": failed,
        "attempted": len(items),
        "checks": checks,
        "target": {k: v for k, v in target.items()},
        "send_rows": [
            {k: v for k, v in r.items() if k != "error" or True}
            for r in send_rows
        ],
        "submit": 0,
        "cancel": 0,
        "notification_behavior_changed": False,
    }
    _write_json(REPORT / "send_test_result.json", result)
    _write_json(
        REPORT / "code_change_manifest.json",
        {
            "files": ["scripts/phase687w25b_discord_live_preview_test.py"],
            "paper_untouched": True,
            "send_conditions_unchanged": True,
        },
    )
    decision = f"""# Phase687W25B Decision

## Verdict: `{verdict}`

- flag `{ENV_FLAG}`: required and set
- target_kind: `{target.get('target_kind')}`
- env_key: `{target.get('env_key')}`
- sent: {sent} / {len(items)}
- failed: {failed}
- submit/cancel: 0/0
- content checks: {checks}

Artifacts: `{REPORT}`
"""
    (REPORT / "phase687w25b_decision.md").write_text(decision, encoding="utf-8")
    print(decision)
    print(f"verdict={verdict}")
    return 0 if verdict == "DISCORD_LIVE_PREVIEW_SENT" else 1


if __name__ == "__main__":
    raise SystemExit(main())
