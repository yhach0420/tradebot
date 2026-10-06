"""LEGACY_DEV AM harvest. Ingress FSM. Canonical X1 / Bid exit. No Sign direction."""
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
from research.full_causal_mechanism_discovery_v1.exits import post_fill_bars, resolve_operator_exit
from research.intraday_special_quote_resolution_full_strategy_v1 import (
    BOARD_FRESHNESS_SEC,
    BURNED_HOLDOUT_DAYS,
    CANDIDATE_C,
    CANDIDATE_D,
    CANDIDATE_U,
    CANARY_ROW_ID,
    DEVELOPMENT_DAYS,
    EXEC_EVAL_ID,
    EXIT_D,
    EXIT_U,
    FORBIDDEN_INPUT_DAYS,
    MAX_RESEARCH_DATE,
    SESSION_FLATTEN_HM,
    STRESS_DAYS,
    THESIS_D,
    THESIS_U,
)
from research.intraday_special_quote_resolution_full_strategy_v1.fsm import IsqSymbolFsm, first_anchor_loss
from research.intraday_special_quote_resolution_full_strategy_v1.isolation import CACHE, TODAY
from research.intraday_special_quote_resolution_full_strategy_v1.trade import ingress_epoch
from research.new_entry_breakout_continuation_v1.harvest import BOARD_FRESHNESS_SEC as HARVEST_FRESH
from research.new_entry_breakout_continuation_v1.harvest import BoardTape, board_row
from research.recovery_sequence_full_strategy_architecture_v1.execution import evaluate_execution
from small_paper.v1r_live_dual_lane import session_end_for_position

AUDIT = {
    "HOLDOUT_BURNED_READ_N": 0,
    "STRESS_READ_N": 0,
    "FUTURE_DATA_N": 0,
    "CURRENT_PRICE_TIME_AS_BOARD_FRESH_N": 0,
    "CURRENT_PRICE_TIME_AS_TRADE_ID_N": 0,
    "CURRENT_PRICE_TIME_AS_AVAILABILITY_N": 0,
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


def emit_filled(
    *,
    sig: dict[str, Any],
    board: dict[str, Any],
    bars: list[dict[str, Any]],
    am_end: float,
    flatten_t: float,
    strategy_id: str,
) -> dict[str, Any]:
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
    }
    if filled and ex.get("fill_t") is not None and ex.get("fill_price") is not None:
        fin = np.asarray([float(b["finalize_t"]) for b in bars if b.get("finalize_t") is not None], dtype=float)
        close = np.asarray([float(b["close"]) for b in bars if b.get("finalize_t") is not None], dtype=float)
        raw = {"finalize_t": fin, "close": close}
        fire_i = None
        if int(fin.size) > 0:
            idxs = post_fill_bars(fin, float(ex["fill_t"]))
            fire_i = first_anchor_loss(
                [{"finalize_t": float(fin[i]), "close": float(close[i])} for i in idxs],
                after_t=float(ex["fill_t"]),
                anchor=float(sig["anchor"]),
            )
            if fire_i is not None:
                fire_i = int(idxs[int(fire_i)])
        tech = resolve_operator_exit(
            board,
            raw,
            exit_id=str(sig["exit_id"]),
            fill_t=float(ex["fill_t"]),
            fill_px=float(ex["fill_price"]),
            sess_end=am_end,
            flatten_t=flatten_t,
            fire_i=fire_i,
        )
    return {
        "date": sig["date"],
        "session": "AM",
        "symbol": sig["symbol"],
        "t0": t0,
        "signal_t0": t0,
        "candidate_id": strategy_id,
        "STRATEGY_ID": strategy_id,
        "thesis": sig.get("thesis"),
        "episode_id": sig.get("episode_id"),
        "episode_seq": sig.get("episode_seq"),
        "exec_id": EXEC_EVAL_ID,
        "exit_id": sig.get("exit_id"),
        "anchor": sig.get("anchor"),
        "release_price": sig.get("release_price"),
        "pre_special_price": sig.get("pre_special_price"),
        "direction": sig.get("direction"),
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
        "trigger_t": tech.get("trigger_t"),
        "trigger_i": tech.get("trigger_i"),
        "spread0": ex.get("spread0"),
        "session_walkback": bool(tech.get("session_walkback")),
        "flatten_reject": flatten_reject,
        "SELECTABLE": True,
        "Z3_CANDIDATE": False,
    }


