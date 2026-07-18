#!/usr/bin/env python3
"""Phase687W47 research helper: attach simple pre-entry features from push_jsonl.

Reads entry_panel.parquet, writes entry_features.parquet under _w47_tmp/.
Max 4 workers. Does not modify Runtime YAML / PBv2.
"""

from __future__ import annotations

import json
import math
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

NATIVE = Path(__file__).resolve().parents[1]
PUSH_ROOT = NATIVE / "data" / "push_jsonl"
TMP = NATIVE / "results" / "research" / "pre_entry_market_state" / "_w47_tmp"
PANEL_PQ = TMP / "entry_panel.parquet"
OUT_PQ = TMP / "entry_features.parquet"
JST = ZoneInfo("Asia/Tokyo")
MAX_WORKERS = 4


def _num(x: Any) -> Optional[float]:
    if x is None or x == "":
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return v


def _parse_ts(val: Any) -> Optional[datetime]:
    if val is None or val == "":
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=JST)
    try:
        dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JST)
    return dt.astimezone(JST)


def day_dash(day: str) -> str:
    d = str(day).replace("-", "")
    if len(d) == 8:
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    return str(day)


def _imbalance(payload: dict[str, Any]) -> Optional[float]:
    bid = 0.0
    ask = 0.0
    for i in range(1, 6):
        b = payload.get(f"Buy{i}")
        s = payload.get(f"Sell{i}")
        if isinstance(b, dict) and b.get("Qty") is not None:
            bid += float(b["Qty"] or 0)
        if isinstance(s, dict) and s.get("Qty") is not None:
            ask += float(s["Qty"] or 0)
    tot = bid + ask
    if tot <= 0:
        return None
    return (bid - ask) / tot


def _spread_bps(payload: dict[str, Any], px: Optional[float]) -> Optional[float]:
    ask = _num(payload.get("AskPrice"))
    bid = _num(payload.get("BidPrice"))
    if ask is None or bid is None or px is None or px <= 0:
        return None
    if ask <= 0 or bid <= 0:
        return None
    return (ask - bid) / px * 10000.0


def load_symbol_series(day: str, symbol: str) -> dict[str, np.ndarray]:
    path = PUSH_ROOT / day_dash(day) / f"{symbol}.jsonl"
    empty = {
        "ts": np.array([], dtype=np.float64),
        "price": np.array([], dtype=np.float64),
        "imb": np.array([], dtype=np.float64),
        "spread": np.array([], dtype=np.float64),
    }
    if not path.is_file():
        return empty
    ts_l: list[float] = []
    px_l: list[float] = []
    imb_l: list[float] = []
    spr_l: list[float] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            recv = _parse_ts(o.get("recorded_at"))
            if recv is None:
                continue
            payload = o.get("payload") or {}
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    continue
            if not isinstance(payload, dict):
                continue
            px = _num(payload.get("CurrentPrice"))
            if px is None or px <= 0:
                continue
            ts_l.append(recv.timestamp())
            px_l.append(px)
            imb = _imbalance(payload)
            imb_l.append(imb if imb is not None else np.nan)
            spr = _spread_bps(payload, px)
            spr_l.append(spr if spr is not None else np.nan)
    if not ts_l:
        return empty
    order = np.argsort(np.asarray(ts_l))
    return {
        "ts": np.asarray(ts_l, dtype=np.float64)[order],
        "price": np.asarray(px_l, dtype=np.float64)[order],
        "imb": np.asarray(imb_l, dtype=np.float64)[order],
        "spread": np.asarray(spr_l, dtype=np.float64)[order],
    }


def _price_near(series: dict[str, np.ndarray], target: float, tol: float = 30.0) -> Optional[float]:
    ts = series["ts"]
    if len(ts) == 0:
        return None
    i = int(np.searchsorted(ts, target))
    best = None
    best_dt = 1e18
    for j in (i - 1, i, i + 1):
        if j < 0 or j >= len(ts):
            continue
        px = series["price"][j]
        if not math.isfinite(px) or px <= 0:
            continue
        dt = abs(ts[j] - target)
        if dt <= tol and dt < best_dt:
            best_dt = dt
            best = float(px)
    if best is None:
        lo = int(np.searchsorted(ts, target - tol))
        hi = int(np.searchsorted(ts, target + tol, side="right"))
        for j in range(lo, hi):
            px = series["price"][j]
            if not (math.isfinite(px) and px > 0):
                continue
            dt = abs(ts[j] - target)
            if dt < best_dt:
                best_dt = dt
                best = float(px)
    return best


def _idx_at(series: dict[str, np.ndarray], t0: float) -> int:
    ts = series["ts"]
    if len(ts) == 0:
        return -1
    return int(np.searchsorted(ts, t0, side="right") - 1)


def _slope(ts: np.ndarray, px: np.ndarray) -> Optional[float]:
    if len(px) < 3:
        return None
    m = np.isfinite(px) & (px > 0) & np.isfinite(ts)
    if m.sum() < 3:
        return None
    x = ts[m] - ts[m][0]
    y = px[m]
    if x[-1] <= 0:
        return None
    y0 = y[0]
    if y0 <= 0:
        return None
    yr = (y / y0 - 1.0) * 100.0
    return float(np.polyfit(x, yr, 1)[0])


