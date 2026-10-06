"""Post-open CalcPrice dynamics and independence vs CurrentPrice/Bid1/Ask1/mid."""
from __future__ import annotations

import gzip
import pickle
import sys
from collections import defaultdict
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
from research.post_open_calc_price_lead_acceptance_full_strategy_v1 import (
    DEVELOPMENT_DAYS,
    EQ_COPY_MAX,
    SESSION_FLATTEN_HM,
)
from research.post_open_calc_price_lead_acceptance_full_strategy_v1.calc import _f, ingress_epoch, trusted_state
from research.post_open_calc_price_lead_acceptance_full_strategy_v1.isolation import CACHE
from small_paper.v1r_live_dual_lane import session_end_for_position

JST = ZoneInfo("Asia/Tokyo")
MIN_CHANGE_DAY_N = 8
MIN_CHANGE_SYMBOL_N = 50
MIN_CHANGE_SPAN_SEC = 60.0
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


def _eq(a: Optional[float], b: Optional[float]) -> bool:
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) < 1e-9


def _iso(t: Optional[float]) -> Optional[str]:
    if t is None:
        return None
    return datetime.fromtimestamp(float(t), tz=JST).isoformat()


def scan_day(payload: dict[str, Any]) -> dict[str, Any]:
    from research.post_open_calc_price_lead_acceptance_full_strategy_v1.harvest import assert_dev_only_day

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
    change_syms: set[str] = set()
    per_sym: dict[str, int] = defaultdict(int)
    last_val: dict[str, Optional[float]] = {}
    run_len: dict[str, int] = {}
    max_run = 0
    first_t = last_t = first_ch = last_ch = None
    first_finite = None
    eq_cp = both_cp = eq_bid = both_bid = eq_ask = both_ask = eq_mid = both_mid = 0
    events_n = continuous_n = 0
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
        events_n += 1
        st = trusted_state(pay, event_t=t)
        if not (st["opened"] and st["continuous"] and (not st["special"]) and (not st["preopen"])):
            continue
        continuous_n += 1
        raw = pay.get("CalcPrice")
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
            if first_finite is None:
                first_finite = t
        prev = last_val.get(sym)
        if v is not None and prev is not None and float(v) != float(prev):
            change += 1
            change_syms.add(sym)
            per_sym[sym] += 1
            if first_ch is None or t < float(first_ch):
                first_ch = t
            last_ch = t
            run_len[sym] = 1
        elif v is not None:
            run_len[sym] = int(run_len.get(sym) or 0) + 1
            if run_len[sym] > max_run:
                max_run = run_len[sym]
        last_val[sym] = float(v) if v is not None else None
        if v is None:
            run_len[sym] = 0
        cp = st["px"]
        bid = st["bid"]
        ask = st["ask"]
        if v is not None and cp is not None:
            both_cp += 1
            if _eq(v, cp):
                eq_cp += 1
        if v is not None and bid is not None:
            both_bid += 1
            if _eq(v, bid):
                eq_bid += 1
        if v is not None and ask is not None:
            both_ask += 1
            if _eq(v, ask):
                eq_ask += 1
        if v is not None and bid is not None and ask is not None:
            mid = (float(bid) + float(ask)) / 2.0
            both_mid += 1
            if _eq(v, mid):
                eq_mid += 1
    span = bool(first_finite is not None and last_ch is not None and (float(last_ch) - float(first_finite)) >= MIN_CHANGE_SPAN_SEC)
    return {
        "ok": True,
        "date": day,
        "events_n": events_n,
        "continuous_n": continuous_n,
        "n": n,
        "non_null": non_null,
        "finite": finite,
        "zero": zero,
        "nonzero": nonzero,
        "unique_value_n": len(uniques),
        "change_event_n": change,
        "change_symbol_n": len(change_syms),
        "change_symbols": list(change_syms),
        "max_unchanged_ingress_run": max_run,
        "first_t": first_t,
        "last_t": last_t,
        "first_ch": first_ch,
        "last_ch": last_ch,
        "span": span,
        "eq_cp": eq_cp,
        "both_cp": both_cp,
        "eq_bid": eq_bid,
        "both_bid": both_bid,
        "eq_ask": eq_ask,
        "both_ask": both_ask,
        "eq_mid": eq_mid,
        "both_mid": both_mid,
    }


