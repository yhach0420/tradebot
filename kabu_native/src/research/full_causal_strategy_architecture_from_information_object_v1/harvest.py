"""DEV Capture stream: depth-rank semantic audit + FDG signals + X1 + span-normalize EXIT + R2 canary."""
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

from replay.pnl_yen import compute_pnl_yen_100
from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import (
    _bare,
    capture_event_epoch,
    find_capture_dir,
    iter_push,
    record_event_stamp,
)
from research.e1_x28_executable_joint import BOARD_FRESHNESS_SEC as E1_FRESH
from research.full_causal_mechanism_discovery_v1.harvest import emit_canary
from research.full_causal_strategy_architecture_from_information_object_v1 import (
    BOARD_FRESHNESS_SEC,
    BURNED_HOLDOUT_DAYS,
    CANARY_ROW_ID,
    DEVELOPMENT_DAYS,
    EXEC_EVAL_ID,
    FORBIDDEN_INPUT_DAYS,
    MAX_RESEARCH_DATE,
    SESSION_FLATTEN_HM,
    STRESS_DAYS,
    STRATEGY_ID,
    TECHNICAL_EXIT_ID,
)
from research.full_causal_strategy_architecture_from_information_object_v1.geometry import (
    snapshot_from_payload,
    onset_false_to_true,
    update_last_known,
)
from research.full_causal_strategy_architecture_from_information_object_v1.isolation import CACHE, TODAY
from research.new_entry_breakout_continuation_v1.harvest import BOARD_FRESHNESS_SEC as HARVEST_FRESH
from research.new_entry_breakout_continuation_v1.harvest import BoardTape, board_row
from research.new_full_strategy_implementation_and_dev_eval_v1.quotes import payload_of
from research.recovery_sequence_full_strategy_architecture_v1.entries import session_vwap_arr
from research.recovery_sequence_full_strategy_architecture_v1.execution import evaluate_execution
from research.simple_full_strategy_discovery_v1.exits import first_causal_bid
from research.simple_tech_entry_family.bars import SymbolBarBuilder, bar_integrity
from research.systematic_state_transition_full_strategy_v1.entries import canary_r2_indices
from small_paper.v1r_live_dual_lane import session_end_for_position

