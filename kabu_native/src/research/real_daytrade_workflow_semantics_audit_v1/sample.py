"""Blinded causal MTF chart sample of prior machines. No economic retest. No future bars on the chart."""
from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from research.cause_first_mechanism_discovery_v1.clock import in_lunch
from research.cause_first_mechanism_discovery_v1.panel import load_minutes
from research.mtf_5min_sma5_25_75_with_1min_trigger_v1 import SESSION_FLAT, ZONE_ATR_MULT
from research.mtf_5min_sma5_25_75_with_1min_trigger_v1.bars5 import CausalATR1m, FiveMinChart, bucket_start_min
from research.mtf_5min_sma5_25_75_with_1min_trigger_v1.machine import new_sides as mtf_sides, step_side as mtf_step
from research.multi_touch_daily_zone_1m_price_action_v1.daily import daily_from_minutes
from research.one_minute_native_playbook_discovery_v1.states import prep_symbol, to_min
from research.real_daytrade_workflow_semantics_audit_v1 import SAMPLE_LOOKBACK_DAYS, SAMPLE_PER_FAMILY, SAMPLE_SEED, SAMPLE_SYMBOLS
from research.real_daytrade_workflow_semantics_audit_v1.isolation import OUT
from research.sma5_25_75_trend_pullback_playbook_discovery_v1 import SMA75
from research.sma5_25_75_trend_pullback_playbook_discovery_v1.ma import rolling_sma, stack_aligned
from research.sma5_25_75_trend_pullback_playbook_discovery_v1.machine import confirm_minor_swing, new_sides as sma1_sides, step_side as sma1_step


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _sma(closes: list[float], n: int) -> float:
    if len(closes) < n:
        return float("nan")
    xs = closes[-n:]
    return float(sum(float(v) for v in xs) / float(n))


def live_daily_sma(prior: list[float], px: float, n: int) -> float:
    if not _finite(px):
        return float("nan")
    return _sma(list(prior) + [float(px)], n)


def _pick(rows: list[dict[str, Any]], n: int, salt: str) -> list[dict[str, Any]]:
    if not rows:
        return []
    scored = []
    for i, r in enumerate(rows):
        key = f"{salt}|{r.get('symbol')}|{r.get('date')}|{r.get('t')}|{r.get('direction')}|{i}"
        h = hashlib.sha256((SAMPLE_SEED + "|" + key).encode("utf-8")).hexdigest()
        scored.append((h, r))
    scored.sort(key=lambda x: x[0])
    return [r for _, r in scored[:n]]


def _candles(ax, o, h, l, c) -> None:
    for i in range(len(c)):
        if not (_finite(o[i]) and _finite(h[i]) and _finite(l[i]) and _finite(c[i])):
            continue
        col = "#2ca02c" if float(c[i]) >= float(o[i]) else "#d62728"
        ax.vlines(i, float(l[i]), float(h[i]), color="#444444", linewidth=0.6)
        lo, hi = sorted((float(o[i]), float(c[i])))
        ax.vlines(i, lo, hi, color=col, linewidth=2.2)


def _five_from_1m(rec: dict[str, Any], session_idx: list[int], pos: int) -> dict[str, list[float]]:
    buckets: dict[int, dict[str, float]] = {}
    order: list[int] = []
    for j in session_idx[: pos + 1]:
        t = rec["t"][j]
        k = bucket_start_min(t)
        if k is None:
            continue
        o, h, l, c = rec["o"][j], rec["h"][j], rec["l"][j], rec["c"][j]
        if k not in buckets:
            buckets[k] = {"o": o, "h": h, "l": l, "c": c}
            order.append(k)
        else:
            b = buckets[k]
            if _finite(h):
                b["h"] = h if not _finite(b["h"]) else max(float(b["h"]), float(h))
            if _finite(l):
                b["l"] = l if not _finite(b["l"]) else min(float(b["l"]), float(l))
            if _finite(c):
                b["c"] = c
    closes = [float(buckets[k]["c"]) for k in order if _finite(buckets[k]["c"])]
    sma5 = [_sma(closes[: i + 1], 5) for i in range(len(closes))]
    sma25 = [_sma(closes[: i + 1], 25) for i in range(len(closes))]
    sma75 = [_sma(closes[: i + 1], 75) for i in range(len(closes))]
    return {
        "o": [buckets[k]["o"] for k in order],
        "h": [buckets[k]["h"] for k in order],
        "l": [buckets[k]["l"] for k in order],
        "c": [buckets[k]["c"] for k in order],
        "sma5": sma5,
        "sma25": sma25,
        "sma75": sma75,
    }


