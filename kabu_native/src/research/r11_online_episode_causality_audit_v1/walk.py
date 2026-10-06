"""Discovery walk: official last-event episodes plus streaming diagnostics. Confirmation/FV never loaded."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.cause_first_mechanism_discovery_v1.panel import load_minutes
from research.native_1m_path_state_strategy_freeze_v1.r11 import match_r11
from research.native_path_state_discrimination_v1.features import mins_from_open
from research.native_path_state_discrimination_v1.outcomes import attach_fwd
from research.native_path_state_discrimination_v1.walk import _sector_of
from research.one_minute_native_playbook_discovery_v1.episodes import classify, cluster_symbol_day
from research.one_minute_native_playbook_discovery_v1.panel import process_day
from research.one_minute_native_playbook_discovery_v1.states import entry_ok, features_at, prep_symbol
from research.r11_online_episode_causality_audit_v1 import ENTRY_CUTOFF, EPISODE_GAP_MIN
from research.r11_online_episode_causality_audit_v1.knowability import earliest_cluster_last_knowable, to_min
from research.r11_online_episode_causality_audit_v1.stream import stream_first_event_refractory, stream_last_event_delayed


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _state(feat: dict[str, Any]) -> dict[str, float]:
    dist = feat.get("dist_vwap")
    return {
        "dist_vwap": float(dist) if _finite(dist) else float("nan"),
        "mins_from_open": mins_from_open(str(feat.get("feature_bar") or "")),
        "vwap_reclaim": 1.0 if feat.get("vwap_reclaim") else 0.0,
    }


def _episode_from_trigger(
    *,
    cluster: list[dict[str, Any]],
    rec: dict[str, Any],
    date_to_block: dict[str, str],
) -> dict[str, Any] | None:
    trigger = cluster[-1]
    feat_t = str(trigger.get("feature_bar") or "")
    i = rec["idx"].get(feat_t)
    if i is None:
        return None
    feat = features_at(rec, i)
    feat["symbol"] = rec["symbol"]
    feat["sector"] = rec.get("sector")
    d0 = str(trigger.get("date") or rec.get("date") or "")
    seq = [str(e.get("event_family")) for e in cluster]
    ep = {
        "date": d0,
        "block": trigger.get("block") or date_to_block.get(d0),
        "symbol": rec["symbol"],
        "sector": rec.get("sector"),
        "start": cluster[0].get("event_time"),
        "event_time": trigger.get("event_time"),
        "feature_bar": feat_t,
        "available_at": feat.get("available_at"),
        "families": seq,
        "sequence": classify(seq),
        "event_family": trigger.get("event_family"),
        "hi20": feat.get("hi20"),
        "cluster_n": len(cluster),
        "first_event_time": cluster[0].get("event_time"),
        "first_feature_bar": cluster[0].get("feature_bar"),
        "first_event_family": cluster[0].get("event_family"),
        "state": _state(feat),
        "original_trigger_event_time": trigger.get("event_time"),
        "original_decision_time": trigger.get("event_time"),
        "decision_time": trigger.get("event_time"),
        "cluster_last_event_time": trigger.get("event_time"),
    }
    attach_fwd(ep, rec, {})
    return ep


def _first_state(cluster: list[dict[str, Any]], rec: dict[str, Any]) -> dict[str, Any] | None:
    first = cluster[0]
    feat_t = str(first.get("feature_bar") or "")
    i = rec["idx"].get(feat_t)
    if i is None:
        return None
    feat = features_at(rec, i)
    return {
        "first_state": _state(feat),
        "first_available_at": feat.get("available_at"),
        "first_feature_bar": feat_t,
    }


def _delayed_row(ep: dict[str, Any], rec: dict[str, Any], know: dict[str, Any]) -> dict[str, Any] | None:
    dec = str(know.get("earliest_time_cluster_last_is_knowable") or "")
    if not dec:
        return None
    if not entry_ok(dec) or dec > ENTRY_CUTOFF:
        row = dict(ep)
        row["delayed_skipped"] = True
        row["delayed_skip_reason"] = "invalid_or_cutoff_entry"
        row["decision_time"] = dec
        row["event_time"] = dec
        row["x0_entry_open"] = None
        row["fwd_bars"] = []
        return row
    ie = rec["idx"].get(dec)
    if ie is None:
        # next observed bar after knowable
        tm = to_min(dec)
        ie2 = None
        if tm is not None:
            for t, j in rec["idx"].items():
                m = to_min(t)
                if m is not None and m > tm:
                    if ie2 is None or m < to_min(rec["t"][ie2]):
                        ie2 = j
        ie = ie2
    if ie is None:
        row = dict(ep)
        row["delayed_skipped"] = True
        row["delayed_skip_reason"] = "no_bar_after_knowable"
        row["decision_time"] = dec
        row["event_time"] = dec
        row["x0_entry_open"] = None
        row["fwd_bars"] = []
        return row
    fill_t = rec["t"][ie]
    row = dict(ep)
    row["delayed_skipped"] = False
    row["decision_time"] = dec
    row["event_time"] = fill_t
    row["entry_time"] = fill_t
    row["streaming_role"] = "LAST_EVENT_CAUSAL_DELAYED"
    row["LOOKAHEAD_VIOLATION"] = True
    attach_fwd(row, rec, {})
    return row


def walk_audit(bind: dict[str, Any], *, med: dict[str, float]) -> dict[str, Any]:
    split = dict(bind.get("split") or {})
    blocks = dict(bind.get("blocks") or {})
    disc = set(str(d) for d in list(split.get("discovery_dates") or []))
    conf = set(str(d) for d in list(split.get("confirmation_dates") or []))
    val = set(str(d) for d in list(split.get("frozen_validation_dates") or []))
    date_to_block = dict(blocks.get("date_to_block") or {})
    symbols = list(bind.get("symbols") or [])
    sector_of = _sector_of(bind)
    print(f"LOAD_MINUTES symbols={len(symbols)} discovery_days={len(disc)} r11_online_causality", flush=True)
    minutes = load_minutes(symbols=symbols, allowed_dates=disc, forbidden_dates=conf | val)
    if minutes.empty:
        return {"ok": False, "rows": []}
    if minutes["date"].isin(list(conf | val)).any():
        raise RuntimeError("forbidden_partition_loaded")
    minutes["date"] = minutes["date"].astype(str)
    minutes["time_label"] = minutes["time_label"].astype(str).str.slice(0, 5)
    minutes = minutes.sort_values(["date", "symbol", "time_label"])
    rows: list[dict[str, Any]] = []
    delayed_rows: list[dict[str, Any]] = []
    first_r11_rows: list[dict[str, Any]] = []
    streamed_last: list[dict[str, Any]] = []
    streamed_first_n = 0
    prev_close: dict[str, float] = {}
    n_days = int(minutes["date"].nunique())
    raw_event_n = 0
    for di, (day, g) in enumerate(minutes.groupby("date", sort=True), start=1):
        pack = process_day(date=str(day), g=g, sector_of=sector_of, date_to_block=date_to_block, prev_close=prev_close)
        events = list(pack.get("events") or [])
        raw_event_n += len(events)
        per: dict[str, dict[str, Any]] = {}
        for rec in (e.get("rec") for e in events):
            if rec and rec.get("symbol"):
                per[str(rec["symbol"])] = rec
        for e in events:
            e.pop("rec", None)
        day_events = [
            {
                "date": e.get("date"),
                "symbol": e.get("symbol"),
                "event_time": e.get("event_time"),
                "event_family": e.get("event_family"),
                "feature_bar": e.get("feature_bar"),
                "available_at": e.get("available_at"),
                "block": e.get("block"),
                "sector": e.get("sector"),
            }
            for e in events
        ]
        streamed_last.extend(stream_last_event_delayed(day_events))
        firsts = stream_first_event_refractory(day_events)
        streamed_first_n += len(firsts)
        for e in firsts:
            rec = per.get(str(e.get("symbol")))
            if rec is None:
                continue
            feat_t = str(e.get("feature_bar") or "")
            i = rec["idx"].get(feat_t)
            if i is None:
                continue
            feat = features_at(rec, i)
            fer = {
                "date": e.get("date"),
                "block": e.get("block") or date_to_block.get(str(day)),
                "symbol": e.get("symbol"),
                "sector": e.get("sector") or rec.get("sector"),
                "start": e.get("event_time"),
                "event_time": e.get("event_time"),
                "feature_bar": feat_t,
                "available_at": feat.get("available_at"),
                "decision_time": e.get("event_time"),
                "hi20": feat.get("hi20"),
                "state": _state(feat),
                "streaming_role": "FIRST_EVENT_REFRACTORY",
                "LOOKAHEAD_VIOLATION": False,
            }
            if match_r11(fer, med=med):
                attach_fwd(fer, rec, {})
                first_r11_rows.append(fer)
        by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for e in events:
            by_key[(str(e.get("date")), str(e.get("symbol")))].append(e)
        for (d0, sym), xs in by_key.items():
            rec = per.get(sym)
            if rec is None:
                continue
            clusters = cluster_symbol_day(xs, gap_min=EPISODE_GAP_MIN)
            for ci, cluster in enumerate(clusters):
                nxt = None
                if ci + 1 < len(clusters):
                    nxt = clusters[ci + 1][0].get("event_time")
                ep = _episode_from_trigger(cluster=cluster, rec=rec, date_to_block=date_to_block)
                if ep is None:
                    continue
                last_t = str(ep.get("event_time") or "")
                know = earliest_cluster_last_knowable(last_t, next_same_symbol_event_time=str(nxt) if nxt else None)
                ep["next_same_symbol_event_time"] = nxt
                ep["earliest_time_cluster_last_is_knowable"] = know.get("earliest_time_cluster_last_is_knowable")
                ep["CAUSAL_KNOWABILITY_LAG_MIN"] = know.get("CAUSAL_KNOWABILITY_LAG_MIN")
                ep["LOOKAHEAD_VIOLATION"] = bool(know.get("LOOKAHEAD_VIOLATION"))
                ep["known_at_own_event_timestamp"] = bool(know.get("known_at_own_event_timestamp"))
                extra = _first_state(cluster, rec) or {}
                ep.update(extra)
                rows.append(ep)
                if match_r11(ep, med=med) and ep.get("x0_entry_open"):
                    drow = _delayed_row(ep, rec, know)
                    if drow is not None:
                        delayed_rows.append(drow)
        prev_close.update(pack.get("last_close") or {})
        for rec in per.values():
            if rec["n"] and np.isfinite(rec["c"][-1]) and rec["c"][-1] > 0:
                prev_close[rec["symbol"]] = float(rec["c"][-1])
        if di % 20 == 0 or di == n_days:
            print(f"DAY {di}/{n_days} episodes={len(rows)} delayed={len(delayed_rows)} first_r11={len(first_r11_rows)}", flush=True)
    return {
        "ok": True,
        "episode_n": len(rows),
        "raw_event_n": raw_event_n,
        "day_n": n_days,
        "rows": rows,
        "delayed_r11_rows": delayed_rows,
        "first_r11_rows": first_r11_rows,
        "streamed_last": streamed_last,
        "streamed_first_n": streamed_first_n,
        "gap_min": EPISODE_GAP_MIN,
        "future_in_boundary": False,
        "offline_inspects_later_events_to_choose_trigger": True,
    }
