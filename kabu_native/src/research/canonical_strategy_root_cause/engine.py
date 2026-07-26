"""Root-cause engine: ENTRY quality, EXIT controls, episodes, C0–C8 CAP5."""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

from research.canonical_quote_mainline_repair.dual_replay import (
    _mom_score,
    _parse_ts,
    _session,
    discover_capture_days,
    iter_capture_events,
    replay_cap5,
)
from research.canonical_strategy_root_cause.constants import (
    AUDIT_DAYS,
    BOARD_P33,
    BOARD_P66,
    BOARD_SPLIT_PERCENTILE,
    CAP,
    COST_BPS,
    HARD_STOP_PCT,
    LEGACY_FIXED_ACTIVATE_PCT,
    LEGACY_FIXED_GIVEBACK_FRAC,
    LOT,
    MAX_HOLD_SEC,
    MOMENTUM_P33,
    SAMPLE_STRIDE,
)
from small_paper.board_dynamic_trailing_shadow import trailing_params_for_board_tier
from small_paper.canonical_board import (
    board_token_from_imbalance,
    buy_limit_price,
    legacy_mixed_imbalance,
    normalize_kabu_board,
    sell_limit_price,
)


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _pnl_yen(entry: float, exit_: float) -> float:
    raw = (exit_ - entry) * LOT
    cost = entry * LOT * COST_BPS / 10000.0 + exit_ * LOT * COST_BPS / 10000.0
    return raw - cost


def _tick_size(px: float) -> float:
    if px < 3000:
        return 1.0
    if px < 5000:
        return 5.0
    if px < 30000:
        return 10.0
    return 50.0


@dataclass
class EventRow:
    day: str
    symbol: str
    ts: datetime
    px: float
    payload: dict
    event_id: str
    idx: int  # index within symbol stream


@dataclass
class Candidate:
    day: str
    symbol: str
    event_id: str
    entry_time: datetime
    entry_ask: float
    entry_bid: float
    entry_mark: float
    mom: float
    leg_imb: float
    can_depth: float
    can_top: float
    leg_token: str
    can_token: str
    e0: bool  # mom low only
    e1: bool  # mom + legacy board
    e2: bool  # mom + canonical board
    session: str
    episode_id: str
    spread_yen: float
    spread_bps: float
    spread_ticks: float
    quote_age_sec: Optional[float]
    stream_idx: int
    path: list[tuple[datetime, float, dict]] = field(default_factory=list)


@dataclass
class TradeSim:
    day: str
    symbol: str
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    exit_reason: str
    pnl_5bps: float
    hold_sec: float
    mfe_pct: float
    mae_pct: float
    entry_mode: str
    exit_mode: str
    portfolio: str
    session: str
    setup_id: str
    operational: bool
    episode_id: str
    stop: bool
    early_stop: bool
    no_progress: bool
    winner: bool
    exit_class: str = ""
    mfe_before_exit: float = 0.0
    px_10s: Optional[float] = None
    px_30s: Optional[float] = None


# ---------- stream load ----------

def load_streams(days: list[str], *, stride: int) -> dict[str, list[EventRow]]:
    streams: dict[str, list[EventRow]] = defaultdict(list)
    for day in days:
        by_sym: dict[str, list[EventRow]] = defaultdict(list)
        for ev in iter_capture_events(day, stride=stride):
            row = EventRow(
                day=day,
                symbol=ev["symbol"],
                ts=ev["ts"],
                px=ev["px"],
                payload=ev["payload"],
                event_id=ev["event_id"],
                idx=0,
            )
            by_sym[ev["symbol"]].append(row)
        for sym, rows in by_sym.items():
            rows.sort(key=lambda r: r.ts)
            for i, r in enumerate(rows):
                r.idx = i
            streams[f"{day}|{sym}"] = rows
    return streams


def build_episodes(rows: list[EventRow]) -> list[str]:
    """Assign episode_id per event index — no entry timestamp in id."""
    if not rows:
        return []
    ids: list[str] = []
    ep_n = 0
    ep_start_px = rows[0].px
    mom_on = False
    prices: list[float] = []
    last_ts = rows[0].ts
    cur_id = f"{rows[0].day}:{rows[0].symbol}:ep{ep_n}"
    for r in rows:
        prices.append(r.px)
        if len(prices) > 60:
            prices = prices[-60:]
        mom = _mom_score(prices)
        mom_low = mom is not None and mom < MOMENTUM_P33
        gap = (r.ts - last_ts).total_seconds() > 120
        sess_flip = _session(r.ts) != _session(last_ts)
        # price reset: >1% move from episode start against prior direction
        reset = abs(r.px - ep_start_px) / ep_start_px > 0.015 if ep_start_px > 0 else False
        # new pullback: mom was on, now off then on again with dip
        end_ep = gap or sess_flip or (mom_on and not mom_low) or (reset and not mom_low)
        if end_ep and ids:
            ep_n += 1
            cur_id = f"{r.day}:{r.symbol}:ep{ep_n}"
            ep_start_px = r.px
            mom_on = False
        if mom_low:
            mom_on = True
        ids.append(cur_id)
        last_ts = r.ts
    return ids


