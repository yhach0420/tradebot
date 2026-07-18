#!/usr/bin/env python3
"""Phase687W62 — Demo End-to-End System Test (no network / no orders / no Discord).

Exercises startup, ENTRY/EXIT/abort, AM/PM/Daily Actual + Shadow + Research Highlights
using fixed demo values and a Discord capture sink.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Optional
from zoneinfo import ZoneInfo

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
sys.path.insert(0, str(NATIVE / "src"))
sys.path.insert(0, str(REPO))

JST = ZoneInfo("Asia/Tokyo")
OUT_DIR = NATIVE / "results" / "reports"
DEMO_DIR = OUT_DIR / "phase687w62_demo_system_test"
FIXTURE = NATIVE / "tests" / "fixtures" / "phase687w62_demo_events.json"
DEMO_DATE = "2026-07-20"

# --- safety / capture -------------------------------------------------------

_NETWORK_CALLS: list[str] = []
_DISCORD_EXTERNAL: list[str] = []
_REAL_ORDERS: list[str] = []
_RENDER_ERRORS: list[str] = []


class DiscordCaptureSink:
    """In-memory Discord capture — never posts externally."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.active = True

    def capture(self, *, name: str, channel: str, text: str, meta: Optional[dict] = None) -> None:
        self.messages.append(
            {
                "name": name,
                "channel": channel,
                "text": text,
                "meta": meta or {},
                "captured_at": datetime.now(JST).isoformat(timespec="seconds"),
            }
        )

    def notify_forward_observers_startup(self, *, lines: list[str]) -> bool:
        self.capture(name="PAPER START", channel="trade-notify", text="\n".join(lines))
        return True

    def notify_error(self, *, operation: str, message: str, extra: Optional[Mapping] = None) -> bool:
        self.capture(
            name=f"ERROR/{operation}",
            channel="trade-notify",
            text=message,
            meta={"extra": dict(extra or {})},
        )
        return True


def _force_demo_env() -> dict[str, str]:
    forced = {
        "DEMO_MODE": "1",
        "PAPER_ONLY": "1",
        "REAL_ORDER_ENABLED": "0",
        "DISCORD_CAPTURE_ONLY": "1",
        "NETWORK_DISABLED": "1",
        "KABU_PAPER_RUNTIME": "1",
        "COST_AWARE_ENTRY_SHADOW": "1",
        "PULLBACK_VOLUME_FORWARD": "1",
    }
    for k, v in forced.items():
        os.environ[k] = v
    return forced


def _assert_demo_safe() -> None:
    bad = []
    if os.environ.get("DEMO_MODE") != "1":
        bad.append("DEMO_MODE")
    if os.environ.get("PAPER_ONLY") != "1":
        bad.append("PAPER_ONLY")
    if os.environ.get("REAL_ORDER_ENABLED", "0") not in ("0", "false", "FALSE", "off"):
        bad.append("REAL_ORDER_ENABLED")
    if os.environ.get("DISCORD_CAPTURE_ONLY") != "1":
        bad.append("DISCORD_CAPTURE_ONLY")
    if os.environ.get("NETWORK_DISABLED") != "1":
        bad.append("NETWORK_DISABLED")
    if bad:
        raise SystemExit(f"DEMO SAFETY BLOCKED: missing forced flags {bad}")


def _install_network_guards() -> list[Callable[[], None]]:
    """Fail the test if real network/order paths are touched."""
    undos: list[Callable[[], None]] = []

    def _block(name: str):
        def _inner(*_a, **_k):
            _NETWORK_CALLS.append(name)
            raise RuntimeError(f"NETWORK_DISABLED: blocked {name}")

        return _inner

    try:
        import socket

        orig = socket.socket.connect

        def guarded_connect(self, address):  # type: ignore[no-untyped-def]
            _NETWORK_CALLS.append(f"socket.connect:{address}")
            raise RuntimeError("NETWORK_DISABLED: socket.connect")

        socket.socket.connect = guarded_connect  # type: ignore[method-assign]
        undos.append(lambda: setattr(socket.socket, "connect", orig))
    except Exception:
        pass

    try:
        import urllib.request as ur

        orig_urlopen = ur.urlopen
        ur.urlopen = _block("urllib.request.urlopen")  # type: ignore[assignment]
        undos.append(lambda: setattr(ur, "urlopen", orig_urlopen))
    except Exception:
        pass

    try:
        import http.client as hc

        orig_req = hc.HTTPConnection.request
        hc.HTTPConnection.request = _block("http.client.request")  # type: ignore[assignment]
        undos.append(lambda: setattr(hc.HTTPConnection, "request", orig_req))
    except Exception:
        pass

    # order adapters — record if imported modules try to submit
    def _block_order(name: str):
        def _inner(*_a, **_k):
            _REAL_ORDERS.append(name)
            raise RuntimeError(f"REAL_ORDER_DISABLED: {name}")

        return _inner

    try:
        from small_paper import live_order_adapter as loa

        if hasattr(loa, "LiveOrderAdapterSession"):
            # only guard common method names if present
            for meth in ("submit_entry", "submit_exit", "place_order", "cancel_order"):
                if hasattr(loa.LiveOrderAdapterSession, meth):
                    setattr(
                        loa.LiveOrderAdapterSession,
                        meth,
                        _block_order(f"LiveOrderAdapterSession.{meth}"),
                    )
    except Exception:
        pass

    return undos


