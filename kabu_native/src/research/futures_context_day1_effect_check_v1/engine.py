"""Day1 lead-effect engine. Frozen features. Clock is the statistical unit. No sendorder."""
from __future__ import annotations

import json
import math
import statistics
from bisect import bisect_right
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.futures_context_day1_effect_check_v1 import (
    AGREEMENT_RET,
    ANCHOR_HM,
    CASE_A,
    CASE_B,
    CASE_C,
    EARLY_HM,
    FEATURE_NAMES,
    HORIZON_LABEL,
    HORIZONS_SEC,
    LATE_HM,
    MATERIAL_BPS,
    NEXT_A,
    NEXT_B,
    NEXT_C,
    PLACEBO_SHIFT_SEC,
    RET_WINDOWS_SEC,
    SIGNED_FEATURES,
    TRADING_DATE,
)
from research.new_causal_information_acquisition_v1.reconcile import iter_jsonl, level_price, parse_received_at
from research.new_causal_information_acquisition_v1.writer import day_layout

JST = ZoneInfo("Asia/Tokyo")


def _finite(v: Any) -> bool:
    try:
        if v is None or v == "":
            return False
        x = float(v)
        return math.isfinite(x) and x > 0
    except (TypeError, ValueError):
        return False


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _epoch(dt: datetime) -> float:
    return dt.astimezone(JST).timestamp()


def clock_dt(day: str, hm: tuple[int, int]) -> datetime:
    return datetime(int(day[:4]), int(day[4:6]), int(day[6:8]), hm[0], hm[1], 0, tzinfo=JST)


def fut_px(rec: dict[str, Any]) -> Optional[float]:
    px = _f(rec.get("CurrentPrice"))
    if px is not None and px > 0:
        return px
    bid = _f(level_price(rec.get("Bid1")))
    ask = _f(level_price(rec.get("Ask1")))
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        return 0.5 * (bid + ask)
    return None


def stock_quotes(rec: dict[str, Any]) -> tuple[Optional[float], Optional[float], Optional[float]]:
    bid = _f(rec.get("canonical_best_bid") if rec.get("canonical_best_bid") is not None else rec.get("bid"))
    ask = _f(rec.get("canonical_best_ask") if rec.get("canonical_best_ask") is not None else rec.get("ask"))
    if bid is None or ask is None:
        payload = rec.get("original_payload") if isinstance(rec.get("original_payload"), dict) else {}
        buy = payload.get("Buy1") if isinstance(payload.get("Buy1"), dict) else {}
        sell = payload.get("Sell1") if isinstance(payload.get("Sell1"), dict) else {}
        bid = bid if bid is not None else _f(buy.get("Price"))
        ask = ask if ask is not None else _f(sell.get("Price"))
    mid = None
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        mid = 0.5 * (bid + ask)
    return bid, ask, mid


class AsOf:
    def __init__(self, times: list[float], rows: list[Any]) -> None:
        self.times = times
        self.rows = rows

    def at(self, t_epoch: float) -> tuple[Optional[Any], int]:
        """Last observation with time <= t. Returns (row, leak_if_positive_unused)."""
        i = bisect_right(self.times, t_epoch) - 1
        if i < 0:
            return None, 0
        return self.rows[i], 0

    def used_after(self, t_epoch: float) -> int:
        i = bisect_right(self.times, t_epoch)
        return 1 if i < len(self.times) and self.times[i] <= t_epoch else 0


def load_futures_series(path: Path) -> AsOf:
    times: list[float] = []
    rows: list[float] = []
    for _i, rec in iter_jsonl(path):
        if rec is None:
            continue
        dt = parse_received_at(rec.get("received_at"))
        px = fut_px(rec)
        if dt is None or px is None:
            continue
        times.append(_epoch(dt))
        rows.append(px)
    return AsOf(times, rows)


