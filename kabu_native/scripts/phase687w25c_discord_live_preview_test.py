"""Phase687W25C — Discord embed live preview send (explicit flag only).

Requires TRADEBOT_DISCORD_FORMAT_TEST=1.
Sends embed cards only — no long TEST banner content.
Does not start Paper; submit/cancel untouched.
"""

from __future__ import annotations

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
REPORT = NATIVE_ROOT / "results" / "reports" / "phase687w25c_discord_readability_restore"

ENV_FLAG = "TRADEBOT_DISCORD_FORMAT_TEST"
ENV_TEST_WEBHOOKS = (
    "KABU_DISCORD_FORMAT_TEST_WEBHOOK_URL",
    "KABU_DISCORD_TEST_WEBHOOK_URL",
)
ENV_TRADE_NOTIFY = "KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL"
ENV_LEGACY = "KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL"


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
            }

    return {
        "target_kind": "none",
        "env_key": "",
        "configured": False,
        "url_fingerprint": "",
        "url_length": 0,
        "falls_back_to_trade_notify": False,
    }


def build_preview_embeds() -> list[dict[str, Any]]:
    from small_paper.discord_message_builder import (
        build_cap_blocked_embed_payload,
        build_entry_embed_payload,
        build_exit_embed_payload,
        build_shadow_observation_embed_payload,
        build_summary_embed_payload,
        embed_to_discord_payload,
    )

    names = {"4174.T": "アピリッツ", "9983.T": "ファーストリテイリング"}
    cards: list[tuple[str, dict[str, Any]]] = []

    cards.append(
        (
            "ENTRY",
            build_entry_embed_payload(
                symbol="4174.T",
                entry_price=925.0,
                slot_usage="3 / 5",
                entry_score_v2=3,
                data={
                    "entry_route": "PBv2",
                    "momentum_label": "low",
                    "board_label": "mid",
                    "entry_reason_tokens": ["Momentum:low", "Board mid以上"],
                },
                name_map=names,
                test_mode=True,
            ),
        )
    )
    cards.append(
        (
            "STOP",
            build_exit_embed_payload(
                symbol="4174.T",
                entry_price=925.0,
                exit_price=910.0,
                pnl_pct=-1.62,
                mfe_pct=0.2,
                mae_pct=-1.7,
                hold_minutes=8.0,
                exit_reason="stop_hit",
                pnl_yen_100=-1500.0,
                name_map=names,
                test_mode=True,
            ),
        )
    )
    cards.append(
        (
            "trailing_mfe",
            build_exit_embed_payload(
                symbol="4174.T",
                entry_price=900.0,
                exit_price=920.0,
                pnl_pct=2.22,
                mfe_pct=3.0,
                mae_pct=-0.4,
                hold_minutes=22.0,
                exit_reason="trailing_mfe_exit",
                pnl_yen_100=2000.0,
                board_dynamic_trailing_tier="board_high",
                board_dynamic_trailing_activate_pct=1.0,
                board_dynamic_trailing_giveback_frac=0.60,
                name_map=names,
                test_mode=True,
            ),
        )
    )
    cards.append(
        (
            "no_progress",
            build_exit_embed_payload(
                symbol="4174.T",
                entry_price=925.0,
                exit_price=925.0,
                pnl_pct=0.0,
                mfe_pct=0.0,
                mae_pct=0.0,
                hold_minutes=15.1167,
                exit_reason="no_progress_exit",
                pnl_yen_100=0.0,
                name_map=names,
                test_mode=True,
            ),
        )
    )
    cards.append(
        (
            "stale_exit",
            build_exit_embed_payload(
                symbol="4174.T",
                entry_price=925.0,
                exit_price=925.0,
                pnl_pct=0.0,
                mfe_pct=0.0,
                mae_pct=0.0,
                hold_minutes=15.1167,
                exit_reason="no_progress_exit",
                pnl_yen_100=0.0,
                name_map=names,
                market_time_age_sec=2070.0,
                board_age_sec=1.0,
                stale_trade=True,
                test_mode=True,
            ),
        )
    )
    cards.append(
        (
            "session_close",
            build_exit_embed_payload(
                symbol="4174.T",
                entry_price=925.0,
                exit_price=930.0,
                pnl_pct=0.54,
                mfe_pct=0.8,
                mae_pct=-0.2,
                hold_minutes=45.0,
                exit_reason="session_close",
                pnl_yen_100=500.0,
                name_map=names,
                session_close=True,
                position_cap_mode=True,
                test_mode=True,
            ),
        )
    )
    cards.append(
        (
            "CAP_BLOCKED",
            build_cap_blocked_embed_payload(
                symbol="4174.T",
                entry_score_v2=4,
                data={"entry_route": "PBv2"},
                active_positions=5,
                position_cap=5,
                name_map=names,
                test_mode=True,
            ),
        )
    )
    cards.append(
        (
            "AM_SUMMARY",
            build_summary_embed_payload(
                {
                    "trade_count": 52,
                    "win_count": 22,
                    "loss_count": 22,
                    "draw_count": 8,
                    "total_pnl_yen_100": 21100,
                    "profit_factor_yen_100": 1.34,
                    "stop_count": 8,
                    "no_progress_exit_count": 22,
                    "max_concurrent": 5,
                    "max_concurrent_cap": 5,
                    "best_trade": {"symbol": "9983.T", "pnl_yen_100": 18000},
                    "worst_trade": {"symbol": "9983.T", "pnl_yen_100": -12000},
                },
                am_pm="AM",
                test_mode=True,
            ),
        )
    )
    cards.append(
        (
            "SHADOW",
            build_shadow_observation_embed_payload(
                {"shadow_name": "rise5", "blocks": 3, "delta_yen": -1200},
                am_pm="AM",
                test_mode=True,
            ),
        )
    )

    out: list[dict[str, Any]] = []
    for label, emb in cards:
        payload = embed_to_discord_payload(emb, content="")
        out.append({"label": label, "payload": payload, "embed": emb})
    return out