def summarize_episodes(episodes: list[dict[str, Any]], signals: list[dict[str, Any]]) -> dict[str, Any]:
    days = {str(e.get("date")) for e in episodes}
    syms = {str(e.get("symbol")) for e in episodes}
    pre_ok = sum(1 for e in episodes if e.get("PRE_SPECIAL_REFERENCE_VALID"))
    coobs = sum(1 for e in episodes if e.get("special_start_coobserved"))
    conflict = sum(1 for e in episodes if e.get("special_active_trade_conflict"))
    ret = sum(1 for e in episodes if e.get("return_continuous_ingress") is not None or e.get("release_price") is not None)
    rel = sum(1 for e in episodes if e.get("release_price") is not None)
    up = sum(1 for e in episodes if e.get("direction") == "UP_RELEASE")
    down = sum(1 for e in episodes if e.get("direction") == "DOWN_RELEASE")
    flat = sum(1 for e in episodes if e.get("direction") == "FLAT_RELEASE")
    acc = sum(1 for e in episodes if (e.get("accept_bar") or {}).get("TRADE_UPDATE_N"))
    no_acc = sum(1 for e in episodes if e.get("no_entry_reason") == "NO_ACCEPTANCE_EVIDENCE")
    reint = sum(1 for e in episodes if e.get("reinterrupted"))
    u_sig = sum(1 for s in signals if s.get("thesis") == THESIS_U)
    d_sig = sum(1 for s in signals if s.get("thesis") == THESIS_D)
    return {
        "ISQ_EPISODE_N": len(episodes),
        "ISQ_EPISODE_DAY_N": len(days),
        "ISQ_EPISODE_SYMBOL_N": len(syms),
        "PRE_SPECIAL_VALID_N": pre_ok,
        "SPECIAL_START_COOBSERVED_TRADE_N": coobs,
        "SPECIAL_ACTIVE_TRADE_CONFLICT_N": conflict,
        "RETURN_CONTINUOUS_N": ret,
        "RELEASE_VALID_N": rel,
        "UP_RELEASE_N": up,
        "DOWN_RELEASE_N": down,
        "FLAT_RELEASE_N": flat,
        "ACCEPT_TRADE_BAR_N": acc,
        "NO_ACCEPTANCE_EVIDENCE_N": no_acc,
        "REINTERRUPTED_N": reint,
        "U_SIGNAL_N": u_sig,
        "D_SIGNAL_N": d_sig,
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
        fsms: dict[str, IsqSymbolFsm] = {s: IsqSymbolFsm(symbol=s, date=day, flatten_t=flatten_t) for s in universe}
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
                print(f"{day} ISQ events={events_n} last_et={last_et} ex={drop or '-'}", flush=True)

        episodes: list[dict[str, Any]] = []
        raw_signals: list[dict[str, Any]] = []
        integrity_n = 0
        rows_by: dict[str, list[dict[str, Any]]] = {CANDIDATE_U: [], CANDIDATE_D: [], CANDIDATE_C: []}
        for s in universe:
            fsms[s].close_session()
            integrity_n += int(fsms[s].integrity_n)
            episodes.extend(list(fsms[s].episodes))
            board = bufs[s].view()
            bars = list(fsms[s].completed_bars)
            for sig in fsms[s].signals:
                raw_signals.append(sig)
                if sig.get("thesis") == THESIS_U:
                    row_u = emit_filled(
                        sig=sig, board=board, bars=bars, am_end=am_end, flatten_t=flatten_t, strategy_id=CANDIDATE_U
                    )
                    rows_by[CANDIDATE_U].append(row_u)
                    row_c = dict(row_u)
                    row_c["candidate_id"] = CANDIDATE_C
                    row_c["STRATEGY_ID"] = CANDIDATE_C
                    rows_by[CANDIDATE_C].append(row_c)
                elif sig.get("thesis") == THESIS_D:
                    row_d = emit_filled(
                        sig=sig, board=board, bars=bars, am_end=am_end, flatten_t=flatten_t, strategy_id=CANDIDATE_D
                    )
                    rows_by[CANDIDATE_D].append(row_d)
                    row_c = dict(row_d)
                    row_c["candidate_id"] = CANDIDATE_C
                    row_c["STRATEGY_ID"] = CANDIDATE_C
                    rows_by[CANDIDATE_C].append(row_c)
        AUDIT["CURRENT_PRICE_TIME_AS_BOARD_FRESH_N"] += int(leak["CURRENT_PRICE_TIME_AS_BOARD_FRESH_N"])
        struct = summarize_episodes(episodes, raw_signals)
        print(
            f"{day} ISQ DEVELOPMENT events={events_n} ep={struct['ISQ_EPISODE_N']} "
            f"u={struct['U_SIGNAL_N']} d={struct['D_SIGNAL_N']} integ={integrity_n} ex={drop or '-'}",
            flush=True,
        )
        ok = int(leak["CURRENT_PRICE_TIME_AS_BOARD_FRESH_N"]) == 0
        return {
            "ok": ok,
            "date": day,
            "rows_by": rows_by,
            "episodes": episodes,
            "signals": raw_signals,
            "structural": struct,
            "integrity_n": integrity_n,
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
        return CACHE / f"ISQ_{day}_ex_{_bare(exclude_symbol)}.pkl.gz"
    return CACHE / f"ISQ_{day}.pkl.gz"


def harvest_isq(*, exclude_symbol: str | None = None) -> dict[str, Any]:
    CACHE.mkdir(parents=True, exist_ok=True)
    invs, blockers = sealed_dev_caps()
    if blockers:
        return {"ok": False, "blocker": "CAPTURE", "blockers": blockers, "day_ok": {}}
    keys = [CANDIDATE_U, CANDIDATE_D, CANDIDATE_C]
    rows_by: dict[str, list[dict[str, Any]]] = {c: [] for c in keys}
    episodes: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    day_ok: dict[str, bool] = {}
    integrity_n = 0
    drop = _bare(exclude_symbol) if exclude_symbol else None
    for inv in invs:
        day = str(inv["date"])
        cache = _cache_path(day, drop)
        saved = _load_gz(cache)
        if saved.get("ok") and str(saved.get("date") or "") == day and saved.get("rows_by"):
            for c, xs in (saved.get("rows_by") or {}).items():
                rows_by.setdefault(c, []).extend(list(xs or []))
            episodes.extend(list(saved.get("episodes") or []))
            signals.extend(list(saved.get("signals") or []))
            integrity_n += int(saved.get("integrity_n") or 0)
            day_ok[day] = True
            print(f"cache-hit ISQ {day} ex={drop or '-'}", flush=True)
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
                "structural": body.get("structural"),
                "leak": body.get("leak"),
            },
        )
        for c, xs in (body.get("rows_by") or {}).items():
            rows_by.setdefault(c, []).extend(list(xs or []))
        episodes.extend(list(body.get("episodes") or []))
        signals.extend(list(body.get("signals") or []))
        integrity_n += int(body.get("integrity_n") or 0)
    struct = summarize_episodes(episodes, signals)
    return {
        "ok": True,
        "rows_by": rows_by,
        "episodes": episodes,
        "signals": signals,
        "structural": struct,
        "integrity_n": integrity_n,
        "day_ok": day_ok,
        "audit": dict(AUDIT),
    }


assert CANARY_ROW_ID
assert EXIT_U
assert EXIT_D
assert first_anchor_loss
