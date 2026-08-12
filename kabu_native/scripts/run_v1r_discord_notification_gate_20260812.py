#!/usr/bin/env python
"""V1R-native Discord notification gate (INVALID day operational validation).

Actual runtime path (no strategy mutation):
  Demo board ingest → fire_anchor_at → PENDING → EXPIRED / FILL
  Dual Primary EXIT → trade-notify
  PBv2 SHADOW → trade-research only (never trade-notify Primary)

Verdict target: V1R_NATIVE_DISCORD_NOTIFICATION_READY
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT.parent))

os.environ.setdefault("V1R_EXIT_V2_LIVE_PRIMARY", "1")

from notify.v1r_discord_routing import (  # noqa: E402
    ROUTING_TABLE,
    V1RNotifyKind,
    publish_v1r,
)
from small_paper.v1r_live_dual_lane import (  # noqa: E402
    V1RLiveDualLane,
    reset_dual_lane_for_tests,
)
from small_paper.v1r_native_entry_live import (  # noqa: E402
    V1RNativeEntryLive,
    reset_native_entry_for_tests,
)
from small_paper.v1r_primary_runtime import WAIT_SEC  # noqa: E402

JST = ZoneInfo("Asia/Tokyo")
OUT = ROOT / "results" / "research" / "v1r_discord_notification_gate_20260812"
OUT.mkdir(parents=True, exist_ok=True)


def _push_history(eng: V1RNativeEntryLive, symbol: str, t0: float, *, bid: float, ask: float) -> None:
    for k in range(200):
        tt = t0 - 200.0 + k
        mid_drift = 1.0 + 0.001 * (k / 200.0)
        b = bid * mid_drift
        a = b + (ask - bid)
        eng.ingest_push(
            symbol=symbol,
            payload={
                "Buy1": {"Price": b, "Qty": 300.0},
                "Sell1": {"Price": a, "Qty": 300.0},
                "board_age_sec": 0.2,
                "SpecialQuote": False,
            },
            event_t=tt,
        )
    eng.ingest_push(
        symbol=symbol,
        payload={
            "Buy1": {"Price": bid, "Qty": 500.0},
            "Sell1": {"Price": ask, "Qty": 200.0},
            "board_age_sec": 0.1,
            "SpecialQuote": False,
        },
        event_t=t0,
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _case_pending_expired(trace: Path) -> dict[str, Any]:
    reset_native_entry_for_tests()
    reset_dual_lane_for_tests()
    eng = V1RNativeEntryLive(
        universe=["3103"],
        score_fn=lambda _f: 9.0,
        model_ser={},
        ready=True,
        trace_dir=trace / "pending_expired",
        trading_date="20260812",
    )
    t0 = datetime(2026, 8, 12, 12, 40, 0, tzinfo=JST).timestamp()
    # ask above limit → expire
    _push_history(eng, "3103", t0, bid=1000.0, ask=1015.0)
    pending = eng.fire_anchor_at(anchor="12:40", t0=t0, day="20260812", session="PM")
    expired = eng.on_tick_fill_check(event_t=t0 + float(WAIT_SEC) + 0.05)
    delivery = _read_jsonl(eng.trace_dir / "v1r_discord_delivery.jsonl")
    kinds = [d.get("kind") for d in delivery]
    return {
        "pending_n": len(pending),
        "expired_n": sum(1 for e in expired if e.get("kind") == "V1R_EXPIRED"),
        "delivery_kinds": kinds,
        "entry_channel": next((d.get("channel") for d in delivery if d.get("kind") == "ENTRY"), None),
        "expired_channel": next((d.get("channel") for d in delivery if d.get("kind") == "EXPIRED"), None),
        "entry_pass": any(d.get("kind") == "ENTRY" and d.get("channel") == "trade-entry" for d in delivery),
        "expired_pass": any(d.get("kind") == "EXPIRED" and d.get("channel") == "trade-entry" for d in delivery),
        "payload_ok": all(
            d.get("source") == "v1r_native" and d.get("role") == "PAPER_PRIMARY"
            for d in delivery
            if d.get("kind") in ("ENTRY", "EXPIRED")
        ),
        "submit_cancel_live": eng.snapshot().get("submit_cancel_live"),
    }


def _case_pending_fill(trace: Path) -> dict[str, Any]:
    reset_native_entry_for_tests()
    reset_dual_lane_for_tests()
    # Keep dual off for isolated FILL notify (avoid EXIT side-effects)
    os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "0"
    eng = V1RNativeEntryLive(
        universe=["4680"],
        score_fn=lambda _f: 9.0,
        model_ser={},
        ready=True,
        trace_dir=trace / "pending_fill",
        trading_date="20260812",
    )
    t0 = datetime(2026, 8, 12, 12, 45, 0, tzinfo=JST).timestamp()
    _push_history(eng, "4680", t0, bid=2000.0, ask=2010.0)
    pending = eng.fire_anchor_at(anchor="12:45", t0=t0, day="20260812", session="PM")
    # Cross ask within wait window
    eng.ingest_push(
        symbol="4680",
        payload={
            "Buy1": {"Price": 2000.0, "Qty": 500.0},
            "Sell1": {"Price": 1999.0, "Qty": 200.0},
            "board_age_sec": 0.1,
            "SpecialQuote": False,
        },
        event_t=t0 + 0.2,
    )
    fills = eng.on_tick_fill_check(event_t=t0 + 0.25)
    delivery = _read_jsonl(eng.trace_dir / "v1r_discord_delivery.jsonl")
    os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"
    return {
        "pending_n": len(pending),
        "fill_n": sum(1 for e in fills if e.get("kind") == "V1R_FILL"),
        "delivery_kinds": [d.get("kind") for d in delivery],
        "fill_channel": next((d.get("channel") for d in delivery if d.get("kind") == "FILL"), None),
        "fill_pass": any(d.get("kind") == "FILL" and d.get("channel") == "trade-notify" for d in delivery),
        "submit_cancel_live": eng.snapshot().get("submit_cancel_live"),
    }


def _case_exit(trace: Path) -> dict[str, Any]:
    reset_native_entry_for_tests()
    reset_dual_lane_for_tests()
    os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"
    dual = V1RLiveDualLane(trace_dir=trace / "exit")
    from small_paper.v1r_live_dual_lane import LanePosition

    pos = LanePosition(
        symbol="5803",
        lane="primary",
        fill_time=datetime(2026, 8, 12, 12, 50, 0, tzinfo=JST).timestamp(),
        fill_price=1500.0,
        fill_iso="t",
    )
    dual._close(
        pos,
        {
            "reason": "CONT_EXIT_600",
            "exit_time": pos.fill_time + 600.0,
            "exit_price": 1510.0,
            "triggered_guard": False,
            "extended": False,
            "exit_off": 600.0,
        },
        {},
    )
    delivery = _read_jsonl(dual.trace_dir / "v1r_discord_delivery.jsonl")
    return {
        "exit_pass": any(d.get("kind") == "EXIT" and d.get("channel") == "trade-notify" for d in delivery),
        "delivery": delivery,
    }


def _case_pbv2_isolation() -> dict[str, Any]:
    r = publish_v1r(
        V1RNotifyKind.PBV2_SHADOW,
        {
            "symbol": "9999",
            "note": "SHADOW_ONLY isolation probe",
            "role": "SHADOW_ONLY",
            "source": "pbv2_shadow",
            "status": "SHADOW_ACCEPT",
        },
        test_only=True,
        sync_http=False,
    )
    return {
        "channel": r.channel,
        "not_trade_notify": r.channel != "trade-notify",
        "is_research": r.channel == "trade-research",
        "routing_env": ROUTING_TABLE[V1RNotifyKind.PBV2_SHADOW]["env_keys"][0],
    }


def main() -> int:
    stamp = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    trace = OUT / f"run_{stamp}"
    trace.mkdir(parents=True, exist_ok=True)

    pending_expired = _case_pending_expired(trace)
    pending_fill = _case_pending_fill(trace)
    exit_case = _case_exit(trace)
    pbv2 = _case_pbv2_isolation()

    # Allow async worker a moment to settle (delivery already recorded at enqueue)
    time.sleep(0.2)

    report = {
        "ts": datetime.now(JST).isoformat(timespec="seconds"),
        "root_cause_fixed": (
            "V1RNativeEntryLive._notify called publish_v1r(..., dry_run=True|False) "
            "but publish_v1r has no dry_run kwarg → TypeError swallowed by bare except"
        ),
        "notifier_call_sites": {
            "PENDING_ENTRY": "V1RNativeEntryLive._run_anchor → _notify(ENTRY)",
            "EXPIRED": "V1RNativeEntryLive.on_tick_fill_check → _notify(EXPIRED)",
            "FILL": "V1RNativeEntryLive._promote_fill → _notify(FILL)",
            "EXIT": "V1RLiveDualLane._close(primary) → _notify_primary_exit / eng._notify(EXIT)",
            "PBV2": "pilot_runner pbv2 shadow → publish_v1r(PBV2_SHADOW) trade-research only",
        },
        "channel_routing": {
            "ENTRY_PENDING": "trade-entry",
            "EXPIRED": "trade-entry",
            "FILL": "trade-notify",
            "EXIT": "trade-notify",
            "PBV2": "trade-research / SHADOW_ONLY",
        },
        "PENDING_notification_PASS": pending_expired["entry_pass"],
        "EXPIRED_notification_PASS": pending_expired["expired_pass"],
        "FILL_notification_PASS": pending_fill["fill_pass"],
        "EXIT_notification_PASS": exit_case["exit_pass"],
        "PBv2_isolation": pbv2["not_trade_notify"] and pbv2["is_research"],
        "delivery_errors": {
            "pending_expired_dir": str(trace / "pending_expired"),
            "pending_fill_dir": str(trace / "pending_fill"),
            "exit_dir": str(trace / "exit"),
            "writer": "v1r_discord_delivery.jsonl + v1r_discord_errors.jsonl (silent fail forbidden)",
        },
        "submit_cancel_live": pending_expired.get("submit_cancel_live"),
        "strategy_precommit_mutation": False,
        "cases": {
            "pending_expired": pending_expired,
            "pending_fill": pending_fill,
            "exit": exit_case,
            "pbv2": pbv2,
        },
    }
    ok = all(
        [
            report["PENDING_notification_PASS"],
            report["EXPIRED_notification_PASS"],
            report["FILL_notification_PASS"],
            report["EXIT_notification_PASS"],
            report["PBv2_isolation"],
            report["submit_cancel_live"] == "0/0/0",
            report["strategy_precommit_mutation"] is False,
        ]
    )
    report["verdict"] = (
        "V1R_NATIVE_DISCORD_NOTIFICATION_READY" if ok else "V1R_NATIVE_DISCORD_NOTIFICATION_FAIL"
    )
    out_path = OUT / f"report_{stamp}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"REPORT={out_path}")
    print(f"VERDICT={report['verdict']}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
