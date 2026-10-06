"""PB1 event census on the five targets. Does not change the V4 machine."""
from __future__ import annotations

from statistics import median, quantiles
from typing import Any

from research.causal_driver_pb1.m3_alpha_actionability_pb1_compatibility import ADAPTER_DISCREPANCY_MIN_N, SPARSE_MAX_FRACTION
from research.causal_driver_pb1.m3_alpha_actionability_pb1_compatibility.response import TARGETS
from research.causal_driver_pb1.phase2_discovery.clock import hhmm_to_min
from research.causal_driver_pb1.sector_state_alpha_complete_economic.join import alpha_live
from research.cause_first_mechanism_discovery_v1.clock import hhmm_add
from research.pb1_v4_clarified_machine_correction_v4 import EXEC_1M_CONFIRMED, EXEC_5M_DIRECT
from research.pb1_v4_clarified_machine_correction_v4 import machine as machine_mod
from research.pb1_v4_clarified_machine_correction_v4.walk import emit_v4

BUCKETS = ("09:00-09:10", "09:10-10:00", "10:00-11:00", "11:00-11:25", "after_11:25")


def bucket(hhmm: str) -> str:
    t = str(hhmm)[:5]
    if t < "09:10":
        return "09:00-09:10"
    if t < "10:00":
        return "09:10-10:00"
    if t < "11:00":
        return "10:00-11:00"
    if t <= "11:25":
        return "11:00-11:25"
    return "after_11:25"


def _direction(raw: Any, dir_code: Any = None) -> str:
    text = str(raw or "").lower()
    if text in {"bull", "long", "1"} or int(dir_code or 0) > 0:
        return "LONG"
    return "SHORT"


def collect_pb1(dates: list[str]) -> list[dict[str, str]]:
    thesis: list[dict[str, str]] = []
    original = machine_mod.mint_thesis

    def _record(st: Any, *, symbol: str, date: str, t: str, pos: int) -> None:
        had = st.thesis_id
        original(st, symbol=symbol, date=date, t=t, pos=pos)
        if st.thesis_id and st.thesis_id != had and str(symbol) in TARGETS:
            thesis.append({
                "date": str(date),
                "symbol": str(symbol),
                "event_type": "THESIS_READY",
                "direction": "LONG" if int(st.sign) > 0 else "SHORT",
                "event_t": str(t)[:5],
                "identity": "PB1_V4_THESIS_READY",
            })

    machine_mod.mint_thesis = _record
    try:
        walked = emit_v4({
            "symbols": list(TARGETS),
            "split": {"discovery_dates": list(dates), "confirmation_dates": [], "frozen_validation_dates": []},
            "blocks": {"date_to_block": {}},
        })
    finally:
        machine_mod.mint_thesis = original
    if not walked.get("ok"):
        raise RuntimeError(walked.get("reason") or "pb1_walk_failed")
    rows = list(thesis)
    for kind, key, ident in (
        ("E0", "e0_events", EXEC_5M_DIRECT),
        ("E1", "e1_events", EXEC_1M_CONFIRMED),
    ):
        for ev in list(walked.get(key) or []):
            if str(ev.get("symbol") or "") not in TARGETS:
                continue
            entry = str(ev.get("entry_t") or "")[:5]
            rows.append({
                "date": str(ev.get("date") or ""),
                "symbol": str(ev.get("symbol") or ""),
                "event_type": kind,
                "direction": _direction(ev.get("direction"), ev.get("DIR")),
                "event_t": str(hhmm_add(entry, -1) or entry)[:5],
                "fill_t": entry,
                "identity": str(ev.get("exec_variant") or ident),
            })
    rows.sort(key=lambda r: (r["date"], r["event_t"], r["symbol"], r["event_type"]))
    return rows


def classify_state(clocks: dict[str, dict[str, Any]], hhmm: str, direction: str) -> str:
    row = clocks.get(str(hhmm)[:5])
    if row is None:
        return "ALPHA_INACTIVE"
    if row.get("state") == "ACTIVE_DATA_GAP" or (row.get("state") == "ACTIVE" and not row.get("available")):
        return "ALPHA_DATA_UNAVAILABLE"
    if alpha_live(clocks, hhmm):
        return "ALPHA_ACTIVE_LONG" if direction == "LONG" else "ALPHA_ACTIVE_DIRECTION_MISMATCH"
    return "ALPHA_INACTIVE"


