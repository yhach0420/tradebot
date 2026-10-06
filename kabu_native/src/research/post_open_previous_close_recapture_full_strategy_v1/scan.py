"""Post-open PreviousClose support and eligible recapture counts. No PnL."""
from __future__ import annotations

import gzip
import pickle
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

NATIVE = Path(__file__).resolve().parents[3]
if str(NATIVE / "src") not in sys.path:
    sys.path.insert(0, str(NATIVE / "src"))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import _bare, iter_push, record_event_stamp
from research.post_open_previous_close_recapture_full_strategy_v1 import (
    DEVELOPMENT_DAYS,
    MIN_ELIGIBLE_DAY_N,
    MIN_ELIGIBLE_EVENT_N,
    MIN_ELIGIBLE_SYMBOL_N,
    SESSION_FLATTEN_HM,
)
from research.post_open_previous_close_recapture_full_strategy_v1.fields import _f, ingress_epoch, trusted_state
from research.post_open_previous_close_recapture_full_strategy_v1.fsm import PrevCloseFsm
from research.post_open_previous_close_recapture_full_strategy_v1.isolation import CACHE
from small_paper.v1r_live_dual_lane import session_end_for_position

JST = ZoneInfo("Asia/Tokyo")
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
    if d <= 0:
        return 0.0
    return float(n) / float(d)


def _iso(t: Optional[float]) -> Optional[str]:
    if t is None:
        return None
    return datetime.fromtimestamp(float(t), tz=JST).isoformat()


def scan_day(payload: dict[str, Any]) -> dict[str, Any]:
    from research.post_open_previous_close_recapture_full_strategy_v1.harvest import assert_dev_only_day

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
    first_t = last_t = None
    fsms: dict[str, PrevCloseFsm] = {
        s: PrevCloseFsm(symbol=s, date=day, flatten_t=flatten_t, am_start=am_start) for s in universe
    }
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
        if t < am_start - 120.0 or t > am_end + 2.0:
            continue
        st = trusted_state(pay, event_t=t)
        post_open = bool(
            st["opened"]
            and st["continuous"]
            and (not st["special"])
            and (not st["preopen"])
            and t >= am_start
            and t + 1e-12 < flatten_t
        )
        if post_open:
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
                if first_t is None or t < float(first_t):
                    first_t = t
                last_t = t if last_t is None or t > float(last_t) else last_t
            prev = last_val.get(sym)
            if v is not None and prev is not None and float(v) != float(prev):
                change += 1
            last_val[sym] = float(v) if v is not None else None
        fsms[sym].process(ingress_t=t, payload=pay)
    sigs = []
    below_n = recap_n = 0
    sig_syms: set[str] = set()
    for s in universe:
        below_n += int(fsms[s].below_event_n)
        recap_n += int(fsms[s].recapture_n)
        for sig in fsms[s].signals:
            sigs.append(sig)
            sig_syms.add(s)
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
        "change_event_n": change,
        "first_t": first_t,
        "last_t": last_t,
        "below_event_n": below_n,
        "recapture_n": recap_n,
        "eligible_event_n": len(sigs),
        "eligible_symbol_n": len(sig_syms),
        "eligible_symbols": list(sig_syms),
    }


def _merge(days: list[dict[str, Any]]) -> dict[str, Any]:
    n = non_null = finite = zero = nonzero = change = 0
    uniq = 0
    below = recap = eligible = 0
    days_el: set[str] = set()
    syms: set[str] = set()
    first_t = last_t = None
    per_day: dict[str, int] = {}
    for d in days:
        n += int(d.get("n") or 0)
        non_null += int(d.get("non_null") or 0)
        finite += int(d.get("finite") or 0)
        zero += int(d.get("zero") or 0)
        nonzero += int(d.get("nonzero") or 0)
        uniq = max(uniq, int(d.get("unique_value_n") or 0))
        change += int(d.get("change_event_n") or 0)
        below += int(d.get("below_event_n") or 0)
        recap += int(d.get("recapture_n") or 0)
        el = int(d.get("eligible_event_n") or 0)
        eligible += el
        per_day[str(d.get("date"))] = el
        if el > 0:
            days_el.add(str(d.get("date")))
        for s in list(d.get("eligible_symbols") or []):
            syms.add(str(s))
        ft = d.get("first_t")
        lt = d.get("last_t")
        if ft is not None:
            first_t = float(ft) if first_t is None else min(float(first_t), float(ft))
        if lt is not None:
            last_t = float(lt) if last_t is None else max(float(last_t), float(lt))
    finite_rate = _pct(finite, n)
    return {
        "row_n": n,
        "non_null_rate": _pct(non_null, n),
        "finite_rate": finite_rate,
        "zero_rate": _pct(zero, n),
        "nonzero_rate": _pct(nonzero, n),
        "unique_value_n": uniq,
        "change_event_n": int(change),
        "first_post_open_valid_time": _iso(first_t),
        "last_post_open_valid_time": _iso(last_t),
        "below_event_n": int(below),
        "recapture_n": int(recap),
        "eligible_event_n": int(eligible),
        "eligible_day_n": len(days_el),
        "eligible_symbol_n": len(syms),
        "per_day_eligible_n": per_day,
        "CAUSAL_USABLE": bool(finite_rate >= MIN_FINITE_RATE and n > 0),
        "BREADTH_OK": int(eligible) >= MIN_ELIGIBLE_EVENT_N
        and len(days_el) >= MIN_ELIGIBLE_DAY_N
        and len(syms) >= MIN_ELIGIBLE_SYMBOL_N,
    }


