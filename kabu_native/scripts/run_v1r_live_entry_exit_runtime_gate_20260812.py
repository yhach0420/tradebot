#!/usr/bin/env python
"""V1R LIVE ENTRY+EXIT runtime gate (operational validation — not Prospective).

Exercises actual dual-lane runtime path used by pilot:
  try_admit_fill(snapshot) → on_tick(.T symbol) → Arch E / FIXED600 EXIT

Cases:
  A) Early Guard IMBALANCE EXIT (<=120s)
  B) CONT_EXIT_600 (no continuation)
  C) CONT_EXTEND_750 then FIRST_VALID_BUY1
  + Control FIXED600 EXIT on each fill
  + pilot_runner hook path with Stage0-like .T symbol
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT.parent))

os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"

from small_paper.v1r_live_dual_lane import (  # noqa: E402
    V1RLiveDualLane,
    canonical_symbol_key,
    ensure_dual_lane,
    reset_dual_lane_for_tests,
)
from small_paper.v1r_native_entry_live import (  # noqa: E402
    reset_native_entry_for_tests,
    boot_v1r_native_entry,
)

JST = ZoneInfo("Asia/Tokyo")
OUT = ROOT / "results" / "research" / "v1r_live_entry_exit_runtime_gate_20260812"
OUT.mkdir(parents=True, exist_ok=True)


def _t0(hour: int, minute: int) -> float:
    """In-session AM clock so Frozen sess_end (11:30) is after 600/750 horizons."""
    return datetime(2026, 8, 12, hour, minute, tzinfo=JST).timestamp()


def _payload(
    t: float,
    *,
    bid: float,
    ask: float,
    bq: float,
    aq: float,
) -> dict[str, Any]:
    return {
        "event_time": t,
        "CurrentPriceTime": datetime.fromtimestamp(t, JST).isoformat(timespec="milliseconds"),
        "Buy1": {"Price": bid, "Qty": bq},
        "Sell1": {"Price": ask, "Qty": aq},
        "CurrentPrice": (bid + ask) / 2.0,
        "board_age_sec": 0.0,
        "fresh_sec": 0.0,
        "SpecialQuote": False,
        "imbalance": (bq - aq) / (bq + aq) if (bq + aq) > 0 else 0.0,
    }


def _feed(
    dual: V1RLiveDualLane,
    *,
    symbol_tick: str,
    t0: float,
    series: list[tuple[float, float, float, float, float]],
) -> list[dict[str, Any]]:
    """series rows: (off, bid, ask, bq, aq)"""
    exits: list[dict[str, Any]] = []
    for i, (off, bid, ask, bq, aq) in enumerate(series):
        t = t0 + float(off)
        ex = dual.on_tick(
            symbol=symbol_tick,
            payload=_payload(t, bid=bid, ask=ask, bq=bq, aq=aq),
            event_t=t,
            push_sequence=i + 1,
        )
        exits.extend(ex)
    return exits


def _case_guard(trace: Path) -> dict[str, Any]:
    reset_dual_lane_for_tests()
    dual = V1RLiveDualLane(trace_dir=trace)
    t0 = _t0(9, 5)
    fill_px = 1000.0
    admit = dual.try_admit_fill(
        symbol="6098",
        fill_price=fill_px,
        fill_time=t0,
        payload=_payload(t0, bid=999.0, ask=1001.0, bq=100.0, aq=300.0),
        source="v1r_native",
    )
    # Persist sell imbalance for >5s within 120s monitor; keep executable Buy1.
    series = [(float(s), 990.0, 1005.0, 100.0, 400.0) for s in range(0, 20)]
    exits = _feed(dual, symbol_tick="6098.T", t0=t0, series=series)
    prim = [e for e in exits if e.get("lane") == "primary"]
    ctrl = [e for e in exits if e.get("lane") == "control"]
    return {
        "case": "A_EARLY_GUARD",
        "admit": admit,
        "primary_exit": prim[-1] if prim else None,
        "control_exits_before_600": bool(ctrl),
        "primary_open": dual.open_n("primary"),
        "control_open": dual.open_n("control"),
        "guard_triggers": dual.stats.guard_triggers,
        "tick_matches": dual.stats.tick_matches,
        "events": [r.get("event") for r in dual.traces],
        "pass": bool(
            admit.get("primary_admitted")
            and admit.get("fill_snapshot_bound")
            and prim
            and prim[-1].get("triggered_guard")
            and dual.open_n("primary") == 0
            and dual.stats.tick_matches > 0
        ),
    }


def _case_600(trace: Path) -> dict[str, Any]:
    reset_dual_lane_for_tests()
    dual = V1RLiveDualLane(trace_dir=trace)
    t0 = _t0(9, 15)
    fill_px = 1000.0
    admit = dual.try_admit_fill(
        symbol="6098.T",
        fill_price=fill_px,
        fill_time=t0,
        payload=_payload(t0, bid=999.0, ask=1001.0, bq=200.0, aq=200.0),
        source="v1r_native",
    )
    # Mild path: no MFE>=60 with imb>=0.1 → no extend; exit at >=600.
    series: list[tuple[float, float, float, float, float]] = []
    for s in range(0, 620, 5):
        # small drift only (~10bps), balanced book
        bid = fill_px * (1.0 + 0.001 * min(s, 100) / 100.0)
        series.append((float(s), bid, bid + 1.0, 200.0, 200.0))
    exits = _feed(dual, symbol_tick="6098", t0=t0, series=series)
    prim = [e for e in exits if e.get("lane") == "primary"]
    ctrl = [e for e in exits if e.get("lane") == "control"]
    return {
        "case": "B_CONT_EXIT_600",
        "admit": admit,
        "primary_exit": prim[-1] if prim else None,
        "control_exit": ctrl[-1] if ctrl else None,
        "primary_open": dual.open_n("primary"),
        "control_open": dual.open_n("control"),
        "exit_600": dual.stats.exit_600,
        "extend_750": dual.stats.extend_750,
        "events": [r.get("event") for r in dual.traces],
        "pass": bool(
            prim
            and not prim[-1].get("triggered_guard")
            and not prim[-1].get("extended")
            and ctrl
            and dual.open_n("primary") == 0
            and dual.open_n("control") == 0
            and float(prim[-1].get("exit_off") or 0) >= 595.0
            and float(ctrl[-1].get("exit_off") or 0) >= 595.0
        ),
    }


def _case_750(trace: Path) -> dict[str, Any]:
    reset_dual_lane_for_tests()
    dual = V1RLiveDualLane(trace_dir=trace)
    t0 = _t0(9, 25)
    fill_px = 1000.0
    admit = dual.try_admit_fill(
        symbol="6098",
        fill_price=fill_px,
        fill_time=t0,
        payload=_payload(t0, bid=999.0, ask=1001.0, bq=300.0, aq=100.0),
        source="v1r_native",
    )
    series: list[tuple[float, float, float, float, float]] = []
    for s in range(0, 770, 5):
        # Strong MFE and positive imbalance through 600 → extend to 750
        ret = min(80.0, 10.0 + s * 0.12)  # bps
        bid = fill_px * (1.0 + ret / 10000.0)
        series.append((float(s), bid, bid + 1.0, 400.0, 100.0))  # imb > 0.1
    exits = _feed(dual, symbol_tick="6098.T", t0=t0, series=series)
    prim = [e for e in exits if e.get("lane") == "primary"]
    ctrl = [e for e in exits if e.get("lane") == "control"]
    return {
        "case": "C_CONT_EXTEND_750",
        "admit": admit,
        "primary_exit": prim[-1] if prim else None,
        "control_exit": ctrl[-1] if ctrl else None,
        "primary_open": dual.open_n("primary"),
        "control_open": dual.open_n("control"),
        "extend_750": dual.stats.extend_750,
        "events": [r.get("event") for r in dual.traces],
        "pass": bool(
            prim
            and prim[-1].get("extended")
            and float(prim[-1].get("exit_off") or 0) >= 745.0
            and ctrl
            and float(ctrl[-1].get("exit_off") or 0) >= 595.0
            and dual.open_n("primary") == 0
            and dual.open_n("control") == 0
        ),
    }


def _case_pilot_hook_path(trace: Path) -> dict[str, Any]:
    """Simulate pilot_runner dual tick after native admit (canonical + .T)."""
    reset_dual_lane_for_tests()
    reset_native_entry_for_tests()
    dual = ensure_dual_lane(trace_dir=trace)
    assert dual is not None
    t0 = _t0(9, 40)
    # Native-like bare admit + fill snapshot
    snap = _payload(t0, bid=16460.0, ask=16470.0, bq=400.0, aq=200.0)
    dual.try_admit_fill(
        symbol="6098",
        fill_price=16460.0,
        fill_time=t0,
        payload=snap,
        source="v1r_native",
    )
    # Pilot Stage0 symbol form
    dual.on_tick(
        symbol="6098.T",
        payload=_payload(t0 + 1.0, bid=16460.0, ask=16470.0, bq=400.0, aq=200.0),
        event_t=t0 + 1.0,
        push_sequence=42,
    )
    hit = dual.stats.tick_matches >= 2
    return {
        "case": "PILOT_HOOK_SYMBOL_PATH",
        "canonical": canonical_symbol_key("6098.T"),
        "tick_matches": dual.stats.tick_matches,
        "primary_board_n": len(dual.primary["6098"].t),
        "trace_exists": (trace / "v1r_dual_lane_trace.jsonl").is_file(),
        "pass": bool(hit and (trace / "v1r_dual_lane_trace.jsonl").is_file()),
    }


def _parity_canonical_no_strategy_change() -> dict[str, Any]:
    """Frozen identity still pinned; canonicalization is wiring-only."""
    from small_paper.v1r_exit_v2_contract import EXIT_V2_CANDIDATE_SHA, FROZEN_GUARD, FROZEN_CONTINUATION
    from small_paper.v1r_exit_v2_activation_gate import STRATEGY_SHA, GUARD_ID, CONTINUATION_ID

    return {
        "case": "HISTORICAL_PARITY_PINS",
        "exit_candidate_sha": EXIT_V2_CANDIDATE_SHA,
        "strategy_sha": STRATEGY_SHA,
        "guard_id": GUARD_ID,
        "continuation_id": CONTINUATION_ID,
        "frozen_guard": FROZEN_GUARD,
        "frozen_continuation": FROZEN_CONTINUATION,
        "pass": (
            EXIT_V2_CANDIDATE_SHA
            == "6cc3b8aade76e323682ec39dfd06878aab0ff1a99dd42922744b0054a7ea3255"
            and GUARD_ID == "IMB_p5_t-10"
            and CONTINUATION_ID == "MFE60_IMB10"
        ),
    }


def main() -> int:
    results = []
    results.append(_case_pilot_hook_path(OUT / "pilot_hook"))
    results.append(_case_guard(OUT / "case_a_guard"))
    results.append(_case_600(OUT / "case_b_600"))
    results.append(_case_750(OUT / "case_c_750"))
    results.append(_parity_canonical_no_strategy_change())

    # smoke: native boot still loads model (fail-closed path intact)
    reset_native_entry_for_tests()
    eng = boot_v1r_native_entry(universe=["6098"], trace_dir=OUT / "native_boot")
    results.append(
        {
            "case": "NATIVE_BOOT",
            "ready": eng.ready,
            "fail_reason": eng.fail_reason,
            "pass": bool(eng.ready),
        }
    )

    ok = all(bool(r.get("pass")) for r in results)
    gate = {
        "gate": "V1R_FULL_STRATEGY_LIVE_ENTRY_EXIT_RUNTIME_READY"
        if ok
        else "V1R_LIVE_ENTRY_EXIT_RUNTIME_GATE_FAIL",
        "ok": ok,
        "results": results,
        "submit_cancel_live": "0/0/0",
        "prospective": False,
        "day_20260812": "INVALID_OPERATIONAL_VALIDATION_ONLY",
    }
    (OUT / "GATE_RESULT.json").write_text(
        json.dumps(gate, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    print(json.dumps({"ok": ok, "gate": gate["gate"]}, ensure_ascii=False))
    for r in results:
        print(f"  {r['case']}: {'PASS' if r.get('pass') else 'FAIL'}")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