def _post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    import requests

    # Ensure no banned banner text leaked into content
    content = str(payload.get("content") or "")
    banned = (
        "[TEST NOTIFICATION]",
        "[DISCORD FORMAT TEST]",
        "本通知は表示確認用です",
        "実際のENTRY/EXITではありません",
        "Real orders: DISABLED",
    )
    for b in banned:
        if b in content:
            raise RuntimeError(f"banned test banner leaked into content: {b}")

    r = requests.post(url, json=payload, timeout=20)
    return {"http_status": r.status_code, "ok": 200 <= r.status_code < 300, "body": (r.text or "")[:200]}


def main() -> int:
    REPORT.mkdir(parents=True, exist_ok=True)
    if os.environ.get(ENV_FLAG, "").strip() != "1":
        _write_json(
            REPORT / "send_skipped.json",
            {"reason": f"set {ENV_FLAG}=1 to send", "at": _iso()},
        )
        print(f"SKIP: set {ENV_FLAG}=1 to send live embed previews")
        return 2

    target = resolve_send_target()
    _write_json(REPORT / "send_target.json", {k: v for k, v in target.items() if k != "url"})
    if not target.get("configured"):
        print("ERROR: no webhook configured")
        return 1

    url = (os.environ.get(str(target["env_key"])) or "").strip()
    cards = build_preview_embeds()
    _write_json(
        REPORT / "preview_payloads.json",
        [{"label": c["label"], "embed": c["embed"]} for c in cards],
    )

    results: list[dict[str, Any]] = []
    for c in cards:
        res = _post(url, c["payload"])
        results.append({"label": c["label"], **res, "title": c["embed"].get("title")})
        print(f"{c['label']}: status={res['http_status']} ok={res['ok']}")
        time.sleep(0.7)

    sent_ok = sum(1 for r in results if r.get("ok"))
    decision = {
        "phase": "Phase687W25C",
        "verdict": "DISCORD_READABILITY_RESTORED" if sent_ok == len(results) else "EMBED_SEND_PARTIAL",
        "sent_ok": sent_ok,
        "sent_total": len(results),
        "submit_cancel": 0,
        "notification_behavior_changed": False,
        "test_label_mode": "title_and_footer_only",
        "at": _iso(),
        "results": results,
        "target": {k: v for k, v in target.items()},
    }
    _write_json(REPORT / "live_send_result.json", decision)
    print(json.dumps({"verdict": decision["verdict"], "sent_ok": sent_ok, "sent_total": len(results)}, ensure_ascii=False))
    return 0 if sent_ok == len(results) else 1


if __name__ == "__main__":
    # Ensure src import path
    src = NATIVE_ROOT / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    if str(NATIVE_ROOT) not in sys.path:
        sys.path.insert(0, str(NATIVE_ROOT))
    raise SystemExit(main())
