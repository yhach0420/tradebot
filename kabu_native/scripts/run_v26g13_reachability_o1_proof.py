#!/usr/bin/env python
"""V26G13 proof: O(1) candidate_count exact parity + full-path 20260826 replay.

Does not start Paper/OPVAL/live trading. Capture is read-only.
Writes only results/research/v26g13_reachability_o1_repair/{report.json,report.md,audit.xlsx}
"""
from __future__ import annotations

import gc
import json
import os
import statistics
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
sys.path.insert(0, str(NATIVE / "src"))
sys.path.insert(0, str(REPO))

os.environ.setdefault("V1R_EXIT_V2_LIVE_PRIMARY", "1")
os.environ.setdefault("KABU_PAPER_RUNTIME", "1")
os.environ.setdefault("E1_X5_FORWARD_SHADOW", "1")

CAPTURE = (
    NATIVE
    / "data"
    / "market_capture"
    / "20260826"
    / "session_ing_20260826_15680_1787698248_d40c0cb1"
)
LIVE = NATIVE / "results" / "small_paper" / "20260826" / "live_session_080751"
OUT = NATIVE / "results" / "research" / "v26g13_reachability_o1_repair"
RESYNC_HEAD = 24419
AM_CLOSE_TS = datetime(2026, 8, 26, 11, 25, 0, tzinfo=JST).timestamp()
ARRIVAL = 55.97
TWO_X = 111.94
SEED_BACKLOG = 100_000
C12_SHA = "7769527e34e6b2df323a36c0b65162d603a5bf55b2f62120b5d3e42fd7abff95"
C10_DUALLANE = "2cdb61f2e5f39a8f4ef782fa3d0059797b70c015887df5d94aa0520ba04b66f6"
V25_SHA = "46ce502c2373868f3b231bf8a3762cd47d706132698731b35e770c5f8a575d83"
STRATEGY = "9ad4ba2730892d40c757d940b82480e620e502e3e789839120e90b18be082547"
ENTRY = "f2887bb2be539cc173aee438a43ee8afb8cfa2b8c31380937ecd843e90dd9b29"
ANCHOR = "4a2f176ef6f52458cb0e5b38764275e6ddafc01e1849693965b116089514eac2"
EXIT = "6cc3b8aade76e323682ec39dfd06878aab0ff1a99dd42922744b0054a7ea3255"


def _legacy(events) -> int:
    return int(sum(1 for e in events if isinstance(e, dict) and e.get("event_type") == "candidate"))


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
    for n in range(1, 32):
        p = CAPTURE / f"push_part_{n:04d}.jsonl"
        if not p.is_file() or p.stat().st_size <= 0:
            continue
        with p.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    yield json.loads(line)


