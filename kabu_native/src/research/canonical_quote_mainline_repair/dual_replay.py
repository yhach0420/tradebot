"""Legacy vs canonical dual replay on raw PUSH (P0–P3 CAP=5)."""
from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional
from zoneinfo import ZoneInfo

from research.canonical_quote_mainline_repair.constants import (
    AUDIT_DAYS,
    BOARD_P33,
    BOARD_P66,
    BOARD_SPLIT_PERCENTILE,
    CAP,
    CAPTURE_ROOT,
    COST_BPS,
    HARD_STOP_PCT,
    LOT,
    MAX_HOLD_SEC,
    MOMENTUM_P33,
    OPERATIONAL_EXIT_REASONS,
    SAMPLE_STRIDE,
)
from small_paper.board_dynamic_trailing_shadow import (
    board_tier_from_percentile,
    trailing_params_for_board_tier,
)
from small_paper.canonical_board import (
    board_token_from_imbalance,
    buy_limit_price,
    legacy_mixed_imbalance,
    normalize_kabu_board,
    sell_limit_price,
)

JST = ZoneInfo("Asia/Tokyo")


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


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def discover_capture_days(root: Path = CAPTURE_ROOT) -> list[str]:
    days = []
    if not root.exists():
        return list(AUDIT_DAYS)
    for p in sorted(root.iterdir()):
        if p.is_dir() and p.name.isdigit() and len(p.name) == 8:
            if any(p.glob("push_part_*.jsonl")):
                days.append(p.name)
    return days if days else list(AUDIT_DAYS)


def iter_capture_events(day: str, *, stride: int = SAMPLE_STRIDE) -> Iterator[dict[str, Any]]:
    day_dir = CAPTURE_ROOT / day
    if not day_dir.exists():
        return
    for fp in sorted(day_dir.glob("push_part_*.jsonl")):
        try:
            with fp.open("r", encoding="utf-8", errors="ignore") as f:
                for i, line in enumerate(f):
                    if i % max(1, stride) != 0:
                        continue
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    op = rec.get("original_payload")
                    if not isinstance(op, dict):
                        continue
                    if not isinstance(op.get("Buy1"), dict) or not isinstance(op.get("Sell1"), dict):
                        continue
                    px = _f(op.get("CurrentPrice") if op.get("CurrentPrice") is not None else rec.get("current_price"))
                    if px is None or px <= 0:
                        continue
                    ts = _parse_ts(rec.get("received_at_jst")) or _parse_ts(op.get("CurrentPriceTime"))
                    if ts is None:
                        continue
                    sym = str(rec.get("symbol") or op.get("Symbol") or "")
                    if not sym.endswith(".T") and sym:
                        sym = f"{sym}.T" if sym.isdigit() or sym[:-1].isdigit() else sym
                    yield {
                        "day": day,
                        "symbol": sym,
                        "ts": ts,
                        "px": px,
                        "payload": op,
                        "event_id": f"{day}:{sym}:{ts.isoformat()}:{rec.get('sequence') or i}",
                        "sequence": rec.get("sequence") or i,
                        "source_file": fp.name,
                        "source_row": i,
                    }
        except Exception:
            continue


@dataclass
class Candidate:
    day: str
    symbol: str
    event_id: str
    entry_time: datetime
    entry_px_mark: float
    entry_px_buy_ask: float
    mom: float
    leg_imb: float
    can_depth_imb: float
    can_top_imb: float
    leg_token: str
    can_token: str
    leg_accept: bool
    can_accept: bool
    session: str
    path: list[tuple[datetime, float, dict]] = field(default_factory=list)  # filled later


@dataclass
class TradeRow:
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
    mfe_capture: Optional[float]
    entry_mode: str
    exit_mode: str
    portfolio: str
    session: str
    setup_id: str
    operational: bool
    leg_imb: float
    can_imb: float
    leg_tier: str
    can_tier: str
    stop: bool
    early_stop: bool
    no_progress: bool
    winner: bool


