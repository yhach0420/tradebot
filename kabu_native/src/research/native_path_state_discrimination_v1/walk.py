"""Rebuild frozen atlas episodes with compact causal state. Same onset clustering. Discovery only."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.behavior_group_sequence_mechanism_v1.prequential import maps_for_blocks
from research.native_path_state_discrimination_v1 import EPISODE_GAP_MIN, GROUP_NAMES, MIN_SECTOR_N
from research.native_path_state_discrimination_v1.features import compact_state
from research.native_path_state_discrimination_v1.outcomes import attach_fwd, label_row
from research.one_minute_native_playbook_discovery_v1.episodes import classify, cluster_symbol_day
from research.one_minute_native_playbook_discovery_v1.panel import process_day
from research.one_minute_native_playbook_discovery_v1.states import features_at, prep_symbol, to_min
from research.cause_first_mechanism_discovery_v1.panel import load_minutes


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _med(xs: list[float]) -> float:
    arr = np.asarray([x for x in xs if _finite(x)], dtype=float)
    if arr.size == 0:
        return float("nan")
    return float(np.median(arr))


def _sector_of(bind: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for sym, row in dict(bind.get("by_symbol") or {}).items():
        out[str(sym)] = str(row.get("tse33_name") or row.get("sector33_name") or "")
    return out


def build_snaps(per: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    times = sorted({t for rec in per.values() for t in rec["t"]}, key=lambda x: to_min(x) or 0)
    present: dict[str, list[str]] = defaultdict(list)
    for rec in per.values():
        for t in rec["t"]:
            present[t].append(rec["symbol"])
    snaps: dict[str, dict[str, Any]] = {}
    for t in times:
        sess_all = []
        by_sec: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for sym in present.get(t) or []:
            rec = per[sym]
            i = rec["idx"].get(t)
            if i is None:
                continue
            sess = rec["sess"][i]
            r1 = rec["r1"][i]
            row = {"symbol": sym, "sector": rec["sector"], "sess": sess, "r1": r1}
            if _finite(sess):
                sess_all.append(float(sess))
            if rec["sector"]:
                by_sec[str(rec["sector"])].append(row)
        sectors = {}
        ranks: dict[str, int] = {}
        sec_n: dict[str, int] = {}
        for sec, xs in by_sec.items():
            finite = [(str(x["symbol"]), float(x["sess"])) for x in xs if _finite(x["sess"])]
            sec_n[sec] = len(finite)
            sess_med = _med([x["sess"] for x in xs])
            r1_med = _med([x["r1"] for x in xs])
            pos = [1.0 for x in xs if _finite(x["sess"]) and float(x["sess"]) > 0]
            nfin = sum(1 for x in xs if _finite(x["sess"]))
            leader = None
            if finite:
                ordered = sorted(finite, key=lambda kv: (-kv[1], kv[0]))
                leader = ordered[0][0]
                if len(finite) >= MIN_SECTOR_N:
                    for i, (sym, _) in enumerate(ordered, start=1):
                        ranks[sym] = i
            leader_r1 = float("nan")
            if leader:
                hit = next((x for x in xs if x["symbol"] == leader), None)
                if hit and _finite(hit["r1"]):
                    leader_r1 = float(hit["r1"])
            sectors[sec] = {
                "sess_med": sess_med,
                "r1": r1_med,
                "breadth_pos": (sum(pos) / nfin) if nfin else float("nan"),
                "leader": leader,
                "leader_r1": leader_r1,
                "n": sec_n[sec],
            }
        snaps[t] = {
            "market_sess_med": _med(sess_all),
            "sectors": sectors,
            "ranks": ranks,
            "sec_n": sec_n,
        }
    return snaps


def _daily_row(*, date: str, block: str, rec: dict[str, Any], lead: dict[str, Any] | None, opening: dict[str, Any] | None, eps: list[dict[str, Any]]) -> dict[str, Any]:
    imp = [e for e in eps if "IMPULSE" in str(e.get("event_family") or "") or "IMPULSE" in str(e.get("sequence") or "")]
    cont = [e for e in eps if e.get("path_type") in {"SUSTAINED_CONTINUATION", "LARGE_WINNER"}]
    rev = [e for e in eps if e.get("path_type") in {"IMMEDIATE_FAILURE", "SMALL_PROGRESS_THEN_FAILURE"}]
    fav = [e for e in eps if e.get("path_type") in {"SUSTAINED_CONTINUATION", "LARGE_WINNER"}]
    n_ep = max(len(eps), 1)
    return {
        "date": date,
        "block": block,
        "symbol": rec["symbol"],
        "sector": rec.get("sector"),
        "leading_n": int((lead or {}).get("leading_n") or 0),
        "lagging_n": int((lead or {}).get("lagging_n") or 0),
        "present_n": int((lead or {}).get("present_n") or 0),
        "impulse_n": len(imp) if imp else len(eps),
        "continuation_n": len(cont),
        "reversal_n": len(rev),
        "favor_first_n": len(fav),
        "opening_n": 1 if opening else 0,
        "opening_hold_n": 1 if opening and opening.get("hold_0914") else 0,
        "gap_up_n": 1 if opening and opening.get("gap_up") else 0,
        "gap_up_hold_n": 1 if opening and opening.get("gap_up") and opening.get("hold_0914") else 0,
        "episode_n_day": len(eps),
        "_base": n_ep,
    }


def walk_episodes(bind: dict[str, Any]) -> dict[str, Any]:
    split = dict(bind.get("split") or {})
    blocks = dict(bind.get("blocks") or {})
    disc = set(str(d) for d in list(split.get("discovery_dates") or []))
    conf = set(str(d) for d in list(split.get("confirmation_dates") or []))
    val = set(str(d) for d in list(split.get("frozen_validation_dates") or []))
    date_to_block = dict(blocks.get("date_to_block") or {})
    symbols = list(bind.get("symbols") or [])
    sector_of = _sector_of(bind)
    print(f"LOAD_MINUTES symbols={len(symbols)} discovery_days={len(disc)} path_state", flush=True)
    minutes = load_minutes(symbols=symbols, allowed_dates=disc, forbidden_dates=conf | val)
    if minutes.empty:
        return {"ok": False, "rows": [], "daily": []}
    if minutes["date"].isin(list(conf | val)).any():
        raise RuntimeError("forbidden_partition_loaded")
    minutes["date"] = minutes["date"].astype(str)
    minutes["time_label"] = minutes["time_label"].astype(str).str.slice(0, 5)
    minutes = minutes.sort_values(["date", "symbol", "time_label"])
    rows: list[dict[str, Any]] = []
    daily: list[dict[str, Any]] = []
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
        for sym, sg in g.groupby("symbol", sort=False):
            if str(sym) not in per:
                rec = prep_symbol(sg)
                if rec["n"] >= 20:
                    rec["symbol"] = str(sym)
                    rec["sector"] = sector_of.get(str(sym)) or ""
                    per[str(sym)] = rec
        snaps = build_snaps(per)
        opening_map = {(str(x.get("symbol"))): x for x in list(pack.get("opening") or [])}
        lead_map = {(str(x.get("symbol"))): x for x in list(pack.get("lead_min") or [])}
        by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for e in events:
            e.pop("rec", None)
            by_key[(str(e.get("date")), str(e.get("symbol")))].append(e)
        day_eps_by_sym: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for (d0, sym), xs in by_key.items():
            rec = per.get(sym)
            if rec is None:
                continue
            for cluster in cluster_symbol_day(xs, gap_min=EPISODE_GAP_MIN):
                trigger = cluster[-1]
                feat_t = str(trigger.get("feature_bar") or "")
                i = rec["idx"].get(feat_t)
                if i is None:
                    continue
                feat = features_at(rec, i)
                feat["symbol"] = sym
                feat["sector"] = rec.get("sector")
                snap = snaps.get(feat_t) or {}
                nsec = int((snap.get("sec_n") or {}).get(str(rec.get("sector") or ""), 0) or 0)
                rk = (snap.get("ranks") or {}).get(sym)
                feat["sector_rank"] = rk
                feat["sector_n"] = nsec
                feat["leading"] = bool(rk == 1 and nsec >= MIN_SECTOR_N)
                feat["lagging"] = bool(rk is not None and nsec >= MIN_SECTOR_N and rk > int(np.ceil(0.70 * nsec)))
                seq = [str(e.get("event_family")) for e in cluster]
                ep = {
                    "date": d0,
                    "block": trigger.get("block") or date_to_block.get(d0),
                    "symbol": sym,
                    "sector": rec.get("sector"),
                    "start": cluster[0].get("event_time"),
                    "event_time": trigger.get("event_time"),
                    "feature_bar": feat_t,
                    "available_at": feat.get("available_at"),
                    "families": seq,
                    "sequence": classify(seq),
                    "event_family": trigger.get("event_family"),
                    "leading": feat["leading"],
                    "lagging": feat["lagging"],
                    "hi20": feat.get("hi20"),
                    "lo20": feat.get("lo20"),
                    "gap": (opening_map.get(sym) or {}).get("gap"),
                }
                attach_fwd(ep, rec, snaps)
                label_row(ep)
                state = compact_state(
                    feat=feat,
                    snap=snap,
                    opening_gap=(opening_map.get(sym) or {}).get("gap"),
                    group="IDIOSYNCRATIC",
                    rec=rec,
                    i=i,
                )
                ep["state"] = state
                rows.append(ep)
                day_eps_by_sym[sym].append(ep)
        for rec in per.values():
            daily.append(
                _daily_row(
                    date=str(day),
                    block=str(date_to_block.get(str(day)) or ""),
                    rec=rec,
                    lead=lead_map.get(rec["symbol"]),
                    opening=opening_map.get(rec["symbol"]),
                    eps=day_eps_by_sym.get(rec["symbol"]) or [],
                )
            )
        prev_close.update(pack.get("last_close") or {})
        for rec in per.values():
            if rec["n"] and np.isfinite(rec["c"][-1]) and rec["c"][-1] > 0:
                prev_close[rec["symbol"]] = float(rec["c"][-1])
        if di % 20 == 0 or di == n_days:
            print(f"DAY {di}/{n_days} episodes={len(rows)}", flush=True)
    maps = maps_for_blocks(daily=daily, blocks=list(blocks.get("blocks") or []))
    by_eval = dict(maps.get("by_eval_block") or {})
    for ep in rows:
        blk = str(ep.get("block") or "")
        grp = (by_eval.get(blk) or {}).get(str(ep.get("symbol"))) or "IDIOSYNCRATIC"
        ep["behavior_group"] = grp
        st = dict(ep.get("state") or {})
        for g in GROUP_NAMES:
            st[f"g_{g}"] = 1.0 if grp == g else 0.0
        ep["state"] = st
    counts: dict[str, int] = defaultdict(int)
    for ep in rows:
        counts[str(ep.get("path_type") or "UNLABELED")] += 1
    return {
        "ok": True,
        "episode_n": len(rows),
        "raw_event_n": raw_event_n,
        "day_n": n_days,
        "rows": rows,
        "daily": daily,
        "group_maps": maps,
        "outcome_counts": dict(counts),
        "gap_min": EPISODE_GAP_MIN,
        "future_in_boundary": False,
        "used_5m_grid": False,
        "bar_start": True,
        "random_cv": False,
    }
