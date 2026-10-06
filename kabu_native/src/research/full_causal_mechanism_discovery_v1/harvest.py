"""DEV-only Capture stream. Canary R2_X1_Z3 original Z3 + 42 operator Full Causal strategies."""
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
from research.causal_mechanism_representation_expansion_v1.operators import reclaim_accept_indices
from research.e1_x28_executable_joint import BOARD_FRESHNESS_SEC as E1_FRESH
from research.full_causal_mechanism_discovery_v1 import (
    BOARD_FRESHNESS_SEC,
    BURNED_HOLDOUT_DAYS,
    CANARY_ROW_ID,
    DEVELOPMENT_DAYS,
    EXEC_EVAL_ID,
    FORBIDDEN_INPUT_DAYS,
    MAX_RESEARCH_DATE,
    SESSION_FLATTEN_HM,
    STRESS_DAYS,
)
from research.full_causal_mechanism_discovery_v1.candidates import freeze_candidate_set
from research.full_causal_mechanism_discovery_v1.exits import (
    fire_for_operator,
    freeze_thesis_level,
    post_fill_bars,
    resolve_canary_exit,
    resolve_operator_exit,
)
from research.full_causal_mechanism_discovery_v1.isolation import CACHE, TODAY
from research.new_entry_breakout_continuation_v1.harvest import BOARD_FRESHNESS_SEC as HARVEST_FRESH
from research.new_entry_breakout_continuation_v1.harvest import BoardTape, board_row, snap_at
from research.profitable_move_mechanism_discovery_v1.predicates import (
    BAR_FNS,
    bar_series,
    breadth_expanding,
    evaluable_i,
)
from research.recovery_sequence_full_strategy_architecture_v1.entries import session_vwap_arr
from research.recovery_sequence_full_strategy_architecture_v1.execution import evaluate_execution
from research.simple_tech_entry_family.bars import SymbolBarBuilder, bar_integrity
from research.simple_tech_entry_family.stages import board_support
from research.systematic_state_transition_full_strategy_v1.entries import canary_r2_indices
from research.systematic_state_transition_full_strategy_v1.states import indicators, persist_next_indices, handoff_next_indices
from small_paper.v1r_live_dual_lane import session_end_for_position