def _session(t: datetime) -> str:
    return "AM" if t.hour < 12 else "PM"


def _mom_score(prices: list[float]) -> Optional[float]:
    """Proxy momentum_continuation_score in [0,1]: low = calm continuation."""
    if len(prices) < 8:
        return None
    window = prices[-20:]
    rets = []
    for a, b in zip(window, window[1:]):
        if a > 0:
            rets.append((b - a) / a)
    if not rets:
        return None
    vol = sum(abs(r) for r in rets) / len(rets)
    # map vol→score; lower vol → lower score (Momentum:low when < p33)
    return max(0.0, min(1.0, vol * 50.0))


def _pnl_yen(entry: float, exit_: float, *, side: str = "long") -> float:
    raw = (exit_ - entry) * LOT if side == "long" else (entry - exit_) * LOT
    cost = entry * LOT * COST_BPS / 10000.0 + exit_ * LOT * COST_BPS / 10000.0
    return raw - cost


def _simulate_exit(
    path: list[tuple[datetime, float, dict]],
    *,
    entry_time: datetime,
    entry_price: float,
    entry_imb: float,
    prior_imbs: list[float],
    exit_mode: str,  # legacy | canonical
    hard_stop_pct: float = HARD_STOP_PCT,
) -> dict[str, Any]:
    """Board Dynamic Trailing + hard stop; collapse uses top imb vs entry top."""
    # percentile among prior accepts
    if prior_imbs:
        le = sum(1 for s in prior_imbs if s <= entry_imb)
        pct = 100.0 * le / len(prior_imbs)
    else:
        pct = 50.0
    act, gb, tier = trailing_params_for_board_tier(pct)
    stop_px = entry_price * (1.0 - hard_stop_pct / 100.0)
    peak_pnl = 0.0
    activated = False
    mfe = 0.0
    mae = 0.0
    entry_top = None
    if path:
        board0 = normalize_kabu_board(path[0][2])
        if exit_mode == "legacy":
            bq = _f(path[0][2].get("BidQty")) or 0.0
            aq = _f(path[0][2].get("AskQty")) or 0.0
            tot = bq + aq
            entry_top = (bq / tot) if tot > 0 else None
        else:
            entry_top = board0.canonical_top_imbalance

    last_ts = entry_time
    last_px = entry_price
    reason = "capture_end"
    for ts, px, op in path:
        if ts <= entry_time:
            continue
        hold = (ts - entry_time).total_seconds()
        if hold > MAX_HOLD_SEC:
            reason = "capture_end"
            last_ts, last_px = ts, px
            break
        # session boundary
        if _session(ts) != _session(entry_time):
            reason = "session_close"
            last_ts, last_px = ts, px
            break
        pnl_pct = (px - entry_price) / entry_price * 100.0
        mfe = max(mfe, pnl_pct)
        mae = min(mae, pnl_pct)
        if px <= stop_px:
            # fill sell at bid
            if exit_mode == "canonical":
                sp = sell_limit_price(op, mode="canonical") or px
            else:
                sp = _f(op.get("BidPrice")) or px
            return {
                "exit_time": ts,
                "exit_price": float(sp),
                "exit_reason": "hard_stop",
                "tier": tier,
                "activate": act,
                "giveback": gb,
                "pct": pct,
                "mfe": mfe,
                "mae": mae,
                "operational": False,
            }
        # collapse / profit protect on top imb
        board = normalize_kabu_board(op)
        if exit_mode == "legacy":
            bq = _f(op.get("BidQty")) or 0.0
            aq = _f(op.get("AskQty")) or 0.0
            tot = bq + aq
            cur_top = (bq / tot) if tot > 0 else None
        else:
            cur_top = board.canonical_top_imbalance
        if entry_top is not None and cur_top is not None:
            delta = cur_top - entry_top
            if pnl_pct > 0 and delta <= -0.08:
                sp = sell_limit_price(op, mode="canonical" if exit_mode == "canonical" else "legacy") or px
                return {
                    "exit_time": ts,
                    "exit_price": float(sp),
                    "exit_reason": "board_collapse_profit_exit",
                    "tier": tier,
                    "activate": act,
                    "giveback": gb,
                    "pct": pct,
                    "mfe": mfe,
                    "mae": min(mae, pnl_pct),
                    "operational": False,
                }
            if pnl_pct > 0 and delta <= -0.05:
                sp = sell_limit_price(op, mode="canonical" if exit_mode == "canonical" else "legacy") or px
                return {
                    "exit_time": ts,
                    "exit_price": float(sp),
                    "exit_reason": "profit_protect_exit",
                    "tier": tier,
                    "activate": act,
                    "giveback": gb,
                    "pct": pct,
                    "mfe": mfe,
                    "mae": mae,
                    "operational": False,
                }
        # trailing
        if pnl_pct > peak_pnl:
            peak_pnl = pnl_pct
        if not activated and peak_pnl >= act:
            activated = True
        if activated and peak_pnl > 0 and pnl_pct <= peak_pnl * (1.0 - gb):
            sp = sell_limit_price(op, mode="canonical" if exit_mode == "canonical" else "legacy") or px
            return {
                "exit_time": ts,
                "exit_price": float(sp),
                "exit_reason": "trailing_mfe",
                "tier": tier,
                "activate": act,
                "giveback": gb,
                "pct": pct,
                "mfe": mfe,
                "mae": mae,
                "operational": False,
            }
        last_ts, last_px = ts, px
    op_flag = reason in OPERATIONAL_EXIT_REASONS
    return {
        "exit_time": last_ts,
        "exit_price": last_px,
        "exit_reason": reason,
        "tier": tier,
        "activate": act,
        "giveback": gb,
        "pct": pct,
        "mfe": mfe,
        "mae": mae,
        "operational": op_flag,
    }