def build_candidates(streams: dict[str, list[EventRow]]) -> tuple[list[Candidate], dict[str, Any]]:
    cands: list[Candidate] = []
    stats = {"n_events": 0, "e0": 0, "e1": 0, "e2": 0}
    for key, rows in streams.items():
        ep_ids = build_episodes(rows)
        prices: list[float] = []
        last_entry_ts: Optional[datetime] = None
        for i, r in enumerate(rows):
            stats["n_events"] += 1
            prices.append(r.px)
            if len(prices) > 60:
                prices = prices[-60:]
            mom = _mom_score(prices)
            if mom is None:
                continue
            board = normalize_kabu_board(r.payload)
            leg = legacy_mixed_imbalance(r.payload)
            if leg is None or board.canonical_depth_imbalance is None:
                continue
            if board.canonical_best_bid is None or board.canonical_best_ask is None:
                continue
            if board.canonical_best_ask <= 0 or board.canonical_best_bid <= 0:
                continue
            leg_tok = board_token_from_imbalance(leg, p33=BOARD_P33, p66=BOARD_P66)
            can_tok = board_token_from_imbalance(board.canonical_depth_imbalance, p33=BOARD_P33, p66=BOARD_P66)
            e0 = mom < MOMENTUM_P33
            e1 = e0 and leg_tok in ("Board:mid", "Board:high")
            e2 = e0 and can_tok in ("Board:mid", "Board:high")
            if not e0:
                continue
            # 60s cooldown per symbol (event-level candidate density control)
            if last_entry_ts is not None and (r.ts - last_entry_ts).total_seconds() < 60:
                continue
            future = rows[i : i + 400]
            if len(future) < 2:
                continue
            last_entry_ts = r.ts
            path = [(x.ts, x.px, x.payload) for x in future]
            ask = float(board.canonical_best_ask)
            bid = float(board.canonical_best_bid)
            spread = ask - bid
            mid = (ask + bid) / 2.0
            sbps = abs(spread) / mid * 10000.0 if mid > 0 else 0.0
            ticks = abs(spread) / _tick_size(mid) if mid > 0 else 0.0
            cpt = _parse_ts(r.payload.get("CurrentPriceTime"))
            qage = (r.ts - cpt).total_seconds() if cpt else None
            c = Candidate(
                day=r.day,
                symbol=r.symbol,
                event_id=r.event_id,
                entry_time=r.ts,
                entry_ask=ask,
                entry_bid=bid,
                entry_mark=r.px,
                mom=float(mom),
                leg_imb=float(leg),
                can_depth=float(board.canonical_depth_imbalance),
                can_top=float(board.canonical_top_imbalance or 0.5),
                leg_token=leg_tok,
                can_token=can_tok,
                e0=e0,
                e1=e1,
                e2=e2,
                session=_session(r.ts),
                episode_id=ep_ids[i] if i < len(ep_ids) else f"{r.day}:{r.symbol}:ep0",
                spread_yen=spread,
                spread_bps=sbps,
                spread_ticks=ticks,
                quote_age_sec=qage,
                stream_idx=i,
                path=path,
            )
            cands.append(c)
            if e0:
                stats["e0"] += 1
            if e1:
                stats["e1"] += 1
            if e2:
                stats["e2"] += 1
    return cands, stats


# ---------- pre-exit opportunity ----------

def measure_opportunity(c: Candidate) -> dict[str, Any]:
    entry = c.entry_ask if c.entry_ask and c.entry_ask > 0 else c.entry_mark
    if entry is None or entry <= 0:
        return {
            "mfe_5s": 0.0, "mfe_30s": 0.0, "mfe_2m": 0.0, "mfe_5m": 0.0, "mae": 0.0,
            "exec_pos_mfe": False, "never_profitable": True, "stop_within_5m": False,
            "np_15m": True, "large_rise": False, "spread_bps": c.spread_bps,
            "quote_age": c.quote_age_sec, "episode_id": c.episode_id,
        }
    mfe = {"5s": 0.0, "30s": 0.0, "2m": 0.0, "5m": 0.0}
    mae = 0.0
    stop_px = entry * (1.0 - HARD_STOP_PCT / 100.0)
    hit_stop_5m = False
    ever_pos = False
    large_rise = False
    for ts, px, op in c.path:
        if ts <= c.entry_time:
            continue
        # mark-to-bid executable
        bid = sell_limit_price(op, mode="canonical") or px
        pnl_pct = (bid - entry) / entry * 100.0
        mae = min(mae, pnl_pct)
        if pnl_pct > 0:
            ever_pos = True
        if pnl_pct >= 1.5:
            large_rise = True
        dt = (ts - c.entry_time).total_seconds()
        if dt <= 5:
            mfe["5s"] = max(mfe["5s"], pnl_pct)
        if dt <= 30:
            mfe["30s"] = max(mfe["30s"], pnl_pct)
        if dt <= 120:
            mfe["2m"] = max(mfe["2m"], pnl_pct)
        if dt <= 300:
            mfe["5m"] = max(mfe["5m"], pnl_pct)
            if bid <= stop_px:
                hit_stop_5m = True
        if dt > 900:
            break
    # 15m no progress: max mfe < 0.3 within 900s
    mfe15 = 0.0
    for ts, px, op in c.path:
        if ts <= c.entry_time:
            continue
        bid = sell_limit_price(op, mode="canonical") or px
        pnl_pct = (bid - entry) / entry * 100.0
        dt = (ts - c.entry_time).total_seconds()
        if dt <= 900:
            mfe15 = max(mfe15, pnl_pct)
        else:
            break
    np15 = mfe15 < 0.3
    return {
        "mfe_5s": mfe["5s"],
        "mfe_30s": mfe["30s"],
        "mfe_2m": mfe["2m"],
        "mfe_5m": mfe["5m"],
        "mae": mae,
        "exec_pos_mfe": ever_pos,
        "never_profitable": not ever_pos,
        "stop_within_5m": hit_stop_5m,
        "np_15m": np15,
        "large_rise": large_rise,
        "spread_bps": c.spread_bps,
        "quote_age": c.quote_age_sec,
        "episode_id": c.episode_id,
    }


