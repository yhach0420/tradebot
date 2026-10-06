"""LEGACY_DEV AM harvest. Ingress FSM. Canonical X1 / Bid exit. PreviousClose recapture."""
from __future__ import annotations

import gzip
import os
import pickle
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
from research.full_causal_mechanism_discovery_v1.exits import resolve_operator_exit
from research.new_entry_breakout_continuation_v1.harvest import BOARD_FRESHNESS_SEC as HARVEST_FRESH
from research.new_entry_breakout_continuation_v1.harvest import BoardTape, board_row
from research.post_open_prior_close_recapture_full_strategy_v1 import (
    BOARD_FRESHNESS_SEC,
    BURNED_HOLDOUT_DAYS,
    CANARY_ROW_ID,
    DEVELOPMENT_DAYS,
    EXEC_EVAL_ID,
    EXIT_ID,
    FORBIDDEN_INPUT_DAYS,
    MAX_RESEARCH_DATE,
    SESSION_FLATTEN_HM,
    STRESS_DAYS,
    STRATEGY_ID,
)
from research.post_open_prior_close_recapture_full_strategy_v1.fields import ingress_epoch
from research.post_open_prior_close_recapture_full_strategy_v1.fsm import PriorCloseFsm
from research.post_open_prior_close_recapture_full_strategy_v1.isolation import CACHE, TODAY
from research.recovery_sequence_full_strategy_architecture_v1.execution import evaluate_execution
from small_paper.v1r_live_dual_lane import session_end_for_position

AUDIT = {
    "HOLDOUT_BURNED_READ_N": 0,
    "STRESS_READ_N": 0,
    "FUTURE_DATA_N": 0,
    "CURRENT_PRICE_TIME_AS_BOARD_FRESH_N": 0,
    "QUEUE_ASSUMED_FILL_N": 0,
    "BAR_OHLC_FILL_N": 0,
    "TRADE_PRINT_PASSIVE_FILL_N": 0,
    "LOOKAHEAD_FILL_N": 0,
    "REPRICE_N": 0,
    "CHASE_N": 0,
    "NO_INGRESS_N": 0,
}