def build_candidates(days: list[str], *, stride: int = SAMPLE_STRIDE) -> tuple[list[Candidate], dict[str, Any]]:
    """Scan capture → board-gated PBv2-like candidates (Momentum:low + Board mid|high).

    Two-pass: (1) load per-symbol streams (2) attach future path for EXIT sim.
    """
    cands: list[Candidate] = []
    stats = {"n_events": 0, "n_mom_ok": 0, "n_leg_board": 0, "n_can_board": 0, "mapping_ok": 0}

    for day in days:
        streams: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for ev in iter_capture_events(day, stride=stride):
            stats["n_events"] += 1
            streams[ev["symbol"]].append(ev)

        for sym, events in streams.items():
            events.sort(key=lambda e: e["ts"])
            last_entry: Optional[datetime] = None
            prices: list[float] = []
            for idx, ev in enumerate(events):
                ts: datetime = ev["ts"]
                px = ev["px"]
                op = ev["payload"]
                prices.append(px)
                if len(prices) > 60:
                    prices = prices[-60:]

                board = normalize_kabu_board(op)
                if (
                    board.kabu_bid_price_raw is not None
                    and board.canonical_best_ask is not None
                    and abs(board.kabu_bid_price_raw - board.canonical_best_ask) < 1e-9
                    and board.kabu_ask_price_raw is not None
                    and board.canonical_best_bid is not None
                    and abs(board.kabu_ask_price_raw - board.canonical_best_bid) < 1e-9
                ):
                    stats["mapping_ok"] += 1

                mom = _mom_score(prices)
                if mom is None:
                    continue
                mom_low = mom < MOMENTUM_P33
                if mom_low:
                    stats["n_mom_ok"] += 1

                leg_imb = legacy_mixed_imbalance(op)
                can_imb = board.canonical_depth_imbalance
                if leg_imb is None or can_imb is None:
                    continue
                leg_tok = board_token_from_imbalance(leg_imb, p33=BOARD_P33, p66=BOARD_P66)
                can_tok = board_token_from_imbalance(can_imb, p33=BOARD_P33, p66=BOARD_P66)
                leg_board = leg_tok in ("Board:mid", "Board:high")
                can_board = can_tok in ("Board:mid", "Board:high")
                if leg_board:
                    stats["n_leg_board"] += 1
                if can_board:
                    stats["n_can_board"] += 1

                leg_accept = mom_low and leg_board
                can_accept = mom_low and can_board
                if not (leg_accept or can_accept):
                    continue
                if last_entry is not None and (ts - last_entry).total_seconds() < 60:
                    continue
                last_entry = ts

                # future path for EXIT (cap length); require at least one post-entry tick
                future = events[idx : idx + 400]
                if len(future) < 2:
                    continue
                path = [(e["ts"], e["px"], e["payload"]) for e in future]
                buy_px = buy_limit_price(op, mode="canonical") or px
                cands.append(
                    Candidate(
                        day=day,
                        symbol=sym,
                        event_id=ev["event_id"],
                        entry_time=ts,
                        entry_px_mark=px,
                        entry_px_buy_ask=float(buy_px),
                        mom=mom,
                        leg_imb=float(leg_imb),
                        can_depth_imb=float(can_imb),
                        can_top_imb=float(board.canonical_top_imbalance or 0.5),
                        leg_token=leg_tok,
                        can_token=can_tok,
                        leg_accept=leg_accept,
                        can_accept=can_accept,
                        session=_session(ts),
                        path=path,
                    )
                )
    return cands, stats