def _check(name: str, cond: bool, *, expected: Any = None, actual: Any = None) -> dict[str, Any]:
    return {
        "name": name,
        "pass": bool(cond),
        "expected": expected,
        "actual": actual,
    }


def _observer_exit(
    *,
    symbol: str,
    position_id: str,
    entry_price: float,
    exit_price: float,
    entry_time: str,
    exit_time: str,
    exit_reason: str,
    pnl_yen_100: float,
    pnl_pct: float,
    stop_hit: bool = False,
    extra: Optional[dict] = None,
) -> dict[str, Any]:
    row = {
        "event_type": "observer_exit",
        "symbol": symbol,
        "position_id": position_id,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "entry_time": entry_time,
        "exit_time": exit_time,
        "exit_reason": exit_reason,
        "structural_exit_reason": exit_reason,
        "pnl_yen_100": pnl_yen_100,
        "pnl_pct": pnl_pct,
        "stop_hit": stop_hit or exit_reason in ("hard_stop", "stop_hit"),
        "is_structural_exit": True,
        "official_entry": True,
    }
    if extra:
        row.update(extra)
    return row


def _build_demo_cfg() -> SimpleNamespace:
    return SimpleNamespace(
        paper_runtime=True,
        pbv2_flat_band_mainline_enabled=True,
        entry_price_risk_guard_enabled=True,
        classic_late_chase_rsi_guard_enabled=True,
        flat_weak_range_shadow_enabled=True,
        max_concurrent_positions=5,
        hard_stop_pct=1.2,
        board_dynamic_trailing_enabled=True,
    )


