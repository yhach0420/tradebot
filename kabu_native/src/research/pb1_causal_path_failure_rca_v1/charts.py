"""New 96-chart blinded RCA sample. Not sorted by future return."""
from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path
from typing import Any

from research.cause_first_mechanism_discovery_v1.panel import load_minutes
from research.multi_touch_daily_zone_1m_price_action_v1.daily import daily_from_minutes
from research.one_minute_native_playbook_discovery_v1.states import prep_symbol
from research.pb1_causal_path_failure_rca_v1 import PER_CELL, SAMPLE_N, SAMPLE_SEED
from research.pb1_causal_path_failure_rca_v1.isolation import OUT
from research.pb1_opening_range_continuation_face_valid_v1.or15 import session_idx_of
from research.pb1_opening_range_continuation_face_valid_v2.charts import V1_DEV, _event_key, _render


def _hash_key(e: dict[str, Any]) -> str:
    key = f"{e.get('symbol')}|{e.get('date')}|{e.get('direction')}|{e.get('trigger_t')}|{e.get('trigger_primary')}|{e.get('block')}"
    return hashlib.sha256((SAMPLE_SEED + "|" + key).encode("utf-8")).hexdigest()


def pick_sample(events: list[dict[str, Any]], v2_keys: set[tuple[str, str, str]] | None = None) -> list[dict[str, Any]]:
    v2_keys = v2_keys or set()
    cells: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        k = _event_key(e)
        if k in V1_DEV or k in v2_keys:
            continue
        cell = (str(e.get("block") or "na"), str(e.get("trigger_primary") or "na"), str(e.get("direction") or "na"))
        cells[cell].append(e)
    picked: list[dict[str, Any]] = []
    used: set[tuple[str, str, str]] = set()
    for block in ("D1", "D2", "D3", "D4"):
        for trig in ("RECLAIM_RETEST_MICRO_HIGH", "FAILED_PUSH_THEN_CLOSE_BACK"):
            for side in ("bull", "bear"):
                rows = sorted(cells.get((block, trig, side), []), key=_hash_key)
                for r in rows[:PER_CELL]:
                    ek = _event_key(r)
                    if ek in used:
                        continue
                    used.add(ek)
                    picked.append(r)
    for i, r in enumerate(picked, start=1):
        r["sample_id"] = i
        r["future_hidden"] = True
        r["v1_dev_reused"] = False
        r["v2_verify_reused"] = False
    return picked[:SAMPLE_N]


def render_sample(bind: dict[str, Any], sample: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not sample:
        return []
    split = dict(bind.get("split") or {})
    disc = [str(d) for d in list(split.get("discovery_dates") or [])]
    conf = set(str(d) for d in list(split.get("confirmation_dates") or []))
    val = set(str(d) for d in list(split.get("frozen_validation_dates") or []))
    symbols = sorted({str(e["symbol"]) for e in sample})
    minutes = load_minutes(symbols=symbols, allowed_dates=set(disc), forbidden_dates=conf | val)
    minutes["date"] = minutes["date"].astype(str)
    minutes["time_label"] = minutes["time_label"].astype(str).str.slice(0, 5)
    recs: dict[tuple[str, str], dict[str, Any]] = {}
    days: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (sym, dt), sg in minutes.groupby(["symbol", "date"], sort=True):
        rec = prep_symbol(sg)
        rec["session_idx"] = session_idx_of(rec["t"])
        recs[(str(sym), str(dt))] = rec
        day = daily_from_minutes(rec, str(dt))
        if day:
            days[str(sym)].append(day)
    out_dir = OUT / "charts"
    meta: list[dict[str, Any]] = []
    for ev in sample:
        rec = recs.get((str(ev["symbol"]), str(ev["date"])))
        if rec is None:
            continue
        prior = [d for d in days[str(ev["symbol"])] if str(d["date"]) < str(ev["date"])]
        path = out_dir / f"sample_{int(ev['sample_id']):02d}_{ev['symbol']}_{ev['date']}_{ev['direction']}.png"
        _render(ev, rec, prior, path)
        meta.append(
            {
                "sample_id": ev["sample_id"],
                "symbol": ev["symbol"],
                "date": ev["date"],
                "block": ev.get("block"),
                "direction": ev.get("direction"),
                "trigger_primary": ev.get("trigger_primary"),
                "in_play_reason": ev.get("in_play_reason"),
                "break_t": ev.get("break_t"),
                "retest_t": ev.get("retest_t"),
                "trigger_t": ev.get("trigger_t"),
                "chart": str(path.name),
                "future_hidden": True,
            }
        )
    return meta