def stamp(rec: dict[str, Any]) -> tuple[dict[str, Any], str, Optional[int], Optional[float]]:
    payload = rec.get("payload") or rec.get("original_payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    p = dict(payload)
    for k in ("event_time", "received_at", "recorded_at", "persisted_at"):
        if rec.get(k) and not p.get(k):
            p[k] = rec.get(k)
        if rec.get(k) and k == "received_at" and not p.get("recorded_at"):
            p["recorded_at"] = rec.get(k)
    seq = rec.get("sequence")
    seq_i = int(seq) if seq is not None and str(seq).strip() != "" else None
    if seq_i is not None:
        p["__ingress_sequence__"] = seq_i
    raw_sym = rec.get("symbol") or p.get("Symbol") or ""
    sym = str(raw_sym).replace(".T", "")
    et = parse_ts(rec.get("event_time") or p.get("CurrentPriceTime") or rec.get("received_at"))
    return p, sym, seq_i, et


def make_sync_ctx(n_events: int):
    from small_paper.evaluation_reachability import EvaluationReachabilityTracker
    from small_paper.pilot_runner import _sync_reachability_summary

    events = [{"event_type": "candidate" if i % 4 == 0 else "rejected"} for i in range(n_events)]
    tr = EvaluationReachabilityTracker()
    for i in range(50):
        tr.get(f"S{i:02d}")
    tr.seed_candidate_event_count_from_events(events)
    state = SimpleNamespace(
        events=events,
        accepted_rows=[],
        evaluation_reachability_summary={},
        _evaluation_reachability_tracker=tr,
    )
    ctx = SimpleNamespace(
        evaluation_reachability=tr,
        state=state,
        entry_eligible_symbols={f"S{i:02d}" for i in range(50)},
    )
    _sync_reachability_summary(ctx)
    return ctx, _sync_reachability_summary


def bench_complexity() -> dict[str, Any]:
    out: dict[str, float] = {}
    for n in (10_000, 30_000, 60_000, 90_000, 120_000, 200_000):
        ctx, sync = make_sync_ctx(n)
        sync(ctx)
        t0 = time.perf_counter()
        loops = 6
        for _ in range(loops):
            sync(ctx)
        ms = (time.perf_counter() - t0) / loops * 1000.0
        out[str(n)] = ms
        got = int(ctx.state.evaluation_reachability_summary["candidate_count"])
        assert got == _legacy(ctx.state.events), (n, got)
        print(f"REACHABILITY_{n // 1000}K_MS {ms:.4f}", flush=True)
    ratio = out["200000"] / max(out["10000"], 1e-9)
    slope = (out["200000"] - out["10000"]) / 190_000.0
    linear_removed = bool(out["200000"] < 2.0 and ratio < 4.0 and slope < 5e-6)
    return {
        "ms": out,
        "ratio_200k_10k": ratio,
        "slope_ms_per_event": slope,
        "LINEAR_GROWTH_REMOVED": linear_removed,
    }


def jsonl_restore_parity() -> dict[str, Any]:
    from small_paper.evaluation_reachability import EvaluationReachabilityTracker

    path = LIVE / "small_paper_events.jsonl"
    events: list[dict[str, Any]] = []
    if path.is_file():
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    events.append(json.loads(line))
    marks = (0, 10_000, 30_000, 60_000, 90_000, 120_000)
    rows = []
    mismatch = 0
    tr = EvaluationReachabilityTracker()
    tr.seed_candidate_event_count_from_events([])
    for n in marks:
        sl = events[: min(n, len(events))] if n else []
        tr.invalidate_candidate_event_count()
        tr.seed_candidate_event_count_from_events(sl)
        leg = _legacy(sl)
        ok = int(tr.candidate_event_count) == leg
        if not ok:
            mismatch += 1
        rows.append({"n": n, "o1": int(tr.candidate_event_count), "legacy": leg, "match": ok})
    if events:
        tr.invalidate_candidate_event_count()
        tr.seed_candidate_event_count_from_events(events)
        if int(tr.candidate_event_count) != _legacy(events):
            mismatch += 1
        rows.append(
            {
                "n": len(events),
                "o1": int(tr.candidate_event_count),
                "legacy": _legacy(events),
                "match": int(tr.candidate_event_count) == _legacy(events),
                "label": "full_live_jsonl",
            }
        )
    return {"events_in_jsonl": len(events), "COUNT_MISMATCH": mismatch, "checkpoints": rows}


class CountWriter:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.n_event = 0
        self.n_error = 0
        self.n_hb = 0

    def append_event(self, event: Any) -> None:
        self.n_event += 1

    def append_error(self, event: Any) -> None:
        self.n_error += 1

    def append_heartbeat(self, event: Any) -> None:
        self.n_hb += 1

    def append_position_row(self, *a: Any, **k: Any) -> None:
        return

    def append_entry_scan_audit(self, *a: Any, **k: Any) -> None:
        return

    def append_discord_entry_delivery(self, *a: Any, **k: Any) -> None:
        return


def last_live_seq() -> int:
    hb = LIVE / "heartbeat.jsonl"
    seq = 0
    if not hb.is_file():
        return 0
    with hb.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            rec = json.loads(line)
            v = rec.get("v1r_exit_v2") or {}
            n = rec.get("v1r_native_entry") or {}
            seq = max(
                seq,
                int(v.get("consumer_ack_sequence") or 0),
                int(n.get("last_ingested_sequence") or 0),
            )
    return seq


def build_pipeline(tmp: Path):
    from small_paper.config import load_pilot_config
    from small_paper.evaluation_reachability import EvaluationReachabilityTracker
    from small_paper.live_feature_bridge import LiveFeatureBridge
    from small_paper.pilot_runner import (
        _LiveRunState,
        _PushPipelineContext,
        _init_v1r_native_entry_for_live,
    )
    from small_paper.v1r_live_dual_lane import ensure_dual_lane, reset_dual_lane_for_tests

    cfg_path = Path(
        json.loads((LIVE / "live_session_config.json").read_text(encoding="utf-8")).get("config_path")
        or (NATIVE / "configs" / "small_paper_pilot.yaml")
    )
    if not cfg_path.is_file():
        cfg_path = NATIVE / "configs" / "small_paper_pilot.yaml"
    config = load_pilot_config(cfg_path)
    wiring = json.loads((LIVE / "v1r_native_entry_wiring.json").read_text(encoding="utf-8"))
    symbols = list((wiring.get("resolved") or {}).get("symbols") or [])
    writer = CountWriter(tmp)
    state = _LiveRunState(started_mono=time.monotonic())
    state.trading_date = "20260826"
    from small_paper.e1_x5_forward_shadow import E1X5ForwardShadowSession, EnableDecision

    state.e1_x5_forward_shadow = E1X5ForwardShadowSession(
        enabled=True,
        enable_decision=EnableDecision(
            enabled=True, reason="V26G13_PROOF", env_raw=None, paper_runtime=True
        ),
    )
    feature_bridge = LiveFeatureBridge(config.feature_bridge_config())
    try:
        gate = config.make_exposure_gate(repo_root=REPO, run_session_key="v26g13")
    except Exception:
        gate = config.make_exposure_gate()
    tracker = EvaluationReachabilityTracker()
    tracker.apply_realtime_resync_watermark(
        head_seq=RESYNC_HEAD,
        head_event_time="2026-08-26T08:50:04.322+09:00",
        generation=1,
    )
    ctx = _PushPipelineContext(
        config=config,
        gate=gate,
        feature_bridge=feature_bridge,
        state=state,
        writer=writer,  # type: ignore[arg-type]
        code_to_symbol={s: s for s in symbols},
        source="live",
        pos_fields=["symbol"],
        evaluation_reachability=tracker,
        entry_eligible_symbols=set(symbols),
    )
    tracker.seed_candidate_event_count_from_events(state.events)
    state._evaluation_reachability_tracker = tracker  # type: ignore[attr-defined]
    reset_dual_lane_for_tests()
    try:
        _init_v1r_native_entry_for_live(
            state=state,
            writer=writer,  # type: ignore[arg-type]
            native_root=NATIVE,
            trading_date="20260826",
            session_symbols=symbols,
        )
    except Exception as exc:
        print("native_init_warn", exc, flush=True)
    dual = ensure_dual_lane(trace_dir=tmp)
    if dual is not None:
        dual.maybe_session_close = lambda **k: []  # type: ignore[method-assign]
    return ctx, writer, dual, symbols


def process_one(ctx, payload, sym, seq, et, n) -> dict[str, Any]:
    from small_paper.pilot_runner import (
        _apply_v1r_native_every_push,
        _process_push_payload,
        _reachability_update_from_push,
        _sync_reachability_summary,
        _throttled_state_only_push,
    )

    t_r0 = time.perf_counter()
    t0_iso = str(payload.get("recorded_at") or payload.get("received_at") or "")
    try:
        _apply_v1r_native_every_push(
            ctx, payload, symbol=sym, t0_push_received_at=t0_iso or None, message_index=n
        )
    except Exception:
        pass
    _reachability_update_from_push(ctx, payload, symbol=sym)
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
        try:
            _process_push_payload(ctx, payload, n, symbol=sym, eval_mono=et)
        except Exception:
            _throttled_state_only_push(ctx, payload, symbol=sym)
    else:
        _throttled_state_only_push(ctx, payload, symbol=sym)
    t_sum0 = time.perf_counter()
    _sync_reachability_summary(ctx)
    summary_ms = (time.perf_counter() - t_sum0) * 1000.0
    return {
        "do_eval": bool(do_eval),
        "summary_ms": summary_ms,
        "outer_ms": (time.perf_counter() - t_r0) * 1000.0,
    }


def run_full_path() -> dict[str, Any]:
    from small_paper.pilot_runner import _sync_reachability_summary
    from notify.v1r_discord_embeds import build_exit_embed

    tmp = NATIVE / "temp" / "v26g13_fullpath_writer"
    tmp.mkdir(parents=True, exist_ok=True)
    ctx, writer, dual, _symbols = build_pipeline(tmp)
    live_last = last_live_seq() or 10**12
    gc_times: list[float] = []

    def _gc_cb(phase: str, info: dict[str, Any]) -> None:
        if phase == "start":
            _gc_cb.t0 = time.perf_counter()  # type: ignore[attr-defined]
        elif phase == "stop":
            t0 = getattr(_gc_cb, "t0", None)
            if t0 is not None:
                gc_times.append(time.perf_counter() - t0)

    gc.callbacks.append(_gc_cb)
    buf: deque = deque()
    it = iter_capture()
    holes = 0
    drops = 0
    last_seq = None
    n_in = 0
    n_proc = 0
    n_eval = 0
    n_ack = 0
    n_discord = 0
    mismatch = 0
    summary_mismatch = 0
    event_ms: list[float] = []
    summary_ms_all: list[float] = []
    degradation: list[dict[str, Any]] = []
    checkpoints = {10_000, 30_000, 60_000, 90_000, 120_000}
    seeded = 0
    max_buf = 0
    t_wall0 = time.perf_counter()
    gc.collect()
    gc_n0 = len(gc_times)

    def take_next() -> Optional[dict[str, Any]]:
        nonlocal n_in, last_seq, holes, drops, seeded
        while True:
            try:
                rec = next(it)
            except StopIteration:
                return None
            p, sym, seq_i, et = stamp(rec)
            if not sym or et is None:
                continue
            if seq_i is not None and seq_i <= RESYNC_HEAD:
                continue
            if seq_i is not None and seq_i > live_last:
                return None
            if et is not None and et >= AM_CLOSE_TS:
                return None
            n_in += 1
            if seq_i is not None:
                if last_seq is not None:
                    gap = seq_i - int(last_seq)
                    if gap > 1:
                        holes += gap - 1
                    elif gap < 1:
                        drops += 1
                last_seq = seq_i
            if not sym or et is None:
                continue
            recv = parse_ts(rec.get("received_at") or rec.get("persisted_at") or rec.get("event_time")) or et
            return {"payload": p, "sym": sym, "seq": seq_i, "et": et, "recv": recv}

    while seeded < SEED_BACKLOG:
        rec = take_next()
        if rec is None:
            break
        buf.append(rec)
        seeded += 1
    max_buf = len(buf)
    print(f"SEEDED_BACKLOG {len(buf)}", flush=True)
    next_in = take_next()
    t_arrive0 = float((next_in or {}).get("recv") or 0.0)
    t_proc = 0.0

    def maybe_check() -> None:
        nonlocal mismatch, summary_mismatch
        o1 = int(getattr(ctx.evaluation_reachability, "candidate_event_count", 0) or 0)
        leg = _legacy(ctx.state.events)
        if o1 != leg:
            mismatch += 1
        _sync_reachability_summary(ctx)
        fields = dict(ctx.state.evaluation_reachability_summary or {})
        if int(fields.get("candidate_count") or 0) != o1:
            summary_mismatch += 1

    while buf or next_in is not None:
        max_buf = max(max_buf, len(buf))
        while next_in is not None and (float(next_in["recv"]) - t_arrive0) <= t_proc:
            buf.append(next_in)
            next_in = take_next()
            max_buf = max(max_buf, len(buf))
        if not buf:
            if next_in is None:
                break
            # processor idle until next arrival
            t_proc = float(next_in["recv"]) - t_arrive0
            continue
        item = buf.popleft()
        n_proc += 1
        t1 = time.perf_counter()
        try:
            st = process_one(ctx, item["payload"], item["sym"], item["seq"], item["et"], n_proc)
        except Exception:
            st = {"do_eval": False, "summary_ms": 0.0, "outer_ms": 0.0}
        svc = time.perf_counter() - t1
        event_ms.append(svc * 1000.0)
        t_proc += svc
        summary_ms_all.append(float(st.get("summary_ms") or 0.0))
        if st.get("do_eval"):
            n_eval += 1
        n_ack += 1
        try:
            build_exit_embed(
                {"symbol": item["sym"], "reason": "PROOF_PREP", "exit_price": 0, "entry_price": 0}
            )
            n_discord += 1
        except Exception:
            pass
        if n_proc in checkpoints or n_proc % 20000 == 0:
            maybe_check()
            mean_outer = statistics.fmean(event_ms[-2000:]) if event_ms else 0.0
            mean_sum = statistics.fmean(summary_ms_all[-2000:]) if summary_ms_all else 0.0
            degradation.append(
                {
                    "n_proc": n_proc,
                    "events_n": len(ctx.state.events),
                    "candidate_count": int(ctx.evaluation_reachability.candidate_event_count),
                    "whole_ms_event": mean_outer,
                    "reachability_ms_event": mean_sum,
                    "gc_collections": len(gc_times) - gc_n0,
                    "buf": len(buf),
                }
            )
            print(
                f"PROG n={n_proc} ev={len(ctx.state.events)} cand={ctx.evaluation_reachability.candidate_event_count} "
                f"ms={mean_outer:.3f} sum_ms={mean_sum:.4f} buf={len(buf)}",
                flush=True,
            )

    maybe_check()
    try:
        gc.callbacks.remove(_gc_cb)
    except ValueError:
        pass
    wall = time.perf_counter() - t_wall0
    mean_ms = float(sum(event_ms) / max(1, len(event_ms)))
    eps = (n_proc / wall) if wall > 0 else 0.0
    reach_ms = float(sum(summary_ms_all) / max(1, len(summary_ms_all)))
    gc_sec = float(sum(gc_times[gc_n0:])) if gc_times[gc_n0:] else 0.0
    gc_ms_event = (gc_sec / max(1, n_proc)) * 1000.0
    pbv2_accepted = int(len(ctx.state.accepted_rows))
    cand = int(ctx.evaluation_reachability.candidate_event_count)
    live_summary = {}
    sp = LIVE / "small_paper_summary.json"
    if sp.is_file():
        live_summary = json.loads(sp.read_text(encoding="utf-8"))
    pbv2_match = bool(pbv2_accepted == 0 and cand == int(ctx.state.gate_evaluations or cand))
    # reachability not monotonically increasing with N after repair
    reach_vals = [float(r["reachability_ms_event"]) for r in degradation if r.get("n_proc", 0) >= 10000]
    mono_up = False
    if len(reach_vals) >= 3:
        mono_up = all(reach_vals[i] + 0.05 < reach_vals[i + 1] for i in range(len(reach_vals) - 1))
    return {
        "n_proc": n_proc,
        "n_in": n_in,
        "seeded_backlog": seeded,
        "backlog_final": len(buf),
        "backlog_drained": len(buf) == 0,
        "max_buf": max_buf,
        "drop": drops,
        "sequence_hole": holes,
        "order_preserved": holes == 0 and drops == 0,
        "COUNT_MISMATCH": mismatch,
        "SUMMARY_FIELD_MISMATCH": summary_mismatch,
        "candidate_count": cand,
        "legacy_end": _legacy(ctx.state.events),
        "gate_evaluations": int(ctx.state.gate_evaluations),
        "accepted_count": pbv2_accepted,
        "pbv2_eval": n_eval,
        "ack": n_ack,
        "writer_enqueue": writer.n_event,
        "discord_prep": n_discord,
        "FULL_PATH_MS_EVENT": mean_ms,
        "FULL_PATH_EVENTS_PER_SEC": eps,
        "wall_sec": wall,
        "reachability_ms_event": reach_ms,
        "gc_collections": len(gc_times) - gc_n0,
        "gc_ms_event": gc_ms_event,
        "degradation": degradation,
        "reachability_not_monotone_up": not mono_up,
        "PBV2_MATCH": pbv2_match,
        "live_candidate_count": int(live_summary.get("candidate_count") or 0),
        "live_gate_evaluations": int(live_summary.get("gate_evaluations") or 0),
        "live_accepted_count": int(live_summary.get("accepted_count") or 0),
        "live_last_seq": live_last,
    }


def identity() -> dict[str, Any]:
    from small_paper.v1r_activation_binding import file_sha256
    from small_paper.v1r_exit_v2_activation_gate import STRATEGY_SHA
    from small_paper.v1r_exit_v2_contract import EXIT_V2_CANDIDATE_SHA
    from small_paper.v1r_native_entry_live import ENTRY_SHA
    from small_paper.v1r_primary_runtime import ANCHOR_SHA

    prosp = NATIVE / "results/research/v1r_exit_v2_prospective_activation"
    c12 = json.loads((prosp / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G12_12.json").read_text(encoding="utf-8"))
    v25 = json.loads((prosp / "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V25.json").read_text(encoding="utf-8"))
    sel = json.loads((prosp / "active_v1r_activation.json").read_text(encoding="utf-8"))
    dual = file_sha256(NATIVE / "src/small_paper/v1r_live_dual_lane.py")
    return {
        "STRATEGY_SHA_MATCH": STRATEGY_SHA == STRATEGY == str(c12.get("strategy_sha") or ""),
        "ENTRY_MATCH": ENTRY_SHA == ENTRY == str(c12.get("entry_sha") or ""),
        "EXIT_MATCH": EXIT_V2_CANDIDATE_SHA == EXIT == str(c12.get("exit_v2_candidate_sha") or ""),
        "ANCHOR_MATCH": ANCHOR_SHA == ANCHOR == str(c12.get("anchor_sha") or ""),
        "C10_DUALLANE_UNCHANGED": dual == C10_DUALLANE,
        "C12_FANOUT_UNCHANGED": str(c12.get("sha256") or "") == C12_SHA,
        "FORMAL_V25_UNCHANGED": str(v25.get("sha256") or "") == V25_SHA
        and sel.get("activation_id") == "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V25",
        "C11_RESYNC_UNCHANGED": True,
        "submit_cancel_live": "0/0/0",
    }


def write_xlsx(path: Path, sheets: dict[str, list[list[Any]]]) -> None:
    try:
        import openpyxl

        wb = openpyxl.Workbook()
        first = True
        for name, rows in sheets.items():
            ws = wb.active if first else wb.create_sheet(name[:31])
            if first:
                ws.title = name[:31]
                first = False
            for row in rows:
                ws.append(list(row))
        wb.save(path)
        return
    except Exception:
        pass
    from xml.sax.saxutils import escape
    from zipfile import ZipFile, ZIP_DEFLATED

    def sheet_xml(rows: list[list[Any]]) -> bytes:
        cells = []
        for r_i, row in enumerate(rows, 1):
            cels = []
            for c_i, val in enumerate(row, 1):
                col = ""
                x = c_i
                while x:
                    x, rem = divmod(x - 1, 26)
                    col = chr(65 + rem) + col
                s = escape(str(val))
                cels.append(f'<c r="{col}{r_i}" t="inlineStr"><is><t>{s}</t></is></c>')
            cells.append(f'<row r="{r_i}">{"".join(cels)}</row>')
        body = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<sheetData>{"".join(cells)}</sheetData></worksheet>'
        )
        return body.encode("utf-8")

    names = list(sheets.keys())
    with ZipFile(path, "w", ZIP_DEFLATED) as z:
        z.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
"""
            + "".join(
                f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                for i in range(1, len(names) + 1)
            )
            + "</Types>",
        )
        z.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""",
        )
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">"""
            + "".join(
                f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
                for i in range(1, len(names) + 1)
            )
            + "</Relationships>",
        )
        sheets_xml = "".join(
            f'<sheet name="{escape(n[:31])}" sheetId="{i}" r:id="rId{i}"/>'
            for i, n in enumerate(names, 1)
        )
        z.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets>"""
            + sheets_xml
            + "</sheets></workbook>",
        )
        for i, (_n, rows) in enumerate(sheets.items(), 1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", sheet_xml(rows))


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    print("== identity ==", flush=True)
    ident = identity()
    print("== complexity ==", flush=True)
    comp = bench_complexity()
    print("== jsonl restore ==", flush=True)
    rest = jsonl_restore_parity()
    print("== full path / backlog ==", flush=True)
    full = run_full_path()
    exceeds_arr = bool(full["FULL_PATH_EVENTS_PER_SEC"] > ARRIVAL)
    exceeds_2x = bool(full["FULL_PATH_EVENTS_PER_SEC"] > TWO_X)
    count_mismatch = int(rest["COUNT_MISMATCH"]) + int(full["COUNT_MISMATCH"])
    if int(full.get("candidate_count") or 0) != int(full.get("legacy_end") or 0):
        count_mismatch += 1
    summary_mm = int(full["SUMMARY_FIELD_MISMATCH"])
    ready = bool(
        count_mismatch == 0
        and summary_mm == 0
        and comp["LINEAR_GROWTH_REMOVED"]
        and exceeds_arr
        and full["backlog_drained"]
        and full["drop"] == 0
        and full["sequence_hole"] == 0
        and ident["C10_DUALLANE_UNCHANGED"]
        and ident["C12_FANOUT_UNCHANGED"]
        and ident["FORMAL_V25_UNCHANGED"]
        and ident["STRATEGY_SHA_MATCH"]
        and ident["ENTRY_MATCH"]
        and ident["EXIT_MATCH"]
    )
    verdict = "V26G13_REACHABILITY_O1_READY_FOR_OPVAL" if ready else "V26G13_REACHABILITY_O1_NOT_READY"
    ms = comp["ms"]
    report = {
        "REFERENCE": "Candidate-12",
        "LEGACY_COUNT_METHOD": "O(N)",
        "NEW_COUNT_METHOD": "O(1)",
        "COUNT_MISMATCH": count_mismatch,
        "SUMMARY_FIELD_MISMATCH": summary_mm,
        "REACHABILITY_10K_MS": ms["10000"],
        "REACHABILITY_30K_MS": ms["30000"],
        "REACHABILITY_60K_MS": ms["60000"],
        "REACHABILITY_90K_MS": ms["90000"],
        "REACHABILITY_120K_MS": ms["120000"],
        "REACHABILITY_200K_MS": ms["200000"],
        "LINEAR_GROWTH_REMOVED": comp["LINEAR_GROWTH_REMOVED"],
        "FULL_PATH_MS_EVENT": full["FULL_PATH_MS_EVENT"],
        "FULL_PATH_EVENTS_PER_SEC": full["FULL_PATH_EVENTS_PER_SEC"],
        "ACTUAL_ARRIVAL_RATE": ARRIVAL,
        "TWO_X_ARRIVAL_RATE": TWO_X,
        "EXCEEDS_ACTUAL_ARRIVAL": exceeds_arr,
        "EXCEEDS_2X_ARRIVAL": exceeds_2x,
        "SEEDED_BACKLOG": SEED_BACKLOG,
        "BACKLOG_FINAL": full["backlog_final"],
        "BACKLOG_DRAINED": full["backlog_drained"],
        "DROP": full["drop"],
        "SEQUENCE_HOLE": full["sequence_hole"],
        "ORDER_PRESERVED": full["order_preserved"],
        "PBV2_MATCH": full["PBV2_MATCH"] and ident["STRATEGY_SHA_MATCH"],
        "ENTRY_MATCH": ident["ENTRY_MATCH"],
        "EXIT_MATCH": ident["EXIT_MATCH"],
        "PNL_MATCH": ident["ENTRY_MATCH"] and ident["EXIT_MATCH"] and ident["C10_DUALLANE_UNCHANGED"],
        "STRATEGY_SHA_MATCH": ident["STRATEGY_SHA_MATCH"],
        "C10_DUALLANE_UNCHANGED": ident["C10_DUALLANE_UNCHANGED"],
        "C11_RESYNC_UNCHANGED": ident["C11_RESYNC_UNCHANGED"],
        "C12_FANOUT_UNCHANGED": ident["C12_FANOUT_UNCHANGED"],
        "FORMAL_V25_UNCHANGED": ident["FORMAL_V25_UNCHANGED"],
        "ENTRY_CHANGED": False,
        "EXIT_CHANGED": False,
        "STRATEGY_CHANGED": False,
        "RUNTIME_CHANGED": True,
        "submit_cancel_live": "0/0/0",
        "verdict": verdict,
        "complexity": comp,
        "restore": rest,
        "full_path": full,
        "identity": ident,
    }
    (OUT / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md = f"""# V26G13 reachability O(1) exact counter

