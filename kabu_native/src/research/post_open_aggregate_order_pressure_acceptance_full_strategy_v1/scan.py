"""Post-open 4-field dynamics. Economics not computed. LEGACY_DEV AM only."""
from __future__ import annotations

import gzip
import math
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
from research.anchor_vs_event_driven.run_comparison import _bare, find_capture_dir, iter_push, record_event_stamp
from research.post_open_aggregate_order_pressure_acceptance_full_strategy_v1 import (
    DEVELOPMENT_DAYS,
    PRESSURE_FIELDS,
    SESSION_FLATTEN_HM,
)
from research.post_open_aggregate_order_pressure_acceptance_full_strategy_v1.isolation import CACHE
from research.post_open_aggregate_order_pressure_acceptance_full_strategy_v1.pressure import _f, ingress_epoch, trusted_state
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


def _empty_field() -> dict[str, Any]:
    return {
        "n": 0,
        "non_null": 0,
        "finite": 0,
        "zero": 0,
        "nonzero": 0,
        "uniques": set(),
        "change_event_n": 0,
        "change_days": set(),
        "change_symbols": set(),
        "span_days": set(),
        "per_day_change": defaultdict(int),
        "per_symbol_change": defaultdict(int),
        "max_unchanged_run": 0,
        "first_t": None,
        "last_t": None,
        "first_change_t": None,
        "last_change_t": None,
    }


def _iso(t: Optional[float]) -> Optional[str]:
    if t is None:
        return None
    return datetime.fromtimestamp(float(t), tz=JST).isoformat()