def _prior_5m(ev: dict[str, Any], recs: dict[tuple[str, str], dict[str, Any]]) -> dict[str, list[float]]:
    symbol = str(ev["symbol"])
    date = str(ev["date"])
    prior_dates = sorted(d for s, d in recs if s == symbol and str(d) < date)
    use = prior_dates[-2:]
    o: list[Any] = []
    h: list[Any] = []
    l: list[Any] = []
    c: list[Any] = []
    for d in use:
        rec = recs[(symbol, d)]
        idx = rec["session_idx"]
        part = _five_from_1m(rec, idx, len(idx) - 1)
        o.extend(part["o"])
        h.extend(part["h"])
        l.extend(part["l"])
        c.extend(part["c"])
    return {"o": o, "h": h, "l": l, "c": c}


def _render(
    ev: dict[str, Any],
    rec: dict[str, Any],
    prior_days: list[dict[str, Any]],
    path: Path,
    recs: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> None:
    pos = int(ev["pos"])
    session_idx = rec["session_idx"]
    o = [rec["o"][i] for i in session_idx[: pos + 1]]
    h = [rec["h"][i] for i in session_idx[: pos + 1]]
    l = [rec["l"][i] for i in session_idx[: pos + 1]]
    c = [rec["c"][i] for i in session_idx[: pos + 1]]
    vw = [rec["vw"][i] for i in session_idx[: pos + 1]]
    today = {
        "open": o[0] if o else float("nan"),
        "high": max(float(x) for x in h if _finite(x)) if any(_finite(x) for x in h) else float("nan"),
        "low": min(float(x) for x in l if _finite(x)) if any(_finite(x) for x in l) else float("nan"),
        "close": c[-1] if c else float("nan"),
    }
    d_all = list(prior_days[-79:]) + [today]
    d_o = [x["open"] for x in d_all]
    d_h = [x["high"] for x in d_all]
    d_l = [x["low"] for x in d_all]
    d_c = [x["close"] for x in d_all]
    d_sma5 = [_sma(d_c[: i + 1], 5) for i in range(len(d_c))]
    d_sma25 = [_sma(d_c[: i + 1], 25) for i in range(len(d_c))]
    d_sma75 = [_sma(d_c[: i + 1], 75) for i in range(len(d_c))]
    today5 = _five_from_1m(rec, session_idx, pos)
    prev5 = _prior_5m(ev, recs or {})
    b5 = {
        "o": list(prev5["o"]) + list(today5["o"]),
        "h": list(prev5["h"]) + list(today5["h"]),
        "l": list(prev5["l"]) + list(today5["l"]),
        "c": list(prev5["c"]) + list(today5["c"]),
    }
    closes = [float(x) for x in b5["c"] if _finite(x)]
    b5["sma5"] = [_sma(closes[: i + 1], 5) for i in range(len(closes))]
    b5["sma25"] = [_sma(closes[: i + 1], 25) for i in range(len(closes))]
    b5["sma75"] = [_sma(closes[: i + 1], 75) for i in range(len(closes))]
    keep = 55
    if len(b5["c"]) > keep:
        for k in list(b5):
            b5[k] = b5[k][-keep:]
    fig, axes = plt.subplots(3, 1, figsize=(11, 10))
    fig.suptitle(f"{ev['family']} {ev['symbol']} {ev['date']} {ev['t']} {ev['direction']}  future hidden", fontsize=11)
    ax = axes[0]
    _candles(ax, d_o, d_h, d_l, d_c)
    ax.plot(d_sma5, color="#1f77b4", lw=1.0, label="daily SMA5")
    ax.plot(d_sma25, color="#ff7f0e", lw=1.0, label="daily SMA25")
    ax.plot(d_sma75, color="#9467bd", lw=1.0, label="daily SMA75")
    pd = prior_days[-1] if prior_days else None
    if pd:
        ax.axhline(float(pd["high"]), color="#2ca02c", ls="--", lw=0.8, label="PDH")
        ax.axhline(float(pd["low"]), color="#d62728", ls="--", lw=0.8, label="PDL")
        ax.axhline(float(pd["close"]), color="#7f7f7f", ls=":", lw=0.8, label="PDC")
    ax.set_title("DAILY through event (today is forming)")
    ax.legend(loc="upper left", fontsize=7, ncol=3)
    ax = axes[1]
    _candles(ax, b5["o"], b5["h"], b5["l"], b5["c"])
    ax.plot(b5["sma5"], color="#1f77b4", lw=0.9, label="5m SMA5")
    ax.plot(b5["sma25"], color="#ff7f0e", lw=0.9, label="5m SMA25")
    ax.plot(b5["sma75"], color="#9467bd", lw=0.9, ls=":", label="5m SMA75 copied numbers")
    ax.set_title("5-MINUTE through event")
    ax.legend(loc="upper left", fontsize=7)
    ax = axes[2]
    n1 = min(len(c), 80)
    sl = slice(len(c) - n1, len(c))
    _candles(ax, o[sl], h[sl], l[sl], c[sl])
    ax.plot(list(vw[sl]), color="#17becf", lw=0.9, label="VWAP")
    ax.axvline(n1 - 1, color="black", lw=0.8)
    ax.set_title("1-MINUTE last bars through event")
    ax.legend(loc="upper left", fontsize=7)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=110)
    plt.close(fig)


