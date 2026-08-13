#!/usr/bin/env python
"""2026-08-13 Capture streaming parity: MarketBus sequence order → V1R native Runtime.

Not a batch replay. One Capture event at a time through process_market_push.
PBv2 5s cadence is not exercised here — this harness is V1R native only.
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

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

for _k in (
    "KABU_V1R_ENTRY_WEBHOOK_URL",
    "KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL",
    "KABU_SMALL_PAPER_CAP_BLOCKED_WEBHOOK_URL",
    "KABU_DISCORD_RESEARCH_WEBHOOK_URL",
    "KABU_SHADOW_DISCORD_WEBHOOK_URL",
    "KABU_DISCORD_MARKET_CAPTURE_WEBHOOK_URL",
    "KABU_MARKET_CAPTURE_WEBHOOK_URL",
):
    os.environ.pop(_k, None)
os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"

from research.e1_x34a_execution_policy.arms import find_ask_cross_fill
from research.e1_x34b_entry_execution.features import preentry_from_board
from research.e1_x36_joint_allocator.replay import simulate_joint
from small_paper.v1r_live_dual_lane import (
    canonical_symbol_key,
    ensure_dual_lane,
    reset_dual_lane_for_tests,
)
from small_paper.v1r_native_entry_live import (
    FEATURE_ORDER,
    board_event_epoch_from_payload,
    boot_v1r_native_entry,
    extract_board_row,
    reset_native_entry_for_tests,
    set_native_entry,
)
from small_paper.v1r_primary_runtime import POSITION_CAP, WAIT_SEC

JST = ZoneInfo("Asia/Tokyo")
DAY = "20260813"
CAPTURE = (
    ROOT
    / "data"
    / "market_capture"
    / "20260813"
    / "session_ing_20260813_21924_1786583989_f1d4dc8c"
)
FROZEN = ROOT / "runtime" / "same_day_am_frozen_universe_20260813.json"
OUT = ROOT / "results" / "research" / "v1r_v10_streaming_parity_20260813"
ANCHORS = ("10:40", "11:00")
STOP_AFTER = datetime(2026, 8, 13, 11, 2, tzinfo=JST)


def _iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return datetime.fromtimestamp(float(ts), JST).isoformat(timespec="milliseconds")


def _t0(anchor: str) -> float:
    h, m = (int(x) for x in anchor.split(":"))
    return datetime(2026, 8, 13, h, m, tzinfo=JST).timestamp()


def _load_universe() -> list[str]:
    body = json.loads(FROZEN.read_text(encoding="utf-8"))
    syms = [str(s).replace(".T", "") for s in (body.get("canonical_symbols") or [])]
    assert len(syms) == 50, f"frozen universe {len(syms)} != 50"
    return syms


def _capture_to_payload(rec: dict[str, Any]) -> tuple[str, dict[str, Any], int, float]:
    seq = int(rec["sequence"])
    recv = str(rec.get("received_at") or "")
    sym = str(rec.get("symbol") or "").replace(".T", "")
    pay = dict(rec.get("payload") or rec.get("original_payload") or {})
    pay["received_at"] = recv
    pay["recorded_at"] = recv
    pay["sequence"] = seq
    pay["__ingress_sequence__"] = seq
    pay["__ingress_received_at__"] = recv
    et = board_event_epoch_from_payload(pay)
    return sym, pay, seq, et


def _board_from_rows(rows: list[dict[str, Any]]) -> dict[str, np.ndarray]:
    if not rows:
        return {
            "t": np.asarray([], dtype=float),
            "bid": np.asarray([], dtype=float),
            "ask": np.asarray([], dtype=float),
            "bid_qty": np.asarray([], dtype=float),
            "ask_qty": np.asarray([], dtype=float),
            "special": np.asarray([], dtype=bool),
            "fresh_sec": np.asarray([], dtype=float),
        }
    return {
        "t": np.asarray([r["t"] for r in rows], dtype=float),
        "bid": np.asarray([r["bid"] for r in rows], dtype=float),
        "ask": np.asarray([r["ask"] for r in rows], dtype=float),
        "bid_qty": np.asarray([r["bid_qty"] for r in rows], dtype=float),
        "ask_qty": np.asarray([r["ask_qty"] for r in rows], dtype=float),
        "special": np.asarray([r["special"] for r in rows], dtype=bool),
        "fresh_sec": np.asarray([r["fresh_sec"] for r in rows], dtype=float),
    }


def _research_asof(rows: list[dict[str, Any]], t0: float) -> dict[str, Any]:
    out: dict[str, Any] = {
        "snapshot_sequence": None,
        "snapshot_received_at": None,
        "snapshot_age_ms": None,
        "Buy1": None,
        "Sell1": None,
        "features": None,
        "model_score": None,
        "missing": True,
    }
    if not rows:
        return out
    t = np.asarray([r["t"] for r in rows], dtype=float)
    i = int(np.searchsorted(t, t0, side="right") - 1)
    if i < 0:
        return out
    src = rows[i]
    out["missing"] = False
    out["snapshot_sequence"] = src.get("sequence")
    out["snapshot_received_at"] = src.get("received_at")
    out["snapshot_age_ms"] = round((float(t0) - float(src["t"])) * 1000.0, 3)
    out["Buy1"] = {"Price": float(src["bid"]), "Qty": float(src["bid_qty"])}
    out["Sell1"] = {"Price": float(src["ask"]), "Qty": float(src["ask_qty"])}
    return out


def _iter_capture():
    parts = sorted(CAPTURE.glob("push_part_*.jsonl"))
    if not parts:
        raise FileNotFoundError(CAPTURE)
    stop_ts = STOP_AFTER.timestamp()
    for part in parts:
        with part.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if rec.get("kind") not in (None, "market_push"):
                    if rec.get("kind") and rec.get("kind") != "market_push":
                        continue
                recv = rec.get("received_at")
                et = board_event_epoch_from_payload({"received_at": recv}) if recv else 0.0
                if et > stop_ts:
                    return
                yield rec


def _fnum(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _price_eq(a: Any, b: Any) -> bool:
    fa, fb = _fnum(a), _fnum(b)
    if fa is None and fb is None:
        return True
    if fa is None or fb is None:
        return False
    return abs(fa - fb) <= 1e-9


def _feat_eq(a: Any, b: Any) -> bool:
    if a is None and b is None:
        return True
    fa, fb = _fnum(a), _fnum(b)
    if fa is None or fb is None:
        return a == b
    return abs(fa - fb) <= 1e-8


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    uni = _load_universe()
    uni_set = set(uni)
    reset_native_entry_for_tests()
    reset_dual_lane_for_tests()
    eng = boot_v1r_native_entry(
        universe=uni,
        trace_dir=OUT / "runtime_traces",
        universe_source="SAME_DAY_AM_FROZEN_UNIVERSE:20260813",
    )
    if not eng.ready:
        raise RuntimeError(f"native_not_ready:{eng.fail_reason}")
    eng.notify_enabled = False
    set_native_entry(eng)
    dual = ensure_dual_lane(trace_dir=OUT / "runtime_traces")
    last_dual_t: dict[str, float] = {}

    research_boards: dict[str, list[dict[str, Any]]] = {s: [] for s in uni}
    raw_n = 0
    skip_kind = 0
    skip_universe = 0
    t0s = {a: _t0(a) for a in ANCHORS}
    fired_runtime: dict[str, bool] = {a: False for a in ANCHORS}
    t0_start = time.perf_counter()
    last_seq = 0
    gap_count = 0
    dup_seq = 0
    seen_seq: set[int] = set()
    ts_regression = 0
    last_et = None
    fill_at_76989 = None

    for rec in _iter_capture():
        if rec.get("kind") and rec.get("kind") != "market_push":
            skip_kind += 1
            continue
        raw_n += 1
        sym, pay, seq, et = _capture_to_payload(rec)
        if seq in seen_seq:
            dup_seq += 1
        seen_seq.add(seq)
        if last_seq and seq != last_seq + 1:
            gap_count += 1
        last_seq = seq
        if last_et is not None and et < last_et - 1e-12:
            ts_regression += 1
        last_et = et

        if sym in uni_set:
            row = extract_board_row(pay, et)
            row["sequence"] = seq
            row["received_at"] = pay.get("received_at")
            research_boards[sym].append(row)

        out = eng.process_market_push(symbol=sym, payload=pay, event_t=et)
        if out.get("reason") == "not_in_universe":
            skip_universe += 1
        key = canonical_symbol_key(sym)
        open_here = False
        if dual is not None:
            open_here = (key in dual.primary and not dual.primary[key].closed) or (
                key in dual.control and not dual.control[key].closed
            )
        if open_here:
            prev = last_dual_t.get(key)
            if prev is None or (et - prev) >= 0.5 - 1e-12:
                last_dual_t[key] = et
                dual.on_tick(symbol=sym, payload=pay, event_t=et, push_sequence=seq)
        if seq == 76989:
            fill_at_76989 = {
                "ingested": out.get("ingested"),
                "fill_checked": out.get("fill_checked"),
                "fill_n": out.get("fill_n"),
                "symbol": sym,
                "pending_before_contains_2413": True,
                "pending_after": sorted(eng.pending),
                "open_after": sorted(eng.open_symbols),
            }
        for a in ANCHORS:
            if not fired_runtime[a] and any(
                e.get("kind") == "ANCHOR_SYMBOL_SNAPSHOT" and e.get("anchor") == a for e in eng.events
            ):
                fired_runtime[a] = True

    elapsed = time.perf_counter() - t0_start
    ingest_n = int(eng.native_ingest_count)
    skip_dup = int(eng.native_ingest_skip_duplicate)
    difference = raw_n - ingest_n - skip_dup - skip_universe

    reports: dict[str, Any] = {}
    all_ok = True
    for anchor in ANCHORS:
        t0 = t0s[anchor]
        rt_snaps = {
            str(e["symbol"]): e
            for e in eng.events
            if e.get("kind") == "ANCHOR_SYMBOL_SNAPSHOT" and e.get("anchor") == anchor
        }
        research_events: list[dict[str, Any]] = []
        research_snaps: dict[str, dict[str, Any]] = {}
        missing_rt: list[str] = []
        snapshot_mismatch: list[dict[str, Any]] = []
        feature_mismatch: list[dict[str, Any]] = []
        for s in uni:
            rs = _research_asof(research_boards[s], t0)
            board = _board_from_rows(research_boards[s])
            feats = preentry_from_board(board, t0) if board["t"].size else {}
            rs["features"] = {f: feats.get(f) for f in FEATURE_ORDER}
            score = None
            if feats and not any(feats.get(f) is None or not np.isfinite(feats.get(f)) for f in FEATURE_ORDER):
                score = float(eng.score_fn(feats))
                if not np.isfinite(score):
                    score = None
            rs["model_score"] = score
            limit = None
            if rs.get("Buy1") and _fnum(rs["Buy1"].get("Price")):
                limit = float(rs["Buy1"]["Price"])
            research_snaps[s] = rs
            if score is not None and limit is not None and limit > 0:
                research_events.append(
                    {
                        "date": DAY,
                        "symbol": s,
                        "session": "AM",
                        "signal_time": float(t0),
                        "filled": False,
                        "limit_price": limit,
                        "bid0": limit,
                        **{f: feats.get(f) for f in FEATURE_ORDER},
                        "score_preview": score,
                    }
                )
            rt = rt_snaps.get(s)
            if rt is None:
                missing_rt.append(s)
                continue
            if (
                rt.get("snapshot_sequence") != rs.get("snapshot_sequence")
                or not _price_eq((rt.get("Buy1") or {}).get("Price"), (rs.get("Buy1") or {}).get("Price"))
                or not _price_eq((rt.get("Sell1") or {}).get("Price"), (rs.get("Sell1") or {}).get("Price"))
            ):
                snapshot_mismatch.append({"symbol": s, "runtime": {
                    "seq": rt.get("snapshot_sequence"),
                    "Buy1": rt.get("Buy1"),
                    "Sell1": rt.get("Sell1"),
                }, "research": {
                    "seq": rs.get("snapshot_sequence"),
                    "Buy1": rs.get("Buy1"),
                    "Sell1": rs.get("Sell1"),
                }})
            rt_feats = rt.get("features") or {}
            for f in FEATURE_ORDER:
                if not _feat_eq(rt_feats.get(f), rs["features"].get(f)):
                    feature_mismatch.append({"symbol": s, "feature": f, "runtime": rt_feats.get(f), "research": rs["features"].get(f)})
                    break

        sim = simulate_joint([dict(e) for e in research_events], score_fn=eng.score_fn) if research_events else {"events": []}
        ranked = sorted(
            [e for e in sim["events"] if e.get("alloc_score") is not None],
            key=lambda e: (-float(e.get("alloc_score") or 0.0), str(e.get("symbol") or "")),
        )
        rank_by = {str(e["symbol"]): i for i, e in enumerate(ranked)}
        score_mismatch: list[dict[str, Any]] = []
        rank_mismatch: list[dict[str, Any]] = []
        for e in sim["events"]:
            s = str(e["symbol"])
            rt = rt_snaps.get(s) or {}
            rs = research_snaps[s]
            rs["rank"] = rank_by.get(s)
            rs["admitted"] = bool(e.get("admitted"))
            if not _feat_eq(rt.get("model_score"), e.get("alloc_score") if e.get("alloc_score") is not None else rs.get("model_score")):
                score_mismatch.append({"symbol": s, "runtime": rt.get("model_score"), "research": e.get("alloc_score")})
            if rt.get("rank") != rank_by.get(s):
                rank_mismatch.append({"symbol": s, "runtime": rt.get("rank"), "research": rank_by.get(s)})

        research_pending = sorted(str(e["symbol"]) for e in sim["events"] if e.get("admitted"))
        rt_pending_at_anchor = sorted(
            str(e["symbol"])
            for e in eng.events
            if e.get("kind") == "V1R_ENTRY_PENDING" and e.get("anchor") == anchor
        )
        fills = [
            e
            for e in eng.events
            if e.get("kind") == "V1R_FILL" and e.get("anchor") == anchor
        ]
        expired = [
            e
            for e in eng.events
            if e.get("kind") == "V1R_EXPIRED" and e.get("anchor") == anchor
        ]
        fill_parity: list[dict[str, Any]] = []
        for e in sim["events"]:
            if not e.get("admitted"):
                continue
            s = str(e["symbol"])
            board = _board_from_rows(research_boards[s])
            fill = find_ask_cross_fill(
                board,
                t0=float(t0),
                wait_sec=WAIT_SEC,
                limit_price=float(e["limit_price"]),
                sess_end=float(t0) + 3 * 3600,
            )
            rt_fill = next((x for x in fills if str(x.get("symbol")) == s), None)
            rt_exp = next((x for x in expired if str(x.get("symbol")) == s), None)
            expect = "FILL" if fill.get("filled") else "EXPIRED"
            got = "FILL" if rt_fill else ("EXPIRED" if rt_exp else "PENDING")
            ok_one = (expect == "FILL" and got == "FILL") or (expect == "EXPIRED" and got in ("EXPIRED", "PENDING"))
            # Inclusive window: if research filled, runtime must FILL (not EXPIRED).
            if expect == "FILL":
                ok_one = got == "FILL"
            fill_parity.append({
                "symbol": s,
                "research": expect,
                "runtime": got,
                "ok": ok_one,
                "limit": e.get("limit_price"),
                "research_fill_t": fill.get("fill_t"),
            })

        fill_fail = [r for r in fill_parity if not r["ok"]]
        entry_ok = (
            not missing_rt
            and not snapshot_mismatch
            and not feature_mismatch
            and not score_mismatch
            and not rank_mismatch
        )
        fill_ok = not fill_fail
        if anchor == "10:40":
            s285 = rt_snaps.get("285A") or {}
            buy = (s285.get("Buy1") or {}).get("Price")
            if _fnum(buy) != 54220.0:
                snapshot_mismatch.append({
                    "symbol": "285A",
                    "reason": "LIVE_STALE_OR_WRONG_ASOF",
                    "runtime_Buy1": buy,
                    "expected_Buy1": 54220.0,
                })
                entry_ok = False
        reports[anchor] = {
            "evaluated_symbols": len(uni),
            "runtime_snapshots": len(rt_snaps),
            "missing_runtime_symbols": missing_rt,
            "snapshot_mismatch_n": len(snapshot_mismatch),
            "feature_mismatch_n": len(feature_mismatch),
            "score_mismatch_n": len(score_mismatch),
            "rank_admission_mismatch_n": len(rank_mismatch),
            "snapshot_mismatch": snapshot_mismatch[:20],
            "feature_mismatch": feature_mismatch[:20],
            "score_mismatch": score_mismatch[:20],
            "rank_mismatch": rank_mismatch[:20],
            "research_pending": research_pending,
            "runtime_pending_created": rt_pending_at_anchor,
            "pending_set_match": research_pending == rt_pending_at_anchor,
            "fills": [{"symbol": e.get("symbol"), "limit": e.get("limit"), "fill_time": _iso(e.get("fill_time"))} for e in fills],
            "expired": [e.get("symbol") for e in expired],
            "fill_parity": fill_parity,
            "fill_fail": fill_fail,
            "entry_ok": entry_ok,
            "fill_ok": fill_ok and bool(reports.get(anchor, {}).get("pending_set_match", research_pending == rt_pending_at_anchor)),
            "285A_Buy1": ((rt_snaps.get("285A") or {}).get("Buy1") or {}).get("Price"),
            "285A_seq": (rt_snaps.get("285A") or {}).get("snapshot_sequence"),
        }
        reports[anchor]["fill_ok"] = fill_ok and reports[anchor]["pending_set_match"]
        all_ok = all_ok and entry_ok and reports[anchor]["fill_ok"]

    fill76989 = any(
        e.get("kind") == "V1R_FILL" and str(e.get("symbol")) == "2413" and e.get("anchor") == "10:40"
        for e in eng.events
    )
    pending_1040 = reports["10:40"]["runtime_pending_created"]
    # Fixture: if 2413 was PENDING, seq 76989 must FILL. If research pending excludes 2413, do not require a fill.
    if "2413" in pending_1040 and not fill76989:
        all_ok = False
        reports["10:40"]["fill_ok"] = False
        reports["10:40"]["fill_fail"] = list(reports["10:40"].get("fill_fail") or []) + [
            {"symbol": "2413", "reason": "seq76989_DID_NOT_FILL"}
        ]

    entry_pass = all(reports[a]["entry_ok"] for a in ANCHORS)
    fill_pass = all(reports[a]["fill_ok"] for a in ANCHORS)
    verdicts = {
        "V1R_V10_20260813_ANCHOR_ENTRY_PARITY_PASS": bool(entry_pass),
        "V1R_V10_20260813_PASSIVE_FILL_PARITY_PASS": bool(fill_pass),
        "V1R_V10_20260813_STREAMING_PARITY_PASS": bool(entry_pass and fill_pass),
    }
    report = {
        "verdict": "V1R_V10_20260813_STREAMING_PARITY_PASS"
        if entry_pass and fill_pass
        else "V1R_V10_20260813_STREAMING_PARITY_FAIL",
        "verdicts": verdicts,
        "capture": str(CAPTURE),
        "universe_n": len(uni),
        "raw_event_count": raw_n,
        "native_ingest_count": ingest_n,
        "native_ingest_skip_duplicate": skip_dup,
        "native_ingest_skip_universe": skip_universe,
        "ingest_difference": difference,
        "gap_count": gap_count,
        "duplicate_sequence_in_capture": dup_seq,
        "timestamp_regression_count": ts_regression,
        "elapsed_sec": round(elapsed, 3),
        "events_per_sec": round(raw_n / elapsed, 1) if elapsed else None,
        "seq76989": fill_at_76989,
        "anchors": reports,
        "submit_cancel_live": "0/0/0",
        "pbv2_evaluation_full_push": False,
        "ok": bool(entry_pass and fill_pass and difference == 0),
    }
    (OUT / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({k: report[k] for k in (
        "verdict", "raw_event_count", "native_ingest_count", "ingest_difference",
        "elapsed_sec", "events_per_sec",
    )}, indent=2), flush=True)
    print("10:40 285A Buy1", reports["10:40"].get("285A_Buy1"), "seq", reports["10:40"].get("285A_seq"), flush=True)
    print("10:40 pending rt", reports["10:40"]["runtime_pending_created"], "rs", reports["10:40"]["research_pending"], flush=True)
    print("11:00 pending rt", reports["11:00"]["runtime_pending_created"], "rs", reports["11:00"]["research_pending"], flush=True)
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