def summarize_opportunity(cands: list[Candidate], label: str) -> dict[str, Any]:
    if not cands:
        return {"cohort": label, "n": 0}
    rows = [measure_opportunity(c) for c in cands]
    n = len(rows)

    def avg(key: str) -> float:
        return sum(float(r[key]) for r in rows) / n

    return {
        "cohort": label,
        "n": n,
        "avg_mfe_5s": round(avg("mfe_5s"), 4),
        "avg_mfe_30s": round(avg("mfe_30s"), 4),
        "avg_mfe_2m": round(avg("mfe_2m"), 4),
        "avg_mfe_5m": round(avg("mfe_5m"), 4),
        "avg_mae": round(avg("mae"), 4),
        "exec_pos_mfe_rate": sum(1 for r in rows if r["exec_pos_mfe"]) / n,
        "never_profitable_rate": sum(1 for r in rows if r["never_profitable"]) / n,
        "stop_5m_rate": sum(1 for r in rows if r["stop_within_5m"]) / n,
        "np_15m_rate": sum(1 for r in rows if r["np_15m"]) / n,
        "large_rise_rate": sum(1 for r in rows if r["large_rise"]) / n,
        "avg_spread_bps": round(avg("spread_bps"), 4),
    }


def board_quantiles(e0: list[Candidate]) -> list[dict[str, Any]]:
    """E3: diagnose E0 by canonical imbalance quantiles (not a gate)."""
    if not e0:
        return []
    tops = sorted(c.can_top for c in e0)
    depths = sorted(c.can_depth for c in e0)

    def q(xs: list[float], p: float) -> float:
        if not xs:
            return float("nan")
        i = int(max(0, min(len(xs) - 1, round((len(xs) - 1) * p))))
        return xs[i]

    cuts_t = [0.0, 0.25, 0.5, 0.75, 1.0]
    thr_t = [q(tops, p) for p in cuts_t]
    thr_d = [q(depths, p) for p in cuts_t]
    out = []
    for kind, thr, getter in (
        ("canonical_top", thr_t, lambda c: c.can_top),
        ("canonical_depth", thr_d, lambda c: c.can_depth),
    ):
        for i in range(4):
            lo, hi = thr[i], thr[i + 1]
            bucket = [c for c in e0 if lo <= getter(c) <= hi + (1e-12 if i == 3 else 0)]
            # avoid double count: use half-open except last
            if i < 3:
                bucket = [c for c in e0 if lo <= getter(c) < hi]
            s = summarize_opportunity(bucket[: min(len(bucket), 2000)], f"{kind}_Q{i+1}")
            s["lo"] = lo
            s["hi"] = hi
            s["kind"] = kind
            out.append(s)
    return out


# ---------- EXIT sims ----------

def _path_after(path: list[tuple[datetime, float, dict]], entry_time: datetime):
    for ts, px, op in path:
        if ts > entry_time:
            yield ts, px, op


def simulate_exit(c: Candidate, *, exit_mode: str, prior_imbs: list[float]) -> dict[str, Any]:
    """exit_mode: X0|X1|X2|X3|X4"""
    entry = c.entry_ask if c.entry_ask and c.entry_ask > 0 else c.entry_mark
    if entry is None or entry <= 0:
        return _exit_pack(c.entry_time, 0.0, "capture_end", "none", 0, 0, 0, 0, True, c.can_top, [], c)
    stop_px = entry * (1.0 - HARD_STOP_PCT / 100.0)
    peak_pnl = 0.0
    activated = False
    mfe = 0.0
    mae = 0.0
    entry_top = c.can_top
    # trailing params
    if exit_mode in ("X1",):
        act, gb = LEGACY_FIXED_ACTIVATE_PCT, LEGACY_FIXED_GIVEBACK_FRAC
        tier = "fixed"
    elif exit_mode in ("X2",):
        act, gb = LEGACY_FIXED_ACTIVATE_PCT, LEGACY_FIXED_GIVEBACK_FRAC
        tier = "mfe_fixed"
    elif exit_mode in ("X3", "X4"):
        if prior_imbs:
            le = sum(1 for s in prior_imbs if s <= c.can_depth)
            pct = 100.0 * le / len(prior_imbs)
        else:
            pct = 50.0
        act, gb, tier = trailing_params_for_board_tier(pct)
    else:
        act, gb, tier = 999.0, 0.0, "none"  # X0 no trailing

    last_ts = c.entry_time
    last_px = entry
    last_op = c.path[0][2] if c.path else {}
    reason = "capture_end"
    imb_path: list[float] = []
    for ts, px, op in _path_after(c.path, c.entry_time):
        hold = (ts - c.entry_time).total_seconds()
        if hold > MAX_HOLD_SEC:
            reason = "capture_end"
            last_ts, last_px, last_op = ts, px, op
            break
        if _session(ts) != c.session:
            reason = "session_close"
            last_ts, last_px, last_op = ts, px, op
            break
        bid = sell_limit_price(op, mode="canonical") or px
        board = normalize_kabu_board(op)
        cur_top = board.canonical_top_imbalance
        if cur_top is not None:
            imb_path.append(cur_top)
        pnl_pct = (bid - entry) / entry * 100.0
        mfe = max(mfe, pnl_pct)
        mae = min(mae, pnl_pct)
        if bid <= stop_px:
            return _exit_pack(ts, bid, "hard_stop", tier, act, gb, mfe, mae, False, entry_top, imb_path, c)
        # board exits X4
        if exit_mode == "X4" and cur_top is not None:
            delta = cur_top - entry_top
            if pnl_pct > 0 and delta <= -0.08:
                return _exit_pack(ts, bid, "board_collapse_profit_exit", tier, act, gb, mfe, mae, False, entry_top, imb_path, c)
            if pnl_pct > 0 and delta <= -0.05:
                return _exit_pack(ts, bid, "profit_protect_exit", tier, act, gb, mfe, mae, False, entry_top, imb_path, c)
        # trailing X1/X2/X3/X4
        if exit_mode in ("X1", "X2", "X3", "X4"):
            if pnl_pct > peak_pnl:
                peak_pnl = pnl_pct
            if not activated and peak_pnl >= act:
                activated = True
            if activated and peak_pnl > 0 and pnl_pct <= peak_pnl * (1.0 - gb):
                return _exit_pack(ts, bid, "trailing_mfe", tier, act, gb, mfe, mae, False, entry_top, imb_path, c)
        last_ts, last_px, last_op = ts, bid, op
    return _exit_pack(last_ts, last_px, reason, tier, act, gb, mfe, mae, reason in ("session_close", "capture_end"), entry_top, imb_path, c)