def _make_trade(
    c: Candidate,
    *,
    entry_mode: str,
    exit_mode: str,
    portfolio: str,
    prior_imbs: list[float],
) -> TradeRow:
    entry_px = c.entry_px_buy_ask if entry_mode == "canonical" else c.entry_px_mark
    # For legacy entry fill approximation use CurrentPrice mark (paper historically)
    if entry_mode == "legacy":
        entry_px = c.entry_px_mark
    imb = c.leg_imb if exit_mode == "legacy" else c.can_depth_imb
    ex = _simulate_exit(
        c.path,
        entry_time=c.entry_time,
        entry_price=entry_px,
        entry_imb=imb,
        prior_imbs=prior_imbs,
        exit_mode=exit_mode,
    )
    pnl = _pnl_yen(entry_px, float(ex["exit_price"]))
    hold = (ex["exit_time"] - c.entry_time).total_seconds()
    mfe = float(ex["mfe"])
    mae = float(ex["mae"])
    pnl_pct = (float(ex["exit_price"]) - entry_px) / entry_px * 100.0 if entry_px else 0.0
    capt = (pnl_pct / mfe) if mfe > 1e-9 else None
    reason = str(ex["exit_reason"])
    stop = reason == "hard_stop"
    early = stop and hold <= 60
    np_ = reason in ("capture_end", "session_close") and mfe < 0.3
    winner = pnl > 0 and mfe >= 0.8
    return TradeRow(
        day=c.day,
        symbol=c.symbol,
        entry_time=c.entry_time,
        exit_time=ex["exit_time"],
        entry_price=entry_px,
        exit_price=float(ex["exit_price"]),
        exit_reason=reason,
        pnl_5bps=round(pnl, 2),
        hold_sec=hold,
        mfe_pct=mfe,
        mae_pct=mae,
        mfe_capture=capt,
        entry_mode=entry_mode,
        exit_mode=exit_mode,
        portfolio=portfolio,
        session=c.session,
        setup_id=f"{portfolio}:{c.event_id}",
        operational=bool(ex["operational"]),
        leg_imb=c.leg_imb,
        can_imb=c.can_depth_imb,
        leg_tier=board_tier_from_percentile(
            (100.0 * sum(1 for s in prior_imbs if s <= c.leg_imb) / len(prior_imbs)) if prior_imbs else 50.0
        ),
        can_tier=board_tier_from_percentile(
            (100.0 * sum(1 for s in prior_imbs if s <= c.can_depth_imb) / len(prior_imbs)) if prior_imbs else 50.0
        ),
        stop=stop,
        early_stop=early,
        no_progress=np_,
        winner=winner,
    )


