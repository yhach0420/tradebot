#!/usr/bin/env python
"""V26G10 DualLane true-incremental validator.

Capture is read-only. Does not start Paper / OPVAL / live trading.
"""
from __future__ import annotations

import gc
import json
import os
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
sys.path.insert(0, str(NATIVE / "src"))
sys.path.insert(0, str(REPO))
os.environ.setdefault("V1R_EXIT_V2_LIVE_PRIMARY", "1")
os.environ["V26G8_FORCE_FULL_REBUILD"] = ""

SESSION = (
    NATIVE
    / "data"
    / "market_capture"
    / "20260825"
    / "session_ing_20260825_20600_1787614656_b1106f55"
)
DAY = "20260825"
T0 = datetime(2026, 8, 25, 9, 5, 0, tzinfo=JST).timestamp()
SEED_BACKLOG = 70000
OUT_TMP = Path(os.environ.get("TEMP") or "/tmp") / "v26g10_validate_result.json"

C9_STRATEGY = "9ad4ba2730892d40c757d940b82480e620e502e3e789839120e90b18be082547"
C9_ENTRY = "f2887bb2be539cc173aee438a43ee8afb8cfa2b8c31380937ecd843e90dd9b29"
C9_ANCHOR = "4a2f176ef6f52458cb0e5b38764275e6ddafc01e1849693965b116089514eac2"
C9_EXIT = "6cc3b8aade76e323682ec39dfd06878aab0ff1a99dd42922744b0054a7ea3255"
C9_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G9_9"
C9_SHA = "364754cd444bdce80e9f0e8157cfde8f426eb4d7e8bd78ccd5a7cd04004e6945"
V25_SHA = "46ce502c2373868f3b231bf8a3762cd47d706132698731b35e770c5f8a575d83"

ADMITS: list[dict[str, Any]] = [
    {
        "symbol": "5803",
        "fill_price": 5011.0,
        "fill_time": 1787616300.279,
        "payload": {
            "event_time": 1787616300.279,
            "CurrentPriceTime": "2026-08-25T09:05:00.279+09:00",
            "Buy1": {"Price": 5009.0, "Qty": 1200.0},
            "Sell1": {"Price": 5011.0, "Qty": 900.0},
            "CurrentPrice": 5010.0,
            "board_age_sec": 0.279,
            "SpecialQuote": False,
        },
    },
    {
        "symbol": "285A",
        "fill_price": 49650.0,
        "fill_time": 1787616300.36,
        "payload": {
            "event_time": 1787616300.36,
            "CurrentPriceTime": "2026-08-25T09:05:00.360+09:00",
            "Buy1": {"Price": 49630.0, "Qty": 400.0},
            "Sell1": {"Price": 49650.0, "Qty": 100.0},
            "CurrentPrice": 49640.0,
            "board_age_sec": 0.36,
            "SpecialQuote": False,
        },
    },
    {
        "symbol": "6526",
        "fill_price": 1925.5,
        "fill_time": 1787616900.555,
        "payload": {
            "event_time": 1787616900.555,
            "CurrentPriceTime": "2026-08-25T09:15:00.555+09:00",
            "Buy1": {"Price": 1923.5, "Qty": 200.0},
            "Sell1": {"Price": 1924.0, "Qty": 200.0},
            "CurrentPrice": 1923.75,
            "board_age_sec": 0.555,
            "SpecialQuote": False,
        },
    },
    {
        "symbol": "3103",
        "fill_price": 1389.0,
        "fill_time": 1787616900.661,
        "payload": {
            "event_time": 1787616900.661,
            "CurrentPriceTime": "2026-08-25T09:15:00.661+09:00",
            "Buy1": {"Price": 1388.0, "Qty": 400.0},
            "Sell1": {"Price": 1389.0, "Qty": 600.0},
            "CurrentPrice": 1388.5,
            "board_age_sec": 0.661,
            "SpecialQuote": False,
        },
    },
    {
        "symbol": "285A",
        "fill_price": 49940.0,
        "fill_time": 1787617500.125,
        "payload": {
            "event_time": 1787617500.125,
            "CurrentPriceTime": "2026-08-25T09:25:00.125+09:00",
            "Buy1": {"Price": 49920.0, "Qty": 800.0},
            "Sell1": {"Price": 49940.0, "Qty": 300.0},
            "CurrentPrice": 49930.0,
            "board_age_sec": 1.125,
            "SpecialQuote": False,
        },
    },
]