def _exit_pack(ts, px, reason, tier, act, gb, mfe, mae, operational, entry_top, imb_path, c):
    # look-ahead prices
    px10 = px30 = None
    for t2, p2, op2 in _path_after(c.path, c.entry_time):
        dt = (t2 - ts).total_seconds()
        if px10 is None and dt >= 10:
            px10 = sell_limit_price(op2, mode="canonical") or p2
        if dt >= 30:
            px30 = sell_limit_price(op2, mode="canonical") or p2
            break
    return {
        "exit_time": ts,
        "exit_price": float(px),
        "exit_reason": reason,
        "tier": tier,
        "activate": act,
        "giveback": gb,
        "mfe": mfe,
        "mae": mae,
        "operational": operational,
        "entry_top": entry_top,
        "imb_path": imb_path[-20:],
        "px_10s": px10,
        "px_30s": px30,
    }


def classify_exit(ex: dict[str, Any], c: Candidate, entry: float) -> str:
    reason = ex["exit_reason"]
    hold = (ex["exit_time"] - c.entry_time).total_seconds()
    mfe = float(ex["mfe"])
    pnl = _pnl_yen(entry, float(ex["exit_price"]))
    px30 = ex.get("px_30s")
    cont = None
    if px30 is not None and entry > 0:
        cont = (px30 - entry) / entry * 100.0
    if reason == "hard_stop":
        if hold <= 1.0 and c.spread_yen > 0 and (entry - float(ex["exit_price"])) <= c.spread_yen * 1.2:
            return "HARD_STOP_AFTER_MISSED_EXIT"
        return "HARD_STOP_AFTER_MISSED_EXIT" if mfe > 0.3 else "EXIT_AFTER_EDGE_ALREADY_LOST"
    if reason == "board_collapse_profit_exit":
        if cont is not None and cont > mfe:
            return "FALSE_BOARD_COLLAPSE"
        if mfe < c.spread_bps / 100.0:  # rough
            return "PROFIT_PROTECT_BEFORE_COST_RECOVERY"
        return "VALID_EARLY_PROTECTION" if pnl >= 0 else "FALSE_BOARD_COLLAPSE"
    if reason == "profit_protect_exit":
        if mfe * entry / 100.0 * LOT < entry * LOT * COST_BPS / 10000.0 * 2:
            return "PROFIT_PROTECT_BEFORE_COST_RECOVERY"
        if cont is not None and cont > mfe + 0.2:
            return "FALSE_BOARD_COLLAPSE"
        return "VALID_EARLY_PROTECTION" if pnl >= 0 else "BOARD_SIGNAL_CHATTER"
    if reason == "trailing_mfe":
        if cont is not None and cont > mfe + 0.3 and pnl < 0:
            return "LOST_WINNER"
        return "VALID_EARLY_PROTECTION" if pnl >= 0 else "EXIT_AFTER_EDGE_ALREADY_LOST"
    if reason in ("session_close", "capture_end"):
        return "EXIT_AFTER_EDGE_ALREADY_LOST" if mfe > 0.3 and pnl <= 0 else "UNKNOWN"
    return "UNKNOWN"


# ---------- spread STOP ----------