def replay_cap5(trades: list[TradeRow], *, portfolio_id: str, cap: int = CAP) -> dict[str, Any]:
    events: list[tuple[datetime, int, str, TradeRow]] = []
    for t in trades:
        events.append((t.entry_time, 1, "ENTRY", t))
        events.append((t.exit_time, 0, "EXIT", t))
    events.sort(key=lambda e: (e[0], e[1], e[3].symbol, e[3].setup_id))

    open_pos: dict[str, TradeRow] = {}
    open_sym: set[tuple[str, str]] = set()
    accepted: list[TradeRow] = []
    blocked_cap = 0
    blocked_sym = 0
    event_log: list[dict[str, Any]] = []

    for ts, _o, kind, t in events:
        if kind == "EXIT":
            if t.setup_id in open_pos:
                open_pos.pop(t.setup_id)
                open_sym.discard((t.day, t.symbol))
                accepted.append(t)
                event_log.append({
                    "ts": ts.isoformat(), "event": "EXIT", "portfolio": portfolio_id,
                    "symbol": t.symbol, "day": t.day, "reason": t.exit_reason, "pnl": t.pnl_5bps,
                    "operational": t.operational,
                })
            continue
        if (t.day, t.symbol) in open_sym:
            blocked_sym += 1
            continue
        if len(open_pos) >= cap:
            blocked_cap += 1
            continue
        open_pos[t.setup_id] = t
        open_sym.add((t.day, t.symbol))
        event_log.append({
            "ts": ts.isoformat(), "event": "ENTRY", "portfolio": portfolio_id,
            "symbol": t.symbol, "day": t.day, "active": len(open_pos),
        })

    return _summarize_portfolio(accepted, portfolio_id=portfolio_id, n_cand=len(trades), cap_blocked=blocked_cap, sym_blocked=blocked_sym, event_log=event_log)


