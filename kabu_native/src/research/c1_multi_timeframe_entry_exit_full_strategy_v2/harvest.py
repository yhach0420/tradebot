"""DEV-only Capture stream. Canary R2_X1_Z3 + uniform 40-pair harvest. No V1 Z3 metric reuse."""
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
from research.c1_multi_timeframe_entry_exit_full_strategy_v2 import (
    BURNED_HOLDOUT_DAYS,
    DEVELOPMENT_DAYS,
    EXECUTION_ID,
    EXIT_FIRE_KEY,
    FORBIDDEN_INPUT_DAYS,
    FROZEN_PAIRS,
    GLOBAL_VWAP_ENTRY_GATE,
    KEPT_EXIT_IDS,
    MAX_RESEARCH_DATE,
    STRESS_DAYS,
)
from research.c1_multi_timeframe_entry_exit_full_strategy_v2.isolation import CACHE, TODAY, V1_FULL_STRATEGY_CACHE
from research.c1_multi_timeframe_entry_exit_pair_precommit_v1.spec import pair_id
from research.c1_multi_timeframe_full_strategy_v1.entries import build_htf, candidate_signal_indices, canary_r2_indices
from research.c1_multi_timeframe_full_strategy_v1.spec import parse_candidate_id
from research.c1_multi_timeframe_precommit_v1 import RAW_CANDIDATE_IDS
from research.e1_x28_executable_joint import BOARD_FRESHNESS_SEC as E1_FRESH
from research.entry_edge_measurement_decomposition_v1.harvest import attach_economics
from research.new_entry_breakout_continuation_v1.harvest import BOARD_FRESHNESS_SEC, BoardTape, board_row
from research.recovery_sequence_full_strategy_architecture_v1.entries import session_vwap_arr
from research.recovery_sequence_full_strategy_architecture_v1.execution import evaluate_execution
from research.simple_full_strategy_discovery_v1.exits import last_session_bid, resolve_exit
from research.simple_tech_entry_family.bars import SymbolBarBuilder, bar_integrity
from replay.pnl_yen import compute_pnl_yen_100
from small_paper.v1r_live_dual_lane import session_end_for_position

CANARY_ROW_ID = "R2_X1_Z3"

AUDIT = {
    "HOLDOUT_BURNED_READ_N": 0,
    "STRESS_READ_N": 0,
    "STRESS_FILE_OPEN_N": 0,
    "STRESS_METRIC_COMPUTE_N": 0,
    "STRESS_REPLAY_N": 0,
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
    "PARTIAL_HTF_BAR_USED_N": 0,
    "FUTURE_HTF_BAR_N": 0,
    "SAME_BUCKET_CARRYBACK_N": 0,
    "CROSS_SESSION_HTF_BAR_N": 0,
    "FUTURE_SESSION_VWAP_CARRYBACK_N": 0,
    "V7_SAME_BUCKET_JOIN_USED": 0,
    "HTF_VWAP_RECOMPUTED_FROM_AGG_CLOSE_VOLUME_N": 0,
    "GLOBAL_VWAP_ENTRY_GATE_N": 0,
    "PRIOR_Z3_GRID_READ_N": 0,
    "PRIOR_Z3_METRIC_REUSE_N": 0,
    "Z4_APPEARED_N": 0,
}


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _dump(path: Path, body: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, ensure_ascii=False, default=str), encoding="utf-8")


def freeze_path() -> Path:
    return CACHE / "strategy_freeze.json"


def assert_dev_only_day(day: str) -> None:
    d = str(day)
    if d in STRESS_DAYS:
        AUDIT["STRESS_READ_N"] += 1
        AUDIT["STRESS_FILE_OPEN_N"] += 1
        AUDIT["STRESS_METRIC_COMPUTE_N"] += 1
        AUDIT["STRESS_REPLAY_N"] += 1
        raise RuntimeError("STRESS_READ")
    if d in BURNED_HOLDOUT_DAYS:
        AUDIT["HOLDOUT_BURNED_READ_N"] += 1
        raise RuntimeError("HOLDOUT_BURNED_READ")
    if d in FORBIDDEN_INPUT_DAYS or d > MAX_RESEARCH_DATE:
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