def _merge(days: list[dict[str, Any]]) -> dict[str, Any]:
    n = non_null = finite = zero = nonzero = change = 0
    uniq = 0
    days_ch: set[str] = set()
    syms: set[str] = set()
    span_n = 0
    max_run = 0
    first_t = last_t = None
    first_ch_s = last_ch_s = None
    per_day: dict[str, int] = {}
    eq_cp = both_cp = eq_bid = both_bid = eq_ask = both_ask = eq_mid = both_mid = 0
    for d in days:
        n += int(d.get("n") or 0)
        non_null += int(d.get("non_null") or 0)
        finite += int(d.get("finite") or 0)
        zero += int(d.get("zero") or 0)
        nonzero += int(d.get("nonzero") or 0)
        uniq = max(uniq, int(d.get("unique_value_n") or 0))
        ch = int(d.get("change_event_n") or 0)
        change += ch
        per_day[str(d.get("date"))] = ch
        if ch > 0:
            days_ch.add(str(d.get("date")))
        for s in list(d.get("change_symbols") or []):
            syms.add(str(s))
        if d.get("span"):
            span_n += 1
        max_run = max(max_run, int(d.get("max_unchanged_ingress_run") or 0))
        ft = d.get("first_t")
        lt = d.get("last_t")
        if ft is not None:
            first_t = float(ft) if first_t is None else min(float(first_t), float(ft))
        if lt is not None:
            last_t = float(lt) if last_t is None else max(float(last_t), float(lt))
        if d.get("first_ch") is not None:
            s = _iso(d.get("first_ch"))
            first_ch_s = s if first_ch_s is None else min(first_ch_s, s)
        if d.get("last_ch") is not None:
            s = _iso(d.get("last_ch"))
            last_ch_s = s if last_ch_s is None else max(last_ch_s, s)
        eq_cp += int(d.get("eq_cp") or 0)
        both_cp += int(d.get("both_cp") or 0)
        eq_bid += int(d.get("eq_bid") or 0)
        both_bid += int(d.get("both_bid") or 0)
        eq_ask += int(d.get("eq_ask") or 0)
        both_ask += int(d.get("both_ask") or 0)
        eq_mid += int(d.get("eq_mid") or 0)
        both_mid += int(d.get("both_mid") or 0)
    eq_current_rate = _pct(eq_cp, both_cp)
    eq_bid_rate = _pct(eq_bid, both_bid)
    eq_ask_rate = _pct(eq_ask, both_ask)
    eq_mid_rate = _pct(eq_mid, both_mid)
    finite_rate = _pct(finite, n)
    not_const = change > 0 and uniq > 1 and len(days_ch) >= MIN_CHANGE_DAY_N and span_n >= MIN_CHANGE_DAY_N
    independent = eq_current_rate < float(EQ_COPY_MAX)
    return {
        "row_n": n,
        "non_null_rate": _pct(non_null, n),
        "finite_rate": finite_rate,
        "zero_rate": _pct(zero, n),
        "nonzero_rate": _pct(nonzero, n),
        "unique_value_n": uniq,
        "change_event_n": int(change),
        "change_day_n": len(days_ch),
        "change_symbol_n": len(syms),
        "change_span_day_n": span_n,
        "per_day_change_event_n": per_day,
        "max_unchanged_ingress_run": max_run,
        "first_post_open_valid_time": _iso(first_t),
        "last_post_open_valid_time": _iso(last_t),
        "first_change_time": first_ch_s,
        "last_change_time": last_ch_s,
        "eq_current_rate": eq_current_rate,
        "eq_bid1_rate": eq_bid_rate,
        "eq_ask1_rate": eq_ask_rate,
        "eq_mid_rate": eq_mid_rate,
        "eq_current_n": eq_cp,
        "both_current_n": both_cp,
        "NOT_CONSTANT": bool(not_const),
        "INDEPENDENT_OF_CURRENT": bool(independent),
        "CAUSAL_USABLE": bool(finite_rate >= MIN_FINITE_RATE and n > 0),
        "BREADTH_OK": len(syms) >= MIN_CHANGE_SYMBOL_N and len(days_ch) >= MIN_CHANGE_DAY_N,
        "EQ_COPY_MAX": float(EQ_COPY_MAX),
    }


