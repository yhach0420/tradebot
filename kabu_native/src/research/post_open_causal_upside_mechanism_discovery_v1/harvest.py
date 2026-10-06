"""Neutral clock-anchor harvest. Features at T only. Outcomes after T. CAP unused.

Board tapes are not stored. Last-board is frozen when ingress crosses each
anchor T; first causal Bid1 after T+horizon is recorded on the stream.
"""
from __future__ import annotations

import gzip
import os
import pickle
import sys
import time
from pathlib import Path
from typing import Any, Optional

NATIVE = Path(__file__).resolve().parents[3]
if str(NATIVE / "src") not in sys.path:
    sys.path.insert(0, str(NATIVE / "src"))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import _bare, iter_push, record_event_stamp
from research.e1_x28_executable_joint import BOARD_FRESHNESS_SEC as E1_FRESH
from research.new_entry_breakout_continuation_v1.harvest import BOARD_FRESHNESS_SEC as HARVEST_FRESH
from research.new_entry_breakout_continuation_v1.harvest import _side_quote_t, board_row
from research.post_open_causal_upside_mechanism_discovery_v1 import (
    ANCHOR_HMS,
    BOARD_FRESHNESS_SEC,
    DEVELOPMENT_DAYS,
    FEATURE_IDS,
    PRIMARY_HORIZON_SEC,
    SECONDARY_HORIZONS_SEC,
    SESSION_FLATTEN_HM,
)
from research.post_open_causal_upside_mechanism_discovery_v1.features import (
    _bps,
    _f,
    _slice_window,
    depth_pack,
    fresh_ok,
    path_outcomes,
    window_otus,
)
from research.post_open_causal_upside_mechanism_discovery_v1.isolation import CACHE
from research.post_open_prior_close_recapture_full_strategy_v1.fields import (
    ingress_epoch,
    observed_trade_update,
    prev_close_valid,
    trusted_state,
)
from research.post_open_prior_close_recapture_full_strategy_v1.harvest import assert_dev_only_day, sealed_dev_caps
from small_paper.v1r_live_dual_lane import session_end_for_position

HORIZONS = (PRIMARY_HORIZON_SEC,) + tuple(SECONDARY_HORIZONS_SEC)


