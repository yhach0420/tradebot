"""LEGACY_DEV AM harvest. Pre-ENTRY features + standalone X1 labels. V5 OUT not written."""
from __future__ import annotations

import gzip
import os
import pickle
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
    iter_push,
    record_event_stamp,
)
from research.e1_x28_executable_joint import BOARD_FRESHNESS_SEC as E1_FRESH
from research.new_entry_breakout_continuation_v1.harvest import BOARD_FRESHNESS_SEC as HARVEST_FRESH
from research.new_entry_breakout_continuation_v1.harvest import BoardTape, board_row
from research.post_open_prior_close_recapture_full_strategy_v1.fields import ingress_epoch
from research.post_open_prior_close_recapture_full_strategy_v1.harvest import (
    AUDIT,
    assert_dev_only_day,
    emit_filled,
    sealed_dev_caps,
)
from research.prior_close_recapture_sustained_mechanism_v1 import (
    BOARD_FRESHNESS_SEC,
    DEVELOPMENT_DAYS,
    LABEL_FAILED,
    LABEL_SUSTAINED,
    LABEL_UNRESOLVED,
    PARENT_STRATEGY_ID,
    SESSION_FLATTEN_HM,
)
from research.prior_close_recapture_sustained_mechanism_v1.fsm import FeatureFsm
from research.prior_close_recapture_sustained_mechanism_v1.isolation import CACHE
from small_paper.v1r_live_dual_lane import session_end_for_position


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


def _label(*, would_fill: bool, fire_t: Optional[float], exit_t: Any, exit_reason: Any) -> tuple[Optional[str], bool]:
    if not would_fill:
        return None, False
    unresolved = exit_t is None or str(exit_reason or "") == "EXIT_MISS"
    if fire_t is not None:
        return LABEL_FAILED, unresolved
    return LABEL_SUSTAINED, unresolved


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
    if abs(float(HARVEST_FRESH) - float(BOARD_FRESHNESS_SEC)) > 1e-12:
        return {"ok": False, "date": day, "blocker": "HARVEST_FRESHNESS_DRIFT"}
    capture = Path(payload["capture_path"])
    universe = [_bare(s) for s in list(payload["universe"]) if _bare(s)]
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
        fsms: dict[str, FeatureFsm] = {
            s: FeatureFsm(symbol=s, date=day, flatten_t=flatten_t, am_start=am_start) for s in universe
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
            fsms[sym].process(ingress_t=float(ing), payload=pay, rec=rec)
            if events_n % 400000 == 0:
                print(f"{day} MECH events={events_n} last_et={last_et}", flush=True)

        rows: list[dict[str, Any]] = []
        integrity_n = 0
        frozen_n = 0
        for s in universe:
            integrity_n += int(fsms[s].integrity_n)
            board = bufs[s].view()
            by_ep = {str(r.get("episode_id")): r for r in fsms[s].frozen}
            frozen_n += len(fsms[s].frozen)
            for sig in fsms[s].signals:
                filled = emit_filled(sig=sig, board=board, fsm=fsms[s], am_end=am_end, flatten_t=flatten_t)
                feat = dict(by_ep.get(str(sig.get("episode_id"))) or {})
                fire_t = None
                if filled.get("WOULD_FILL") and filled.get("fill_t") is not None:
                    fire_t = fsms[s].first_exit_fire(fill_t=float(filled["fill_t"]), anchor=float(sig["anchor"]))
                alpha, unresolved = _label(
                    would_fill=bool(filled.get("WOULD_FILL")),
                    fire_t=fire_t,
                    exit_t=filled.get("exit_t"),
                    exit_reason=filled.get("exit_reason"),
                )
                rec = dict(filled)
                for k, v in feat.items():
                    if k not in rec or rec.get(k) is None:
                        rec[k] = v
                rec["alpha_label"] = alpha
                rec["execution_unresolved"] = bool(unresolved) if filled.get("WOULD_FILL") else False
                rec["standalone_fire_t"] = fire_t
                rec["CAP_USED_FOR_LABEL"] = False
                rows.append(rec)
        AUDIT["CURRENT_PRICE_TIME_AS_BOARD_FRESH_N"] += int(leak["CURRENT_PRICE_TIME_AS_BOARD_FRESH_N"])
        ok = int(leak["CURRENT_PRICE_TIME_AS_BOARD_FRESH_N"]) == 0
        print(
            f"{day} MECH DEVELOPMENT events={events_n} sig={len(rows)} frozen={frozen_n} integ={integrity_n}",
            flush=True,
        )
        return {
            "ok": ok,
            "date": day,
            "rows": rows,
            "integrity_n": integrity_n,
            "leak": leak,
            "events_n": events_n,
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
            "blocker": None if ok else "FRESHNESS_LEAK",
        }
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }


def harvest_mechanism() -> dict[str, Any]:
    CACHE.mkdir(parents=True, exist_ok=True)
    invs, blockers = sealed_dev_caps()
    if blockers:
        return {"ok": False, "blocker": "CAPTURE", "blockers": blockers, "day_ok": {}}
    rows: list[dict[str, Any]] = []
    day_ok: dict[str, bool] = {}
    integrity_n = 0
    for inv in invs:
        day = str(inv["date"])
        cache = CACHE / f"MECH_{day}.pkl.gz"
        saved = _load_gz(cache)
        if saved.get("ok") and str(saved.get("date") or "") == day and saved.get("rows") is not None:
            rows.extend(list(saved.get("rows") or []))
            integrity_n += int(saved.get("integrity_n") or 0)
            day_ok[day] = True
            print(f"cache-hit MECH {day}", flush=True)
            continue
        body = process_dev_day({"date": day, "capture_path": inv["capture_path"], "universe": list(inv["universe_symbols"])})
        day_ok[day] = bool(body.get("ok"))
        if not body.get("ok"):
            return {"ok": False, "blocker": f"DEVELOPMENT:{day}:{body.get('blocker')}", "day_ok": day_ok}
        _dump_gz(cache, {"ok": True, "date": day, "rows": body.get("rows"), "integrity_n": body.get("integrity_n")})
        rows.extend(list(body.get("rows") or []))
        integrity_n += int(body.get("integrity_n") or 0)
    return {
        "ok": True,
        "rows": rows,
        "integrity_n": integrity_n,
        "day_ok": day_ok,
        "audit": dict(AUDIT),
        "STRATEGY_ID": PARENT_STRATEGY_ID,
        "days": list(DEVELOPMENT_DAYS),
    }
