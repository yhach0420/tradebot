"""V1R Paper Primary Discord routing — final channel map for 2026-08-10.

trade-notify  : FILL / EXIT only
trade-entry   : ENTRY / EXPIRED only (KABU_V1R_ENTRY_WEBHOOK_URL, no fallback)
trade-research: PRIMARY SUMMARY / PBV2 SHADOW / 1M SHADOW
cap-blocked   : CAP BLOCKED (unchanged)
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from notify.discord_demo_sender import _send_one
from notify.discord_notification_model import (
    ActualOrShadow,
    NotificationCategory,
    Severity,
    WEBHOOK_ENV_CAP,
    WEBHOOK_ENV_RESEARCH,
    WEBHOOK_ENV_TRADE,
    build_envelope,
    trading_date_jst,
)
from notify.discord_notification_router import get_router, resolve_webhook_url
from notify.v1r_discord_embeds import (
    COLOR_ENTRY,
    COLOR_EXIT,
    COLOR_EXPIRED,
    COLOR_FILL,
    COLOR_SHADOW,
    COLOR_SUMMARY,
    assert_entry_fields,
    assert_exit_fields,
    assert_expired_fields,
    assert_fill_fields,
    build_1m_shadow_embed,
    build_cap_blocked_embed,
    build_entry_embed,
    build_expired_embed,
    build_exit_embed,
    build_fill_embed,
    build_pbv2_shadow_embed,
    build_primary_summary_embed,
    human_exit_reason,
)

# New ENV — no fallback to other webhooks
WEBHOOK_ENV_V1R_ENTRY = "KABU_V1R_ENTRY_WEBHOOK_URL"

PREFIX_ENTRY = "[V1R PAPER ENTRY]"
PREFIX_EXPIRED = "[V1R PAPER EXPIRED]"
PREFIX_FILL = "[V1R PAPER FILL]"
PREFIX_EXIT = "[V1R PAPER EXIT]"
PREFIX_SUMMARY = "[V1R PAPER PRIMARY SUMMARY]"
PREFIX_PBV2 = "[PBV2 SHADOW]"
PREFIX_1M = "[V1R 1M SHADOW]"
PREFIX_CAP = "[V1R PAPER CAP BLOCKED]"

EVENT_COLOR: dict[str, int] = {
    "ENTRY": COLOR_ENTRY,
    "EXPIRED": COLOR_EXPIRED,
    "FILL": COLOR_FILL,
    "EXIT": COLOR_EXIT,
}

EVENT_TITLE_EMOJI: dict[str, str] = {
    "ENTRY": "🟢 ENTRY",
    "FILL": "🔵 FILL",
    "EXIT": "🔴 EXIT",
    "EXPIRED": "🟠 EXPIRED",
}


class V1RNotifyKind(str, Enum):
    ENTRY = "ENTRY"
    EXPIRED = "EXPIRED"
    FILL = "FILL"
    EXIT = "EXIT"
    PRIMARY_SUMMARY = "PRIMARY_SUMMARY"
    PBV2_SHADOW = "PBV2_SHADOW"
    ONE_M_SHADOW = "ONE_M_SHADOW"
    CAP_BLOCKED = "CAP_BLOCKED"


# Final routing table (env key names only — never URLs)
ROUTING_TABLE: dict[V1RNotifyKind, dict[str, Any]] = {
    V1RNotifyKind.ENTRY: {
        "prefix": PREFIX_ENTRY,
        "env_keys": (WEBHOOK_ENV_V1R_ENTRY,),
        "fallback_forbidden": True,
        "channel": "trade-entry",
        "category": NotificationCategory.RESEARCH_SHADOW,  # envelope label only; URL from env_keys
    },
    V1RNotifyKind.EXPIRED: {
        "prefix": PREFIX_EXPIRED,
        "env_keys": (WEBHOOK_ENV_V1R_ENTRY,),
        "fallback_forbidden": True,
        "channel": "trade-entry",
        "category": NotificationCategory.RESEARCH_SHADOW,
    },
    V1RNotifyKind.FILL: {
        "prefix": PREFIX_FILL,
        "env_keys": (WEBHOOK_ENV_TRADE,),
        "fallback_forbidden": True,
        "channel": "trade-notify",
        "category": NotificationCategory.TRADE_ACTUAL,
    },
    V1RNotifyKind.EXIT: {
        "prefix": PREFIX_EXIT,
        "env_keys": (WEBHOOK_ENV_TRADE,),
        "fallback_forbidden": True,
        "channel": "trade-notify",
        "category": NotificationCategory.TRADE_ACTUAL,
    },
    V1RNotifyKind.PRIMARY_SUMMARY: {
        "prefix": PREFIX_SUMMARY,
        "env_keys": (WEBHOOK_ENV_RESEARCH,),
        "fallback_forbidden": True,
        "channel": "trade-research",
        "category": NotificationCategory.RESEARCH_SHADOW,
    },
    V1RNotifyKind.PBV2_SHADOW: {
        "prefix": PREFIX_PBV2,
        "env_keys": (WEBHOOK_ENV_RESEARCH,),
        "fallback_forbidden": True,
        "channel": "trade-research",
        "category": NotificationCategory.RESEARCH_SHADOW,
    },
    V1RNotifyKind.ONE_M_SHADOW: {
        "prefix": PREFIX_1M,
        "env_keys": (WEBHOOK_ENV_RESEARCH,),
        "fallback_forbidden": True,
        "channel": "trade-research",
        "category": NotificationCategory.RESEARCH_SHADOW,
    },
    V1RNotifyKind.CAP_BLOCKED: {
        "prefix": PREFIX_CAP,
        "env_keys": (WEBHOOK_ENV_CAP,),
        "fallback_forbidden": True,
        "channel": "cap-blocked",
        "category": NotificationCategory.CAP_BLOCKED,
    },
}


def public_routing_table() -> list[dict[str, Any]]:
    """Secret-free routing audit rows."""
    rows = []
    for kind, meta in ROUTING_TABLE.items():
        rows.append({
            "kind": kind.value,
            "prefix": meta["prefix"],
            "channel": meta["channel"],
            "env_key": meta["env_keys"][0],
            "fallback": None if meta["fallback_forbidden"] else "see keys",
            "fallback_forbidden": meta["fallback_forbidden"],
        })
    return rows


def v1r_entry_webhook_missing() -> bool:
    url, _ = resolve_webhook_url((WEBHOOK_ENV_V1R_ENTRY,))
    return not bool(url)


def heartbeat_flags() -> dict[str, Any]:
    return {
        "v1r_entry_webhook_missing": v1r_entry_webhook_missing(),
        "v1r_entry_env_key": WEBHOOK_ENV_V1R_ENTRY,
    }


# ---------------------------------------------------------------------------
# Embed UI — field-rich, event-color locked (PnL never changes color)
# ---------------------------------------------------------------------------

_EMBED_BUILDERS = {
    V1RNotifyKind.ENTRY: build_entry_embed,
    V1RNotifyKind.EXPIRED: build_expired_embed,
    V1RNotifyKind.FILL: build_fill_embed,
    V1RNotifyKind.EXIT: build_exit_embed,
    V1RNotifyKind.PRIMARY_SUMMARY: build_primary_summary_embed,
    V1RNotifyKind.PBV2_SHADOW: build_pbv2_shadow_embed,
    V1RNotifyKind.ONE_M_SHADOW: build_1m_shadow_embed,
    V1RNotifyKind.CAP_BLOCKED: build_cap_blocked_embed,
}


def event_color(kind: V1RNotifyKind | str, *, pnl_yen: Any = None) -> int:
    """Event-type color lock. pnl_yen is accepted but ignored (negative test hook)."""
    _ = pnl_yen  # explicitly unused — PnL must not affect color
    k = V1RNotifyKind(kind) if not isinstance(kind, V1RNotifyKind) else kind
    if k == V1RNotifyKind.ENTRY:
        return COLOR_ENTRY
    if k == V1RNotifyKind.FILL:
        return COLOR_FILL
    if k == V1RNotifyKind.EXIT:
        return COLOR_EXIT
    if k == V1RNotifyKind.EXPIRED:
        return COLOR_EXPIRED
    if k == V1RNotifyKind.PRIMARY_SUMMARY:
        return COLOR_SUMMARY
    return COLOR_SHADOW


def display_title(kind: V1RNotifyKind, symbol: str = "") -> str:
    base = EVENT_TITLE_EMOJI.get(kind.value, kind.value)
    sym = str(symbol or "").strip()
    return f"{base} | {sym}" if sym else base


def _title_desc_from_embed(embed: dict[str, Any]) -> tuple[str, str]:
    """Compatibility view: title + flattened description/fields text."""
    parts = [str(embed.get("description") or "")]
    for f in embed.get("fields") or []:
        parts.append(str(f.get("name") or ""))
        parts.append(str(f.get("value") or ""))
    return str(embed.get("title") or ""), "\n".join(p for p in parts if p)


def format_entry(p: dict[str, Any], *, test_only: bool = False) -> tuple[str, str]:
    return _title_desc_from_embed(build_entry_embed(p, test_only=test_only))


def format_expired(p: dict[str, Any], *, test_only: bool = False) -> tuple[str, str]:
    return _title_desc_from_embed(build_expired_embed(p, test_only=test_only))


def format_fill(p: dict[str, Any], *, test_only: bool = False) -> tuple[str, str]:
    return _title_desc_from_embed(build_fill_embed(p, test_only=test_only))


def format_exit(p: dict[str, Any], *, test_only: bool = False) -> tuple[str, str]:
    return _title_desc_from_embed(build_exit_embed(p, test_only=test_only))


def format_primary_summary(p: dict[str, Any], *, test_only: bool = False) -> tuple[str, str]:
    return _title_desc_from_embed(build_primary_summary_embed(p, test_only=test_only))


def format_pbv2_shadow(p: dict[str, Any], *, test_only: bool = False) -> tuple[str, str]:
    return _title_desc_from_embed(build_pbv2_shadow_embed(p, test_only=test_only))


def format_1m_shadow(p: dict[str, Any], *, test_only: bool = False) -> tuple[str, str]:
    return _title_desc_from_embed(build_1m_shadow_embed(p, test_only=test_only))


def format_cap_blocked(p: dict[str, Any], *, test_only: bool = False) -> tuple[str, str]:
    return _title_desc_from_embed(build_cap_blocked_embed(p, test_only=test_only))


def build_event_embed(
    kind: V1RNotifyKind,
    payload: dict[str, Any],
    *,
    test_only: bool = False,
) -> tuple[str, list[dict[str, Any]], int]:
    """Return (routing_prefix_title, embeds, color). One embed = one Discord message."""
    embed = _EMBED_BUILDERS[kind](payload, test_only=test_only)
    # Force event color lock even if builder color drifts
    color = event_color(kind, pnl_yen=payload.get("pnl_yen"))
    embed["color"] = int(color)
    return meta_prefix(kind), [embed], color


def meta_prefix(kind: V1RNotifyKind) -> str:
    return ROUTING_TABLE[kind]["prefix"]


def field_completeness(kind: V1RNotifyKind, embed: dict[str, Any]) -> dict[str, bool]:
    if kind == V1RNotifyKind.ENTRY:
        return assert_entry_fields(embed)
    if kind == V1RNotifyKind.FILL:
        return assert_fill_fields(embed)
    if kind == V1RNotifyKind.EXIT:
        return assert_exit_fields(embed)
    if kind == V1RNotifyKind.EXPIRED:
        return assert_expired_fields(embed)
    return {"ok": True}


@dataclass
class V1RNotifyResult:
    kind: str
    status: str
    channel: str
    env_key: str
    queued: bool
    http_status: Optional[int] = None
    latency_ms: Optional[float] = None
    notification_id: str = ""
    v1r_entry_webhook_missing: bool = False
    enqueue_latency_ms: float = 0.0
    blocking: bool = False
    error: str = ""
    color: Optional[int] = None
    color_name: str = ""
    display_title: str = ""
    embed_count: int = 0


def resolve_destination(kind: V1RNotifyKind) -> tuple[str, str, bool]:
    """Return (url, env_key, missing). No cross-channel fallback."""
    meta = ROUTING_TABLE[kind]
    keys = meta["env_keys"]
    url, key = resolve_webhook_url(keys)
    missing = not bool(url)
    if kind in (V1RNotifyKind.ENTRY, V1RNotifyKind.EXPIRED) and missing:
        return "", WEBHOOK_ENV_V1R_ENTRY, True
    return url, (key or keys[0]), missing


def _color_name(color: int) -> str:
    return {
        COLOR_ENTRY: "green",
        COLOR_FILL: "blue",
        COLOR_EXIT: "red",
        COLOR_EXPIRED: "orange",
        COLOR_SUMMARY: "neutral",
        COLOR_SHADOW: "shadow",
    }.get(int(color), f"#{int(color):06X}")


def publish_v1r(
    kind: V1RNotifyKind | str,
    payload: dict[str, Any],
    *,
    test_only: bool = False,
    sync_http: bool = False,
    session_id: str = "",
) -> V1RNotifyResult:
    """
    Format + route V1R notification via existing infra (Embed + event color lock).

    One call → one Discord message (1 embed). Never merges FILL+EXIT etc.
    """
    k = V1RNotifyKind(kind) if not isinstance(kind, V1RNotifyKind) else kind
    meta = ROUTING_TABLE[k]
    t0 = time.perf_counter()
    url, env_key, missing = resolve_destination(k)
    enqueue_ms = (time.perf_counter() - t0) * 1000.0

    routing_title, embeds, color = build_event_embed(k, payload, test_only=test_only)
    disp = str((embeds[0] or {}).get("title") or routing_title)

    if missing:
        return V1RNotifyResult(
            kind=k.value,
            status="SKIPPED_WEBHOOK_NOT_CONFIGURED",
            channel=meta["channel"],
            env_key=env_key,
            queued=False,
            v1r_entry_webhook_missing=(k in (V1RNotifyKind.ENTRY, V1RNotifyKind.EXPIRED)),
            enqueue_latency_ms=enqueue_ms,
            blocking=False,
            error="webhook_missing_fail_soft",
            color=color,
            color_name=_color_name(color),
            display_title=disp,
            embed_count=len(embeds),
        )

    dedupe = f"v1r|{k.value}|{session_id or uuid.uuid4().hex}|{uuid.uuid4().hex[:8]}"
    envelope = build_envelope(
        category=meta["category"],
        severity=Severity.INFO,
        event_type=f"V1R_{k.value}" + ("_TEST" if test_only else ""),
        title=routing_title,  # stable prefix for routing identity
        content="",  # Embed-only UI (no plain-text dump)
        embeds=embeds,
        trading_date=str(payload.get("date") or trading_date_jst()),
        session_id=session_id or f"v1r-{trading_date_jst()}",
        symbol=str(payload.get("symbol") or ""),
        source_module="notify.v1r_discord_routing",
        dedupe_key=dedupe,
        actual_or_shadow=(
            ActualOrShadow.ACTUAL
            if k in (V1RNotifyKind.FILL, V1RNotifyKind.EXIT)
            else ActualOrShadow.SHADOW
        ),
        ownership="V1R_PAPER_PRIMARY",
        extra={
            "v1r_kind": k.value,
            "channel": meta["channel"],
            "test_only": test_only,
            "embed_color": color,
            "embed_color_name": _color_name(color),
            "one_event_one_message": True,
            "embed_count": len(embeds),
        },
    )
    envelope.webhook_env_key = env_key

    if sync_http:
        r = _send_one(envelope, url=url, destination_key=env_key)
        return V1RNotifyResult(
            kind=k.value,
            status=r.status,
            channel=meta["channel"],
            env_key=env_key,
            queued=False,
            http_status=r.http_status,
            latency_ms=r.latency_ms,
            notification_id=r.notification_id,
            enqueue_latency_ms=enqueue_ms,
            blocking=False,
            error=r.error or "",
            color=color,
            color_name=_color_name(color),
            display_title=disp,
            embed_count=len(embeds),
        )

    router = get_router()
    q = router.worker.enqueue(envelope, url)
    return V1RNotifyResult(
        kind=k.value,
        status=str(q.get("status") or "UNKNOWN"),
        channel=meta["channel"],
        env_key=env_key,
        queued=bool(q.get("queued")),
        notification_id=envelope.notification_id,
        enqueue_latency_ms=enqueue_ms,
        blocking=False,
        color=color,
        color_name=_color_name(color),
        display_title=disp,
        embed_count=len(embeds),
    )


def assert_color_lock() -> dict[str, Any]:
    """PnL must not change EXIT color; event colors fixed; internals hidden."""
    def _blob(kind: V1RNotifyKind, payload: dict[str, Any]) -> str:
        _, embeds, _ = build_event_embed(kind, payload, test_only=True)
        e = embeds[0]
        parts = [str(e.get("title") or ""), str(e.get("description") or "")]
        for f in e.get("fields") or []:
            parts.append(str(f.get("name") or ""))
            parts.append(str(f.get("value") or ""))
        return "\n".join(parts)

    exit_payload = {
        "symbol": "285A", "symbol_name": "テスト銘柄",
        "entry_time": "15:05:00.42", "exit_time": "15:15:05.21",
        "entry_price": 3820, "exit_price": 3875, "qty": 100,
        "pnl_yen": 5500, "pnl_pct": 1.44, "hold_sec": 604.8,
        "daily_symbol_pnl_yen": 11500, "daily_v1r_pnl_yen": 18700,
        "today_pnl_yen": 18700, "mfe_pct": 0.08, "mae_pct": -0.11,
        "buy1": 3875, "buy1_qty": 1200, "freshness_sec": 0.4,
        "reason": "FIRST_VALID_BUY1_AT_OR_AFTER_TARGET",
    }
    exit_blob = _blob(V1RNotifyKind.EXIT, exit_payload)
    checks = {
        "ENTRY_green": event_color(V1RNotifyKind.ENTRY) == COLOR_ENTRY,
        "FILL_blue": event_color(V1RNotifyKind.FILL) == COLOR_FILL,
        "EXIT_red": event_color(V1RNotifyKind.EXIT) == COLOR_EXIT,
        "EXPIRED_orange": event_color(V1RNotifyKind.EXPIRED) == COLOR_EXPIRED,
        "EXIT_profit_red": event_color(V1RNotifyKind.EXIT, pnl_yen=5500) == COLOR_EXIT,
        "EXIT_loss_red": event_color(V1RNotifyKind.EXIT, pnl_yen=-3000) == COLOR_EXIT,
        "EXIT_profit_embed_red": build_event_embed(
            V1RNotifyKind.EXIT, exit_payload, test_only=True,
        )[2] == COLOR_EXIT,
        "EXIT_loss_embed_red": build_event_embed(
            V1RNotifyKind.EXIT,
            {**exit_payload, "pnl_yen": -3000, "pnl_pct": -1.42, "exit_price": 2075},
            test_only=True,
        )[2] == COLOR_EXIT,
        "one_embed_per_event": all(
            len(build_event_embed(k, {
                "symbol": "6674.T", "symbol_name": "ジーエス・ユアサ コーポレーション",
                "anchor": "15:05:00", "limit": 5234, "rank": 3, "score": 0.913,
                "fill": 5234, "fill_delay_sec": 0.42, "exit_target": "15:15:00.42",
                "entry_price": 5234, "exit_price": 5229, "pnl_yen": -500, "pnl_pct": -0.10,
                "hold_sec": 604.8, "today_pnl_yen": 18700, "daily_symbol_pnl_yen": -5400,
                "daily_v1r_pnl_yen": 18700, "qty": 100, "mfe_pct": 0.08, "mae_pct": -0.11,
                "reason": "FIRST_VALID_BUY1_AT_OR_AFTER_TARGET",
                "fill_time": "15:05:00.42", "expire_time": "15:05:01",
                "open": 2, "pending": 1, "cap": 5,
            }, test_only=True)[1]) == 1
            for k in (V1RNotifyKind.ENTRY, V1RNotifyKind.FILL, V1RNotifyKind.EXIT, V1RNotifyKind.EXPIRED)
        ),
        "exit_body_hides_contract": "FIRST_VALID" not in exit_blob,
        "exit_human_reason": "600秒経過後の最初の有効Buy1" in exit_blob,
        "exit_has_mfe_mae": "MFE" in exit_blob and "MAE" in exit_blob,
        "exit_has_daily_symbol": "本日同銘柄累計" in exit_blob,
        "exit_has_daily_v1r": "本日V1R累計" in exit_blob,
        "human_exit_reason_fn": human_exit_reason("FIRST_VALID_BUY1_AT_OR_AFTER_TARGET")
            == "600秒経過後の最初の有効Buy1",
    }
    return {"checks": checks, "pass": all(checks.values())}


def assert_negative_routing() -> dict[str, Any]:
    """Static routing invariants — no HTTP."""
    checks = {
        "ENTRY_not_trade_notify": ROUTING_TABLE[V1RNotifyKind.ENTRY]["env_keys"] != (WEBHOOK_ENV_TRADE,),
        "EXPIRED_not_trade_notify": ROUTING_TABLE[V1RNotifyKind.EXPIRED]["env_keys"] != (WEBHOOK_ENV_TRADE,),
        "FILL_not_research": ROUTING_TABLE[V1RNotifyKind.FILL]["env_keys"] != (WEBHOOK_ENV_RESEARCH,),
        "EXIT_not_research": ROUTING_TABLE[V1RNotifyKind.EXIT]["env_keys"] != (WEBHOOK_ENV_RESEARCH,),
        "PBV2_not_trade_notify": ROUTING_TABLE[V1RNotifyKind.PBV2_SHADOW]["env_keys"] != (WEBHOOK_ENV_TRADE,),
        "ONE_M_not_trade_notify": ROUTING_TABLE[V1RNotifyKind.ONE_M_SHADOW]["env_keys"] != (WEBHOOK_ENV_TRADE,),
        "CAP_not_trade_entry": ROUTING_TABLE[V1RNotifyKind.CAP_BLOCKED]["env_keys"] != (WEBHOOK_ENV_V1R_ENTRY,),
        "ENTRY_is_entry_env": ROUTING_TABLE[V1RNotifyKind.ENTRY]["env_keys"] == (WEBHOOK_ENV_V1R_ENTRY,),
        "EXPIRED_is_entry_env": ROUTING_TABLE[V1RNotifyKind.EXPIRED]["env_keys"] == (WEBHOOK_ENV_V1R_ENTRY,),
        "FILL_is_notify": ROUTING_TABLE[V1RNotifyKind.FILL]["env_keys"] == (WEBHOOK_ENV_TRADE,),
        "EXIT_is_notify": ROUTING_TABLE[V1RNotifyKind.EXIT]["env_keys"] == (WEBHOOK_ENV_TRADE,),
        "SUMMARY_is_research": ROUTING_TABLE[V1RNotifyKind.PRIMARY_SUMMARY]["env_keys"] == (WEBHOOK_ENV_RESEARCH,),
        "PBV2_is_research": ROUTING_TABLE[V1RNotifyKind.PBV2_SHADOW]["env_keys"] == (WEBHOOK_ENV_RESEARCH,),
        "ONE_M_is_research": ROUTING_TABLE[V1RNotifyKind.ONE_M_SHADOW]["env_keys"] == (WEBHOOK_ENV_RESEARCH,),
        "CAP_unchanged": ROUTING_TABLE[V1RNotifyKind.CAP_BLOCKED]["env_keys"] == (WEBHOOK_ENV_CAP,),
        "entry_no_fallback": ROUTING_TABLE[V1RNotifyKind.ENTRY]["fallback_forbidden"] is True,
    }
    return {"checks": checks, "pass": all(checks.values())}