def scan_day(payload: dict[str, Any]) -> dict[str, Any]:
    from research.post_open_aggregate_order_pressure_acceptance_full_strategy_v1.harvest import assert_dev_only_day

    day = str(payload["date"])
    assert_dev_only_day(day)
    capture = Path(payload["capture_path"])
    universe = [_bare(s) for s in list(payload["universe"]) if _bare(s)]
    uni = set(universe)
    am_start = float(hm_epoch(day, 9, 0))
    am_end = float(session_end_for_position(date=day, session="AM", fill_time=am_start + 60.0))
    flatten_t = float(hm_epoch(day, int(SESSION_FLATTEN_HM[0]), int(SESSION_FLATTEN_HM[1])))
    fields = {k: _empty_field() for k in PRESSURE_FIELDS}
    last_val: dict[tuple[str, str], Optional[float]] = {}
    run_len: dict[tuple[str, str], int] = {}
    first_finite_day: dict[str, Optional[float]] = {k: None for k in PRESSURE_FIELDS}
    last_change_day: dict[str, Optional[float]] = {k: None for k in PRESSURE_FIELDS}
    events_n = 0
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
        events_n += 1
        st = trusted_state(pay, event_t=t)
        if not (st["opened"] and st["continuous"] and (not st["special"]) and (not st["preopen"])):
            continue
        continuous_n += 1
        for k in PRESSURE_FIELDS:
            raw = pay.get(k)
            v = _f(raw)
            rec_f = fields[k]
            rec_f["n"] += 1
            if raw is not None and raw != "":
                rec_f["non_null"] += 1
            if v is not None:
                rec_f["finite"] += 1
                rec_f["uniques"].add(float(v))
                if abs(float(v)) < 1e-15:
                    rec_f["zero"] += 1
                else:
                    rec_f["nonzero"] += 1
                if rec_f["first_t"] is None or t < float(rec_f["first_t"]):
                    rec_f["first_t"] = t
                rec_f["last_t"] = t if rec_f["last_t"] is None or t > float(rec_f["last_t"]) else rec_f["last_t"]
                if first_finite_day[k] is None:
                    first_finite_day[k] = t
            key = (sym, k)
            prev = last_val.get(key)
            if v is not None and prev is not None and float(v) != float(prev):
                rec_f["change_event_n"] += 1
                rec_f["change_days"].add(day)
                rec_f["change_symbols"].add(sym)
                rec_f["per_day_change"][day] += 1
                rec_f["per_symbol_change"][sym] += 1
                if rec_f["first_change_t"] is None or t < float(rec_f["first_change_t"]):
                    rec_f["first_change_t"] = t
                rec_f["last_change_t"] = t
                last_change_day[k] = t
                run_len[key] = 1
            elif v is not None:
                run_len[key] = int(run_len.get(key) or 0) + 1
                if run_len[key] > rec_f["max_unchanged_run"]:
                    rec_f["max_unchanged_run"] = run_len[key]
            if v is not None:
                last_val[key] = float(v)
            else:
                last_val[key] = None
                run_len[key] = 0
    for k in PRESSURE_FIELDS:
        a = first_finite_day[k]
        b = last_change_day[k]
        if a is not None and b is not None and (float(b) - float(a)) >= MIN_CHANGE_SPAN_SEC:
            fields[k]["span_days"].add(day)
    compact = {}
    for k, rec_f in fields.items():
        n = int(rec_f["n"])
        dist = sorted(int(v) for v in rec_f["per_symbol_change"].values())
        compact[k] = {
            "n": n,
            "non_null_rate": _pct(int(rec_f["non_null"]), n),
            "finite_rate": _pct(int(rec_f["finite"]), n),
            "zero_rate": _pct(int(rec_f["zero"]), n),
            "nonzero_rate": _pct(int(rec_f["nonzero"]), n),
            "unique_value_n": len(rec_f["uniques"]),
            "change_event_n": int(rec_f["change_event_n"]),
            "change_symbol_n": len(rec_f["change_symbols"]),
            "change_day_n": len(rec_f["change_days"]),
            "per_day_change_event_n": dict(rec_f["per_day_change"]),
            "per_symbol_change_p50": dist[len(dist) // 2] if dist else 0,
            "per_symbol_change_p90": dist[int(0.9 * (len(dist) - 1))] if dist else 0,
            "per_symbol_change_max": dist[-1] if dist else 0,
            "max_unchanged_ingress_run": int(rec_f["max_unchanged_run"]),
            "first_post_open_observed_time": _iso(rec_f["first_t"]),
            "last_post_open_observed_time": _iso(rec_f["last_t"]),
            "first_change_time": _iso(rec_f["first_change_t"]),
            "last_change_time": _iso(rec_f["last_change_t"]),
            "change_span_day": bool(day in rec_f["span_days"]),
        }
    return {
        "ok": True,
        "date": day,
        "events_n": events_n,
        "continuous_n": continuous_n,
        "fields": compact,
        "span_days": {k: list(fields[k]["span_days"]) for k in PRESSURE_FIELDS},
        "change_days": {k: list(fields[k]["change_days"]) for k in PRESSURE_FIELDS},
        "change_symbols": {k: list(fields[k]["change_symbols"]) for k in PRESSURE_FIELDS},
        "unique_values": {k: list(fields[k]["uniques"])[:50] for k in PRESSURE_FIELDS},
        "unique_n": {k: len(fields[k]["uniques"]) for k in PRESSURE_FIELDS},
        "change_event_n": {k: int(fields[k]["change_event_n"]) for k in PRESSURE_FIELDS},
        "max_unchanged": {k: int(fields[k]["max_unchanged_run"]) for k in PRESSURE_FIELDS},
        "first_t": {k: fields[k]["first_t"] for k in PRESSURE_FIELDS},
        "last_t": {k: fields[k]["last_t"] for k in PRESSURE_FIELDS},
        "rates": compact,
    }


def _merge(days: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in PRESSURE_FIELDS:
        n = non_null = finite = zero = nonzero = change = 0
        uniques = 0
        days_ch: set[str] = set()
        syms: set[str] = set()
        span: set[str] = set()
        per_day: dict[str, int] = {}
        max_run = 0
        first_t = None
        last_t = None
        first_ch = None
        last_ch = None
        p50s: list[int] = []
        p90s: list[int] = []
        maxs: list[int] = []
        for d in days:
            f = dict((d.get("fields") or {}).get(k) or {})
            n += int(f.get("n") or 0)
            non_null += int(round(float(f.get("non_null_rate") or 0) * int(f.get("n") or 0)))
            finite += int(round(float(f.get("finite_rate") or 0) * int(f.get("n") or 0)))
            zero += int(round(float(f.get("zero_rate") or 0) * int(f.get("n") or 0)))
            nonzero += int(round(float(f.get("nonzero_rate") or 0) * int(f.get("n") or 0)))
            uniques = max(uniques, int(f.get("unique_value_n") or 0))
            change += int(f.get("change_event_n") or 0)
            per_day[str(d.get("date"))] = int(f.get("change_event_n") or 0)
            if int(f.get("change_event_n") or 0) > 0:
                days_ch.add(str(d.get("date")))
            max_run = max(max_run, int(f.get("max_unchanged_ingress_run") or 0))
            p50s.append(int(f.get("per_symbol_change_p50") or 0))
            p90s.append(int(f.get("per_symbol_change_p90") or 0))
            maxs.append(int(f.get("per_symbol_change_max") or 0))
        for d in days:
            for s in list((d.get("change_symbols") or {}).get(k) or []):
                syms.add(str(s))
            for s in list((d.get("span_days") or {}).get(k) or []):
                span.add(str(s))
            ft = (d.get("first_t") or {}).get(k)
            lt = (d.get("last_t") or {}).get(k)
            if ft is not None:
                first_t = float(ft) if first_t is None else min(float(first_t), float(ft))
            if lt is not None:
                last_t = float(lt) if last_t is None else max(float(last_t), float(lt))
            f = dict((d.get("fields") or {}).get(k) or {})
            if f.get("first_change_time"):
                first_ch = f.get("first_change_time") if first_ch is None else min(str(first_ch), str(f.get("first_change_time")))
            if f.get("last_change_time"):
                last_ch = f.get("last_change_time") if last_ch is None else max(str(last_ch), str(f.get("last_change_time")))
        finite_rate = _pct(finite, n)
        not_freeze = (
            int(change) > 0
            and uniques > 1
            and len(days_ch) >= MIN_CHANGE_DAY_N
            and len(span) >= MIN_CHANGE_DAY_N
        )
        usable = finite_rate >= MIN_FINITE_RATE and n > 0
        out[k] = {
            "n": n,
            "non_null_rate": _pct(non_null, n),
            "finite_rate": finite_rate,
            "zero_rate": _pct(zero, n),
            "nonzero_rate": _pct(nonzero, n),
            "unique_value_n": uniques,
            "change_event_n": int(change),
            "change_symbol_n": len(syms),
            "change_day_n": len(days_ch),
            "change_span_day_n": len(span),
            "per_day_change_event_n": per_day,
            "per_symbol_change_p50_day_median": sorted(p50s)[len(p50s) // 2] if p50s else 0,
            "per_symbol_change_p90_day_median": sorted(p90s)[len(p90s) // 2] if p90s else 0,
            "per_symbol_change_max": max(maxs) if maxs else 0,
            "max_unchanged_ingress_run": max_run,
            "first_post_open_observed_time": _iso(first_t),
            "last_post_open_observed_time": _iso(last_t),
            "first_change_time": first_ch,
            "last_change_time": last_ch,
            "NOT_0900_FREEZE": bool(not_freeze),
            "CAUSAL_USABLE": bool(usable),
            "BREADTH_OK": len(syms) >= MIN_CHANGE_SYMBOL_N and len(days_ch) >= MIN_CHANGE_DAY_N,
        }
    return out


def gate_support(*, semantics_ok: bool, merged: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    if not semantics_ok:
        reasons.append("SEMANTICS_NOT_PROVEN")
    for k in PRESSURE_FIELDS:
        row = dict(merged.get(k) or {})
        if not row.get("CAUSAL_USABLE"):
            reasons.append(f"{k}:NOT_CAUSAL_USABLE")
        if not row.get("NOT_0900_FREEZE"):
            reasons.append(f"{k}:0900_FREEZE_OR_WEAK_DYNAMICS")
        if int(row.get("change_day_n") or 0) < MIN_CHANGE_DAY_N:
            reasons.append(f"{k}:CHANGE_DAY<{MIN_CHANGE_DAY_N}")
        if int(row.get("change_symbol_n") or 0) < MIN_CHANGE_SYMBOL_N:
            reasons.append(f"{k}:CHANGE_SYMBOL<{MIN_CHANGE_SYMBOL_N}")
        if not row.get("BREADTH_OK"):
            reasons.append(f"{k}:BREADTH")
    ok = not reasons
    return {
        "PASS": bool(ok),
        "REASONS": reasons,
        "GATES": {
            "trusted_semantics": bool(semantics_ok),
            "all_four_causal_post_open": all(bool((merged.get(k) or {}).get("CAUSAL_USABLE")) for k in PRESSURE_FIELDS),
            "all_four_not_0900_freeze": all(bool((merged.get(k) or {}).get("NOT_0900_FREEZE")) for k in PRESSURE_FIELDS),
            "change_day_n_ge_8": all(int((merged.get(k) or {}).get("change_day_n") or 0) >= MIN_CHANGE_DAY_N for k in PRESSURE_FIELDS),
            "symbol_breadth": all(int((merged.get(k) or {}).get("change_symbol_n") or 0) >= MIN_CHANGE_SYMBOL_N for k in PRESSURE_FIELDS),
            "CURRENT_PRICE_TIME_INFERENCE": False,
        },
        "MIN_CHANGE_DAY_N": MIN_CHANGE_DAY_N,
        "MIN_CHANGE_SYMBOL_N": MIN_CHANGE_SYMBOL_N,
        "MIN_CHANGE_SPAN_SEC": MIN_CHANGE_SPAN_SEC,
        "MIN_FINITE_RATE": MIN_FINITE_RATE,
        "NOTE": "missing_rate=0 is not sufficient. Each field must update on >=8/10 days with span>=60s and >=50 symbols.",
    }


def scan_post_open(*, semantics_ok: bool) -> dict[str, Any]:
    from research.post_open_aggregate_order_pressure_acceptance_full_strategy_v1.harvest import sealed_dev_caps

    CACHE.mkdir(parents=True, exist_ok=True)
    cache = CACHE / "AOP_SCAN.pkl.gz"
    saved = _load_gz(cache)
    if saved.get("ok") and list(saved.get("days_ok") or []) == list(DEVELOPMENT_DAYS):
        print("cache-hit AOP SCAN", flush=True)
        return saved
    invs, blockers = sealed_dev_caps()
    if blockers:
        return {"ok": False, "blocker": "CAPTURE", "blockers": blockers}
    day_bodies: list[dict[str, Any]] = []
    for inv in invs:
        day = str(inv["date"])
        day_cache = CACHE / f"AOP_SCAN_{day}.pkl.gz"
        hit = _load_gz(day_cache)
        if hit.get("ok") and str(hit.get("date") or "") == day:
            print(f"cache-hit AOP SCAN {day}", flush=True)
            day_bodies.append(hit)
            continue
        print(f"PHASE SCAN {day}", flush=True)
        body = scan_day({"date": day, "capture_path": inv["capture_path"], "universe": list(inv["universe_symbols"])})
        _dump_gz(day_cache, body)
        day_bodies.append(body)
        print(f"{day} SCAN continuous={body.get('continuous_n')} changes={body.get('change_event_n')}", flush=True)
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
                **{f"{k}_change_n": int((d.get("change_event_n") or {}).get(k) or 0) for k in PRESSURE_FIELDS},
            }
            for d in day_bodies
        ],
        "CURRENT_PRICE_TIME_INFERENCE": False,
    }
    _dump_gz(cache, out)
    return out


assert math.isfinite
assert DEVELOPMENT_DAYS
