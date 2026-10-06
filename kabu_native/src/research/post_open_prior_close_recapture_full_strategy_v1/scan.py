"""Post-open PreviousClose dynamics and eligible below→recapture events."""
from __future__ import annotations

import gzip
import pickle
import sys
from pathlib import Path
from typing import Any, Optional

NATIVE = Path(__file__).resolve().parents[3]
if str(NATIVE / "src") not in sys.path:
    sys.path.insert(0, str(NATIVE / "src"))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import _bare, iter_push, record_event_stamp
from research.post_open_prior_close_recapture_full_strategy_v1 import (
    DEVELOPMENT_DAYS,
    EQ_COPY_MAX,
    MIN_ELIGIBLE_DAY_N,
    MIN_ELIGIBLE_EVENT_N,
    MIN_ELIGIBLE_SYMBOL_N,
    SESSION_FLATTEN_HM,
)
from research.post_open_prior_close_recapture_full_strategy_v1.fields import _f, ingress_epoch, trusted_state
from research.post_open_prior_close_recapture_full_strategy_v1.fsm import PriorCloseFsm
from research.post_open_prior_close_recapture_full_strategy_v1.isolation import CACHE
from small_paper.v1r_live_dual_lane import session_end_for_position

MIN_FINITE_RATE = 0.50


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


def _pct(n: int, d: int) -> float:
    return 0.0 if d <= 0 else float(n) / float(d)


def scan_day(payload: dict[str, Any]) -> dict[str, Any]:
    from research.post_open_prior_close_recapture_full_strategy_v1.harvest import assert_dev_only_day

    day = str(payload["date"])
    assert_dev_only_day(day)
    capture = Path(payload["capture_path"])
    universe = [_bare(s) for s in list(payload["universe"]) if _bare(s)]
    uni = set(universe)
    am_start = float(hm_epoch(day, 9, 0))
    am_end = float(session_end_for_position(date=day, session="AM", fill_time=am_start + 60.0))
    flatten_t = float(hm_epoch(day, int(SESSION_FLATTEN_HM[0]), int(SESSION_FLATTEN_HM[1])))
    n = non_null = finite = zero = nonzero = change = 0
    uniques: set[float] = set()
    last_val: dict[str, Optional[float]] = {}
    eq_cp = both_cp = 0
    fsms = {s: PriorCloseFsm(symbol=s, date=day, flatten_t=flatten_t, am_start=am_start) for s in universe}
    continuous_n = 0
    for rec in iter_push(capture):
        sym = _bare(rec.get("symbol") or (rec.get("payload") or rec.get("original_payload") or {}).get("Symbol"))
        if not sym or sym not in uni:
            continue
        pay = dict(rec.get("payload") or rec.get("original_payload") or {})
        recv = record_event_stamp(rec)
        if recv:
            pay["received_at"] = recv
        ing = ingress_epoch(rec, pay)
        if ing is None:
            continue
        t = float(ing)
        if t < am_start or t + 1e-12 >= flatten_t or t > am_end + 2.0:
            continue
        st = trusted_state(pay, event_t=t)
        if not (st["opened"] and st["continuous"] and (not st["special"]) and (not st["preopen"])):
            fsms[sym].process(ingress_t=t, payload=pay)
            continue
        continuous_n += 1
        raw = pay.get("PreviousClose")
        v = _f(raw)
        n += 1
        if raw is not None and raw != "":
            non_null += 1
        if v is not None:
            finite += 1
            uniques.add(float(v))
            if abs(float(v)) < 1e-15:
                zero += 1
            else:
                nonzero += 1
        prev = last_val.get(sym)
        if v is not None and prev is not None and float(v) != float(prev):
            change += 1
        last_val[sym] = float(v) if v is not None else None
        cp = st["px"]
        if v is not None and cp is not None:
            both_cp += 1
            if abs(float(v) - float(cp)) < 1e-9:
                eq_cp += 1
        fsms[sym].process(ingress_t=t, payload=pay)
    sigs = [s for fsm in fsms.values() for s in fsm.signals]
    below_n = sum(int(fsm.below_event_n) for fsm in fsms.values())
    sq_n = sum(int(fsm.special_overlap_n) for fsm in fsms.values())
    sq_sig = sum(int(fsm.sq_on_signal_n) for fsm in fsms.values())
    return {
        "ok": True,
        "date": day,
        "continuous_n": continuous_n,
        "n": n,
        "non_null": non_null,
        "finite": finite,
        "zero": zero,
        "nonzero": nonzero,
        "unique_value_n": len(uniques),
        "prev_change_event_n": change,
        "eq_cp": eq_cp,
        "both_cp": both_cp,
        "eligible_event_n": len(sigs),
        "eligible_symbol_n": len({s["symbol"] for s in sigs}),
        "below_event_n": below_n,
        "special_otu_n": sq_n,
        "sq_on_signal_n": sq_sig,
        "signal_symbols": list({s["symbol"] for s in sigs}),
    }


