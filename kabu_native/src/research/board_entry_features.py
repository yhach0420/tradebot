"""Shared board microstructure features for multi-day ENTRY dataset (research only).

Leak-safe: nearest backward received_at_jst; no future board in features.
OFI is OFI_PROXY (trade vs cancel not fully identifiable).
"""

from __future__ import annotations

import csv
import hashlib
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

JST = ZoneInfo("Asia/Tokyo")
PRE_WINDOWS = (5, 15, 30, 60, 120, 300)
POST_WINDOWS = (30, 60, 120, 300, 600)
MAX_WORKERS = 4


def parse_ts(v: Any) -> Optional[datetime]:
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


def fnum(v: Any, default: float = float("nan")) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def sym_code(sym: Any) -> str:
    s = str(sym or "").strip()
    return s[:-2] if s.endswith(".T") else s


def payload_hash(symbol: str, ts: str, op: dict[str, Any]) -> str:
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
    return fnum(lv.get("Qty"), 0.0) if isinstance(lv, dict) else 0.0


def _lvl_px(op: dict[str, Any], side: str, i: int) -> float:
    lv = op.get(f"{side}{i}")
    return fnum(lv.get("Price")) if isinstance(lv, dict) else float("nan")


def extract_part(args: tuple[str, list[str], float, float, str]) -> dict[str, Any]:
    part_path, symbols, t0_epoch, t1_epoch, out_path = args
    symset = set(symbols)
    rows: list[dict[str, Any]] = []
    malformed = dups = board_missing = cpt_missing = bt_missing = seen = kept = 0
    dup_keys: set[str] = set()
    path = Path(part_path)
    if path.stat().st_size <= 0:
        return {"part": path.name, "kept": 0, "seen": 0, "zero_byte": True, "dups_skipped": 0}

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
            sym = sym_code(o.get("symbol"))
            if sym not in symset:
                continue
            recv = parse_ts(o.get("received_at_jst"))
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
            h = payload_hash(sym, ts_key, op if isinstance(op, dict) else {})
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
                "current_price": fnum(o.get("current_price") or op.get("CurrentPrice")),
                "current_price_time": cpt or "",
                "board_time": board_t or "",
                "trading_volume": fnum(o.get("trading_volume") or op.get("TradingVolume"), 0.0),
                "trading_value": fnum(o.get("trading_value") or op.get("TradingValue"), 0.0),
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
    pd.DataFrame(rows).to_parquet(outp, index=False)
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


def load_accepted_entries(session_dir: Path) -> list[dict[str, Any]]:
    acc: list[dict[str, Any]] = []
    exits: list[dict[str, Any]] = []
    ev = session_dir / "small_paper_events.jsonl"
    if not ev.is_file():
        return []
    with ev.open(encoding="utf-8") as fh:
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
    st_path = session_dir / "structural_trades.csv"
    if st_path.is_file():
        with st_path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                struct[(str(row.get("symbol")), str(row.get("entry_time")))] = row

    by_sym: dict[str, list[dict[str, Any]]] = {}
    for a in sorted(acc, key=lambda x: parse_ts(x.get("entry_time")) or datetime.min.replace(tzinfo=JST)):
        by_sym.setdefault(str(a.get("symbol")), []).append(a)
    exit_q: dict[str, list[dict[str, Any]]] = {}
    for e in sorted(exits, key=lambda x: parse_ts(x.get("exit_time")) or datetime.min.replace(tzinfo=JST)):
        exit_q.setdefault(str(e.get("symbol")), []).append(e)

    first_seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for sym, entries in by_sym.items():
        outs = exit_q.get(sym, [])
        for i, a in enumerate(entries):
            e = outs[i] if i < len(outs) else {}
            et = str(a.get("entry_time") or "")
            xt = str(e.get("exit_time") or e.get("event_time") or "")
            st = struct.get((sym, et), {})
            route = str(a.get("entry_type") or "PBV2")
            route = "OR" if route.upper().startswith("OR") else "PBV2"
            is_re = sym in first_seen
            first_seen.add(sym)
            tea, txa = parse_ts(et), parse_ts(xt)
            hold = (txa - tea).total_seconds() if tea and txa else fnum(st.get("hold_duration_sec"), 0.0)
            pnl = fnum(e.get("pnl_pct") or st.get("realized_pnl_pct"), 0.0)
            out.append(
                {
                    "symbol": sym,
                    "symbol_code": sym_code(sym),
                    "entry_time": et,
                    "exit_time": xt,
                    "entry_epoch": tea.timestamp() if tea else float("nan"),
                    "exit_epoch": txa.timestamp() if txa else float("nan"),
                    "entry_price": fnum(a.get("current_price") or st.get("entry_price")),
                    "exit_price": fnum(e.get("exit_price") or st.get("close_price")),
                    "route": route,
                    "score_v2": fnum(a.get("entry_expectancy_score_v2")),
                    "momentum": fnum(a.get("momentum_continuation_score") or a.get("entry_momentum_score")),
                    "quality": fnum(a.get("continuation_quality_score")),
                    "board_mid": bool(a.get("entry_board_mid_token_active")),
                    "entry_imbalance_percentile": fnum(a.get("entry_imbalance_percentile")),
                    "entry_order_book_imbalance": fnum(a.get("entry_order_book_imbalance")),
                    "price_age_sec": fnum(a.get("price_age_sec")),
                    "board_age_sec": fnum(a.get("board_age_sec")),
                    "spread_bps": fnum(a.get("spread_bps")),
                    "update_count": fnum(
                        a.get("update_count_before_entry")
                        if a.get("update_count_before_entry") is not None
                        else a.get("update_count")
                    ),
                    "pnl_pct": pnl,
                    "pnl_yen_100": round(pnl * 100.0, 4),
                    "mfe_pct": fnum(st.get("mfe_pct") or e.get("peak_mfe_pct") or e.get("rolling_mfe_pct"), 0.0),
                    "mae_pct": fnum(st.get("mae_pct") or e.get("rolling_mae_pct"), 0.0),
                    "hold_sec": float(hold or 0.0),
                    "exit_reason": str(e.get("exit_reason") or st.get("close_reason") or ""),
                    "is_reentry": is_re,
                    "price_freshness_source": str(a.get("price_freshness_source") or ""),
                }
            )
    out.sort(key=lambda r: r["entry_epoch"] if r["entry_epoch"] == r["entry_epoch"] else 0)
    return out