def load_stock_series(stock_dir: Path, expected: list[str]) -> dict[str, AsOf]:
    buckets: dict[str, list[tuple[float, tuple[float, float, float]]]] = {s: [] for s in expected}
    extra_ok = True
    files = sorted(stock_dir.glob("push_part_*.jsonl")) if stock_dir.is_dir() else []
    for fp in files:
        for _i, rec in iter_jsonl(fp):
            if rec is None:
                continue
            payload = rec.get("original_payload") if isinstance(rec.get("original_payload"), dict) else {}
            sym = str(rec.get("symbol") or payload.get("Symbol") or "").split("@", 1)[0]
            if sym not in buckets:
                continue
            dt = parse_received_at(rec.get("received_at_jst") or rec.get("received_at"))
            bid, ask, mid = stock_quotes(rec)
            if dt is None or bid is None or ask is None or mid is None:
                continue
            buckets[sym].append((_epoch(dt), (bid, ask, mid)))
    out: dict[str, AsOf] = {}
    for sym, items in buckets.items():
        items.sort(key=lambda x: x[0])
        out[sym] = AsOf([t for t, _ in items], [q for _, q in items])
    return out


def ret_asof(series: AsOf, t_epoch: float, lookback_sec: int) -> tuple[Optional[float], int]:
    """Causal return using only observations <= t. Leakage n is 0 by construction."""
    now, _ = series.at(t_epoch)
    lag, _ = series.at(t_epoch - float(lookback_sec))
    leak = 0
    i_now = bisect_right(series.times, t_epoch) - 1
    i_lag = bisect_right(series.times, t_epoch - float(lookback_sec)) - 1
    if i_now >= 0 and series.times[i_now] > t_epoch:
        leak += 1
    if i_lag >= 0 and series.times[i_lag] > (t_epoch - float(lookback_sec)):
        leak += 1
    if now is None or lag is None or lag == 0:
        return None, leak
    return (float(now) - float(lag)) / float(lag), leak


def zscore(xs: list[Optional[float]]) -> list[Optional[float]]:
    finite = [float(x) for x in xs if x is not None]
    if len(finite) < 2:
        return [None for _ in xs]
    mu = statistics.mean(finite)
    sd = statistics.pstdev(finite)
    if sd == 0:
        return [0.0 if x is not None else None for x in xs]
    return [((float(x) - mu) / sd) if x is not None else None for x in xs]


def mean(xs: list[float]) -> Optional[float]:
    return statistics.mean(xs) if xs else None


def median(xs: list[float]) -> Optional[float]:
    return statistics.median(xs) if xs else None


def bps(num: Optional[float], den: Optional[float]) -> Optional[float]:
    if num is None or den is None or den == 0:
        return None
    return 10000.0 * (float(num) / float(den))


def stock_outcomes_at(
    quotes: AsOf, t_epoch: float, horizon_sec: int
) -> dict[str, Optional[float]]:
    q0, _ = quotes.at(t_epoch)
    q1, _ = quotes.at(t_epoch + float(horizon_sec))
    if q0 is None or q1 is None:
        return {"MID_RETURN_BPS": None, "LONG_EXEC_MARKOUT_BPS": None, "SHORT_EXEC_MARKOUT_BPS": None}
    bid0, ask0, mid0 = q0
    bid1, ask1, mid1 = q1
    return {
        "MID_RETURN_BPS": bps(mid1 - mid0, mid0) if mid0 and mid1 else None,
        "LONG_EXEC_MARKOUT_BPS": bps(bid1 - ask0, ask0) if ask0 and bid1 else None,
        "SHORT_EXEC_MARKOUT_BPS": bps(bid0 - ask1, bid0) if bid0 and ask1 else None,
    }


def _hm_key(hm: tuple[int, int]) -> str:
    return f"{hm[0]:02d}:{hm[1]:02d}"


def block_of(hm: tuple[int, int]) -> str:
    if hm in EARLY_HM:
        return "EARLY"
    if hm in LATE_HM:
        return "LATE"
    return "OTHER"


def expected_symbols(native_root: Path, day: str) -> list[str]:
    layout = day_layout(day, native_root=native_root)
    prepared = json.loads(layout["prepared_manifest"].read_text(encoding="utf-8"))
    return list(prepared.get("core10") or []) + list(prepared.get("dynamic38") or [])