AUDIT = {
    "HOLDOUT_BURNED_READ_N": 0,
    "STRESS_READ_N": 0,
    "STRESS_FILE_OPEN_N": 0,
    "STRESS_METRIC_COMPUTE_N": 0,
    "FUTURE_DATA_N": 0,
    "CURRENT_PRICE_TIME_AS_BOARD_FRESH_N": 0,
    "SPLIT_LEAKAGE_N": 0,
    "EXTRA_CANDIDATE_N": 0,
    "QUEUE_ASSUMED_FILL_N": 0,
    "BAR_OHLC_FILL_N": 0,
    "TRADE_PRINT_PASSIVE_FILL_N": 0,
    "LOOKAHEAD_FILL_N": 0,
    "REPRICE_N": 0,
    "CHASE_N": 0,
    "RAW_SIGNAL_SCREEN_N": 0,
    "SESSION_WALKBACK_42_N": 0,
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


def _board_pred_series(board: dict[str, np.ndarray], finalize: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    n = int(finalize.size)
    bid_gt = np.zeros(n, dtype=bool)
    bsup = np.zeros(n, dtype=bool)
    for i in range(n):
        snap = snap_at(board, float(finalize[i]))
        if not snap.get("ok"):
            continue
        try:
            bq = float(snap.get("bid_qty"))
            aq = float(snap.get("ask_qty"))
        except (TypeError, ValueError):
            continue
        bid_gt[i] = bq == bq and aq == aq and float(bq) > float(aq)
        ok_board, _meta = board_support(snap)
        bsup[i] = bool(ok_board)
    return bid_gt, bsup


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


def emit_canary(
    *,
    day: str,
    symbol: str,
    i: int,
    t0: float,
    board: dict[str, np.ndarray],
    raw: dict[str, np.ndarray],
    vwap: np.ndarray,
    am_end: float,
) -> dict[str, Any]:
    ex, filled = _x1(board, t0, am_end)
    tech: dict[str, Any] = {
        "exit_t": None,
        "exit_price": None,
        "exit_reason": "EXIT_MISS",
        "pnl_yen_100": None,
        "miss": True,
    }
    if filled and ex.get("fill_t") is not None and ex.get("fill_price") is not None:
        tech = resolve_canary_exit(
            board,
            raw,
            vwap,
            fill_t=float(ex["fill_t"]),
            fill_px=float(ex["fill_price"]),
            sess_end=am_end,
        )
    return {
        "date": day,
        "session": "AM",
        "symbol": symbol,
        "t0": t0,
        "signal_t0": t0,
        "i": i,
        "candidate_id": CANARY_ROW_ID,
        "STRATEGY_ID": CANARY_ROW_ID,
        "MECHANISM_ID": "O3_RECLAIM_ACCEPT_NEXT__R2",
        "OPERATOR": "O3_RECLAIM_ACCEPT_NEXT",
        "exec_id": EXEC_EVAL_ID,
        "exit_id": "Z3",
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
        "spread0": ex.get("spread0"),
        "session_walkback": True,
        "flatten_reject": False,
        "SELECTABLE": False,
        "CONTROL_OR_CLOSED_REFERENCE": True,
    }


def emit_strategy(
    *,
    day: str,
    symbol: str,
    i: int,
    t0: float,
    board: dict[str, np.ndarray],
    raw: dict[str, np.ndarray],
    vwap: np.ndarray,
    am_end: float,
    flatten_t: float,
    cand: dict[str, Any],
    series: dict[str, np.ndarray],
) -> dict[str, Any]:
    sid = str(cand["STRATEGY_ID"])
    operator = str(cand["OPERATOR"])
    predicates = list(cand.get("PREDICATES") or [])
    exit_id = str(cand["TECHNICAL_EXIT_ID"])
    thesis = freeze_thesis_level(vwap, i) if operator == "O3_RECLAIM_ACCEPT_NEXT" else None
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
    fire_i = None
    vwap_at_trigger = None
    close_at_trigger = None
    if filled and ex.get("fill_t") is not None and ex.get("fill_price") is not None:
        idxs = post_fill_bars(raw["finalize_t"], float(ex["fill_t"]))
        fire_i = fire_for_operator(
            operator=operator,
            idxs=idxs,
            series=series,
            predicates=predicates,
            close=raw["close"],
            thesis_level=thesis,
        )
        tech = resolve_operator_exit(
            board,
            raw,
            exit_id=exit_id,
            fill_t=float(ex["fill_t"]),
            fill_px=float(ex["fill_price"]),
            sess_end=am_end,
            flatten_t=flatten_t,
            fire_i=fire_i,
        )
        if tech.get("session_walkback"):
            AUDIT["SESSION_WALKBACK_42_N"] += 1
        ti = tech.get("trigger_i")
        if ti is not None:
            vwap_at_trigger = float(vwap[int(ti)]) if int(ti) < int(vwap.size) else None
            close_at_trigger = float(raw["close"][int(ti)]) if int(ti) < int(raw["close"].size) else None
    p_at_trig = None
    q_at_trig = None
    if fire_i is not None and predicates:
        p_at_trig = bool(series[str(predicates[0])][int(fire_i)])
        if len(predicates) > 1:
            q_at_trig = bool(series[str(predicates[1])][int(fire_i)])
    return {
        "date": day,
        "session": "AM",
        "symbol": symbol,
        "t0": t0,
        "signal_t0": t0,
        "i": i,
        "candidate_id": sid,
        "STRATEGY_ID": sid,
        "MECHANISM_ID": str(cand["MECHANISM_ID"]),
        "OPERATOR": operator,
        "PREDICATES": predicates,
        "exec_id": EXEC_EVAL_ID,
        "exit_id": exit_id,
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
        "thesis_level": thesis,
        "vwap_at_signal": float(vwap[i]) if i < int(vwap.size) else None,
        "vwap_at_trigger": vwap_at_trigger,
        "close_at_trigger": close_at_trigger,
        "p_at_trigger": p_at_trig,
        "q_at_trigger": q_at_trig,
        "spread0": ex.get("spread0"),
        "session_walkback": bool(tech.get("session_walkback")),
        "flatten_reject": flatten_reject,
        "SELECTABLE": bool(cand.get("SELECTABLE")),
        "CONTROL_OR_CLOSED_REFERENCE": bool(cand.get("CONTROL_OR_CLOSED_REFERENCE")),
        "EXACT_CLOSED_ENTRY_IDENTITY": bool(cand.get("EXACT_CLOSED_ENTRY_IDENTITY")),
    }


def _assign_breadth(packed: list[dict[str, Any]]) -> None:
    by_min: dict[float, dict[str, tuple[int, bool, bool]]] = {}
    for p in packed:
        trend = p["series"]["S_MA_TREND_UP"]
        mins = np.asarray(p["raw"]["minute_epoch"], dtype=float)
        n = int(trend.size)
        for i in range(n):
            if not evaluable_i(i, n):
                continue
            e = round(float(mins[i]), 6)
            by_min.setdefault(e, {})[p["symbol"]] = (i, bool(trend[i]), bool(trend[i - 1]) if i >= 1 else False)
    xs_by_min: dict[float, bool] = {}
    for e, names in by_min.items():
        trend_now = {s: v[1] for s, v in names.items()}
        trend_prev = {s: v[2] for s, v in names.items()}
        xs_by_min[e] = bool(breadth_expanding(trend_now, trend_prev))
    for p in packed:
        mins = np.asarray(p["raw"]["minute_epoch"], dtype=float)
        xs = p["series"]["S_BREADTH_EXPANDING"]
        for i in range(int(xs.size)):
            e = round(float(mins[i]), 6)
            xs[i] = bool(xs_by_min.get(e, False))


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
    freeze = freeze_candidate_set()
    cands = list(freeze["candidates"])
    if int(freeze["FULL_CAUSAL_CANDIDATE_N"]) != 42:
        AUDIT["EXTRA_CANDIDATE_N"] += 1
        return {"ok": False, "date": day, "blocker": "CANDIDATE_N"}
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
    keys = [CANARY_ROW_ID] + [str(c["STRATEGY_ID"]) for c in cands]
    rows_by: dict[str, list[dict[str, Any]]] = {c: [] for c in keys}
    try:
        am_start = float(hm_epoch(day, 9, 0))
        am_end = float(session_end_for_position(date=day, session="AM", fill_time=am_start + 60.0))
        flatten_t = float(hm_epoch(day, int(SESSION_FLATTEN_HM[0]), int(SESSION_FLATTEN_HM[1])))
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
                print(f"{day} stream events={events_n} last_et={last_et} ex={drop or '-'}", flush=True)

        packed: list[dict[str, Any]] = []
        canary_sig = 0
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
            vwap = session_vwap_arr(raw["close"], raw["volume"], raw.get("vwap_num"))
            board = bufs[s].view()
            ind = indicators(raw)
            series = {pid: bar_series(ind, pid) for pid in BAR_FNS}
            bid_gt, bsup = _board_pred_series(board, raw["finalize_t"])
            series["S_BID_GT_ASK_QTY"] = bid_gt
            series["S_BOARD_SUPPORT"] = bsup
            series["S_BREADTH_EXPANDING"] = np.zeros(n, dtype=bool)
            packed.append({"symbol": s, "raw": raw, "vwap": vwap, "board": board, "ind": ind, "series": series})
            if not drop:
                r2 = canary_r2_indices(raw["open"], raw["high"], raw["low"], raw["close"], raw["volume"], vwap)
                canary_sig += len(r2)
                for i in r2:
                    t0 = float(raw["finalize_t"][i])
                    rows_by[CANARY_ROW_ID].append(
                        emit_canary(day=day, symbol=s, i=i, t0=t0, board=board, raw=raw, vwap=vwap, am_end=am_end)
                    )
        _assign_breadth(packed)
        strat_sig = 0
        for p in packed:
            raw = p["raw"]
            series = p["series"]
            board = p["board"]
            vwap = p["vwap"]
            s = p["symbol"]
            for cand in cands:
                operator = str(cand["OPERATOR"])
                prim = list(cand.get("PREDICATES") or [])
                if operator == "O1_PERSIST_NEXT":
                    xs = persist_next_indices(series[str(prim[0])])
                elif operator == "O2_HANDOFF_NEXT":
                    xs = handoff_next_indices(series[str(prim[0])], series[str(prim[1])])
                elif operator == "O3_RECLAIM_ACCEPT_NEXT":
                    rid = str(cand.get("RECLAIM_ID") or "")
                    xs = reclaim_accept_indices(
                        rid, raw["open"], raw["high"], raw["low"], raw["close"], raw["volume"], vwap
                    )
                else:
                    raise RuntimeError(operator)
                sid = str(cand["STRATEGY_ID"])
                for i in xs:
                    t0 = float(raw["finalize_t"][i])
                    if float(t0) + 1e-12 >= float(flatten_t):
                        continue
                    strat_sig += 1
                    rows_by[sid].append(
                        emit_strategy(
                            day=day,
                            symbol=s,
                            i=int(i),
                            t0=t0,
                            board=board,
                            raw=raw,
                            vwap=vwap,
                            am_end=am_end,
                            flatten_t=flatten_t,
                            cand=cand,
                            series=series,
                        )
                    )
        AUDIT["CURRENT_PRICE_TIME_AS_BOARD_FRESH_N"] += int(leak["CURRENT_PRICE_TIME_AS_BOARD_FRESH_N"])
        print(
            f"{day} DEVELOPMENT events={events_n} canary_sig={canary_sig} strat_sig={strat_sig} "
            f"last_et={last_et} ex={drop or '-'}",
            flush=True,
        )
        ok = (
            int(leak["BAR_INTEG_FAIL_N"]) == 0
            and int(leak["FUTURE_BAR_N"]) == 0
            and int(leak["CURRENT_PRICE_TIME_AS_BOARD_FRESH_N"]) == 0
        )
        return {
            "ok": ok,
            "date": day,
            "rows_by": rows_by,
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
        return CACHE / f"DEVELOPMENT_{day}_ex_{_bare(exclude_symbol)}.pkl.gz"
    return CACHE / f"DEVELOPMENT_{day}_grid.pkl.gz"


def harvest_development(*, exclude_symbol: str | None = None) -> dict[str, Any]:
    CACHE.mkdir(parents=True, exist_ok=True)
    invs, blockers = sealed_dev_caps()
    if blockers:
        return {"ok": False, "blocker": "CAPTURE", "blockers": blockers, "day_ok": {}}
    freeze = freeze_candidate_set()
    keys = [CANARY_ROW_ID] + [str(c["STRATEGY_ID"]) for c in freeze["candidates"]]
    rows_by: dict[str, list[dict[str, Any]]] = {c: [] for c in keys}
    day_ok: dict[str, bool] = {}
    drop = _bare(exclude_symbol) if exclude_symbol else None
    for inv in invs:
        day = str(inv["date"])
        cache = _cache_path(day, drop)
        saved = _load_gz(cache)
        if saved.get("ok") and str(saved.get("date") or "") == day and saved.get("rows_by"):
            extra = [c for c in (saved.get("rows_by") or {}) if c not in keys]
            if extra:
                AUDIT["EXTRA_CANDIDATE_N"] += len(extra)
                return {"ok": False, "blocker": "EXTRA_CANDIDATE", "day_ok": day_ok}
            for c, xs in (saved.get("rows_by") or {}).items():
                rows_by.setdefault(c, []).extend(list(xs or []))
            day_ok[day] = True
            print(f"cache-hit DEVELOPMENT {day} ex={drop or '-'}", flush=True)
            continue
        body = process_dev_day(
            {"date": day, "capture_path": inv["capture_path"], "universe": list(inv["universe_symbols"])},
            exclude_symbol=drop,
        )
        day_ok[day] = bool(body.get("ok"))
        if not body.get("ok"):
            return {"ok": False, "blocker": f"DEVELOPMENT:{day}:{body.get('blocker')}", "day_ok": day_ok}
        _dump_gz(cache, {"ok": True, "date": day, "rows_by": body.get("rows_by"), "leak": body.get("leak"), "exclude_symbol": drop})
        for c, xs in (body.get("rows_by") or {}).items():
            rows_by.setdefault(c, []).extend(list(xs or []))
    return {"ok": True, "rows_by": rows_by, "day_ok": day_ok, "audit": dict(AUDIT), "exclude_symbol": drop}


def harvest_g6_universe(top_symbol: str) -> dict[str, Any]:
    return harvest_development(exclude_symbol=top_symbol)
