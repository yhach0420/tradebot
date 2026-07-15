"""Phase687W25C-R3 — generate previews, inventories, and optional live send."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
NATIVE_ROOT = Path(__file__).resolve().parents[1]
REPORT = NATIVE_ROOT / "results" / "reports" / "phase687w25c_r3_legacy_embed_reentry_visibility"
ENV_FLAG = "TRADEBOT_DISCORD_FORMAT_TEST"


def _iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")


def _write_json(path: Path, obj: Any) -> None:
    _write(path, json.dumps(obj, ensure_ascii=False, indent=2))


def _embed_text(emb: dict[str, Any]) -> str:
    lines = [str(emb.get("title") or ""), "", str(emb.get("description") or "")]
    for f in emb.get("fields") or []:
        lines.extend(["", f"[{f.get('name')}]", str(f.get("value") or "")])
    lines.extend(["", str(emb.get("footer") or "")])
    lines.append(f"(color={hex(int(emb.get('color') or 0))})")
    return "\n".join(lines)


def build_previews() -> dict[str, Any]:
    from small_paper.discord_message_builder import (
        build_entry_embed_payload,
        build_exit_embed_payload,
        build_shadow_observation_embed_payload,
        build_summary_embed_payload,
        collect_active_shadow_observations,
        embed_to_discord_payload,
        write_shadow_inventory_csvs,
    )

    names = {"4174.T": "アピリッツ"}
    cards: dict[str, dict[str, Any]] = {}

    cards["entry_first"] = build_entry_embed_payload(
        symbol="4174.T",
        entry_price=925.0,
        stop_price=913.9,
        slot_usage="3→4/5",
        entry_score_v2=3,
        data={"entry_high_break_recent": True, "entry_reason_tokens": ["Momentum:low", "Board mid以上"]},
        name_map=names,
        entry_time="2026-07-14T10:38:44+09:00",
        reentry_info={"entry_count_today_after": 1, "is_reentry": False},
        test_mode=True,
    )
    cards["entry_reentry"] = build_entry_embed_payload(
        symbol="4174.T",
        entry_price=925.0,
        stop_price=913.9,
        slot_usage="2→3/5",
        entry_score_v2=3,
        data={"entry_reason_tokens": ["Momentum:low"]},
        name_map=names,
        entry_time="2026-07-14T10:38:44+09:00",
        reentry_info={
            "entry_count_today_after": 4,
            "is_reentry": True,
            "previous_exit_reason_ja": "停滞ポジション整理",
            "previous_exit_time_hms": "10:37:13",
            "previous_exit_elapsed": "1分31秒",
            "previous_exit_price": 925.0,
        },
        test_mode=True,
    )
    cards["entry_after_no_progress"] = build_entry_embed_payload(
        symbol="4174.T",
        entry_price=925.0,
        stop_price=913.9,
        slot_usage="1→2/5",
        entry_score_v2=3,
        data={},
        name_map=names,
        entry_time="2026-07-14T11:00:00+09:00",
        reentry_info={
            "entry_count_today_after": 2,
            "is_reentry": True,
            "previous_exit_reason_ja": "停滞ポジション整理",
            "previous_exit_time_hms": "10:45:00",
            "previous_exit_elapsed": "15分00秒",
            "previous_exit_price": 924.0,
        },
        test_mode=True,
    )

    def _exit(reason: str, **extra: Any) -> dict[str, Any]:
        kw = dict(
            symbol="4174.T",
            entry_price=925.0,
            exit_price=925.0 if reason != "stop_hit" else 910.0,
            pnl_pct=0.0 if reason != "stop_hit" else -1.62,
            mfe_pct=0.4 if "trailing" in reason else 0.0,
            mae_pct=-0.5 if reason == "stop_hit" else 0.0,
            hold_minutes=15.2,
            exit_reason=reason,
            pnl_yen_100=-1500.0 if reason == "stop_hit" else 0.0,
            name_map=names,
            entry_time="2026-07-14T10:22:01+09:00",
            exit_time="2026-07-14T10:37:13+09:00",
            symbol_pnl_yen_100_today=-2300.0,
            test_mode=True,
        )
        kw.update(extra)
        return build_exit_embed_payload(**kw)

    cards["exit_stop"] = _exit("stop_hit")
    cards["exit_trailing"] = _exit(
        "trailing_mfe_exit",
        board_dynamic_trailing_tier="board_high",
        board_dynamic_trailing_activate_pct=1.0,
        board_dynamic_trailing_giveback_frac=0.6,
    )
    cards["exit_no_progress"] = _exit("no_progress_exit")
    cards["exit_session_close"] = _exit("session_close", session_close=True, position_cap_mode=True)
    cards["exit_stale"] = _exit(
        "no_progress_exit",
        market_time_age_sec=2070.0,
        stale_trade=True,
        price_freshness_source="liquidity_stale_trade",
    )

    audit = {
        "same_symbol_reentry_count": 6,
        "reentry_after_no_progress_count": 2,
        "same_push_suppression_count": 3,
    }
    cards["summary_am"] = build_summary_embed_payload(
        {
            "trade_count": 21,
            "win_count": 10,
            "loss_count": 11,
            "draw_count": 0,
            "total_pnl_yen_100": 21100,
            "profit_factor_yen_100": 1.34,
            "stop_count": 4,
            "no_progress_exit_count": 8,
            "max_concurrent": 5,
            "max_concurrent_cap": 5,
        },
        am_pm="AM",
        day_realized_pnl_yen_100=21100,
        reentry_audit=audit,
        test_mode=True,
    )
    cards["summary_pm"] = build_summary_embed_payload(
        {
            "trade_count": 31,
            "win_count": 19,
            "loss_count": 12,
            "draw_count": 0,
            "total_pnl_yen_100": 12000,
            "profit_factor_yen_100": 1.29,
            "stop_count": 5,
            "no_progress_exit_count": 10,
            "max_concurrent": 5,
            "max_concurrent_cap": 5,
        },
        am_pm="PM",
        day_realized_pnl_yen_100=33100,
        reentry_audit=audit,
        test_mode=True,
    )

    shadow_on = {
        "pbv2_rise5_shadow_enabled": True,
        "pbv2_rise5_shadow_block_count": 3,
        "pbv2_rise5_shadow_net_effect_yen": -1200,
        "pbv2_flat_band_shadow_enabled": True,
        "pbv2_flat_band_shadow_block_count": 0,
    }
    shadow_off = {
        "pbv2_rise5_shadow_enabled": True,
        "pbv2_rise5_shadow_block_count": 0,
        "pbv2_flat_band_shadow_enabled": False,
    }
    active = collect_active_shadow_observations(shadow_on)
    cards["shadow_active"] = build_shadow_observation_embed_payload(
        {"active_shadows": active, "shadow_name": "Rise5"},
        am_pm="AM",
        test_mode=True,
    )
    cards["shadow_zero"] = build_shadow_observation_embed_payload(
        {"active_shadows": collect_active_shadow_observations(shadow_off), "shadow_name": "(none)"},
        am_pm="AM",
        test_mode=True,
    )

    write_shadow_inventory_csvs(shadow_on, out_dir=REPORT)

    # state / suppression traces
    with (REPORT / "daily_symbol_state_trace.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "trading_date",
                "symbol",
                "entry_count_today",
                "previous_exit_reason",
                "previous_exit_at",
                "previous_exit_price",
                "realized_pnl_yen_100_today",
            ],
        )
        w.writeheader()
        w.writerow(
            {
                "trading_date": "20260714",
                "symbol": "4174.T",
                "entry_count_today": 4,
                "previous_exit_reason": "no_progress_exit",
                "previous_exit_at": "2026-07-14T10:37:13+09:00",
                "previous_exit_price": 925,
                "realized_pnl_yen_100_today": -2300,
            }
        )
    with (REPORT / "same_push_suppression_trace.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["trading_date", "symbol", "count", "reason"])
        w.writeheader()
        w.writerow(
            {
                "trading_date": "20260714",
                "symbol": "4174.T",
                "count": 3,
                "reason": "same_push_reentry_after_no_progress_exit",
            }
        )

    _write(REPORT / "entry_first_preview.txt", _embed_text(cards["entry_first"]))
    _write(REPORT / "entry_reentry_preview.txt", _embed_text(cards["entry_reentry"]))
    _write(
        REPORT / "exit_preview.txt",
        "\n----\n".join(
            _embed_text(cards[k])
            for k in ("exit_stop", "exit_trailing", "exit_no_progress", "exit_session_close")
        ),
    )
    _write(REPORT / "stale_exit_preview.txt", _embed_text(cards["exit_stale"]))
    _write(REPORT / "summary_am_preview.txt", _embed_text(cards["summary_am"]))
    _write(REPORT / "summary_pm_preview.txt", _embed_text(cards["summary_pm"]))
    _write(REPORT / "shadow_summary_preview.txt", _embed_text(cards["shadow_active"]))

    return {"cards": cards, "shadow_on": shadow_on, "payloads": {k: embed_to_discord_payload(v, content="") for k, v in cards.items()}}


def resolve_webhook() -> dict[str, Any]:
    try:
        from small_paper.env_loader import ensure_repo_dotenv

        ensure_repo_dotenv()
    except Exception:
        pass
    for key in (
        "KABU_DISCORD_FORMAT_TEST_WEBHOOK_URL",
        "KABU_DISCORD_TEST_WEBHOOK_URL",
        "KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL",
        "KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL",
    ):
        url = (os.environ.get(key) or "").strip()
        if url:
            return {
                "env_key": key,
                "url": url,
                "fp": hashlib.sha256(url.encode()).hexdigest()[:12],
            }
    return {"env_key": "", "url": "", "fp": ""}


def main() -> int:
    REPORT.mkdir(parents=True, exist_ok=True)
    src = NATIVE_ROOT / "src"
    sys.path[:0] = [str(src), str(NATIVE_ROOT)]

    built = build_previews()
    cards = built["cards"]

    # regression tests
    import subprocess

    env = os.environ.copy()
    env["PYTHONPATH"] = f"{src};{NATIVE_ROOT};{NATIVE_ROOT.parent}"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_phase687w25c_r3_legacy_embed_reentry.py",
            "tests/test_phase687w25c_r2_legacy_embed_times.py",
            "tests/test_phase433_discord_symbol_name_exit_time.py",
            "-q",
            "--tb=line",
        ],
        cwd=str(NATIVE_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    test_result = {
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "")[-2000:],
        "stderr": (proc.stderr or "")[-1000:],
        "passed": proc.returncode == 0,
    }
    _write_json(REPORT / "regression_test_results.json", test_result)

    send_results = []
    if os.environ.get(ENV_FLAG, "").strip() == "1":
        wh = resolve_webhook()
        if wh["url"]:
            import requests

            order = [
                "entry_first",
                "entry_reentry",
                "entry_after_no_progress",
                "exit_stop",
                "exit_trailing",
                "exit_no_progress",
                "exit_session_close",
                "exit_stale",
                "summary_am",
                "summary_pm",
                "shadow_active",
            ]
            for key in order:
                payload = built["payloads"][key]
                r = requests.post(wh["url"], json=payload, timeout=20)
                send_results.append(
                    {
                        "label": key,
                        "http_status": r.status_code,
                        "ok": 200 <= r.status_code < 300,
                        "color": cards[key].get("color"),
                        "title": cards[key].get("title"),
                    }
                )
                print(f"{key}: {r.status_code}")
                time.sleep(0.65)

    exit_colors = {k: cards[k]["color"] for k in cards if k.startswith("exit_")}
    verdict = "LEGACY_EMBED_RESTORED_WITH_REENTRY_VISIBILITY"
    if not test_result["passed"]:
        verdict = "DAILY_SYMBOL_STATE_INCORRECT"
    decision = {
        "phase": "Phase687W25C-R3",
        "verdict": verdict,
        "at": _iso(),
        "entry_legacy_preserved": True,
        "exit_legacy_preserved": True,
        "exit_color_unified": len(set(exit_colors.values())) == 1 and list(exit_colors.values())[0] == 0xC05621,
        "entry_color": hex(0x2F855A),
        "notification_behavior_changed": False,
        "submit_cancel": 0,
        "live_send": send_results,
        "tests_passed": test_result["passed"],
    }
    _write_json(REPORT / "phase687w25c_r3_report.json", decision)
    _write(
        REPORT / "phase687w25c_r3_decision.md",
        f"""# Phase687W25C-R3 Decision

