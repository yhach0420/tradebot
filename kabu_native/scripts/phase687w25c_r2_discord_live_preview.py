"""Phase687W25C-R2 — legacy embed live preview (TRADEBOT_DISCORD_FORMAT_TEST=1)."""

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
REPORT = NATIVE_ROOT / "results" / "reports" / "phase687w25c_r2_legacy_embed_times"

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


def _fp(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:12] if url else ""


def resolve_target() -> dict[str, Any]:
    try:
        from small_paper.env_loader import ensure_repo_dotenv

        ensure_repo_dotenv()
    except Exception:
        pass
    for key in ENV_TEST_WEBHOOKS + (ENV_TRADE_NOTIFY, ENV_LEGACY):
        url = (os.environ.get(key) or "").strip()
        if url:
            return {
                "env_key": key,
                "configured": True,
                "url_fingerprint": _fp(url),
                "falls_back": key in (ENV_TRADE_NOTIFY, ENV_LEGACY),
            }
    return {"env_key": "", "configured": False, "url_fingerprint": "", "falls_back": False}


def build_cards() -> list[dict[str, Any]]:
    from small_paper.discord_message_builder import (
        audit_discord_shadow_inventory,
        build_entry_embed_payload,
        build_exit_embed_payload,
        build_shadow_observation_embed_payload,
        collect_active_shadow_observations,
        embed_to_discord_payload,
    )

    names = {"4174.T": "アピリッツ"}
    entry_t = "2026-07-14T10:06:53+09:00"
    exit_t = "2026-07-14T10:21:59+09:00"
    cards: list[tuple[str, dict[str, Any]]] = []

    cards.append(
        (
            "legacy_ENTRY",
            build_entry_embed_payload(
                symbol="4174.T",
                entry_price=925.0,
                slot_usage="3/5",
                entry_score_v2=3,
                data={
                    "price_age_sec": 0.8,
                    "board_age_sec": 0.2,
                    "price_source": "event_fresh",
                    "scan_id": "preview-scan",
                    "gate_model": "gate_v2",
                    "entry_reason_tokens": ["Momentum:low", "Board mid以上"],
                    "entry_high_break_recent": True,
                },
                name_map=names,
                entry_time=entry_t,
                test_mode=True,
            ),
        )
    )
    for label, reason, extra in (
        ("legacy_STOP", "stop_hit", {}),
        (
            "legacy_trailing",
            "trailing_mfe_exit",
            {
                "board_dynamic_trailing_tier": "board_high",
                "board_dynamic_trailing_activate_pct": 1.0,
                "board_dynamic_trailing_giveback_frac": 0.60,
                "mfe_pct": 0.4,
            },
        ),
        ("legacy_no_progress", "no_progress_exit", {}),
        (
            "legacy_stale_EXIT",
            "no_progress_exit",
            {
                "market_time_age_sec": 2070.0,
                "stale_trade": True,
                "price_freshness_source": "liquidity_stale_trade",
            },
        ),
    ):
        kw = {
            "symbol": "4174.T",
            "entry_price": 925.0,
            "exit_price": 920.0 if reason == "stop_hit" else 925.0,
            "pnl_pct": -0.54 if reason == "stop_hit" else 0.0,
            "mfe_pct": float(extra.get("mfe_pct", 0.0)),
            "mae_pct": -0.5 if reason == "stop_hit" else 0.0,
            "hold_minutes": 15.1167,
            "exit_reason": reason,
            "pnl_yen_100": -500.0 if reason == "stop_hit" else 0.0,
            "name_map": names,
            "entry_time": entry_t,
            "exit_time": exit_t,
            "test_mode": True,
        }
        for k in (
            "board_dynamic_trailing_tier",
            "board_dynamic_trailing_activate_pct",
            "board_dynamic_trailing_giveback_frac",
            "market_time_age_sec",
            "stale_trade",
            "price_freshness_source",
        ):
            if k in extra:
                kw[k] = extra[k]
        cards.append((label, build_exit_embed_payload(**kw)))

    shadow_summary = {
        "pbv2_rise5_shadow_enabled": True,
        "pbv2_rise5_shadow_block_count": 3,
        "pbv2_rise5_shadow_net_effect_yen": -1200,
        "pbv2_flat_band_shadow_enabled": True,
        "pbv2_flat_band_shadow_block_count": 0,
    }
    active = collect_active_shadow_observations(shadow_summary)
    inventory = audit_discord_shadow_inventory(shadow_summary)
    cards.append(
        (
            "latest_SHADOW",
            build_shadow_observation_embed_payload(
                {
                    "shadow_name": ", ".join(a["name"] for a in active) or "none",
                    "active_shadows": active,
                    "blocks": sum(int(a["count"]) for a in active),
                    "delta_yen": " / ".join(f"{a['name']}={a['delta']}" for a in active),
                },
                am_pm="AM",
                test_mode=True,
            ),
        )
    )
    out = []
    for label, emb in cards:
        out.append(
            {
                "label": label,
                "embed": emb,
                "payload": embed_to_discord_payload(emb, content=""),
                "inventory": inventory if label == "latest_SHADOW" else None,
            }
        )
    return out


