#!/usr/bin/env python3
"""
Phase280: Universe Refresh watch list — one symbol per line + paginated embed fields.

Usage:
  python kabu_native/scripts/run_phase280_universe_refresh_readability_fix.py
  python kabu_native/scripts/run_phase280_universe_refresh_readability_fix.py --offline-only
"""

from __future__ import annotations

import argparse
import json
import os
import sys
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


def _build_sample(repo_root: Path) -> dict[str, Any]:
    from small_paper.discord_message_builder import (
        build_universe_refresh_detail,
        build_universe_refresh_overview,
        preview_payload,
        split_watch_symbols_discord_fields,
    )
    from small_paper.discord_symbol_names import load_symbol_name_map

    name_map = load_symbol_name_map(repo_root=repo_root)
    watch = [f"{7200 + i}.T" for i in range(40)]
    watch[0] = "7203.T"
    watch[1] = "4062.T"
    watch[2] = "3905.T"

    overview = build_universe_refresh_overview(
        session_label="AM",
        refresh_time="10:00",
        added=["3719.T", "4263.T"],
        removed=["2667.T"],
        watch_symbol_count=len(watch),
        name_map=name_map,
    )
    watch_fields = split_watch_symbols_discord_fields(watch, name_map=name_map)
    full_detail = build_universe_refresh_detail(
        session_label="AM",
        refresh_time="10:00",
        added=["3719.T", "4263.T"],
        removed=["2667.T"],
        watch_symbols=watch,
        name_map=name_map,
    )
    preview = preview_payload(
        event_tag="Universe Refresh",
        title_line="【Universe Refresh】 AM 10:00 [Phase280]",
        detail=full_detail,
        color=0x3182CE,
    )
    return {
        "overview": overview,
        "watch_embed_fields": watch_fields,
        "full_detail": full_detail,
        "preview": preview,
        "watch": watch,
    }


def _send_live(*, config_path: Path, watch: list[str]) -> dict[str, Any]:
    from small_paper.config import load_pilot_config
    from small_paper.discord_notifier import SmallPaperDiscordNotifier, discord_config_from_pilot

    cfg = load_pilot_config(config_path)
    cfg = replace(
        cfg,
        discord_enabled=True,
        discord_observer_only=True,
        discord_send_universe_refresh=True,
    )
    dcfg = discord_config_from_pilot(cfg)
    dcfg = replace(dcfg, enabled=True, cooldown_sec=0.0)
    n = SmallPaperDiscordNotifier(
        dcfg,
        profile=cfg.profile,
        entry_profile=cfg.entry_profile,
        policy_label=str(cfg.policy_label),
    )
    n._last_sent_mono.clear()
    sent = n.notify_universe_refresh(
        session_label="AM",
        refresh_time="10:00",
        added_symbols=["3719.T", "4263.T"],
        removed_symbols=["2667.T"],
        watch_symbols=watch,
        status="completed",
    )
    return {"event": "Universe_Refresh", "sent": sent}


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase280 Universe Refresh readability")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--offline-only", action="store_true")
    parser.add_argument("--webhook-env", default=_DEFAULT_WEBHOOK_ENV)
    args = parser.parse_args()

    native_root, repo_root = _bootstrap()
    _load_env(repo_root)
    config_path = args.config or (native_root / "configs" / "small_paper_pilot_q070_cap3.yaml")
    if not config_path.is_absolute():
        config_path = repo_root / config_path

    sample = _build_sample(repo_root)
    detail = sample["full_detail"]
    fields = sample["watch_embed_fields"]

    checklist = {
        "one_symbol_per_line": "\n01." in detail and "\n02." in detail,
        "no_comma_separated_watch_row": not any(
            ", " in ln and ln.count(".") >= 2
            for ln in detail.split("\n")
            if ln.strip().startswith(("01.", "02.", "10.", "20.", "30."))
        ),
        "added_one_per_line": "+ 3719" in sample["overview"] and "\n+ " in sample["overview"],
        "removed_one_per_line": "- 2667" in sample["overview"],
        "watch_split_into_4_fields": len(fields) == 4,
        "watch_field_names_paginated": all(
            f"監視銘柄一覧 {i}/4" == fields[i - 1]["name"] for i in range(1, 5)
        ),
        "watch_chunks_have_range_header": all(
            "〜" in f["value"].split("\n", 1)[0] for f in fields
        ),
        "all_field_values_within_limit": all(
            len(f["value"]) <= _DISCORD_FIELD_MAX for f in fields
        ),
    }

    env_name = (args.webhook_env or _DEFAULT_WEBHOOK_ENV).strip()
    webhook_set = bool((os.getenv(env_name) or "").strip())
    send_result: dict[str, Any] = {}
    if args.offline_only:
        send_mode = "offline_only"
    elif not webhook_set:
        send_mode = "webhook_missing"
    else:
        send_mode = "live_webhook"
        try:
            send_result = _send_live(config_path=config_path, watch=sample["watch"])
        except Exception as e:
            send_result = {"event": "Universe_Refresh", "sent": False, "error": str(e)}

    checklist_ok = all(checklist.values())
    sent_ok = send_result.get("sent") is True

    report = {
        "phase": 280,
        "title": "Universe Refresh readability — one symbol per line",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "send_mode": send_mode,
        "webhook_env": env_name,
        "webhook_configured": webhook_set,
        "config_path": str(config_path),
        "verdict": (
            "live_send_ok"
            if send_mode == "live_webhook" and sent_ok and checklist_ok
            else (
                "offline_validation_ok"
                if send_mode in ("offline_only", "webhook_missing") and checklist_ok
                else "needs_attention"
            )
        ),
        "operator_checklist": checklist,
        "send_result": send_result,
        "overview_sample": sample["overview"],
        "watch_embed_fields": fields,
        "discord_sample": sample["preview"],
        "notes": [
            "監視銘柄: 01. code name per line; embed split 10 symbols per field (1/4 … 4/4).",
            "追加/削除: + / - prefix, one line each.",
            "Screenshot on Discord recommended for final readability sign-off.",
        ],
    }

    out_path = (
        repo_root / "kabu_native/results/reports/phase280_universe_refresh_readability_fix.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out_path}")
    print(f"send_mode={send_mode} verdict={report['verdict']}")
    for k, v in checklist.items():
        print(f"  {k}: {v}")
    if send_result:
        print(f"  sent={send_result.get('sent')}")
    return 0 if report["verdict"] in ("live_send_ok", "offline_validation_ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