def build_clock_table(*, native_root: Path, day: str = TRADING_DATE) -> dict[str, Any]:
    layout = day_layout(day, native_root=native_root)
    symbols = expected_symbols(native_root, day)
    nk = load_futures_series(layout["nk225mini_jsonl"])
    tx = load_futures_series(layout["topix_jsonl"])
    stocks = load_stock_series(layout["stock"], symbols)
    leak = 0
    clocks: list[dict[str, Any]] = []
    stock_rows: list[dict[str, Any]] = []

    for hm in ANCHOR_HM:
        t = clock_dt(day, hm)
        te = _epoch(t)
        feats: dict[str, Optional[float]] = {}
        for win in RET_WINDOWS_SEC:
            nk_r, lk = ret_asof(nk, te, win)
            tx_r, lk2 = ret_asof(tx, te, win)
            leak += lk + lk2
            feats[f"NK_RET_{win}S"] = nk_r
            feats[f"TOPIX_RET_{win}S"] = tx_r
        nk60 = feats["NK_RET_60S"]
        tx60 = feats["TOPIX_RET_60S"]
        if nk60 is None or tx60 is None:
            feats["AGREEMENT"] = None
        elif nk60 > 0 and tx60 > 0:
            feats["AGREEMENT"] = 1.0
        elif nk60 < 0 and tx60 < 0:
            feats["AGREEMENT"] = -1.0
        else:
            feats["AGREEMENT"] = 0.0
        clocks.append({"hm": hm, "label": _hm_key(hm), "block": block_of(hm), "t_epoch": te, "features": feats})

        for sym in symbols:
            series = stocks.get(sym)
            if series is None:
                continue
            rec: dict[str, Any] = {"symbol": sym, "clock": _hm_key(hm), "block": block_of(hm)}
            for h in HORIZONS_SEC:
                rec[HORIZON_LABEL[h]] = stock_outcomes_at(series, te, h)
            stock_rows.append(rec)

    nk_z = zscore([c["features"]["NK_RET_60S"] for c in clocks])
    tx_z = zscore([c["features"]["TOPIX_RET_60S"] for c in clocks])
    for c, a, b in zip(clocks, nk_z, tx_z):
        c["features"]["COMPOSITE"] = (a + b) if a is not None and b is not None else None

    clock_out: list[dict[str, Any]] = []
    for c in clocks:
        lab = c["label"]
        members = [r for r in stock_rows if r["clock"] == lab]
        row: dict[str, Any] = {
            "clock": lab,
            "block": c["block"],
            "stock_n": len(members),
            "features": c["features"],
        }
        for h in HORIZONS_SEC:
            hl = HORIZON_LABEL[h]
            for metric in ("MID_RETURN_BPS", "LONG_EXEC_MARKOUT_BPS", "SHORT_EXEC_MARKOUT_BPS"):
                xs = [float(m[hl][metric]) for m in members if m[hl].get(metric) is not None]
                row[f"{hl}_{metric}_mean"] = mean(xs)
                row[f"{hl}_{metric}_median"] = median(xs)
                row[f"{hl}_{metric}_n"] = len(xs)
        clock_out.append(row)

    return {
        "symbols": symbols,
        "symbol_n": len(symbols),
        "clocks": clock_out,
        "stock_anchor_n": len(stock_rows),
        "stock_rows": stock_rows,
        "future_leakage_n": leak,
        "anchor_clock_n": len(ANCHOR_HM),
        "nk_series": nk,
        "tx_series": tx,
        "stock_series": stocks,
    }


def _clocks_in(clocks: list[dict[str, Any]], block: Optional[str]) -> list[dict[str, Any]]:
    if block is None:
        return clocks
    return [c for c in clocks if c.get("block") == block]