def _post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    import requests

    r = requests.post(url, json=payload, timeout=20)
    return {"http_status": r.status_code, "ok": 200 <= r.status_code < 300}


def main() -> int:
    REPORT.mkdir(parents=True, exist_ok=True)
    if os.environ.get(ENV_FLAG, "").strip() != "1":
        print(f"SKIP: set {ENV_FLAG}=1")
        return 2
    target = resolve_target()
    if not target["configured"]:
        print("ERROR: no webhook")
        return 1
    url = (os.environ.get(target["env_key"]) or "").strip()
    cards = build_cards()
    _write_json(REPORT / "preview_payloads.json", [{"label": c["label"], "embed": c["embed"]} for c in cards])
    results = []
    for c in cards:
        res = _post(url, c["payload"])
        results.append({"label": c["label"], **res, "title": c["embed"].get("title"), "color": c["embed"].get("color")})
        print(f"{c['label']}: {res['http_status']} color={hex(int(c['embed'].get('color') or 0))}")
        time.sleep(0.7)
    ok_n = sum(1 for r in results if r["ok"])
    colors = {r["label"]: r["color"] for r in results if "EXIT" in r["label"] or r["label"].startswith("legacy_")}
    exit_colors = [r["color"] for r in results if r["label"].startswith("legacy_") and r["label"] != "legacy_ENTRY"]
    decision = {
        "phase": "Phase687W25C-R2",
        "verdict": "LEGACY_EMBED_RESTORED_WITH_TIMES" if ok_n == len(results) else "EMBED_SEND_PARTIAL",
        "sent_ok": ok_n,
        "sent_total": len(results),
        "exit_all_same_orange": len(set(exit_colors)) == 1 and exit_colors[0] == 0xC05621,
        "entry_green": any(r["label"] == "legacy_ENTRY" and r["color"] == 0x2F855A for r in results),
        "submit_cancel": 0,
        "at": _iso(),
        "results": results,
        "target": target,
        "shadow_inventory": cards[-1].get("inventory"),
    }
    if decision.get("shadow_inventory", {}).get("verdict") == "SHADOW_INVENTORY_OUTDATED":
        decision["verdict"] = "SHADOW_INVENTORY_OUTDATED"
    _write_json(REPORT / "live_send_result.json", decision)
    print(json.dumps({"verdict": decision["verdict"], "sent_ok": ok_n}, ensure_ascii=False))
    return 0 if ok_n == len(results) else 1


if __name__ == "__main__":
    src = NATIVE_ROOT / "src"
    sys.path[:0] = [str(src), str(NATIVE_ROOT)]
    raise SystemExit(main())