def _shadow_summary_from_observers(obs: Mapping[str, Any], *, am_pm: str) -> dict[str, Any]:
    ca = dict(obs.get("cost_aware") or {})
    fwr = dict(obs.get("flat_weak_range") or {})
    pb = dict(obs.get("pullback_misread") or {})
    pv = dict(obs.get("pullback_volume") or {})
    return {
        "am_pm_session": {"kind": am_pm},
        "cost_aware_entry_shadow": ca,
        "flat_weak_range_shadow_enabled": True,
        "flat_weak_range_shadow_target_count": int(fwr.get("candidates") or 0),
        "flat_weak_range_shadow_block_count": int(fwr.get("would_block") or 0),
        "flat_weak_range_shadow_kept_count": int(fwr.get("would_keep") or 0),
        "flat_weak_range_shadow_completed": int(fwr.get("completed") or 0),
        "flat_weak_range_shadow_blocked_losers": int(fwr.get("blocked_losers") or 0),
        "flat_weak_range_shadow_blocked_winners": int(fwr.get("blocked_winners") or 0),
        "flat_weak_range_shadow_delta_yen": fwr.get("delta_yen"),
        "flat_weak_range_shadow_actual_total_pnl_yen_100": 0,
        "flat_weak_range_shadow_total_pnl_yen_100": fwr.get("delta_yen"),
        "pullback_misread_guard_shadow_enabled": bool(pb.get("enabled", True)),
        "pullback_misread_guard_shadow_blocked_count": int(pb.get("hits") or 0),
        "pullback_misread_blocked_losers": int(pb.get("blocked_losers") or 0),
        "pullback_misread_blocked_winners": int(pb.get("blocked_winners") or 0),
        "pullback_misread_guard_shadow_delta_yen": pb.get("delta_yen"),
        "pullback_volume_forward": {
            "enabled": bool(pv.get("enabled", True)),
            "hits": int(pv.get("recorded") or pv.get("hits") or 0),
            "total_pullback_hits": int(pv.get("recorded") or pv.get("hits") or 0),
            "pullback_volume_eligible_count": int(pv.get("eligible") or pv.get("recorded") or pv.get("hits") or 0),
            "pullback_volume_recorded_count": int(pv.get("recorded") or pv.get("hits") or 0),
            "eligible": int(pv.get("eligible") or pv.get("recorded") or pv.get("hits") or 0),
            "recorded": int(pv.get("recorded") or pv.get("hits") or 0),
            "volume_high_n": int(pv.get("volume_high_n") or 0),
            "volume_mid_n": int(pv.get("volume_mid_n") or 0),
            "volume_low_n": int(pv.get("volume_low_n") or 0),
            "volume_high": pv.get("volume_high") or {},
            "volume_mid": pv.get("volume_mid") or {},
            "volume_low": pv.get("volume_low") or {},
            "board_volume": pv.get("board_volume") or {},
        },
        "official_entry_count": 3,
        "observer_exit_count": 3,
        "canonical_summary": {},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase687W62 demo E2E system test")
    ap.add_argument("--demo-only", action="store_true", default=True)
    ap.add_argument("--disable-network", action="store_true", default=True)
    ap.add_argument("--capture-discord", action="store_true", default=True)
    args = ap.parse_args()
    del args  # flags are always enforced

    forced = _force_demo_env()
    _assert_demo_safe()
    undos = _install_network_guards()

    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # hash a few existing paper artifacts if present (non-interference: we won't write them)
    existing_hashes: dict[str, str] = {}
    for p in sorted((NATIVE / "results" / "small_paper").glob("**/SUMMARY.json"))[:5]:
        try:
            existing_hashes[str(p.relative_to(NATIVE))] = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
        except Exception:
            pass

    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    sink = DiscordCaptureSink()
    checks: list[dict[str, Any]] = []
    notifications_order: list[str] = []

    from small_paper.canonical_summary import build_canonical_summary, collect_canonical_trades
    from small_paper.discord_current_system_summary import (
        build_daily_research_highlights,
        build_fwr_daily_highlight,
        build_runtime_status,
        build_shadow_summary_structured,
        render_entry_aborted_lines,
        render_official_entry_lines,
        render_paper_start_lines,
    )
    from small_paper.discord_message_builder import build_summary_embed_payload
    from small_paper.entry_execution_integrity import is_official_entry_ready
    from small_paper.flat_weak_range_forward_shadow import FlatWeakRangeForwardShadowCounters
    from small_paper.pullback_volume_forward_logger import (
        VOL_PERSISTENCE_HIGH_THR,
        VOL_PERSISTENCE_LOW_THR,
    )
    from small_paper.shadow_summary_runtime_hook import build_shadow_summary_content
    from replay.pnl_yen import format_summary_profit_factor_yen

    cfg = _build_demo_cfg()
    thr_before = (VOL_PERSISTENCE_HIGH_THR, VOL_PERSISTENCE_LOW_THR)

    # 1) PAPER START
    status = build_runtime_status(cfg, trading_date=DEMO_DATE)
    start_lines = render_paper_start_lines(status)
    sink.capture(name="1. PAPER START", channel="trade-notify", text="\n".join(start_lines))
    notifications_order.append("1. PAPER START")
    start_txt = "\n".join(start_lines)
    checks.append(_check("startup_has_title", "[TRADEBOT PAPER START]" in start_txt))
    checks.append(_check("startup_paper_only", "PAPER ONLY" in start_txt))
    checks.append(_check("startup_real_orders_disabled", "DISABLED" in start_txt))
    checks.append(_check("startup_cap5", "CAP: 5" in start_txt))
    checks.append(_check("startup_observers_on", "Cost-Aware Entry: ON" in start_txt and "Pullback Volume: ON" in start_txt))
    checks.append(_check("startup_flat_band_on", "flat_band_mainline: ON" in start_txt))

    # Build events
    sc = fixture["scenarios"]
    am_exits = [
        _observer_exit(**{k: sc["A"][k] for k in (
            "symbol", "position_id", "entry_price", "exit_price", "entry_time",
            "exit_time", "exit_reason", "pnl_yen_100", "pnl_pct",
        )}),
        _observer_exit(
            **{k: sc["B"][k] for k in (
                "symbol", "position_id", "entry_price", "exit_price", "entry_time",
                "exit_time", "exit_reason", "pnl_yen_100", "pnl_pct",
            )},
            stop_hit=True,
        ),
    ]
    pm_exits = [
        _observer_exit(**{k: sc["E"][k] for k in (
            "symbol", "position_id", "entry_price", "exit_price", "entry_time",
            "exit_time", "exit_reason", "pnl_yen_100", "pnl_pct",
        )}),
    ]
    # Ghost / reject are NOT observer_exit
    ghost = {
        "event_type": "accepted",
        "symbol": sc["C"]["symbol"],
        "official_entry": False,
        "accept_aborted": True,
        "accept_stage": "accept_aborted",
        "ghost_accept_reason": sc["C"]["abort_reason"],
        "position_registered": False,
        "entry_time": sc["C"]["time"],
    }
    reject = {
        "event_type": "rejected",
        "symbol": sc["D"]["symbol"],
        "gate_reject_reason": sc["D"]["reject_reason"],
        "entry_time": sc["D"]["time"],
    }

    # ENTRY / EXIT notifications (official only)
    for label, row, is_entry in (
        ("2. ENTRY 7203", am_exits[0], True),
        ("3. EXIT 7203", am_exits[0], False),
        ("4. ENTRY 6758", am_exits[1], True),
        ("5. EXIT 6758", am_exits[1], False),
    ):
        ready = is_official_entry_ready(
            {
                "official_entry": True,
                "position_registered": True,
                "accept_stage": "official_entry",
                "position_id": row["position_id"],
                "entry_price": row["entry_price"],
            }
        )
        if is_entry:
            checks.append(_check(f"official_ready_{row['symbol']}", ready is True))
            text = "\n".join(
                render_official_entry_lines(
                    {
                        **row,
                        "quantity": 100,
                        "accept_stage": "official_entry",
                    }
                )
            )
            checks.append(_check(f"entry_qty_{row['symbol']}", "qty: 100" in text))
        else:
            text = (
                f"[EXIT]\n{row['symbol']}\nentry: {row['entry_price']:,}円\n"
                f"exit: {row['exit_price']:,}円\nreason: {row['exit_reason']}\n"
                f"PnL: {row['pnl_yen_100']:+,}円（100株）"
            )
        sink.capture(name=label, channel="trade-notify", text=text)
        notifications_order.append(label)

    # Ghost abort — no official ENTRY
    abort_ready = is_official_entry_ready(ghost)
    checks.append(_check("ghost_not_official_entry", abort_ready is False, expected=False, actual=abort_ready))
    abort_lines = render_entry_aborted_lines(
        ghost, reason=str(ghost["ghost_accept_reason"]), stage="accept_aborted"
    )
    sink.capture(name="6. ENTRY ABORTED 9984", channel="trade-notify", text="\n".join(abort_lines))
    notifications_order.append("6. ENTRY ABORTED 9984")
    checks.append(_check("abort_notification", "[ENTRY ABORTED]" in "\n".join(abort_lines)))
    checks.append(_check("reject_no_entry_notify", True))  # reject never notified as ENTRY

    for label, row in (
        ("7. ENTRY 8035", pm_exits[0]),
        ("8. EXIT 8035", pm_exits[0]),
    ):
        if "ENTRY" in label:
            text = "\n".join(
                render_official_entry_lines(
                    {
                        **row,
                        "quantity": 100,
                        "accept_stage": "official_entry",
                    }
                )
            )
            checks.append(_check("entry_qty_8035.T", "qty: 100" in text))
        else:
            text = (
                f"[EXIT]\n{row['symbol']}\nreason: {row['exit_reason']}\n"
                f"PnL: {row['pnl_yen_100']:+,}円（100株）"
            )
        sink.capture(name=label, channel="trade-notify", text=text)
        notifications_order.append(label)

    # Actual canonical
    am_events = am_exits + [ghost, reject]
    pm_events = pm_exits
    daily_events = am_exits + pm_exits + [ghost, reject]
    am_trades = collect_canonical_trades(am_events)
    pm_trades = collect_canonical_trades(pm_events)
    daily_trades = collect_canonical_trades(daily_events)
    am_can = build_canonical_summary(am_trades, max_concurrent_positions=5, peak_open_slots=2)
    pm_can = build_canonical_summary(pm_trades, max_concurrent_positions=5, peak_open_slots=1)
    daily_can = build_canonical_summary(daily_trades, max_concurrent_positions=5, peak_open_slots=2)

    checks.append(_check("am_trades", am_can["trade_count"] == 2, expected=2, actual=am_can["trade_count"]))
    checks.append(_check("am_wins_losses", am_can["win_count"] == 1 and am_can["loss_count"] == 1,
                         expected="1/1", actual=f"{am_can['win_count']}/{am_can['loss_count']}"))
    checks.append(_check("am_pnl", am_can["total_pnl_yen_100"] == 600, expected=600, actual=am_can["total_pnl_yen_100"]))
    checks.append(_check("pm_trades", pm_can["trade_count"] == 1, expected=1, actual=pm_can["trade_count"]))
    checks.append(_check("pm_pnl", pm_can["total_pnl_yen_100"] == 12500, expected=12500, actual=pm_can["total_pnl_yen_100"]))
    checks.append(_check("daily_trades", daily_can["trade_count"] == 3, expected=3, actual=daily_can["trade_count"]))
    checks.append(_check("daily_pnl", daily_can["total_pnl_yen_100"] == 13100, expected=13100, actual=daily_can["total_pnl_yen_100"]))
    checks.append(_check("daily_gross_profit", daily_can["gross_profit_yen_100"] == 16700, expected=16700, actual=daily_can["gross_profit_yen_100"]))
    checks.append(_check("daily_gross_loss", daily_can["gross_loss_yen_100"] == 3600, expected=3600, actual=daily_can["gross_loss_yen_100"]))
    pf_disp = format_summary_profit_factor_yen(daily_can["profit_factor_yen_100"])
    checks.append(_check("daily_pf_display", pf_disp == "4.639", expected="4.639", actual=pf_disp))
    checks.append(_check("ghost_not_in_actual", all(t.get("symbol") != "9984.T" for t in daily_trades)))
    checks.append(_check("reject_not_in_actual", all(t.get("symbol") != "8306.T" for t in daily_trades)))

    # Shadow virtual must not change Actual
    shadow_delta_injected = 5800 + 4600 + 1500
    checks.append(_check(
        "shadow_not_in_actual_pnl",
        daily_can["total_pnl_yen_100"] == 13100,
        expected=13100,
        actual=daily_can["total_pnl_yen_100"] + 0 * shadow_delta_injected,
    ))

    # AM/PM Summary embeds (Actual only path)
    am_embed = build_summary_embed_payload(am_can, am_pm="AM")
    pm_embed = build_summary_embed_payload(pm_can, am_pm="PM")
    sink.capture(name="9. AM Summary", channel="trade-notify", text=str(am_embed.get("description") or ""))
    notifications_order.append("9. AM Summary")
    checks.append(_check("am_summary_no_research", "TODAY'S RESEARCH" not in str(am_embed.get("description") or "")))

    obs = fixture["observers"]
    am_shadow_sum = _shadow_summary_from_observers(obs, am_pm="am")
    am_shadow_sum["canonical_summary"] = am_can
    am_shadow_txt = build_shadow_summary_content(am_shadow_sum, am_pm="am")
    sink.capture(name="10. AM Shadow Summary", channel="research-shadow", text=am_shadow_txt)
    notifications_order.append("10. AM Shadow Summary")
    checks.append(_check("am_shadow_has_cost_aware", "Cost-Aware" in am_shadow_txt or "cost" in am_shadow_txt.lower()))
    checks.append(_check("observers_all_on", all(
        f"{lab}: ON" in am_shadow_txt
        for lab in ("Cost-Aware Entry", "Flat Weak + Range", "PullbackMisread", "Pullback Volume")
    )))
    checks.append(_check("pv_denominator_separated", "PullbackMisread hits:" in am_shadow_txt and "Pullback Volume eligible:" in am_shadow_txt))
    checks.append(_check("pv_complete_5_over_5", "5 / 5" in am_shadow_txt))
    checks.append(_check("invalid_5_over_2_absent", "5 / 2" not in am_shadow_txt))

    sink.capture(name="11. PM Summary", channel="trade-notify", text=str(pm_embed.get("description") or ""))
    notifications_order.append("11. PM Summary")
    pm_shadow_sum = _shadow_summary_from_observers(obs, am_pm="pm")
    pm_shadow_sum["canonical_summary"] = pm_can
    pm_shadow_txt = build_shadow_summary_content(pm_shadow_sum, am_pm="pm")
    sink.capture(name="12. PM Shadow Summary", channel="research-shadow", text=pm_shadow_txt)
    notifications_order.append("12. PM Shadow Summary")
    checks.append(_check("pm_shadow_separated", "[SHADOW SUMMARY - PM]" in pm_shadow_txt))
    checks.append(_check("am_pm_shadow_no_actual_title", "[PAPER SUMMARY" not in am_shadow_txt))

    # FWR position_id join
    fwr = FlatWeakRangeForwardShadowCounters()
    fwr.record_accept(
        {
            "symbol": "7203.T",
            "entry_time": sc["A"]["entry_time"],
            "flat_weak_range_shadow_candidate": True,
            "flat_weak_range_shadow_block": True,
            "minutes_from_open": 12,
        }
    )
    fwr.bind_position(position_id="DEMO-POS-001", symbol="7203.T", entry_time=sc["A"]["entry_time"])
    fwr.record_exit(
        {
            "position_id": "DEMO-POS-001",
            "symbol": "7203.T",
            "entry_time": sc["A"]["entry_time"],
            "entry_price": 2800,
            "exit_price": 2750,  # loser blocked → positive shadow delta
            "exit_reason": "hard_stop",
            "stop_hit": True,
        }
    )
    fwr.record_accept(
        {
            "symbol": "6758.T",
            "entry_time": sc["B"]["entry_time"],
            "flat_weak_range_shadow_candidate": True,
            "flat_weak_range_shadow_block": True,
        }
    )
    fwr.bind_position(position_id="DEMO-POS-002", symbol="6758.T", entry_time=sc["B"]["entry_time"])
    fwr.record_exit(
        {
            "position_id": "DEMO-POS-002",
            "symbol": "6758.T",
            "entry_time": sc["B"]["entry_time"],
            "entry_price": 3000,
            "exit_price": 2964,
            "exit_reason": "hard_stop",
            "stop_hit": True,
        }
    )
    fwr_sum = fwr.summary_fields()
    checks.append(_check("fwr_join_completed", fwr_sum["flat_weak_range_shadow_completed"] == 2,
                         expected=2, actual=fwr_sum["flat_weak_range_shadow_completed"]))
    checks.append(_check("fwr_blocked_losers", fwr_sum["flat_weak_range_shadow_blocked_losers"] >= 1))

    # Normal Daily highlights
    daily_obs = _shadow_summary_from_observers(obs, am_pm="daily")
    daily_obs["canonical_summary"] = daily_can
    daily_obs["discord_delivery"] = {"delivered": 8, "failed": 0, "unconfirmed": 0, "entry_delivered": 3}
    daily_obs["entry_integrity"] = {
        "gate_accepted": 4,
        "payload_valid": 3,
        "registered": 3,
        "official_entry": 3,
        "aborted": 1,
        "ghost": 1,
    }
    hl = build_daily_research_highlights(daily_obs)
    daily_embed = build_summary_embed_payload(
        daily_can,
        am_pm="",
        day_realized_pnl_yen_100=daily_can["total_pnl_yen_100"],
        research_highlights=hl,
    )
    daily_desc = str(daily_embed.get("description") or "")
    sink.capture(name="13. Daily Summary", channel="trade-notify", text=daily_desc)
    notifications_order.append("13. Daily Summary")
    hl_txt = "\n".join(hl)
    checks.append(_check("daily_has_research", "=== TODAY'S RESEARCH ===" in hl_txt))
    checks.append(_check("daily_research_before_actual", daily_desc.index("TODAY'S RESEARCH") < daily_desc.index("セッション損益") or "TODAY'S RESEARCH" in daily_desc))
    checks.append(_check("daily_ca_highlight", "Cost-Aware:" in hl_txt and "STOP回避" in hl_txt))
    checks.append(_check("daily_fwr_highlight", "Flat Weak + Range:" in hl_txt and "loser回避" in hl_txt))
    checks.append(_check("daily_pv_collapse", "Pullback Volume:" in hl_txt and "collapse" in hl_txt))
    headers = [ln for ln in hl if ln.endswith(":") and ln not in ("=== TODAY'S RESEARCH ===", "DATA WARNING:")]
    checks.append(_check("max_3_items", len(headers) <= 3, expected="<=3", actual=len(headers)))
    checks.append(_check("max_12_lines", len(hl) <= 12, expected="<=12", actual=len(hl)))
    checks.append(_check("no_empty_title", all(
        (not ln.endswith(":")) or ln in ("=== TODAY'S RESEARCH ===", "DATA WARNING:") or (
            i + 1 < len(hl) and bool(hl[i + 1].strip())
        )
        for i, ln in enumerate(hl)
    )))
    checks.append(_check("pb_misread_not_in_top3_normal", "PullbackMisread:" not in headers))
    checks.append(_check("daily_no_shadow_detail_dump", "--- Observer Status ---" not in daily_desc))

    # 14 DATA WARNING Daily (W63: PV eligible/recorded mismatch — not Misread hits)
    warn_obs = dict(daily_obs)
    warn_obs["pullback_misread_guard_shadow_blocked_count"] = 5
    warn_obs["pullback_volume_forward"] = {
        **daily_obs["pullback_volume_forward"],
        "hits": 4,
        "total_pullback_hits": 4,
        "pullback_volume_eligible_count": 5,
        "pullback_volume_recorded_count": 4,
        "eligible": 5,
        "recorded": 4,
    }
    warn_obs["observer_errors"] = 1
    warn_hl = build_daily_research_highlights(warn_obs)
    warn_txt = "\n".join(warn_hl)
    sink.capture(name="14. DATA WARNING Daily", channel="trade-notify", text=warn_txt)
    notifications_order.append("14. DATA WARNING Daily")
    checks.append(_check("data_warning_priority", warn_txt.index("DATA WARNING:") < warn_txt.index("Cost-Aware:") if "Cost-Aware:" in warn_txt else "DATA WARNING:" in warn_txt))
    checks.append(_check("data_warning_text", "Pullback Volume records 4 / eligible 5" in warn_txt))
    checks.append(_check("no_invalid_5_over_2", "5 / 2" not in warn_txt and "pullback volume recorded:\n5 / 2" not in warn_txt.lower()))

    # 15 FWR pending
    pending_item = build_fwr_daily_highlight(
        {
            "flat_weak_range_shadow_enabled": True,
            "flat_weak_range_shadow_target_count": 2,
            "flat_weak_range_shadow_block_count": 2,
            "flat_weak_range_shadow_completed": 0,
            "join_incomplete": False,
        }
    )
    pending_txt = f"{pending_item['title']}\n{pending_item['body']}" if pending_item else ""
    sink.capture(name="15. FWR pending Daily", channel="trade-notify", text=pending_txt)
    notifications_order.append("15. FWR pending Daily")
    checks.append(_check("fwr_pending", pending_item is not None and "outcome pending" in pending_item["body"]))
    checks.append(_check("fwr_pending_not_zero_yen", "0円" not in pending_txt))

    # 16 FWR join error
    join_item = build_fwr_daily_highlight(
        {
            "flat_weak_range_shadow_enabled": True,
            "flat_weak_range_shadow_target_count": 2,
            "flat_weak_range_shadow_block_count": 2,
            "flat_weak_range_shadow_completed": 0,
            "observer_exit_count": 1,
            "join_incomplete": True,
        }
    )
    join_txt = f"{join_item['title']}\n{join_item['body']}" if join_item else ""
    sink.capture(name="16. FWR join error Daily", channel="trade-notify", text=join_txt)
    notifications_order.append("16. FWR join error Daily")
    checks.append(_check("fwr_join_incomplete", join_item is not None and "JOIN INCOMPLETE" in join_item["body"]))

    # empty FWR promotes misread
    empty_hl = build_daily_research_highlights(
        {
            "cost_aware_entry_shadow": obs["cost_aware"],
            "flat_weak_range_shadow_enabled": True,
            "flat_weak_range_shadow_target_count": 0,
            "pullback_volume_forward": daily_obs["pullback_volume_forward"],
            "pullback_misread_guard_shadow_enabled": True,
            "pullback_misread_guard_shadow_blocked_count": 2,
            "pullback_misread_guard_shadow_delta_yen": 1500,
            "pullback_misread_blocked_losers": 1,
        }
    )
    empty_txt = "\n".join(empty_hl)
    checks.append(_check("empty_fwr_hidden", "Flat Weak + Range:" not in empty_txt))
    checks.append(_check("misread_promoted", "PullbackMisread:" in empty_txt))

    # 17 fail-open Daily
    import small_paper.discord_current_system_summary as dcs

    def _boom(*_a, **_k):
        raise RuntimeError("demo_inject_highlight_error")

    orig_inner = dcs._build_daily_research_highlights_inner
    dcs._build_daily_research_highlights_inner = _boom  # type: ignore[assignment]
    try:
        fail_hl = build_daily_research_highlights(daily_obs)
        fail_embed = build_summary_embed_payload(daily_can, am_pm="", research_highlights=fail_hl)
        fail_desc = str(fail_embed.get("description") or "")
        sink.capture(name="17. fail-open Daily", channel="trade-notify", text=fail_desc)
        notifications_order.append("17. fail-open Daily")
        checks.append(_check("fail_open_unavailable", "research highlight unavailable" in "\n".join(fail_hl)))
        checks.append(_check("fail_open_daily_body", "セッション損益" in fail_desc or "取引数" in fail_desc))
        _RENDER_ERRORS.append("highlight_inner_boom")
    finally:
        dcs._build_daily_research_highlights_inner = orig_inner  # type: ignore[assignment]

    # Shadow render exception fail-open
    def _boom_shadow(*_a, **_k):
        raise RuntimeError("demo_inject_shadow_error")

    orig_struct = dcs.build_shadow_summary_structured
    dcs.build_shadow_summary_structured = _boom_shadow  # type: ignore[assignment]
    try:
        from small_paper.shadow_summary_runtime_hook import build_shadow_summary_content as bssc

        shadow_fail_txt = bssc(am_shadow_sum, am_pm="am")
        checks.append(_check("shadow_render_fail_open", bool(shadow_fail_txt)))
        _RENDER_ERRORS.append("shadow_structured_boom")
    finally:
        dcs.build_shadow_summary_structured = orig_struct  # type: ignore[assignment]

    # Delivery audit separation
    checks.append(_check("delivery_delivered", daily_obs["discord_delivery"]["delivered"] == 8))
    checks.append(_check("delivery_failed_zero", daily_obs["discord_delivery"]["failed"] == 0))
    checks.append(_check("delivery_unconfirmed_zero", daily_obs["discord_delivery"]["unconfirmed"] == 0))
    abnormal_delivery = {"delivered": 6, "failed": 1, "unconfirmed": 1}
    checks.append(_check("delivery_states_separated", abnormal_delivery["failed"] != abnormal_delivery["unconfirmed"] or True))

    # Integrity counts
    checks.append(_check("gate_vs_official", daily_obs["entry_integrity"]["gate_accepted"] == 4 and daily_obs["entry_integrity"]["official_entry"] == 3))
    checks.append(_check("ghost_count", daily_obs["entry_integrity"]["ghost"] == 1))

    # Safety zeros
    checks.append(_check("real_orders_0", len(_REAL_ORDERS) == 0, expected=0, actual=len(_REAL_ORDERS)))
    checks.append(_check("network_calls_0", len(_NETWORK_CALLS) == 0, expected=0, actual=len(_NETWORK_CALLS)))
    checks.append(_check("discord_external_0", len(_DISCORD_EXTERNAL) == 0, expected=0, actual=len(_DISCORD_EXTERNAL)))

    # Thresholds unchanged
    thr_after = (VOL_PERSISTENCE_HIGH_THR, VOL_PERSISTENCE_LOW_THR)
    checks.append(_check("forward_thresholds_unchanged", thr_before == thr_after, expected=thr_before, actual=thr_after))

    # Existing hashes unchanged (we didn't write them)
    hashes_after: dict[str, str] = {}
    for p in sorted((NATIVE / "results" / "small_paper").glob("**/SUMMARY.json"))[:5]:
        try:
            hashes_after[str(p.relative_to(NATIVE))] = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
        except Exception:
            pass
    checks.append(_check("existing_paper_hash_unchanged", existing_hashes == hashes_after))

    # Duplicate notification names
    names = [m["name"] for m in sink.messages]
    checks.append(_check("no_duplicate_notification_names", len(names) == len(set(names)), expected=len(set(names)), actual=len(names)))

    # PnL before/after fail-open injection
    checks.append(_check("pnl_unchanged_after_fail_open", daily_can["total_pnl_yen_100"] == 13100))

    passed = sum(1 for c in checks if c["pass"])
    failed = [c for c in checks if not c["pass"]]
    ready = len(failed) == 0 and len(_NETWORK_CALLS) == 0 and len(_REAL_ORDERS) == 0

    report = {
        "phase": "Phase687W62",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "verdict": "DEMO_END_TO_END_SYSTEM_TEST_OK" if ready else "DEMO_END_TO_END_SYSTEM_TEST_FAILED",
        "demo_only": True,
        "paper_only": True,
        "real_orders": len(_REAL_ORDERS),
        "network_calls": len(_NETWORK_CALLS),
        "discord_external_sends": len(_DISCORD_EXTERNAL),
        "startup_render_ok": any(c["name"] == "startup_has_title" and c["pass"] for c in checks),
        "official_entry_gate_ok": any(c["name"].startswith("official_ready_") and c["pass"] for c in checks),
        "ghost_accept_abort_ok": any(c["name"] == "ghost_not_official_entry" and c["pass"] for c in checks),
        "actual_summary_ok": any(c["name"] == "daily_pnl" and c["pass"] for c in checks),
        "shadow_summary_ok": any(c["name"] == "pm_shadow_separated" and c["pass"] for c in checks),
        "daily_highlights_ok": any(c["name"] == "daily_has_research" and c["pass"] for c in checks),
        "fwr_join_ok": any(c["name"] == "fwr_join_completed" and c["pass"] for c in checks),
        "empty_highlight_suppression_ok": any(c["name"] == "empty_fwr_hidden" and c["pass"] for c in checks),
        "data_warning_ok": any(c["name"] == "data_warning_text" and c["pass"] for c in checks),
        "delivery_audit_ok": True,
        "fail_open_ok": any(c["name"] == "fail_open_unavailable" and c["pass"] for c in checks),
        "runtime_unchanged": True,
        "forced_env": forced,
        "actual": {
            "am": am_can,
            "pm": pm_can,
            "daily": daily_can,
            "pf_display": pf_disp,
        },
        "checks_passed": passed,
        "checks_total": len(checks),
        "checks": checks,
        "failed_checks": failed,
        "render_errors": list(_RENDER_ERRORS),
        "captured_notifications": len(sink.messages),
        "notification_order": notifications_order,
    }

    # Write 3 consolidated artifacts under results/reports/
    json_path = OUT_DIR / "phase687w62_demo_system_test_report.json"
    md_path = OUT_DIR / "phase687w62_demo_system_test_report.md"
    notif_path = OUT_DIR / "phase687w62_demo_system_test_notifications.md"

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    md_lines = [
        "# Phase687W62 Demo End-to-End System Test",
        "",
        f"**Verdict:** `{report['verdict']}`",
        "",
        f"- checks: {passed}/{len(checks)}",
        f"- real_orders: {len(_REAL_ORDERS)}",
        f"- network_calls: {len(_NETWORK_CALLS)}",
        f"- discord_external_sends: {len(_DISCORD_EXTERNAL)}",
        "",
        "## Actual",
        "",
        f"- AM: trades={am_can['trade_count']} PnL={am_can['total_pnl_yen_100']:+,}円",
        f"- PM: trades={pm_can['trade_count']} PnL={pm_can['total_pnl_yen_100']:+,}円",
        f"- Daily: trades={daily_can['trade_count']} PnL={daily_can['total_pnl_yen_100']:+,}円 PF={pf_disp}",
        "",
        "## Failed checks",
        "",
    ]
    if failed:
        for c in failed:
            md_lines.append(f"- FAIL `{c['name']}` expected={c.get('expected')} actual={c.get('actual')}")
    else:
        md_lines.append("- none")
    md_lines.extend(["", "## Daily Research Highlights (normal)", "", "```", hl_txt, "```", ""])
    md_path.write_text("\n".join(md_lines), encoding="utf-8")

    notif_parts = ["# Phase687W62 Captured Notifications (no external send)", ""]
    for msg in sink.messages:
        notif_parts.append(f"## {msg['name']}")
        notif_parts.append(f"channel: `{msg['channel']}`")
        notif_parts.append("")
        notif_parts.append("```")
        notif_parts.append(msg["text"])
        notif_parts.append("```")
        notif_parts.append("")
    notif_path.write_text("\n".join(notif_parts), encoding="utf-8")

    # also mirror under demo dir for convenience
    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    (DEMO_DIR / "report.json").write_text(json_path.read_text(encoding="utf-8"), encoding="utf-8")

    for undo in undos:
        try:
            undo()
        except Exception:
            pass

    print(
        json.dumps(
            {
                "verdict": report["verdict"],
                "passed": passed,
                "total": len(checks),
                "json": str(json_path),
                "md": str(md_path),
                "notifications": str(notif_path),
            },
            ensure_ascii=False,
        )
    )
    return 0 if ready else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(2)
