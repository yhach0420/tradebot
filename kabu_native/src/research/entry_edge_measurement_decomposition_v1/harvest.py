"""DEV-only quote stream. Reuse existing family signal keys. Never open Holdout/Stress capture."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

import numpy as np

NATIVE = Path(__file__).resolve().parents[3]
if str(NATIVE / "src") not in sys.path:
    sys.path.insert(0, str(NATIVE / "src"))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import (
    _bare,
    capture_event_epoch,
    find_capture_dir,
    iter_push,
    record_event_stamp,
)
from research.e1_x28_executable_joint import BOARD_FRESHNESS_SEC as E1_FRESH
from research.entry_edge_measurement_decomposition_v1 import (
    DEVELOPMENT_DAYS,
    FORBIDDEN_INPUT_DAYS,
    HORIZONS_SEC,
    LOCKED_HOLDOUT_DAYS,
    MAX_RESEARCH_DATE,
    STRESS_DAYS,
)
from research.entry_edge_measurement_decomposition_v1.isolation import BREAKOUT_CACHE, CACHE, TODAY, VWAP_CACHE
from research.entry_edge_measurement_decomposition_v1.quotes import (
    bps_ratio,
    entry_half_spread_bps,
    exit_half_spread_bps,
    last_ask_before,
    mid,
    residual_ask_bid,
    spread_bps,
)
from research.new_entry_breakout_continuation_v1.harvest import (
    BOARD_FRESHNESS_SEC,
    BoardTape,
    ask_entry_ok,
    board_row,
    last_bid_before,
    snap_at,
)
from research.simple_tech_entry_family.bars import SymbolBarBuilder, bar_integrity
from small_paper.v1r_live_dual_lane import session_end_for_position

AUDIT = {
    "LOCKED_HOLDOUT_READ_N": 0,
    "STRESS_READ_N": 0,
    "FUTURE_DATA_N": 0,
    "CURRENT_PRICE_TIME_AS_BOARD_FRESH_N": 0,
    "SIGNAL_KEY_MISMATCH_N": 0,
    "ASK_BID_PARITY_FAIL_N": 0,
    "SPLIT_LEAKAGE_N": 0,
    "RULE_CHANGE_N": 0,
}


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _dump(path: Path, body: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, ensure_ascii=False, default=str), encoding="utf-8")


def assert_dev_only_day(day: str) -> None:
    d = str(day)
    if d in LOCKED_HOLDOUT_DAYS:
        AUDIT["LOCKED_HOLDOUT_READ_N"] += 1
        raise RuntimeError("LOCKED_HOLDOUT_READ")
    if d in STRESS_DAYS:
        AUDIT["STRESS_READ_N"] += 1
        raise RuntimeError("STRESS_READ")
    if d in FORBIDDEN_INPUT_DAYS or d > MAX_RESEARCH_DATE:
        AUDIT["FUTURE_DATA_N"] += 1
        raise RuntimeError(f"FUTURE:{d}")
    if d not in DEVELOPMENT_DAYS:
        AUDIT["FUTURE_DATA_N"] += 1
        raise RuntimeError(f"NON_DEV:{d}")


def load_family_day(family: str, day: str) -> list[dict[str, Any]]:
    assert_dev_only_day(day)
    if family == "BREAKOUT":
        path = BREAKOUT_CACHE / f"DEVELOPMENT_{day}_signals.json"
    elif family == "VWAP":
        path = VWAP_CACHE / f"PRIMARY_DEVELOPMENT_{day}_signals.json"
    else:
        raise RuntimeError(f"UNKNOWN_FAMILY:{family}")
    body = _load(path)
    if not body.get("ok"):
        raise RuntimeError(f"SIGNAL_CACHE_MISSING:{family}:{day}")
    rows = list(body.get("signals") or [])
    for r in rows:
        if str(r.get("date") or "") != str(day):
            AUDIT["SIGNAL_KEY_MISMATCH_N"] += 1
    return rows


def sealed_caps(days: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    from _p1_inventory import resolve_universe

    out: list[dict[str, Any]] = []
    blockers: list[str] = []
    for day in days:
        try:
            assert_dev_only_day(str(day))
        except RuntimeError as exc:
            blockers.append(str(exc))
            continue
        if str(day) == str(TODAY):
            blockers.append(f"ACTIVE_DAY:{day}")
            continue
        cap = find_capture_dir(str(day))
        uni = resolve_universe(str(day), cap) if cap is not None else {}
        rec = {
            "date": str(day),
            "capture_path": str(cap) if cap is not None else "",
            "universe_symbols": list(uni.get("symbols") or []),
            "ok": cap is not None and bool(uni.get("symbols")),
        }
        if not rec["ok"]:
            blockers.append(f"CAPTURE_OR_UNIVERSE_MISSING:{day}")
        out.append(rec)
    return out, blockers


def _horizon_quotes(board: dict[str, np.ndarray], *, t0: float, h: int, am_end: float) -> dict[str, Any]:
    mark_t = min(float(t0) + float(h), float(am_end))
    bidh, bt = last_bid_before(board, t0=float(t0), mark_t=mark_t)
    askh, at = last_ask_before(board, t0=float(t0), mark_t=mark_t)
    mh = mid(bidh, askh)
    return {
        "bidh": bidh,
        "askh": askh,
        "midh": mh,
        "bidh_t": bt,
        "askh_t": at,
        "spread_h": spread_bps(bidh, askh),
        "exit_hs": exit_half_spread_bps(bidh, askh),
    }


def attach_economics(base: dict[str, Any], board: dict[str, np.ndarray], *, t0: float, am_end: float, orig: dict[str, Any] | None) -> dict[str, Any]:
    snap = snap_at(board, float(t0))
    ok, reason = ask_entry_ok(snap, require_qty=False)
    rec = dict(base)
    rec["t0"] = float(t0)
    rec["eligible"] = bool(ok)
    rec["ask_reason"] = reason
    rec["bid0"] = float(snap["bid"]) if snap.get("ok") else None
    rec["ask0"] = float(snap["ask"]) if snap.get("ok") else None
    rec["mid0"] = mid(rec.get("bid0"), rec.get("ask0")) if ok else None
    rec["spread0"] = spread_bps(rec.get("bid0"), rec.get("ask0")) if ok else None
    rec["entry_hs"] = entry_half_spread_bps(rec.get("bid0"), rec.get("ask0")) if ok else None
    rec["fresh_source"] = snap.get("fresh_source") if snap.get("ok") else None
    if orig is not None and orig.get("executable_signal") and orig.get("ask_t0") is not None and rec.get("ask0") is not None:
        if abs(float(orig["ask_t0"]) - float(rec["ask0"])) > 1e-6:
            AUDIT["SIGNAL_KEY_MISMATCH_N"] += 1
    if not ok or rec.get("mid0") is None:
        for h in HORIZONS_SEC:
            rec[f"MID_{int(h)}"] = None
            rec[f"ASK_MID_{int(h)}"] = None
            rec[f"ASK_BID_{int(h)}"] = None
            rec[f"SPREAD_{int(h)}"] = None
            rec[f"EXIT_HS_{int(h)}"] = None
            rec[f"RESIDUAL_{int(h)}"] = None
        return rec
    ask0 = float(rec["ask0"])
    mid0 = float(rec["mid0"])
    for h in HORIZONS_SEC:
        q = _horizon_quotes(board, t0=float(t0), h=int(h), am_end=am_end)
        rec[f"SPREAD_{int(h)}"] = q["spread_h"]
        rec[f"EXIT_HS_{int(h)}"] = q["exit_hs"]
        rec[f"MID_{int(h)}"] = bps_ratio(q["midh"], mid0)
        rec[f"ASK_MID_{int(h)}"] = bps_ratio(q["midh"], ask0)
        rec[f"ASK_BID_{int(h)}"] = bps_ratio(q["bidh"], ask0)
        rec[f"RESIDUAL_{int(h)}"] = residual_ask_bid(
            rec[f"MID_{int(h)}"],
            rec[f"ASK_BID_{int(h)}"],
            rec.get("entry_hs"),
            q["exit_hs"],
        )
        if orig is not None and orig.get("executable_signal"):
            old = orig.get(f"markout_{int(h)}")
            neu = rec[f"ASK_BID_{int(h)}"]
            if old is not None and neu is not None and abs(float(old) - float(neu)) > 1e-4:
                AUDIT["ASK_BID_PARITY_FAIL_N"] += 1
    return rec


def process_day(payload: dict[str, Any]) -> dict[str, Any]:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"
    day = str(payload["date"])
    try:
        assert_dev_only_day(day)
    except RuntimeError as exc:
        return {"ok": False, "date": day, "blocker": str(exc)}
    if abs(float(BOARD_FRESHNESS_SEC) - float(E1_FRESH)) > 1e-12:
        return {"ok": False, "date": day, "blocker": "BOARD_FRESHNESS_DRIFT"}
    brk = list(payload.get("breakout_signals") or [])
    vwp = list(payload.get("vwap_signals") or [])
    capture = Path(payload["capture_path"])
    universe = [_bare(s) for s in list(payload["universe"]) if _bare(s)]
    uni = set(universe)
    t0w = time.perf_counter()
    leak = {
        "FUTURE_BAR_N": 0,
        "BAR_INTEG_FAIL_N": 0,
        "CURRENT_PRICE_TIME_AS_BOARD_FRESH_N": 0,
        "UNIVERSE_SKIP_N": 0,
        "NO_EVENT_TIME_N": 0,
        "UNMATCHED_BREAKOUT_N": 0,
        "UNMATCHED_VWAP_N": 0,
    }
    try:
        am_start = float(hm_epoch(day, 9, 0))
        am_end = float(session_end_for_position(date=day, session="AM", fill_time=am_start + 60.0))
        bufs: dict[str, BoardTape] = {s: BoardTape() for s in universe}
        builders: dict[str, SymbolBarBuilder] = {s: SymbolBarBuilder(am_start=am_start, am_end=am_end) for s in universe}
        events_n = 0
        last_et: Optional[float] = None
        for rec in iter_push(capture):
            sym = _bare(rec.get("symbol") or (rec.get("payload") or rec.get("original_payload") or {}).get("Symbol"))
            if not sym or sym not in uni:
                leak["UNIVERSE_SKIP_N"] += 1
                continue
            pay = dict(rec.get("payload") or rec.get("original_payload") or {})
            et = capture_event_epoch(rec, pay)
            if et is None:
                leak["NO_EVENT_TIME_N"] += 1
                continue
            if float(et) < am_start - 120.0:
                continue
            if float(et) > am_end + 2.0:
                continue
            recv = record_event_stamp(rec)
            if recv:
                pay["received_at"] = recv
            last_et = float(et)
            events_n += 1
            row = board_row(rec, pay, float(et))
            if str(row.get("fresh_source") or "") not in ("BOARD_EVENT_TIME", "INGRESS_RECEIVED_AT", "UNRESOLVED"):
                leak["CURRENT_PRICE_TIME_AS_BOARD_FRESH_N"] += 1
            bufs[sym].append(row)
            builders[sym].on_event(
                et=float(et),
                px=row["px"] if row["px"] == row["px"] else None,
                cum_vol=row.get("cum_vol"),
                bid=row["bid"] if row["bid"] == row["bid"] else None,
                ask=row["ask"] if row["ask"] == row["ask"] else None,
                continuous=bool(row.get("continuous")),
            )
            if events_n % 400000 == 0:
                print(f"{day} stream events={events_n} last_et={last_et}", flush=True)

        brk_by: dict[str, dict[int, dict[str, Any]]] = {}
        vwp_by: dict[str, dict[int, dict[str, Any]]] = {}
        for r in brk:
            brk_by.setdefault(str(r.get("symbol") or ""), {})[int(r.get("i"))] = r
        for r in vwp:
            vwp_by.setdefault(str(r.get("symbol") or ""), {})[int(r.get("i"))] = r
        brk_hit: set[tuple[str, int]] = set()
        vwp_hit: set[tuple[str, int]] = set()
        out_brk: list[dict[str, Any]] = []
        out_vwp: list[dict[str, Any]] = []
        out_unc: list[dict[str, Any]] = []

        for s in universe:
            builders[s].close_session()
            raw = builders[s].as_arrays()
            integ = bar_integrity(raw, am_start=am_start, am_end=am_end)
            leak["FUTURE_BAR_N"] += int(integ.get("FUTURE_BAR_N") or 0)
            if not integ.get("ok"):
                leak["BAR_INTEG_FAIL_N"] += 1
                continue
            n = int(raw["close"].size)
            if n == 0:
                continue
            board = bufs[s].view()
            for i in range(n):
                t0 = float(raw["finalize_t"][i])
                base = {"date": day, "session": "AM", "symbol": s, "i": i}
                unc = attach_economics(dict(base), board, t0=t0, am_end=am_end, orig=None)
                if unc.get("eligible"):
                    out_unc.append(unc)
                orig_b = (brk_by.get(s) or {}).get(i)
                if orig_b is not None:
                    if abs(float(orig_b.get("t0") or 0.0) - t0) > 1e-3:
                        AUDIT["SIGNAL_KEY_MISMATCH_N"] += 1
                    out_brk.append(attach_economics({**base, "orig_executable": bool(orig_b.get("executable_signal"))}, board, t0=t0, am_end=am_end, orig=orig_b))
                    brk_hit.add((s, i))
                orig_v = (vwp_by.get(s) or {}).get(i)
                if orig_v is not None:
                    if abs(float(orig_v.get("t0") or 0.0) - t0) > 1e-3:
                        AUDIT["SIGNAL_KEY_MISMATCH_N"] += 1
                    out_vwp.append(attach_economics({**base, "orig_executable": bool(orig_v.get("executable_signal"))}, board, t0=t0, am_end=am_end, orig=orig_v))
                    vwp_hit.add((s, i))

        for r in brk:
            if (str(r.get("symbol") or ""), int(r.get("i"))) not in brk_hit:
                leak["UNMATCHED_BREAKOUT_N"] += 1
                AUDIT["SIGNAL_KEY_MISMATCH_N"] += 1
        for r in vwp:
            if (str(r.get("symbol") or ""), int(r.get("i"))) not in vwp_hit:
                leak["UNMATCHED_VWAP_N"] += 1
                AUDIT["SIGNAL_KEY_MISMATCH_N"] += 1

        AUDIT["CURRENT_PRICE_TIME_AS_BOARD_FRESH_N"] += int(leak["CURRENT_PRICE_TIME_AS_BOARD_FRESH_N"])
        print(
            f"{day} DEVELOPMENT events={events_n} breakout={len(out_brk)} vwap={len(out_vwp)} "
            f"uncond_eligible={sum(1 for r in out_unc if r.get('eligible'))} last_et={last_et}",
            flush=True,
        )
        ok = (
            int(leak["BAR_INTEG_FAIL_N"]) == 0
            and int(leak["FUTURE_BAR_N"]) == 0
            and int(leak["CURRENT_PRICE_TIME_AS_BOARD_FRESH_N"]) == 0
            and int(leak["UNMATCHED_BREAKOUT_N"]) == 0
            and int(leak["UNMATCHED_VWAP_N"]) == 0
        )
        return {
            "ok": ok,
            "date": day,
            "breakout": out_brk,
            "vwap": out_vwp,
            "unconditional": out_unc,
            "leak": leak,
            "events_n": events_n,
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
            "blocker": None if ok else "BAR_OR_SIGNAL_REUSE_LEAK",
        }
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }


def harvest_development() -> dict[str, Any]:
    CACHE.mkdir(parents=True, exist_ok=True)
    invs, blockers = sealed_caps(list(DEVELOPMENT_DAYS))
    if blockers:
        return {"ok": False, "blocker": "CAPTURE", "blockers": blockers, "inventory": invs}
    brk_all: list[dict[str, Any]] = []
    vwp_all: list[dict[str, Any]] = []
    unc_all: list[dict[str, Any]] = []
    brk_sig_n = 0
    vwp_sig_n = 0
    day_meta: list[dict[str, Any]] = []
    for inv in invs:
        day = str(inv["date"])
        cache = CACHE / f"DEVELOPMENT_{day}_decomp.json"
        brk_day = load_family_day("BREAKOUT", day)
        vwp_day = load_family_day("VWAP", day)
        brk_sig_n += len(brk_day)
        vwp_sig_n += len(vwp_day)
        saved = _load(cache)
        if saved.get("ok") and str(saved.get("date") or "") == day:
            brk_all.extend(list(saved.get("breakout") or []))
            vwp_all.extend(list(saved.get("vwap") or []))
            unc_all.extend(list(saved.get("unconditional") or []))
            day_meta.append({"date": day, "cache": True})
            print(f"cache-hit DEVELOPMENT {day}", flush=True)
            continue
        body = process_day(
            {
                "date": day,
                "capture_path": inv["capture_path"],
                "universe": list(inv["universe_symbols"]),
                "breakout_signals": brk_day,
                "vwap_signals": vwp_day,
            }
        )
        if not body.get("ok"):
            return {"ok": False, "blocker": f"DEVELOPMENT:{day}:{body.get('blocker')}", "inventory": invs}
        _dump(cache, body)
        brk_all.extend(list(body.get("breakout") or []))
        vwp_all.extend(list(body.get("vwap") or []))
        unc_all.extend(list(body.get("unconditional") or []))
        day_meta.append({"date": day, "cache": False, "leak": body.get("leak")})
    return {
        "ok": True,
        "breakout": brk_all,
        "vwap": vwp_all,
        "unconditional": unc_all,
        "breakout_signal_n": brk_sig_n,
        "vwap_signal_n": vwp_sig_n,
        "day_meta": day_meta,
        "audit": dict(AUDIT),
    }