def bucket_signed(clocks: list[dict[str, Any]], feature: str, horizon: str) -> dict[str, Any]:
    pos = [c for c in clocks if isinstance(c["features"].get(feature), (int, float)) and c["features"][feature] > 0]
    neg = [c for c in clocks if isinstance(c["features"].get(feature), (int, float)) and c["features"][feature] < 0]
    allc = [c for c in clocks if c.get(f"{horizon}_MID_RETURN_BPS_mean") is not None]

    def agg(rows: list[dict[str, Any]], metric: str) -> dict[str, Optional[float]]:
        xs = [float(r[f"{horizon}_{metric}_mean"]) for r in rows if r.get(f"{horizon}_{metric}_mean") is not None]
        return {"n": len(xs), "mean": mean(xs), "median": median(xs)}

    pop_mid = agg(allc, "MID_RETURN_BPS")
    pop_long = agg(allc, "LONG_EXEC_MARKOUT_BPS")
    pop_short = agg(allc, "SHORT_EXEC_MARKOUT_BPS")
    pos_mid = agg(pos, "MID_RETURN_BPS")
    neg_mid = agg(neg, "MID_RETURN_BPS")
    pos_long = agg(pos, "LONG_EXEC_MARKOUT_BPS")
    neg_short = agg(neg, "SHORT_EXEC_MARKOUT_BPS")
    mid_shift = None
    if pos_mid["mean"] is not None and neg_mid["mean"] is not None:
        mid_shift = pos_mid["mean"] - neg_mid["mean"]
    elif pos_mid["mean"] is not None:
        mid_shift = pos_mid["mean"]
    long_impr = None
    if pos_long["mean"] is not None and pop_long["mean"] is not None:
        long_impr = pos_long["mean"] - pop_long["mean"]
    short_impr = None
    if neg_short["mean"] is not None and pop_short["mean"] is not None:
        short_impr = neg_short["mean"] - pop_short["mean"]
    long_ok = bool(pos_mid["mean"] is not None and pos_mid["mean"] > 0 and long_impr is not None and long_impr > 0)
    short_ok = bool(neg_mid["mean"] is not None and neg_mid["mean"] < 0 and short_impr is not None and short_impr > 0)
    direction = 0
    if mid_shift is not None:
        direction = 1 if mid_shift > 0 else (-1 if mid_shift < 0 else 0)
    return {
        "feature": feature,
        "horizon": horizon,
        "clock_n": len(clocks),
        "pos_clock_n": len(pos),
        "neg_clock_n": len(neg),
        "pos_mid": pos_mid,
        "neg_mid": neg_mid,
        "pop_mid": pop_mid,
        "pos_long": pos_long,
        "neg_short": neg_short,
        "pop_long": pop_long,
        "pop_short": pop_short,
        "mid_shift_bps": mid_shift,
        "long_impr_bps": long_impr,
        "short_impr_bps": short_impr,
        "long_candidate": long_ok,
        "short_candidate": short_ok,
        "direction": direction,
        "pos_mid_median": pos_mid["median"],
        "neg_mid_median": neg_mid["median"],
    }


def bucket_agreement(clocks: list[dict[str, Any]], horizon: str) -> dict[str, Any]:
    groups = {"both_up": [], "mixed": [], "both_down": []}
    for c in clocks:
        a = c["features"].get("AGREEMENT")
        if a == 1.0:
            groups["both_up"].append(c)
        elif a == -1.0:
            groups["both_down"].append(c)
        elif a == 0.0:
            groups["mixed"].append(c)

    def agg(rows: list[dict[str, Any]], metric: str) -> dict[str, Optional[float]]:
        xs = [float(r[f"{horizon}_{metric}_mean"]) for r in rows if r.get(f"{horizon}_{metric}_mean") is not None]
        return {"n": len(xs), "mean": mean(xs), "median": median(xs)}

    out = {}
    for name, rows in groups.items():
        out[name] = {
            "MID": agg(rows, "MID_RETURN_BPS"),
            "LONG": agg(rows, "LONG_EXEC_MARKOUT_BPS"),
            "SHORT": agg(rows, "SHORT_EXEC_MARKOUT_BPS"),
        }
    up = out["both_up"]["MID"]["mean"]
    down = out["both_down"]["MID"]["mean"]
    shift = (up - down) if up is not None and down is not None else None
    return {"horizon": horizon, "groups": out, "mid_shift_both_up_minus_down_bps": shift}


