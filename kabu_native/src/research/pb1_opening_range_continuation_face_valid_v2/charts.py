"""Blinded DAILY / 5-minute / 1-minute charts. New VERIFY seed. V1 24 excluded."""
from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from research.cause_first_mechanism_discovery_v1.panel import load_minutes
from research.mtf_5min_sma5_25_75_with_1min_trigger_v1.bars5 import bucket_start_min
from research.multi_touch_daily_zone_1m_price_action_v1.daily import daily_from_minutes
from research.one_minute_native_playbook_discovery_v1.states import prep_symbol
from research.pb1_opening_range_continuation_face_valid_v1.or15 import session_idx_of
from research.pb1_opening_range_continuation_face_valid_v2 import (
    SAMPLE_N,
    SAMPLE_PER_BLOCK,
    SAMPLE_SEED,
    V1_DEV_SAMPLE_KEYS,
)
from research.pb1_opening_range_continuation_face_valid_v2.isolation import OUT

V1_DEV = {(str(a), str(b), str(c)) for a, b, c in V1_DEV_SAMPLE_KEYS}


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _event_key(e: dict[str, Any]) -> tuple[str, str, str]:
    return (str(e.get("symbol")), str(e.get("date")), str(e.get("direction")))


def _hash_key(e: dict[str, Any]) -> str:
    key = f"{e.get('symbol')}|{e.get('date')}|{e.get('direction')}|{e.get('trigger_t')}|{e.get('block')}"
    return hashlib.sha256((SAMPLE_SEED + "|" + key).encode("utf-8")).hexdigest()


def sampling_procedure() -> str:
    return (
        f"Seed={SAMPLE_SEED}. Independent of the V1 24-event development sample. "
        f"Partition V2 causal continuation events by Discovery block (D1/D2/D3/D4). "
        f"Exclude V1_DEV_SAMPLE_KEYS. Within each block, sort by SHA256(seed|symbol|date|direction|trigger_t). "
        f"Take the first {SAMPLE_PER_BLOCK // 2} bull and first {SAMPLE_PER_BLOCK // 2} bear "
        f"(fill from leftover if a side is short) for a target of {SAMPLE_N}. "
        "No sort by gap, TV, chart prettiness, or any future path."
    )


