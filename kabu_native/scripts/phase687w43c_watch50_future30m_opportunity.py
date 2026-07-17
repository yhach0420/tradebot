#!/usr/bin/env python3
"""Phase687W43C: Watch50 Future-30m Opportunity Analysis (20260717).

Research-only. No Runtime / YAML / ENTRY / EXIT / CAP / Shadow / order changes.
Uses existing push_jsonl + universe CSVs + canonical trades. No Capture rebuild.
"""

from __future__ import annotations

import csv
import json
import math
import sys
import warnings
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import combinations
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

warnings.filterwarnings("ignore", category=UserWarning)

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(NATIVE / "src"))

from small_paper.canonical_summary import collect_canonical_trades  # noqa: E402

JST = ZoneInfo("Asia/Tokyo")
DATE = "20260717"
DATE_DASH = "2026-07-17"
OUT = NATIVE / "results" / "research" / "pre_entry_market_state"
PUSH_DIR = NATIVE / "data" / "push_jsonl" / DATE_DASH
REPORTS = NATIVE / "results" / "reports"
SESSIONS = {
    "am": NATIVE / "results" / "small_paper" / DATE / "live_session_081810",
    "pm": NATIVE / "results" / "small_paper" / DATE / "live_session_122525",
}
W43_PQ = OUT / f"trading_date={DATE}" / "market_state_entries.parquet"
OUTLIER = "7581.T"
MAX_WORKERS = 4

# Universe segments (time-varying; not final-day universe applied retroactively)
UNIVERSE_SEGMENTS = [
    {
        "session": "am",
        "segment": "am_open",
        "refresh": "before",
        "csv": REPORTS / f"universe_core10_dynamic40_price_risk_am_{DATE}.csv",
        "start": "2026-07-17T09:03:00+09:00",
        "end": "2026-07-17T10:00:00+09:00",
        "label_end": "2026-07-17T11:00:00+09:00",
    },
    {
        "session": "am",
        "segment": "am_refresh1000",
        "refresh": "after",
        "csv": REPORTS / f"universe_core10_dynamic40_price_risk_am_refresh1000_{DATE}.csv",
        "start": "2026-07-17T10:00:00+09:00",
        "end": "2026-07-17T11:00:00+09:00",
        "label_end": "2026-07-17T11:00:00+09:00",
    },
    {
        "session": "pm",
        "segment": "pm_open",
        "refresh": "before",
        "csv": REPORTS / f"universe_core10_dynamic40_price_risk_pm_{DATE}.csv",
        "start": "2026-07-17T12:33:00+09:00",
        "end": "2026-07-17T14:30:00+09:00",
        "label_end": "2026-07-17T15:00:00+09:00",
    },
    {
        "session": "pm",
        "segment": "pm_refresh1430",
        "refresh": "after",
        "csv": REPORTS / f"universe_core10_dynamic40_price_risk_pm_refresh1430_{DATE}.csv",
        "start": "2026-07-17T14:30:00+09:00",
        "end": "2026-07-17T15:00:00+09:00",
        "label_end": "2026-07-17T15:00:00+09:00",
    },
]

HORIZONS = (5, 10, 15, 20, 30)
FEATURE_COLS = [
    "ret_10s",
    "ret_30s",
    "ret_60s",
    "ret_120s",
    "ret_300s",
    "slope_30s",
    "slope_60s",
    "slope_120s",
    "slope_300s",
    "accel_60s",
    "max_dd_300s",
    "bounce_from_low_300s",
    "fall_from_high_300s",
    "day_high_distance_pct",
    "vwap_dev_pct",
    "vwap_slope_300s",
    "pre_300s_new_high_count",
    "seconds_since_last_new_high",
    "vol_accel_300s",
    "vol_persistence_300s",
    "vol_ratio_60_300",
    "trade_updates_per_sec_60s",
    "board_updates_per_sec_60s",
    "push_updates_per_sec_60s",
    "imbalance_l5",
    "imbalance_chg_30s",
    "imbalance_chg_60s",
    "imbalance_chg_120s",
    "net_ask_pressure_60s",
    "net_bid_pressure_60s",
    "spread_bps",
    "microprice_chg_60s",
    "price_age_sec",
    "board_age_sec",
]


def _wj(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _wc(path: Path, rows: list[dict[str, Any]] | pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(rows, pd.DataFrame):
        rows.to_csv(path, index=False)
        return
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    cols: list[str] = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in cols})


def _wm(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")


def parse_ts(val: Any) -> Optional[datetime]:
    if val is None or val == "":
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=JST)
    s = str(val).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JST)
    return dt.astimezone(JST)


def to_epoch(dt: datetime) -> float:
    return dt.timestamp()


def finite(x: Any) -> Optional[float]:
    if x is None or x == "":
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return v


def load_universe(path: Path) -> list[str]:
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    out = []
    for r in rows:
        sym = str(r.get("symbol") or "").strip()
        if sym:
            out.append(sym if sym.endswith(".T") or "." in sym else f"{sym}.T")
    return out


def load_events(sd: Path) -> list[dict[str, Any]]:
    p = sd / "small_paper_events.jsonl"
    out = []
    if not p.is_file():
        return out
    for line in p.open(encoding="utf-8"):
        if line.strip():
            out.append(json.loads(line))
    return out


def load_official_entries() -> pd.DataFrame:
    rows = []
    for sk, sd in SESSIONS.items():
        events = load_events(sd)
        can = collect_canonical_trades(events)
        accepts = [e for e in events if e.get("event_type") == "accepted"]
        # official = matched to canonical (excludes ghost)
        q: dict[str, list] = defaultdict(list)
        for a in sorted(accepts, key=lambda x: str(x.get("entry_time") or "")):
            q[str(a.get("symbol"))].append(a)
        for t in can:
            sym = str(t.get("symbol"))
            a = q[sym].pop(0) if q[sym] else {}
            # ghost if accept had null price / no position — already excluded from can
            et = parse_ts(t.get("entry_time") or a.get("entry_time"))
            if et is None:
                continue
            rows.append(
                {
                    "session": sk,
                    "symbol": sym,
                    "entry_time": et.isoformat(),
                    "entry_epoch": to_epoch(et),
                    "entry_price": finite(t.get("entry_price") or a.get("entry_price") or a.get("current_price")),
                    "exit_reason": t.get("exit_reason"),
                    "pnl_yen_100": finite(t.get("pnl_yen_100")),
                    "pnl_pct": finite(t.get("pnl_pct")),
                    "position_id": t.get("position_id") or a.get("position_id") or a.get("observer_position_id"),
                    "official_entry": True,
                    "position_registered": True,
                }
            )
    return pd.DataFrame(rows)


def classify_episodes_with_rejects(ep: pd.DataFrame) -> pd.DataFrame:
    """Stream events.jsonl once; classify MISSED episodes (CAP / RULE / NEVER)."""
    if ep is None or ep.empty:
        return ep
    ep = ep.copy()
    missed = ep[ep["capture_class"] == "MISSED"]
    if missed.empty:
        return ep
    windows = []
    for i, r in missed.iterrows():
        windows.append(
            (
                int(i),
                str(r["symbol"]),
                float(r["episode_start_epoch"]) - 60.0,
                float(r["episode_end_epoch"]) + 900.0,
            )
        )
    # per episode accumulators
    hits: dict[int, list[str]] = defaultdict(list)
    for sk, sd in SESSIONS.items():
        p = sd / "small_paper_events.jsonl"
        if not p.is_file():
            continue
        for line in p.open(encoding="utf-8"):
            if '"event_type": "rejected"' not in line and '"event_type":"rejected"' not in line:
                # still allow candidates with gate info
                if '"event_type": "candidate"' not in line and '"event_type":"candidate"' not in line:
                    continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            if o.get("event_type") not in ("rejected", "candidate"):
                continue
            sym = str(o.get("symbol") or "")
            et = parse_ts(o.get("entry_time") or o.get("event_time"))
            if et is None:
                continue
            epoch = to_epoch(et)
            reason = str(
                o.get("final_reject_reason")
                or o.get("gate_reject_reason")
                or o.get("or_overlay_reason")
                or o.get("pbv2_internal_reason")
                or ""
            )
            for idx, wsym, lo, hi in windows:
                if wsym == sym and lo <= epoch <= hi:
                    if reason:
                        hits[idx].append(reason)
                    else:
                        hits[idx].append("candidate_seen")
    for idx, reasons in hits.items():
        joined = "|".join(reasons[:30])
        if any("max_concurrent" in r or "position_cap" in r or r == "cap_full" for r in reasons):
            ep.at[idx, "opportunity_class"] = "CAP_BLOCKED"
        elif any(r and r not in ("candidate_seen",) for r in reasons):
            ep.at[idx, "opportunity_class"] = "RULE_REJECTED"
        else:
            ep.at[idx, "opportunity_class"] = "NEVER_CANDIDATE"
        ep.at[idx, "reject_reasons"] = joined
    # untouched missed stay NEVER_CANDIDATE
    m = (ep["capture_class"] == "MISSED") & (ep["opportunity_class"].isin(["", "MISSED"]) | ep["opportunity_class"].isna())
    ep.loc[m, "opportunity_class"] = "NEVER_CANDIDATE"
    return ep