def _dump_gz(path: Path, body: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wb") as fh:
        pickle.dump(body, fh, protocol=4)


def _load_gz(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with gzip.open(path, "rb") as fh:
        got = pickle.load(fh)
    return got if isinstance(got, dict) else {}


def assert_dev_only_day(day: str) -> None:
    d = str(day)
    if d in STRESS_DAYS:
        AUDIT["STRESS_READ_N"] += 1
        raise RuntimeError("STRESS_READ")
    if d in BURNED_HOLDOUT_DAYS:
        AUDIT["HOLDOUT_BURNED_READ_N"] += 1
        raise RuntimeError("HOLDOUT_BURNED_READ")
    if d in FORBIDDEN_INPUT_DAYS or d > MAX_RESEARCH_DATE or d >= "20260903":
        AUDIT["FUTURE_DATA_N"] += 1
        raise RuntimeError(f"FUTURE:{d}")
    if d not in DEVELOPMENT_DAYS:
        AUDIT["FUTURE_DATA_N"] += 1
        raise RuntimeError(f"NON_DEV:{d}")


def sealed_dev_caps() -> tuple[list[dict[str, Any]], list[str]]:
    from _p1_inventory import resolve_universe

    out: list[dict[str, Any]] = []
    blockers: list[str] = []
    for day in DEVELOPMENT_DAYS:
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


def _absorb_flags(ex: dict[str, Any]) -> None:
    AUDIT["QUEUE_ASSUMED_FILL_N"] += int(ex.get("QUEUE_ASSUMED_FILL") or 0)
    AUDIT["BAR_OHLC_FILL_N"] += int(ex.get("BAR_OHLC_FILL") or 0)
    AUDIT["TRADE_PRINT_PASSIVE_FILL_N"] += int(ex.get("TRADE_PRINT_PASSIVE_FILL") or 0)
    AUDIT["LOOKAHEAD_FILL_N"] += int(ex.get("LOOKAHEAD_FILL") or 0)
    AUDIT["REPRICE_N"] += int(ex.get("REPRICE") or 0)
    AUDIT["CHASE_N"] += int(ex.get("CHASE") or 0)


def _x1(board: dict[str, Any], t0: float, am_end: float) -> tuple[dict[str, Any], bool]:
    ex = evaluate_execution(board, exec_id=EXEC_EVAL_ID, t0=float(t0), sess_end=float(am_end))
    _absorb_flags(ex)
    filled = bool(ex.get("WOULD_FILL"))
    if filled and ex.get("fill_t") is not None and float(ex["fill_t"]) + 1e-12 < float(t0):
        AUDIT["LOOKAHEAD_FILL_N"] += 1
        filled = False
        ex["WOULD_FILL"] = False
        ex["filled"] = False
    return ex, filled


def emit_filled(*, sig: dict[str, Any], board: dict[str, Any], fsm: PriorCloseFsm, am_end: float, flatten_t: float) -> dict[str, Any]:
    t0 = float(sig["t0"])
    flatten_reject = False
    ex, filled = _x1(board, t0, am_end)
    if filled and ex.get("fill_t") is not None and float(ex["fill_t"]) + 1e-12 >= float(flatten_t):
        flatten_reject = True
        filled = False
        ex["WOULD_FILL"] = False
        ex["filled"] = False
    tech: dict[str, Any] = {
        "exit_t": None,
        "exit_price": None,
        "exit_reason": "EXIT_MISS",
        "pnl_yen_100": None,
        "miss": True,
        "session_walkback": False,
        "trigger_t": None,
        "trigger_i": None,
    }
    fire_t = None
    expire_t = None
    if filled and ex.get("fill_t") is not None and ex.get("fill_price") is not None:
        fire_t = fsm.first_exit_fire(fill_t=float(ex["fill_t"]), anchor=float(sig["anchor"]))
        fire_i = None
        raw = {"finalize_t": np.asarray([], dtype=float), "close": np.asarray([], dtype=float)}
        if fire_t is not None:
            raw = {
                "finalize_t": np.asarray([float(fire_t)], dtype=float),
                "close": np.asarray([float(sig["anchor"])], dtype=float),
            }
            fire_i = 0
        tech = resolve_operator_exit(
            board,
            raw,
            exit_id=EXIT_ID,
            fill_t=float(ex["fill_t"]),
            fill_px=float(ex["fill_price"]),
            sess_end=am_end,
            flatten_t=flatten_t,
            fire_i=fire_i,
        )
    else:
        expire_t = fsm.first_below_after(t0=t0, anchor=float(sig["anchor"]))
    return {
        "date": sig["date"],
        "session": "AM",
        "symbol": sig["symbol"],
        "t0": t0,
        "signal_t0": t0,
        "candidate_id": STRATEGY_ID,
        "STRATEGY_ID": STRATEGY_ID,
        "thesis": sig.get("thesis"),
        "episode_id": sig.get("episode_id"),
        "episode_seq": sig.get("episode_seq"),
        "exec_id": EXEC_EVAL_ID,
        "exit_id": sig.get("exit_id"),
        "anchor": sig.get("anchor"),
        "px": sig.get("px"),
        "ORDERED": bool(ex.get("ORDERED")),
        "WOULD_FILL": filled,
        "fill_t": ex.get("fill_t") if filled else None,
        "fill_price": ex.get("fill_price") if filled else None,
        "exit_t": tech.get("exit_t"),
        "exit_price": tech.get("exit_price"),
        "exit_reason": tech.get("exit_reason"),
        "pnl_yen_100": tech.get("pnl_yen_100"),
        "hold_sec": tech.get("hold_sec"),
        "mfe_yen": tech.get("mfe_yen"),
        "mae_yen": tech.get("mae_yen"),
        "trigger_t": tech.get("trigger_t") if fire_t is not None else None,
        "trigger_i": tech.get("trigger_i"),
        "spread0": ex.get("spread0"),
        "session_walkback": bool(tech.get("session_walkback")),
        "flatten_reject": flatten_reject,
        "pre_fill_expire": expire_t is not None,
        "SELECTABLE": True,
        "Z3_CANDIDATE": False,
        "technical_exit_fire": fire_t is not None,
    }


def summarize(episodes: list[dict[str, Any]], signals: list[dict[str, Any]], stats: dict[str, int], rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    days = {str(e.get("date")) for e in episodes}
    syms = {str(e.get("symbol")) for e in episodes}
    sig_days = {str(s.get("date")) for s in signals}
    sig_syms = {str(s.get("symbol")) for s in signals}
    expire_n = sum(1 for r in (rows or []) if r.get("pre_fill_expire"))
    return {
        "prev_valid_n": int(stats.get("prev_valid_n") or 0),
        "prev_change_n": int(stats.get("prev_change_n") or 0),
        "below_event_n": int(stats.get("below_event_n") or 0),
        "eligible_event_n": len(signals),
        "event_day_n": len(sig_days),
        "event_symbol_n": len(sig_syms),
        "below_regime_n": len(episodes),
        "below_day_n": len(days),
        "below_symbol_n": len(syms),
        "valid_pre_event_price_n": len(episodes),
        "signal_n": len(signals),
        "signal_day_n": len(sig_days),
        "signal_symbol_n": len(sig_syms),
        "pre_fill_normal_return_expire_n": int(expire_n),
        "special_quote_overlap_n": int(stats.get("special_overlap_n") or 0),
    }


def process_dev_day(payload: dict[str, Any], *, exclude_symbol: str | None = None) -> dict[str, Any]:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"
    day = str(payload["date"])
    try:
        assert_dev_only_day(day)
    except RuntimeError as exc:
        return {"ok": False, "date": day, "blocker": str(exc)}
    if abs(float(BOARD_FRESHNESS_SEC) - float(E1_FRESH)) > 1e-12:
        return {"ok": False, "date": day, "blocker": "BOARD_FRESHNESS_DRIFT"}
    if abs(float(HARVEST_FRESH) - float(BOARD_FRESHNESS_SEC)) > 1e-12:
        return {"ok": False, "date": day, "blocker": "HARVEST_FRESHNESS_DRIFT"}
    capture = Path(payload["capture_path"])
    universe = [_bare(s) for s in list(payload["universe"]) if _bare(s)]
    drop = _bare(exclude_symbol) if exclude_symbol else ""
    if drop:
        universe = [s for s in universe if s != drop]
    uni = set(universe)
    t0w = time.perf_counter()
    leak = {
        "CURRENT_PRICE_TIME_AS_BOARD_FRESH_N": 0,
        "UNIVERSE_SKIP_N": 0,
        "NO_EVENT_TIME_N": 0,
        "NO_INGRESS_N": 0,
    }
    try:
        am_start = float(hm_epoch(day, 9, 0))
        am_end = float(session_end_for_position(date=day, session="AM", fill_time=am_start + 60.0))
        flatten_t = float(hm_epoch(day, int(SESSION_FLATTEN_HM[0]), int(SESSION_FLATTEN_HM[1])))
        bufs: dict[str, BoardTape] = {s: BoardTape() for s in universe}
        fsms: dict[str, PriorCloseFsm] = {
            s: PriorCloseFsm(symbol=s, date=day, flatten_t=flatten_t, am_start=am_start) for s in universe
        }
        events_n = 0
        last_et: Optional[float] = None
        for rec in iter_push(capture):
            sym = _bare(rec.get("symbol") or (rec.get("payload") or rec.get("original_payload") or {}).get("Symbol"))
            if not sym or sym not in uni:
                leak["UNIVERSE_SKIP_N"] += 1
                continue
            pay = dict(rec.get("payload") or rec.get("original_payload") or {})
            ing = ingress_epoch(rec, pay)
            et = capture_event_epoch(rec, pay)
            if et is None and ing is None:
                leak["NO_EVENT_TIME_N"] += 1
                continue
            board_t = float(et if et is not None else ing)
            if float(board_t) < am_start - 120.0:
                continue
            if float(board_t) > am_end + 2.0:
                continue
            recv = record_event_stamp(rec)
            if recv:
                pay["received_at"] = recv
            last_et = float(board_t)
            events_n += 1
            row = board_row(rec, pay, float(board_t))
            if str(row.get("fresh_source") or "") not in ("BOARD_EVENT_TIME", "INGRESS_RECEIVED_AT", "UNRESOLVED"):
                leak["CURRENT_PRICE_TIME_AS_BOARD_FRESH_N"] += 1
            bufs[sym].append(row)
            if ing is None:
                leak["NO_INGRESS_N"] += 1
                AUDIT["NO_INGRESS_N"] += 1
                continue
            if float(ing) < am_start - 120.0 or float(ing) > am_end + 2.0:
                continue
            fsms[sym].process(ingress_t=float(ing), payload=pay)
            if events_n % 400000 == 0:
                print(f"{day} PCLOSE events={events_n} last_et={last_et} ex={drop or '-'}", flush=True)

        episodes: list[dict[str, Any]] = []
        raw_signals: list[dict[str, Any]] = []
        rows: list[dict[str, Any]] = []
        integrity_n = 0
        stats = {
            "prev_valid_n": 0,
            "prev_change_n": 0,
            "below_event_n": 0,
            "special_overlap_n": 0,
        }
        for s in universe:
            integrity_n += int(fsms[s].integrity_n)
            stats["prev_valid_n"] += int(fsms[s].prev_valid_n)
            stats["prev_change_n"] += int(fsms[s].prev_change_n)
            stats["below_event_n"] += int(fsms[s].below_event_n)
            stats["special_overlap_n"] += int(fsms[s].sq_on_signal_n)
            episodes.extend(list(fsms[s].episodes))
            board = bufs[s].view()
            for sig in fsms[s].signals:
                raw_signals.append(sig)
                rows.append(emit_filled(sig=sig, board=board, fsm=fsms[s], am_end=am_end, flatten_t=flatten_t))
        AUDIT["CURRENT_PRICE_TIME_AS_BOARD_FRESH_N"] += int(leak["CURRENT_PRICE_TIME_AS_BOARD_FRESH_N"])
        struct = summarize(episodes, raw_signals, stats, rows)
        print(
            f"{day} PCLOSE DEVELOPMENT events={events_n} below={struct['below_regime_n']} "
            f"sig={struct['signal_n']} integ={integrity_n} ex={drop or '-'}",
            flush=True,
        )
        ok = int(leak["CURRENT_PRICE_TIME_AS_BOARD_FRESH_N"]) == 0
        return {
            "ok": ok,
            "date": day,
            "rows_by": {STRATEGY_ID: rows},
            "episodes": episodes,
            "signals": raw_signals,
            "structural": struct,
            "integrity_n": integrity_n,
            "stats": stats,
            "leak": leak,
            "events_n": events_n,
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
            "blocker": None if ok else "FRESHNESS_LEAK",
            "exclude_symbol": drop or None,
        }
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }


def _cache_path(day: str, exclude_symbol: str | None) -> Path:
    if exclude_symbol:
        return CACHE / f"PCLOSE_{day}_ex_{_bare(exclude_symbol)}.pkl.gz"
    return CACHE / f"PCLOSE_{day}.pkl.gz"


def harvest_pclose(*, exclude_symbol: str | None = None) -> dict[str, Any]:
    CACHE.mkdir(parents=True, exist_ok=True)
    invs, blockers = sealed_dev_caps()
    if blockers:
        return {"ok": False, "blocker": "CAPTURE", "blockers": blockers, "day_ok": {}}
    rows_by: dict[str, list[dict[str, Any]]] = {STRATEGY_ID: []}
    episodes: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    day_ok: dict[str, bool] = {}
    integrity_n = 0
    stats = {"prev_valid_n": 0, "prev_change_n": 0, "below_event_n": 0, "special_overlap_n": 0}
    drop = _bare(exclude_symbol) if exclude_symbol else None
    all_rows: list[dict[str, Any]] = []
    for inv in invs:
        day = str(inv["date"])
        cache = _cache_path(day, drop)
        saved = _load_gz(cache)
        if saved.get("ok") and str(saved.get("date") or "") == day and saved.get("rows_by"):
            for c, xs in (saved.get("rows_by") or {}).items():
                rows_by.setdefault(c, []).extend(list(xs or []))
                all_rows.extend(list(xs or []))
            episodes.extend(list(saved.get("episodes") or []))
            signals.extend(list(saved.get("signals") or []))
            integrity_n += int(saved.get("integrity_n") or 0)
            for k in stats:
                stats[k] += int((saved.get("stats") or {}).get(k) or 0)
            day_ok[day] = True
            print(f"cache-hit PCLOSE {day} ex={drop or '-'}", flush=True)
            continue
        body = process_dev_day(
            {"date": day, "capture_path": inv["capture_path"], "universe": list(inv["universe_symbols"])},
            exclude_symbol=drop,
        )
        day_ok[day] = bool(body.get("ok"))
        if not body.get("ok"):
            return {"ok": False, "blocker": f"DEVELOPMENT:{day}:{body.get('blocker')}", "day_ok": day_ok}
        _dump_gz(
            cache,
            {
                "ok": True,
                "date": day,
                "rows_by": body.get("rows_by"),
                "episodes": body.get("episodes"),
                "signals": body.get("signals"),
                "integrity_n": body.get("integrity_n"),
                "stats": body.get("stats"),
                "structural": body.get("structural"),
                "leak": body.get("leak"),
            },
        )
        for c, xs in (body.get("rows_by") or {}).items():
            rows_by.setdefault(c, []).extend(list(xs or []))
            all_rows.extend(list(xs or []))
        episodes.extend(list(body.get("episodes") or []))
        signals.extend(list(body.get("signals") or []))
        integrity_n += int(body.get("integrity_n") or 0)
        for k in stats:
            stats[k] += int((body.get("stats") or {}).get(k) or 0)
    struct = summarize(episodes, signals, stats, all_rows)
    return {
        "ok": True,
        "rows_by": rows_by,
        "episodes": episodes,
        "signals": signals,
        "structural": struct,
        "integrity_n": integrity_n,
        "stats": stats,
        "day_ok": day_ok,
        "audit": dict(AUDIT),
    }


assert CANARY_ROW_ID
assert EXIT_ID