class SymBuf:
    def __init__(self, symbol: str, anchors: list[float]) -> None:
        self.symbol = symbol
        self.anchors = list(anchors)
        self.last_vol: Optional[float] = None
        self.prev_close: Optional[float] = None
        self.last_bid_clock: Optional[float] = None
        self.last_ask_clock: Optional[float] = None
        self.last_t: Optional[float] = None
        self.otu_t: list[float] = []
        self.otu_px: list[float] = []
        self.otu_vol: list[float] = []
        self.otu_val: list[float] = []
        self.bid_upd_t: list[float] = []
        self.ask_upd_t: list[float] = []
        self.fresh_src_bad_n = 0
        self.out_of_order_n = 0
        self.frozen: list[Optional[dict[str, Any]]] = [None] * len(self.anchors)
        self.mark_bid: list[dict[float, float]] = [{} for _ in self.anchors]
        self.bid = float("nan")
        self.ask = float("nan")
        self.bq = float("nan")
        self.aq = float("nan")
        self.bid_clock = float("nan")
        self.ask_clock = float("nan")
        self.exe = 0
        self.px = float("nan")
        self.calc = float("nan")
        self.under = float("nan")
        self.over = float("nan")
        self.pc = float("nan")
        self.buy10 = float("nan")
        self.sell10 = float("nan")
        self.dimb = float("nan")
        self.bnear = float("nan")
        self.snear = float("nan")

    def _board_snap(self) -> dict[str, Any]:
        return {
            "last_t": self.last_t,
            "bid": float(self.bid),
            "ask": float(self.ask),
            "bq": float(self.bq),
            "aq": float(self.aq),
            "bid_clock": float(self.bid_clock),
            "ask_clock": float(self.ask_clock),
            "exe": int(self.exe),
            "px": float(self.px),
            "calc": float(self.calc),
            "under": float(self.under),
            "over": float(self.over),
            "pc": float(self.pc),
            "buy10": float(self.buy10),
            "sell10": float(self.sell10),
            "dimb": float(self.dimb),
            "bnear": float(self.bnear),
            "snear": float(self.snear),
        }

    def maybe_freeze(self, ing: float) -> None:
        if self.last_t is None:
            return
        last = float(self.last_t)
        now = float(ing)
        for i, T in enumerate(self.anchors):
            if self.frozen[i] is not None:
                continue
            if last <= float(T) + 1e-12 < now:
                self.frozen[i] = self._board_snap()

    def _fresh_valid_bid(self, ing: float) -> bool:
        if int(self.exe) != 1:
            return False
        if self.bid != self.bid or self.bid <= 0:
            return False
        if self.bq != self.bq or self.bq <= 0:
            return False
        age = (float(ing) - float(self.bid_clock)) if self.bid_clock == self.bid_clock else None
        return fresh_ok(age)

    def maybe_markout(self, ing: float, flatten_t: float) -> None:
        if float(ing) + 1e-12 >= float(flatten_t):
            return
        if not self._fresh_valid_bid(ing):
            return
        bid = float(self.bid)
        for i, T in enumerate(self.anchors):
            if self.frozen[i] is None:
                continue
            for hz in HORIZONS:
                if hz in self.mark_bid[i]:
                    continue
                if float(ing) + 1e-12 < float(T) + float(hz):
                    continue
                self.mark_bid[i][hz] = bid

    def finalize(self) -> None:
        for i, T in enumerate(self.anchors):
            if self.frozen[i] is not None:
                continue
            if self.last_t is not None and float(self.last_t) <= float(T) + 1e-12:
                self.frozen[i] = self._board_snap()

    def push(self, *, ing: float, pay: dict[str, Any], rec: dict[str, Any], flatten_t: float) -> None:
        if self.last_t is not None and float(ing) + 1e-9 < float(self.last_t):
            self.out_of_order_n += 1
        self.maybe_freeze(ing)
        st = trusted_state(pay, event_t=ing)
        vol = st["vol"]
        px = st["px"]
        pc = prev_close_valid(pay)
        if pc is not None and self.prev_close is None:
            self.prev_close = float(pc)
        trade = observed_trade_update(last_vol=self.last_vol, vol=vol, px=px)
        if trade:
            self.otu_t.append(float(ing))
            self.otu_px.append(float(px))
            self.otu_vol.append(float(vol))
            val = _f(pay.get("TradingValue"))
            self.otu_val.append(float(val) if val is not None else float("nan"))
        if vol is not None and not (self.last_vol is not None and float(vol) < float(self.last_vol)):
            self.last_vol = float(vol)
        row = board_row(rec, pay, float(ing))
        if str(row.get("fresh_source") or "") not in ("BOARD_EVENT_TIME", "INGRESS_RECEIVED_AT", "UNRESOLVED"):
            self.fresh_src_bad_n += 1
        bid_c = _side_quote_t(pay, side="bid")
        ask_c = _side_quote_t(pay, side="ask")
        if bid_c is not None and (self.last_bid_clock is None or float(bid_c) != float(self.last_bid_clock)):
            if self.last_bid_clock is not None:
                self.bid_upd_t.append(float(ing))
            self.last_bid_clock = float(bid_c)
        if ask_c is not None and (self.last_ask_clock is None or float(ask_c) != float(self.last_ask_clock)):
            if self.last_ask_clock is not None:
                self.ask_upd_t.append(float(ing))
            self.last_ask_clock = float(ask_c)
        dep = depth_pack(pay)
        continuous = bool(st["continuous"] and st["opened"] and (not st["special"]) and (not st["preopen"]))
        bid = _f(row.get("bid"))
        ask = _f(row.get("ask"))
        bq = _f(row.get("bid_qty"))
        aq = _f(row.get("ask_qty"))
        self.bid = float(bid) if bid is not None else float("nan")
        self.ask = float(ask) if ask is not None else float("nan")
        self.bq = float(bq) if bq is not None else float("nan")
        self.aq = float(aq) if aq is not None else float("nan")
        self.bid_clock = float(bid_c) if bid_c is not None else float("nan")
        self.ask_clock = float(ask_c) if ask_c is not None else float("nan")
        self.exe = 1 if continuous else 0
        self.px = float(px) if px is not None else float("nan")
        calc = _f(pay.get("CalcPrice"))
        self.calc = float(calc) if calc is not None else float("nan")
        under = _f(pay.get("UnderBuyQty"))
        over = _f(pay.get("OverSellQty"))
        self.under = float(under) if under is not None else float("nan")
        self.over = float(over) if over is not None else float("nan")
        self.pc = float(self.prev_close) if self.prev_close is not None else float("nan")
        self.buy10 = float(dep["BUY_DEPTH_10_SUM"]) if dep["BUY_DEPTH_10_SUM"] is not None else float("nan")
        self.sell10 = float(dep["SELL_DEPTH_10_SUM"]) if dep["SELL_DEPTH_10_SUM"] is not None else float("nan")
        self.dimb = float(dep["DEPTH_IMBALANCE_10"]) if dep["DEPTH_IMBALANCE_10"] is not None else float("nan")
        self.bnear = float(dep["BUY_DEPTH_NEAR_SHARE"]) if dep["BUY_DEPTH_NEAR_SHARE"] is not None else float("nan")
        self.snear = float(dep["SELL_DEPTH_NEAR_SHARE"]) if dep["SELL_DEPTH_NEAR_SHARE"] is not None else float("nan")
        self.last_t = float(ing)
        self.maybe_markout(ing, flatten_t)