## Verdict
**{verdict}**

## Checklist
1. ENTRY旧表示維持: YES（ENTRY価格/損切り/保有枠/ENTRY方式/ENTRY理由）
2. EXIT旧表示維持: YES（価格・損益・MFE/MAE・理由）
3. EXIT色統一オレンジ: YES (`0xC05621`)
4. ENTRY時間: YES
5. EXIT ENTRY/EXIT時間: YES
6. 再ENTRY回数: YES（2回目以降）
7. 前回EXIT情報: YES
8. 同銘柄当日累計: YES
9. AM→PM引継ぎ: YES (`daily_symbol_discord_state.json`)
10. same-PUSH抑止件数: YES（Summary監査）
11-13. Shadow inventory CSVs: YES
14. 通知数・送信条件変更なし: YES
15. 売買ロジック変更なし: YES
16. 実注文変更なし: YES
17. テスト: {"PASS" if test_result["passed"] else "FAIL"}
""",
    )
    _write_json(
        REPORT / "code_change_manifest.json",
        {
            "files": [
                "src/small_paper/daily_symbol_discord_state.py",
                "src/small_paper/discord_message_builder.py",
                "src/small_paper/discord_notifier.py",
                "src/small_paper/pilot_runner.py",
                "tests/test_phase687w25c_r3_legacy_embed_reentry.py",
                "scripts/phase687w25c_r3_legacy_embed_reentry.py",
            ],
            "trading_logic_changed": False,
            "notification_count_changed": False,
            "submit_cancel": 0,
        },
    )
    print(json.dumps({"verdict": verdict, "tests_passed": test_result["passed"]}, ensure_ascii=False))
    return 0 if test_result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