def depth_sum(row: pd.Series, side: str, n: int) -> float:
    return float(sum(fnum(row.get(f"{side}{i}_qty"), 0.0) for i in range(1, n + 1)))


def imbalance(bid: float, ask: float) -> float:
    s = bid + ask
    return (bid - ask) / s if s > 0 else float("nan")


def static_from_row(row: pd.Series) -> dict[str, float]:
    bid1, ask1 = fnum(row.get("bid1_qty"), 0.0), fnum(row.get("ask1_qty"), 0.0)
    b3, a3 = depth_sum(row, "bid", 3), depth_sum(row, "ask", 3)
    b5, a5 = depth_sum(row, "bid", 5), depth_sum(row, "ask", 5)
    b10, a10 = depth_sum(row, "bid", 10), depth_sum(row, "ask", 10)
    bp1, ap1 = fnum(row.get("bid1_px")), fnum(row.get("ask1_px"))
    mid = (bp1 + ap1) / 2.0 if bp1 == bp1 and ap1 == ap1 else float("nan")
    spread = (ap1 - bp1) if bp1 == bp1 and ap1 == ap1 else float("nan")
    spread_bps = (spread / mid * 10000.0) if mid and mid == mid and mid > 0 and spread == spread else float("nan")
    micro = (
        (ap1 * bid1 + bp1 * ask1) / (bid1 + ask1)
        if bid1 + ask1 > 0 and bp1 == bp1 and ap1 == ap1
        else float("nan")
    )
    micro_mid = (micro - mid) if micro == micro and mid == mid else float("nan")
    bid_qtys = [fnum(row.get(f"bid{i}_qty"), 0.0) for i in range(1, 11)]
    ask_qtys = [fnum(row.get(f"ask{i}_qty"), 0.0) for i in range(1, 11)]
    xs = np.arange(1, 11, dtype=float)
    bid_slope = float(np.polyfit(xs, bid_qtys, 1)[0]) if sum(bid_qtys) > 0 else float("nan")
    ask_slope = float(np.polyfit(xs, ask_qtys, 1)[0]) if sum(ask_qtys) > 0 else float("nan")
    conc_l1_l5 = (bid1 + ask1) / (b5 + a5) if (b5 + a5) > 0 else float("nan")
    conc_l1_l10 = (bid1 + ask1) / (b10 + a10) if (b10 + a10) > 0 else float("nan")
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
        "bid_wall_qty": max(bid_qtys) if bid_qtys else 0.0,
        "ask_wall_qty": max(ask_qtys) if ask_qtys else 0.0,
        "bid_wall_level": float(int(np.argmax(bid_qtys)) + 1) if bid_qtys else 0.0,
        "ask_wall_level": float(int(np.argmax(ask_qtys)) + 1) if ask_qtys else 0.0,
        "top_heavy": 1.0 if conc_l1_l5 == conc_l1_l5 and conc_l1_l5 >= 0.5 else 0.0,
    }


