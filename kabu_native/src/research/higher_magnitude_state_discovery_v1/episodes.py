"""Causal episodes from onset events. Boundaries use timestamps only. One precommitted 15m gap."""
from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any

from research.cause_first_mechanism_discovery_v1.clock import parse_hhmm
from research.higher_magnitude_state_discovery_v1 import EPISODE_GAP_MIN

ARCHETYPES = (
    "CONTROLLED_PULLBACK_RECLAIM",
    "COMPRESSION_BREAKOUT_EXPANSION",
    "SECTOR_LEADER_TRANSITION",
)

FEATURE_KEYS = (
    "breadth_level",
    "breadth_slope",
    "breadth_accel",
    "dispersion_5m",
    "dispersion_change",
    "leadership_hhi",
    "volume_active_ratio",
    "ema_breadth",
    "sector",
    "sector_up_5m",
    "sector_rs",
    "sector_rs_slope",
    "sector_n",
    "rs_5m",
    "rs_slope",
    "vwap_dist",
    "ema_dist",
    "ema9_gt_ema21",
    "dist_20high",
    "hi20",
    "vol_rel20",
    "va_rel20",
    "rng_rel20",
    "ret_5m",
    "ret_15m",
    "pullback_state",
)


def _mins(hhmm: str) -> int | None:
    p = parse_hhmm(hhmm)
    if p is None:
        return None
    return p[0] * 60 + p[1]


def _before(seq: list[str], a: str, b: str) -> bool:
    try:
        return seq.index(a) < seq.index(b)
    except ValueError:
        return False


def _has_before(fams: list[str], earlier: set[str], later: str) -> bool:
    if later not in fams:
        return False
    i = fams.index(later)
    return any(f in earlier for f in fams[:i])


def cluster_symbol_day(events: list[dict[str, Any]], *, gap_min: int = EPISODE_GAP_MIN) -> list[list[dict[str, Any]]]:
    ordered = sorted(events, key=lambda e: (str(e.get("event_time") or ""), str(e.get("event_family") or "")))
    clusters: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    last_m: int | None = None
    for e in ordered:
        m = _mins(str(e.get("event_time") or ""))
        if m is None:
            continue
        if not cur or last_m is None or (m - last_m) <= int(gap_min):
            cur.append(e)
            last_m = m
        else:
            clusters.append(cur)
            cur = [e]
            last_m = m
    if cur:
        clusters.append(cur)
    return clusters


def _classify(seq: list[str], trigger: dict[str, Any], sector_improve: bool) -> dict[str, Any]:
    fams = seq
    sector_rs = trigger.get("sector_rs")
    sector_pos = sector_rs is not None and sector_rs == sector_rs and float(sector_rs) > 0
    pull_then_reclaim = _has_before(fams, {"PULLBACK_START"}, "VWAP_RECLAIM")
    reclaim_only = "VWAP_RECLAIM" in fams and not pull_then_reclaim
    comp_then_brk = _has_before(fams, {"COMPRESSION_RELEASE"}, "BREAKOUT20")
    brk_only = "BREAKOUT20" in fams and not comp_then_brk
    rs_then_tech = (
        _has_before(fams, {"RS_IMPROVE"}, "EMA_IMPROVE")
        or _has_before(fams, {"RS_IMPROVE"}, "VWAP_RECLAIM")
        or _has_before(fams, {"RS_IMPROVE"}, "BREAKOUT20")
    )
    tech = next((x for x in ("VWAP_RECLAIM", "BREAKOUT20", "EMA_IMPROVE") if x in fams), None)

    sequence_class = "OTHER"
    archetype = None
    if pull_then_reclaim and (sector_pos or sector_improve):
        sequence_class = "SECTOR_THEN_PULLBACK_THEN_RECLAIM"
        archetype = "CONTROLLED_PULLBACK_RECLAIM"
    elif pull_then_reclaim:
        sequence_class = "PULLBACK_THEN_RECLAIM"
        archetype = "CONTROLLED_PULLBACK_RECLAIM"
    elif reclaim_only:
        sequence_class = "RECLAIM_ONLY"
    elif comp_then_brk and (sector_pos or sector_improve):
        sequence_class = "SECTOR_THEN_COMPRESSION_THEN_BREAKOUT"
        archetype = "COMPRESSION_BREAKOUT_EXPANSION"
    elif comp_then_brk:
        sequence_class = "COMPRESSION_THEN_BREAKOUT"
        archetype = "COMPRESSION_BREAKOUT_EXPANSION"
    elif brk_only:
        sequence_class = "BREAKOUT_ONLY"
    elif rs_then_tech and (sector_pos or sector_improve) and tech:
        sequence_class = "SECTOR_THEN_RS_THEN_TECHNICAL"
        archetype = "SECTOR_LEADER_TRANSITION"
    elif rs_then_tech and tech:
        sequence_class = "RS_THEN_TECHNICAL"
        archetype = "SECTOR_LEADER_TRANSITION"

    return {
        "sequence_class": sequence_class,
        "archetype": archetype,
        "sector_support": bool(sector_pos or sector_improve),
        "pull_then_reclaim": pull_then_reclaim,
        "comp_then_brk": comp_then_brk,
        "rs_then_tech": rs_then_tech,
    }