def _nan(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if v != v else v


def _dump_gz(path: Path, body: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wb") as fh:
        pickle.dump(body, fh, protocol=4)


def _load_gz(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with gzip.open(path, "rb") as fh:
        got = pickle.load(fh)
    return got if isinstance(got, dict) else {}


CACHE_SCHEMA = "UPSIDE_V1_FREEZE"


def _count_upd(ts: list[float], t: float, sec: float) -> int:
    lo, hi = _slice_window(ts, t - sec, t)
    n = 0
    for i in range(lo, hi):
        if float(ts[i]) <= float(t) + 1e-12:
            n += 1
    return n


def snapshot_symbol(
    buf: SymBuf,
    *,
    idx: int,
    t: float,
    am_start: float,
    flatten_t: float,
    universe_ctx: dict[str, Any],
) -> dict[str, Any]:
    fr = buf.frozen[idx] if 0 <= idx < len(buf.frozen) else None
    leak = 0
    if fr is None:
        return {"executable": False, "reason": "NO_BOARD", "future_leak_n": 0}
    bid = float(fr["bid"])
    ask = float(fr["ask"])
    bq = float(fr["bq"])
    aq = float(fr["aq"])
    bid_clock = float(fr["bid_clock"])
    ask_clock = float(fr["ask_clock"])
    bid_age = (float(t) - float(bid_clock)) if bid_clock == bid_clock else None
    ask_age = (float(t) - float(ask_clock)) if ask_clock == ask_clock else None
    exe = (
        int(fr["exe"]) == 1
        and fresh_ok(bid_age)
        and fresh_ok(ask_age)
        and bid == bid
        and ask == ask
        and bid > 0
        and ask > 0
        and bq == bq
        and aq == aq
        and bq > 0
        and aq > 0
    )
    w60 = window_otus(buf.otu_t, buf.otu_px, buf.otu_vol, buf.otu_val, t=t, sec=60.0)
    w180 = window_otus(buf.otu_t, buf.otu_px, buf.otu_vol, buf.otu_val, t=t, sec=180.0)
    leak += int(w60["leak"]) + int(w180["leak"])
    px = w60["px"]
    pc = _nan(fr["pc"])
    mid = (bid + ask) / 2.0 if exe else None
    calc = _nan(fr["calc"])
    under = _nan(fr["under"])
    over = _nan(fr["over"])
    feat = {
        "PRICE_RETURN_60S_BPS": w60["ret"],
        "PRICE_RETURN_180S_BPS": w180["ret"],
        "TRADE_RANGE_60S_BPS": w60["rng"],
        "TRADE_RANGE_180S_BPS": w180["rng"],
        "DIST_FROM_PREVIOUS_CLOSE_BPS": _bps((px - pc) if (px is not None and pc is not None) else None, pc),
        "TRADING_VOLUME_DELTA_60S": w60["vol_delta"],
        "TRADING_VOLUME_DELTA_180S": w180["vol_delta"],
        "TRADING_VALUE_DELTA_60S": w60["val_delta"],
        "TRADING_VALUE_DELTA_180S": w180["val_delta"],
        "OBSERVED_TRADE_N_60S": w60["n"],
        "OBSERVED_TRADE_N_180S": w180["n"],
        "SPREAD_BPS": _bps((ask - bid) if exe else None, mid),
        "BID1_QTY": float(bq) if exe else None,
        "ASK1_QTY": float(aq) if exe else None,
        "L1_QTY_IMBALANCE": ((bq - aq) / (bq + aq)) if exe and (bq + aq) > 0 else None,
        "BUY_DEPTH_10_SUM": _nan(fr["buy10"]),
        "SELL_DEPTH_10_SUM": _nan(fr["sell10"]),
        "DEPTH_IMBALANCE_10": _nan(fr["dimb"]),
        "BUY_DEPTH_NEAR_SHARE": _nan(fr["bnear"]),
        "SELL_DEPTH_NEAR_SHARE": _nan(fr["snear"]),
        "BID_UPDATE_N_60S": _count_upd(buf.bid_upd_t, t, 60.0),
        "ASK_UPDATE_N_60S": _count_upd(buf.ask_upd_t, t, 60.0),
        "CALCPRICE_MINUS_CURRENT_BPS": _bps((calc - px) if (calc is not None and px is not None) else None, px),
        "UNDER_BUY_QTY": under,
        "OVER_SELL_QTY": over,
        "UNDER_MINUS_OVER": (under - over) if (under is not None and over is not None) else None,
        "UNIVERSE_POSITIVE_60S_RATE": universe_ctx.get("pos60"),
        "UNIVERSE_ABOVE_PREVCLOSE_RATE": universe_ctx.get("above_pc"),
        "UNIVERSE_POSITIVE_180S_RATE": universe_ctx.get("pos180"),
        "MINUTES_FROM_0900": (float(t) - float(am_start)) / 60.0,
    }
    assert all(k in feat for k in FEATURE_IDS)
    out: dict[str, Any] = {
        "executable": bool(exe),
        "ASK1_AT_T": float(ask) if exe else None,
        "BID1_AT_T": float(bid) if exe else None,
        "future_leak_n": leak,
        "ret60_pos": w60["pos"] if w60["has_ret"] else None,
        "ret180_pos": w180["pos"] if w180["has_ret"] else None,
        "above_pc": (px is not None and pc is not None and px > pc),
        "has_ret60": w60["has_ret"],
        "has_ret180": w180["has_ret"],
        **feat,
    }
    if not exe:
        return out
    ask0 = float(ask)
    p10 = path_outcomes(otu_t=buf.otu_t, otu_px=buf.otu_px, ask0=ask0, t=t, horizon=PRIMARY_HORIZON_SEC, flatten_t=flatten_t)
    leak += int(p10["leak"])
    bid10 = buf.mark_bid[idx].get(PRIMARY_HORIZON_SEC)
    mark = _bps((float(bid10) - ask0) if bid10 is not None else None, ask0)
    out.update(
        {
            "EXEC_MARKOUT_10M_BPS": mark,
            "MFE_10M_BPS": p10["mfe"],
            "MAE_10M_BPS": p10["mae"],
            "PATH_EDGE_10M_BPS": p10["edge"],
            "UP_DOMINANT_10M": (p10["edge"] is not None and float(p10["edge"]) > 0),
            "EXEC_POSITIVE_10M": (mark is not None and float(mark) > 0),
            "path_otu_n_10m": p10["n"],
            "future_leak_n": leak,
        }
    )
    for hz, tag in ((SECONDARY_HORIZONS_SEC[0], "5M"), (SECONDARY_HORIZONS_SEC[1], "15M")):
        pz = path_outcomes(otu_t=buf.otu_t, otu_px=buf.otu_px, ask0=ask0, t=t, horizon=hz, flatten_t=flatten_t)
        bz = buf.mark_bid[idx].get(hz)
        mz = _bps((float(bz) - ask0) if bz is not None else None, ask0)
        out[f"EXEC_MARKOUT_{tag}_BPS"] = mz
        out[f"PATH_EDGE_{tag}_BPS"] = pz["edge"]
        leak += int(pz["leak"])
    out["future_leak_n"] = leak
    return out


def universe_at(bufs: dict[str, SymBuf], t: float) -> dict[str, Any]:
    pos60 = 0
    n60 = 0
    pos180 = 0
    n180 = 0
    above = 0
    n_pc = 0
    for buf in bufs.values():
        w60 = window_otus(buf.otu_t, buf.otu_px, buf.otu_vol, buf.otu_val, t=t, sec=60.0)
        w180 = window_otus(buf.otu_t, buf.otu_px, buf.otu_vol, buf.otu_val, t=t, sec=180.0)
        if w60["has_ret"]:
            n60 += 1
            if w60["pos"]:
                pos60 += 1
        if w180["has_ret"]:
            n180 += 1
            if w180["pos"]:
                pos180 += 1
        px = w60["px"]
        if px is not None and buf.prev_close is not None:
            n_pc += 1
            if px > float(buf.prev_close):
                above += 1
    return {
        "pos60": (pos60 / n60) if n60 else None,
        "pos180": (pos180 / n180) if n180 else None,
        "above_pc": (above / n_pc) if n_pc else None,
    }


def process_dev_day(payload: dict[str, Any]) -> dict[str, Any]:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    day = str(payload["date"])
    try:
        assert_dev_only_day(day)
    except RuntimeError as exc:
        return {"ok": False, "date": day, "blocker": str(exc)}
    if abs(float(BOARD_FRESHNESS_SEC) - float(E1_FRESH)) > 1e-12:
        return {"ok": False, "date": day, "blocker": "BOARD_FRESHNESS_DRIFT"}
    if abs(float(HARVEST_FRESH) - float(BOARD_FRESHNESS_SEC)) > 1e-12:
        return {"ok": False, "date": day, "blocker": "HARVEST_FRESHNESS_DRIFT"}
    capture = Path(payload["capture_path"])
    universe = [_bare(s) for s in list(payload["universe"]) if _bare(s)]
    uni = set(universe)
    t0w = time.perf_counter()
    am_start = float(hm_epoch(day, 9, 0))
    am_end = float(session_end_for_position(date=day, session="AM", fill_time=am_start + 60.0))
    flatten_t = float(hm_epoch(day, int(SESSION_FLATTEN_HM[0]), int(SESSION_FLATTEN_HM[1])))
    anchors = [float(hm_epoch(day, h, m)) for h, m in ANCHOR_HMS]
    bufs: dict[str, SymBuf] = {s: SymBuf(s, anchors) for s in universe}
    events_n = 0
    no_ing = 0
    try:
        for rec in iter_push(capture):
            sym = _bare(rec.get("symbol") or (rec.get("payload") or rec.get("original_payload") or {}).get("Symbol"))
            if not sym or sym not in uni:
                continue
            pay = dict(rec.get("payload") or rec.get("original_payload") or {})
            recv = record_event_stamp(rec)
            if recv:
                pay["received_at"] = recv
            ing = ingress_epoch(rec, pay)
            if ing is None:
                no_ing += 1
                continue
            if float(ing) < am_start - 120.0 or float(ing) > am_end + 2.0:
                continue
            bufs[sym].push(ing=float(ing), pay=pay, rec=rec, flatten_t=flatten_t)
            events_n += 1
            if events_n % 400000 == 0:
                print(f"{day} UPSIDE events={events_n}", flush=True)
        for buf in bufs.values():
            buf.finalize()
        rows: list[dict[str, Any]] = []
        leak = 0
        fresh_bad = sum(int(buf.fresh_src_bad_n) for buf in bufs.values())
        ooo = sum(int(buf.out_of_order_n) for buf in bufs.values())
        for i, t in enumerate(anchors):
            uctx = universe_at(bufs, t)
            h, m = ANCHOR_HMS[i]
            hm = f"{h:02d}:{m:02d}"
            for s, buf in bufs.items():
                snap = snapshot_symbol(buf, idx=i, t=t, am_start=am_start, flatten_t=flatten_t, universe_ctx=uctx)
                leak += int(snap.get("future_leak_n") or 0)
                rec = {
                    "date": day,
                    "symbol": s,
                    "anchor_hm": hm,
                    "t": t,
                    "executable": bool(snap.get("executable")),
                    **snap,
                }
                rows.append(rec)
        ok = leak == 0 and fresh_bad == 0 and ooo == 0
        print(
            f"{day} UPSIDE DEVELOPMENT events={events_n} rows={len(rows)} leak={leak} ooo={ooo}",
            flush=True,
        )
        blocker = None
        if not ok:
            if leak:
                blocker = "FUTURE_LEAK"
            elif ooo:
                blocker = "OUT_OF_ORDER"
            else:
                blocker = "FRESHNESS_LEAK"
        return {
            "ok": ok,
            "date": day,
            "rows": rows,
            "events_n": events_n,
            "future_leak_n": leak,
            "no_ingress_n": no_ing,
            "out_of_order_n": ooo,
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
            "blocker": blocker,
        }
    except Exception as exc:
        return {"ok": False, "date": day, "blocker": f"{type(exc).__name__}:{exc}", "elapsed_sec": round(time.perf_counter() - t0w, 3)}


def harvest_upside() -> dict[str, Any]:
    CACHE.mkdir(parents=True, exist_ok=True)
    invs, blockers = sealed_dev_caps()
    if blockers:
        return {"ok": False, "blocker": "CAPTURE", "blockers": blockers}
    rows: list[dict[str, Any]] = []
    day_ok: dict[str, bool] = {}
    leak = 0
    for inv in invs:
        day = str(inv["date"])
        cache = CACHE / f"UPSIDE_{day}.pkl.gz"
        saved = _load_gz(cache)
        if (
            saved.get("ok")
            and saved.get("schema") == CACHE_SCHEMA
            and str(saved.get("date") or "") == day
            and saved.get("rows") is not None
        ):
            rows.extend(list(saved.get("rows") or []))
            leak += int(saved.get("future_leak_n") or 0)
            day_ok[day] = True
            print(f"cache-hit UPSIDE {day}", flush=True)
            continue
        body = process_dev_day({"date": day, "capture_path": inv["capture_path"], "universe": list(inv["universe_symbols"])})
        day_ok[day] = bool(body.get("ok"))
        if not body.get("ok"):
            return {"ok": False, "blocker": f"DEVELOPMENT:{day}:{body.get('blocker')}", "day_ok": day_ok}
        _dump_gz(
            cache,
            {
                "ok": True,
                "schema": CACHE_SCHEMA,
                "date": day,
                "rows": body.get("rows"),
                "future_leak_n": body.get("future_leak_n"),
            },
        )
        rows.extend(list(body.get("rows") or []))
        leak += int(body.get("future_leak_n") or 0)
    return {"ok": True, "rows": rows, "day_ok": day_ok, "future_leak_n": leak, "days": list(DEVELOPMENT_DAYS)}