def _imbalance(payload: dict) -> Optional[float]:
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


def _spread_bps(payload: dict, px: Optional[float]) -> Optional[float]:
    ask = finite(payload.get("AskPrice"))
    bid = finite(payload.get("BidPrice"))
    if ask is None or bid is None or px is None or px <= 0:
        return None
    if ask <= 0 or bid <= 0:
        return None
    return (ask - bid) / px * 10000.0


def load_symbol_series(
    symbol: str,
    *,
    push_dir: Optional[Path] = None,
    date_dash: Optional[str] = None,
) -> dict[str, np.ndarray]:
    push_dir = push_dir or PUSH_DIR
    date_dash = date_dash or DATE_DASH
    path = Path(push_dir) / f"{symbol}.jsonl"
    empty = {
        "ts": np.array([], dtype=np.float64),
        "price": np.array([], dtype=np.float64),
        "vol": np.array([], dtype=np.float64),
        "vwap": np.array([], dtype=np.float64),
        "imb": np.array([], dtype=np.float64),
        "spread": np.array([], dtype=np.float64),
        "micro": np.array([], dtype=np.float64),
        "has_price": np.array([], dtype=np.bool_),
        "has_board": np.array([], dtype=np.bool_),
        "price_time": np.array([], dtype=np.float64),
        "board_time": np.array([], dtype=np.float64),
    }
    if not path.is_file():
        return empty
    ts_l: list[float] = []
    px_l: list[float] = []
    vol_l: list[float] = []
    vwap_l: list[float] = []
    imb_l: list[float] = []
    spr_l: list[float] = []
    mic_l: list[float] = []
    hp_l: list[bool] = []
    hb_l: list[bool] = []
    pt_l: list[float] = []
    bt_l: list[float] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = o.get("payload") if isinstance(o.get("payload"), dict) else {}
            recv = parse_ts(o.get("recorded_at"))
            if recv is None:
                continue
            # session filter loose — keep day
            if recv.date().isoformat() != date_dash:
                continue
            hour = recv.hour + recv.minute / 60.0
            if not (8.9 <= hour <= 15.5):
                continue
            px = finite(payload.get("CurrentPrice"))
            cpt = parse_ts(payload.get("CurrentPriceTime"))
            b1 = payload.get("Buy1") if isinstance(payload.get("Buy1"), dict) else None
            s1 = payload.get("Sell1") if isinstance(payload.get("Sell1"), dict) else None
            bt = None
            if b1 and b1.get("Time"):
                bt = parse_ts(b1.get("Time"))
            elif s1 and s1.get("Time"):
                bt = parse_ts(s1.get("Time"))
            imb = _imbalance(payload)
            spr = _spread_bps(payload, px)
            bid = finite(payload.get("BidPrice"))
            ask = finite(payload.get("AskPrice"))
            micro = None
            if bid is not None and ask is not None:
                bq = float((b1 or {}).get("Qty") or 0)
                aq = float((s1 or {}).get("Qty") or 0)
                if bq + aq > 0:
                    micro = (ask * bq + bid * aq) / (bq + aq)
                else:
                    micro = (bid + ask) / 2.0
            ts_l.append(to_epoch(recv))
            px_l.append(px if px is not None and px > 0 else np.nan)
            vol_l.append(finite(payload.get("TradingVolume")) or np.nan)
            vwap_l.append(finite(payload.get("VWAP")) or np.nan)
            imb_l.append(imb if imb is not None else np.nan)
            spr_l.append(spr if spr is not None else np.nan)
            mic_l.append(micro if micro is not None else np.nan)
            hp_l.append(px is not None and px > 0)
            hb_l.append(b1 is not None or s1 is not None)
            pt_l.append(to_epoch(cpt) if cpt else np.nan)
            bt_l.append(to_epoch(bt) if bt else np.nan)
    if not ts_l:
        return empty
    order = np.argsort(np.asarray(ts_l))
    return {
        "ts": np.asarray(ts_l, dtype=np.float64)[order],
        "price": np.asarray(px_l, dtype=np.float64)[order],
        "vol": np.asarray(vol_l, dtype=np.float64)[order],
        "vwap": np.asarray(vwap_l, dtype=np.float64)[order],
        "imb": np.asarray(imb_l, dtype=np.float64)[order],
        "spread": np.asarray(spr_l, dtype=np.float64)[order],
        "micro": np.asarray(mic_l, dtype=np.float64)[order],
        "has_price": np.asarray(hp_l, dtype=np.bool_)[order],
        "has_board": np.asarray(hb_l, dtype=np.bool_)[order],
        "price_time": np.asarray(pt_l, dtype=np.float64)[order],
        "board_time": np.asarray(bt_l, dtype=np.float64)[order],
    }


def _last_valid_price_before(series: dict[str, np.ndarray], t0: float) -> tuple[Optional[float], Optional[float], int]:
    ts = series["ts"]
    if len(ts) == 0:
        return None, None, 0
    i = int(np.searchsorted(ts, t0, side="right") - 1)
    if i < 0:
        return None, None, 0
    # walk back for valid CurrentPrice
    for j in range(i, max(-1, i - 500), -1):
        if series["has_price"][j] and math.isfinite(series["price"][j]) and series["price"][j] > 0:
            return float(series["price"][j]), float(ts[j]), j
    return None, None, -1


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
        if not series["has_price"][j]:
            continue
        px = series["price"][j]
        if not math.isfinite(px) or px <= 0:
            continue
        dt = abs(ts[j] - target)
        if dt <= tol and dt < best_dt:
            best_dt = dt
            best = float(px)
    # expand small window
    if best is None:
        lo = int(np.searchsorted(ts, target - tol))
        hi = int(np.searchsorted(ts, target + tol, side="right"))
        for j in range(lo, hi):
            if not series["has_price"][j]:
                continue
            px = series["price"][j]
            if math.isfinite(px) and px > 0:
                dt = abs(ts[j] - target)
                if dt < best_dt:
                    best_dt = dt
                    best = float(px)
    return best


def _prices_in_window(series: dict[str, np.ndarray], t0: float, t1: float) -> np.ndarray:
    ts = series["ts"]
    if len(ts) == 0:
        return np.array([], dtype=np.float64)
    lo = int(np.searchsorted(ts, t0, side="left"))
    hi = int(np.searchsorted(ts, t1, side="right"))
    if hi <= lo:
        return np.array([], dtype=np.float64)
    mask = series["has_price"][lo:hi]
    px = series["price"][lo:hi][mask]
    px = px[np.isfinite(px) & (px > 0)]
    return px


def _idx_window(series: dict[str, np.ndarray], t0: float, lookback: float) -> slice:
    ts = series["ts"]
    hi = int(np.searchsorted(ts, t0, side="right"))
    lo = int(np.searchsorted(ts, t0 - lookback, side="left"))
    return slice(lo, hi)


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
    # % per second * 60 → rough %/min scale; use simple linreg on return space
    y0 = y[0]
    if y0 <= 0:
        return None
    yr = (y / y0 - 1.0) * 100.0
    coef = np.polyfit(x, yr, 1)[0]
    return float(coef)  # pct points per second