def audit_spread_stop(c: Candidate) -> Optional[dict[str, Any]]:
    entry = c.entry_ask
    stop_px = entry * (1.0 - HARD_STOP_PCT / 100.0)
    stop_width = entry - stop_px
    first_bid_ts = None
    first_exec_loss = None
    bid_100 = bid_500 = bid_1s = None
    stop_ts = None
    stop_px_hit = None
    for ts, px, op in _path_after(c.path, c.entry_time):
        bid = sell_limit_price(op, mode="canonical") or px
        dt = (ts - c.entry_time).total_seconds()
        if first_bid_ts is None:
            first_bid_ts = ts
        if first_exec_loss is None and bid < entry:
            first_exec_loss = entry - bid
        if bid_100 is None and dt >= 0.1:
            bid_100 = bid
        if bid_500 is None and dt >= 0.5:
            bid_500 = bid
        if bid_1s is None and dt >= 1.0:
            bid_1s = bid
        if bid <= stop_px and stop_ts is None:
            stop_ts = ts
            stop_px_hit = bid
            break
        if dt > 60:
            break
    if stop_ts is None:
        return None
    hold = (stop_ts - c.entry_time).total_seconds()
    cls = "UNKNOWN"
    if hold <= 1.0:
        # spread consumed if loss ≈ spread and little further decline
        loss = entry - float(stop_px_hit or entry)
        if c.spread_yen > 0 and loss <= c.spread_yen * 1.5 and (bid_1s is None or (entry - bid_1s) <= c.spread_yen * 2):
            cls = "SPREAD_CONSUMED_STOP"
        elif first_bid_ts and (first_bid_ts - c.entry_time).total_seconds() <= 0.5:
            cls = "FIRST_QUOTE_STOP"
        else:
            cls = "TRUE_PRICE_DECLINE"
    elif c.spread_bps >= 15:
        cls = "WIDE_SPREAD_ENTRY"
    elif c.quote_age_sec is not None and c.quote_age_sec > 5:
        cls = "STALE_QUOTE"
    else:
        cls = "TRUE_PRICE_DECLINE"
    return {
        "event_id": c.event_id,
        "symbol": c.symbol,
        "buy_ask": entry,
        "sell_bid_entry": c.entry_bid,
        "spread_yen": c.spread_yen,
        "spread_ticks": c.spread_ticks,
        "spread_bps": c.spread_bps,
        "hard_stop_width": stop_width,
        "spread_over_stop": c.spread_yen / stop_width if stop_width > 0 else None,
        "first_bid_update_sec": (first_bid_ts - c.entry_time).total_seconds() if first_bid_ts else None,
        "first_executable_loss": first_exec_loss,
        "bid_100ms": bid_100,
        "bid_500ms": bid_500,
        "bid_1s": bid_1s,
        "stop_price": stop_px_hit,
        "stop_sec": hold,
        "class": cls,
    }


# ---------- trade building / CAP ----------

def make_trade(c: Candidate, *, exit_mode: str, portfolio: str, prior: list[float], entry_label: str) -> TradeSim:
    ex = simulate_exit(c, exit_mode=exit_mode, prior_imbs=prior)
    entry = c.entry_ask if c.entry_ask and c.entry_ask > 0 else c.entry_mark
    if entry is None or entry <= 0:
        entry = 1.0
    pnl = _pnl_yen(entry, float(ex["exit_price"]) if ex["exit_price"] else entry)
    hold = (ex["exit_time"] - c.entry_time).total_seconds()
    reason = str(ex["exit_reason"])
    stop = reason == "hard_stop"
    early = stop and hold <= 60
    np_ = reason in ("capture_end", "session_close") and float(ex["mfe"]) < 0.3
    winner = pnl > 0 and float(ex["mfe"]) >= 0.8
    ecl = classify_exit(ex, c, entry)
    return TradeSim(
        day=c.day,
        symbol=c.symbol,
        entry_time=c.entry_time,
        exit_time=ex["exit_time"],
        entry_price=entry,
        exit_price=float(ex["exit_price"]),
        exit_reason=reason,
        pnl_5bps=round(pnl, 2),
        hold_sec=hold,
        mfe_pct=float(ex["mfe"]),
        mae_pct=float(ex["mae"]),
        entry_mode=entry_label,
        exit_mode=exit_mode,
        portfolio=portfolio,
        session=c.session,
        setup_id=f"{portfolio}:{c.event_id}",
        operational=bool(ex["operational"]),
        episode_id=c.episode_id,
        stop=stop,
        early_stop=early,
        no_progress=np_,
        winner=winner,
        exit_class=ecl,
        mfe_before_exit=float(ex["mfe"]),
        px_10s=ex.get("px_10s"),
        px_30s=ex.get("px_30s"),
    )


def to_trade_row_compat(t: TradeSim):
    """Adapt to dual_replay.replay_cap5 TradeRow-like interface via duck typing.

    replay_cap5 expects TradeRow fields — we reuse a thin wrapper by importing TradeRow.
    """
    from research.canonical_quote_mainline_repair.dual_replay import TradeRow

    return TradeRow(
        day=t.day,
        symbol=t.symbol,
        entry_time=t.entry_time,
        exit_time=t.exit_time,
        entry_price=t.entry_price,
        exit_price=t.exit_price,
        exit_reason=t.exit_reason,
        pnl_5bps=t.pnl_5bps,
        hold_sec=t.hold_sec,
        mfe_pct=t.mfe_pct,
        mae_pct=t.mae_pct,
        mfe_capture=(t.pnl_5bps / (t.mfe_pct * t.entry_price * LOT / 100.0)) if t.mfe_pct > 1e-9 else None,
        entry_mode=t.entry_mode,
        exit_mode=t.exit_mode,
        portfolio=t.portfolio,
        session=t.session,
        setup_id=t.setup_id,
        operational=t.operational,
        leg_imb=0.0,
        can_imb=0.0,
        leg_tier="",
        can_tier="",
        stop=t.stop,
        early_stop=t.early_stop,
        no_progress=t.no_progress,
        winner=t.winner,
    )