def annotate(events: list[dict[str, str]], clocks: dict[str, dict[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    out = []
    for ev in events:
        day = clocks.get(ev["date"]) or {}
        state = classify_state(day, ev["event_t"], ev["direction"])
        episode = str((day.get(ev["event_t"]) or {}).get("episode_id") or "")
        out.append({**ev, "alpha_state": state, "episode_id": episode, "clock_bucket": bucket(ev["event_t"])})
    return out


def _count_table(events: list[dict[str, Any]], *, event_type: str | None = None) -> dict[str, int]:
    chosen = events if event_type is None else [e for e in events if e["event_type"] == event_type]
    labels = ("ALPHA_ACTIVE_LONG", "ALPHA_INACTIVE", "ALPHA_ACTIVE_DIRECTION_MISMATCH", "ALPHA_DATA_UNAVAILABLE")
    return {label: sum(1 for e in chosen if e["alpha_state"] == label) for label in labels}


def overlap(events: list[dict[str, Any]], episodes: list[dict[str, str]]) -> dict[str, Any]:
    qual = [e for e in events if e["event_type"] in {"E0", "E1"} and e["alpha_state"] in {"ALPHA_ACTIVE_LONG", "ALPHA_ACTIVE_DIRECTION_MISMATCH"}]
    by_ep: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for ev in qual:
        by_ep.setdefault((ev["episode_id"], ev["symbol"]), []).append(ev)
    lags = []
    hit = 0
    n = len(episodes) * len(TARGETS)
    for ep in episodes:
        for symbol in TARGETS:
            found = [
                ev for ev in by_ep.get((ep["episode_id"], symbol), [])
                if ev["date"] == ep["date"] and ev["event_t"] >= ep["start"]
            ]
            if not found:
                continue
            hit += 1
            first = min(found, key=lambda ev: ev["event_t"])
            lags.append(hhmm_to_min(first["event_t"]) - hhmm_to_min(ep["start"]))
    qs = quantiles(lags, n=4) if len(lags) >= 2 else ([lags[0], lags[0], lags[0]] if lags else [None, None, None])
    return {
        "alpha_target_episode_n": n,
        "episodes_with_any_pb1_confirmation_n": hit,
        "episodes_without_pb1_confirmation_n": n - hit,
        "fraction": float(hit / n) if n else None,
        "sparse": bool(n and hit / n <= float(SPARSE_MAX_FRACTION)),
        "lag_minutes": {
            "n": len(lags),
            "median": float(median(lags)) if lags else None,
            "p25": None if qs[0] is None else float(qs[0]),
            "p75": None if qs[2] is None else float(qs[2]),
            "max": max(lags) if lags else None,
        },
    }


def base_rate(events: list[dict[str, Any]], *, eligible_day_n: int) -> dict[str, Any]:
    def n_of(kind: str) -> int:
        return sum(1 for e in events if e["event_type"] == kind)

    per_symbol = []
    for symbol in TARGETS:
        chunk = [e for e in events if e["symbol"] == symbol]
        per_symbol.append({
            "symbol": symbol,
            "THESIS_READY_n": sum(1 for e in chunk if e["event_type"] == "THESIS_READY"),
            "E0_n": sum(1 for e in chunk if e["event_type"] == "E0"),
            "E1_n": sum(1 for e in chunk if e["event_type"] == "E1"),
        })
    ready, e0, e1 = n_of("THESIS_READY"), n_of("E0"), n_of("E1")
    return {
        "THESIS_READY_n": ready,
        "E0_n": e0,
        "E1_n": e1,
        "events_per_eligible_day": {
            "THESIS_READY": float(ready / eligible_day_n) if eligible_day_n else None,
            "E0": float(e0 / eligible_day_n) if eligible_day_n else None,
            "E1": float(e1 / eligible_day_n) if eligible_day_n else None,
        },
        "per_symbol": per_symbol,
    }


def direction_report(events: list[dict[str, Any]]) -> dict[str, Any]:
    execs = [e for e in events if e["event_type"] in {"E0", "E1"}]
    active = [e for e in execs if e["alpha_state"] in {"ALPHA_ACTIVE_LONG", "ALPHA_ACTIVE_DIRECTION_MISMATCH"}]
    return {
        "PB1_LONG_n": sum(1 for e in execs if e["direction"] == "LONG"),
        "PB1_SHORT_n": sum(1 for e in execs if e["direction"] == "SHORT"),
        "while_alpha_active_LONG_n": sum(1 for e in active if e["direction"] == "LONG"),
        "while_alpha_active_SHORT_n": sum(1 for e in active if e["direction"] == "SHORT"),
        "thesis_ready_LONG_n": sum(1 for e in events if e["event_type"] == "THESIS_READY" and e["direction"] == "LONG"),
        "thesis_ready_SHORT_n": sum(1 for e in events if e["event_type"] == "THESIS_READY" and e["direction"] == "SHORT"),
    }


def clock_report(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for kind in ("THESIS_READY", "E0", "E1"):
        chunk = [e for e in events if e["event_type"] == kind]
        item = {"event_type": kind}
        for name in BUCKETS:
            item[name] = sum(1 for e in chunk if e["clock_bucket"] == name)
        rows.append(item)
    return rows


def adapter_consistent(*, in_episode_n: int) -> bool:
    return int(in_episode_n) < int(ADAPTER_DISCREPANCY_MIN_N)