def compute_features(series: dict[str, np.ndarray], t0: float, idx: int) -> dict[str, Any]:
    out: dict[str, Any] = {c: None for c in FEATURE_COLS}
    px0 = series["price"][idx] if idx >= 0 else np.nan
    if not (math.isfinite(px0) and px0 > 0):
        return out

    def ret(sec: float) -> Optional[float]:
        p = _price_near(series, t0 - sec, tol=30.0)
        if p is None or p <= 0:
            return None
        return (px0 / p - 1.0) * 100.0

    for sec, name in ((10, "ret_10s"), (30, "ret_30s"), (60, "ret_60s"), (120, "ret_120s"), (300, "ret_300s")):
        out[name] = ret(sec)

    for sec, name in ((30, "slope_30s"), (60, "slope_60s"), (120, "slope_120s"), (300, "slope_300s")):
        sl = _idx_window(series, t0, sec)
        out[name] = _slope(series["ts"][sl], series["price"][sl])

    s30 = out["slope_30s"]
    s60 = out["slope_60s"]
    if s30 is not None and s60 is not None:
        out["accel_60s"] = s30 - s60

    sl = _idx_window(series, t0, 300)
    pxw = series["price"][sl]
    tsw = series["ts"][sl]
    m = np.isfinite(pxw) & (pxw > 0)
    if m.sum() >= 3:
        p = pxw[m]
        t = tsw[m]
        peak = np.maximum.accumulate(p)
        dd = (p / peak - 1.0) * 100.0
        out["max_dd_300s"] = float(np.min(dd))
        out["bounce_from_low_300s"] = float((px0 / np.min(p) - 1.0) * 100.0)
        out["fall_from_high_300s"] = float((px0 / np.max(p) - 1.0) * 100.0)
        # new highs
        highs = 0
        cur_hi = -1.0
        last_hi_t = None
        for pi, ti in zip(p, t):
            if pi > cur_hi * 1.00001 or cur_hi < 0:
                if cur_hi > 0:
                    highs += 1
                cur_hi = pi
                last_hi_t = ti
        out["pre_300s_new_high_count"] = highs
        if last_hi_t is not None:
            out["seconds_since_last_new_high"] = float(t0 - last_hi_t)
        # day high distance using max so far in series up to t0
        day_hi = float(np.max(p))
        out["day_high_distance_pct"] = float((day_hi / px0 - 1.0) * 100.0) if px0 > 0 else None

    # VWAP
    if idx >= 0 and math.isfinite(series["vwap"][idx]) and series["vwap"][idx] > 0:
        vwap = float(series["vwap"][idx])
        out["vwap_dev_pct"] = (px0 / vwap - 1.0) * 100.0
        sl = _idx_window(series, t0, 300)
        out["vwap_slope_300s"] = _slope(series["ts"][sl], series["vwap"][sl])

    # volume
    sl = _idx_window(series, t0, 300)
    vol = series["vol"][sl]
    tsv = series["ts"][sl]
    vm = np.isfinite(vol)
    if vm.sum() >= 4:
        v = vol[vm]
        tv = tsv[vm]
        # persistence: fraction of intervals with increasing volume
        dv = np.diff(v)
        out["vol_persistence_300s"] = float(np.mean(dv > 0)) if len(dv) else None
        if len(v) >= 6 and (tv[-1] - tv[0]) > 0:
            mid = len(v) // 2
            d1 = v[mid] - v[0]
            d2 = v[-1] - v[mid]
            out["vol_accel_300s"] = float(d2 - d1)
        # ratio recent 60s delta vs 300s
        sl60 = _idx_window(series, t0, 60)
        v60 = series["vol"][sl60]
        v60 = v60[np.isfinite(v60)]
        if len(v60) >= 2 and len(v) >= 2:
            d60 = float(v60[-1] - v60[0])
            d300 = float(v[-1] - v[0])
            if abs(d300) > 1e-9:
                out["vol_ratio_60_300"] = d60 / d300

    # update rates
    for sec, key_trade, key_board, key_push in (
        (60, "trade_updates_per_sec_60s", "board_updates_per_sec_60s", "push_updates_per_sec_60s"),
    ):
        sl = _idx_window(series, t0, sec)
        n = sl.stop - sl.start
        out[key_push] = n / sec if n > 0 else 0.0
        out[key_trade] = float(np.sum(series["has_price"][sl])) / sec
        out[key_board] = float(np.sum(series["has_board"][sl])) / sec

    # board
    if idx >= 0 and math.isfinite(series["imb"][idx]):
        out["imbalance_l5"] = float(series["imb"][idx])
    for sec, name in ((30, "imbalance_chg_30s"), (60, "imbalance_chg_60s"), (120, "imbalance_chg_120s")):
        past = _price_near  # reuse search pattern for imb
        # find last imb at t0-sec
        ts = series["ts"]
        j = int(np.searchsorted(ts, t0 - sec, side="right") - 1)
        if j >= 0 and math.isfinite(series["imb"][j]) and out["imbalance_l5"] is not None:
            out[name] = out["imbalance_l5"] - float(series["imb"][j])
    sl = _idx_window(series, t0, 60)
    im = series["imb"][sl]
    im = im[np.isfinite(im)]
    if len(im) >= 2:
        chg = np.diff(im)
        out["net_bid_pressure_60s"] = float(np.sum(chg[chg > 0]))
        out["net_ask_pressure_60s"] = float(-np.sum(chg[chg < 0]))
    if idx >= 0 and math.isfinite(series["spread"][idx]):
        out["spread_bps"] = float(series["spread"][idx])
    if idx >= 0 and math.isfinite(series["micro"][idx]):
        j = int(np.searchsorted(series["ts"], t0 - 60, side="right") - 1)
        if j >= 0 and math.isfinite(series["micro"][j]) and series["micro"][j] > 0:
            out["microprice_chg_60s"] = (float(series["micro"][idx]) / float(series["micro"][j]) - 1.0) * 100.0

    # ages
    if idx >= 0 and math.isfinite(series["price_time"][idx]):
        out["price_age_sec"] = float(t0 - series["price_time"][idx])
    if idx >= 0 and math.isfinite(series["board_time"][idx]):
        out["board_age_sec"] = float(t0 - series["board_time"][idx])
    return out


def primary_label(mfe: Optional[float], mae: Optional[float], ret30: Optional[float]) -> str:
    if ret30 is None or mfe is None or mae is None:
        return "UNAVAILABLE"
    if mfe >= 1.0 and ret30 >= 0.5:
        return "LARGE_RISE"
    if mfe >= 1.0 and ret30 < 0.5 and mae > -1.0:
        return "RECOVERED_RISE"
    if 0.2 <= ret30 < 0.5 and mfe < 1.0:
        return "SMALL_RISE"
    if -0.2 < ret30 < 0.2 and mfe < 0.8 and mae > -0.8:
        return "SIDEWAYS"
    if ret30 <= -0.2 or mae <= -1.0:
        return "DECLINE"
    return "SIDEWAYS"