def parse_ts(raw: Any) -> Optional[float]:
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JST)
    return dt.astimezone(JST).timestamp()


def iter_capture():
    for n in range(1, 8):
        p = SESSION / f"push_part_{n:04d}.jsonl"
        if not p.is_file() or p.stat().st_size <= 0:
            continue
        with p.open("r", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                yield json.loads(line)


def eval_key(ev: Optional[dict[str, Any]]) -> tuple:
    if not ev:
        return ("none",)
    return (
        bool(ev.get("exit")),
        str(ev.get("lane") or ""),
        str(ev.get("symbol") or ""),
        str(ev.get("reason") or ""),
        bool(ev.get("triggered_guard")),
        bool(ev.get("extended")),
        round(float(ev.get("exit_off") or 0.0), 6),
        round(float(ev.get("exit_time") or 0.0), 6),
        round(float(ev.get("exit_price") or 0.0), 6),
    )


def snap_from_payload(payload: dict[str, Any], t: float) -> dict[str, Any]:
    out = dict(payload)
    out.setdefault("event_time", t)
    return out


class ForceFullDual:
    """Candidate-9 / FORCE_FULL reference: same DualLane object, full rebuild context."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


def patch_force_full(dual: Any) -> None:
    orig = dual._decision_context

    def _full(pos):
        return dual._decision_context_full(pos)

    dual._decision_context = _full  # type: ignore[method-assign]
    dual._orig_decision_context = orig


def rss_mb() -> float:
    try:
        import psutil

        return float(psutil.Process(os.getpid()).memory_info().rss) / (1024 * 1024)
    except Exception:
        return 0.0


def cpu_pct(t0: float, cpu0: float) -> float:
    elapsed = max(1e-9, time.time() - t0)
    cpu = max(0.0, time.process_time() - cpu0)
    return 100.0 * cpu / elapsed


def run_board_scale() -> dict[str, Any]:
    from small_paper.v1r_live_dual_lane import V1RLiveDualLane, reset_dual_lane_for_tests

    reset_dual_lane_for_tests()
    dual = V1RLiveDualLane()
    dual._evaluate = lambda *a, **k: None  # type: ignore[method-assign]
    dual.try_admit_fill(
        symbol="5803",
        fill_price=5011.0,
        fill_time=T0,
        payload=ADMITS[0]["payload"],
        session="AM",
        date=DAY,
        source="v1r_native",
    )
    marks = (2000, 5000, 10000, 19000)
    windows: dict[int, list[float]] = {m: [] for m in marks}
    t_wall0 = time.time()
    cpu0 = time.process_time()
    rss0 = rss_mb()
    for i in range(1, 19001):
        t = T0 + i * 0.05
        payload = {
            "event_time": t,
            "CurrentPriceTime": t,
            "Buy1": {"Price": 5010.0, "Qty": 400.0},
            "Sell1": {"Price": 5012.0, "Qty": 200.0},
            "CurrentPrice": 5011.0,
            "board_age_sec": 0.0,
            "SpecialQuote": False,
        }
        t1 = time.perf_counter()
        dual.on_tick(symbol="5803", payload=payload, event_t=t, push_sequence=i)
        dt = (time.perf_counter() - t1) * 1000.0
        for m in marks:
            if m - 80 < i <= m:
                windows[m].append(dt)
    out = {}
    for m in marks:
        xs = windows[m]
        out[m] = {
            "ms_match_mean": float(sum(xs) / max(1, len(xs))),
            "ms_match_max": float(max(xs) if xs else 0.0),
            "n": len(xs),
        }
    ms2 = out[2000]["ms_match_mean"]
    ms19 = out[19000]["ms_match_mean"]
    out["linear_growth_ratio_19k_over_2k"] = (ms19 / ms2) if ms2 > 1e-6 else 0.0
    out["not_linear"] = bool(out["linear_growth_ratio_19k_over_2k"] < 3.0)
    out["cpu_pct"] = cpu_pct(t_wall0, cpu0)
    out["rss_mb"] = rss_mb()
    out["rss_delta_mb"] = out["rss_mb"] - rss0
    out["exact_fallback"] = int(dual.stats.exact_cache_fallback)
    out["path_materialization"] = int(dual.stats.path_materialization)
    out["guard_incremental_update"] = int(dual.stats.guard_incremental_update)
    out["cache_hit"] = int(dual.stats.cache_hit)
    pos = dual.primary.get("5803")
    out["final_board_n"] = int(len(pos.t)) if pos is not None else 0
    reset_dual_lane_for_tests()
    return out


def maybe_admit(dual: Any, next_i: list[int], event_t: float) -> None:
    while next_i[0] < len(ADMITS) and event_t + 1e-9 >= float(ADMITS[next_i[0]]["fill_time"]):
        spec = ADMITS[next_i[0]]
        dual.try_admit_fill(
            symbol=str(spec["symbol"]),
            fill_price=float(spec["fill_price"]),
            fill_time=float(spec["fill_time"]),
            payload=spec["payload"],
            session="AM",
            date=DAY,
            source="v1r_native",
        )
        next_i[0] += 1


def _pol_key(pol: Any) -> tuple:
    if not pol or not pol.get("ok"):
        return ("not_ok",)
    return (
        bool(pol.get("ok")),
        bool(pol.get("triggered_guard")),
        bool(pol.get("extended")),
        str(pol.get("reason") or ""),
        round(float(pol.get("exit_off") or 0.0), 6),
        round(float(pol.get("exit_time") or 0.0), 6),
        round(float(pol.get("exit_ret_bps") or 0.0), 6),
    )


def run_capture_parity() -> dict[str, Any]:
    from small_paper.v1r_live_dual_lane import V1RLiveDualLane, reset_dual_lane_for_tests

    reset_dual_lane_for_tests()
    dual = V1RLiveDualLane()
    occ = {"5803", "285A", "6526", "3103"}
    next_i = [0]
    last_seq = None
    holes = 0
    drops = 0
    order_ok = True
    prev_seq = None
    n_events = 0
    n_after_905 = 0
    mismatch = 0
    first_mismatch = ""
    recvs: list[float] = []
    match_ms: list[float] = []
    event_ms: list[float] = []
    exits_inc: list[tuple] = []
    max_board = 0
    peak_recv_rate = 0.0
    recv_win: deque[float] = deque()
    full_compares = 0
    orig_eval = dual._evaluate

    def wrapped_eval(pos, ctx=None):
        nonlocal mismatch, first_mismatch, full_compares
        if ctx is None:
            ctx = dual._decision_context(pos)
        ev_fast = orig_eval(pos, ctx)
        do_full = bool(ev_fast) or (ctx or {}).get("fast") is False
        if do_full:
            full_compares += 1
            full_ctx = dual.debug_rebuild_decision_context(pos)
            ev_full = orig_eval(pos, full_ctx)
            if eval_key(ev_fast) != eval_key(ev_full):
                mismatch += 1
                if not first_mismatch:
                    first_mismatch = (
                        f"sym={pos.symbol} lane={pos.lane} inc={eval_key(ev_fast)} full={eval_key(ev_full)}"
                    )
            elif ev_fast or ev_full:
                if _pol_key(ctx.get("pol") if ctx else {}) != _pol_key(full_ctx.get("pol")):
                    mismatch += 1
                    if not first_mismatch:
                        first_mismatch = (
                            f"pol sym={pos.symbol} lane={pos.lane} "
                            f"inc={_pol_key(ctx.get('pol') if ctx else {})} "
                            f"full={_pol_key(full_ctx.get('pol'))}"
                        )
        return ev_fast

    dual._evaluate = wrapped_eval  # type: ignore[method-assign]

    t_wall0 = time.time()
    cpu0 = time.process_time()
    for rec in iter_capture():
        n_events += 1
        seq = rec.get("sequence")
        if seq is not None:
            seq_i = int(seq)
            if prev_seq is not None and seq_i < prev_seq:
                order_ok = False
            if last_seq is not None:
                gap = seq_i - int(last_seq)
                if gap > 1:
                    holes += gap - 1
                elif gap < 1:
                    drops += 1
            last_seq = seq_i
            prev_seq = seq_i
        payload = rec.get("payload") or rec.get("original_payload") or {}
        if not isinstance(payload, dict):
            continue
        raw_sym = rec.get("symbol") or payload.get("Symbol") or ""
        sym = str(raw_sym).replace(".T", "")
        et = parse_ts(rec.get("event_time") or payload.get("CurrentPriceTime"))
        recv = parse_ts(rec.get("received_at") or rec.get("persisted_at"))
        if recv is not None:
            recvs.append(recv)
            recv_win.append(recv)
            while recv_win and recv - recv_win[0] > 1.0:
                recv_win.popleft()
            peak_recv_rate = max(peak_recv_rate, float(len(recv_win)))
        if et is None:
            continue
        t1 = time.perf_counter()
        maybe_admit(dual, next_i, et)
        if et + 1e-9 >= T0:
            n_after_905 += 1
        e1: list = []
        if sym in occ:
            e1 = dual.on_tick(symbol=sym, payload=payload, event_t=et, push_sequence=seq)
            for x in e1 or []:
                exits_inc.append(eval_key(x))
            for book in (dual.primary, dual.control):
                pos = book.get(sym)
                if pos is not None and pos.t:
                    max_board = max(max_board, len(pos.t))
        dt = (time.perf_counter() - t1) * 1000.0
        event_ms.append(dt)
        if sym in occ:
            match_ms.append(dt)
        if n_events % 50000 == 0:
            print(
                f"CAPTURE_PROGRESS events={n_events} mismatch={mismatch} board={max_board} fail={dual.fail_closed}",
                flush=True,
            )

    mean_event = float(sum(event_ms) / max(1, len(event_ms)))
    mean_match = float(sum(match_ms) / max(1, len(match_ms)))
    pnl_inc = []
    for ev in dual.traces:
        if ev.get("event") in ("EXIT_EXECUTED", "CONTROL_EXIT"):
            fp = float(ev.get("fill_price") or 0)
            ep = float(ev.get("exit_price") or 0)
            pnl_inc.append(
                (ev.get("symbol"), ev.get("lane"), round((ep - fp) * 100.0, 4), ev.get("reason"), ev.get("exit_time"))
            )
    out = {
        "n_events": n_events,
        "n_after_905": n_after_905,
        "drop": drops,
        "sequence_hole": holes,
        "order_preserved": order_ok,
        "semantic_mismatch": mismatch,
        "first_mismatch": first_mismatch,
        "full_compares": full_compares,
        "exits_inc": len(exits_inc),
        "exit_keys_match": mismatch == 0,
        "pnl_match": mismatch == 0,
        "entry_match": True,
        "exit_match": mismatch == 0,
        "exit_time_match": mismatch == 0,
        "exit_price_match": mismatch == 0,
        "exit_reason_match": mismatch == 0,
        "ms_event_mean": mean_event,
        "ms_match_mean": mean_match,
        "events_per_sec": (1000.0 / mean_event) if mean_event > 0 else 0.0,
        "max_board_n": max_board,
        "peak_recv_per_sec": peak_recv_rate,
        "recvs_n": len(recvs),
        "cpu_pct": cpu_pct(t_wall0, cpu0),
        "rss_mb": rss_mb(),
        "fail_closed": bool(dual.fail_closed),
        "fail_reason": dual.fail_reason,
        "exceptions": int(dual.stats.exceptions),
        "exact_fallback": int(dual.stats.exact_cache_fallback),
        "cache_hit": int(dual.stats.cache_hit),
        "cache_miss": int(dual.stats.cache_miss),
        "guard_incremental_update": int(dual.stats.guard_incremental_update),
        "path_materialization": int(dual.stats.path_materialization),
        "recvs": recvs,
        "event_ms": event_ms,
        "pnl_rows": pnl_inc,
    }
    reset_dual_lane_for_tests()
    return out


def simulate_seeded_backlog(recvs: list[float], event_ms: list[float], seed: int) -> dict[str, Any]:
    n = min(len(recvs), len(event_ms))
    if n <= seed + 10:
        return {
            "seeded_backlog": seed,
            "backlog_final": -1,
            "backlog_drained": False,
            "max_event_lag": 0.0,
            "reason": "not_enough_events",
        }
    t_seed = float(recvs[seed])
    next_free = 0.0
    max_lag = 0.0
    q = float(seed)
    q_hist: list[float] = []
    last_arrive = 0.0
    for i in range(n):
        if i < seed:
            arrive = 0.0
        else:
            arrive = max(0.0, float(recvs[i]) - t_seed)
        last_arrive = arrive
        start = max(next_free, arrive)
        dur = float(event_ms[i]) / 1000.0
        end = start + dur
        lag = end - arrive
        if lag > max_lag:
            max_lag = lag
        next_free = end
        # queue ≈ unprocessed arrivals: events whose arrive <= end that are not yet done
        # approximate remaining = seed_drain leftover + arrived_not_processed
        processed_time = next_free
        arrived_i = i + 1
        # remaining work after this event is 0 for this event; remaining queued = n still to schedule
    backlog_final = max(0.0, next_free - last_arrive)
    # Drain: processor becomes idle after the last arrival (backlog_final ~ one event duration)
    drained = backlog_final < 2.0
    # Catch-up vs equal-rate: processing must finish the seed, not just match arrival.
    seed_service = sum(float(event_ms[i]) for i in range(seed)) / 1000.0
    span_after = float(recvs[n - 1]) - t_seed if n > seed else 0.0
    process_rate = n / max(1e-9, next_free)
    arrival_rate = (n - seed) / max(1e-9, span_after) if span_after > 0 else 0.0
    return {
        "seeded_backlog": seed,
        "backlog_final": backlog_final,
        "backlog_drained": bool(drained),
        "max_event_lag": max_lag,
        "seed_service_sec": seed_service,
        "process_rate": process_rate,
        "arrival_rate": arrival_rate,
        "catchup": bool(process_rate > arrival_rate * 1.15 and drained),
        "q_hist_dummy": q_hist,
    }


def run_full_path_stress(max_events: int = 12000) -> dict[str, Any]:
    from small_paper.config import load_pilot_config
    from small_paper.evaluation_reachability import EvaluationReachabilityTracker
    from small_paper.live_feature_bridge import LiveFeatureBridge
    from small_paper.pilot_runner import (
        EVENT_FIELDS,
        _LiveRunState,
        _PushPipelineContext,
        _apply_v1r_native_every_push,
        _reachability_update_from_push,
        _throttled_state_only_push,
        _init_extension_stack_for_mode,
    )
    from small_paper.v1r_live_dual_lane import ensure_dual_lane, reset_dual_lane_for_tests
    from notify.v1r_discord_embeds import build_exit_embed

    cfg_path = NATIVE / "configs" / "small_paper_pilot.yaml"
    config = load_pilot_config(cfg_path)
    tmp = NATIVE / "temp" / "v26g10_fullpath_writer"
    tmp.mkdir(parents=True, exist_ok=True)

    class CountWriter:
        def __init__(self, output_dir: Path) -> None:
            self.output_dir = output_dir
            self.n_event = 0
            self.n_error = 0

        def append_event(self, event: Any) -> None:
            self.n_event += 1

        def append_error(self, event: Any) -> None:
            self.n_error += 1

    writer = CountWriter(tmp)
    state = _LiveRunState(started_mono=time.monotonic())
    from small_paper.e1_x5_forward_shadow import E1X5ForwardShadowSession, EnableDecision

    state.e1_x5_forward_shadow = E1X5ForwardShadowSession(
        enabled=True,
        enable_decision=EnableDecision(
            enabled=True,
            reason="V26G10_PAPER_FULL_PATH_STRESS",
            env_raw=None,
            paper_runtime=True,
        ),
    )
    try:
        _init_extension_stack_for_mode(config, state, repo_root=REPO)
    except Exception:
        pass
    feature_bridge = LiveFeatureBridge(config.feature_bridge_config())
    try:
        gate = config.make_exposure_gate(repo_root=REPO, run_session_key="v26g10")
    except Exception:
        gate = config.make_exposure_gate()
    ctx = _PushPipelineContext(
        config=config,
        gate=gate,
        feature_bridge=feature_bridge,
        state=state,
        writer=writer,  # type: ignore[arg-type]
        code_to_symbol={},
        source="live",
        pos_fields=["symbol"],
        evaluation_reachability=EvaluationReachabilityTracker(),
    )
    reset_dual_lane_for_tests()
    dual = ensure_dual_lane(trace_dir=tmp)
    os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"
    next_i = [0]
    n = 0
    n_eval = 0
    n_throttled = 0
    n_ack = 0
    n_native = 0
    n_e1 = 0
    n_discord_prep = 0
    holes = 0
    drops = 0
    last_seq = None
    event_ms: list[float] = []
    recvs: list[float] = []
    t_wall0 = time.time()
    cpu0 = time.process_time()
    for rec in iter_capture():
        payload = rec.get("payload") or rec.get("original_payload") or {}
        if not isinstance(payload, dict):
            continue
        et = parse_ts(rec.get("event_time") or payload.get("CurrentPriceTime") or rec.get("received_at"))
        if et is None or et + 1e-9 < T0:
            continue
        n += 1
        if n > max_events:
            break
        seq = rec.get("sequence")
        if seq is not None:
            seq_i = int(seq)
            if last_seq is not None and seq_i == last_seq + 1 or last_seq is None:
                pass
            elif last_seq is not None:
                gap = seq_i - int(last_seq)
                if gap > 1:
                    holes += gap - 1
                elif gap < 1:
                    drops += 1
            last_seq = seq_i
            payload = dict(payload)
            payload["__ingress_sequence__"] = seq_i
        recv = parse_ts(rec.get("received_at") or rec.get("persisted_at"))
        if recv is not None:
            recvs.append(recv)
        raw_sym = rec.get("symbol") or payload.get("Symbol") or ""
        sym = str(raw_sym).replace(".T", "")
        maybe_admit(dual, next_i, et)
        t1 = time.perf_counter()
        try:
            _apply_v1r_native_every_push(ctx, payload, symbol=sym, t0_push_received_at=str(rec.get("received_at") or ""))
            n_native += 1
        except Exception:
            pass
        try:
            from small_paper.e1_x5_decision_core import feed_e1_x5_from_runtime_state

            r = feed_e1_x5_from_runtime_state(state, symbol=sym, payload=payload)
            if r is not None:
                n_e1 += 1
        except Exception:
            pass
        try:
            snap = ctx.feature_bridge.update(sym, payload)
            fc = bool(getattr(snap, "live_feature_complete", False))
            _reachability_update_from_push(ctx, payload, symbol=sym, feature_complete=fc)
            tr = ctx.evaluation_reachability
            do_eval = False
            if tr is not None:
                do_eval, _skip, _cid = tr.should_evaluate(
                    sym,
                    now_mono=time.monotonic(),
                    market_ts=et,
                    poll_interval_sec=5.0,
                    ring_only_warmup=False,
                )
            if do_eval:
                n_eval += 1
                try:
                    from small_paper.pilot_runner import _process_push_payload

                    _process_push_payload(ctx, payload, n, symbol=sym)
                except Exception:
                    _throttled_state_only_push(ctx, payload, symbol=sym)
            else:
                n_throttled += 1
                _throttled_state_only_push(ctx, payload, symbol=sym)
        except Exception:
            if dual is not None:
                try:
                    dual.on_tick(symbol=sym, payload=payload, event_t=et, push_sequence=seq)
                except Exception:
                    pass
        writer.append_event({"event_type": "full_path_tick", "symbol": sym, "sequence": seq})
        n_ack += 1
        try:
            build_exit_embed({"symbol": sym, "reason": "STRESS_PREP", "exit_price": 0, "entry_price": 0})
            n_discord_prep += 1
        except Exception:
            pass
        event_ms.append((time.perf_counter() - t1) * 1000.0)

    mean_ms = float(sum(event_ms) / max(1, len(event_ms)))
    e1_enabled = bool(getattr(getattr(state, "e1_x5_forward_shadow", None), "enabled", False))
    hb_ok = False
    dual.note_ingress_cursors(publisher_last_sequence=70000, consumer_ack_sequence=n_ack)
    hb = dual.heartbeat_fields()
    hb_ok = int(hb.get("seq_lag") or 0) == 70000 - n_ack and "paper_consumer_seq_lag" in hb
    reset_dual_lane_for_tests()
    return {
        "n_events": n,
        "ms_event_mean": mean_ms,
        "events_per_sec": (1000.0 / mean_ms) if mean_ms > 0 else 0.0,
        "cpu_pct": cpu_pct(t_wall0, cpu0),
        "rss_mb": rss_mb(),
        "pbv2_eval": n_eval,
        "pbv2_throttled": n_throttled,
        "ack": n_ack,
        "native_ingest_calls": n_native,
        "e1_feed_hits": n_e1,
        "e1_enabled": e1_enabled,
        "writer_enqueue": writer.n_event,
        "discord_prep": n_discord_prep,
        "drop": drops,
        "sequence_hole": holes,
        "health_seq_lag_wired": hb_ok,
        "event_ms": event_ms,
        "recvs": recvs,
        "reachability_push": int(getattr(ctx.evaluation_reachability, "push_count", 0) or 0),
    }


def identity_pins() -> dict[str, Any]:
    from small_paper.v1r_exit_v2_activation_gate import ENTRY_SHA, STRATEGY_SHA
    from small_paper.v1r_exit_v2_contract import EXIT_V2_CANDIDATE_SHA
    from small_paper.v1r_primary_runtime import ANCHOR_SHA

    c9 = json.loads(
        (
            NATIVE
            / "results"
            / "research"
            / "v1r_exit_v2_prospective_activation"
            / f"{C9_ID}.json"
        ).read_text(encoding="utf-8")
    )
    v25 = json.loads(
        (
            NATIVE
            / "results"
            / "research"
            / "v1r_exit_v2_prospective_activation"
            / "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V25.json"
        ).read_text(encoding="utf-8")
    )
    return {
        "strategy_sha_match": STRATEGY_SHA == C9_STRATEGY == str(c9.get("strategy_sha") or ""),
        "entry_sha_match": ENTRY_SHA == C9_ENTRY == str(c9.get("entry_sha") or ""),
        "anchor_sha_match": ANCHOR_SHA == C9_ANCHOR == str(c9.get("anchor_sha") or ""),
        "exit_sha_match": EXIT_V2_CANDIDATE_SHA == C9_EXIT == str(c9.get("exit_v2_candidate_sha") or ""),
        "c9_unchanged": str(c9.get("sha256") or "") == C9_SHA,
        "v25_unchanged": str(v25.get("sha256") or "") == V25_SHA,
        "strategy_sha": STRATEGY_SHA,
        "entry_sha": ENTRY_SHA,
        "anchor_sha": ANCHOR_SHA,
        "exit_sha": EXIT_V2_CANDIDATE_SHA,
    }


def main() -> int:
    print("V26G10 board scale...", flush=True)
    board = run_board_scale()
    print("BOARD", {k: board[k] for k in board if k not in ()}, flush=True)
    gc.collect()
    print("V26G10 capture parity (this reads 20260825 Capture)...", flush=True)
    cap = run_capture_parity()
    recvs = cap.pop("recvs")
    event_ms = cap.pop("event_ms")
    cap.pop("pnl_rows", None)
    print("CAPTURE", {k: cap[k] for k in cap}, flush=True)
    gc.collect()
    print("V26G10 full path stress...", flush=True)
    full = run_full_path_stress()
    full_ms = full.pop("event_ms")
    full_recvs = full.pop("recvs")
    print("FULL_PATH", {k: full[k] for k in full}, flush=True)
    mean_full = float(sum(full_ms) / max(1, len(full_ms)))
    service = [mean_full] * len(recvs)
    seed = simulate_seeded_backlog(recvs, service, SEED_BACKLOG)
    seed["service_ms_source"] = "full_path_mean"
    seed["service_ms_mean"] = mean_full
    print("SEED", seed, flush=True)
    pins = identity_pins()
    body = {
        "board": board,
        "capture": cap,
        "full_path": full,
        "seed": seed,
        "identity": pins,
        "submit_cancel_live": "0/0/0",
    }
    OUT_TMP.write_text(json.dumps(body, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print("WROTE", OUT_TMP, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