def decay_pattern(shifts: dict[str, Optional[float]]) -> bool:
    """Natural lead-decay if short-horizon |shift| >= longer among 1m/3m/5m, same sign."""
    s1 = shifts.get("1m")
    s3 = shifts.get("3m")
    s5 = shifts.get("5m")
    vals = [v for v in (s1, s3, s5) if v is not None]
    if len(vals) < 2:
        return False
    signs = [1 if v > 0 else (-1 if v < 0 else 0) for v in vals]
    if not (all(s == signs[0] for s in signs) and signs[0] != 0):
        return False
    abs1 = abs(s1) if s1 is not None else None
    abs3 = abs(s3) if s3 is not None else None
    abs5 = abs(s5) if s5 is not None else None
    if abs1 is not None and abs5 is not None and abs1 >= abs5:
        return True
    if abs3 is not None and abs5 is not None and abs3 >= abs5:
        return True
    if abs1 is not None and abs3 is not None and abs1 >= abs3:
        return True
    return False


def top_symbol_exclusion(
    *,
    stock_rows: list[dict[str, Any]],
    clocks: list[dict[str, Any]],
    feature: str,
    horizon: str,
    drop_n: int,
) -> dict[str, Any]:
    pos_clocks = {c["clock"] for c in clocks if isinstance(c["features"].get(feature), (int, float)) and c["features"][feature] > 0}
    contrib: dict[str, list[float]] = {}
    for r in stock_rows:
        if r["clock"] not in pos_clocks:
            continue
        v = (r.get(horizon) or {}).get("MID_RETURN_BPS")
        if v is None:
            continue
        contrib.setdefault(r["symbol"], []).append(float(v))
    ranked = sorted(((statistics.mean(xs), s) for s, xs in contrib.items() if xs), reverse=True)
    drop = [s for _m, s in ranked[:drop_n]]
    remain = [s for _m, s in ranked[drop_n:]]
    # rebuild clock means without dropped
    by_clock: dict[str, list[float]] = {}
    for r in stock_rows:
        if r["symbol"] in drop:
            continue
        v = (r.get(horizon) or {}).get("MID_RETURN_BPS")
        if v is None:
            continue
        by_clock.setdefault(r["clock"], []).append(float(v))
    pos_xs = []
    neg_xs = []
    for c in clocks:
        xs = by_clock.get(c["clock"]) or []
        if not xs:
            continue
        m = statistics.mean(xs)
        f = c["features"].get(feature)
        if isinstance(f, (int, float)) and f > 0:
            pos_xs.append(m)
        elif isinstance(f, (int, float)) and f < 0:
            neg_xs.append(m)
    shift = None
    if pos_xs and neg_xs:
        shift = statistics.mean(pos_xs) - statistics.mean(neg_xs)
    elif pos_xs:
        shift = statistics.mean(pos_xs)
    orig = None
    pos0 = []
    neg0 = []
    for c in clocks:
        f = c["features"].get(feature)
        v = c.get(f"{horizon}_MID_RETURN_BPS_mean")
        if v is None or not isinstance(f, (int, float)):
            continue
        if f > 0:
            pos0.append(float(v))
        elif f < 0:
            neg0.append(float(v))
    if pos0 and neg0:
        orig = statistics.mean(pos0) - statistics.mean(neg0)
    hold = orig is not None and shift is not None and ((orig > 0 and shift > 0) or (orig < 0 and shift < 0))
    return {"dropped": drop, "orig_mid_shift_bps": orig, "after_mid_shift_bps": shift, "hold": hold, "remain_n": len(remain)}