AUDIT = {
    "HOLDOUT_BURNED_READ_N": 0,
    "STRESS_READ_N": 0,
    "STRESS_FILE_OPEN_N": 0,
    "STRESS_METRIC_COMPUTE_N": 0,
    "FUTURE_DATA_N": 0,
    "CURRENT_PRICE_TIME_AS_BOARD_FRESH_N": 0,
    "SPLIT_LEAKAGE_N": 0,
    "QUEUE_ASSUMED_FILL_N": 0,
    "BAR_OHLC_FILL_N": 0,
    "TRADE_PRINT_PASSIVE_FILL_N": 0,
    "LOOKAHEAD_FILL_N": 0,
    "REPRICE_N": 0,
    "CHASE_N": 0,
    "RAW_SIGNAL_SCREEN_N": 0,
    "FORBIDDEN_TRANSFORMATION_USED_N": 0,
    "QTY_IMBALANCE_ALPHA_N": 0,
    "K_SUBSET_N": 0,
    "MIGRATION_RULE_N": 0,
    "PREOPEN_EXECUTION_N": 0,
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
        AUDIT["STRESS_FILE_OPEN_N"] += 1
        AUDIT["STRESS_METRIC_COMPUTE_N"] += 1
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


def _x1(board: dict[str, np.ndarray], t0: float, am_end: float) -> tuple[dict[str, Any], bool]:
    ex = evaluate_execution(board, exec_id=EXEC_EVAL_ID, t0=float(t0), sess_end=float(am_end))
    _absorb_flags(ex)
    filled = bool(ex.get("WOULD_FILL"))
    if filled and ex.get("fill_t") is not None and float(ex["fill_t"]) + 1e-12 < float(t0):
        AUDIT["LOOKAHEAD_FILL_N"] += 1
        filled = False
        ex["WOULD_FILL"] = False
        ex["filled"] = False
    return ex, filled


def _empty_sem() -> dict[str, int]:
    return {
        "snapshot_n": 0,
        "bid_all10_present_n": 0,
        "ask_all10_present_n": 0,
        "bid_internal_zero_then_nonzero_n": 0,
        "ask_internal_zero_then_nonzero_n": 0,
        "bid_strict_order_n": 0,
        "ask_strict_order_n": 0,
        "valid_full_depth_n": 0,
        "unknown_n": 0,
        "long_state_true_n": 0,
        "long_state_false_n": 0,
    }


def _add_sem(dst: dict[str, int], src: dict[str, int]) -> None:
    for k, v in src.items():
        dst[k] = int(dst.get(k) or 0) + int(v)


def _geo_onsets(geo_t: np.ndarray, geo_long: np.ndarray, flatten_t: float) -> list[int]:
    last: Optional[bool] = None
    out: list[int] = []
    for i in range(int(geo_t.size)):
        t = float(geo_t[i])
        if t + 1e-12 >= float(flatten_t):
            break
        cur = bool(geo_long[i])
        if onset_false_to_true(last, cur):
            out.append(i)
        last = update_last_known(last, cur)
    return out


def _first_normalize_after(geo_t: np.ndarray, geo_long: np.ndarray, fill_t: float, flatten_t: float) -> Optional[int]:
    i0 = int(np.searchsorted(geo_t, float(fill_t), side="right"))
    for i in range(i0, int(geo_t.size)):
        t = float(geo_t[i])
        if t + 1e-12 <= float(fill_t):
            continue
        if t + 1e-12 > float(flatten_t):
            return None
        if not bool(geo_long[i]):
            return int(i)
    return None


def _pack_exit(*, fill_t: float, fill_px: float, chosen: dict[str, Any], reason: str, trigger_t: Any) -> dict[str, Any]:
    if chosen.get("miss") or chosen.get("exit_t") is None or chosen.get("exit_bid") is None:
        return {
            "exit_t": None,
            "exit_price": None,
            "exit_reason": "EXIT_MISS",
            "pnl_yen_100": None,
            "miss": True,
            "hold_sec": None,
            "trigger_t": trigger_t,
        }
    exit_t = float(chosen["exit_t"])
    exit_px = float(chosen["exit_bid"])
    pnl = float(compute_pnl_yen_100(float(fill_px), exit_px, side="long"))
    return {
        "exit_t": exit_t,
        "exit_price": exit_px,
        "exit_reason": reason,
        "pnl_yen_100": pnl,
        "miss": False,
        "hold_sec": float(exit_t - float(fill_t)),
        "trigger_t": trigger_t,
    }


def resolve_fdg_exit(
    board: dict[str, np.ndarray],
    geo_t: np.ndarray,
    geo_long: np.ndarray,
    *,
    fill_t: float,
    fill_px: float,
    sess_end: float,
    flatten_t: float,
) -> dict[str, Any]:
    sess_event = max(float(fill_t), float(flatten_t))
    sess = first_causal_bid(board, event_t=float(sess_event), sess_end=float(sess_end))
    fire_i = _first_normalize_after(geo_t, geo_long, float(fill_t), float(flatten_t))
    trig_t = None if fire_i is None else float(geo_t[int(fire_i)])
    tech = {"exit_t": None, "exit_bid": None, "miss": True}
    if trig_t is not None:
        tech = first_causal_bid(board, event_t=float(trig_t), sess_end=float(sess_end))
    tech_eligible = trig_t is not None and float(trig_t) <= float(flatten_t) + 1e-12
    if tech_eligible and not tech.get("miss") and tech.get("exit_t") is not None:
        if sess.get("exit_t") is not None and float(sess["exit_t"]) + 1e-12 < float(tech["exit_t"]):
            return _pack_exit(fill_t=fill_t, fill_px=fill_px, chosen=sess, reason="SESSION_CLOSE", trigger_t=trig_t)
        return _pack_exit(fill_t=fill_t, fill_px=fill_px, chosen=tech, reason=TECHNICAL_EXIT_ID, trigger_t=trig_t)
    return _pack_exit(fill_t=fill_t, fill_px=fill_px, chosen=sess, reason="SESSION_CLOSE", trigger_t=trig_t)


def emit_fdg(
    *,
    day: str,
    symbol: str,
    t0: float,
    board: dict[str, np.ndarray],
    geo_t: np.ndarray,
    geo_long: np.ndarray,
    am_end: float,
    flatten_t: float,
    bid_span: float,
    ask_span: float,
) -> dict[str, Any]:
    flatten_reject = float(t0) + 1e-12 >= float(flatten_t)
    if flatten_reject:
        AUDIT["PREOPEN_EXECUTION_N"] += 0
        return {
            "date": day,
            "session": "AM",
            "symbol": symbol,
            "t0": t0,
            "signal_t0": t0,
            "STRATEGY_ID": STRATEGY_ID,
            "exec_id": EXEC_EVAL_ID,
            "exit_id": TECHNICAL_EXIT_ID,
            "ORDERED": False,
            "WOULD_FILL": False,
            "fill_t": None,
            "fill_price": None,
            "exit_t": None,
            "exit_price": None,
            "exit_reason": "FLATTEN_REJECT",
            "pnl_yen_100": None,
            "flatten_reject": True,
            "BID_SPAN": bid_span,
            "ASK_SPAN": ask_span,
        }
    ex, filled = _x1(board, t0, am_end)
    tech: dict[str, Any] = {
        "exit_t": None,
        "exit_price": None,
        "exit_reason": "EXIT_MISS",
        "pnl_yen_100": None,
        "miss": True,
        "trigger_t": None,
        "hold_sec": None,
    }
    if filled and ex.get("fill_t") is not None and ex.get("fill_price") is not None:
        tech = resolve_fdg_exit(
            board,
            geo_t,
            geo_long,
            fill_t=float(ex["fill_t"]),
            fill_px=float(ex["fill_price"]),
            sess_end=am_end,
            flatten_t=flatten_t,
        )
    return {
        "date": day,
        "session": "AM",
        "symbol": symbol,
        "t0": t0,
        "signal_t0": t0,
        "STRATEGY_ID": STRATEGY_ID,
        "ARCHITECTURE_CLASS": "FULL_DEPTH_PRICE_GEOMETRY_ASYMMETRY",
        "exec_id": EXEC_EVAL_ID,
        "exit_id": TECHNICAL_EXIT_ID,
        "ORDERED": bool(ex.get("ORDERED")),
        "WOULD_FILL": filled,
        "fill_t": ex.get("fill_t") if filled else None,
        "fill_price": ex.get("fill_price") if filled else None,
        "exit_t": tech.get("exit_t"),
        "exit_price": tech.get("exit_price"),
        "exit_reason": tech.get("exit_reason"),
        "pnl_yen_100": tech.get("pnl_yen_100"),
        "hold_sec": tech.get("hold_sec"),
        "trigger_t": tech.get("trigger_t"),
        "spread0": ex.get("spread0"),
        "session_walkback": False,
        "flatten_reject": False,
        "BID_SPAN": bid_span,
        "ASK_SPAN": ask_span,
        "LONG_GEOMETRY_STATE": True,
    }


def process_dev_day(payload: dict[str, Any], *, exclude_symbol: str | None = None, emit_canary_rows: bool = True) -> dict[str, Any]:
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
        "FUTURE_BAR_N": 0,
        "BAR_INTEG_FAIL_N": 0,
        "CURRENT_PRICE_TIME_AS_BOARD_FRESH_N": 0,
        "UNIVERSE_SKIP_N": 0,
        "NO_EVENT_TIME_N": 0,
    }
    sem = _empty_sem()
    canary_rows: list[dict[str, Any]] = []
    fdg_rows: list[dict[str, Any]] = []
    try:
        am_start = float(hm_epoch(day, 9, 0))
        am_end = float(session_end_for_position(date=day, session="AM", fill_time=am_start + 60.0))
        flatten_t = float(hm_epoch(day, int(SESSION_FLATTEN_HM[0]), int(SESSION_FLATTEN_HM[1])))
        bufs: dict[str, BoardTape] = {s: BoardTape() for s in universe}
        builders: dict[str, SymbolBarBuilder] = {s: SymbolBarBuilder(am_start=am_start, am_end=am_end) for s in universe}
        geo_t: dict[str, list[float]] = {s: [] for s in universe}
        geo_long: dict[str, list[bool]] = {s: [] for s in universe}
        geo_bspan: dict[str, list[float]] = {s: [] for s in universe}
        geo_aspan: dict[str, list[float]] = {s: [] for s in universe}
        events_n = 0
        last_et: Optional[float] = None
        for rec in iter_push(capture):
            pay = payload_of(rec)
            sym = _bare(rec.get("symbol") or pay.get("Symbol"))
            if not sym or sym not in uni:
                leak["UNIVERSE_SKIP_N"] += 1
                continue
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
            if float(et) + 1e-12 < am_start:
                continue
            if not bool(row.get("continuous")):
                continue
            if float(et) >= am_end:
                continue
            snap = snapshot_from_payload(pay)
            sem["snapshot_n"] += 1
            if snap["bid_all10"]:
                sem["bid_all10_present_n"] += 1
            if snap["ask_all10"]:
                sem["ask_all10_present_n"] += 1
            if snap["bid_internal_zero_then_nonzero"]:
                sem["bid_internal_zero_then_nonzero_n"] += 1
            if snap["ask_internal_zero_then_nonzero"]:
                sem["ask_internal_zero_then_nonzero_n"] += 1
            if snap["bid_strict"]:
                sem["bid_strict_order_n"] += 1
            if snap["ask_strict"]:
                sem["ask_strict_order_n"] += 1
            if snap["VALID_FULL_DEPTH"]:
                sem["valid_full_depth_n"] += 1
                geo_t[sym].append(float(et))
                geo_long[sym].append(bool(snap["LONG_GEOMETRY_STATE"]))
                geo_bspan[sym].append(float(snap["BID_SPAN"]))
                geo_aspan[sym].append(float(snap["ASK_SPAN"]))
                if snap["LONG_GEOMETRY_STATE"]:
                    sem["long_state_true_n"] += 1
                else:
                    sem["long_state_false_n"] += 1
            else:
                sem["unknown_n"] += 1
            if events_n % 400000 == 0:
                print(f"{day} FDG stream events={events_n} last_et={last_et} ex={drop or '-'}", flush=True)

        canary_sig = 0
        for s in universe:
            builders[s].close_session()
            raw = builders[s].as_arrays()
            integ = bar_integrity(raw, am_start=am_start, am_end=am_end)
            leak["FUTURE_BAR_N"] += int(integ.get("FUTURE_BAR_N") or 0)
            board = bufs[s].view()
            gt = np.asarray(geo_t[s], dtype=float)
            gl = np.asarray(geo_long[s], dtype=bool)
            gb = np.asarray(geo_bspan[s], dtype=float)
            ga = np.asarray(geo_aspan[s], dtype=float)
            if emit_canary_rows and not drop and integ.get("ok") and int(raw["close"].size) > 0:
                vwap = session_vwap_arr(raw["close"], raw["volume"], raw.get("vwap_num"))
                r2 = canary_r2_indices(raw["open"], raw["high"], raw["low"], raw["close"], raw["volume"], vwap)
                canary_sig += len(r2)
                for i in r2:
                    t0 = float(raw["finalize_t"][i])
                    canary_rows.append(
                        emit_canary(day=day, symbol=s, i=i, t0=t0, board=board, raw=raw, vwap=vwap, am_end=am_end)
                    )
            elif not integ.get("ok"):
                leak["BAR_INTEG_FAIL_N"] += 1
            for j in _geo_onsets(gt, gl, flatten_t):
                fdg_rows.append(
                    emit_fdg(
                        day=day,
                        symbol=s,
                        t0=float(gt[j]),
                        board=board,
                        geo_t=gt,
                        geo_long=gl,
                        am_end=am_end,
                        flatten_t=flatten_t,
                        bid_span=float(gb[j]),
                        ask_span=float(ga[j]),
                    )
                )
        AUDIT["CURRENT_PRICE_TIME_AS_BOARD_FRESH_N"] += int(leak["CURRENT_PRICE_TIME_AS_BOARD_FRESH_N"])
        print(
            f"{day} FDG events={events_n} canary_sig={canary_sig} fdg_sig={len(fdg_rows)} "
            f"valid={sem['valid_full_depth_n']} last_et={last_et} ex={drop or '-'}",
            flush=True,
        )
        ok = int(leak["FUTURE_BAR_N"]) == 0 and int(leak["CURRENT_PRICE_TIME_AS_BOARD_FRESH_N"]) == 0
        if emit_canary_rows and not drop:
            ok = ok and int(leak["BAR_INTEG_FAIL_N"]) == 0
        return {
            "ok": ok,
            "date": day,
            "canary_rows": canary_rows,
            "fdg_rows": fdg_rows,
            "semantic": sem,
            "leak": leak,
            "events_n": events_n,
            "canary_sig": canary_sig,
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
            "blocker": None if ok else "BAR_OR_FRESHNESS_LEAK",
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
        return CACHE / f"FDG_{day}_ex_{_bare(exclude_symbol)}.pkl.gz"
    return CACHE / f"FDG_{day}.pkl.gz"


def harvest_development(*, exclude_symbol: str | None = None, emit_canary_rows: bool = True) -> dict[str, Any]:
    CACHE.mkdir(parents=True, exist_ok=True)
    invs, blockers = sealed_dev_caps()
    if blockers:
        return {"ok": False, "blocker": "CAPTURE", "blockers": blockers, "day_ok": {}}
    canary_rows: list[dict[str, Any]] = []
    fdg_rows: list[dict[str, Any]] = []
    sem = _empty_sem()
    day_ok: dict[str, bool] = {}
    drop = _bare(exclude_symbol) if exclude_symbol else None
    for inv in invs:
        day = str(inv["date"])
        cache = _cache_path(day, drop)
        saved = _load_gz(cache)
        if saved.get("ok") and str(saved.get("date") or "") == day:
            canary_rows.extend(list(saved.get("canary_rows") or []))
            fdg_rows.extend(list(saved.get("fdg_rows") or []))
            _add_sem(sem, dict(saved.get("semantic") or {}))
            day_ok[day] = True
            print(f"cache-hit FDG {day} ex={drop or '-'}", flush=True)
            continue
        body = process_dev_day(
            {"date": day, "capture_path": inv["capture_path"], "universe": list(inv["universe_symbols"])},
            exclude_symbol=drop,
            emit_canary_rows=emit_canary_rows and not drop,
        )
        day_ok[day] = bool(body.get("ok"))
        if not body.get("ok"):
            return {"ok": False, "blocker": f"DEVELOPMENT:{day}:{body.get('blocker')}", "day_ok": day_ok, "semantic": sem}
        _dump_gz(
            cache,
            {
                "ok": True,
                "date": day,
                "canary_rows": body.get("canary_rows"),
                "fdg_rows": body.get("fdg_rows"),
                "semantic": body.get("semantic"),
                "leak": body.get("leak"),
                "exclude_symbol": drop,
            },
        )
        canary_rows.extend(list(body.get("canary_rows") or []))
        fdg_rows.extend(list(body.get("fdg_rows") or []))
        _add_sem(sem, dict(body.get("semantic") or {}))
    return {
        "ok": True,
        "canary_rows": canary_rows,
        "fdg_rows": fdg_rows,
        "semantic": sem,
        "day_ok": day_ok,
        "blocker": None,
    }