def _summarize_portfolio(
    trades: list[TradeRow],
    *,
    portfolio_id: str,
    n_cand: int,
    cap_blocked: int,
    sym_blocked: int,
    event_log: list[dict[str, Any]],
) -> dict[str, Any]:
    pnls = [t.pnl_5bps for t in trades]
    gp = sum(p for p in pnls if p > 0)
    gl = sum(p for p in pnls if p < 0)
    pf = (gp / abs(gl)) if gl < 0 else (None if not pnls else float("inf") if gp > 0 else None)
    days = sorted({t.day for t in trades})
    by_day: dict[str, float] = defaultdict(float)
    for t in trades:
        by_day[t.day] += t.pnl_5bps
    pos_d = sum(1 for d, v in by_day.items() if v > 0)
    neg_d = sum(1 for d, v in by_day.items() if v <= 0)
    # trade-sequence DD
    eq = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        eq += p
        peak = max(peak, eq)
        max_dd = min(max_dd, eq - peak)
    # symbol dependency
    by_sym: dict[str, float] = defaultdict(float)
    for t in trades:
        by_sym[t.symbol] += t.pnl_5bps
    top_syms = sorted(by_sym.items(), key=lambda x: -x[1])[:5]
    # leave-one-day-out PF
    lodo = {}
    for d in days:
        sub = [t.pnl_5bps for t in trades if t.day != d]
        gpp = sum(p for p in sub if p > 0)
        gll = sum(p for p in sub if p < 0)
        lodo[d] = (gpp / abs(gll)) if gll < 0 else None
    holds = [t.hold_sec for t in trades]
    return {
        "portfolio_id": portfolio_id,
        "candidates": n_cand,
        "accepted": len(trades),
        "cap_blocked": cap_blocked,
        "same_symbol_blocked": sym_blocked,
        "trades": len(trades),
        "trades_per_day": (len(trades) / len(days)) if days else 0.0,
        "gross_profit": round(gp, 2),
        "gross_loss": round(gl, 2),
        "pnl_5bps": round(sum(pnls), 2),
        "PF_5bps": round(pf, 4) if isinstance(pf, float) and math.isfinite(pf) else pf,
        "win_rate": (sum(1 for p in pnls if p > 0) / len(pnls)) if pnls else None,
        "stop_rate": (sum(1 for t in trades if t.stop) / len(trades)) if trades else None,
        "early_stop_rate": (sum(1 for t in trades if t.early_stop) / len(trades)) if trades else None,
        "no_progress_rate": (sum(1 for t in trades if t.no_progress) / len(trades)) if trades else None,
        "winner_rate": (sum(1 for t in trades if t.winner) / len(trades)) if trades else None,
        "avg_mfe": (sum(t.mfe_pct for t in trades) / len(trades)) if trades else None,
        "avg_mae": (sum(t.mae_pct for t in trades) / len(trades)) if trades else None,
        "avg_mfe_capture": (
            sum(t.mfe_capture for t in trades if t.mfe_capture is not None)
            / max(1, sum(1 for t in trades if t.mfe_capture is not None))
        ) if trades else None,
        "avg_hold": (sum(holds) / len(holds)) if holds else None,
        "median_hold": (sorted(holds)[len(holds) // 2] if holds else None),
        "trade_sequence_dd": round(max_dd, 2),
        "pos_days": pos_d,
        "neg_days": neg_d,
        "daily_pnl": dict(by_day),
        "top_symbols": top_syms,
        "leave_one_day_out_pf": lodo,
        "event_log_n": len(event_log),
        "event_log_sample": event_log[:50],
        "trade_sample": [
            {
                "day": t.day, "symbol": t.symbol, "entry": t.entry_time.isoformat(),
                "exit": t.exit_time.isoformat(), "reason": t.exit_reason, "pnl": t.pnl_5bps,
                "mfe": t.mfe_pct, "operational": t.operational,
            }
            for t in trades[:40]
        ],
    }


def run_dual_replay(days: Optional[list[str]] = None, *, stride: int = SAMPLE_STRIDE) -> dict[str, Any]:
    days = days or discover_capture_days()
    # Prefer required days first
    ordered = [d for d in AUDIT_DAYS if d in days] + [d for d in days if d not in AUDIT_DAYS]
    cands, stats = build_candidates(ordered, stride=stride)

    # ENTRY diffs
    only_leg = [c for c in cands if c.leg_accept and not c.can_accept]
    only_can = [c for c in cands if c.can_accept and not c.leg_accept]
    both = [c for c in cands if c.leg_accept and c.can_accept]
    token_flip = sum(1 for c in cands if c.leg_token != c.can_token)

    entry_diff = {
        "candidates_union": len(cands),
        "legacy_accept": sum(1 for c in cands if c.leg_accept),
        "canonical_accept": sum(1 for c in cands if c.can_accept),
        "only_legacy": len(only_leg),
        "only_canonical": len(only_can),
        "both": len(both),
        "token_flip": token_flip,
        "token_flip_rate": token_flip / len(cands) if cands else None,
        "gate_flip": len(only_leg) + len(only_can),
    }

    # Build trade sets
    def build_set(entry_mode: str, exit_mode: str, portfolio: str, accept_fn) -> list[TradeRow]:
        rows: list[TradeRow] = []
        prior: list[float] = []
        for c in sorted([x for x in cands if accept_fn(x)], key=lambda x: (x.day, x.entry_time, x.symbol)):
            imb_for_pct = c.leg_imb if exit_mode == "legacy" else c.can_depth_imb
            tr = _make_trade(c, entry_mode=entry_mode, exit_mode=exit_mode, portfolio=portfolio, prior_imbs=list(prior))
            rows.append(tr)
            prior.append(imb_for_pct)
            if len(prior) > 500:
                prior = prior[-500:]
        return rows

    t_p0 = build_set("legacy", "legacy", "P0", lambda c: c.leg_accept)
    t_p1 = build_set("canonical", "legacy", "P1", lambda c: c.can_accept)
    t_p2 = build_set("legacy", "canonical", "P2", lambda c: c.leg_accept)
    t_p3 = build_set("canonical", "canonical", "P3", lambda c: c.can_accept)

    # Deterministic: re-run P0
    t_p0_b = build_set("legacy", "legacy", "P0", lambda c: c.leg_accept)
    det_ok = len(t_p0) == len(t_p0_b) and all(
        a.pnl_5bps == b.pnl_5bps and a.exit_reason == b.exit_reason for a, b in zip(t_p0, t_p0_b)
    )

    p0 = replay_cap5(t_p0, portfolio_id="P0")
    p1 = replay_cap5(t_p1, portfolio_id="P1")
    p2 = replay_cap5(t_p2, portfolio_id="P2")
    p3 = replay_cap5(t_p3, portfolio_id="P3")

    # EXIT decision diff on both-accept cohort
    exit_diffs = []
    for c in both[:80]:
        prior = [c.leg_imb]
        t_leg = _make_trade(c, entry_mode="legacy", exit_mode="legacy", portfolio="X0", prior_imbs=prior)
        t_can = _make_trade(c, entry_mode="legacy", exit_mode="canonical", portfolio="X1", prior_imbs=prior)
        exit_diffs.append({
            "event_id": c.event_id,
            "symbol": c.symbol,
            "leg_exit": t_leg.exit_reason,
            "can_exit": t_can.exit_reason,
            "leg_time": t_leg.exit_time.isoformat(),
            "can_time": t_can.exit_time.isoformat(),
            "leg_pnl": t_leg.pnl_5bps,
            "can_pnl": t_can.pnl_5bps,
            "time_diff_sec": (t_can.exit_time - t_leg.exit_time).total_seconds(),
            "tier_flip": t_leg.leg_tier != t_can.can_tier,
        })

    # B2: depth NOT_TRANSFORMABLE; top transformable
    board_class = {
        "B0_LEGACY": "reproduced via legacy_mixed_imbalance + legacy top",
        "B1_CANONICAL_FIXED_THRESHOLD": "canonical_depth_imbalance with same p33/p66",
        "B2_CANONICAL_SEMANTIC_TRANSFORM": "NOT_TRANSFORMABLE for depth-mixed; top-only threshold = 1 - legacy",
        "depth_transformable": False,
        "top_transformable": True,
        "top_transform_example": {"legacy_split_pct": BOARD_SPLIT_PERCENTILE, "canonical_split_pct": 100.0 - BOARD_SPLIT_PERCENTILE},
    }

    return {
        "days": ordered,
        "stats": stats,
        "entry_diff": entry_diff,
        "exit_diff_sample": exit_diffs,
        "board_classification": board_class,
        "P0": p0,
        "P1": p1,
        "P2": p2,
        "P3": p3,
        "deterministic_p0": det_ok,
        "entry_traces": [
            {
                "event_id": c.event_id, "symbol": c.symbol, "day": c.day,
                "mom": c.mom, "leg_imb": c.leg_imb, "can_depth": c.can_depth_imb,
                "leg_token": c.leg_token, "can_token": c.can_token,
                "leg_accept": c.leg_accept, "can_accept": c.can_accept,
            }
            for c in (only_leg[:20] + only_can[:20] + both[:20])
        ],
        "mapping_ok_rate": (stats["mapping_ok"] / stats["n_events"]) if stats["n_events"] else None,
    }