def placebo_clocks(causal: list[dict[str, Any]], nk: AsOf, tx: AsOf, day: str) -> tuple[list[dict[str, Any]], int]:
    leak = 0
    out = []
    for src in causal:
        hm = tuple(int(x) for x in src["clock"].split(":"))
        t = clock_dt(day, hm) + timedelta(seconds=PLACEBO_SHIFT_SEC)
        te = _epoch(t)
        feats: dict[str, Optional[float]] = {}
        for win in RET_WINDOWS_SEC:
            nk_r, lk = ret_asof(nk, te, win)
            tx_r, lk2 = ret_asof(tx, te, win)
            leak += lk + lk2
            feats[f"NK_RET_{win}S"] = nk_r
            feats[f"TOPIX_RET_{win}S"] = tx_r
        row = dict(src)
        row["features"] = dict(src["features"])
        row["features"].update(feats)
        out.append(row)
    nz = zscore([c["features"]["NK_RET_60S"] for c in out])
    tz = zscore([c["features"]["TOPIX_RET_60S"] for c in out])
    for c, a, b in zip(out, nz, tz):
        c["features"]["COMPOSITE"] = (a + b) if a is not None and b is not None else None
        nk60 = c["features"].get("NK_RET_60S")
        tx60 = c["features"].get("TOPIX_RET_60S")
        if nk60 is None or tx60 is None:
            c["features"]["AGREEMENT"] = None
        elif nk60 > 0 and tx60 > 0:
            c["features"]["AGREEMENT"] = 1.0
        elif nk60 < 0 and tx60 < 0:
            c["features"]["AGREEMENT"] = -1.0
        else:
            c["features"]["AGREEMENT"] = 0.0
    return out, leak


def preopen_test(*, native_root: Path, day: str, stocks: dict[str, AsOf], nk: AsOf, tx: AsOf, symbols: list[str]) -> dict[str, Any]:
    t0 = clock_dt(day, (8, 45))
    t1 = clock_dt(day, (9, 0))
    e0, e1 = _epoch(t0), _epoch(t1)
    nk0, _ = nk.at(e0)
    nk1, _ = nk.at(e1)
    tx0, _ = tx.at(e0)
    tx1, _ = tx.at(e1)
    nk_ret = ((nk1 - nk0) / nk0) if nk0 and nk1 and nk0 != 0 else None
    tx_ret = ((tx1 - tx0) / tx0) if tx0 and tx1 and tx0 != 0 else None
    if nk_ret is None or tx_ret is None:
        agr = None
    elif nk_ret > 0 and tx_ret > 0:
        agr = "both_up"
    elif nk_ret < 0 and tx_ret < 0:
        agr = "both_down"
    else:
        agr = "mixed"
    open_e = _epoch(t1)
    by_h: dict[str, dict[str, Optional[float]]] = {}
    for h in HORIZONS_SEC:
        hl = HORIZON_LABEL[h]
        mids, longs, shorts = [], [], []
        for sym in symbols:
            o = stock_outcomes_at(stocks[sym], open_e, h)
            if o["MID_RETURN_BPS"] is not None:
                mids.append(o["MID_RETURN_BPS"])
            if o["LONG_EXEC_MARKOUT_BPS"] is not None:
                longs.append(o["LONG_EXEC_MARKOUT_BPS"])
            if o["SHORT_EXEC_MARKOUT_BPS"] is not None:
                shorts.append(o["SHORT_EXEC_MARKOUT_BPS"])
        by_h[hl] = {
            "MID_mean": mean(mids),
            "LONG_mean": mean(longs),
            "SHORT_mean": mean(shorts),
            "n": len(mids),
        }
    return {
        "nk_ret_0845_0900": nk_ret,
        "topix_ret_0845_0900": tx_ret,
        "agreement": agr,
        "from_0900": by_h,
    }


def relation_sentence(best: dict[str, Any]) -> str:
    feat = best.get("feature")
    hz = best.get("horizon")
    shift = best.get("mid_shift_bps")
    long_i = best.get("long_impr_bps")
    short_i = best.get("short_impr_bps")
    return (
        f"On 20260911 only, clock-level averages of the 48-stock tape show that {feat} "
        f"available at T (futures received_at <= T) has a {hz} MID shift of "
        f"{None if shift is None else round(float(shift), 3)} bps between feature>0 and feature<0 clocks, "
        f"with LONG improvement {None if long_i is None else round(float(long_i), 3)} bps and SHORT improvement "
        f"{None if short_i is None else round(float(short_i), 3)} bps versus the unconditional clock population."
    )