def build_episodes(
    events: list[dict[str, Any]],
    *,
    gap_min: int = EPISODE_GAP_MIN,
) -> dict[str, Any]:
    stock = [e for e in events if e.get("layer") == "STOCK"]
    sector_ev = [e for e in events if e.get("layer") == "SECTOR"]
    sector_times: dict[tuple[str, str], list[int]] = defaultdict(list)
    for e in sector_ev:
        if e.get("event_family") != "SECTOR_RS_IMPROVE":
            continue
        m = _mins(str(e.get("event_time") or ""))
        sec = str(e.get("sector") or "")
        day = str(e.get("date") or "")
        if m is None:
            continue
        sector_times[(day, sec)].append(m)

    by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for e in stock:
        by_key[(str(e.get("date")), str(e.get("symbol")))].append(e)

    episodes: list[dict[str, Any]] = []
    raw_in_eps = 0
    for (day, sym), xs in by_key.items():
        for cluster in cluster_symbol_day(xs, gap_min=gap_min):
            raw_in_eps += len(cluster)
            trigger = cluster[-1]
            seq = [str(e.get("event_family")) for e in cluster]
            start_t = str(cluster[0].get("event_time"))
            end_t = str(trigger.get("event_time"))
            end_m = _mins(end_t)
            sec = str(trigger.get("sector") or "")
            sector_improve = False
            if end_m is not None:
                for tm in sector_times.get((day, sec), []):
                    if 0 <= end_m - tm <= int(gap_min):
                        sector_improve = True
                        break
            cls = _classify(seq, trigger, sector_improve)
            eid = f"{day}|{sym}|{start_t}|{end_t}"
            rec = {
                "episode_id": eid,
                "date": day,
                "symbol": sym,
                "sector": sec,
                "start_time": start_t,
                "event_time": end_t,
                "decision_time": end_t,
                "feature_bar": trigger.get("feature_bar"),
                "feature_available_at": trigger.get("feature_available_at") or end_t,
                "entry_bar": trigger.get("entry_bar") or end_t,
                "same_bar_close_entry": False,
                "future_used_as_decision_feature": False,
                "future_used_for_episode_boundary": False,
                "raw_event_n_in_episode": len(cluster),
                "sequence": seq,
                "sequence_compact": _compact(seq),
                "trigger_family": trigger.get("event_family"),
                "layer": "STOCK",
                "rec": trigger.get("rec"),
                "hi20": trigger.get("hi20"),
                **{k: trigger.get(k) for k in FEATURE_KEYS},
                **cls,
            }
            episodes.append(rec)
    ids = sorted(e["episode_id"] for e in episodes)
    sha = hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()
    by_arch: dict[str, int] = defaultdict(int)
    by_class: dict[str, int] = defaultdict(int)
    for e in episodes:
        by_class[str(e.get("sequence_class"))] += 1
        if e.get("archetype"):
            by_arch[str(e["archetype"])] += 1
    days = {e["date"] for e in episodes}
    per_day = defaultdict(int)
    for e in episodes:
        per_day[e["date"]] += 1
    dist = list(per_day.values())
    return {
        "ok": True,
        "episodes": episodes,
        "raw_event_n": len(events),
        "raw_stock_event_n": len(stock),
        "raw_events_assigned_to_episodes": raw_in_eps,
        "unique_episode_n": len(episodes),
        "unique_symbol_n": len({e["symbol"] for e in episodes}),
        "unique_day_n": len(days),
        "episode_per_day_mean": (sum(dist) / len(dist)) if dist else None,
        "episode_per_day_p50": sorted(dist)[len(dist) // 2] if dist else None,
        "episode_per_day_max": max(dist) if dist else None,
        "gap_min_precommitted": int(gap_min),
        "gap_grid_searched": False,
        "identities_frozen_before_outcomes": True,
        "future_used_for_episode_boundary": False,
        "episode_id_sha256": sha,
        "archetype_n": dict(by_arch),
        "sequence_class_n": dict(by_class),
        "dedup_rule": "same-symbol events with decision_time gap<=15m form one ordered episode; outcomes never set cluster bounds",
    }


def _compact(seq: list[str]) -> list[str]:
    out: list[str] = []
    for s in seq:
        if not out or out[-1] != s:
            out.append(s)
    return out


def freeze_episode_ids(episodes: list[dict[str, Any]]) -> str:
    ids = sorted(str(e["episode_id"]) for e in episodes)
    return hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()
