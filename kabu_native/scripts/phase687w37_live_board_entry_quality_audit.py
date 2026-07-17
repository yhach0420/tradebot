#!/usr/bin/env python3
"""Phase687W37: Live board microstructure vs AM Paper ENTRY quality (research only).

No Capture file copies. Streams push_part_*.jsonl; writes slim intermediates only.
MAINLINE / Shadow / ENTRY-EXIT / CAP / OR / orders untouched.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import shutil
import statistics
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Sequence
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

NATIVE = Path(__file__).resolve().parents[1]
JST = ZoneInfo("Asia/Tokyo")
CAPTURE = NATIVE / "data" / "market_capture" / "20260716"
SESSION = NATIVE / "results" / "small_paper" / "20260716" / "live_session_073602"
OUT = NATIVE / "results" / "reports" / "phase687w37_live_board_entry_quality"
CACHE = OUT / "_cache_slim"
MAX_WORKERS = 4
PRE_WINDOWS = (5, 15, 30, 60, 120, 300)
POST_WINDOWS = (30, 60, 120, 300, 600)


def _wj(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _wc(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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
            w.writerow({k: r.get(k, "") for k in cols})


def _wm(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _parse_ts(v: Any) -> Optional[datetime]:
    if v is None or v == "":
        return None
    s = str(v).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return dt.astimezone(JST)
    except Exception:
        return None


def _f(v: Any, default: float = float("nan")) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _sym_code(sym: Any) -> str:
    s = str(sym or "").strip()
    return s[:-2] if s.endswith(".T") else s


def _payload_hash(symbol: str, ts: str, op: dict[str, Any]) -> str:
    # Stable hash on symbol + timestamp + key board/price fields (not full payload copy)
    parts = [symbol, ts, str(op.get("CurrentPrice")), str(op.get("TradingVolume"))]
    for side in ("Buy", "Sell"):
        for i in range(1, 11):
            lv = op.get(f"{side}{i}") or {}
            if isinstance(lv, dict):
                parts.append(f"{lv.get('Price')}:{lv.get('Qty')}")
            else:
                parts.append(":")
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _lvl_qty(op: dict[str, Any], side: str, i: int) -> float:
    lv = op.get(f"{side}{i}")
    if isinstance(lv, dict):
        return _f(lv.get("Qty"), 0.0)
    return 0.0


def _lvl_px(op: dict[str, Any], side: str, i: int) -> float:
    lv = op.get(f"{side}{i}")
    if isinstance(lv, dict):
        return _f(lv.get("Price"), float("nan"))
    return float("nan")


def _extract_part(args: tuple[str, list[str], float, float, str]) -> dict[str, Any]:
    """Worker: stream one Capture part → slim parquet for target symbols/time."""
    part_path, symbols, t0_epoch, t1_epoch, out_path = args
    symset = set(symbols)
    rows: list[dict[str, Any]] = []
    malformed = 0
    seen = 0
    kept = 0
    dup_keys: set[str] = set()
    dups = 0
    board_missing = 0
    cpt_missing = 0
    bt_missing = 0
    path = Path(part_path)
    if path.stat().st_size <= 0:
        return {"part": path.name, "kept": 0, "seen": 0, "zero_byte": True}

    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.strip():
                continue
            seen += 1
            try:
                o = json.loads(line)
            except Exception:
                malformed += 1
                continue
            sym = _sym_code(o.get("symbol"))
            if sym not in symset:
                continue
            recv = _parse_ts(o.get("received_at_jst"))
            if recv is None:
                continue
            epoch = recv.timestamp()
            if epoch < t0_epoch or epoch > t1_epoch:
                continue
            op = o.get("original_payload") if isinstance(o.get("original_payload"), dict) else {}
            cpt = o.get("current_price_time") or op.get("CurrentPriceTime")
            if not cpt:
                cpt_missing += 1
            board_t = None
            b1 = op.get("Buy1") if isinstance(op.get("Buy1"), dict) else None
            s1 = op.get("Sell1") if isinstance(op.get("Sell1"), dict) else None
            if b1 and b1.get("Time"):
                board_t = b1.get("Time")
            elif s1 and s1.get("Time"):
                board_t = s1.get("Time")
            if not board_t:
                bt_missing += 1
            if not isinstance(op.get("Buy1"), dict) and not isinstance(op.get("Sell1"), dict):
                board_missing += 1
            ts_key = str(cpt or o.get("received_at_jst") or "")
            h = _payload_hash(sym, ts_key, op if isinstance(op, dict) else {})
            dk = f"{sym}|{ts_key}|{h}"
            if dk in dup_keys:
                dups += 1
                continue
            dup_keys.add(dk)
            row: dict[str, Any] = {
                "symbol": sym,
                "recv_epoch": epoch,
                "received_at_jst": o.get("received_at_jst"),
                "sequence": o.get("sequence"),
                "current_price": _f(o.get("current_price") or op.get("CurrentPrice")),
                "current_price_time": cpt or "",
                "board_time": board_t or "",
                "trading_volume": _f(o.get("trading_volume") or op.get("TradingVolume"), 0.0),
                "trading_value": _f(o.get("trading_value") or op.get("TradingValue"), 0.0),
                "payload_hash": h,
            }
            for i in range(1, 11):
                row[f"bid{i}_px"] = _lvl_px(op, "Buy", i)
                row[f"bid{i}_qty"] = _lvl_qty(op, "Buy", i)
                row[f"ask{i}_px"] = _lvl_px(op, "Sell", i)
                row[f"ask{i}_qty"] = _lvl_qty(op, "Sell", i)
            rows.append(row)
            kept += 1

    outp = Path(out_path)
    outp.parent.mkdir(parents=True, exist_ok=True)
    if rows:
        pd.DataFrame(rows).to_parquet(outp, index=False)
    else:
        pd.DataFrame([]).to_parquet(outp, index=False)
    return {
        "part": path.name,
        "seen": seen,
        "kept": kept,
        "malformed": malformed,
        "dups_skipped": dups,
        "board_missing": board_missing,
        "cpt_missing": cpt_missing,
        "bt_missing": bt_missing,
        "zero_byte": False,
        "out": str(outp),
    }


def load_entries() -> list[dict[str, Any]]:
    acc: list[dict[str, Any]] = []
    exits: list[dict[str, Any]] = []
    with (SESSION / "small_paper_events.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            o = json.loads(line)
            t = o.get("event_type")
            if t == "accepted":
                acc.append(o)
            elif t == "observer_exit":
                exits.append(o)
    struct: dict[tuple[str, str], dict[str, str]] = {}
    with (SESSION / "structural_trades.csv").open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            struct[(str(row.get("symbol")), str(row.get("entry_time")))] = row

    by_sym: dict[str, list[dict[str, Any]]] = {}
    for a in sorted(acc, key=lambda x: _parse_ts(x.get("entry_time")) or datetime.min.replace(tzinfo=JST)):
        by_sym.setdefault(str(a.get("symbol")), []).append(a)
    exit_q: dict[str, list[dict[str, Any]]] = {}
    for e in sorted(exits, key=lambda x: _parse_ts(x.get("exit_time")) or datetime.min.replace(tzinfo=JST)):
        exit_q.setdefault(str(e.get("symbol")), []).append(e)

    # first ENTRY per symbol marks reENTRY later
    first_seen: dict[str, str] = {}
    out: list[dict[str, Any]] = []
    for sym, entries in by_sym.items():
        outs = exit_q.get(sym, [])
        for i, a in enumerate(entries):
            e = outs[i] if i < len(outs) else {}
            et = str(a.get("entry_time") or "")
            xt = str(e.get("exit_time") or e.get("event_time") or "")
            st = struct.get((sym, et), {})
            route = str(a.get("entry_type") or "PBV2")
            if route.upper().startswith("OR"):
                route = "OR"
            else:
                route = "PBV2"
            is_re = sym in first_seen
            if not is_re:
                first_seen[sym] = et
            hold = None
            tea, txa = _parse_ts(et), _parse_ts(xt)
            if tea and txa:
                hold = (txa - tea).total_seconds()
            pnl = _f(e.get("pnl_pct") or st.get("realized_pnl_pct"), 0.0)
            out.append(
                {
                    "symbol": sym,
                    "symbol_code": _sym_code(sym),
                    "entry_time": et,
                    "exit_time": xt,
                    "entry_epoch": tea.timestamp() if tea else float("nan"),
                    "exit_epoch": txa.timestamp() if txa else float("nan"),
                    "entry_price": _f(a.get("current_price") or st.get("entry_price")),
                    "exit_price": _f(e.get("exit_price") or st.get("close_price")),
                    "route": route,
                    "score_v2": _f(a.get("entry_expectancy_score_v2")),
                    "momentum": _f(a.get("momentum_continuation_score") or a.get("entry_momentum_score")),
                    "quality": _f(a.get("continuation_quality_score")),
                    "board_mid": bool(a.get("entry_board_mid_token_active")),
                    "entry_imbalance_percentile": _f(a.get("entry_imbalance_percentile")),
                    "entry_order_book_imbalance": _f(a.get("entry_order_book_imbalance")),
                    "price_age_sec": _f(a.get("price_age_sec")),
                    "board_age_sec": _f(a.get("board_age_sec")),
                    "spread_bps": _f(a.get("spread_bps")),
                    "update_count": _f(
                        a.get("update_count_before_entry")
                        if a.get("update_count_before_entry") is not None
                        else a.get("update_count")
                    ),
                    "pnl_pct": pnl,
                    "pnl_yen_100": round(pnl * 100.0, 4),
                    "mfe_pct": _f(st.get("mfe_pct") or e.get("peak_mfe_pct") or e.get("rolling_mfe_pct"), 0.0),
                    "mae_pct": _f(st.get("mae_pct") or e.get("rolling_mae_pct"), 0.0),
                    "hold_sec": float(hold or _f(st.get("hold_duration_sec"), 0.0) or 0.0),
                    "exit_reason": str(e.get("exit_reason") or st.get("close_reason") or ""),
                    "is_reentry": is_re,
                    "price_freshness_source": str(a.get("price_freshness_source") or ""),
                }
            )
    out.sort(key=lambda r: r["entry_epoch"] if r["entry_epoch"] == r["entry_epoch"] else 0)
    return out


def depth_sum(row: pd.Series, side: str, n: int) -> float:
    return float(sum(_f(row.get(f"{side}{i}_qty"), 0.0) for i in range(1, n + 1)))


def imbalance(bid: float, ask: float) -> float:
    s = bid + ask
    if s <= 0:
        return float("nan")
    return (bid - ask) / s


def static_from_row(row: pd.Series) -> dict[str, float]:
    bid1 = _f(row.get("bid1_qty"), 0.0)
    ask1 = _f(row.get("ask1_qty"), 0.0)
    b3, a3 = depth_sum(row, "bid", 3), depth_sum(row, "ask", 3)
    b5, a5 = depth_sum(row, "bid", 5), depth_sum(row, "ask", 5)
    b10, a10 = depth_sum(row, "bid", 10), depth_sum(row, "ask", 10)
    bp1, ap1 = _f(row.get("bid1_px")), _f(row.get("ask1_px"))
    mid = (bp1 + ap1) / 2.0 if bp1 == bp1 and ap1 == ap1 else float("nan")
    spread = (ap1 - bp1) if bp1 == bp1 and ap1 == ap1 else float("nan")
    spread_bps = (spread / mid * 10000.0) if mid and mid == mid and mid > 0 and spread == spread else float("nan")
    if bid1 + ask1 > 0 and bp1 == bp1 and ap1 == ap1:
        micro = (ap1 * bid1 + bp1 * ask1) / (bid1 + ask1)
    else:
        micro = float("nan")
    micro_mid = (micro - mid) if micro == micro and mid == mid else float("nan")
    # slopes: qty vs level index
    bid_qtys = [_f(row.get(f"bid{i}_qty"), 0.0) for i in range(1, 11)]
    ask_qtys = [_f(row.get(f"ask{i}_qty"), 0.0) for i in range(1, 11)]
    xs = np.arange(1, 11, dtype=float)
    bid_slope = float(np.polyfit(xs, bid_qtys, 1)[0]) if sum(bid_qtys) > 0 else float("nan")
    ask_slope = float(np.polyfit(xs, ask_qtys, 1)[0]) if sum(ask_qtys) > 0 else float("nan")
    conc_l1_l5 = (bid1 + ask1) / (b5 + a5) if (b5 + a5) > 0 else float("nan")
    conc_l1_l10 = (bid1 + ask1) / (b10 + a10) if (b10 + a10) > 0 else float("nan")
    bid_wall = max(bid_qtys) if bid_qtys else 0.0
    ask_wall = max(ask_qtys) if ask_qtys else 0.0
    bid_wall_lvl = int(np.argmax(bid_qtys)) + 1 if bid_qtys else 0
    ask_wall_lvl = int(np.argmax(ask_qtys)) + 1 if ask_qtys else 0
    return {
        "bid_depth_l1": bid1,
        "ask_depth_l1": ask1,
        "bid_depth_l3": b3,
        "ask_depth_l3": a3,
        "bid_depth_l5": b5,
        "ask_depth_l5": a5,
        "bid_depth_l10": b10,
        "ask_depth_l10": a10,
        "imbalance_l1": imbalance(bid1, ask1),
        "imbalance_l3": imbalance(b3, a3),
        "imbalance_l5": imbalance(b5, a5),
        "imbalance_l10": imbalance(b10, a10),
        "depth_ratio_l5": (b5 / a5) if a5 > 0 else float("nan"),
        "total_visible_depth_l10": b10 + a10,
        "best_bid_size": bid1,
        "best_ask_size": ask1,
        "spread_yen": spread,
        "spread_bps": spread_bps,
        "mid_price": mid,
        "microprice": micro,
        "microprice_minus_mid": micro_mid,
        "microprice_above_mid": 1.0 if micro_mid == micro_mid and micro_mid > 0 else 0.0,
        "bid_depth_slope": bid_slope,
        "ask_depth_slope": ask_slope,
        "depth_conc_l1_l5": conc_l1_l5,
        "depth_conc_l1_l10": conc_l1_l10,
        "bid_wall_qty": bid_wall,
        "ask_wall_qty": ask_wall,
        "bid_wall_level": float(bid_wall_lvl),
        "ask_wall_level": float(ask_wall_lvl),
        "top_heavy": 1.0 if conc_l1_l5 == conc_l1_l5 and conc_l1_l5 >= 0.5 else 0.0,
    }


def window_dynamics(df: pd.DataFrame) -> dict[str, float]:
    """df: events with recv_epoch <= entry, within window, sorted."""
    if df is None or len(df) == 0:
        return {}
    d = df.sort_values("recv_epoch")
    first, last = d.iloc[0], d.iloc[-1]
    s0, s1 = static_from_row(first), static_from_row(last)
    out: dict[str, float] = {
        "bid_depth_l5_chg": s1["bid_depth_l5"] - s0["bid_depth_l5"],
        "ask_depth_l5_chg": s1["ask_depth_l5"] - s0["ask_depth_l5"],
        "imbalance_l5_chg": (
            s1["imbalance_l5"] - s0["imbalance_l5"]
            if s1["imbalance_l5"] == s1["imbalance_l5"] and s0["imbalance_l5"] == s0["imbalance_l5"]
            else float("nan")
        ),
        "spread_bps_chg": (
            s1["spread_bps"] - s0["spread_bps"]
            if s1["spread_bps"] == s1["spread_bps"] and s0["spread_bps"] == s0["spread_bps"]
            else float("nan")
        ),
        "microprice_chg": (
            s1["microprice"] - s0["microprice"]
            if s1["microprice"] == s1["microprice"] and s0["microprice"] == s0["microprice"]
            else float("nan")
        ),
        "board_update_count": float(len(d)),
    }
    # best bid/ask up/down ticks
    bb = d["bid1_px"].to_numpy(dtype=float)
    ba = d["ask1_px"].to_numpy(dtype=float)
    px = d["current_price"].to_numpy(dtype=float)
    bid_up = bid_dn = ask_up = ask_dn = 0
    for i in range(1, len(bb)):
        if bb[i] == bb[i] and bb[i - 1] == bb[i - 1]:
            if bb[i] > bb[i - 1]:
                bid_up += 1
            elif bb[i] < bb[i - 1]:
                bid_dn += 1
        if ba[i] == ba[i] and ba[i - 1] == ba[i - 1]:
            if ba[i] > ba[i - 1]:
                ask_up += 1
            elif ba[i] < ba[i - 1]:
                ask_dn += 1
    out.update(
        {
            "best_bid_upticks": float(bid_up),
            "best_bid_downticks": float(bid_dn),
            "best_ask_upticks": float(ask_up),
            "best_ask_downticks": float(ask_dn),
        }
    )
    # OFI_PROXY + add/cancel approx at L1
    bid_add = bid_cancel = ask_add = ask_cancel = 0.0
    ofi = 0.0
    for i in range(1, len(d)):
        prev, cur = d.iloc[i - 1], d.iloc[i]
        pb, qb = _f(prev.get("bid1_px")), _f(prev.get("bid1_qty"), 0.0)
        cb, cqb = _f(cur.get("bid1_px")), _f(cur.get("bid1_qty"), 0.0)
        pa, qa = _f(prev.get("ask1_px")), _f(prev.get("ask1_qty"), 0.0)
        ca, cqa = _f(cur.get("ask1_px")), _f(cur.get("ask1_qty"), 0.0)
        if pb == pb and cb == cb and abs(pb - cb) < 1e-9:
            dq = cqb - qb
            if dq > 0:
                bid_add += dq
            else:
                bid_cancel += -dq
            ofi += dq
        elif cb == cb and (pb != pb or cb > pb):
            ofi += cqb
            bid_add += cqb
        elif pb == pb and (cb != cb or cb < pb):
            ofi -= qb
            bid_cancel += qb
        if pa == pa and ca == ca and abs(pa - ca) < 1e-9:
            dq = cqa - qa
            if dq > 0:
                ask_add += dq
            else:
                ask_cancel += -dq
            ofi -= dq
        elif ca == ca and (pa != pa or ca < pa):
            ofi -= cqa
            ask_add += cqa
        elif pa == pa and (ca != ca or ca > pa):
            ofi += qa
            ask_cancel += qa
    span = max(1e-6, float(d.iloc[-1]["recv_epoch"] - d.iloc[0]["recv_epoch"]))
    uniq_px = float(pd.Series(px).dropna().nunique())
    same_price_churn = float(len(d) - uniq_px) if len(d) else 0.0
    price_updates = 0
    for i in range(1, len(px)):
        if px[i] == px[i] and px[i - 1] == px[i - 1] and abs(px[i] - px[i - 1]) > 1e-9:
            price_updates += 1
    out.update(
        {
            "ofi_proxy": ofi,
            "bid_add": bid_add,
            "bid_cancel": bid_cancel,
            "ask_add": ask_add,
            "ask_cancel": ask_cancel,
            "net_bid_pressure": bid_add - bid_cancel,
            "net_ask_pressure": ask_add - ask_cancel,
            "cancellation_imbalance": (bid_cancel - ask_cancel) / max(1.0, bid_cancel + ask_cancel),
            "updates_per_sec": float(len(d)) / span,
            "unique_price_changes": uniq_px,
            "same_price_board_churn": same_price_churn,
            "price_update_count": float(price_updates),
            "board_price_update_ratio": float(len(d)) / max(1.0, float(price_updates)),
        }
    )
    # wall appear/disappear (ask wall near = level 1-2 large)
    ask_wall_near0 = 1.0 if s0.get("ask_wall_level", 99) <= 2 and s0.get("ask_wall_qty", 0) > s0.get("ask_depth_l5", 1) * 0.4 else 0.0
    ask_wall_near1 = 1.0 if s1.get("ask_wall_level", 99) <= 2 and s1.get("ask_wall_qty", 0) > s1.get("ask_depth_l5", 1) * 0.4 else 0.0
    bid_wall0 = 1.0 if s0.get("bid_wall_level", 99) <= 2 and s0.get("bid_wall_qty", 0) > s0.get("bid_depth_l5", 1) * 0.4 else 0.0
    bid_wall1 = 1.0 if s1.get("bid_wall_level", 99) <= 2 and s1.get("bid_wall_qty", 0) > s1.get("bid_depth_l5", 1) * 0.4 else 0.0
    out["ask_wall_near_appear"] = 1.0 if ask_wall_near1 > ask_wall_near0 else 0.0
    out["ask_wall_near_disappear"] = 1.0 if ask_wall_near0 > ask_wall_near1 else 0.0
    out["bid_wall_disappear"] = 1.0 if bid_wall0 > bid_wall1 else 0.0
    out["bid_wall_appear"] = 1.0 if bid_wall1 > bid_wall0 else 0.0
    out["microprice_above_mid_persistent"] = (
        1.0
        if s0.get("microprice_above_mid", 0) > 0 and s1.get("microprice_above_mid", 0) > 0
        else 0.0
    )
    out["ask_depletion_bid_replenish"] = (
        1.0
        if out["ask_depth_l5_chg"] < 0 and out["bid_depth_l5_chg"] > 0
        else 0.0
    )
    return out


def nearest_backward(df: pd.DataFrame, epoch: float) -> Optional[pd.Series]:
    sub = df[df["recv_epoch"] <= epoch]
    if sub.empty:
        return None
    return sub.iloc[-1]


def post_return(df: pd.DataFrame, entry_epoch: float, entry_price: float, horizon: float) -> float:
    if not (entry_price == entry_price) or entry_price <= 0:
        return float("nan")
    target = entry_epoch + horizon
    sub = df[(df["recv_epoch"] > entry_epoch) & (df["recv_epoch"] <= target)]
    if sub.empty:
        # last at or before target after entry
        sub2 = df[df["recv_epoch"] <= target]
        if sub2.empty or float(sub2.iloc[-1]["recv_epoch"]) <= entry_epoch:
            return float("nan")
        px = _f(sub2.iloc[-1]["current_price"])
    else:
        px = _f(sub.iloc[-1]["current_price"])
    if not (px == px) or px <= 0:
        return float("nan")
    return (px / entry_price - 1.0) * 100.0


def mfe_mae_horizon(df: pd.DataFrame, entry_epoch: float, entry_price: float, horizon: float) -> tuple[float, float]:
    if not (entry_price == entry_price) or entry_price <= 0:
        return float("nan"), float("nan")
    sub = df[(df["recv_epoch"] > entry_epoch) & (df["recv_epoch"] <= entry_epoch + horizon)]
    if sub.empty:
        return float("nan"), float("nan")
    rets = (sub["current_price"].astype(float) / entry_price - 1.0) * 100.0
    return float(rets.max()), float(rets.min())


def safe_auc(y: np.ndarray, s: np.ndarray) -> float:
    mask = np.isfinite(s) & np.isfinite(y)
    y2, s2 = y[mask], s[mask]
    if len(y2) < 5 or len(np.unique(y2)) < 2:
        return float("nan")
    try:
        return float(roc_auc_score(y2, s2))
    except Exception:
        return float("nan")


def effect_size(a: Sequence[float], b: Sequence[float]) -> float:
    aa = [x for x in a if x == x]
    bb = [x for x in b if x == x]
    if len(aa) < 2 or len(bb) < 2:
        return float("nan")
    ma, mb = statistics.mean(aa), statistics.mean(bb)
    sa, sb = statistics.pstdev(aa), statistics.pstdev(bb)
    pooled = math.sqrt((sa**2 + sb**2) / 2.0) if (sa + sb) > 0 else float("nan")
    if not pooled or pooled != pooled or pooled == 0:
        return float("nan")
    return (ma - mb) / pooled


def portfolio_replay(rows: Sequence[dict[str, Any]], blocked: set[int], max_concurrent: int = 5) -> dict[str, Any]:
    legs = sorted(enumerate(rows), key=lambda iv: iv[1]["entry_epoch"])
    kept = []
    open_pos: list[dict[str, Any]] = []
    for idx, leg in legs:
        if idx in blocked:
            continue
        et = leg["entry_epoch"]
        open_pos = [o for o in open_pos if o["exit_epoch"] > et]
        if len(open_pos) >= max_concurrent:
            continue
        kept.append(leg)
        open_pos.append(leg)
    pnls = [k["pnl_pct"] for k in kept]
    yens = [p * 100.0 for p in pnls]
    gp = sum(p for p in pnls if p > 0)
    gl = abs(sum(p for p in pnls if p < 0))
    pf = 999.0 if gl <= 0 and gp > 0 else (0.0 if gl <= 0 else gp / gl)
    equity = peak = mdd = 0.0
    for k in sorted(kept, key=lambda x: x["exit_epoch"]):
        equity += k["pnl_pct"] * 100.0
        peak = max(peak, equity)
        mdd = min(mdd, equity - peak)
    return {
        "n": len(kept),
        "pnl_yen_100": round(sum(yens), 4),
        "PF": round(pf, 4),
        "max_drawdown": round(mdd, 4),
        "stop_n": sum(1 for k in kept if k["exit_reason"] == "stop_hit"),
        "np_n": sum(1 for k in kept if k["exit_reason"] == "no_progress_exit"),
        "win_n": sum(1 for k in kept if k["pnl_pct"] > 0),
    }


def build_slim_board(entries: list[dict[str, Any]]) -> tuple[pd.DataFrame, dict[str, Any]]:
    symbols = sorted({e["symbol_code"] for e in entries})
    t0 = min(e["entry_epoch"] for e in entries) - 320
    t1 = max(max(e["exit_epoch"], e["entry_epoch"] + 620) for e in entries) + 5
    parts = [p for p in sorted(CAPTURE.glob("push_part_*.jsonl")) if p.stat().st_size > 0]
    CACHE.mkdir(parents=True, exist_ok=True)
    jobs = []
    for i, p in enumerate(parts):
        jobs.append((str(p), symbols, t0, t1, str(CACHE / f"slim_{i:02d}.parquet")))

    stats = []
    with ProcessPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(_extract_part, j): j[0] for j in jobs}
        for fut in as_completed(futs):
            stats.append(fut.result())

    frames = []
    for s in sorted(stats, key=lambda x: x.get("part", "")):
        op = s.get("out")
        if op and Path(op).is_file():
            try:
                df = pd.read_parquet(op)
                if len(df):
                    frames.append(df)
            except Exception:
                pass
    board = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if len(board):
        board = board.sort_values(["symbol", "recv_epoch", "sequence"]).drop_duplicates(
            ["symbol", "payload_hash", "recv_epoch"], keep="first"
        )
        board_path = OUT / "_board_slim_20260716.parquet"
        board.to_parquet(board_path, index=False)
    quality = {
        "parts_processed": stats,
        "symbols_requested": symbols,
        "rows": int(len(board)),
        "t0": t0,
        "t1": t1,
        "disk_used_pct_after_extract": round(100 * shutil.disk_usage(NATIVE).used / shutil.disk_usage(NATIVE).total, 2),
    }
    return board, quality


def compute_dataset(entries: list[dict[str, Any]], board: pd.DataFrame) -> pd.DataFrame:
    rows = []
    by_sym = {s: g.sort_values("recv_epoch").reset_index(drop=True) for s, g in board.groupby("symbol")} if len(board) else {}
    for e in entries:
        sym = e["symbol_code"]
        df = by_sym.get(sym, pd.DataFrame())
        ee = e["entry_epoch"]
        feat: dict[str, Any] = {**e}
        snap = nearest_backward(df, ee) if len(df) else None
        feat["board_sync_ok"] = snap is not None
        if snap is not None:
            lag = ee - float(snap["recv_epoch"])
            feat["board_sync_lag_sec"] = lag
            feat["sync_clock"] = "received_at_jst_backward"
            st = static_from_row(snap)
            for k, v in st.items():
                feat[f"board_at_entry_{k}"] = v
            # ages from capture clocks
            cpt = _parse_ts(snap.get("current_price_time"))
            bt = _parse_ts(snap.get("board_time"))
            et = _parse_ts(e["entry_time"])
            feat["capture_price_age_sec"] = (et - cpt).total_seconds() if et and cpt else float("nan")
            feat["capture_board_age_sec"] = (et - bt).total_seconds() if et and bt else float("nan")
            # microprice direction vs entry
            if st["microprice"] == st["microprice"] and e["entry_price"] == e["entry_price"]:
                feat["board_at_entry_micro_vs_entry_bps"] = (st["microprice"] / e["entry_price"] - 1.0) * 10000.0
        else:
            feat["board_sync_lag_sec"] = float("nan")

        for w in PRE_WINDOWS:
            if len(df) == 0 or snap is None:
                continue
            wdf = df[(df["recv_epoch"] > ee - w) & (df["recv_epoch"] <= ee)]
            dyn = window_dynamics(wdf)
            for k, v in dyn.items():
                feat[f"board_{w}s_{k}"] = v
            # end-of-window static snapshot (still <= entry)
            if len(wdf):
                stw = static_from_row(wdf.iloc[-1])
                feat[f"board_{w}s_imbalance_l5"] = stw["imbalance_l5"]
                feat[f"board_{w}s_imbalance_l10"] = stw["imbalance_l10"]
                feat[f"board_{w}s_microprice_above_mid"] = stw["microprice_above_mid"]

        # post outcomes (labels only; not used as features)
        for w in POST_WINDOWS:
            feat[f"return_{w}s"] = post_return(df, ee, e["entry_price"], float(w)) if len(df) else float("nan")
        mfe5, mae5 = mfe_mae_horizon(df, ee, e["entry_price"], 300.0) if len(df) else (float("nan"), float("nan"))
        mfe10, mae10 = mfe_mae_horizon(df, ee, e["entry_price"], 600.0) if len(df) else (float("nan"), float("nan"))
        feat["MFE_5m"] = mfe5
        feat["MAE_5m"] = mae5
        feat["MFE_10m"] = mfe10
        feat["MAE_10m"] = mae10
        feat["early_up"] = int(mfe5 == mfe5 and mfe5 >= 0.3)
        feat["strong_up"] = int(mfe10 == mfe10 and mfe10 >= 0.6)
        feat["early_adverse"] = int(mae5 == mae5 and mae5 <= -0.3)
        feat["early_stop_like"] = int(mae5 == mae5 and mae5 <= -0.6)
        feat["label_no_progress"] = int(e["exit_reason"] == "no_progress_exit")
        feat["label_stop"] = int(e["exit_reason"] == "stop_hit")
        feat["label_trailing_winner"] = int(e["exit_reason"] == "trailing_mfe_exit")
        feat["label_winner"] = int(e["pnl_pct"] > 0)
        feat["stale_price"] = int(e["price_age_sec"] == e["price_age_sec"] and e["price_age_sec"] >= 60)
        rows.append(feat)
    return pd.DataFrame(rows)


def analyze(ds: pd.DataFrame) -> dict[str, Any]:
    # feature columns
    feat_cols = [
        c
        for c in ds.columns
        if c.startswith("board_")
        or c
        in (
            "score_v2",
            "momentum",
            "entry_imbalance_percentile",
            "spread_bps",
            "update_count",
            "price_age_sec",
            "board_age_sec",
        )
    ]
    labels = {
        "winner": "label_winner",
        "stop": "label_stop",
        "no_progress": "label_no_progress",
        "trailing_winner": "label_trailing_winner",
        "early_up": "early_up",
        "early_adverse": "early_adverse",
    }
    auc_rows = []
    for feat in feat_cols:
        s = ds[feat].to_numpy(dtype=float)
        for lname, lcol in labels.items():
            y = ds[lcol].to_numpy(dtype=float)
            auc = safe_auc(y, s)
            # also inverted
            auc_inv = safe_auc(y, -s)
            best = auc if (auc == auc and (auc_inv != auc_inv or auc >= auc_inv)) else auc_inv
            direction = "high" if best == auc else "low"
            if best == best:
                auc_rows.append(
                    {
                        "feature": feat,
                        "label": lname,
                        "auc": round(best, 4),
                        "direction": direction,
                        "n": int(np.isfinite(s).sum()),
                    }
                )
    auc_df = pd.DataFrame(auc_rows)
    top10 = []
    if len(auc_df):
        # rank by max abs separation across stop/np/winner/early
        pivot = auc_df.groupby("feature")["auc"].max().sort_values(ascending=False)
        top10 = [{"feature": f, "best_auc": float(a)} for f, a in pivot.head(10).items()]

    # group comparisons
    group_rows = []
    pairs = [
        ("winners_vs_losers", ds["label_winner"] == 1, ds["label_winner"] == 0),
        ("trailing_vs_stop", ds["label_trailing_winner"] == 1, ds["label_stop"] == 1),
        ("trailing_vs_np", ds["label_trailing_winner"] == 1, ds["label_no_progress"] == 1),
        ("early_up_vs_adverse", ds["early_up"] == 1, ds["early_adverse"] == 1),
        ("reentry_vs_first", ds["is_reentry"] == True, ds["is_reentry"] == False),
        ("stale_vs_fresh", ds["stale_price"] == 1, ds["stale_price"] == 0),
        ("OR_vs_PBv2", ds["route"] == "OR", ds["route"] == "PBV2"),
    ]
    key_feats = [
        "board_at_entry_imbalance_l5",
        "board_at_entry_imbalance_l10",
        "board_60s_ofi_proxy",
        "board_60s_imbalance_l5_chg",
        "board_60s_microprice_above_mid_persistent",
        "board_at_entry_ask_wall_qty",
        "entry_imbalance_percentile",
        "score_v2",
        "price_age_sec",
    ]
    for name, m_a, m_b in pairs:
        for feat in key_feats:
            if feat not in ds.columns:
                continue
            a = ds.loc[m_a, feat].astype(float).tolist()
            b = ds.loc[m_b, feat].astype(float).tolist()
            aa = [x for x in a if x == x]
            bb = [x for x in b if x == x]
            group_rows.append(
                {
                    "comparison": name,
                    "feature": feat,
                    "n_a": len(aa),
                    "n_b": len(bb),
                    "median_a": statistics.median(aa) if aa else float("nan"),
                    "median_b": statistics.median(bb) if bb else float("nan"),
                    "mean_a": statistics.mean(aa) if aa else float("nan"),
                    "mean_b": statistics.mean(bb) if bb else float("nan"),
                    "effect_size": effect_size(aa, bb),
                }
            )

    # incremental AUC: baseline vs board vs combined (simple average rank score)
    def _z(col: str) -> np.ndarray:
        x = ds[col].to_numpy(dtype=float)
        m = np.nanmean(x)
        s = np.nanstd(x)
        if not s or s != s or s == 0:
            return np.zeros(len(x))
        return np.nan_to_num((x - m) / s, nan=0.0)

    base_cols = [c for c in ["score_v2", "momentum", "entry_imbalance_percentile", "spread_bps", "update_count", "price_age_sec"] if c in ds.columns]
    board_cols = [c for c in ["board_at_entry_imbalance_l5", "board_at_entry_imbalance_l10", "board_60s_ofi_proxy", "board_60s_imbalance_l5_chg", "board_60s_ask_depletion_bid_replenish", "board_at_entry_microprice_above_mid"] if c in ds.columns]
    y_stop = ds["label_stop"].to_numpy(dtype=float)
    y_win = ds["label_winner"].to_numpy(dtype=float)
    y_np = ds["label_no_progress"].to_numpy(dtype=float)
    base_score = sum(_z(c) for c in base_cols) if base_cols else np.zeros(len(ds))
    # price_age / spread higher often worse → invert those in baseline for winner
    board_score = sum(_z(c) for c in board_cols) if board_cols else np.zeros(len(ds))
    # for stop: higher board ask wall / negative ofi may predict stop — use -board for stop auc separately
    incr_rows = []
    for lname, y, invert_board_for in (
        ("winner", y_win, False),
        ("stop", y_stop, True),
        ("no_progress", y_np, True),
    ):
        bsc = -board_score if invert_board_for else board_score
        # baseline: for stop invert score_v2 path via -base for consistency when predicting bad outcomes
        base = -base_score if lname != "winner" else base_score
        comb = base + bsc
        ba = safe_auc(y, base)
        bo = safe_auc(y, bsc)
        co = safe_auc(y, comb)
        incr_rows.append(
            {
                "label": lname,
                "baseline_auc": ba,
                "board_only_auc": bo,
                "combined_auc": co,
                "incremental_auc": (co - ba) if co == co and ba == ba else float("nan"),
                "n": int(len(y)),
                "base_features": "|".join(base_cols),
                "board_features": "|".join(board_cols),
            }
        )

    # candidate rules (max 5), descriptive thresholds from medians of winners — not grid-optimized
    med_ofi = float(ds["board_60s_ofi_proxy"].median()) if "board_60s_ofi_proxy" in ds else 0.0
    med_imb_chg = float(ds["board_60s_imbalance_l5_chg"].median()) if "board_60s_imbalance_l5_chg" in ds else 0.0

    def rule_mask(name: str) -> pd.Series:
        if name == "persistent_bid_pressure":
            return (ds.get("board_60s_ofi_proxy", 0) > max(0.0, med_ofi)) & (
                ds.get("board_at_entry_imbalance_l5", 0) > 0
            )
        if name == "ask_depletion_with_bid_replenishment":
            return ds.get("board_60s_ask_depletion_bid_replenish", 0) >= 1
        if name == "microprice_above_mid_persistent":
            return ds.get("board_60s_microprice_above_mid_persistent", 0) >= 1
        if name == "l5_imbalance_rising":
            return ds.get("board_60s_imbalance_l5_chg", 0) > max(0.0, med_imb_chg)
        if name == "stale_price_board_only_churn":
            return (
                (ds["price_age_sec"] >= 60)
                & (ds.get("board_60s_board_update_count", 0) >= 5)
                & (ds.get("board_60s_price_update_count", 0) <= 1)
            )
        return pd.Series([False] * len(ds))

    # Rules as REJECT filters for adverse: block when NOT good signal / or block when bad signal
    # Evaluate both keep-if-true (require signal) style
    cand_rows = []
    replay_rows = []
    entries = ds.to_dict(orient="records")
    base_port = portfolio_replay(entries, set())
    for name in [
        "persistent_bid_pressure",
        "ask_depletion_with_bid_replenishment",
        "microprice_above_mid_persistent",
        "l5_imbalance_rising",
        "stale_price_board_only_churn",
    ]:
        m = rule_mask(name).fillna(False)
        # require-signal: block when rule false
        blocked = {i for i, v in enumerate(m.tolist()) if not v}
        # for stale_price_board_only_churn: block when rule TRUE (reject bad)
        if name == "stale_price_board_only_churn":
            blocked = {i for i, v in enumerate(m.tolist()) if v}
            mode = "reject_if_true"
        else:
            mode = "require_signal"
        port = portfolio_replay(entries, blocked)
        kept_idx = [i for i in range(len(entries)) if i not in blocked]
        # approximate kept via replay n may differ due to CAP — use blocked set for capture stats on intended filter
        intend_keep = [entries[i] for i in range(len(entries)) if i not in blocked]
        bw = sum(1 for i in blocked if entries[i]["pnl_pct"] > 0)
        bl = sum(1 for i in blocked if entries[i]["pnl_pct"] <= 0)
        ww = sum(1 for r in intend_keep if r["pnl_pct"] > 0)
        ll = sum(1 for r in intend_keep if r["pnl_pct"] <= 0)
        rem_pnl = sum(entries[i]["pnl_pct"] * 100 for i in range(len(entries)) if i not in blocked) - sum(
            e["pnl_pct"] * 100 for e in entries
        )
        cand_rows.append(
            {
                "rule": name,
                "mode": mode,
                "n_target_flag_true": int(m.sum()),
                "n_blocked": len(blocked),
                "winner_capture_kept": ww,
                "loser_capture_kept": ll,
                "blocked_winners": bw,
                "blocked_losers": bl,
                "delta_pnl_trade_removal": round(rem_pnl, 4),
                "note": "1-day research only; not for mainline",
            }
        )
        replay_rows.append(
            {
                "rule": name,
                "mode": mode,
                **port,
                "baseline_pnl": base_port["pnl_yen_100"],
                "delta_pnl_cap_replay": round(port["pnl_yen_100"] - base_port["pnl_yen_100"], 4),
                "delta_stop": port["stop_n"] - base_port["stop_n"],
                "delta_np": port["np_n"] - base_port["np_n"],
            }
        )

    return {
        "auc_rows": auc_rows,
        "top10": top10,
        "group_rows": group_rows,
        "incr_rows": incr_rows,
        "cand_rows": cand_rows,
        "replay_rows": replay_rows,
        "base_port": base_port,
    }


def trace_symbol(ds: pd.DataFrame, board: pd.DataFrame, code: str, pre_sec: float) -> list[dict[str, Any]]:
    rows = []
    sub_e = ds[ds["symbol_code"] == code]
    b = board[board["symbol"] == code].sort_values("recv_epoch") if len(board) else pd.DataFrame()
    for _, e in sub_e.iterrows():
        ee = float(e["entry_epoch"])
        wdf = b[(b["recv_epoch"] > ee - pre_sec) & (b["recv_epoch"] <= ee)] if len(b) else pd.DataFrame()
        # sample every ~nth row to keep trace small
        step = max(1, len(wdf) // 40) if len(wdf) else 1
        for i in range(0, len(wdf), step):
            r = wdf.iloc[i]
            st = static_from_row(r)
            rows.append(
                {
                    "symbol": code,
                    "phase": "pre_entry",
                    "entry_time": e["entry_time"],
                    "event_time": r.get("received_at_jst"),
                    "sec_before_entry": ee - float(r["recv_epoch"]),
                    "current_price": r.get("current_price"),
                    "imbalance_l5": st["imbalance_l5"],
                    "imbalance_l10": st["imbalance_l10"],
                    "microprice": st["microprice"],
                    "mid_price": st["mid_price"],
                    "bid1_qty": st["best_bid_size"],
                    "ask1_qty": st["best_ask_size"],
                    "bid_depth_l10": st["bid_depth_l10"],
                    "ask_depth_l10": st["ask_depth_l10"],
                    "ask_wall_qty": st["ask_wall_qty"],
                    "bid_wall_qty": st["bid_wall_qty"],
                    "exit_reason": e["exit_reason"],
                    "is_reentry": e["is_reentry"],
                    "price_age_sec": e["price_age_sec"],
                    "board_age_sec": e["board_age_sec"],
                }
            )
        # window summary at entry
        dyn = window_dynamics(wdf) if len(wdf) else {}
        rows.append(
            {
                "symbol": code,
                "phase": "entry_summary",
                "entry_time": e["entry_time"],
                "event_time": e["entry_time"],
                "sec_before_entry": 0,
                "ofi_proxy": dyn.get("ofi_proxy"),
                "imbalance_l5_chg": dyn.get("imbalance_l5_chg"),
                "board_update_count": dyn.get("board_update_count"),
                "price_update_count": dyn.get("price_update_count"),
                "ask_depletion_bid_replenish": dyn.get("ask_depletion_bid_replenish"),
                "microprice_above_mid_persistent": dyn.get("microprice_above_mid_persistent"),
                "same_price_board_churn": dyn.get("same_price_board_churn"),
                "exit_reason": e["exit_reason"],
                "pnl_pct": e["pnl_pct"],
                "is_reentry": e["is_reentry"],
                "price_age_sec": e["price_age_sec"],
                "board_age_sec": e["board_age_sec"],
                "entry_imbalance_percentile": e["entry_imbalance_percentile"],
            }
        )
    return rows


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    disk0 = shutil.disk_usage(NATIVE)
    used0 = 100 * disk0.used / disk0.total

    entries = load_entries()
    assert len(entries) == 44, len(entries)

    board, extract_q = build_slim_board(entries)
    ds = compute_dataset(entries, board)
    sync_ok = int(ds["board_sync_ok"].sum()) if "board_sync_ok" in ds else 0

    # capture quality from summary + extract
    summary = json.loads((CAPTURE / "capture_summary.json").read_text(encoding="utf-8"))
    status = json.loads((CAPTURE / "capture_status.json").read_text(encoding="utf-8"))
    # board level missing rate on slim
    if len(board):
        lvl_miss = float((board["bid1_qty"].isna() | (board["bid1_px"].isna())).mean())
        cpt_miss = float((board["current_price_time"].astype(str) == "").mean())
        bt_miss = float((board["board_time"].astype(str) == "").mean())
        # timestamp order violations
        order_viol = 0
        for _, g in board.groupby("symbol"):
            ep = g["recv_epoch"].to_numpy()
            order_viol += int(np.sum(ep[1:] < ep[:-1]))
    else:
        lvl_miss = cpt_miss = bt_miss = 1.0
        order_viol = -1

    dup_audit = {
        "summary_duplicate_payload_count": summary.get("duplicate_payload_count"),
        "extract_dups_skipped": sum(int(s.get("dups_skipped") or 0) for s in extract_q["parts_processed"]),
        "definition": "symbol + (CurrentPriceTime|received_at) + sha1(board/price key fields)",
        "note": "Feature extract skips duplicates; Capture summary reports 2386 duplicates",
    }
    _wj(OUT / "capture_duplicate_audit.json", dup_audit)

    board_quality = {
        "symbols_coverage_summary": summary.get("symbols_seen_count"),
        "event_count": status.get("event_count"),
        "malformed": summary.get("malformed_payload_count"),
        "dropped": status.get("dropped_event_count"),
        "zero_byte_parts_excluded": True,
        "slim_rows": int(len(board)),
        "board_level_missing_rate_slim": lvl_miss,
        "current_price_time_missing_rate_slim": cpt_miss,
        "board_time_missing_rate_slim": bt_miss,
        "timestamp_order_violations": order_viol,
        "disk_used_pct_start": round(used0, 2),
        "disk_used_pct_after": extract_q.get("disk_used_pct_after_extract"),
        "extract": {k: extract_q[k] for k in ("rows", "symbols_requested") if k in extract_q},
        "parts_nonzero": sum(1 for s in extract_q["parts_processed"] if not s.get("zero_byte")),
    }
    _wj(OUT / "board_data_quality.json", board_quality)

    analysis = analyze(ds)
    _wc(OUT / "board_feature_auc.csv", analysis["auc_rows"])
    _wc(OUT / "board_feature_group_comparison.csv", analysis["group_rows"])
    _wc(OUT / "board_incremental_value.csv", analysis["incr_rows"])
    _wc(OUT / "board_candidate_rules.csv", analysis["cand_rows"])
    _wc(OUT / "board_candidate_portfolio_replay.csv", analysis["replay_rows"])

    # feature dictionary
    dict_rows = []
    for prefix in ["board_at_entry_"] + [f"board_{w}s_" for w in PRE_WINDOWS]:
        for name, desc in [
            ("imbalance_l5", "L1-5 depth imbalance"),
            ("imbalance_l10", "L1-10 depth imbalance"),
            ("ofi_proxy", "L1 best-level OFI proxy (not true trade/cancel ID)"),
            ("microprice_above_mid_persistent", "micro>mid at window start and end"),
            ("ask_depletion_bid_replenish", "ask L5 down and bid L5 up in window"),
            ("board_update_count", "board events in window"),
            ("price_update_count", "current_price changes in window"),
            ("same_price_board_churn", "board updates without unique price change"),
        ]:
            dict_rows.append({"feature_prefix": prefix, "name": name, "full": prefix + name, "description": desc, "leak_safe": True})
    _wc(OUT / "board_feature_dictionary.csv", dict_rows)

    # dataset parquet (1 row / ENTRY)
    ds_path = OUT / "board_entry_dataset_20260716.parquet"
    ds.to_parquet(ds_path, index=False)

    schema = {
        "grain": "1 ENTRY = 1 row",
        "keys": ["trading_date", "symbol_code", "entry_time", "route"],
        "prefixes": [f"board_{w}s_" for w in PRE_WINDOWS] + ["board_at_entry_"],
        "outcome_cols": [
            "pnl_pct",
            "pnl_yen_100",
            "mfe_pct",
            "mae_pct",
            "hold_sec",
            "exit_reason",
            "return_30s",
            "return_60s",
            "return_120s",
            "return_300s",
            "return_600s",
            "MFE_5m",
            "MAE_5m",
            "early_up",
            "strong_up",
            "early_adverse",
            "early_stop_like",
            "label_no_progress",
            "label_stop",
            "label_trailing_winner",
            "label_winner",
        ],
        "data_quality_cols": ["board_sync_ok", "board_sync_lag_sec", "sync_clock"],
        "append_mode": "concat daily parquet partitions by trading_date",
        "trading_date": "20260716",
        "n_rows": int(len(ds)),
    }
    # add trading_date col for multi-day
    ds2 = ds.copy()
    ds2.insert(0, "trading_date", "20260716")
    ds2.to_parquet(ds_path, index=False)
    _wj(OUT / "multi_day_dataset_schema.json", schema)

    tr6506 = trace_symbol(ds, board, "6506", 300)
    tr6474 = trace_symbol(ds, board, "6474", 600)
    _wc(OUT / "symbol_6506_board_trace.csv", tr6506)
    _wc(OUT / "symbol_6474_board_trace.csv", tr6474)

    # 6506 / 6474 narrative metrics
    e6506 = ds[ds["symbol_code"] == "6506"].sort_values("entry_epoch")
    e6474 = ds[ds["symbol_code"] == "6474"]
    ans_6506 = {
        "entries": int(len(e6506)),
        "rows": e6506[
            [
                c
                for c in [
                    "entry_time",
                    "entry_price",
                    "is_reentry",
                    "exit_reason",
                    "pnl_pct",
                    "entry_imbalance_percentile",
                    "board_60s_ofi_proxy",
                    "board_60s_imbalance_l5_chg",
                    "board_60s_microprice_above_mid_persistent",
                    "board_5s_ofi_proxy",
                    "board_5s_imbalance_l5_chg",
                    "board_at_entry_imbalance_l5",
                ]
                if c in e6506.columns
            ]
        ].to_dict(orient="records"),
    }
    # 5s reENTRY: second or later with short gap
    re_eval = "insufficient"
    if len(e6506) >= 2:
        last = e6506.iloc[-1]
        # find same-price quick reentry
        for i in range(1, len(e6506)):
            prev, cur = e6506.iloc[i - 1], e6506.iloc[i]
            gap = float(cur["entry_epoch"] - prev["exit_epoch"]) if cur["entry_epoch"] == cur["entry_epoch"] else 9999
            if gap <= 10:
                ofi5 = _f(cur.get("board_5s_ofi_proxy"))
                ofi60 = _f(cur.get("board_60s_ofi_proxy"))
                imb_chg = _f(cur.get("board_5s_imbalance_l5_chg"))
                persistent = bool(_f(cur.get("board_60s_microprice_above_mid_persistent")) >= 1)
                re_eval = (
                    f"gap={gap:.1f}s same-price reENTRY: ofi_proxy_5s={ofi5}, ofi_60s={ofi60}, "
                    f"imb_l5_chg_5s={imb_chg}, micro_persist={persistent}. "
                    + (
                        "Persistent buy pressure NOT clearly supported beyond Board percentile / brief board churn."
                        if not (ofi5 > 0 and ofi60 > 0 and imb_chg > 0)
                        else "Some bid-pressure proxies positive; still 1-day anecdotal."
                    )
                )
                ans_6506["quick_reentry"] = {
                    "gap_sec": gap,
                    "ofi_5s": ofi5,
                    "ofi_60s": ofi60,
                    "imb_chg_5s": imb_chg,
                    "micro_persist": persistent,
                }
                break
    ans_6506["assessment"] = re_eval

    ans_6474 = {}
    if len(e6474):
        r = e6474.iloc[0]
        ans_6474 = {
            "entry_time": r["entry_time"],
            "price_age_sec": r["price_age_sec"],
            "board_age_sec": r["board_age_sec"],
            "update_count": r["update_count"],
            "board_300s_board_update_count": r.get("board_300s_board_update_count"),
            "board_300s_price_update_count": r.get("board_300s_price_update_count"),
            "board_300s_same_price_board_churn": r.get("board_300s_same_price_board_churn"),
            "board_300s_ofi_proxy": r.get("board_300s_ofi_proxy"),
            "board_at_entry_imbalance_l5": r.get("board_at_entry_imbalance_l5"),
            "board_at_entry_ask_wall_qty": r.get("board_at_entry_ask_wall_qty"),
            "exit_reason": r["exit_reason"],
            "pnl_pct": r["pnl_pct"],
            "assessment": (
                "Identifiable as board-fresh/price-stale if board_update_count high while price_update_count≈0 "
                "and price_age large; L1-10 churn/OFI_PROXY can flag quote-only activity without prints."
            ),
        }

    # top features vs current board
    cur_board_auc = [r for r in analysis["auc_rows"] if r["feature"] == "entry_imbalance_percentile"]
    new_board_auc = [
        r
        for r in analysis["auc_rows"]
        if r["feature"]
        in (
            "board_at_entry_imbalance_l5",
            "board_at_entry_imbalance_l10",
            "board_60s_ofi_proxy",
            "board_60s_imbalance_l5_chg",
        )
    ]

    incr = analysis["incr_rows"]
    add_auc_win = next((x for x in incr if x["label"] == "winner"), {})
    stop_feats = sorted(
        [r for r in analysis["auc_rows"] if r["label"] == "stop"],
        key=lambda x: x.get("auc") or 0,
        reverse=True,
    )[:5]
    np_feats = sorted(
        [r for r in analysis["auc_rows"] if r["label"] == "no_progress"],
        key=lambda x: x.get("auc") or 0,
        reverse=True,
    )[:5]
    win_feats = sorted(
        [r for r in analysis["auc_rows"] if r["label"] == "winner"],
        key=lambda x: x.get("auc") or 0,
        reverse=True,
    )[:5]

    # Verdict
    max_incr = max((abs(x.get("incremental_auc") or 0) for x in incr), default=0)
    best_auc = analysis["top10"][0]["best_auc"] if analysis["top10"] else 0
    if board_quality["board_level_missing_rate_slim"] > 0.2 or sync_ok < 30:
        verdict = "BOARD_CAPTURE_QUALITY_FAILED"
    elif sync_ok >= 40 and best_auc >= 0.65 and max_incr >= 0.05:
        verdict = "BOARD_SIGNAL_CANDIDATES_FOUND"
    elif sync_ok >= 40 and best_auc >= 0.58:
        verdict = "BOARD_INCREMENTAL_VALUE_WEAK"
    elif sync_ok < 20:
        verdict = "BOARD_DATA_INSUFFICIENT"
    else:
        verdict = "NO_ROBUST_BOARD_SIGNAL"

    answers = {
        "1_valid_entries": len(entries),
        "2_board_sync_ok": sync_ok,
        "3_board_level_missing_rate": board_quality["board_level_missing_rate_slim"],
        "4_top10_features": analysis["top10"],
        "5_vs_current_board": {
            "current_entry_imbalance_percentile_aucs": cur_board_auc,
            "new_l5_l10_ofi_aucs": new_board_auc,
        },
        "6_incremental_auc": incr,
        "7_stop_features": stop_feats,
        "8_no_progress_features": np_feats,
        "9_winner_features": win_feats,
        "10_candidate_rules": [c["rule"] for c in analysis["cand_rows"]],
        "11_cap_replay": analysis["replay_rows"],
        "12_6506_reentry": ans_6506,
        "13_6474_stale_board": ans_6474,
        "14_one_day_decisive": False,
        "15_days_needed": "10-20 trading days minimum before mainline consideration",
        "16_mainline_unchanged": True,
        "17_shadow_not_added": True,
        "18_submit_cancel": {"submit": 0, "cancel": 0},
    }

    report = {
        "phase": "687W37",
        "verdict": verdict,
        "answers": answers,
        "baseline_portfolio": analysis["base_port"],
        "PBv2_n": int((ds["route"] == "PBV2").sum()),
        "OR_n": int((ds["route"] == "OR").sum()),
        "generated_at": datetime.now(JST).isoformat(),
    }
    _wj(OUT / "phase687w37_report.json", report)

    _wj(
        OUT / "code_change_manifest.json",
        {
            "phase": "687W37",
            "mainline_changed": False,
            "shadow_added": False,
            "entry_exit_cap_or_changed": False,
            "files_added": [
                "scripts/phase687w37_live_board_entry_quality_audit.py",
                "results/reports/phase687w37_live_board_entry_quality/*",
            ],
            "capture_copied": False,
            "max_workers": MAX_WORKERS,
        },
    )
    _wj(
        OUT / "order_safety_audit.json",
        {"submit": 0, "cancel": 0, "live_order_path_touched": False},
    )

    decision = f"""# Phase687W37 Decision — Live Board Microstructure Entry Quality