def build_trades(cands: list[Candidate], *, exit_mode: str, portfolio: str, entry_label: str) -> list[TradeSim]:
    rows: list[TradeSim] = []
    prior: list[float] = []
    for c in sorted(cands, key=lambda x: (x.day, x.entry_time, x.symbol)):
        t = make_trade(c, exit_mode=exit_mode, portfolio=portfolio, prior=list(prior), entry_label=entry_label)
        rows.append(t)
        prior.append(c.can_depth)
        if len(prior) > 500:
            prior = prior[-500:]
    return rows


def one_episode_one_entry(cands: list[Candidate]) -> list[Candidate]:
    """R1: first candidate per episode_id only."""
    seen: set[str] = set()
    out: list[Candidate] = []
    for c in sorted(cands, key=lambda x: (x.day, x.entry_time, x.symbol)):
        if c.episode_id in seen:
            continue
        seen.add(c.episode_id)
        out.append(c)
    return out


def r2_filter(cands: list[Candidate], trades_by_ep: dict[str, datetime] | None = None) -> list[Candidate]:
    """R2 diagnostic: after exit, 30s same-episode reentry ban — approximate using entry spacing."""
    # Without full trade loop, approximate: within episode, min 30s between entries
    last: dict[str, datetime] = {}
    out = []
    for c in sorted(cands, key=lambda x: (x.day, x.entry_time)):
        prev = last.get(c.episode_id)
        if prev is not None and (c.entry_time - prev).total_seconds() < 30:
            continue
        last[c.episode_id] = c.entry_time
        out.append(c)
    return out


def r3_filter(cands: list[Candidate]) -> list[Candidate]:
    """R3 diagnostic: require board or price reset vs prior entry in episode."""
    last: dict[str, Candidate] = {}
    out = []
    for c in sorted(cands, key=lambda x: (x.day, x.entry_time)):
        prev = last.get(c.episode_id)
        if prev is not None:
            px_reset = abs(c.entry_mark - prev.entry_mark) / prev.entry_mark > 0.005
            board_reset = abs(c.can_top - prev.can_top) > 0.08
            if not (px_reset or board_reset):
                continue
        last[c.episode_id] = c
        out.append(c)
    return out


def run_arm(cands: list[Candidate], *, exit_mode: str, arm_id: str, entry_label: str) -> dict[str, Any]:
    trades = build_trades(cands, exit_mode=exit_mode, portfolio=arm_id, entry_label=entry_label)
    compat = [to_trade_row_compat(t) for t in trades]
    cap = replay_cap5(compat, portfolio_id=arm_id, cap=CAP)
    # exit class mix
    cls = Counter(t.exit_class for t in trades)
    imm01 = sum(1 for t in trades if t.hold_sec <= 1)
    imm15 = sum(1 for t in trades if 1 < t.hold_sec <= 5)
    imm530 = sum(1 for t in trades if 5 < t.hold_sec <= 30)
    return {
        **cap,
        "exit_class_mix": dict(cls),
        "exit_0_1s": imm01,
        "exit_1_5s": imm15,
        "exit_5_30s": imm530,
        "false_collapse": cls.get("FALSE_BOARD_COLLAPSE", 0),
        "n_pre_cap_trades": len(trades),
    }


