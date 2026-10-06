"""New 96-chart blinded V3 sample. Future hidden. Prior face samples excluded."""
from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from research.cause_first_mechanism_discovery_v1.panel import load_minutes
from research.multi_touch_daily_zone_1m_price_action_v1.daily import daily_from_minutes
from research.one_minute_native_playbook_discovery_v1.states import prep_symbol
from research.pb1_opening_range_continuation_face_valid_v1.or15 import session_idx_of
from research.pb1_opening_range_continuation_face_valid_v2.charts import V1_DEV, _candles, _event_key, _finite, _five_from_1m, _sma
from research.pb1_playbook_redesign_v3 import SAMPLE_N, SAMPLE_SEED
from research.pb1_playbook_redesign_v3.isolation import OUT


def _hash_key(e: dict[str, Any]) -> str:
    key = f"{e.get('symbol')}|{e.get('date')}|{e.get('direction')}|{e.get('trigger_t')}|{e.get('block')}"
    return hashlib.sha256((SAMPLE_SEED + "|" + key).encode("utf-8")).hexdigest()


def excluded_keys(bind: dict[str, Any]) -> set[tuple[str, str, str]]:
    out: set[tuple[str, str, str]] = set(V1_DEV)
    for name in ("v1_dev_keys", "v2_verify_keys", "trigger_rca96_keys", "structure_rca96_keys"):
        out |= set(bind.get(name) or set())
    return out


def pick_sample(events: list[dict[str, Any]], bind: dict[str, Any]) -> list[dict[str, Any]]:
    ban = excluded_keys(bind)
    by_cell: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    leftover = []
    for e in events:
        k = _event_key(e)
        if k in ban:
            continue
        leftover.append(e)
        by_cell[(str(e.get("block") or "na"), str(e.get("direction") or "na"))].append(e)
    picked: list[dict[str, Any]] = []
    used: set[tuple[str, str, str]] = set()
    per = max(1, SAMPLE_N // 8)
    for block in ("D1", "D2", "D3", "D4"):
        for side in ("bull", "bear"):
            rows = sorted(by_cell.get((block, side), []), key=_hash_key)
            for r in rows[:per]:
                ek = _event_key(r)
                if ek in used:
                    continue
                used.add(ek)
                picked.append(r)
    if len(picked) < SAMPLE_N:
        for r in sorted(leftover, key=_hash_key):
            ek = _event_key(r)
            if ek in used:
                continue
            used.add(ek)
            picked.append(r)
            if len(picked) >= SAMPLE_N:
                break
    picked = picked[:SAMPLE_N]
    for i, r in enumerate(picked, start=1):
        r["sample_id"] = i
        r["future_hidden"] = True
        r["prior_sample_reused"] = False
    return picked


def sampling_procedure() -> str:
    return (
        f"Seed={SAMPLE_SEED}. Exclude V1 development, V2 VERIFY, trigger-RCA 96, structure-RCA 96. "
        f"Partition remaining V3 events by Discovery block × side, hash-sort, fill to {SAMPLE_N}. "
        "No sort by gap, TV, chart prettiness, or any future path."
    )


def _zone_bands(ax, zones: list[dict[str, Any]]) -> None:
    for z in zones or []:
        lo, hi = z.get("zone_low"), z.get("zone_high")
        if not (_finite(lo) and _finite(hi)):
            continue
        col = "#d62728" if str(z.get("role")) == "RESISTANCE" else "#2ca02c"
        ax.axhspan(float(lo), float(hi), color=col, alpha=0.12, lw=0)
        if _finite(z.get("zone_mid")):
            ax.axhline(float(z["zone_mid"]), color=col, ls=":", lw=0.5, alpha=0.8)


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
        f"{ev.get('direction')} route={ev.get('structural_route')} zone={ev.get('zone_class')}  [future hidden]"
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
        f"5-MINUTE defended={ev.get('defended_level_type')} R={ev.get('planned_R')} "
        f"trig={ev.get('trigger_primary')}  [future hidden]"
    )
    _candles(ax5, five["o"], five["h"], five["l"], five["c"])
    _zone_bands(ax5, list(ev.get("chart_zones") or []))
    if _finite(ev.get("or_high")):
        ax5.axhline(float(ev["or_high"]), color="#d62728", ls="--", lw=0.8, label="OR_HIGH")
    if _finite(ev.get("or_low")):
        ax5.axhline(float(ev["or_low"]), color="#2ca02c", ls="--", lw=0.8, label="OR_LOW")
    if _finite(ev.get("pdh")):
        ax5.axhline(float(ev["pdh"]), color="#8c564b", ls=":", lw=0.7, label="PDH")
    if _finite(ev.get("pdl")):
        ax5.axhline(float(ev["pdl"]), color="#8c564b", ls=":", lw=0.7, label="PDL")
    if _finite(ev.get("pdc")):
        ax5.axhline(float(ev["pdc"]), color="#7f7f7f", ls="-.", lw=0.6, label="PDC")
    if _finite(ev.get("retest_extreme")):
        ax5.axhline(float(ev["retest_extreme"]), color="#ff7f0e", ls="--", lw=0.8, label="RETEST_EXT")
    ax5.legend(loc="upper left", fontsize=6)
    ax5.set_xticks([])

    ax1.set_title(
        f"1-MINUTE break={ev.get('break_t')} leave/retest={ev.get('retest_t')} "
        f"trigger={ev.get('trigger_t')}  [future hidden]"
    )
    _candles(ax1, o, h, l, c)
    ax1.plot(vw, color="#17becf", lw=0.8, label="VWAP")
    _zone_bands(ax1, list(ev.get("chart_zones") or []))
    if _finite(ev.get("or_high")):
        ax1.axhline(float(ev["or_high"]), color="#d62728", ls="--", lw=0.8)
    if _finite(ev.get("or_low")):
        ax1.axhline(float(ev["or_low"]), color="#2ca02c", ls="--", lw=0.8)
    if _finite(ev.get("retest_extreme")):
        ax1.axhline(float(ev["retest_extreme"]), color="#ff7f0e", ls="--", lw=0.8)
    if _finite(ev.get("sma25")):
        ax1.axhline(float(ev["sma25"]), color="#ff7f0e", ls=":", lw=0.6)
    if _finite(ev.get("sma75")):
        ax1.axhline(float(ev["sma75"]), color="#9467bd", ls=":", lw=0.6)
    tmap = {str(t): i for i, t in enumerate(times)}
    for lab, key, col in (
        ("B", "break_t", "#d62728"),
        ("R", "retest_t", "#ff7f0e"),
        ("T", "trigger_t", "#1f77b4"),
    ):
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
                "trigger_primary": ev.get("trigger_primary"),
                "structural_route": ev.get("structural_route"),
                "zone_class": ev.get("zone_class"),
                "defended_level_type": ev.get("defended_level_type"),
                "break_t": ev.get("break_t"),
                "retest_t": ev.get("retest_t"),
                "trigger_t": ev.get("trigger_t"),
                "chart": str(path.name),
                "future_hidden": True,
            }
        )
    return meta