def collect_events(bind: dict[str, Any]) -> dict[str, Any]:
    split = dict(bind.get("split") or {})
    disc = [str(d) for d in list(split.get("discovery_dates") or [])]
    conf = set(str(d) for d in list(split.get("confirmation_dates") or []))
    val = set(str(d) for d in list(split.get("frozen_validation_dates") or []))
    pool = set(str(s) for s in list(bind.get("symbols") or []))
    symbols = [s for s in SAMPLE_SYMBOLS if s in pool]
    if len(symbols) < 6:
        symbols = [str(s) for s in list(bind.get("symbols") or [])[:8]]
    if len(disc) < int(SAMPLE_LOOKBACK_DAYS) + 5:
        return {"ok": False, "reason": "not_enough_discovery_dates"}
    allowed = set(disc[-int(SAMPLE_LOOKBACK_DAYS) :])
    print(f"SAMPLE_LOAD symbols={symbols} days={len(allowed)}", flush=True)
    minutes = load_minutes(symbols=symbols, allowed_dates=allowed, forbidden_dates=conf | val)
    if minutes.empty:
        return {"ok": False, "reason": "empty_minutes"}
    minutes["date"] = minutes["date"].astype(str)
    minutes["time_label"] = minutes["time_label"].astype(str).str.slice(0, 5)
    minutes = minutes.sort_values(["date", "symbol", "time_label"])
    blocks = dict((bind.get("blocks") or {}).get("date_to_block") or {})
    mtf_a: list[dict[str, Any]] = []
    sma1_a: list[dict[str, Any]] = []
    recs: dict[tuple[str, str], dict[str, Any]] = {}
    prior_map: dict[tuple[str, str], list[dict[str, Any]]] = {}
    charts: dict[str, FiveMinChart] = defaultdict(FiveMinChart)
    atrs: dict[str, CausalATR1m] = defaultdict(CausalATR1m)
    daily_hist: dict[str, list[float]] = defaultdict(list)
    daily_bars: dict[str, list[dict[str, Any]]] = defaultdict(list)
    sides_m: dict[str, dict[int, Any]] = defaultdict(mtf_sides)
    sides_1: dict[str, dict[int, Any]] = defaultdict(sma1_sides)
    seq: dict[str, int] = defaultdict(int)
    for day, g in minutes.groupby("date", sort=True):
        date = str(day)
        for sym, sg in g.groupby("symbol", sort=False):
            rec0 = prep_symbol(sg)
            symbol = str(sym)
            session_idx = [i for i in range(int(rec0["n"])) if not in_lunch(rec0["t"][i])]
            if len(session_idx) < 40:
                continue
            rec = {
                "session_idx": session_idx,
                "t": rec0["t"],
                "o": rec0["o"],
                "h": rec0["h"],
                "l": rec0["l"],
                "c": rec0["c"],
                "vw": rec0["vw"],
            }
            recs[(symbol, date)] = rec
            prior_map[(symbol, date)] = list(daily_bars[symbol])
            chart = charts[symbol]
            atr1 = atrs[symbol]
            hs = rec0["h"][np.asarray(session_idx, dtype=int)]
            ls = rec0["l"][np.asarray(session_idx, dtype=int)]
            sc = np.asarray([rec0["c"][i] for i in session_idx], dtype=float)
            sma1 = {"sma5": rolling_sma(sc, 5), "sma25": rolling_sma(sc, 25), "sma75": rolling_sma(sc, 75)}
            swing_h = swing_l = None
            d_closes = list(daily_hist[symbol])
            for pos, i in enumerate(session_idx):
                t = rec0["t"][i]
                snap5 = chart.on_minute(t, rec0["o"][i], rec0["h"][i], rec0["l"][i], rec0["c"][i])
                atr_now = atr1.observe(rec0["h"][i], rec0["l"][i], rec0["c"][i])
                seq[symbol] += 1
                sh, sl = confirm_minor_swing(hs, ls, pos)
                if sh is not None:
                    swing_h = sh
                if sl is not None:
                    swing_l = sl
                px = rec0["c"][i]
                if str(t) >= SESSION_FLAT or not _finite(px):
                    continue
                live5, live25, live75 = snap5["live_sma5"], snap5["live_sma25"], snap5["live_sma75"]
                d5 = live_daily_sma(d_closes, float(px), 5)
                d25 = live_daily_sma(d_closes, float(px), 25)
                d75 = live_daily_sma(d_closes, float(px), 75)
                prev_i = session_idx[pos - 1] if pos else i
                prev_c = rec0["c"][prev_i]
                for dsgn, direction in ((1, "BULLISH"), (-1, "BEARISH")):
                    st = sides_m[symbol][dsgn]
                    ev = mtf_step(
                        st,
                        seq=seq[symbol],
                        sign=dsgn,
                        close=float(px),
                        high=float(rec0["h"][i]) if _finite(rec0["h"][i]) else float("nan"),
                        low=float(rec0["l"][i]) if _finite(rec0["l"][i]) else float("nan"),
                        prev_c=prev_c,
                        sma5=live5,
                        sma25=live25,
                        sma75=live75,
                        prev_sma5=float("nan"),
                        vwap=rec0["vw"][i],
                        prev_vw=rec0["vw"][prev_i],
                        tv_pctl=float("nan"),
                        swing_high=swing_h,
                        swing_low=swing_l,
                        stack_now=stack_aligned(live5, live25, live75, dsgn),
                        atr1m=atr_now,
                    )
                    if ev:
                        mtf_a.append(
                            {
                                "family": "mtf_5m_sma",
                                "symbol": symbol,
                                "date": date,
                                "t": t,
                                "pos": pos,
                                "direction": direction,
                                "DIR": dsgn,
                                "block": str(blocks.get(date) or ""),
                                "daily_stack": stack_aligned(d5, d25, d75, dsgn),
                                "tod_min": float((to_min(t) or 0) - 9 * 60),
                                "close": float(px),
                                "daily_sma25": d25,
                                "five_sma25": live25,
                                "zone": float(ZONE_ATR_MULT) * float(atr_now) if _finite(atr_now) else None,
                            }
                        )
                    if pos >= int(SMA75) - 1:
                        s5, s25, s75 = sma1["sma5"][pos], sma1["sma25"][pos], sma1["sma75"][pos]
                        ev1 = sma1_step(
                            sides_1[symbol][dsgn],
                            pos=pos,
                            sign=dsgn,
                            close=float(px),
                            high=float(rec0["h"][i]) if _finite(rec0["h"][i]) else float("nan"),
                            low=float(rec0["l"][i]) if _finite(rec0["l"][i]) else float("nan"),
                            prev_c=prev_c,
                            sma5=s5,
                            sma25=s25,
                            sma75=s75,
                            prev_sma5=sma1["sma5"][pos - 1] if pos else float("nan"),
                            vwap=rec0["vw"][i],
                            prev_vw=rec0["vw"][prev_i],
                            tv_pctl=float("nan"),
                            swing_high=swing_h,
                            swing_low=swing_l,
                            stack_now=stack_aligned(s5, s25, s75, dsgn),
                        )
                        if ev1:
                            sma1_a.append(
                                {
                                    "family": "sma_1m",
                                    "symbol": symbol,
                                    "date": date,
                                    "t": t,
                                    "pos": pos,
                                    "direction": direction,
                                    "DIR": dsgn,
                                    "block": str(blocks.get(date) or ""),
                                    "daily_stack": stack_aligned(d5, d25, d75, dsgn),
                                    "tod_min": float((to_min(t) or 0) - 9 * 60),
                                    "close": float(px),
                                }
                            )
            chart.close_session()
            dbar = daily_from_minutes(rec0, date)
            if dbar and _finite(dbar.get("close")):
                daily_hist[symbol].append(float(dbar["close"]))
                daily_bars[symbol].append({"open": dbar["open"], "high": dbar["high"], "low": dbar["low"], "close": dbar["close"]})
    picked = _pick(mtf_a, int(SAMPLE_PER_FAMILY), "mtf") + _pick(sma1_a, int(SAMPLE_PER_FAMILY), "sma1")
    chart_dir = OUT / "charts"
    rendered = []
    for j, ev in enumerate(picked, start=1):
        key = (ev["symbol"], ev["date"])
        if key not in recs:
            continue
        png = chart_dir / f"{j:02d}_{ev['family']}_{ev['symbol']}_{ev['date']}_{str(ev['t']).replace(':','')}_{ev['direction']}.png"
        _render(ev, recs[key], prior_map.get(key) or [], png, recs=recs)
        row = dict(ev)
        row["chart"] = str(png)
        row["sample_id"] = j
        row["outcome_hidden"] = True
        rendered.append(row)
        print(f"CHART {j}/{len(picked)} {png.name}", flush=True)
    agree = [r for r in mtf_a if r.get("daily_stack")]
    return {
        "ok": True,
        "mtf_event_n": len(mtf_a),
        "sma1_event_n": len(sma1_a),
        "sampled": rendered,
        "mtf_daily_stack_agree_n": len(agree),
        "mtf_daily_stack_agree_rate": (len(agree) / len(mtf_a)) if mtf_a else None,
        "symbols_used": symbols,
        "n_days_loaded": int(minutes["date"].nunique()),
        "future_hidden": True,
        "economic_test_not_run": True,
    }