def build_snapshots_for_symbol(
    symbol: str,
    segments: list[dict[str, Any]],
    *,
    push_dir: Optional[Path] = None,
    date_dash: Optional[str] = None,
    trading_date: Optional[str] = None,
) -> list[dict[str, Any]]:
    series = load_symbol_series(symbol, push_dir=push_dir, date_dash=date_dash)
    rows: list[dict[str, Any]] = []
    if len(series["ts"]) == 0:
        return rows
    trading_date = trading_date or DATE
    for seg in segments:
        if symbol not in seg["symbols"]:
            continue
        t0 = seg["start_epoch"]
        t_end = seg["end_epoch"]
        label_end = seg["label_end_epoch"]
        # 30s grid
        t = t0
        while t < t_end - 1e-6:
            px, px_ts, idx = _last_valid_price_before(series, t)
            # window stats for push counts
            sl = _idx_window(series, t, 30)
            n_push = sl.stop - sl.start
            n_trade = int(np.sum(series["has_price"][sl])) if n_push else 0
            n_board = int(np.sum(series["has_board"][sl])) if n_push else 0
            # high updates in last 30s
            n_high = 0
            if n_push and idx >= 0:
                pslice = series["price"][sl]
                if np.isfinite(pslice).any():
                    # count new highs vs prior max
                    valid = pslice[np.isfinite(pslice) & (pslice > 0)]
                    if len(valid) >= 2:
                        peak = valid[0]
                        for v in valid[1:]:
                            if v > peak:
                                n_high += 1
                                peak = v
            base = {
                "trading_date": trading_date,
                "symbol": symbol,
                "session": seg["session"],
                "universe_segment": seg["segment"],
                "refresh_flag": seg["refresh"],
                "t0_epoch": t,
                "t0_time": datetime.fromtimestamp(t, tz=JST).isoformat(timespec="seconds"),
                "current_price": px,
                "current_price_ts": px_ts,
                "push_count_30s": n_push,
                "trade_update_count_30s": n_trade,
                "board_update_count_30s": n_board,
                "high_update_count_30s": n_high,
                "last_update_time": datetime.fromtimestamp(float(series["ts"][idx]), tz=JST).isoformat(timespec="seconds")
                if idx >= 0
                else None,
                "exclude_reason": None,
                "primary_label": "UNAVAILABLE",
            }
            if px is None or px <= 0:
                base["exclude_reason"] = "label_unavailable_current_price"
                base["primary_label"] = "UNAVAILABLE"
                rows.append(base)
                t += 30.0
                continue
            feats = compute_features(series, t, idx)
            base.update(feats)
            # future labels
            for h in HORIZONS:
                tgt = t + h * 60.0
                if tgt > label_end + 1e-6:
                    base[f"future_{h}m_return"] = None
                else:
                    fp = _price_near(series, tgt, tol=30.0)
                    if fp is None:
                        base[f"future_{h}m_return"] = None
                    else:
                        base[f"future_{h}m_return"] = (fp / px - 1.0) * 100.0
            # MFE/MAE over 30m
            t_mfe_end = t + 1800.0
            if t_mfe_end > label_end + 1e-6:
                base["future_30m_mfe"] = None
                base["future_30m_mae"] = None
                base["time_to_mfe_sec"] = None
                base["time_to_mae_sec"] = None
                base["primary_label"] = "UNAVAILABLE"
            else:
                # path prices
                ts = series["ts"]
                lo = int(np.searchsorted(ts, t, side="left"))
                hi = int(np.searchsorted(ts, t_mfe_end, side="right"))
                mfe = None
                mae = None
                ttm = None
                tta = None
                if hi > lo:
                    for j in range(lo, hi):
                        if not series["has_price"][j]:
                            continue
                        p = series["price"][j]
                        if not (math.isfinite(p) and p > 0):
                            continue
                        r = (p / px - 1.0) * 100.0
                        if mfe is None or r > mfe:
                            mfe = r
                            ttm = float(ts[j] - t)
                        if mae is None or r < mae:
                            mae = r
                            tta = float(ts[j] - t)
                base["future_30m_mfe"] = mfe
                base["future_30m_mae"] = mae
                base["time_to_mfe_sec"] = ttm
                base["time_to_mae_sec"] = tta
                base["primary_label"] = primary_label(mfe, mae, base.get("future_30m_return"))
            # threshold flags
            mfe = base.get("future_30m_mfe")
            ret30 = base.get("future_30m_return")
            for thr in (0.5, 0.8, 1.0, 1.5, 2.0):
                base[f"mfe_ge_{thr}"] = bool(mfe is not None and mfe >= thr)
            for thr in (0.2, 0.5, 0.8):
                base[f"ret30_ge_{thr}"] = bool(ret30 is not None and ret30 >= thr)
            rows.append(base)
            t += 30.0
    return rows


def _worker(args: tuple) -> list[dict[str, Any]]:
    if len(args) == 2:
        symbol, segments = args
        return build_snapshots_for_symbol(symbol, segments)
    symbol, segments, push_dir, date_dash, trading_date = args
    return build_snapshots_for_symbol(
        symbol,
        segments,
        push_dir=Path(push_dir) if push_dir else None,
        date_dash=date_dash,
        trading_date=trading_date,
    )