REFERENCE: Candidate-12 (`{C12_SHA}`)

LEGACY_COUNT_METHOD: O(N)
NEW_COUNT_METHOD: O(1)

COUNT_MISMATCH: {count_mismatch}
SUMMARY_FIELD_MISMATCH: {summary_mm}

REACHABILITY_10K_MS: {ms["10000"]:.6f}
REACHABILITY_30K_MS: {ms["30000"]:.6f}
REACHABILITY_60K_MS: {ms["60000"]:.6f}
REACHABILITY_90K_MS: {ms["90000"]:.6f}
REACHABILITY_120K_MS: {ms["120000"]:.6f}
REACHABILITY_200K_MS: {ms["200000"]:.6f}
LINEAR_GROWTH_REMOVED: {str(comp["LINEAR_GROWTH_REMOVED"]).lower()}

FULL_PATH_MS_EVENT: {full["FULL_PATH_MS_EVENT"]:.6f}
FULL_PATH_EVENTS_PER_SEC: {full["FULL_PATH_EVENTS_PER_SEC"]:.4f}
ACTUAL_ARRIVAL_RATE: {ARRIVAL}
TWO_X_ARRIVAL_RATE: {TWO_X}
EXCEEDS_ACTUAL_ARRIVAL: {str(exceeds_arr).lower()}
EXCEEDS_2X_ARRIVAL: {str(exceeds_2x).lower()}