def scan_post_open(*, semantics_ok: bool) -> dict[str, Any]:
    from research.post_open_prior_close_recapture_full_strategy_v1.harvest import sealed_dev_caps

    CACHE.mkdir(parents=True, exist_ok=True)
    cache = CACHE / "PCLOSE_SCAN.pkl.gz"
    saved = _load_gz(cache)
    if saved.get("ok") and list(saved.get("days_ok") or []) == list(DEVELOPMENT_DAYS):
        print("cache-hit PCLOSE SCAN", flush=True)
        return saved
    invs, blockers = sealed_dev_caps()
    if blockers:
        return {"ok": False, "blocker": "CAPTURE", "blockers": blockers}
    bodies: list[dict[str, Any]] = []
    for inv in invs:
        day = str(inv["date"])
        day_cache = CACHE / f"PCLOSE_SCAN_{day}.pkl.gz"
        hit = _load_gz(day_cache)
        if hit.get("ok") and str(hit.get("date") or "") == day:
            print(f"cache-hit PCLOSE SCAN {day}", flush=True)
            bodies.append(hit)
            continue
        print(f"PHASE SCAN {day}", flush=True)
        body = scan_day({"date": day, "capture_path": inv["capture_path"], "universe": list(inv["universe_symbols"])})
        _dump_gz(day_cache, body)
        bodies.append(body)
        print(f"{day} SCAN eligible={body.get('eligible_event_n')} below={body.get('below_event_n')}", flush=True)
    n = sum(int(d.get("n") or 0) for d in bodies)
    finite = sum(int(d.get("finite") or 0) for d in bodies)
    zero = sum(int(d.get("zero") or 0) for d in bodies)
    eligible = sum(int(d.get("eligible_event_n") or 0) for d in bodies)
    below = sum(int(d.get("below_event_n") or 0) for d in bodies)
    days_ok = [str(d.get("date")) for d in bodies if int(d.get("eligible_event_n") or 0) > 0]
    syms: set[str] = set()
    for d in bodies:
        for s in list(d.get("signal_symbols") or []):
            syms.add(str(s))
    eq_cp = sum(int(d.get("eq_cp") or 0) for d in bodies)
    both_cp = sum(int(d.get("both_cp") or 0) for d in bodies)
    eq_rate = _pct(eq_cp, both_cp)
    sq_sig = sum(int(d.get("sq_on_signal_n") or 0) for d in bodies)
    merged = {
        "row_n": n,
        "non_null_rate": _pct(sum(int(d.get("non_null") or 0) for d in bodies), n),
        "finite_rate": _pct(finite, n),
        "zero_rate": _pct(zero, n),
        "unique_value_n": max(int(d.get("unique_value_n") or 0) for d in bodies) if bodies else 0,
        "prev_change_event_n": sum(int(d.get("prev_change_event_n") or 0) for d in bodies),
        "eq_current_rate": eq_rate,
        "INDEPENDENT_OF_CURRENT": eq_rate < float(EQ_COPY_MAX),
        "below_event_n": below,
        "eligible_event_n": eligible,
        "eligible_day_n": len(days_ok),
        "eligible_symbol_n": len(syms),
        "per_day_eligible": {str(d.get("date")): int(d.get("eligible_event_n") or 0) for d in bodies},
        "sq_on_signal_n": sq_sig,
        "special_quote_overlap_rate": _pct(sq_sig, eligible),
        "CAUSAL_USABLE": _pct(finite, n) >= MIN_FINITE_RATE and n > 0,
    }
    reasons = []
    if not semantics_ok:
        reasons.append("SEMANTICS")
    if not merged["CAUSAL_USABLE"]:
        reasons.append("PREVIOUSCLOSE_NOT_USABLE")
    if not merged["INDEPENDENT_OF_CURRENT"]:
        reasons.append("PREVIOUSCLOSE_EQ_CURRENT")
    if int(merged["eligible_event_n"]) < MIN_ELIGIBLE_EVENT_N:
        reasons.append("ELIGIBLE_EVENT<40")
    if int(merged["eligible_day_n"]) < MIN_ELIGIBLE_DAY_N:
        reasons.append("ELIGIBLE_DAY<8")
    if int(merged["eligible_symbol_n"]) < MIN_ELIGIBLE_SYMBOL_N:
        reasons.append("ELIGIBLE_SYMBOL<20")
    support = {"PASS": not reasons, "REASONS": reasons, "KIND": None if not reasons else "INPUT"}
    out = {
        "ok": True,
        "days_ok": list(DEVELOPMENT_DAYS),
        "fields": merged,
        "support": support,
        "CURRENT_PRICE_TIME_INFERENCE": False,
    }
    _dump_gz(cache, out)
    return out
