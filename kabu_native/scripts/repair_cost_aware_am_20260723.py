"""Repair 2026-07-23 AM Cost-Aware V1/V2 shadow sections + Discord resend.

Canonical mainline fields are NOT modified. Paper / observe-only only.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NATIVE / "src"))

DAY = "20260723"
SESSION = "live_session_075933"
SESSION_DIR = NATIVE / "results" / "small_paper" / DAY / SESSION
REPORT_DIR = NATIVE / "results" / "reports" / "phase_723_cost_aware_am_pipeline"


def _patch_summary(path: Path, v1: dict, v2: dict) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    # Preserve canonical identity fields
    canonical_keys = [
        "accepted_count",
        "total_pnl_yen",
        "canonical_pnl",
        "stop_reason",
        "session_seal",
        "seal_status",
    ]
    before = {k: data.get(k) for k in canonical_keys}

    data["cost_aware_entry_shadow"] = v1
    data["cost_aware_entry_shadow_enabled"] = True
    data["cost_aware_shadow_entries_proxy"] = int(v1.get("shadow_entries") or 0)
    data["cost_aware_virtual_entry_count"] = int(v1.get("virtual_entry_count") or v1.get("shadow_entries") or 0)
    data["cost_aware_real_block_count"] = 0
    data["cost_aware_evaluable_count"] = int(v1.get("evaluable_count") or v1.get("n_closed") or 0)
    data["cost_aware_delta_proxy"] = v1.get("delta_total_5bps")
    data["cost_aware_entry_shadow_pf_delta"] = v1.get("pf_delta_5bps")
    data["cost_aware_status"] = v1.get("status")
    data["cost_aware_status_reason"] = v1.get("status_reason")
    data["cost_aware_runtime_compatible_pnl"] = v1.get("runtime_compatible_pnl")
    data["cost_aware_shadow_pnl_after_5bps"] = v1.get("pnl_after_5bps_30m")
    data["cost_aware_join_success_count"] = v1.get("join_success_count")
    data["cost_aware_join_failed_count"] = v1.get("join_failed_count")
    data["cost_aware_pending_count"] = v1.get("pending_count")

    data["cost_aware_entry_v2_shadow"] = v2
    data["cost_aware_entry_v2_shadow_enabled"] = bool(v2.get("enabled", True))

    after = {k: data.get(k) for k in canonical_keys}
    assert before == after, f"canonical mutated: {before} vs {after}"

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return data


def _send_discord_v2(summary: dict) -> dict:
    from notify.discord_notification_model import (
        ActualOrShadow,
        NotificationCategory,
        Severity,
        build_envelope,
    )
    from notify.discord_notification_router import DiscordNotificationRouter
    from small_paper.cost_aware_entry_v2_shadow import format_discord_lines

    lines = format_discord_lines(summary, am_pm="AM")
    content = "\n".join(lines)
    dedupe_key = f"{DAY}|{SESSION}|AM|cost_aware_v2_pipeline_repair_v1"
    router = DiscordNotificationRouter(NATIVE)
    env = build_envelope(
        category=NotificationCategory.RESEARCH_SHADOW,
        severity=Severity.INFO,
        event_type="COST_AWARE_V2_SHADOW_AM_REPAIR",
        title="[Cost-Aware V2 Shadow - AM]",
        content=content,
        embeds=[],
        trading_date=DAY,
        session_id=SESSION,
        am_pm="AM",
        dedupe_key=dedupe_key,
        actual_or_shadow=ActualOrShadow.SHADOW,
        source_module="repair_cost_aware_am_20260723",
        ownership="RESEARCH",
        extra={"auto_resend": False, "observe_only": True},
    )
    outcome = router.publish(env)
    # Ensure HTTP flush before exit
    try:
        router.worker.stop(flush_sec=20)
    except Exception as exc:
        outcome["flush_error"] = str(exc)
    outcome["dedupe_key"] = dedupe_key
    outcome["content_preview"] = content[:500]
    return outcome


def main() -> int:
    from small_paper.cost_aware_shadow_enrich import enrich_cost_aware_v1_from_session
    from small_paper.cost_aware_v2_shadow_recompute import recompute_cost_aware_v2_session

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    print("enriching Cost-Aware V1 existing closed_trades...", flush=True)
    v1 = enrich_cost_aware_v1_from_session(SESSION_DIR, trading_date=DAY)
    # Keep closed_trades in report; omit bulky list from patched summary embed.
    v1_disk = dict(v1)
    closed = list(v1_disk.get("closed_trades") or [])
    (REPORT_DIR / "cost_aware_v1_closed_trades.json").write_text(
        json.dumps(closed, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # Keep a compact closed_trades sample in summary (full list stays in report).
    # Preserve all closed_trades so future enrich remains possible.
    v1_disk["closed_trades"] = closed

    print("rejoining Cost-Aware V2...", flush=True)
    v2 = recompute_cost_aware_v2_session(SESSION_DIR)

    print("patching summaries (shadow only)...", flush=True)
    summary = _patch_summary(SESSION_DIR / "small_paper_summary.json", v1_disk, v2)
    am_path = SESSION_DIR / "small_paper_summary_am.json"
    if am_path.is_file():
        _patch_summary(am_path, v1_disk, v2)

    metrics = {
        "session_dir": str(SESSION_DIR),
        "v1": {
            "shadow_entries": v1.get("shadow_entries"),
            "n_closed": v1.get("n_closed"),
            "n_open": v1.get("n_open"),
            "join_success_count": v1.get("join_success_count"),
            "join_failed_count": v1.get("join_failed_count"),
            "pending_count": v1.get("pending_count"),
            "delta_eligible_count": v1.get("delta_eligible_count"),
            "runtime_total_5bps": v1.get("runtime_total_5bps"),
            "cost_aware_total_5bps": v1.get("cost_aware_total_5bps"),
            "delta_total_5bps": v1.get("delta_total_5bps"),
            "runtime_pf_5bps": v1.get("runtime_pf_5bps"),
            "cost_aware_pf_5bps": v1.get("cost_aware_pf_5bps"),
            "pf_delta_5bps": v1.get("pf_delta_5bps"),
            "status": v1.get("status"),
            "status_reason": v1.get("status_reason"),
            "join_failure_reasons": v1.get("join_failure_reasons"),
        },
        "v2": {
            "evaluated_candidates": v2.get("evaluated_candidates"),
            "join_success_count": v2.get("join_success_count"),
            "join_failed_count": v2.get("join_failed_count"),
            "pending_count": v2.get("pending_count"),
            "delta_eligible_count": v2.get("delta_eligible_count"),
            "fail_open_count": v2.get("fail_open_count"),
            "H_board_ts": {
                "keep": (v2.get("H_board_ts") or {}).get("keep"),
                "reject": (v2.get("H_board_ts") or {}).get("reject"),
            },
            "I_price_board": {
                "keep": (v2.get("I_price_board") or {}).get("keep"),
                "reject": (v2.get("I_price_board") or {}).get("reject"),
            },
            "runtime_total_5bps": v2.get("runtime_total_5bps"),
            "cost_aware_total_5bps": v2.get("cost_aware_total_5bps"),
            "delta_total_5bps": v2.get("delta_total_5bps"),
            "runtime_pf_5bps": v2.get("runtime_pf_5bps"),
            "cost_aware_pf_5bps": v2.get("cost_aware_pf_5bps"),
            "pf_delta_5bps": v2.get("pf_delta_5bps"),
            "status": v2.get("status"),
            "submit": v2.get("submit"),
            "cancel": v2.get("cancel"),
            "live_order": v2.get("live_order"),
        },
        "canonical_untouched": True,
    }
    (REPORT_DIR / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2))

    do_send = os.environ.get("COST_AWARE_DISCORD_RESEND", "1") == "1"
    discord_out = {"skipped": True}
    if do_send:
        print("sending Cost-Aware V2 Discord (research)...", flush=True)
        discord_out = _send_discord_v2(summary)
        (REPORT_DIR / "discord_resend.json").write_text(
            json.dumps(discord_out, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(discord_out, ensure_ascii=False, indent=2, default=str))

    v1_ready = (
        int(v1.get("join_success_count") or 0) > 0
        and v1.get("delta_total_5bps") is not None
        and int(v1.get("pending_count") or 0) == 0
    )
    v2_ready = int(v2.get("pending_count") or 0) == 0 and v2.get("delta_total_5bps") is not None
    verdict = (
        "COST_AWARE_AM_PIPELINE_COMPLETED"
        if v1_ready and v2_ready
        else "COST_AWARE_AM_PIPELINE_STILL_PARTIAL"
    )
    metrics["verdict"] = verdict
    (REPORT_DIR / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("VERDICT", verdict)
    return 0 if verdict == "COST_AWARE_AM_PIPELINE_COMPLETED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
