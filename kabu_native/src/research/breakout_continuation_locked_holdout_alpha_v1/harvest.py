"""DEV reuse from measurement decomp. Holdout stream only after PRIMARY freeze. Stress sealed."""
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
from research.breakout_continuation_locked_holdout_alpha_v1 import (
    DEVELOPMENT_DAYS,
    FORBIDDEN_INPUT_DAYS,
    LOCKED_HOLDOUT_DAYS,
    MAX_RESEARCH_DATE,
    STRESS_DAYS,
)
from research.breakout_continuation_locked_holdout_alpha_v1.isolation import CACHE, DECOMP_CACHE, TODAY
from research.e1_x28_executable_joint import BOARD_FRESHNESS_SEC as E1_FRESH
from research.entry_edge_measurement_decomposition_v1.harvest import attach_economics
from research.new_entry_breakout_continuation_v1.harvest import (
    BOARD_FRESHNESS_SEC,
    BoardTape,
    board_row,
)
from research.new_entry_breakout_continuation_v1.rule import session_vwap, signal_at
from research.simple_tech_entry_family.bars import SymbolBarBuilder, bar_integrity
from small_paper.v1r_live_dual_lane import session_end_for_position

AUDIT = {
    "HOLDOUT_READ_BEFORE_PRIMARY_FREEZE_N": 0,
    "STRESS_READ_N": 0,
    "VWAP_HOLDOUT_READ_N": 0,
    "RULE_CHANGE_AFTER_HOLDOUT_OPEN_N": 0,
    "PARAMETER_CHANGE_N": 0,
    "HOLDOUT_USED_FOR_THRESHOLD_N": 0,
    "FUTURE_DATA_N": 0,
    "CURRENT_PRICE_TIME_AS_BOARD_FRESH_N": 0,
    "DEV_PARITY_FAIL_N": 0,
    "SPLIT_LEAKAGE_N": 0,
}


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _dump(path: Path, body: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, ensure_ascii=False, default=str), encoding="utf-8")


def freeze_path() -> Path:
    return CACHE / "primary_freeze.json"


def freeze_active() -> bool:
    body = _load(freeze_path())
    return bool(body.get("PRIMARY_SELECTION_FROZEN_BEFORE_HOLDOUT")) and bool(body.get("ENTRY_RULE_FROZEN"))


def assert_holdout_day(day: str) -> None:
    d = str(day)
    if d in STRESS_DAYS:
        AUDIT["STRESS_READ_N"] += 1
        raise RuntimeError("STRESS_READ")
    if d in FORBIDDEN_INPUT_DAYS or d > MAX_RESEARCH_DATE:
        AUDIT["FUTURE_DATA_N"] += 1
        raise RuntimeError(f"FUTURE:{d}")
    if d not in LOCKED_HOLDOUT_DAYS:
        AUDIT["FUTURE_DATA_N"] += 1
        raise RuntimeError(f"NON_HOLDOUT:{d}")
    if not freeze_active():
        AUDIT["HOLDOUT_READ_BEFORE_PRIMARY_FREEZE_N"] += 1
        raise RuntimeError("HOLDOUT_READ_BEFORE_PRIMARY_FREEZE")


def load_dev_breakout_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for day in DEVELOPMENT_DAYS:
        if day in LOCKED_HOLDOUT_DAYS or day in STRESS_DAYS:
            AUDIT["FUTURE_DATA_N"] += 1
            raise RuntimeError("DEV_SPLIT_LEAK")
        path = DECOMP_CACHE / f"DEVELOPMENT_{day}_decomp.json"
        body = _load(path)
        if not body.get("ok"):
            raise RuntimeError(f"DEV_DECOMP_MISSING:{day}")
        rows.extend(list(body.get("breakout") or []))
    return rows


def sealed_holdout_caps() -> tuple[list[dict[str, Any]], list[str]]:
    from _p1_inventory import resolve_universe

    out: list[dict[str, Any]] = []
    blockers: list[str] = []
    for day in LOCKED_HOLDOUT_DAYS:
        try:
            assert_holdout_day(str(day))
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


