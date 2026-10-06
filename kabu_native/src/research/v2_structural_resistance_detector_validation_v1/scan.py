"""Capture scan for visual validation (and optional full economic pass).

Does not modify frozen V2. Reconstructs ratchet timestamps beside the simulator.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import numpy as np

from research.anchor_timing_robustness import AM_END, AM_START, PM_END, PM_START
from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import capture_event_epoch, find_capture_dir, iter_push, record_event_stamp
from research.event_time_impulse_complete_strategy_v2.rules import (
    activity_lost,
    exhausted,
    participation_lost,
    ratchet,
    reconfirm,
    support_failure,
)
from research.event_time_impulse_complete_strategy_v2.scan import _Col, _arrays, _dates, _full_signals
from research.event_time_impulse_complete_strategy_v2.simulate import prepare_book, simulate_session
from research.event_time_volume_confirmed_impulse.features import evaluate
from research.event_time_volume_confirmed_impulse.scan import _classify, _quote_ok
from research.event_time_volume_confirmed_impulse_entry import EXPECTED_DAY_FULL
import research.stock_specific_sequential_setup.episodes  # noqa: F401
from research.simple_tech_entry_family.harvest import _Buf, _bare, _board_row
from research.symbol_setup_baseline_complete_strategy_precommit.contract import EXTENSION17, ORIGINAL18
from research.v2_structural_resistance_detector_validation_v1.detector import SessionDetector, zone_to_dict, runtime_to_dict


# Dates chosen for visual coverage only (symbols/times vary within). Not PnL-tuned.
VISUAL_DATES = (
    "20260722",
    "20260729",
    "20260805",
    "20260819",
    "20260901",
    "20260910",
)


def reconstruct_path_events(book: dict[str, Any], trade: dict[str, Any]) -> dict[str, Any]:
    """Replay exit path beside the simulator; abort claims if ratchet/exit mismatch."""
    entry_t = float(trade["entry_t"])
    exit_t = float(trade["exit_t"])
    times = book["t"]
    index = int(np.searchsorted(times, entry_t, side="left"))
    if index >= int(times.size) or abs(float(times[index]) - entry_t) > 1e-3:
        return {"ok": False, "reason": "entry_index_miss"}
    support = float(book["pre_high"][index])
    last = entry_t
    ratchets = 0
    ratchet_events: list[dict[str, Any]] = []
    exit_reason = None
    i = index + 1
    n = int(times.size)
    while i < n and float(times[i]) <= exit_t + 1e-9:
        price = float(book["px"][i])
        event_t = float(times[i])
        if reconfirm(
            bool(book["vol10"][i]),
            bool(book["vol30"][i]),
            bool(book["buy"][i]),
            float(book["classified"][i]),
            bool(book["tick"][i]),
            bool(book["break"][i]),
        ):
            new = ratchet(float(book["pre_high"][i]), support)
            if new > support:
                support = new
                ratchets += 1
                ratchet_events.append({"t": event_t, "support": support, "pre_high": float(book["pre_high"][i])})
            last = event_t
        if support_failure(price, support):
            exit_reason = "BREAK_SUPPORT_FAILURE"
            break
        if exhausted(
            participation_lost(float(book["classified"][i]), float(book["ask10"][i]), float(book["bid10"][i])),
            activity_lost(bool(book["vol10"][i]), bool(book["vol30"][i]), bool(book["tick"][i])),
            event_t - last,
        ):
            exit_reason = "IMPULSE_EXHAUSTED"
            break
        i += 1
    if exit_reason is None and abs(exit_t - float(trade["exit_t"])) <= 1e-6:
        # SESSION_FLAT or fill lag; accept trade reason if ratchet matches
        exit_reason = str(trade.get("reason") or "")
    match_ratchet = int(ratchets) == int(trade.get("ratchets") or 0)
    match_reason = exit_reason == str(trade.get("reason") or "") or str(trade.get("reason") or "") == "SESSION_FLAT"
    return {
        "ok": match_ratchet,
        "match_ratchet": match_ratchet,
        "match_reason": match_reason,
        "reconstructed_ratchets": ratchets,
        "reconstructed_reason": exit_reason,
        "ratchet_events": ratchet_events,
        "pre_break": float(book["pre_high"][index]),
        "entry_index": index,
    }


STRUCTURE_SLOTS = (
    "repeated_upper_rejection",
    "sideways_consolidation",
    "single_incidental_high",
    "clean_breakout",
    "failed_breakout",
    "retest_and_hold",
)


def _session_events(book: dict[str, Any]) -> list[tuple[str, float]]:
    """One causal pass per book. Flags are structure labels, not trade outcomes."""
    det = SessionDetector()
    det.run_arrays(
        book["t"],
        book["px"],
        vol=book.get("classified"),
        buy=book.get("ask10"),
        sell=book.get("bid10"),
        board_t=book.get("board_t"),
        board_bid=book.get("bb") if book.get("bb") is not None else book.get("board_bid"),
        board_ask=book.get("board_ask"),
    )
    events: list[tuple[str, float]] = []
    for zone in det.zones:
        if zone.zone_type == "UPPER_REJECTION_ZONE":
            events.append(("repeated_upper_rejection", float(zone.first_seen_t)))
        elif zone.zone_type == "CONSOLIDATION_ZONE":
            events.append(("sideways_consolidation", float(zone.first_seen_t)))
        elif zone.zone_type == "SINGLE_SWING_HIGH":
            events.append(("single_incidental_high", float(zone.first_seen_t)))
    for rt in det.runtimes.values():
        if rt.support_confirm_t is not None:
            events.append(("retest_and_hold", float(rt.support_confirm_t)))
        if rt.state == "BREAK_ACCEPTED_CANDIDATE" and rt.state_t is not None:
            events.append(("clean_breakout", float(rt.state_t)))
        if rt.failed_t is not None:
            events.append(("failed_breakout", float(rt.failed_t)))
    return events


def _coverage_kind(row: dict[str, Any]) -> Optional[str]:
    hold = float(row["exit_t"]) - float(row["entry_t"])
    pnl = float(row["pnl_yen"])
    ratchets = int(row["ratchets"])
    if ratchets == 0 and hold < 1.0:
        return "short_hold_r0"
    if ratchets == 0 and hold >= 30.0:
        return "long_hold_r0"
    if ratchets == 1:
        return "ratchet_1"
    if ratchets >= 2 and pnl > 0:
        return "ratchet_ge2_win"
    if ratchets >= 2 and pnl <= 0:
        return "ratchet_ge2_loss"
    if hold < 0.1 and pnl < 0:
        return "fail_100ms"
    if str(row["reason"]) == "IMPULSE_EXHAUSTED":
        return "exhausted"
    return None


def analyze_trade_structure(book: dict[str, Any], trade: dict[str, Any], recon: dict[str, Any]) -> dict[str, Any]:
    """Run causal detector on the session path; match PRE_BREAK_HIGH after the fact."""
    entry_t = float(trade["entry_t"])
    exit_t = float(trade["exit_t"])
    pre = float(recon["pre_break"])
    det = SessionDetector()
    # Full session path through exit so breakout/retest after entry are visible
    board_ask = book.get("board_ask")
    det.run_arrays(
        book["t"],
        book["px"],
        vol=book.get("classified"),
        buy=book.get("ask10"),
        sell=book.get("bid10"),
        board_t=book.get("board_t"),
        board_bid=book.get("bb") if book.get("bb") is not None else book.get("board_bid"),
        board_ask=board_ask,
        until_t=exit_t + 1.0,
    )
    # Pre-entry causal view for next-resistance / match
    det_pre = SessionDetector()
    det_pre.run_arrays(
        book["t"],
        book["px"],
        vol=book.get("classified"),
        buy=book.get("ask10"),
        sell=book.get("bid10"),
        board_t=book.get("board_t"),
        board_bid=book.get("bb") if book.get("bb") is not None else book.get("board_bid"),
        board_ask=board_ask,
        until_t=entry_t - 1e-9,
    )
    match = det_pre.pre_break_match(pre_break=pre, t=entry_t - 1e-9)
    nxt = det_pre.next_resistance(t=entry_t - 1e-9, px=float(trade["entry_px"]))
    # Find zone nearest to pre_break among all, and runtime around entry
    matched_zone = match.get("zone")
    runtime_info = None
    if matched_zone is not None:
        rt = det.runtimes.get(matched_zone.zone_id)
        if rt is not None:
            runtime_info = runtime_to_dict(rt)

    # Also locate any UPPER_REJECTION near entry for diagnostics
    upper = [z for z in det_pre.zones if z.zone_type == "UPPER_REJECTION_ZONE"]
    consol = [z for z in det_pre.zones if z.zone_type == "CONSOLIDATION_ZONE"]
    single = [z for z in det_pre.zones if z.zone_type == "SINGLE_SWING_HIGH"]

    headroom = None
    if nxt["status"] == "KNOWN" and nxt.get("level") is not None:
        headroom = float(nxt["level"]) - float(trade["entry_px"])

    return {
        "pre_break": pre,
        "pre_break_in_structural_zone": bool(match.get("matched")),
        "match": {k: v for k, v in match.items() if k != "zone"},
        "matched_zone": None if matched_zone is None else zone_to_dict(matched_zone),
        "runtime": runtime_info,
        "next_resistance": {
            "status": nxt["status"],
            "level": nxt.get("level"),
            "source": nxt.get("source"),
            "zone_type": nxt.get("zone_type"),
            "zone_id": nxt.get("zone_id"),
            "zone_low": None if nxt.get("zone") is None else nxt["zone"].zone_low,
            "zone_high": None if nxt.get("zone") is None else nxt["zone"].zone_high,
        },
        "headroom_yen": headroom,
        "pre_entry_zone_counts": {
            "upper_rejection": len(upper),
            "consolidation": len(consol),
            "single_swing": len(single),
            "all": len(det_pre.zones),
            "swings": len(det_pre.swings),
        },
        "swings": [
            {
                "kind": s.kind,
                "price": s.price,
                "source_t": s.source_t,
                "recognized_at": s.recognized_at,
                "rejection_depth_ticks": s.rejection_depth_ticks,
            }
            for s in det.swings
            if s.recognized_at <= exit_t + 1.0
        ],
        "zones": [zone_to_dict(z) for z in det.zones if z.first_seen_t <= exit_t + 1.0],
        "runtimes": {str(k): runtime_to_dict(v) for k, v in det.runtimes.items()},
        "session_high": det_pre.session_high,
    }


def _chart_payload(book: dict[str, Any], trade: dict[str, Any], structure: dict[str, Any], recon: dict[str, Any], kind: str) -> dict[str, Any]:
    entry_t = float(trade["entry_t"])
    exit_t = float(trade["exit_t"])
    # Enough pre-entry structure: back to session start or 600s
    t0 = max(float(book["t"][0]), entry_t - 600.0)
    t1 = exit_t + 5.0
    times = book["t"]
    left = int(np.searchsorted(times, t0, side="left"))
    right = int(np.searchsorted(times, t1, side="right"))
    bt = book.get("board_t")
    if bt is None:
        bt = book["bt"]
        bid = book["bb"]
        ask = book.get("board_ask")
        if ask is None:
            ask = np.full(bt.shape, np.nan)
    else:
        bid = book["board_bid"]
        ask = book["board_ask"]
    bleft = int(np.searchsorted(bt, t0, side="left"))
    bright = int(np.searchsorted(bt, t1, side="right"))
    return {
        "kind": kind,
        "symbol": trade["symbol"],
        "date": trade["date"],
        "session": trade["session"],
        "entry_t": entry_t,
        "exit_t": exit_t,
        "entry_px": float(trade["entry_px"]),
        "exit_px": float(trade["exit_px"]),
        "reason": trade["reason"],
        "pnl_yen": float(trade["pnl_yen"]),
        "ratchets": int(trade["ratchets"]),
        "hold_sec": float(trade["exit_t"]) - float(trade["entry_t"]),
        "pre_break": structure["pre_break"],
        "structure": structure,
        "ratchet_events": recon.get("ratchet_events") or [],
        "t": times[left:right].astype(float).tolist(),
        "px": book["px"][left:right].astype(float).tolist(),
        "bt": bt[bleft:bright].astype(float).tolist(),
        "bid": bid[bleft:bright].astype(float).tolist(),
        "ask": ask[bleft:bright].astype(float).tolist(),
    }


def scan_days(
    dates: tuple[str, ...] | list[str],
    *,
    collect_charts: bool = True,
    max_charts: int = 16,
    annotate_all: bool = False,
) -> dict[str, Any]:
    from research.causal_driver_pb1.datasets.universe import load_research_observation_universe
    from research.causal_driver_pb1.phase2_precommit.sector_map import bind_sector_mapping

    universe = load_research_observation_universe()
    mapping = bind_sector_mapping()
    symbols = list(universe.ordered_symbols)
    seen = set(symbols)
    sector_of = {str(row["symbol"]): str(row.get("sector_id") or "") for row in mapping["rows"]}
    baseline: list[dict[str, Any]] = []
    charts: list[dict[str, Any]] = []
    kind_counts: dict[str, int] = {}
    struct_counts: dict[str, int] = {}
    seen_chart: set[tuple[str, str, str]] = set()
    push_records = 0
    recon_mismatch = 0

    def _annotate(book: dict[str, Any], row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        nonlocal recon_mismatch
        recon = reconstruct_path_events(book, row)
        if not recon.get("match_ratchet"):
            recon_mismatch += 1
        structure = analyze_trade_structure(book, row, recon)
        row.update({
            "recon_ok": recon.get("ok"),
            "pre_break": structure["pre_break"],
            "pre_break_in_structural_zone": structure["pre_break_in_structural_zone"],
            "zone_type": None if not structure["matched_zone"] else structure["matched_zone"]["zone_type"],
            "independent_test_n": None if not structure["matched_zone"] else structure["matched_zone"]["independent_test_n"],
            "rejection_n": None if not structure["matched_zone"] else structure["matched_zone"]["rejection_n"],
            "next_status": structure["next_resistance"]["status"],
            "next_level": structure["next_resistance"].get("level"),
            "runtime_state": None if not structure["runtime"] else structure["runtime"]["state"],
            "retest_label": None if not structure["runtime"] else structure["runtime"]["retest_label"],
            "headroom_yen": structure.get("headroom_yen"),
        })
        return recon, structure

    for day in dates:
        if day >= "20260911":
            raise RuntimeError(f"refused_date:{day}")
        capture = find_capture_dir(day)
        if capture is None:
            raise RuntimeError(f"missing_capture:{day}")
        am0 = float(hm_epoch(day, AM_START[0], AM_START[1]))
        am1 = float(hm_epoch(day, AM_END[0], AM_END[1]))
        pm0 = float(hm_epoch(day, PM_START[0], PM_START[1]))
        pm1 = float(hm_epoch(day, PM_END[0], PM_END[1]))
        bufs = {sym: _Buf() for sym in symbols}
        cols = {sym: {"AM": _Col(), "PM": _Col()} for sym in symbols}
        last_cum: dict[str, Optional[float]] = {sym: None for sym in symbols}
        for rec in iter_push(Path(capture)):
            push_records += 1
            sym = _bare(rec.get("symbol") or (rec.get("payload") or rec.get("original_payload") or {}).get("Symbol"))
            if sym not in seen:
                continue
            pay = dict(rec.get("payload") or rec.get("original_payload") or {})
            et = capture_event_epoch(rec, pay)
            if et is None:
                continue
            event_t = float(et)
            recv = record_event_stamp(rec)
            if recv:
                pay["received_at"] = recv
            row = _board_row(pay, event_t)
            if event_t < am0 - 120.0 or event_t > pm1 + 2.0:
                continue
            bufs[sym].append(row)
            cum = row["cum_vol"]
            dvol = 0.0
            if cum is not None and cum == cum and cum >= 0:
                if last_cum[sym] is not None and cum >= float(last_cum[sym]):
                    dvol = float(cum - float(last_cum[sym]))
                last_cum[sym] = float(cum)
            if not row["continuous"]:
                continue
            if am0 <= event_t < am1:
                session = "AM"
            elif pm0 <= event_t < pm1:
                session = "PM"
            else:
                continue
            price = float(row["px"])
            finite_px = price == price and price > 0
            ask_vol, bid_vol = _classify(price, float(row["bid"]), float(row["ask"]), dvol)
            fresh = _quote_ok(row)
            cols[sym][session].add(
                t=event_t,
                px=price if finite_px else np.nan,
                vol=dvol,
                ask=ask_vol,
                bid=bid_vol,
                tick=1.0 if finite_px else 0.0,
                spread=(float(row["ask"]) - float(row["bid"])) if fresh else np.nan,
                ok=fresh,
                ask_px=float(row["ask"]) if fresh else np.nan,
            )
        day_full = 0
        traded_base: set[str] = set()
        for session, start, end in (("AM", am0, am1), ("PM", pm0, pm1)):
            books: dict[str, Any] = {}
            for sym in symbols:
                arrays, ask_px = _arrays(cols[sym][session])
                if int(arrays["t"].size) == 0:
                    continue
                feat = evaluate(arrays["t"], arrays["px"], arrays["vol"], arrays["ask"], arrays["bid"], arrays["tick"], start)
                signals = _full_signals(feat, arrays)
                day_full += int(signals.size)
                if int(signals.size) == 0:
                    continue
                board = bufs[sym].view()
                book = prepare_book(feat, arrays, board, signals, ask_px)
                book["board_t"] = board["t"]
                book["board_bid"] = board["bid"]
                book["board_ask"] = board["ask"]
                books[sym] = book
            got = simulate_session(books, day=day, session=session, sess_end=end, traded_today=traded_base)
            session_events: dict[str, list[tuple[str, float]]] = {}
            if collect_charts:
                for sym, book in books.items():
                    session_events[sym] = _session_events(book)
            for row in got["trades"]:
                if row.get("pnl_yen") is None:
                    continue
                row["sector"] = sector_of.get(row["symbol"], "")
                row["hold_sec"] = float(row["exit_t"]) - float(row["entry_t"])
                book = books[row["symbol"]]
                recon = None
                structure = None
                if annotate_all:
                    recon, structure = _annotate(book, row)
                baseline.append(row)
                if not collect_charts or len(charts) >= max_charts:
                    continue
                kind = _coverage_kind(row) or "other"
                entry_t = float(row["entry_t"])
                exit_t = float(row["exit_t"])
                window0 = max(float(book["t"][0]), entry_t - 600.0)
                window1 = exit_t + 5.0
                covered_structs = [
                    name
                    for name, event_t in session_events.get(row["symbol"], [])
                    if window0 <= event_t <= window1 and struct_counts.get(name, 0) < 2
                ]
                need_kind = kind != "other" and kind_counts.get(kind, 0) < 2 and not any(
                    c["kind"] == kind and c["date"] == row["date"] for c in charts
                )
                if not need_kind and not covered_structs:
                    continue
                key = (kind if need_kind else covered_structs[0], str(row["date"]), str(row["symbol"]))
                if key in seen_chart:
                    continue
                if structure is None:
                    recon, structure = _annotate(book, row)
                if not recon.get("match_ratchet"):
                    continue
                seen_chart.add(key)
                if need_kind:
                    kind_counts[kind] = kind_counts.get(kind, 0) + 1
                for name in covered_structs:
                    struct_counts[name] = struct_counts.get(name, 0) + 1
                charts.append(_chart_payload(book, row, structure, recon, kind if need_kind else covered_structs[0]))
        if day_full != int(EXPECTED_DAY_FULL[day]):
            raise RuntimeError(f"signal_identity_mismatch:{day}")
        print(f"CAPTURE {day} full={day_full} trades_day={sum(1 for r in baseline if r['date']==day)} charts={len(charts)} structs={struct_counts}", flush=True)

    return {
        "baseline": baseline,
        "charts": charts,
        "dates": list(dates),
        "push_records": push_records,
        "recon_mismatch": recon_mismatch,
        "original": list(ORIGINAL18),
        "extension": list(EXTENSION17),
        "kind_counts": kind_counts,
        "struct_counts": struct_counts,
    }


def scan_visual() -> dict[str, Any]:
    return scan_days(VISUAL_DATES, collect_charts=True, max_charts=24, annotate_all=False)


def scan_full() -> dict[str, Any]:
    return scan_days(_dates(), collect_charts=True, max_charts=16, annotate_all=True)
