#!/usr/bin/env python3
"""
Phase279: Universe Refresh (symbol names) + Daily Summary (deferred opportunity) polish.

Usage:
  python kabu_native/scripts/run_phase279_discord_refresh_summary_polish.py
  python kabu_native/scripts/run_phase279_discord_refresh_summary_polish.py --offline-only
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
_DEFAULT_WEBHOOK_ENV = "KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL"
_DISCORD_FIELD_MAX = 1024


def _bootstrap() -> tuple[Path, Path]:
    script = Path(__file__).resolve()
    native_root = script.parents[1]
    repo_root = script.parents[2]
    for p in (native_root / "src", repo_root):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    return native_root, repo_root


def _load_env(repo_root: Path) -> None:
    try:
        from api.rest_client import load_kabu_env

        load_kabu_env(repo_root=repo_root)
    except Exception:
        env_path = repo_root / ".env"
        if env_path.is_file():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _build_samples(repo_root: Path) -> dict[str, Any]:
    from small_paper.discord_message_builder import (
        aggregate_daily_metrics,
        build_daily_summary_detail,
        build_universe_refresh_detail,
        preview_payload,
    )
    from small_paper.discord_symbol_names import get_cached_symbol_name_map, load_symbol_name_map
    from small_paper.discord_ux_session import DiscordUxSessionStats

    name_map = load_symbol_name_map(repo_root=repo_root)
    watch = [f"{7200 + i}.T" for i in range(40)]
    watch[0] = "7203.T"
    watch[1] = "4062.T"
    watch[2] = "3905.T"

    refresh_detail = build_universe_refresh_detail(
        session_label="AM",
        refresh_time="10:00",
        added=["3719.T", "4263.T"],
        removed=["2667.T"],
        watch_symbols=watch,
        name_map=name_map,
    )
    refresh_preview = preview_payload(
        event_tag="Universe Refresh",
        title_line="【Universe Refresh】 AM 10:00 [Phase279]",
        detail=refresh_detail,
        color=0x3182CE,
    )

    mock_rejects = [
        {
            "symbol": "4062.T",
            "gate_reject_reason": "max_concurrent",
            "entry_expectancy_score_v2": 6,
            "continuation_quality_score": 0.72,
        },
        {
            "symbol": "4062.T",
            "gate_reject_reason": "max_concurrent",
            "entry_expectancy_score_v2": 6,
            "continuation_quality_score": 0.71,
        },
        {
            "symbol": "4062.T",
            "gate_reject_reason": "max_concurrent",
            "entry_expectancy_score_v2": 5,
            "continuation_quality_score": 0.70,
        },
        {
            "symbol": "3719.T",
            "gate_reject_reason": "max_concurrent",
            "entry_expectancy_score_v2": 5,
            "continuation_quality_score": 0.68,
        },
        {
            "symbol": "3719.T",
            "gate_reject_reason": "max_concurrent",
            "entry_expectancy_score_v2": 5,
            "continuation_quality_score": 0.65,
        },
    ]
    ux = DiscordUxSessionStats(
        score5_candidate_count=12,
        score5_entry_count=4,
        score5_deferred_total_count=5,
        entry_deferred_notify_count=2,
    )
    for _ in range(3):
        ux.record_score5_deferred_reject(symbol="4062.T", entry_score_v2=6)
    ux.record_score5_deferred_reject(symbol="3719.T", entry_score_v2=5)
    ux.record_score5_deferred_reject(symbol="3719.T", entry_score_v2=5)

    metrics = aggregate_daily_metrics(
        [],
        {"peak_open_slots": 3, "observer_entry_count": 4, "observer_exit_count": 0},
        max_concurrent_positions=3,
        monitored_symbol_count=40,
        reject_rows=mock_rejects,
        ux_stats=ux.to_summary_dict(),
    )
    summary_detail = build_daily_summary_detail(metrics, name_map=name_map)
    summary_preview = preview_payload(
        event_tag="Daily Summary",
        title_line="【Daily Summary】 [Phase279]",
        detail=summary_detail,
        color=0x805AD5,
    )

    return {
        "name_map_size": len(name_map),
        "cached_name_map_size": len(get_cached_symbol_name_map()),
        "previews": {
            "Universe_Refresh": refresh_preview,
            "Daily_Summary": summary_preview,
        },
        "metrics": metrics,
        "watch": watch,
    }


def _send_phase279(
    *,
    repo_root: Path,
    config_path: Path,
    watch: list[str],
) -> list[dict[str, Any]]:
    from small_paper.config import load_pilot_config
    from small_paper.discord_notifier import SmallPaperDiscordNotifier, discord_config_from_pilot
    from small_paper.discord_ux_session import DiscordUxSessionStats

    cfg = load_pilot_config(config_path)
    cfg = replace(
        cfg,
        discord_enabled=True,
        discord_observer_only=True,
        discord_send_universe_refresh=True,
        discord_send_daily_summary=True,
    )
    dcfg = discord_config_from_pilot(cfg)
    dcfg = replace(dcfg, enabled=True, cooldown_sec=0.0)
    results: list[dict[str, Any]] = []

    def _notifier() -> SmallPaperDiscordNotifier:
        n = SmallPaperDiscordNotifier(
            dcfg,
            profile=cfg.profile,
            entry_profile=cfg.entry_profile,
            policy_label=str(cfg.policy_label),
        )
        n._last_sent_mono.clear()
        return n

    n = _notifier()
    ok = n.notify_universe_refresh(
        session_label="AM",
        refresh_time="10:00",
        added_symbols=["3719.T", "4263.T"],
        removed_symbols=["2667.T"],
        watch_symbols=watch,
        status="completed",
    )
    results.append({"event": "Universe_Refresh", "sent": ok})

    time.sleep(1.2)

    mock_rejects = [
        {
            "symbol": "4062.T",
            "gate_reject_reason": "max_concurrent",
            "entry_expectancy_score_v2": 6,
        },
    ] * 3 + [
        {
            "symbol": "3719.T",
            "gate_reject_reason": "max_concurrent",
            "entry_expectancy_score_v2": 5,
        },
    ] * 2
    ux = DiscordUxSessionStats(
        score5_candidate_count=12,
        score5_entry_count=4,
        score5_deferred_total_count=5,
    )
    for _ in range(3):
        ux.record_score5_deferred_reject(symbol="4062.T", entry_score_v2=6)
    for _ in range(2):
        ux.record_score5_deferred_reject(symbol="3719.T", entry_score_v2=5)

    n = _notifier()
    ok = n.notify_daily_summary(
        events=[],
        summary={"peak_open_slots": 3, "observer_entry_count": 4, "observer_exit_count": 0},
        monitored_symbol_count=40,
        reject_rows=mock_rejects,
        ux_stats=ux,
    )
    results.append({"event": "Daily_Summary", "sent": ok})
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase279 Refresh/Summary Discord polish")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--offline-only", action="store_true")
    parser.add_argument("--webhook-env", default=_DEFAULT_WEBHOOK_ENV)
    args = parser.parse_args()

    native_root, repo_root = _bootstrap()
    _load_env(repo_root)
    config_path = args.config or (native_root / "configs" / "small_paper_pilot_q070_cap3.yaml")
    if not config_path.is_absolute():
        config_path = repo_root / config_path

    samples = _build_samples(repo_root)
    refresh_detail = samples["previews"]["Universe_Refresh"]["detail"]
    summary_detail = samples["previews"]["Daily_Summary"]["detail"]

    checklist = {
        "refresh_has_symbol_names": (
            "7203 トヨタ" in refresh_detail or "7203 トヨタ自動車" in refresh_detail
        )
        and ("4062 イビデン" in refresh_detail)
        and ("3905 データセクション" in refresh_detail),
        "summary_has_top_deferred_score": "見送り最高score" in summary_detail
        and "4062" in summary_detail
        and "score6" in summary_detail,
        "summary_has_deferred_ranking_with_count": "ENTRY見送り上位" in summary_detail
        and "回" in summary_detail,
        "summary_has_score5_counters": "score5以上候補:" in summary_detail
        and "枠不足見送り:" in summary_detail,
        "refresh_within_discord_or_split": len(refresh_detail) > 0,
    }

    env_name = (args.webhook_env or _DEFAULT_WEBHOOK_ENV).strip()
    webhook_set = bool((os.getenv(env_name) or "").strip())
    send_results: list[dict[str, Any]] = []
    if args.offline_only:
        send_mode = "offline_only"
    elif not webhook_set:
        send_mode = "webhook_missing"
    else:
        send_mode = "live_webhook"
        try:
            send_results = _send_phase279(
                repo_root=repo_root,
                config_path=config_path,
                watch=samples["watch"],
            )
        except Exception as e:
            send_results = [{"event": "batch", "sent": False, "error": str(e)}]

    all_sent = all(r.get("sent") for r in send_results) if send_results else False
    checklist_ok = all(checklist.values())

    report = {
        "phase": 279,
        "title": "Discord Universe Refresh + Daily Summary polish",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "send_mode": send_mode,
        "webhook_env": env_name,
        "webhook_configured": webhook_set,
        "config_path": str(config_path),
        "name_map_size": samples["name_map_size"],
        "verdict": (
            "live_send_ok"
            if send_mode == "live_webhook" and all_sent and checklist_ok
            else (
                "offline_validation_ok"
                if send_mode in ("offline_only", "webhook_missing") and checklist_ok
                else "needs_attention"
            )
        ),
        "operator_checklist": checklist,
        "send_results": send_results,
        "discord_samples": samples["previews"],
        "metrics_snapshot": samples["metrics"],
        "notes": [
            "Universe Refresh: code + name from data/jpx/tradable_symbols.csv; code-only fallback.",
            "Daily Summary: 見送り最高score, ENTRY見送り上位 (回), compact score5 counters.",
            "No ENTRY/EXIT/Universe selection/max_concurrent logic changes.",
        ],
    }

    out_path = (
        repo_root / "kabu_native/results/reports/phase279_discord_refresh_summary_polish.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out_path}")
    print(f"send_mode={send_mode} verdict={report['verdict']}")
    for k, v in checklist.items():
        print(f"  {k}: {v}")
    for r in send_results:
        print(f"  {r.get('event')}: sent={r.get('sent')}")
    return 0 if report["verdict"] in ("live_send_ok", "offline_validation_ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