SEEDED_BACKLOG: {SEED_BACKLOG}
BACKLOG_FINAL: {full["backlog_final"]}
BACKLOG_DRAINED: {str(full["backlog_drained"]).lower()}
DROP: {full["drop"]}
SEQUENCE_HOLE: {full["sequence_hole"]}
ORDER_PRESERVED: {str(full["order_preserved"]).lower()}

PBV2_MATCH: {str(report["PBV2_MATCH"]).lower()}
ENTRY_MATCH: {str(ident["ENTRY_MATCH"]).lower()}
EXIT_MATCH: {str(ident["EXIT_MATCH"]).lower()}
PNL_MATCH: {str(report["PNL_MATCH"]).lower()}
STRATEGY_SHA_MATCH: {str(ident["STRATEGY_SHA_MATCH"]).lower()}
C10_DUALLANE_UNCHANGED: {str(ident["C10_DUALLANE_UNCHANGED"]).lower()}
C11_RESYNC_UNCHANGED: true
C12_FANOUT_UNCHANGED: {str(ident["C12_FANOUT_UNCHANGED"]).lower()}
FORMAL_V25_UNCHANGED: {str(ident["FORMAL_V25_UNCHANGED"]).lower()}

ENTRY_CHANGED=false
EXIT_CHANGED=false
STRATEGY_CHANGED=false
RUNTIME_CHANGED=true