def run_full_analysis(*, days: Optional[list[str]] = None, stride: int = SAMPLE_STRIDE) -> dict[str, Any]:
    days = days or [d for d in discover_capture_days() if d in AUDIT_DAYS or True]
    ordered = [d for d in AUDIT_DAYS if d in days] + [d for d in days if d not in AUDIT_DAYS]
    streams = load_streams(ordered, stride=stride)
    cands, stats = build_candidates(streams)

    e0 = [c for c in cands if c.e0]
    e1 = [c for c in cands if c.e1]
    e2 = [c for c in cands if c.e2]
    # opportunity: subsample for speed if huge
    def sample(xs: list[Candidate], n: int = 3000) -> list[Candidate]:
        if len(xs) <= n:
            return xs
        step = max(1, len(xs) // n)
        return xs[::step][:n]

    opp_e0 = summarize_opportunity(sample(e0), "E0")
    opp_e1 = summarize_opportunity(sample(e1), "E1")
    opp_e2 = summarize_opportunity(sample(e2), "E2")
    quant = board_quantiles(sample(e0, 4000))

    # EXIT controls on fixed E2 cohort (event-level)
    e2_s = sample(e2, 4000)
    exit_controls = {}
    for xm in ("X0", "X1", "X2", "X3", "X4"):
        exit_controls[xm] = run_arm(e2_s, exit_mode=xm, arm_id=f"E2_{xm}", entry_label="E2")

    # EXIT reason audit sample from X3/X4
    exit_audit = []
    prior: list[float] = []
    for c in e2_s[:500]:
        for xm in ("X3", "X4"):
            t = make_trade(c, exit_mode=xm, portfolio=xm, prior=prior, entry_label="E2")
            prior.append(c.can_depth)
            exit_audit.append({
                "exit_mode": xm,
                "symbol": c.symbol,
                "entry_time": c.entry_time.isoformat(),
                "exit_time": t.exit_time.isoformat(),
                "hold_sec": t.hold_sec,
                "exit_reason": t.exit_reason,
                "exit_class": t.exit_class,
                "mfe": t.mfe_pct,
                "mae": t.mae_pct,
                "pnl": t.pnl_5bps,
                "px_10s": t.px_10s,
                "px_30s": t.px_30s,
                "spread_bps": c.spread_bps,
            })

    # spread stop audit on E2 early stops
    spread_rows = []
    for c in e2_s[:2000]:
        row = audit_spread_stop(c)
        if row:
            spread_rows.append(row)
    spread_cls = Counter(r["class"] for r in spread_rows)

    # episodes / reentry
    ep_ids = {c.episode_id for c in e2}
    reentry_same = 0
    by_ep: dict[str, int] = Counter()
    for c in e2:
        by_ep[c.episode_id] += 1
    reentry_same = sum(max(0, n - 1) for n in by_ep.values())
    e2_r1 = one_episode_one_entry(e2)
    e2_r2 = r2_filter(e2)
    e2_r3 = r3_filter(e2)
    reentry = {
        "raw_candidates_e2": len(e2),
        "true_episodes": len(ep_ids),
        "candidates_per_episode": (len(e2) / len(ep_ids)) if ep_ids else None,
        "same_episode_reentry": reentry_same,
        "R0_event": run_arm(sample(e2, 4000), exit_mode="X4", arm_id="R0", entry_label="E2"),
        "R1_one_ep": run_arm(sample(e2_r1, 4000), exit_mode="X4", arm_id="R1", entry_label="E2_R1"),
        "R2_30s": run_arm(sample(e2_r2, 4000), exit_mode="X4", arm_id="R2", entry_label="E2_R2"),
        "R3_reset": run_arm(sample(e2_r3, 4000), exit_mode="X4", arm_id="R3", entry_label="E2_R3"),
    }

    # C0–C8
    arms_def = [
        ("C0", e0, "X0", "E0"),
        ("C1", e0, "X1", "E0"),
        ("C2", e0, "X2", "E0"),
        ("C3", e0, "X3", "E0"),
        ("C4", e2, "X0", "E2"),
        ("C5", e2, "X1", "E2"),
        ("C6", e2, "X2", "E2"),
        ("C7", e2, "X3", "E2"),
        ("C8", e2, "X4", "E2"),
    ]
    c_event = {}
    c_episode = {}
    for arm_id, cohort, xm, elabel in arms_def:
        c_event[arm_id] = run_arm(sample(cohort, 4000), exit_mode=xm, arm_id=arm_id, entry_label=elabel)
        ep_cohort = one_episode_one_entry(cohort)
        c_episode[arm_id] = run_arm(sample(ep_cohort, 4000), exit_mode=xm, arm_id=f"{arm_id}_EP", entry_label=f"{elabel}_EP")

    # attribution vs C0 baseline
    def pnl(arm: dict) -> float:
        return float(arm.get("pnl_5bps") or 0)

    base = pnl(c_event["C0"])
    attr = {
        "ENTRY_MOMENTUM_NO_EDGE": base,  # C0 itself
        "CANONICAL_BOARD_ENTRY_DELTA": pnl(c_event["C4"]) - pnl(c_event["C0"]),  # E2 vs E0 at X0
        "BOARD_EXIT_X3_DELTA": pnl(c_event["C7"]) - pnl(c_event["C6"]),  # X3 vs X2 on E2
        "BOARD_EXIT_X4_DELTA": pnl(c_event["C8"]) - pnl(c_event["C7"]),
        "PRICE_TRAIL_VS_X0": pnl(c_event["C6"]) - pnl(c_event["C4"]),
        "REENTRY_R1_DELTA": pnl(reentry["R1_one_ep"]) - pnl(reentry["R0_event"]),
        "spread_consumed_stops": spread_cls.get("SPREAD_CONSUMED_STOP", 0),
        "false_collapse_X4": exit_controls["X4"].get("false_collapse", 0),
    }

    # root cause codes
    causes = []
    if opp_e0.get("never_profitable_rate", 1) >= 0.55 or (opp_e0.get("avg_mfe_5m") or 0) <= 0.15:
        causes.append(("ENTRY_MOMENTUM_NO_EDGE", abs(min(0, base))))
    board_delta = attr["CANONICAL_BOARD_ENTRY_DELTA"]
    if board_delta < -10000:
        causes.append(("CANONICAL_BOARD_ENTRY_HARMFUL", abs(board_delta)))
    elif abs(board_delta) < 5000:
        causes.append(("CANONICAL_BOARD_ENTRY_NEUTRAL", abs(board_delta)))
    x3d = attr["BOARD_EXIT_X3_DELTA"]
    x4d = attr["BOARD_EXIT_X4_DELTA"]
    if x3d < -20000 or x4d < -20000:
        causes.append(("BOARD_EXIT_FALSE_COLLAPSE", abs(min(x3d, x4d))))
        causes.append(("BOARD_EXIT_SIGNAL_CHATTER", abs(min(x3d, x4d)) * 0.5))
    if spread_cls.get("SPREAD_CONSUMED_STOP", 0) > 50:
        causes.append(("SPREAD_DOMINATED", spread_cls.get("SPREAD_CONSUMED_STOP", 0) * 1000))
    r1d = attr["REENTRY_R1_DELTA"]
    if r1d > 50000:
        causes.append(("REENTRY_TURNOVER_DOMINATED", r1d))

    if len(causes) >= 2:
        primary = "MULTIPLE_ROOT_CAUSES"
    elif causes:
        primary = causes[0][0]
    else:
        primary = "MULTIPLE_ROOT_CAUSES"

    # next decision A–E
    e0_price_ok = (pnl(c_event["C0"]) > 0 and (c_event["C0"].get("PF_5bps") or 0) not in (None,) and float(c_event["C0"].get("PF_5bps") or 0) > 1) or (
        pnl(c_event["C2"]) > 0 and float(c_event["C2"].get("PF_5bps") or 0) > 1
    )
    pf_c0 = c_event["C0"].get("PF_5bps")
    pf_c2 = c_event["C2"].get("PF_5bps")
    try:
        pf_c0_f = float(pf_c0) if pf_c0 not in (None, float("inf")) else None
    except Exception:
        pf_c0_f = None
    try:
        pf_c2_f = float(pf_c2) if pf_c2 not in (None, float("inf")) else None
    except Exception:
        pf_c2_f = None

    decisions = []
    e0_quality_bad = (
        (opp_e0.get("never_profitable_rate") or 0) >= 0.5
        or (opp_e0.get("avg_mfe_5m") or 0) <= 0.30
        or (opp_e0.get("exec_pos_mfe_rate") or 0) < 0.55
    )
    price_exit_no_edge = (pf_c0_f is None or pf_c0_f <= 1) and (pf_c2_f is None or pf_c2_f <= 1)
    if e0_quality_bad and price_exit_no_edge:
        decisions.append("PBV2_MOMENTUM_CORE_REJECT")
    else:
        decisions.append("PBV2_MOMENTUM_CORE_PROVISIONAL")
    if board_delta < -10000:
        decisions.append("CANONICAL_BOARD_ENTRY_COMPONENT_REJECT")
    else:
        decisions.append("CANONICAL_BOARD_ENTRY_COMPONENT_PROVISIONAL")
    # Board EXIT architecture: reject if realtime board EXIT (X4) materially worsens vs price trailing
    if x4d < -20000 or (pnl(c_event["C6"]) > pnl(c_event["C8"]) + 20000):
        decisions.append("CURRENT_BOARD_EXIT_ARCHITECTURE_REJECT")
    elif x3d < -20000:
        decisions.append("CURRENT_BOARD_EXIT_ARCHITECTURE_REJECT")
    else:
        decisions.append("CURRENT_BOARD_EXIT_ARCHITECTURE_PROVISIONAL")
    if r1d > 50000:
        decisions.append("REENTRY_EPISODE_CONTROL_REQUIRED")
    all_neg = all(pnl(c_event[k]) <= 0 for k in c_event)
    if all_neg:
        decisions.append("CANONICAL_STRATEGY_REBUILD_REQUIRED")
    if spread_cls.get("SPREAD_CONSUMED_STOP", 0) > 100:
        decisions.append("SPREAD_EXECUTION_BLOCKED")
    decisions += ["CAPTURE_ONLY_CONTINUE", "NO_PAPER_ENTRY", "LIVE_TRADING_BLOCKED", "CANONICAL_ROOT_CAUSE_CLOSED"]

    # determinism check
    d1 = run_arm(sample(e2, 500), exit_mode="X0", arm_id="DET1", entry_label="E2")
    d2 = run_arm(sample(e2, 500), exit_mode="X0", arm_id="DET2", entry_label="E2")
    det = d1.get("pnl_5bps") == d2.get("pnl_5bps") and d1.get("trades") == d2.get("trades")

    return {
        "days": ordered,
        "stats": stats,
        "cohort_counts": {"E0": len(e0), "E1": len(e1), "E2": len(e2), "E3_diagnostic": "quantiles"},
        "opportunity": {"E0": opp_e0, "E1": opp_e1, "E2": opp_e2},
        "board_quantiles": quant,
        "exit_controls": exit_controls,
        "exit_audit_sample": exit_audit[:200],
        "immediate_exit": {
            "X3": {k: exit_controls["X3"].get(k) for k in ("exit_0_1s", "exit_1_5s", "exit_5_30s", "false_collapse")},
            "X4": {k: exit_controls["X4"].get(k) for k in ("exit_0_1s", "exit_1_5s", "exit_5_30s", "false_collapse")},
        },
        "spread_stop": {"n": len(spread_rows), "class_counts": dict(spread_cls), "sample": spread_rows[:80]},
        "episodes": {
            "raw_e2": len(e2),
            "true_episodes": len(ep_ids),
            "candidates_per_episode": reentry["candidates_per_episode"],
            "same_episode_reentry": reentry_same,
        },
        "reentry": {k: {kk: vv for kk, vv in v.items() if kk not in ("event_log_sample", "trade_sample", "daily_pnl", "leave_one_day_out_pf")} if isinstance(v, dict) else v for k, v in reentry.items()},
        "C_event": {k: {kk: vv for kk, vv in v.items() if kk not in ("event_log_sample", "trade_sample", "daily_pnl", "leave_one_day_out_pf")} for k, v in c_event.items()},
        "C_episode": {k: {kk: vv for kk, vv in v.items() if kk not in ("event_log_sample", "trade_sample", "daily_pnl", "leave_one_day_out_pf")} for k, v in c_episode.items()},
        "attribution": attr,
        "primary_root_cause": primary,
        "causes": causes,
        "decisions": decisions,
        "determinism_pass": det,
        "parity": {
            "LEGACY_REPLAY_DETERMINISM_PASS": det,
            "LEGACY_RUNTIME_PARITY_PASS": False,
            "LEGACY_RUNTIME_PARITY_NOT_EVALUABLE": True,
            "note": "No frozen Paper session accepted_rows for 20260721-24; runtime parity not evaluable",
        },
    }