def gate_support(*, semantics_ok: bool, merged: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    if not semantics_ok:
        reasons.append("SEMANTICS_NOT_PROVEN")
    if not merged.get("CAUSAL_USABLE"):
        reasons.append("PREVIOUSCLOSE_NOT_USABLE")
    if int(merged.get("eligible_event_n") or 0) < MIN_ELIGIBLE_EVENT_N:
        reasons.append("ELIGIBLE_EVENT<40")
    if int(merged.get("eligible_day_n") or 0) < MIN_ELIGIBLE_DAY_N:
        reasons.append("ELIGIBLE_DAY<8")
    if int(merged.get("eligible_symbol_n") or 0) < MIN_ELIGIBLE_SYMBOL_N:
        reasons.append("ELIGIBLE_SYMBOL<20")
    kind = "PASS"
    if not semantics_ok:
        kind = "SEMANTICS"
    elif reasons:
        kind = "INPUT"
    return {
        "PASS": not reasons,
        "KIND": kind,
        "REASONS": reasons,
        "GATES": {
            "trusted_semantics": bool(semantics_ok),
            "previous_close_usable": bool(merged.get("CAUSAL_USABLE")),
            "eligible_event_n_ge_40": int(merged.get("eligible_event_n") or 0) >= MIN_ELIGIBLE_EVENT_N,
            "eligible_day_n_ge_8": int(merged.get("eligible_day_n") or 0) >= MIN_ELIGIBLE_DAY_N,
            "eligible_symbol_n_ge_20": int(merged.get("eligible_symbol_n") or 0) >= MIN_ELIGIBLE_SYMBOL_N,
            "CURRENT_PRICE_TIME_INFERENCE": False,
        },
    }


def scan_post_open(*, semantics_ok: bool) -> dict[str, Any]:
    from research.post_open_previous_close_recapture_full_strategy_v1.harvest import sealed_dev_caps

    CACHE.mkdir(parents=True, exist_ok=True)
    cache = CACHE / "PCR_SCAN.pkl.gz"
    saved = _load_gz(cache)
    if saved.get("ok") and list(saved.get("days_ok") or []) == list(DEVELOPMENT_DAYS):
        print("cache-hit PCR SCAN", flush=True)
        support = gate_support(semantics_ok=semantics_ok, merged=dict(saved.get("fields") or {}))
        saved["support"] = support
        return saved
    invs, blockers = sealed_dev_caps()
    if blockers:
        return {"ok": False, "blocker": "CAPTURE", "blockers": blockers}
    day_bodies: list[dict[str, Any]] = []
    for inv in invs:
        day = str(inv["date"])
        day_cache = CACHE / f"PCR_SCAN_{day}.pkl.gz"
        hit = _load_gz(day_cache)
        if hit.get("ok") and str(hit.get("date") or "") == day:
            print(f"cache-hit PCR SCAN {day}", flush=True)
            day_bodies.append(hit)
            continue
        print(f"PHASE SCAN {day}", flush=True)
        body = scan_day({"date": day, "capture_path": inv["capture_path"], "universe": list(inv["universe_symbols"])})
        slim = dict(body)
        _dump_gz(day_cache, slim)
        day_bodies.append(body)
        print(
            f"{day} SCAN continuous={body.get('continuous_n')} eligible={body.get('eligible_event_n')} "
            f"below={body.get('below_event_n')}",
            flush=True,
        )
    merged = _merge(day_bodies)
    support = gate_support(semantics_ok=semantics_ok, merged=merged)
    out = {
        "ok": True,
        "days_ok": list(DEVELOPMENT_DAYS),
        "fields": merged,
        "support": support,
        "day_rows": [
            {
                "date": d.get("date"),
                "continuous_n": d.get("continuous_n"),
                "eligible_event_n": d.get("eligible_event_n"),
                "below_event_n": d.get("below_event_n"),
            }
            for d in day_bodies
        ],
        "CURRENT_PRICE_TIME_INFERENCE": False,
    }
    _dump_gz(cache, out)
    return out
