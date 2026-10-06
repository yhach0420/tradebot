"""DEV-only Capture stream. Canary R2_X1_Z3 + frozen 25. Stress/holdout sealed."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

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
from research.entry_edge_measurement_decomposition_v1.harvest import attach_economics
from research.new_entry_breakout_continuation_v1.harvest import BOARD_FRESHNESS_SEC, BoardTape, board_row
from research.recovery_sequence_full_strategy_architecture_v1.entries import session_vwap_arr
from research.recovery_sequence_full_strategy_architecture_v1.execution import evaluate_execution
from research.systematic_state_transition_full_strategy_v1 import (
    BURNED_HOLDOUT_DAYS,
    DEVELOPMENT_DAYS,
    FORBIDDEN_INPUT_DAYS,
    MAX_RESEARCH_DATE,
    STRESS_DAYS,
)
from research.systematic_state_transition_full_strategy_v1.entries import canary_r2_indices, frozen_candidates, library_signal_indices
from research.systematic_state_transition_full_strategy_v1.isolation import CACHE, TODAY
from research.systematic_state_transition_full_strategy_v1.spec import candidate_ids
from research.systematic_state_transition_full_strategy_v1.states import indicators
from replay.pnl_yen import compute_pnl_yen_100
from research.simple_full_strategy_discovery_v1.exits import last_session_bid, resolve_exit
from research.simple_tech_entry_family.bars import SymbolBarBuilder, bar_integrity
from small_paper.v1r_live_dual_lane import session_end_for_position

CANARY_ROW_ID = "R2_X1_Z3"

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


def _emit(
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
) -> dict[str, Any]:
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
            exit_id="Z3",
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
        "entry_id": candidate_id,
        "exec_id": "X1",
        "exit_id": "Z3",
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
    cids = candidate_ids()
    if len(cids) != 25:
        AUDIT["EXTRA_CANDIDATE_N"] += 1
        return {"ok": False, "date": day, "blocker": "CANDIDATE_N"}
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
    }
    lib = frozen_candidates()
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
        st_sig = 0
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
            r2 = canary_r2_indices(raw["open"], raw["high"], raw["low"], raw["close"], raw["volume"], vwap)
            canary_sig += len(r2)
            for i in r2:
                t0 = float(raw["finalize_t"][i])
                rows_by[CANARY_ROW_ID].append(
                    _emit(day=day, symbol=s, i=i, t0=t0, board=board, raw=raw, vwap=vwap, am_end=am_end, candidate_id=CANARY_ROW_ID)
                )
            for cand in lib:
                cid = str(cand["CANDIDATE_ID"])
                xs = library_signal_indices(ind, cand)
                st_sig += len(xs)
                for i in xs:
                    t0 = float(raw["finalize_t"][i])
                    rows_by[cid].append(
                        _emit(day=day, symbol=s, i=i, t0=t0, board=board, raw=raw, vwap=vwap, am_end=am_end, candidate_id=cid)
                    )
        AUDIT["CURRENT_PRICE_TIME_AS_BOARD_FRESH_N"] += int(leak["CURRENT_PRICE_TIME_AS_BOARD_FRESH_N"])
        print(f"{day} DEVELOPMENT events={events_n} canary_sig={canary_sig} st_sig={st_sig} last_et={last_et}", flush=True)
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
    invs, blockers = sealed_dev_caps()
    if blockers:
        return {"ok": False, "blocker": "CAPTURE", "blockers": blockers, "day_ok": {}}
    cids = candidate_ids()
    keys = [CANARY_ROW_ID] + cids
    rows_by: dict[str, list[dict[str, Any]]] = {c: [] for c in keys}
    day_ok: dict[str, bool] = {}
    for inv in invs:
        day = str(inv["date"])
        cache = CACHE / f"DEVELOPMENT_{day}_grid.json"
        saved = _load(cache)
        if saved.get("ok") and str(saved.get("date") or "") == day and saved.get("rows_by"):
            extra = [c for c in (saved.get("rows_by") or {}) if c not in keys]
            if extra:
                AUDIT["EXTRA_CANDIDATE_N"] += len(extra)
                return {"ok": False, "blocker": "EXTRA_CANDIDATE", "day_ok": day_ok}
            for c, xs in (saved.get("rows_by") or {}).items():
                rows_by.setdefault(c, []).extend(list(xs or []))
            day_ok[day] = True
            print(f"cache-hit DEVELOPMENT {day}", flush=True)
            continue
        body = process_dev_day({"date": day, "capture_path": inv["capture_path"], "universe": list(inv["universe_symbols"])})
        day_ok[day] = bool(body.get("ok"))
        if not body.get("ok"):
            return {"ok": False, "blocker": f"DEVELOPMENT:{day}:{body.get('blocker')}", "day_ok": day_ok}
        _dump(cache, {"ok": True, "date": day, "rows_by": body.get("rows_by"), "leak": body.get("leak")})
        for c, xs in (body.get("rows_by") or {}).items():
            rows_by.setdefault(c, []).extend(list(xs or []))
    return {"ok": True, "rows_by": rows_by, "day_ok": day_ok, "audit": dict(AUDIT)}