def gate_support(*, semantics_ok: bool, merged: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    kind = "PASS"
    if not semantics_ok:
        reasons.append("SEMANTICS_NOT_PROVEN")
        kind = "SEMANTICS"
    if not merged.get("INDEPENDENT_OF_CURRENT"):
        reasons.append("CALC_EQ_CURRENTPRICE")
        if kind == "PASS":
            kind = "INDEP"
    if not merged.get("CAUSAL_USABLE"):
        reasons.append("NOT_CAUSAL_USABLE")
        if kind == "PASS":
            kind = "INPUT"
    if int(merged.get("change_day_n") or 0) < MIN_CHANGE_DAY_N:
        reasons.append("CHANGE_DAY<8")
        if kind == "PASS":
            kind = "INPUT"
    if int(merged.get("change_symbol_n") or 0) < MIN_CHANGE_SYMBOL_N:
        reasons.append("CHANGE_SYMBOL<50")
        if kind == "PASS":
            kind = "INPUT"
    if not merged.get("NOT_CONSTANT"):
        reasons.append("CONSTANT_OR_WEAK_DYNAMICS")
        if kind == "PASS":
            kind = "INPUT"
    ok = not reasons
    if ok:
        kind = "PASS"
    return {
        "PASS": bool(ok),
        "KIND": None if ok else kind,
        "REASONS": reasons,
        "GATES": {
            "trusted_semantics": bool(semantics_ok),
            "post_open_usable": bool(merged.get("CAUSAL_USABLE")),
            "change_day_n_ge_8": int(merged.get("change_day_n") or 0) >= MIN_CHANGE_DAY_N,
            "change_symbol_n_ge_50": int(merged.get("change_symbol_n") or 0) >= MIN_CHANGE_SYMBOL_N,
            "not_constant": bool(merged.get("NOT_CONSTANT")),
            "not_currentprice_copy": bool(merged.get("INDEPENDENT_OF_CURRENT")),
            "CURRENT_PRICE_TIME_INFERENCE": False,
        },
        "EQ_COPY_MAX": float(EQ_COPY_MAX),
    }


def scan_post_open(*, semantics_ok: bool) -> dict[str, Any]:
    from research.post_open_calc_price_lead_acceptance_full_strategy_v1.harvest import sealed_dev_caps

    CACHE.mkdir(parents=True, exist_ok=True)
    cache = CACHE / "CALC_SCAN.pkl.gz"
    saved = _load_gz(cache)
    if saved.get("ok") and list(saved.get("days_ok") or []) == list(DEVELOPMENT_DAYS):
        print("cache-hit CALC SCAN", flush=True)
        support = gate_support(semantics_ok=semantics_ok, merged=dict(saved.get("fields") or {}))
        saved["support"] = support
        return saved
    invs, blockers = sealed_dev_caps()
    if blockers:
        return {"ok": False, "blocker": "CAPTURE", "blockers": blockers}
    day_bodies: list[dict[str, Any]] = []
    for inv in invs:
        day = str(inv["date"])
        day_cache = CACHE / f"CALC_SCAN_{day}.pkl.gz"
        hit = _load_gz(day_cache)
        if hit.get("ok") and str(hit.get("date") or "") == day:
            print(f"cache-hit CALC SCAN {day}", flush=True)
            day_bodies.append(hit)
            continue
        print(f"PHASE SCAN {day}", flush=True)
        body = scan_day({"date": day, "capture_path": inv["capture_path"], "universe": list(inv["universe_symbols"])})
        slim = dict(body)
        slim.pop("change_symbols", None)
        _dump_gz(day_cache, {**slim, "change_symbols": body.get("change_symbols")})
        day_bodies.append(body)
        print(
            f"{day} SCAN continuous={body.get('continuous_n')} change={body.get('change_event_n')} "
            f"eq_cp={body.get('eq_cp')}/{body.get('both_cp')}",
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
                "change_event_n": d.get("change_event_n"),
                "eq_current_rate": _pct(int(d.get("eq_cp") or 0), int(d.get("both_cp") or 0)),
            }
            for d in day_bodies
        ],
        "CURRENT_PRICE_TIME_INFERENCE": False,
    }
    _dump_gz(cache, out)
    return out