def _exec_once(
    *,
    day: str,
    symbol: str,
    i: int,
    t0: float,
    board: dict[str, np.ndarray],
    am_end: float,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    econ = attach_economics(
        {"date": day, "session": "AM", "symbol": symbol, "i": i},
        board,
        t0=t0,
        am_end=am_end,
        orig=None,
    )
    ex = evaluate_execution(board, exec_id="X1", t0=t0, sess_end=am_end)
    _absorb_flags(ex)
    filled = bool(ex.get("WOULD_FILL"))
    if filled and ex.get("fill_t") is not None and float(ex["fill_t"]) + 1e-12 < t0:
        AUDIT["LOOKAHEAD_FILL_N"] += 1
        filled = False
        ex["WOULD_FILL"] = False
        ex["filled"] = False
    return econ, ex, filled


def _row_from_exec(
    *,
    day: str,
    symbol: str,
    i: int,
    t0: float,
    board: dict[str, np.ndarray],
    raw: dict[str, np.ndarray],
    vwap: np.ndarray,
    am_end: float,
    candidate_id: str,
    entry_id: str,
    htf_id: str,
    fire_key: str,
    exit_full_id: str,
    econ: dict[str, Any],
    ex: dict[str, Any],
    filled: bool,
) -> dict[str, Any]:
    if fire_key == "Z4" or "Z4_TRAILING_STRUCTURE" in str(exit_full_id) or "Z4_TRAILING_STRUCTURE" in str(candidate_id):
        AUDIT["Z4_APPEARED_N"] += 1
        raise RuntimeError("Z4_FORBIDDEN")
    sess = {"exit_t": None, "exit_price": None, "pnl_yen_100": None, "exit_reason": "SESSION_CLOSE", "miss": True}
    tech: dict[str, Any] = {}
    if filled and ex.get("fill_t") is not None and ex.get("fill_price") is not None:
        sb = last_session_bid(board, fill_t=float(ex["fill_t"]), sess_end=am_end)
        if not sb.get("miss") and sb.get("exit_bid") is not None:
            sess = {
                "exit_t": sb["exit_t"],
                "exit_price": sb["exit_bid"],
                "pnl_yen_100": float(compute_pnl_yen_100(float(ex["fill_price"]), float(sb["exit_bid"]))),
                "exit_reason": "SESSION_CLOSE",
                "miss": False,
            }
        tech = resolve_exit(
            board,
            raw,
            vwap,
            exit_id=fire_key,
            fill_t=float(ex["fill_t"]),
            fill_px=float(ex["fill_price"]),
            sess_end=am_end,
        )
    rec = {
        "date": day,
        "session": "AM",
        "symbol": symbol,
        "t0": t0,
        "signal_t0": t0,
        "i": i,
        "entry_id": entry_id,
        "ENTRY_ID": entry_id,
        "HTF_ID": htf_id,
        "exec_id": "X1",
        "EXECUTION": EXECUTION_ID,
        "exit_id": fire_key,
        "EXIT_ID": exit_full_id,
        "candidate_id": candidate_id,
        "ORDERED": bool(ex.get("ORDERED")),
        "WOULD_FILL": filled,
        "fill_t": ex.get("fill_t") if filled else None,
        "fill_price": ex.get("fill_price") if filled else None,
        "exit_t": sess.get("exit_t"),
        "exit_price": sess.get("exit_price"),
        "exit_reason": sess.get("exit_reason"),
        "pnl_yen_100": sess.get("pnl_yen_100"),
        "spread0": ex.get("spread0") if ex.get("spread0") is not None else econ.get("spread0"),
        "MID_60": econ.get("MID_60"),
        "MID_180": econ.get("MID_180"),
        "MID_300": econ.get("MID_300"),
    }
    if filled and tech:
        rec.update(
            {
                "exit_t": tech.get("exit_t"),
                "exit_price": tech.get("exit_price"),
                "exit_reason": tech.get("exit_reason"),
                "pnl_yen_100": tech.get("pnl_yen_100"),
                "hold_sec": tech.get("hold_sec"),
                "mfe_yen": tech.get("mfe_yen"),
                "mae_yen": tech.get("mae_yen"),
                "trigger_t": tech.get("trigger_t"),
                "exit_pending": True if tech.get("exit_reason") not in (None, "SESSION_CLOSE", "EXIT_MISS") else False,
            }
        )
    return rec


def process_dev_day(payload: dict[str, Any]) -> dict[str, Any]:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"
    day = str(payload["date"])
    try:
        assert_dev_only_day(day)
    except RuntimeError as exc:
        return {"ok": False, "date": day, "blocker": str(exc)}
    if abs(float(BOARD_FRESHNESS_SEC) - float(E1_FRESH)) > 1e-12:
        return {"ok": False, "date": day, "blocker": "BOARD_FRESHNESS_DRIFT"}
    if GLOBAL_VWAP_ENTRY_GATE:
        AUDIT["GLOBAL_VWAP_ENTRY_GATE_N"] += 1
        return {"ok": False, "date": day, "blocker": "GLOBAL_VWAP_ENTRY_GATE"}
    cids = list(FROZEN_PAIRS)
    if len(cids) != 40:
        AUDIT["EXTRA_CANDIDATE_N"] += 1
        return {"ok": False, "date": day, "blocker": "PAIR_N"}
    if any("Z4_TRAILING_STRUCTURE" in c for c in cids):
        AUDIT["Z4_APPEARED_N"] += 1
        return {"ok": False, "date": day, "blocker": "Z4_IN_PAIRS"}
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
        "FUTURE_HTF_BAR_N": 0,
        "PARTIAL_HTF_BAR_USED_N": 0,
        "SAME_BUCKET_CARRYBACK_N": 0,
        "CROSS_SESSION_HTF_BAR_N": 0,
        "FUTURE_SESSION_VWAP_CARRYBACK_N": 0,
    }
    keys = [CANARY_ROW_ID] + cids
    rows_by: dict[str, list[dict[str, Any]]] = {c: [] for c in keys}
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

        canary_sig = 0
        c1_sig = 0
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
            vwap_exit = session_vwap_arr(raw["close"], raw["volume"], raw.get("vwap_num"))
            board = bufs[s].view()
            ind = indicators_of(raw)
            vwap_1m = ind["vwap"]
            htf3, leak3, ind3 = build_htf(raw, width_sec=180.0, am_start=am_start, am_end=am_end)
            htf5, leak5, ind5 = build_htf(raw, width_sec=300.0, am_start=am_start, am_end=am_end)
            if int((leak3.get("integ") or {}).get("SESSION_CARRY_N") or 0):
                leak["CROSS_SESSION_HTF_BAR_N"] += int((leak3["integ"]).get("SESSION_CARRY_N") or 0)
            if int((leak5.get("integ") or {}).get("SESSION_CARRY_N") or 0):
                leak["CROSS_SESSION_HTF_BAR_N"] += int((leak5["integ"]).get("SESSION_CARRY_N") or 0)
            htf_by = {"HTF_3M": (htf3, ind3), "HTF_5M": (htf5, ind5)}
            r2 = canary_r2_indices(raw["open"], raw["high"], raw["low"], raw["close"], raw["volume"], vwap_exit)
            canary_sig += len(r2)
            for i in r2:
                t0 = float(raw["finalize_t"][i])
                econ, ex, filled = _exec_once(day=day, symbol=s, i=i, t0=t0, board=board, am_end=am_end)
                rows_by[CANARY_ROW_ID].append(
                    _row_from_exec(
                        day=day,
                        symbol=s,
                        i=i,
                        t0=t0,
                        board=board,
                        raw=raw,
                        vwap=vwap_exit,
                        am_end=am_end,
                        candidate_id=CANARY_ROW_ID,
                        entry_id=CANARY_ROW_ID,
                        htf_id="",
                        fire_key="Z3",
                        exit_full_id="Z3",
                        econ=econ,
                        ex=ex,
                        filled=filled,
                    )
                )
            sig_leak = {
                "FUTURE_HTF_BAR_N": 0,
                "SAME_BUCKET_CARRYBACK_N": 0,
                "CROSS_SESSION_HTF_BAR_N": 0,
                "FUTURE_SESSION_VWAP_CARRYBACK_N": 0,
            }
            for entry_id in RAW_CANDIDATE_IDS:
                xs = candidate_signal_indices(
                    entry_id,
                    ind,
                    raw,
                    htf_by=htf_by,
                    vwap_1m=vwap_1m,
                    am_start=am_start,
                    leak=sig_leak,
                )
                c1_sig += len(xs)
                htf_id = str(parse_candidate_id(entry_id)["HTF_ID"])
                for i in xs:
                    t0 = float(raw["finalize_t"][i])
                    econ, ex, filled = _exec_once(day=day, symbol=s, i=i, t0=t0, board=board, am_end=am_end)
                    for exit_id in KEPT_EXIT_IDS:
                        pid = pair_id(entry_id, exit_id)
                        rows_by[pid].append(
                            _row_from_exec(
                                day=day,
                                symbol=s,
                                i=i,
                                t0=t0,
                                board=board,
                                raw=raw,
                                vwap=vwap_exit,
                                am_end=am_end,
                                candidate_id=pid,
                                entry_id=entry_id,
                                htf_id=htf_id,
                                fire_key=EXIT_FIRE_KEY[exit_id],
                                exit_full_id=exit_id,
                                econ=econ,
                                ex=ex,
                                filled=filled,
                            )
                        )
            leak["FUTURE_HTF_BAR_N"] += int(sig_leak["FUTURE_HTF_BAR_N"])
            leak["SAME_BUCKET_CARRYBACK_N"] += int(sig_leak["SAME_BUCKET_CARRYBACK_N"])
            leak["CROSS_SESSION_HTF_BAR_N"] += int(sig_leak["CROSS_SESSION_HTF_BAR_N"])
            leak["FUTURE_SESSION_VWAP_CARRYBACK_N"] += int(sig_leak["FUTURE_SESSION_VWAP_CARRYBACK_N"])
        AUDIT["CURRENT_PRICE_TIME_AS_BOARD_FRESH_N"] += int(leak["CURRENT_PRICE_TIME_AS_BOARD_FRESH_N"])
        AUDIT["FUTURE_HTF_BAR_N"] += int(leak["FUTURE_HTF_BAR_N"])
        AUDIT["PARTIAL_HTF_BAR_USED_N"] += int(leak["PARTIAL_HTF_BAR_USED_N"])
        AUDIT["SAME_BUCKET_CARRYBACK_N"] += int(leak["SAME_BUCKET_CARRYBACK_N"])
        AUDIT["CROSS_SESSION_HTF_BAR_N"] += int(leak["CROSS_SESSION_HTF_BAR_N"])
        AUDIT["FUTURE_SESSION_VWAP_CARRYBACK_N"] += int(leak["FUTURE_SESSION_VWAP_CARRYBACK_N"])
        print(f"{day} DEVELOPMENT events={events_n} canary_sig={canary_sig} entry_sig={c1_sig} last_et={last_et}", flush=True)
        ok = (
            int(leak["BAR_INTEG_FAIL_N"]) == 0
            and int(leak["FUTURE_BAR_N"]) == 0
            and int(leak["CURRENT_PRICE_TIME_AS_BOARD_FRESH_N"]) == 0
            and int(leak["FUTURE_HTF_BAR_N"]) == 0
            and int(leak["PARTIAL_HTF_BAR_USED_N"]) == 0
            and int(leak["SAME_BUCKET_CARRYBACK_N"]) == 0
            and int(leak["CROSS_SESSION_HTF_BAR_N"]) == 0
            and int(leak["FUTURE_SESSION_VWAP_CARRYBACK_N"]) == 0
        )
        return {
            "ok": ok,
            "date": day,
            "rows_by": rows_by,
            "leak": leak,
            "events_n": events_n,
            "canary_sig": canary_sig,
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
            "blocker": None if ok else "BAR_OR_HTF_OR_FRESHNESS_LEAK",
        }
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }


def indicators_of(raw: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    from research.systematic_state_transition_full_strategy_v1.states import indicators

    return indicators(raw)


def harvest_development() -> dict[str, Any]:
    CACHE.mkdir(parents=True, exist_ok=True)
    # V1 Z3-only grids may exist under V1_FULL_STRATEGY_CACHE. They are never loaded.
    _ = V1_FULL_STRATEGY_CACHE
    invs, blockers = sealed_dev_caps()
    if blockers:
        return {"ok": False, "blocker": "CAPTURE", "blockers": blockers, "day_ok": {}, "PAIR_RERUN_N": 0, "PRIOR_Z3_METRIC_REUSE_N": 0}
    keys = [CANARY_ROW_ID] + list(FROZEN_PAIRS)
    rows_by: dict[str, list[dict[str, Any]]] = {c: [] for c in keys}
    day_ok: dict[str, bool] = {}
    for inv in invs:
        day = str(inv["date"])
        cache = CACHE / f"DEVELOPMENT_{day}_grid.json"
        saved = _load(cache)
        if saved.get("ok") and str(saved.get("date") or "") == day and saved.get("rows_by") and str(saved.get("engine") or "") == "C1_MULTI_TIMEFRAME_ENTRY_EXIT_FULL_STRATEGY_V2":
            extra = [c for c in (saved.get("rows_by") or {}) if c not in keys]
            if extra:
                AUDIT["EXTRA_CANDIDATE_N"] += len(extra)
                return {"ok": False, "blocker": "EXTRA_CANDIDATE", "day_ok": day_ok, "PAIR_RERUN_N": 0, "PRIOR_Z3_METRIC_REUSE_N": 0}
            missing = [c for c in keys if c not in (saved.get("rows_by") or {})]
            if missing:
                return {"ok": False, "blocker": "CACHE_MISSING_CANDIDATE", "day_ok": day_ok, "PAIR_RERUN_N": 0, "PRIOR_Z3_METRIC_REUSE_N": 0}
            if any("Z4_TRAILING_STRUCTURE" in c for c in (saved.get("rows_by") or {})):
                AUDIT["Z4_APPEARED_N"] += 1
                return {"ok": False, "blocker": "Z4_IN_CACHE", "day_ok": day_ok, "PAIR_RERUN_N": 0, "PRIOR_Z3_METRIC_REUSE_N": 0}
            for c, xs in (saved.get("rows_by") or {}).items():
                rows_by.setdefault(c, []).extend(list(xs or []))
            saved_leak = dict(saved.get("leak") or {})
            for k in (
                "CURRENT_PRICE_TIME_AS_BOARD_FRESH_N",
                "FUTURE_HTF_BAR_N",
                "PARTIAL_HTF_BAR_USED_N",
                "SAME_BUCKET_CARRYBACK_N",
                "CROSS_SESSION_HTF_BAR_N",
                "FUTURE_SESSION_VWAP_CARRYBACK_N",
            ):
                AUDIT[k] = int(AUDIT.get(k) or 0) + int(saved_leak.get(k) or 0)
            day_ok[day] = True
            print(f"cache-hit DEVELOPMENT {day} engine=V2", flush=True)
            continue
        body = process_dev_day({"date": day, "capture_path": inv["capture_path"], "universe": list(inv["universe_symbols"])})
        day_ok[day] = bool(body.get("ok"))
        if not body.get("ok"):
            return {
                "ok": False,
                "blocker": f"DEVELOPMENT:{day}:{body.get('blocker')}",
                "day_ok": day_ok,
                "PAIR_RERUN_N": 0,
                "PRIOR_Z3_METRIC_REUSE_N": 0,
            }
        _dump(
            cache,
            {
                "ok": True,
                "date": day,
                "engine": "C1_MULTI_TIMEFRAME_ENTRY_EXIT_FULL_STRATEGY_V2",
                "PAIR_N": 40,
                "rows_by": body.get("rows_by"),
                "leak": body.get("leak"),
            },
        )
        for c, xs in (body.get("rows_by") or {}).items():
            rows_by.setdefault(c, []).extend(list(xs or []))
    pair_n = sum(1 for c in FROZEN_PAIRS if c in rows_by)
    return {
        "ok": True,
        "rows_by": rows_by,
        "day_ok": day_ok,
        "audit": dict(AUDIT),
        "PAIR_RERUN_N": int(pair_n),
        "PRIOR_Z3_METRIC_REUSE_N": int(AUDIT["PRIOR_Z3_METRIC_REUSE_N"]),
        "PRIOR_Z3_GRID_READ_N": int(AUDIT["PRIOR_Z3_GRID_READ_N"]),
    }