def pick_sample(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_block: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        if _event_key(e) in V1_DEV:
            continue
        by_block[str(e.get("block") or "na")].append(e)
    picked: list[dict[str, Any]] = []
    used: set[tuple[str, str, str]] = set()
    for block in ("D1", "D2", "D3", "D4"):
        rows = sorted(by_block.get(block) or [], key=_hash_key)
        bulls = [r for r in rows if r.get("direction") == "bull"]
        bears = [r for r in rows if r.get("direction") == "bear"]
        take: list[dict[str, Any]] = []
        half = SAMPLE_PER_BLOCK // 2
        take.extend(bulls[:half])
        take.extend(bears[:half])
        if len(take) < SAMPLE_PER_BLOCK:
            leftover = [r for r in rows if r not in take]
            take.extend(leftover[: SAMPLE_PER_BLOCK - len(take)])
        for r in take:
            k = _event_key(r)
            if k in used or k in V1_DEV:
                continue
            used.add(k)
            picked.append(r)
    for i, r in enumerate(picked, start=1):
        r["sample_id"] = i
        r["v1_dev_reused"] = False
    return picked


def _candles(ax, o, h, l, c) -> None:
    for i in range(len(c)):
        if not (_finite(o[i]) and _finite(h[i]) and _finite(l[i]) and _finite(c[i])):
            continue
        col = "#2ca02c" if float(c[i]) >= float(o[i]) else "#d62728"
        ax.vlines(i, float(l[i]), float(h[i]), color="#444444", linewidth=0.6)
        lo, hi = sorted((float(o[i]), float(c[i])))
        ax.vlines(i, lo, hi, color=col, linewidth=2.2)


def _sma(closes: list[float], n: int) -> float:
    if len(closes) < n:
        return float("nan")
    return float(sum(float(v) for v in closes[-n:]) / float(n))


def _five_from_1m(rec: dict[str, Any], session_idx: list[int], pos: int) -> dict[str, list]:
    buckets: dict[int, dict[str, float]] = {}
    order: list[int] = []
    labels: list[str] = []
    for j in session_idx:
        if j > pos:
            break
        t = rec["t"][j]
        k = bucket_start_min(t)
        if k is None:
            continue
        o, h, l, c = rec["o"][j], rec["h"][j], rec["l"][j], rec["c"][j]
        if k not in buckets:
            buckets[k] = {"o": o, "h": h, "l": l, "c": c}
            order.append(k)
            labels.append(str(t)[:5])
        else:
            b = buckets[k]
            if _finite(h):
                b["h"] = h if not _finite(b["h"]) else max(float(b["h"]), float(h))
            if _finite(l):
                b["l"] = l if not _finite(b["l"]) else min(float(b["l"]), float(l))
            if _finite(c):
                b["c"] = c
    return {
        "o": [buckets[k]["o"] for k in order],
        "h": [buckets[k]["h"] for k in order],
        "l": [buckets[k]["l"] for k in order],
        "c": [buckets[k]["c"] for k in order],
        "t": labels,
    }


def _render(ev: dict[str, Any], rec: dict[str, Any], prior_days: list[dict[str, Any]], path: Path) -> None:
    pos = int(ev["trigger_pos"])
    session_idx = rec["session_idx"]
    o = [rec["o"][i] for i in session_idx if i <= pos]
    h = [rec["h"][i] for i in session_idx if i <= pos]
    l = [rec["l"][i] for i in session_idx if i <= pos]
    c = [rec["c"][i] for i in session_idx if i <= pos]
    vw = [rec["vw"][i] for i in session_idx if i <= pos]
    times = [rec["t"][i] for i in session_idx if i <= pos]
    today = {
        "open": o[0] if o else float("nan"),
        "high": max(float(x) for x in h if _finite(x)) if any(_finite(x) for x in h) else float("nan"),
        "low": min(float(x) for x in l if _finite(x)) if any(_finite(x) for x in l) else float("nan"),
        "close": c[-1] if c else float("nan"),
    }
    d_all = list(prior_days[-79:]) + [today]
    fig, axes = plt.subplots(3, 1, figsize=(11, 12), gridspec_kw={"height_ratios": [1.1, 1.0, 1.2]})
    axd, ax5, ax1 = axes
    axd.set_title(
        f"DAILY  sample={ev.get('sample_id')} {ev.get('symbol')} {ev.get('date')} "
        f"{ev.get('direction')} {ev.get('in_play_reason')} room={ev.get('room_class')}  [future hidden]"
    )
    _candles(axd, [x["open"] for x in d_all], [x["high"] for x in d_all], [x["low"] for x in d_all], [x["close"] for x in d_all])
    closes = [float(x["close"]) for x in d_all]
    axd.plot([_sma(closes[: i + 1], 5) for i in range(len(closes))], color="#1f77b4", lw=0.9, label="SMA5")
    axd.plot([_sma(closes[: i + 1], 25) for i in range(len(closes))], color="#ff7f0e", lw=0.9, label="SMA25")
    axd.plot([_sma(closes[: i + 1], 75) for i in range(len(closes))], color="#9467bd", lw=0.9, label="SMA75")
    axd.legend(loc="upper left", fontsize=7)
    axd.set_xticks([])

    five = _five_from_1m(rec, session_idx, pos)
    ax5.set_title(
        f"5-MINUTE open={ev.get('open_state')} away_n={ev.get('away_n')} "
        f"retest_min={ev.get('break_to_retest_minutes')}  [future hidden]"
    )
    _candles(ax5, five["o"], five["h"], five["l"], five["c"])
    if _finite(ev.get("or_high")):
        ax5.axhline(float(ev["or_high"]), color="#d62728", ls="--", lw=0.8, label="OR_HIGH")
    if _finite(ev.get("or_low")):
        ax5.axhline(float(ev["or_low"]), color="#2ca02c", ls="--", lw=0.8, label="OR_LOW")
    if _finite(ev.get("pdh")):
        ax5.axhline(float(ev["pdh"]), color="#8c564b", ls=":", lw=0.7, label="PDH")
    if _finite(ev.get("pdl")):
        ax5.axhline(float(ev["pdl"]), color="#8c564b", ls=":", lw=0.7, label="PDL")
    ax5.legend(loc="upper left", fontsize=7)
    ax5.set_xticks([])

    ax1.set_title(
        f"1-MINUTE break={ev.get('break_t')} retest={ev.get('retest_t')} "
        f"trigger={ev.get('trigger_t')} {ev.get('trigger_labels')}  [future hidden]"
    )
    _candles(ax1, o, h, l, c)
    ax1.plot(vw, color="#17becf", lw=0.8, label="VWAP")
    if _finite(ev.get("or_high")):
        ax1.axhline(float(ev["or_high"]), color="#d62728", ls="--", lw=0.8)
    if _finite(ev.get("or_low")):
        ax1.axhline(float(ev["or_low"]), color="#2ca02c", ls="--", lw=0.8)
    tmap = {str(t): i for i, t in enumerate(times)}
    for lab, key, col in (("B", "break_t", "#d62728"), ("R", "retest_t", "#ff7f0e"), ("T", "trigger_t", "#1f77b4")):
        idx = tmap.get(str(ev.get(key)))
        if idx is not None:
            ax1.axvline(idx, color=col, ls=":", lw=0.9)
            ax1.text(idx, (c[idx] if _finite(c[idx]) else 0), lab, color=col, fontsize=8)
    ax1.legend(loc="upper left", fontsize=7)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=110)
    plt.close(fig)


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
                "in_play": ev.get("in_play"),
                "in_play_reason": ev.get("in_play_reason"),
                "open_state": ev.get("open_state"),
                "room_class": ev.get("room_class"),
                "break_to_retest_minutes": ev.get("break_to_retest_minutes"),
                "away_n": ev.get("away_n"),
                "trigger_t": ev.get("trigger_t"),
                "trigger_labels": ev.get("trigger_labels"),
                "abs_gap_atr": ev.get("abs_gap_atr"),
                "xs_rank_pct": ev.get("xs_rank_pct"),
                "tv_sequence": ev.get("tv_sequence"),
                "daily_bias": ev.get("daily_bias"),
                "chart": str(path.name),
                "v1_dev_reused": False,
                "future_hidden": True,
            }
        )
    return meta
