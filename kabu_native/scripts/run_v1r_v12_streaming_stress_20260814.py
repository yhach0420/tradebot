#!/usr/bin/env python
"""V12 consumer-stack streaming + stress on 2026-08-14 Capture 09:14–09:16:30.

Does not start live Paper. Strategy results are not scored.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

for _k in (
    "KABU_V1R_ENTRY_WEBHOOK_URL",
    "KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL",
    "KABU_SMALL_PAPER_CAP_BLOCKED_WEBHOOK_URL",
    "KABU_DISCORD_RESEARCH_WEBHOOK_URL",
    "KABU_SHADOW_DISCORD_WEBHOOK_URL",
):
    os.environ.pop(_k, None)
os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"

from small_paper.consumer_push_telemetry import ConsumerPushTelemetry
from small_paper.evaluation_reachability import EvaluationReachabilityTracker
from small_paper.live_writer import LiveSessionWriter
from small_paper.v1r_native_entry_live import (
    board_event_epoch_from_payload,
    boot_v1r_native_entry,
    reset_native_entry_for_tests,
)
from small_paper.v1r_live_dual_lane import reset_dual_lane_for_tests

JST = ZoneInfo("Asia/Tokyo")
DAY = "20260814"
CAPTURE = (
    ROOT
    / "data"
    / "market_capture"
    / DAY
    / "session_ing_20260814_7552_1786662464_40d6e13a"
)
FROZEN = ROOT / "runtime" / "same_day_am_frozen_universe_20260814.json"
OUT = ROOT / "results" / "research" / "v1r_v12_streaming_stress_20260814"
START = datetime(2026, 8, 14, 9, 14, 0, tzinfo=JST)
END = datetime(2026, 8, 14, 9, 16, 30, tzinfo=JST)
POLL = 5.0


def _load_universe() -> list[str]:
    body = json.loads(FROZEN.read_text(encoding="utf-8"))
    return [str(s).replace(".T", "") for s in (body.get("canonical_symbols") or [])]


def _iter_window() -> list[tuple[str, dict[str, Any], int, float]]:
    start_ts = START.timestamp()
    end_ts = END.timestamp()
    out: list[tuple[str, dict[str, Any], int, float]] = []
    for part in sorted(CAPTURE.glob("push_part_*.jsonl")):
        with part.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if rec.get("kind") not in (None, "market_push"):
                    continue
                recv = str(rec.get("received_at") or "")
                et = board_event_epoch_from_payload({"received_at": recv}) if recv else 0.0
                if et < start_ts:
                    continue
                if et > end_ts:
                    return out
                seq = int(rec.get("sequence") or 0)
                sym = str(rec.get("symbol") or "").replace(".T", "")
                pay = dict(rec.get("payload") or rec.get("original_payload") or {})
                pay["received_at"] = recv
                pay["recorded_at"] = recv
                pay["sequence"] = seq
                pay["__ingress_sequence__"] = seq
                pay["__ingress_received_at__"] = recv
                out.append((sym, pay, seq, float(et)))
    return out


def _v11_should_evaluate(tr: EvaluationReachabilityTracker, symbol: str, *, now_mono: float, market_ts: float) -> bool:
    st = tr.get(symbol)
    force = bool(st.pending_ready_eval or st.pending_recovery_eval)
    if not force and st.last_eval_market_ts is not None:
        if (market_ts - st.last_eval_market_ts) < POLL:
            return False
    elif not force and st.last_eval_mono is not None:
        if (now_mono - st.last_eval_mono) < POLL:
            return False
    return True


def _run_stack(
    events: list[tuple[str, dict[str, Any], int, float]],
    *,
    mode: str,
    speed: float,
    out_dir: Path,
    v11_force: bool,
    realtime: bool,
) -> dict[str, Any]:
    reset_native_entry_for_tests()
    reset_dual_lane_for_tests()
    uni = _load_universe()
    eng = boot_v1r_native_entry(universe=uni, trace_dir=None, universe_source="FROZEN_AM_20260814")
    eng.notify_enabled = False
    eng.ready = True
    tr = EvaluationReachabilityTracker()
    writer = LiveSessionWriter(out_dir, incremental=True, event_fields=["event_type", "symbol"], async_io=True)
    tel = ConsumerPushTelemetry()
    ack = 0
    evals = 0
    native = 0
    max_lag_sec = 0.0
    t0 = time.perf_counter()
    first_et = events[0][3] if events else 0.0
    last_et = first_et
    for i, (sym, pay, seq, et) in enumerate(events):
        last_et = et
        if realtime and speed > 0:
            due = t0 + (et - first_et) / float(speed)
            now = time.perf_counter()
            if due > now:
                time.sleep(due - now)
            lag = time.perf_counter() - due
            if lag > max_lag_sec:
                max_lag_sec = lag
        tel.begin_push()
        t_n = time.perf_counter()
        ing = eng.process_market_push(symbol=sym, payload=pay, event_t=et)
        tel.record_sec("native_ingest_us", time.perf_counter() - t_n)
        if ing.get("ingested"):
            native += 1
        ev_dt = datetime.fromtimestamp(et, JST)
        wall = datetime.now(JST) if realtime else ev_dt
        tr.note_consumer_delay(event_time=ev_dt, wall_now=wall)
        tr.update_from_payload(sym, pay, reference_now=ev_dt, feature_complete=True, history_ticks=10)
        t_s = time.perf_counter()
        if v11_force:
            do_eval = _v11_should_evaluate(tr, sym, now_mono=float(i), market_ts=et)
            cycle = f"{sym}:{i}" if do_eval else None
            tr.push_count += 1
            if do_eval:
                tr.pbv2_eval_count += 1
            else:
                tr.pbv2_throttled_count += 1
        else:
            do_eval, _skip, cycle = tr.should_evaluate(
                sym, now_mono=float(i), market_ts=et, poll_interval_sec=POLL, ring_only_warmup=False
            )
        tel.record_sec("pbv2_schedule_us", time.perf_counter() - t_s)
        if do_eval:
            evals += 1
            t_e = time.perf_counter()
            writer.append_event({"event_type": "candidate", "symbol": sym, "seq": seq})
            writer.append_event({"event_type": "rejected", "symbol": sym, "seq": seq})
            tel.record_sec("audit_enqueue_us", time.perf_counter() - t_e)
            tel.record_sec("pbv2_eval_us", time.perf_counter() - t_e)
            tr.mark_evaluated(
                sym,
                now_mono=float(i),
                market_ts=et,
                cycle_id=str(cycle or f"{sym}:{i}"),
                fresh_ok=not v11_force,
                stale_reject=bool(v11_force),
            )
        ack += 1
        tel.record_us("ack_us", 1.0)
    wall = time.perf_counter() - t0
    writer.flush(timeout=5.0)
    writer.close()
    span = max(1e-9, last_et - first_et)
    input_eps = len(events) / span
    proc_eps = ack / max(1e-9, wall)
    frac = (evals / len(events)) if events else 0.0
    end_lag = 0.0 if not realtime else max(0.0, (time.perf_counter() - t0) - span / max(speed, 1e-9))
    return {
        "mode": mode,
        "v11_force": v11_force,
        "speed": speed,
        "realtime": realtime,
        "n_events": len(events),
        "native_ingest": native,
        "ack": ack,
        "pbv2_eval_count": evals,
        "eval_fraction": round(frac, 6),
        "raw_native_difference": int(len(events) - native) if not v11_force else int(len(events) - native),
        "input_events_per_sec": round(input_eps, 3),
        "processed_events_per_sec": round(proc_eps, 3),
        "ack_events_per_sec": round(proc_eps, 3),
        "max_lag_sec": round(max_lag_sec, 6),
        "end_lag_sec": round(end_lag, 6),
        "wall_sec": round(wall, 3),
        "forced_eval_count": int(tr.forced_eval_count),
        "recovery_eval_count": int(tr.recovery_eval_count),
        "stage_us": tel.summary(),
        "writer_dropped": writer.dropped_count(),
    }


def _burst(events: list[tuple[str, dict[str, Any], int, float]], rate: float) -> dict[str, Any]:
    """Replay a 2s slice as a 372.7/s burst (or given rate)."""
    if not events:
        return {"n": 0}
    mid = events[len(events) // 2][3]
    slice_e = [e for e in events if mid <= e[3] < mid + 2.0]
    if len(slice_e) < 10:
        slice_e = events[: min(800, len(events))]
    out_dir = OUT / "burst"
    out_dir.mkdir(parents=True, exist_ok=True)
    r = _run_stack(slice_e, mode="burst", speed=rate, out_dir=out_dir, v11_force=False, realtime=False)
    r["target_burst_rate"] = rate
    r["death_spiral"] = r["eval_fraction"] >= 0.5
    return r


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    print("loading capture window...", flush=True)
    events = _iter_window()
    print(f"events={len(events)}", flush=True)
    if len(events) < 100:
        raise SystemExit(f"capture window too small: {len(events)}")

    v11_dir = OUT / "v11_sim"
    v12_dir = OUT / "v12_fast"
    v11_dir.mkdir(parents=True, exist_ok=True)
    v12_dir.mkdir(parents=True, exist_ok=True)
    print("V11-sim fast...", flush=True)
    v11 = _run_stack(events, mode="v11_sim", speed=0.0, out_dir=v11_dir, v11_force=True, realtime=False)
    print("V12 fast...", flush=True)
    v12 = _run_stack(events, mode="v12_fast", speed=0.0, out_dir=v12_dir, v11_force=False, realtime=False)

    stresses = []
    for spd, name in ((1.0, "1.0x"), (1.25, "1.25x"), (1.5, "1.5x")):
        print(f"stress {name}...", flush=True)
        d = OUT / f"stress_{name.replace('.', 'p')}"
        d.mkdir(parents=True, exist_ok=True)
        stresses.append(
            _run_stack(events, mode=name, speed=spd, out_dir=d, v11_force=False, realtime=True)
        )

    burst = _burst(events, 372.7)
    s1 = next(s for s in stresses if s["mode"] == "1.0x")
    s125 = next(s for s in stresses if s["mode"] == "1.25x")
    s15 = next(s for s in stresses if s["mode"] == "1.5x")
    ok = (
        v12["eval_fraction"] < 0.20
        and v12["forced_eval_count"] == 0
        and v12["raw_native_difference"] == 0
        and s1["end_lag_sec"] <= 0.5
        and s125["eval_fraction"] < 0.25
        and (not burst.get("death_spiral"))
    )
    report = {
        "verdict": "V1R_V12_STREAMING_STRESS_PASS" if ok else "V1R_V12_STREAMING_STRESS_FAIL",
        "window": {"start": START.isoformat(), "end": END.isoformat(), "n": len(events)},
        "v11_sim": v11,
        "v12_fast": v12,
        "stress": stresses,
        "burst_372p7": burst,
        "notes": {
            "v11_eval_frac_live_rca": 0.994,
            "strategy_scored": False,
            "paper_started": False,
        },
    }
    (OUT / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"verdict": report["verdict"], "v11_frac": v11["eval_fraction"], "v12_frac": v12["eval_fraction"]}, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