def window_dynamics(df: pd.DataFrame) -> dict[str, float]:
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
    bb = d["bid1_px"].to_numpy(dtype=float)
    ba = d["ask1_px"].to_numpy(dtype=float)
    px = d["current_price"].to_numpy(dtype=float)
    bid_up = bid_dn = ask_up = ask_dn = 0
    for i in range(1, len(bb)):
        if bb[i] == bb[i] and bb[i - 1] == bb[i - 1]:
            bid_up += int(bb[i] > bb[i - 1])
            bid_dn += int(bb[i] < bb[i - 1])
        if ba[i] == ba[i] and ba[i - 1] == ba[i - 1]:
            ask_up += int(ba[i] > ba[i - 1])
            ask_dn += int(ba[i] < ba[i - 1])
    out.update(
        {
            "best_bid_upticks": float(bid_up),
            "best_bid_downticks": float(bid_dn),
            "best_ask_upticks": float(ask_up),
            "best_ask_downticks": float(ask_dn),
        }
    )
    bid_add = bid_cancel = ask_add = ask_cancel = ofi = 0.0
    for i in range(1, len(d)):
        prev, cur = d.iloc[i - 1], d.iloc[i]
        pb, qb = fnum(prev.get("bid1_px")), fnum(prev.get("bid1_qty"), 0.0)
        cb, cqb = fnum(cur.get("bid1_px")), fnum(cur.get("bid1_qty"), 0.0)
        pa, qa = fnum(prev.get("ask1_px")), fnum(prev.get("ask1_qty"), 0.0)
        ca, cqa = fnum(cur.get("ask1_px")), fnum(cur.get("ask1_qty"), 0.0)
        if pb == pb and cb == cb and abs(pb - cb) < 1e-9:
            dq = cqb - qb
            bid_add += max(dq, 0.0)
            bid_cancel += max(-dq, 0.0)
            ofi += dq
        elif cb == cb and (pb != pb or cb > pb):
            ofi += cqb
            bid_add += cqb
        elif pb == pb and (cb != cb or cb < pb):
            ofi -= qb
            bid_cancel += qb
        if pa == pa and ca == ca and abs(pa - ca) < 1e-9:
            dq = cqa - qa
            ask_add += max(dq, 0.0)
            ask_cancel += max(-dq, 0.0)
            ofi -= dq
        elif ca == ca and (pa != pa or ca < pa):
            ofi -= cqa
            ask_add += cqa
        elif pa == pa and (ca != ca or ca > pa):
            ofi += qa
            ask_cancel += qa
    span = max(1e-6, float(d.iloc[-1]["recv_epoch"] - d.iloc[0]["recv_epoch"]))
    uniq_px = float(pd.Series(px).dropna().nunique())
    price_updates = sum(
        1
        for i in range(1, len(px))
        if px[i] == px[i] and px[i - 1] == px[i - 1] and abs(px[i] - px[i - 1]) > 1e-9
    )
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
            "same_price_board_churn": float(len(d) - uniq_px) if len(d) else 0.0,
            "price_update_count": float(price_updates),
            "board_price_update_ratio": float(len(d)) / max(1.0, float(price_updates)),
            "ask_wall_near_appear": 0.0,
            "ask_wall_near_disappear": 0.0,
            "bid_wall_disappear": 0.0,
            "bid_wall_appear": 0.0,
            "microprice_above_mid_persistent": (
                1.0
                if s0.get("microprice_above_mid", 0) > 0 and s1.get("microprice_above_mid", 0) > 0
                else 0.0
            ),
            "ask_depletion_bid_replenish": (
                1.0 if out["ask_depth_l5_chg"] < 0 and out["bid_depth_l5_chg"] > 0 else 0.0
            ),
        }
    )
    return out


def nearest_backward(df: pd.DataFrame, epoch: float) -> Optional[pd.Series]:
    sub = df[df["recv_epoch"] <= epoch]
    return None if sub.empty else sub.iloc[-1]


def post_return(df: pd.DataFrame, entry_epoch: float, entry_price: float, horizon: float) -> float:
    if not (entry_price == entry_price) or entry_price <= 0 or len(df) == 0:
        return float("nan")
    target = entry_epoch + horizon
    sub = df[(df["recv_epoch"] > entry_epoch) & (df["recv_epoch"] <= target)]
    if sub.empty:
        sub2 = df[df["recv_epoch"] <= target]
        if sub2.empty or float(sub2.iloc[-1]["recv_epoch"]) <= entry_epoch:
            return float("nan")
        px = fnum(sub2.iloc[-1]["current_price"])
    else:
        px = fnum(sub.iloc[-1]["current_price"])
    if not (px == px) or px <= 0:
        return float("nan")
    return (px / entry_price - 1.0) * 100.0