## Verdict: `{verdict}`

### Completion answers
1. Valid ENTRY: **{answers['1_valid_entries']}** (PBv2={report['PBv2_n']}, OR={report['OR_n']})
2. Board sync OK: **{answers['2_board_sync_ok']}**
3. Board level missing rate (slim): **{answers['3_board_level_missing_rate']}**
4. Top10 features: `{json.dumps(answers['4_top10_features'], ensure_ascii=False)}`
5. vs current Board: see report `5_vs_current_board`
6. Incremental AUC: `{json.dumps(answers['6_incremental_auc'], ensure_ascii=False)}`
7. STOP features: `{json.dumps(answers['7_stop_features'], ensure_ascii=False)}`
8. no_progress features: `{json.dumps(answers['8_no_progress_features'], ensure_ascii=False)}`
9. Winner features: `{json.dumps(answers['9_winner_features'], ensure_ascii=False)}`
10. Candidate rules (max5): {answers['10_candidate_rules']}
11. CAP replay: see `board_candidate_portfolio_replay.csv`
12. 6506 5s reENTRY: {answers['12_6506_reentry'].get('assessment')}
13. 6474 stale board: {answers['13_6474_stale_board'].get('assessment')}
14. One-day decisive?: **No**
15. Days needed: **{answers['15_days_needed']}**
16. Mainline unchanged: **True**
17. Shadow not added: **True**
18. submit/cancel: **0/0**

### Method notes
- Sync clock: nearest **backward** `received_at_jst` (no future board in features)
- OFI labeled **OFI_PROXY** (add/cancel not fully identifiable)
- n=44 ENTRY units (not 717k board events)
- Disk: start {board_quality['disk_used_pct_start']}% → after {board_quality['disk_used_pct_after']}%; Capture not copied
"""
    _wm(OUT / "phase687w37_decision.md", decision)

    # cleanup part cache to limit disk (keep consolidated slim + dataset)
    try:
        if CACHE.is_dir():
            shutil.rmtree(CACHE)
    except Exception:
        pass

    print(json.dumps({"verdict": verdict, "answers": answers}, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