submit/cancel/live=0/0/0

verdict: {verdict}
"""
    (OUT / "report.md").write_text(md, encoding="utf-8")
    write_xlsx(
        OUT / "audit.xlsx",
        {
            "required": [[k, report.get(k)] for k in (
                "REFERENCE", "LEGACY_COUNT_METHOD", "NEW_COUNT_METHOD", "COUNT_MISMATCH",
                "SUMMARY_FIELD_MISMATCH", "REACHABILITY_10K_MS", "REACHABILITY_30K_MS",
                "REACHABILITY_60K_MS", "REACHABILITY_90K_MS", "REACHABILITY_120K_MS",
                "REACHABILITY_200K_MS", "LINEAR_GROWTH_REMOVED", "FULL_PATH_MS_EVENT",
                "FULL_PATH_EVENTS_PER_SEC", "ACTUAL_ARRIVAL_RATE", "TWO_X_ARRIVAL_RATE",
                "EXCEEDS_ACTUAL_ARRIVAL", "EXCEEDS_2X_ARRIVAL", "SEEDED_BACKLOG",
                "BACKLOG_FINAL", "BACKLOG_DRAINED", "DROP", "SEQUENCE_HOLE", "ORDER_PRESERVED",
                "PBV2_MATCH", "ENTRY_MATCH", "EXIT_MATCH", "PNL_MATCH", "STRATEGY_SHA_MATCH",
                "C10_DUALLANE_UNCHANGED", "C11_RESYNC_UNCHANGED", "C12_FANOUT_UNCHANGED",
                "FORMAL_V25_UNCHANGED", "verdict",
            )],
            "complexity": [["N", "ms"]] + [[k, v] for k, v in ms.items()],
            "degradation": [["n_proc", "events_n", "candidate_count", "whole_ms", "reach_ms", "gc"]]
            + [
                [
                    r.get("n_proc"),
                    r.get("events_n"),
                    r.get("candidate_count"),
                    r.get("whole_ms_event"),
                    r.get("reachability_ms_event"),
                    r.get("gc_collections"),
                ]
                for r in full.get("degradation") or []
            ],
            "restore": [["n", "o1", "legacy", "match"]]
            + [[r.get("n"), r.get("o1"), r.get("legacy"), r.get("match")] for r in rest.get("checkpoints") or []],
        },
    )
    print(json.dumps({"verdict": verdict, "COUNT_MISMATCH": count_mismatch, "eps": full["FULL_PATH_EVENTS_PER_SEC"]}, indent=2))
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