def process_holdout_day(payload: dict[str, Any]) -> dict[str, Any]:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"
    day = str(payload["date"])
    try:
        assert_holdout_day(day)
    except RuntimeError as exc:
        return {"ok": False, "date": day, "blocker": str(exc)}
    if abs(float(BOARD_FRESHNESS_SEC) - float(E1_FRESH)) > 1e-12:
        return {"ok": False, "date": day, "blocker": "BOARD_FRESHNESS_DRIFT"}
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
        "FUTURE_BOARD_N": 0,
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

        signals: list[dict[str, Any]] = []
        uncond: list[dict[str, Any]] = []
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
            vwap = session_vwap(raw["close"], raw["volume"], raw.get("vwap_num"))
            board = bufs[s].view()
            for i in range(n):
                t0 = float(raw["finalize_t"][i])
                base = {"date": day, "session": "AM", "symbol": s, "i": i}
                econ = attach_economics(dict(base), board, t0=t0, am_end=am_end, orig=None)
                if econ.get("eligible"):
                    uncond.append(econ)
                bits = signal_at(raw["high"], raw["close"], raw["volume"], vwap, i)
                if not bits["SIGNAL"]:
                    continue
                rec_s = attach_economics(
                    {
                        **base,
                        "P1": bits["P1"],
                        "P2": bits["P2"],
                        "P3": bits["P3"],
                        "FIRST_CROSS": bits["FIRST_CROSS"],
                        "close": float(raw["close"][i]),
                        "high": float(raw["high"][i]),
                        "volume": float(raw["volume"][i]),
                    },
                    board,
                    t0=t0,
                    am_end=am_end,
                    orig=None,
                )
                signals.append(rec_s)
        AUDIT["CURRENT_PRICE_TIME_AS_BOARD_FRESH_N"] += int(leak["CURRENT_PRICE_TIME_AS_BOARD_FRESH_N"])
        exe_n = sum(1 for r in signals if r.get("eligible"))
        print(
            f"{day} HOLDOUT events={events_n} signals={len(signals)} evaluable={exe_n} "
            f"uncond={len(uncond)} last_et={last_et}",
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
            "signals": signals,
            "unconditional": uncond,
            "leak": leak,
            "events_n": events_n,
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


def harvest_holdout() -> dict[str, Any]:
    CACHE.mkdir(parents=True, exist_ok=True)
    invs, blockers = sealed_holdout_caps()
    if blockers:
        return {"ok": False, "blocker": "CAPTURE", "blockers": blockers, "inventory": invs, "day_ok": {}}
    rows: list[dict[str, Any]] = []
    unc: list[dict[str, Any]] = []
    day_ok: dict[str, bool] = {}
    day_meta: list[dict[str, Any]] = []
    for inv in invs:
        day = str(inv["date"])
        cache = CACHE / f"HOLDOUT_{day}_alpha.json"
        saved = _load(cache)
        if saved.get("ok") and str(saved.get("date") or "") == day:
            rows.extend(list(saved.get("signals") or []))
            unc.extend(list(saved.get("unconditional") or []))
            day_ok[day] = True
            day_meta.append({"date": day, "cache": True})
            print(f"cache-hit HOLDOUT {day}", flush=True)
            continue
        body = process_holdout_day(
            {
                "date": day,
                "capture_path": inv["capture_path"],
                "universe": list(inv["universe_symbols"]),
            }
        )
        day_ok[day] = bool(body.get("ok"))
        if not body.get("ok"):
            return {
                "ok": False,
                "blocker": f"HOLDOUT:{day}:{body.get('blocker')}",
                "inventory": invs,
                "day_ok": day_ok,
            }
        _dump(cache, body)
        rows.extend(list(body.get("signals") or []))
        unc.extend(list(body.get("unconditional") or []))
        day_meta.append({"date": day, "cache": False, "leak": body.get("leak")})
    return {
        "ok": True,
        "rows": rows,
        "unconditional": unc,
        "day_ok": day_ok,
        "day_meta": day_meta,
        "audit": dict(AUDIT),
    }