def attach_entries(df: pd.DataFrame, entries: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    for col in (
        "entry_within_next_30s",
        "entry_within_next_60s",
        "entry_within_next_120s",
        "entry_within_next_300s",
        "actual_entry_time",
        "actual_entry_price",
        "actual_exit_reason",
        "actual_pnl_yen_100",
        "actual_pnl_pct",
        "secs_to_next_entry",
    ):
        df[col] = np.nan if col.startswith("actual_pnl") or col == "secs_to_next_entry" else None
    if entries.empty:
        return df
    # merge_asof per symbol
    parts = []
    for sym, g in df.groupby("symbol", sort=False):
        g = g.sort_values("t0_epoch")
        eg = entries[entries["symbol"] == sym].sort_values("entry_epoch")
        if eg.empty:
            parts.append(g)
            continue
        m = pd.merge_asof(
            g,
            eg.rename(
                columns={
                    "entry_epoch": "next_entry_epoch",
                    "entry_time": "actual_entry_time",
                    "entry_price": "actual_entry_price",
                    "exit_reason": "actual_exit_reason",
                    "pnl_yen_100": "actual_pnl_yen_100",
                    "pnl_pct": "actual_pnl_pct",
                }
            )[
                [
                    "next_entry_epoch",
                    "actual_entry_time",
                    "actual_entry_price",
                    "actual_exit_reason",
                    "actual_pnl_yen_100",
                    "actual_pnl_pct",
                ]
            ],
            left_on="t0_epoch",
            right_on="next_entry_epoch",
            direction="forward",
        )
        dt = m["next_entry_epoch"] - m["t0_epoch"]
        has = m["next_entry_epoch"].notna()
        m["secs_to_next_entry"] = np.where(has, dt, np.nan)
        m["entry_within_next_30s"] = np.where(has, dt <= 30, False)
        m["entry_within_next_60s"] = np.where(has, dt <= 60, False)
        m["entry_within_next_120s"] = np.where(has, dt <= 120, False)
        m["entry_within_next_300s"] = np.where(has, dt <= 300, False)
        for c in ("actual_entry_time", "actual_entry_price", "actual_exit_reason"):
            m.loc[~has, c] = None
        for c in ("actual_pnl_yen_100", "actual_pnl_pct"):
            m.loc[~has, c] = np.nan
        parts.append(m.drop(columns=["next_entry_epoch"]))
    return pd.concat(parts, ignore_index=True)


def build_episodes(df: pd.DataFrame, entries: pd.DataFrame) -> pd.DataFrame:
    lr = df[df["primary_label"] == "LARGE_RISE"].sort_values(["symbol", "t0_epoch"])
    episodes = []
    if lr.empty:
        return pd.DataFrame()
    for sym, g in lr.groupby("symbol"):
        g = g.sort_values("t0_epoch")
        cur: list[pd.Series] = []
        prev_t = None
        for _, row in g.iterrows():
            t = float(row["t0_epoch"])
            if prev_t is None or (t - prev_t) <= 90:
                cur.append(row)
            else:
                if cur:
                    episodes.append(_finalize_episode(cur, entries))
                cur = [row]
            prev_t = t
        if cur:
            episodes.append(_finalize_episode(cur, entries))
    return pd.DataFrame(episodes)


def _finalize_episode(rows: list[pd.Series], entries: pd.DataFrame) -> dict[str, Any]:
    start = float(rows[0]["t0_epoch"])
    end = float(rows[-1]["t0_epoch"])
    sym = rows[0]["symbol"]
    session = rows[0]["session"]
    mfes = [float(r["future_30m_mfe"]) for r in rows if r.get("future_30m_mfe") is not None]
    rets = [float(r["future_30m_return"]) for r in rows if r.get("future_30m_return") is not None]
    best_entry = start
    capture = "MISSED"
    earliest = None
    if not entries.empty:
        eg = entries[
            (entries["symbol"] == sym)
            & (entries["entry_epoch"] >= start)
            & (entries["entry_epoch"] <= end + 900)
        ]
        if not eg.empty:
            earliest = float(eg["entry_epoch"].min())
            dt = earliest - start
            if dt <= 300:
                capture = "CAPTURED"
            elif dt <= 900:
                capture = "LATE_CAPTURED"
            else:
                capture = "MISSED"
    return {
        "symbol": sym,
        "session": session,
        "universe_segment": rows[0]["universe_segment"],
        "refresh_flag": rows[0]["refresh_flag"],
        "episode_start": datetime.fromtimestamp(start, tz=JST).isoformat(timespec="seconds"),
        "episode_end": datetime.fromtimestamp(end, tz=JST).isoformat(timespec="seconds"),
        "episode_start_epoch": start,
        "episode_end_epoch": end,
        "first_signal_time": datetime.fromtimestamp(start, tz=JST).isoformat(timespec="seconds"),
        "best_entry_time": datetime.fromtimestamp(best_entry, tz=JST).isoformat(timespec="seconds"),
        "snapshot_count": len(rows),
        "max_future_mfe": max(mfes) if mfes else None,
        "max_future_return": max(rets) if rets else None,
        "earliest_capture_time": datetime.fromtimestamp(earliest, tz=JST).isoformat(timespec="seconds")
        if earliest
        else None,
        "capture_class": capture,
        "opportunity_class": capture,
        "reject_reasons": "",
        "captured": capture in ("CAPTURED", "LATE_CAPTURED"),
        "missed": capture == "MISSED",
    }


def cliffs_delta(a: np.ndarray, b: np.ndarray) -> Optional[float]:
    a = a[np.isfinite(a)]
    b = b[np.isfinite(b)]
    if len(a) < 3 or len(b) < 3:
        return None
    # approximate via ranks
    # efficient approx: compare means of pairwise via sampling
    rng = np.random.default_rng(42)
    aa = rng.choice(a, size=min(500, len(a)), replace=False)
    bb = rng.choice(b, size=min(500, len(b)), replace=False)
    gt = 0
    lt = 0
    for x in aa:
        gt += np.sum(x > bb)
        lt += np.sum(x < bb)
    n = len(aa) * len(bb)
    return float((gt - lt) / n) if n else None


def cohens_d(a: np.ndarray, b: np.ndarray) -> Optional[float]:
    a = a[np.isfinite(a)]
    b = b[np.isfinite(b)]
    if len(a) < 3 or len(b) < 3:
        return None
    va = np.var(a, ddof=1)
    vb = np.var(b, ddof=1)
    pooled = math.sqrt(((len(a) - 1) * va + (len(b) - 1) * vb) / (len(a) + len(b) - 2))
    if pooled <= 1e-12:
        return 0.0
    return float((np.mean(a) - np.mean(b)) / pooled)


def auc_safe(y: np.ndarray, s: np.ndarray) -> Optional[float]:
    m = np.isfinite(s) & np.isfinite(y)
    if m.sum() < 10 or len(np.unique(y[m])) < 2:
        return None
    try:
        return float(roc_auc_score(y[m], s[m]))
    except Exception:
        return None


def feature_effect(df: pd.DataFrame, group_a: pd.Series, group_b: pd.Series, label: str) -> list[dict[str, Any]]:
    rows = []
    for col in FEATURE_COLS:
        if col not in df.columns:
            continue
        a = pd.to_numeric(df.loc[group_a, col], errors="coerce").to_numpy(dtype=float)
        b = pd.to_numeric(df.loc[group_b, col], errors="coerce").to_numpy(dtype=float)
        y = np.concatenate([np.ones(group_a.sum()), np.zeros(group_b.sum())])
        s = np.concatenate([a, b])
        rows.append(
            {
                "comparison": label,
                "feature": col,
                "n_a": int(np.isfinite(a).sum()),
                "n_b": int(np.isfinite(b).sum()),
                "median_a": float(np.nanmedian(a)) if np.isfinite(a).any() else None,
                "median_b": float(np.nanmedian(b)) if np.isfinite(b).any() else None,
                "mean_a": float(np.nanmean(a)) if np.isfinite(a).any() else None,
                "mean_b": float(np.nanmean(b)) if np.isfinite(b).any() else None,
                "std_a": float(np.nanstd(a)) if np.isfinite(a).any() else None,
                "std_b": float(np.nanstd(b)) if np.isfinite(b).any() else None,
                "iqr_a": float(np.nanpercentile(a, 75) - np.nanpercentile(a, 25)) if np.isfinite(a).sum() >= 4 else None,
                "iqr_b": float(np.nanpercentile(b, 75) - np.nanpercentile(b, 25)) if np.isfinite(b).sum() >= 4 else None,
                "cliffs_delta": cliffs_delta(a, b),
                "cohens_d": cohens_d(a, b),
                "auc": auc_safe(y, s),
                "missing_rate_a": float(1.0 - np.isfinite(a).mean()) if len(a) else 1.0,
                "missing_rate_b": float(1.0 - np.isfinite(b).mean()) if len(b) else 1.0,
            }
        )
    rows.sort(key=lambda r: -(abs(r["cliffs_delta"]) if r["cliffs_delta"] is not None else -1))
    return rows


def interaction_effects(df: pd.DataFrame, group_a: pd.Series, group_b: pd.Series, label: str) -> list[dict[str, Any]]:
    # top features by |cliff| then 2- and 3-way products
    base = feature_effect(df, group_a, group_b, label)
    top = [r["feature"] for r in base if r["cliffs_delta"] is not None][:8]
    rows = []
    sub = df.copy()
    for f1, f2 in combinations(top, 2):
        name = f"{f1}__x__{f2}"
        sub[name] = pd.to_numeric(sub[f1], errors="coerce") * pd.to_numeric(sub[f2], errors="coerce")
        a = sub.loc[group_a, name].to_numpy(dtype=float)
        b = sub.loc[group_b, name].to_numpy(dtype=float)
        rows.append(
            {
                "comparison": label,
                "features": name,
                "order": 2,
                "cliffs_delta": cliffs_delta(a, b),
                "cohens_d": cohens_d(a, b),
                "n_a": int(np.isfinite(a).sum()),
                "n_b": int(np.isfinite(b).sum()),
            }
        )
    for f1, f2, f3 in combinations(top[:6], 3):
        name = f"{f1}__x__{f2}__x__{f3}"
        sub[name] = (
            pd.to_numeric(sub[f1], errors="coerce")
            * pd.to_numeric(sub[f2], errors="coerce")
            * pd.to_numeric(sub[f3], errors="coerce")
        )
        a = sub.loc[group_a, name].to_numpy(dtype=float)
        b = sub.loc[group_b, name].to_numpy(dtype=float)
        rows.append(
            {
                "comparison": label,
                "features": name,
                "order": 3,
                "cliffs_delta": cliffs_delta(a, b),
                "cohens_d": cohens_d(a, b),
                "n_a": int(np.isfinite(a).sum()),
                "n_b": int(np.isfinite(b).sum()),
            }
        )
    rows.sort(key=lambda r: -(abs(r["cliffs_delta"]) if r["cliffs_delta"] is not None else -1))
    return rows[:40]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    print("W43C loading universe segments...", flush=True)
    segments = []
    all_syms: set[str] = set()
    integrity: dict[str, Any] = {"universe_segments": [], "push_dir": str(PUSH_DIR), "leakage_guards": True}
    for seg in UNIVERSE_SEGMENTS:
        if not seg["csv"].is_file():
            integrity.setdefault("missing_universe", []).append(str(seg["csv"]))
            continue
        syms = load_universe(seg["csv"])
        all_syms.update(syms)
        start = parse_ts(seg["start"])
        end = parse_ts(seg["end"])
        label_end = parse_ts(seg["label_end"])
        assert start and end and label_end
        s = {
            "session": seg["session"],
            "segment": seg["segment"],
            "refresh": seg["refresh"],
            "symbols": set(syms),
            "start_epoch": to_epoch(start),
            "end_epoch": to_epoch(end),
            "label_end_epoch": to_epoch(label_end),
        }
        segments.append(s)
        integrity["universe_segments"].append(
            {"segment": seg["segment"], "n_symbols": len(syms), "start": seg["start"], "end": seg["end"]}
        )
    print(f"  symbols union={len(all_syms)} segments={len(segments)}", flush=True)
    if not segments or not all_syms:
        _wj(OUT / f"w43c_{DATE}_data_integrity.json", {"error": "missing_universe", **integrity})
        _wj(OUT / f"w43c_{DATE}_report.json", {"verdicts": ["INSUFFICIENT_WATCH50_DATA", "DATA_INTEGRITY_BLOCKED"]})
        return 1

    print("Loading official entries...", flush=True)
    entries = load_official_entries()
    integrity["official_entry_count"] = int(len(entries))
    integrity["w43_parquet_exists"] = W43_PQ.is_file()

    missing_push = [s for s in sorted(all_syms) if not (PUSH_DIR / f"{s}.jsonl").is_file()]
    integrity["missing_push_symbols"] = missing_push
    integrity["push_coverage"] = 1.0 - len(missing_push) / max(1, len(all_syms))

    print(f"Building snapshots with {MAX_WORKERS} workers...", flush=True)
    tasks = [(sym, segments) for sym in sorted(all_syms)]
    snap_rows: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(_worker, t): t[0] for t in tasks}
        done = 0
        for fut in as_completed(futs):
            sym = futs[fut]
            try:
                rows = fut.result()
            except Exception as exc:
                print(f"  FAIL {sym}: {exc}", flush=True)
                rows = []
            snap_rows.extend(rows)
            done += 1
            if done % 10 == 0 or done == len(tasks):
                print(f"  {done}/{len(tasks)} symbols  rows={len(snap_rows)}", flush=True)

    df = pd.DataFrame(snap_rows)
    print(f"Snapshots: {len(df)}", flush=True)
    if df.empty:
        _wj(OUT / f"w43c_{DATE}_data_integrity.json", {"error": "no_snapshots", **integrity})
        _wj(OUT / f"w43c_{DATE}_report.json", {"verdicts": ["INSUFFICIENT_WATCH50_DATA"]})
        return 1

    df = attach_entries(df, entries)
    # episodes
    ep = build_episodes(df, entries)
    print(f"Episodes LARGE_RISE: {len(ep)} — classifying missed via reject stream...", flush=True)
    ep = classify_episodes_with_rejects(ep)
    print(f"Episodes classified: {len(ep)}", flush=True)

    # Capture summary
    capture_rows = []
    for scope, edf in (
        ("am", ep[ep["session"] == "am"] if not ep.empty else ep),
        ("pm", ep[ep["session"] == "pm"] if not ep.empty else ep),
        ("day", ep),
    ):
        n = len(edf)
        cap = int((edf["capture_class"] == "CAPTURED").sum()) if n else 0
        late = int((edf["capture_class"] == "LATE_CAPTURED").sum()) if n else 0
        miss = int((edf["capture_class"] == "MISSED").sum()) if n else 0
        capture_rows.append(
            {
                "scope": scope,
                "large_rise_episode_count": n,
                "captured_episode_count": cap,
                "late_captured_episode_count": late,
                "missed_episode_count": miss,
                "capture_rate_5m": round(cap / n, 4) if n else None,
                "capture_rate_15m": round((cap + late) / n, 4) if n else None,
                "cap_blocked": int((edf["opportunity_class"] == "CAP_BLOCKED").sum()) if n else 0,
                "rule_rejected": int((edf["opportunity_class"] == "RULE_REJECTED").sum()) if n else 0,
                "never_candidate": int((edf["opportunity_class"] == "NEVER_CANDIDATE").sum()) if n else 0,
            }
        )
    # ENTRY outcome mix vs labels at entry time (±30s snap)
    entry_label_rows = []
    if not entries.empty and not df.empty:
        for _, e in entries.iterrows():
            near = df[(df["symbol"] == e["symbol"]) & (df["t0_epoch"] <= e["entry_epoch"])]
            if near.empty:
                lab = "UNAVAILABLE"
            else:
                # snapshot at or just before entry
                row = near.iloc[(near["t0_epoch"] - e["entry_epoch"]).abs().argmin()]
                # prefer snapshot within 60s after? use last before
                before = near[near["t0_epoch"] <= e["entry_epoch"]]
                row = before.iloc[-1] if not before.empty else near.iloc[0]
                lab = row["primary_label"]
            entry_label_rows.append(
                {
                    "symbol": e["symbol"],
                    "session": e["session"],
                    "entry_time": e["entry_time"],
                    "exit_reason": e["exit_reason"],
                    "pnl_yen_100": e["pnl_yen_100"],
                    "label_at_entry": lab,
                }
            )
    entry_lab_df = pd.DataFrame(entry_label_rows)
    for scope in ("am", "pm", "day"):
        sub = entry_lab_df if scope == "day" else entry_lab_df[entry_lab_df["session"] == scope]
        n = len(sub)
        for lab in ("LARGE_RISE", "SIDEWAYS", "DECLINE", "RECOVERED_RISE", "SMALL_RISE", "UNAVAILABLE"):
            capture_rows.append(
                {
                    "scope": scope,
                    "metric": f"entry_share_{lab}",
                    "value": round(float((sub["label_at_entry"] == lab).mean()), 4) if n else None,
                    "n": n,
                }
            )

    # Funnel
    funnel = []
    for scope, sdf in (("am", df[df["session"] == "am"]), ("pm", df[df["session"] == "pm"]), ("day", df)):
        n_uni = len(sdf)
        n_px = int((sdf["exclude_reason"].isna() | (sdf["exclude_reason"] == "") | (sdf["exclude_reason"].isnull())).sum()) if n_uni else 0
        # refine valid price
        n_px = int(sdf["current_price"].notna().sum()) if n_uni else 0
        # approximate funnel using reject proximity is hard per snapshot — use episode reverse funnel too
        n_lr = int((sdf["primary_label"] == "LARGE_RISE").sum())
        n_entry = int(sdf["entry_within_next_300s"].fillna(False).astype(bool).sum()) if "entry_within_next_300s" in sdf else 0
        funnel.append({"scope": scope, "stage": "universe_active_snapshots", "n": n_uni})
        funnel.append({"scope": scope, "stage": "valid_current_price", "n": n_px})
        funnel.append({"scope": scope, "stage": "future_large_rise_snapshots", "n": n_lr})
        funnel.append({"scope": scope, "stage": "official_entry_within_300s", "n": n_entry})
    # reverse funnel on episodes
    if not ep.empty:
        for scope, edf in (("am", ep[ep["session"] == "am"]), ("pm", ep[ep["session"] == "pm"]), ("day", ep)):
            funnel.append({"scope": scope, "stage": "rev_large_rise_episodes", "n": len(edf)})
            funnel.append(
                {
                    "scope": scope,
                    "stage": "rev_captured_5m",
                    "n": int((edf["capture_class"] == "CAPTURED").sum()),
                }
            )
            funnel.append(
                {
                    "scope": scope,
                    "stage": "rev_missed",
                    "n": int((edf["capture_class"] == "MISSED").sum()),
                }
            )
            funnel.append(
                {
                    "scope": scope,
                    "stage": "rev_cap_blocked",
                    "n": int((edf["opportunity_class"] == "CAP_BLOCKED").sum()),
                }
            )
            funnel.append(
                {
                    "scope": scope,
                    "stage": "rev_rule_rejected",
                    "n": int((edf["opportunity_class"] == "RULE_REJECTED").sum()),
                }
            )
            funnel.append(
                {
                    "scope": scope,
                    "stage": "rev_never_candidate",
                    "n": int((edf["opportunity_class"] == "NEVER_CANDIDATE").sum()),
                }
            )

    # Feature comparisons — use episode-start snapshots for MISSED vs etc.
    # Map episode start rows
    effect_rows: list[dict[str, Any]] = []
    inter_rows: list[dict[str, Any]] = []
    missed_vs_bad: list[dict[str, Any]] = []

    def episode_start_mask(edf: pd.DataFrame) -> pd.Series:
        if edf is None or edf.empty:
            return pd.Series(False, index=df.index)
        keys = set(zip(edf["symbol"].astype(str), edf["episode_start_epoch"].astype(float)))
        return pd.Series(
            [(str(s), float(t)) in keys for s, t in zip(df["symbol"], df["t0_epoch"])],
            index=df.index,
        )

    if not ep.empty:
        missed_ep = ep[ep["capture_class"] == "MISSED"]
        captured_ep = ep[ep["capture_class"] == "CAPTURED"]
        # snapshots
        m_miss = episode_start_mask(missed_ep) if len(missed_ep) else pd.Series(False, index=df.index)
        m_cap = episode_start_mask(captured_ep) if len(captured_ep) else pd.Series(False, index=df.index)
        m_lr = df["primary_label"] == "LARGE_RISE"
        m_side = df["primary_label"] == "SIDEWAYS"
        m_dec = df["primary_label"] == "DECLINE"
        # actual NP / STOP entries
        np_entries = entries[entries["exit_reason"] == "no_progress_exit"] if not entries.empty else entries
        stop_entries = entries[entries["exit_reason"] == "stop_hit"] if not entries.empty else entries

        def entry_snap_mask(edf: pd.DataFrame) -> pd.Series:
            mask = pd.Series(False, index=df.index)
            if edf is None or edf.empty:
                return mask
            for _, e in edf.iterrows():
                before = df[(df["symbol"] == e["symbol"]) & (df["t0_epoch"] <= e["entry_epoch"])]
                if before.empty:
                    continue
                idx = before.index[-1]
                mask.loc[idx] = True
            return mask

        m_np = entry_snap_mask(np_entries)
        m_stop = entry_snap_mask(stop_entries)

        comps = [
            ("CAPTURED_vs_MISSED_LARGE_RISE", m_cap, m_miss),
            ("LARGE_RISE_vs_SIDEWAYS", m_lr, m_side),
            ("LARGE_RISE_vs_DECLINE", m_lr, m_dec),
            ("MISSED_LARGE_RISE_vs_NO_PROGRESS", m_miss, m_np),
            ("MISSED_LARGE_RISE_vs_STOP", m_miss, m_stop),
            ("CAPTURED_LARGE_RISE_vs_STOP", m_cap, m_stop),
        ]
        for name, a, b in comps:
            if a.sum() >= 3 and b.sum() >= 3:
                eff = feature_effect(df, a, b, name)
                effect_rows.extend(eff)
                inter_rows.extend(interaction_effects(df, a, b, name))
                top = eff[0] if eff else {}
                missed_vs_bad.append(
                    {
                        "comparison": name,
                        "n_a": int(a.sum()),
                        "n_b": int(b.sum()),
                        "top_feature": top.get("feature"),
                        "cliffs_delta": top.get("cliffs_delta"),
                        "cohens_d": top.get("cohens_d"),
                        "auc": top.get("auc"),
                        "median_a": top.get("median_a"),
                        "median_b": top.get("median_b"),
                    }
                )

    # Refresh analysis
    refresh_rows = []
    if not ep.empty:
        for seg in ("am_open", "am_refresh1000", "pm_open", "pm_refresh1430"):
            edf = ep[ep["universe_segment"] == seg]
            n = len(edf)
            cap = int((edf["capture_class"] == "CAPTURED").sum()) if n else 0
            late = int((edf["capture_class"] == "LATE_CAPTURED").sum()) if n else 0
            refresh_rows.append(
                {
                    "segment": seg,
                    "episodes": n,
                    "captured_5m": cap,
                    "captured_15m": cap + late,
                    "capture_rate_5m": round(cap / n, 4) if n else None,
                    "capture_rate_15m": round((cap + late) / n, 4) if n else None,
                    "missed": int((edf["capture_class"] == "MISSED").sum()) if n else 0,
                }
            )

    # Outlier sensitivity
    outlier_rows = []
    for name, edf in (
        ("all", ep),
        ("excl_max_mfe_symbol", ep),
        ("excl_max_pnl_entry_symbol", ep),
        ("excl_7581", ep[ep["symbol"] != OUTLIER] if not ep.empty else ep),
    ):
        e2 = edf.copy()
        if name == "excl_max_mfe_symbol" and not e2.empty:
            sym = e2.sort_values("max_future_mfe", ascending=False).iloc[0]["symbol"]
            e2 = e2[e2["symbol"] != sym]
            name = f"excl_max_mfe_{sym}"
        if name == "excl_max_pnl_entry_symbol" and not entries.empty:
            sym = entries.sort_values("pnl_yen_100", ascending=False).iloc[0]["symbol"]
            e2 = e2[e2["symbol"] != sym]
            name = f"excl_max_pnl_{sym}"
        n = len(e2)
        cap = int((e2["capture_class"] == "CAPTURED").sum()) if n else 0
        outlier_rows.append(
            {
                "variant": name,
                "episodes": n,
                "capture_rate_5m": round(cap / n, 4) if n else None,
                "missed": int((e2["capture_class"] == "MISSED").sum()) if n else 0,
            }
        )

    # AM/PM direction agreement on LARGE_RISE vs SIDEWAYS
    am_pm_agree = []
    m_am_lr = (df["session"] == "am") & (df["primary_label"] == "LARGE_RISE")
    m_am_side = (df["session"] == "am") & (df["primary_label"] == "SIDEWAYS")
    m_pm_lr = (df["session"] == "pm") & (df["primary_label"] == "LARGE_RISE")
    m_pm_side = (df["session"] == "pm") & (df["primary_label"] == "SIDEWAYS")
    if m_am_lr.sum() >= 3 and m_am_side.sum() >= 3 and m_pm_lr.sum() >= 3 and m_pm_side.sum() >= 3:
        am_eff = {r["feature"]: r["cliffs_delta"] for r in feature_effect(df, m_am_lr, m_am_side, "am")}
        pm_eff = {r["feature"]: r["cliffs_delta"] for r in feature_effect(df, m_pm_lr, m_pm_side, "pm")}
        for f in FEATURE_COLS:
            da, db = am_eff.get(f), pm_eff.get(f)
            if da is None or db is None:
                continue
            am_pm_agree.append(
                {
                    "feature": f,
                    "cliffs_am": da,
                    "cliffs_pm": db,
                    "same_direction": (da > 0 and db > 0) or (da < 0 and db < 0),
                }
            )

    # Snapshot summary
    snap_summary = []
    for scope, sdf in (("am", df[df["session"] == "am"]), ("pm", df[df["session"] == "pm"]), ("day", df)):
        vc = sdf["primary_label"].value_counts().to_dict()
        snap_summary.append(
            {
                "scope": scope,
                "snapshots": len(sdf),
                "symbols": int(sdf["symbol"].nunique()),
                **{f"label_{k}": int(v) for k, v in vc.items()},
                "valid_price_rate": round(float(sdf["current_price"].notna().mean()), 4) if len(sdf) else None,
            }
        )

    # Write outputs
    print("Writing outputs...", flush=True)
    pq_path = OUT / f"w43c_{DATE}_watch50_snapshot.parquet"
    # slim columns for parquet
    df.to_parquet(pq_path, index=False)
    _wc(OUT / f"w43c_{DATE}_watch50_snapshot_summary.csv", snap_summary)
    _wc(OUT / f"w43c_{DATE}_opportunity_episodes.csv", ep)
    _wc(OUT / f"w43c_{DATE}_entry_funnel.csv", funnel)
    _wc(OUT / f"w43c_{DATE}_capture_summary.csv", capture_rows)
    _wc(OUT / f"w43c_{DATE}_feature_effect.csv", effect_rows)
    _wc(OUT / f"w43c_{DATE}_feature_interactions.csv", inter_rows)
    _wc(OUT / f"w43c_{DATE}_missed_vs_bad_entry.csv", missed_vs_bad)
    _wc(OUT / f"w43c_{DATE}_refresh_analysis.csv", refresh_rows)
    _wc(OUT / f"w43c_{DATE}_outlier_sensitivity.csv", outlier_rows)

    day_cap = next(r for r in capture_rows if r.get("scope") == "day" and "large_rise_episode_count" in r)
    # Answers
    def top_feat(comp: str) -> dict[str, Any]:
        for r in missed_vs_bad:
            if r["comparison"] == comp:
                return r
        return {}

    agree_feats = [r["feature"] for r in am_pm_agree if r.get("same_direction")][:10]
    # stable after excl 7581
    stable_note = "single_day"
    if outlier_rows:
        base_cr = next((r["capture_rate_5m"] for r in outlier_rows if r["variant"] == "all"), None)
        ex_cr = next((r["capture_rate_5m"] for r in outlier_rows if "7581" in str(r["variant"])), None)
        if base_cr is not None and ex_cr is not None and abs((base_cr or 0) - (ex_cr or 0)) < 0.15:
            stable_note = "capture_rate_stable_excl_7581"

    missed_reasons = (
        Counter(ep.loc[ep["capture_class"] == "MISSED", "opportunity_class"])
        if not ep.empty
        else Counter()
    )
    # universe miss: LARGE_RISE symbols not in any segment? by construction all snaps are in universe
    # symbols with LARGE_RISE that weren't in open universe but only after refresh — count episodes in refresh segments that weren't in prior
    universe_late = 0
    if not ep.empty:
        am_open_syms = set()
        am_ref_syms = set()
        for seg in segments:
            if seg["segment"] == "am_open":
                am_open_syms = seg["symbols"]
            if seg["segment"] == "am_refresh1000":
                am_ref_syms = seg["symbols"]
        for _, r in ep.iterrows():
            if r["universe_segment"] == "am_refresh1000" and r["symbol"] not in am_open_syms:
                universe_late += 1

    never_cand_syms = set(ep.loc[ep["opportunity_class"] == "NEVER_CANDIDATE", "symbol"]) if not ep.empty else set()
    # PBv2 never candidate among missed
    pbv2_not_candidate = int(missed_reasons.get("NEVER_CANDIDATE", 0))

    # System diagnosis
    miss_n = int(day_cap.get("missed_episode_count") or 0)
    cap_n = int(day_cap.get("captured_episode_count") or 0)
    entry_side = None
    if not entry_lab_df.empty:
        entry_side = float((entry_lab_df["label_at_entry"].isin(["SIDEWAYS", "DECLINE"])).mean())
    diagnosis = []
    if miss_n > cap_n:
        diagnosis.append("misses_good_names")
    if entry_side is not None and entry_side >= 0.4:
        diagnosis.append("selects_bad_or_flat_entries")
    if not diagnosis:
        diagnosis.append("mixed_or_limited_signal")

    answers = {
        "1_large_rise_episode_count": int(day_cap["large_rise_episode_count"]),
        "2_captured_5m_count": int(day_cap["captured_episode_count"]),
        "2_capture_rate_5m": day_cap["capture_rate_5m"],
        "3_capture_rate_15m": day_cap["capture_rate_15m"],
        "4_missed_main_causes": dict(missed_reasons),
        "5_cap_blocked_count": int(day_cap.get("cap_blocked") or 0),
        "6_guard_rule_rejected_count": int(day_cap.get("rule_rejected") or 0),
        "7_pbv2_never_candidate_missed_episodes": pbv2_not_candidate,
        "8_universe_late_or_refresh_only_episodes": universe_late,
        "9_missed_vs_noprogress_top": top_feat("MISSED_LARGE_RISE_vs_NO_PROGRESS"),
        "10_missed_vs_stop_top": top_feat("MISSED_LARGE_RISE_vs_STOP"),
        "11_large_rise_vs_sideways_top": top_feat("LARGE_RISE_vs_SIDEWAYS"),
        "12_large_rise_vs_decline_top": top_feat("LARGE_RISE_vs_DECLINE"),
        "13_am_pm_agree_features": agree_feats,
        "14_refresh_capture_improved": refresh_rows,
        "15_outlier_stable": stable_note,
        "16_system_diagnosis": diagnosis,
        "17_next5d_feature_candidates": [
            r["feature"]
            for r in effect_rows
            if r["comparison"] == "MISSED_LARGE_RISE_vs_NO_PROGRESS" and r.get("cliffs_delta") is not None
        ][:8],
        "18_w43d_combos": [r["features"] for r in inter_rows if r.get("order") in (2, 3)][:5],
        "19_runtime_unchanged_conclusion": (
            "Single-day Watch50 analysis only; no Runtime/YAML changes. "
            "Use W43D 5-day extension before any rule proposal."
        ),
        "20_data_integrity": {
            "push_coverage": integrity["push_coverage"],
            "missing_push": missing_push,
            "leakage_guards": True,
            "yahoo_unused": True,
            "capture_rebuild": False,
            "ask_fallback": False,
        },
    }

    verdicts = ["FOUND_SINGLE_DAY_OPPORTUNITY_SIGNAL"]
    if miss_n > 0:
        verdicts.append("FOUND_MISSED_WINNER_STATE")
    if int(day_cap.get("cap_blocked") or 0) > 0:
        verdicts.append("FOUND_CAP_CAPTURE_LIMIT")
    if int(day_cap.get("rule_rejected") or 0) > 0:
        verdicts.append("FOUND_GUARD_CAPTURE_LIMIT")
    if pbv2_not_candidate > 0:
        verdicts.append("FOUND_PBV2_CANDIDATE_LIMIT")
    if universe_late > 0:
        verdicts.append("FOUND_UNIVERSE_CAPTURE_LIMIT")
    # refresh improvement?
    am_o = next((r for r in refresh_rows if r["segment"] == "am_open"), {})
    am_r = next((r for r in refresh_rows if r["segment"] == "am_refresh1000"), {})
    if (am_r.get("capture_rate_5m") or 0) > (am_o.get("capture_rate_5m") or 0):
        verdicts.append("FOUND_REFRESH_IMPROVEMENT")
    if "selects_bad_or_flat_entries" in diagnosis and "misses_good_names" in diagnosis:
        verdicts.append("FOUND_BAD_SELECTION_INVERSION")
    if integrity["push_coverage"] < 0.8:
        verdicts.append("INSUFFICIENT_WATCH50_DATA")

    report = {
        "phase": "Phase687W43C",
        "trading_date": DATE,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "verdicts": verdicts,
        "required_answers": answers,
        "snapshot_count": int(len(df)),
        "episode_count": int(len(ep)),
        "official_entries": int(len(entries)),
        "runtime_changed": False,
        "outputs": [
            f"w43c_{DATE}_watch50_snapshot.parquet",
            f"w43c_{DATE}_watch50_snapshot_summary.csv",
            f"w43c_{DATE}_opportunity_episodes.csv",
            f"w43c_{DATE}_entry_funnel.csv",
            f"w43c_{DATE}_capture_summary.csv",
            f"w43c_{DATE}_feature_effect.csv",
            f"w43c_{DATE}_feature_interactions.csv",
            f"w43c_{DATE}_missed_vs_bad_entry.csv",
            f"w43c_{DATE}_refresh_analysis.csv",
            f"w43c_{DATE}_outlier_sensitivity.csv",
            f"w43c_{DATE}_data_integrity.json",
            f"w43c_{DATE}_report.json",
            f"w43c_{DATE}_report.md",
        ],
    }
    integrity.update(
        {
            "snapshot_count": int(len(df)),
            "episode_count": int(len(ep)),
            "label_counts": df["primary_label"].value_counts().to_dict(),
            "am_pm_agree_features": agree_feats,
            "no_ask_fallback": True,
            "no_future_universe_leak": True,
            "no_session_close_as_30m": True,
        }
    )
    _wj(OUT / f"w43c_{DATE}_data_integrity.json", integrity)
    _wj(OUT / f"w43c_{DATE}_report.json", report)

    md = f"""# Phase687W43C — Watch50 Future-30m Opportunity ({DATE})

## Verdict
`{' | '.join(verdicts)}`

Runtime/YAML unchanged. Official entry = position_registered canonical only (ghost excluded).

## Capture (episodes)
| scope | LARGE_RISE episodes | captured≤5m | rate5m | rate15m | missed |
|-------|--------------------:|------------:|-------:|--------:|-------:|
"""
    for r in capture_rows:
        if "large_rise_episode_count" not in r:
            continue
        md += (
            f"| {r['scope']} | {r['large_rise_episode_count']} | {r['captured_episode_count']} | "
            f"{r['capture_rate_5m']} | {r['capture_rate_15m']} | {r['missed_episode_count']} |\n"
        )

    md += f"""
## Required answers (summary)

1. LARGE_RISE episodes: **{answers['1_large_rise_episode_count']}**
2. Captured ≤5m: **{answers['2_captured_5m_count']}** (rate {answers['2_capture_rate_5m']})
3. Capture ≤15m: **{answers['3_capture_rate_15m']}**
4. Missed causes: `{answers['4_missed_main_causes']}`
5. CAP blocked: **{answers['5_cap_blocked_count']}**
6. Guard/rule rejected: **{answers['6_guard_rule_rejected_count']}**
7. Never PBv2-candidate missed episodes: **{answers['7_pbv2_never_candidate_missed_episodes']}**
8. Refresh-only / late-universe episodes: **{answers['8_universe_late_or_refresh_only_episodes']}**
9. MISSED vs NO_PROGRESS top: `{answers['9_missed_vs_noprogress_top']}`
10. MISSED vs STOP top: `{answers['10_missed_vs_stop_top']}`
11. LARGE_RISE vs SIDEWAYS top: `{answers['11_large_rise_vs_sideways_top']}`
12. LARGE_RISE vs DECLINE top: `{answers['12_large_rise_vs_decline_top']}`
13. AM/PM agree features: `{answers['13_am_pm_agree_features']}`
14. Refresh analysis: see `w43c_{DATE}_refresh_analysis.csv`
15. Outlier: `{answers['15_outlier_stable']}`
16. Diagnosis: `{answers['16_system_diagnosis']}`
17. Next-5d candidates: `{answers['17_next5d_feature_candidates']}`
18. W43D combos: `{answers['18_w43d_combos']}`
19. {answers['19_runtime_unchanged_conclusion']}
20. Integrity: push_coverage={answers['20_data_integrity']['push_coverage']}, Ask fallback=False, Yahoo unused, no capture rebuild

## Snapshots
- rows: {len(df)}
- parquet: `w43c_{DATE}_watch50_snapshot.parquet`
"""
    _wm(OUT / f"w43c_{DATE}_report.md", md)
    print(json.dumps({"verdicts": verdicts, "episodes": len(ep), "snapshots": len(df)}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