def _seconds_since_last_new_high(series: dict[str, np.ndarray], t0: float, lookback: float = 300.0) -> Optional[float]:
    ts = series["ts"]
    px = series["price"]
    if len(ts) == 0:
        return None
    hi = int(np.searchsorted(ts, t0, side="right"))
    lo = int(np.searchsorted(ts, t0 - lookback, side="left"))
    if hi <= lo:
        return None
    p = px[lo:hi]
    t = ts[lo:hi]
    m = np.isfinite(p) & (p > 0)
    if m.sum() < 2:
        return None
    p = p[m]
    t = t[m]
    cur_hi = -1.0
    last_hi_t = None
    for pi, ti in zip(p, t):
        if pi > cur_hi * 1.00001 or cur_hi < 0:
            cur_hi = float(pi)
            last_hi_t = float(ti)
    if last_hi_t is None:
        return None
    return float(t0 - last_hi_t)


def features_at(series: dict[str, np.ndarray], t0: float) -> dict[str, Any]:
    out: dict[str, Any] = {
        "ret_30": None,
        "ret_60": None,
        "ret_120": None,
        "ret_300": None,
        "slope_60": None,
        "slope_120": None,
        "spread_bps": None,
        "imbalance": None,
        "seconds_since": None,
        "push_points": int(len(series["ts"])),
        "feature_ok": False,
    }
    idx = _idx_at(series, t0)
    if idx < 0:
        return out
    px0 = float(series["price"][idx])
    if not (math.isfinite(px0) and px0 > 0):
        return out

    def ret(sec: float) -> Optional[float]:
        p = _price_near(series, t0 - sec, tol=30.0)
        if p is None or p <= 0:
            return None
        return (px0 / p - 1.0) * 100.0

    out["ret_30"] = ret(30)
    out["ret_60"] = ret(60)
    out["ret_120"] = ret(120)
    out["ret_300"] = ret(300)

    for sec, name in ((60, "slope_60"), (120, "slope_120")):
        hi = idx + 1
        lo = int(np.searchsorted(series["ts"], t0 - sec, side="left"))
        out[name] = _slope(series["ts"][lo:hi], series["price"][lo:hi])

    if math.isfinite(series["spread"][idx]):
        out["spread_bps"] = float(series["spread"][idx])
    if math.isfinite(series["imb"][idx]):
        out["imbalance"] = float(series["imb"][idx])
    out["seconds_since"] = _seconds_since_last_new_high(series, t0)
    out["feature_ok"] = any(
        out[k] is not None for k in ("ret_30", "ret_60", "ret_120", "ret_300", "slope_60", "slope_120")
    )
    return out


def _process_symbol_group(args: tuple[str, str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    day, symbol, trades = args
    series = load_symbol_series(day, symbol)
    rows: list[dict[str, Any]] = []
    for t in trades:
        et = _parse_ts(t.get("entry_time"))
        feats = features_at(series, et.timestamp()) if et is not None else features_at(series, -1.0)
        rows.append(
            {
                "trade_id": t.get("trade_id"),
                "trading_date": day,
                "session": t.get("session"),
                "session_id": t.get("session_id"),
                "symbol": symbol,
                "entry_time": t.get("entry_time"),
                "label_primary": t.get("label_primary"),
                "label_winner_a": t.get("label_winner_a"),
                "label_winner_b": t.get("label_winner_b"),
                "label_stop": t.get("label_stop"),
                "label_no_progress": t.get("label_no_progress"),
                "pnl_pct": t.get("pnl_pct"),
                **feats,
            }
        )
    return rows


def main() -> int:
    if not PANEL_PQ.is_file():
        raise SystemExit(f"missing panel: {PANEL_PQ} (run _w47_entry_panel_build.py first)")
    panel = pd.read_parquet(PANEL_PQ)
    if panel.empty:
        TMP.mkdir(parents=True, exist_ok=True)
        pd.DataFrame().to_parquet(OUT_PQ, index=False)
        print(json.dumps({"n_rows": 0, "out_path": str(OUT_PQ), "feature_ok_rate": None}, ensure_ascii=False, indent=2))
        return 0

    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for rec in panel.to_dict(orient="records"):
        key = (str(rec.get("trading_date")), str(rec.get("symbol")))
        groups.setdefault(key, []).append(rec)

    tasks = [(d, s, rows) for (d, s), rows in sorted(groups.items())]
    out_rows: list[dict[str, Any]] = []
    workers = min(MAX_WORKERS, max(1, len(tasks)))
    if workers == 1 or len(tasks) <= 1:
        for t in tasks:
            out_rows.extend(_process_symbol_group(t))
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futs = [pool.submit(_process_symbol_group, t) for t in tasks]
            for fut in as_completed(futs):
                out_rows.extend(fut.result())

    df = pd.DataFrame(out_rows)
    if not df.empty:
        df = df.sort_values(["trading_date", "entry_time", "symbol"]).reset_index(drop=True)
    TMP.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT_PQ, index=False)
    n = len(df)
    ok = float(df["feature_ok"].mean()) if n and "feature_ok" in df.columns else None
    print(
        json.dumps(
            {
                "n_rows": int(n),
                "n_symbol_day": int(len(tasks)),
                "feature_ok_rate": ok,
                "out_path": str(OUT_PQ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