def mfe_mae_horizon(
    df: pd.DataFrame, entry_epoch: float, entry_price: float, horizon: float
) -> tuple[float, float]:
    if not (entry_price == entry_price) or entry_price <= 0 or len(df) == 0:
        return float("nan"), float("nan")
    sub = df[(df["recv_epoch"] > entry_epoch) & (df["recv_epoch"] <= entry_epoch + horizon)]
    if sub.empty:
        return float("nan"), float("nan")
    rets = (sub["current_price"].astype(float) / entry_price - 1.0) * 100.0
    return float(rets.max()), float(rets.min())


def stream_slim_board(
    *,
    capture_dir: Path,
    entries: list[dict[str, Any]],
    cache_dir: Path,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if not entries:
        return pd.DataFrame(), {"rows": 0, "parts_processed": [], "dups_skipped": 0}
    symbols = sorted({e["symbol_code"] for e in entries})
    t0 = min(e["entry_epoch"] for e in entries) - 320
    t1 = max(max(e["exit_epoch"], e["entry_epoch"] + 620) for e in entries) + 5
    parts = [p for p in sorted(capture_dir.glob("push_part_*.jsonl")) if p.stat().st_size > 0]
    cache_dir.mkdir(parents=True, exist_ok=True)
    jobs = [(str(p), symbols, t0, t1, str(cache_dir / f"slim_{i:02d}.parquet")) for i, p in enumerate(parts)]
    stats: list[dict[str, Any]] = []
    if jobs:
        with ProcessPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futs = [ex.submit(extract_part, j) for j in jobs]
            for fut in as_completed(futs):
                stats.append(fut.result())
    frames = []
    for s in stats:
        op = s.get("out")
        if op and Path(op).is_file():
            df = pd.read_parquet(op)
            if len(df):
                frames.append(df)
    board = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if len(board):
        board = board.sort_values(["symbol", "recv_epoch", "sequence"]).drop_duplicates(
            ["symbol", "payload_hash", "recv_epoch"], keep="first"
        )
    return board, {
        "rows": int(len(board)),
        "parts_processed": stats,
        "dups_skipped": sum(int(s.get("dups_skipped") or 0) for s in stats),
        "symbols": symbols,
        "t0": t0,
        "t1": t1,
    }


def compute_entry_feature_rows(
    entries: list[dict[str, Any]],
    board: pd.DataFrame,
    *,
    trading_date: str,
    session_kind: str,
    session_id: str,
) -> pd.DataFrame:
    rows = []
    by_sym = (
        {s: g.sort_values("recv_epoch").reset_index(drop=True) for s, g in board.groupby("symbol")}
        if len(board)
        else {}
    )
    for e in entries:
        sym = e["symbol_code"]
        df = by_sym.get(sym, pd.DataFrame())
        ee = e["entry_epoch"]
        feat: dict[str, Any] = {
            "trading_date": trading_date,
            "session_kind": session_kind,
            "session_id": session_id,
            **e,
        }
        snap = nearest_backward(df, ee) if len(df) else None
        feat["board_sync_ok"] = snap is not None
        feat["sync_clock"] = "received_at_jst_backward"
        if snap is not None:
            feat["board_sync_lag_sec"] = ee - float(snap["recv_epoch"])
            st = static_from_row(snap)
            for k, v in st.items():
                feat[f"board_at_entry_{k}"] = v
            cpt = parse_ts(snap.get("current_price_time"))
            bt = parse_ts(snap.get("board_time"))
            et = parse_ts(e["entry_time"])
            feat["capture_price_age_sec"] = (et - cpt).total_seconds() if et and cpt else float("nan")
            feat["capture_board_age_sec"] = (et - bt).total_seconds() if et and bt else float("nan")
            if st["microprice"] == st["microprice"] and e["entry_price"] == e["entry_price"]:
                feat["board_at_entry_micro_vs_entry_bps"] = (
                    st["microprice"] / e["entry_price"] - 1.0
                ) * 10000.0
        else:
            feat["board_sync_lag_sec"] = float("nan")

        for w in PRE_WINDOWS:
            if len(df) == 0 or snap is None:
                continue
            wdf = df[(df["recv_epoch"] > ee - w) & (df["recv_epoch"] <= ee)]
            dyn = window_dynamics(wdf)
            for k, v in dyn.items():
                feat[f"board_{w}s_{k}"] = v
            if len(wdf):
                stw = static_from_row(wdf.iloc[-1])
                feat[f"board_{w}s_imbalance_l5"] = stw["imbalance_l5"]
                feat[f"board_{w}s_imbalance_l10"] = stw["imbalance_l10"]
                feat[f"board_{w}s_microprice_above_mid"] = stw["microprice_above_mid"]

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
