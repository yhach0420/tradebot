#!/usr/bin/env python3
"""
Phase306: Token fire-rate and profit attribution on replay trades (20260518–20260603).

Output: kabu_native/results/reports/phase306_token_fire_rate_profit_attribution.json
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
SMALL_PAPER = REPO / "kabu_native" / "results" / "small_paper"
OUT = REPO / "kabu_native/results/reports/phase306_token_fire_rate_profit_attribution.json"
CHECKPOINT = REPO / "kabu_native/results/reports/phase306_token_fire_rate_profit_attribution.checkpoint.json"

DATE_START = 20260518
DATE_END = 20260603
V2_MIN = 5
MAX_POS = 3
V1_MODE = "legacy"
V1_RATIO = 0.85
REJECT_REPLAY_MAX_EVENTS = 500_000
MIN_COMBO_TRADES = 3
JST = ZoneInfo("Asia/Tokyo")

TARGET_TOKENS = (
    "HBRecent:no",
    "Momentum:low",
    "Price:high",
    "TV:mid",
    "Board:mid",
    "Duration:high",
)

BASE_HARD_EXCLUDE = frozenset(
    {
        "symbol_cooloff",
        "risk_cluster_block",
        "daily_loss_guard",
        "wrong_profile",
        "outside_allowed_trading_window",
        "low_liquidity_shadow",
        "low_liquidity_shadow_reject",
    }
)

AUX_FILTER = {
    "hard_exclude_extra": frozenset({"daytrade_suitability", "entry_price_risk_guard"}),
    "daytrade_mode": "on",
    "daytrade_percentile": 0.50,
    "price_risk_universe": True,
    "price_risk_guard": True,
}


def _bootstrap() -> Any:
    for p in (REPO / "kabu_native" / "src", REPO / "kabu_native" / "scripts", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    import run_phase270_fast_paper_integration_comparison as p270

    return p270


def _load_p71() -> Any:
    path = REPO / "kabu_native/scripts/run_phase71_split_momentum_fade_review.py"
    spec = importlib.util.spec_from_file_location("phase71_p306", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["phase71_p306"] = mod
    spec.loader.exec_module(mod)
    return mod


def _day_from_sid(sid: str) -> Optional[str]:
    parts = sid.replace("\\", "/").split("/")
    if parts and len(parts[0]) == 8 and parts[0].isdigit():
        return parts[0]
    return None


def _day_in_range(day: str) -> bool:
    try:
        return DATE_START <= int(day) <= DATE_END
    except ValueError:
        return False


def _skip_session(sid: str, event_count: int) -> Optional[str]:
    low = sid.lower()
    if "phase282_discord_flow" in low:
        return "phase282_test_harness"
    if "phase284_resim" in low or "phase285_resim" in low:
        return "phase284_285_resim_harness"
    if event_count > REJECT_REPLAY_MAX_EVENTS:
        return f"event_count>{REJECT_REPLAY_MAX_EVENTS}"
    return None


def _discover_sessions(p270: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    found: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for summary_path in sorted(SMALL_PAPER.rglob("small_paper_summary.json")):
        sid = summary_path.parent.relative_to(SMALL_PAPER).as_posix()
        day = _day_from_sid(sid)
        if not day or not _day_in_range(day):
            continue
        events = p270._load_events(summary_path.parent)
        if not events:
            continue
        skip = _skip_session(sid, len(events))
        if skip:
            skipped.append({"session_id": sid, "day": day, "event_count": len(events), "reason": skip})
            continue
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            summary = {}
        found.append({"session_id": sid, "day": day, "stream": p270._session_stream(sid, summary)})
    return found, skipped


def _quantile(xs: list[float], q: float) -> float:
    ys = sorted(xs)
    pos = (len(ys) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ys[lo]
    return ys[lo] + (ys[hi] - ys[lo]) * (pos - lo)


def _accepted_entry_duration_cutoffs(p270: Any, sessions: list[dict[str, Any]]) -> dict[str, float]:
    vals: list[float] = []
    for meta in sessions:
        for ev in p270._load_events(SMALL_PAPER / meta["session_id"]):
            if str(ev.get("event_type") or "") != "accepted":
                continue
            raw = p270._float(ev.get("max_continuation_duration"))
            if raw is not None:
                vals.append(float(raw))
    if not vals:
        return {"p33": 31.0, "p66": 109.0}
    return {"p33": round(_quantile(vals, 1.0 / 3.0), 4), "p66": round(_quantile(vals, 2.0 / 3.0), 4)}


class PriceRingTracker:
    def __init__(self) -> None:
        self.rings: dict[str, list[tuple[float, float]]] = {}

    def observe(self, ev: dict[str, Any], p270: Any) -> None:
        from small_paper.extended_entry_shadow import append_price_tick
        from storage.intraday_recorder import parse_kabu_time

        sym = str(ev.get("symbol") or "")
        px = p270._float(ev.get("current_price")) or 0.0
        ent = str(ev.get("entry_time") or ev.get("event_time") or "")
        if not sym or px <= 0 or not ent:
            return
        ts = parse_kabu_time(ent, fallback=datetime.now(JST)).timestamp()
        append_price_tick(self.rings.setdefault(sym, []), ts=ts, px=px)

    def hbrecent(self, ev: dict[str, Any], p270: Any) -> bool:
        from small_paper.extended_entry_shadow import compute_entry_high_break_recent_field
        from storage.intraday_recorder import parse_kabu_time

        sym = str(ev.get("symbol") or "")
        ent = str(ev.get("entry_time") or ev.get("event_time") or "")
        px = p270._float(ev.get("current_price")) or 0.0
        if not sym or not ent:
            return False
        ts = parse_kabu_time(ent, fallback=datetime.now(JST)).timestamp()
        return bool(
            compute_entry_high_break_recent_field(
                trade=ev,
                payload={"CurrentPrice": px},
                price_ring=self.rings.get(sym, []),
                entry_ts=ts,
            )["entry_high_break_recent"]
        )


def _board_from_event(ev: dict[str, Any]) -> Optional[float]:
    from small_paper.board_imbalance_shadow import compute_entry_order_book_imbalance_field

    payload: dict[str, Any] = {}
    for key in ("BidQty", "AskQty"):
        if ev.get(key) is not None:
            payload[key] = ev[key]
    if payload:
        return compute_entry_order_book_imbalance_field(payload=payload).get("entry_order_book_imbalance")
    logged = ev.get("entry_order_book_imbalance")
    if logged is None:
        return None
    try:
        return float(logged)
    except (TypeError, ValueError):
        return None


def _active_tokens(
    work: dict[str, Any],
    *,
    duration_p33: float,
    duration_p66: float,
) -> dict[str, bool]:
    from small_paper.entry_expectancy_score_shadow import SCORE_POINTS_V2, _bin_tertile, _float, _feature_token

    active: dict[str, bool] = {}
    for token in TARGET_TOKENS:
        pts = SCORE_POINTS_V2.get(token, 0)
        if pts <= 0:
            continue
        lbl = token.split(":", 1)[0]
        if lbl == "HBRecent":
            hb = work.get("entry_high_break_recent")
            if hb is None:
                active[token] = False
                continue
            tok = f"HBRecent:{'yes' if str(hb).lower() in ('true', '1', 'yes') else 'no'}"
        elif lbl == "Duration":
            v = _float(work.get("max_continuation_duration"))
            if v is None:
                active[token] = False
                continue
            level = _bin_tertile(v, duration_p33, duration_p66)
            tok = f"Duration:{level}"
        else:
            tok = _feature_token(lbl, work)
        active[token] = tok == token
    return active


def _tokens_from_ev(
    ev: dict[str, Any],
    ring: PriceRingTracker,
    p270: Any,
    *,
    duration_p33: float,
    duration_p66: float,
) -> tuple[int, list[str]]:
    from small_paper.entry_expectancy_score_shadow import SCORE_POINTS_V2

    work = dict(ev)
    work["entry_high_break_recent"] = ring.hbrecent(work, p270)
    imb = _board_from_event(ev)
    if imb is not None:
        work["entry_order_book_imbalance"] = imb
    active = _active_tokens(work, duration_p33=duration_p33, duration_p66=duration_p66)
    score = sum(SCORE_POINTS_V2[t] for t, on in active.items() if on)
    fired = [t for t in TARGET_TOKENS if active.get(t, False)]
    return score, fired


def _price_guard_state() -> Any:
    from small_paper.entry_price_risk_guard import EntryPriceRiskGuardConfig, EntryPriceRiskGuardState

    return EntryPriceRiskGuardState(
        config=EntryPriceRiskGuardConfig(
            enabled=True, min_entry_price=50.0, max_tick_ratio_pct=5.0, shadow_only=True
        )
    )


_DAYTRADE_CACHE: dict[tuple[str, float], Any] = {}


def _daytrade_state(p270: Any, session_id: str, percentile: float) -> Any:
    key = (session_id, round(float(percentile), 4))
    if key in _DAYTRADE_CACHE:
        return _DAYTRADE_CACHE[key]
    from small_paper.daytrade_suitability import percentile_value
    from small_paper.daytrade_suitability_gate import (
        DaytradeSuitabilityConfig,
        DaytradeSuitabilityState,
        discover_sessions_for_suitability_prior,
        prior_vol_liq_scores,
    )

    base = REPO / "kabu_native/results/small_paper"
    sources = discover_sessions_for_suitability_prior(base, before_session_key=session_id)
    scores, used = prior_vol_liq_scores(sources, repo_root=REPO)
    th = percentile_value(scores, percentile) if scores else None
    state = DaytradeSuitabilityState(
        config=DaytradeSuitabilityConfig(enabled=True),
        run_session_key=session_id,
        source_sessions=used,
        vol_liq_threshold=round(th, 6) if th is not None else None,
        prior_quality_trade_count=len(scores),
    )
    _DAYTRADE_CACHE[key] = state
    return state


@dataclass
class TradeRow:
    pnl_pct: float
    win: bool
    symbol: str
    day: str
    entry_score_v2: int
    active_tokens: list[str] = field(default_factory=list)


def _pf(pnls: list[float]) -> Any:
    wins = sum(p for p in pnls if p > 0)
    loss = abs(sum(p for p in pnls if p < 0))
    if loss <= 0:
        return None if wins <= 0 else "inf"
    return round(wins / loss, 4)


def _trade_metrics(rows: list[TradeRow]) -> dict[str, Any]:
    if not rows:
        return {
            "trade_count": 0,
            "win_rate": None,
            "profit_factor": None,
            "total_pnl_pct": 0.0,
            "avg_pnl_pct": None,
        }
    pnls = [r.pnl_pct for r in rows]
    n = len(rows)
    wins = sum(1 for r in rows if r.win)
    return {
        "trade_count": n,
        "win_rate": round(wins / n, 4),
        "profit_factor": _pf(pnls),
        "total_pnl_pct": round(sum(pnls), 4),
        "avg_pnl_pct": round(sum(pnls) / n, 6),
    }


def _pf_num(pf: Any) -> float:
    if pf is None:
        return 0.0
    if pf == "inf":
        return 99.0
    try:
        return float(pf)
    except (TypeError, ValueError):
        return 0.0


def _fire_rates(rows: list[TradeRow]) -> dict[str, Any]:
    n = len(rows)
    if n == 0:
        return {t: 0.0 for t in TARGET_TOKENS}
    counts = Counter()
    for r in rows:
        for t in r.active_tokens:
            counts[t] += 1
    return {t: round(counts[t] / n, 4) for t in TARGET_TOKENS}


def _token_yes_no(rows: list[TradeRow], token: str) -> dict[str, Any]:
    with_t = [r for r in rows if token in r.active_tokens]
    without_t = [r for r in rows if token not in r.active_tokens]
    return {
        "token": token,
        "with_token": _trade_metrics(with_t),
        "without_token": _trade_metrics(without_t),
    }


def _token_deltas(rows: list[TradeRow], token: str) -> dict[str, Any]:
    yn = _token_yes_no(rows, token)
    w = yn["with_token"]
    wo = yn["without_token"]
    d_wr = None
    if w["win_rate"] is not None and wo["win_rate"] is not None:
        d_wr = round(float(w["win_rate"]) - float(wo["win_rate"]), 4)
    d_pf = round(_pf_num(w["profit_factor"]) - _pf_num(wo["profit_factor"]), 4)
    d_pnl = round(float(w["total_pnl_pct"] or 0) - float(wo["total_pnl_pct"] or 0), 4)
    return {
        "token": token,
        "delta_win_rate_with_minus_without": d_wr,
        "delta_pf_with_minus_without": d_pf,
        "delta_pnl_pct_with_minus_without": d_pnl,
        "with_token": w,
        "without_token": wo,
    }


def _combo_label(tokens: list[str]) -> str:
    short = {
        "HBRecent:no": "HBRecent",
        "Momentum:low": "Momentum",
        "Price:high": "Price",
        "TV:mid": "TV",
        "Board:mid": "Board",
        "Duration:high": "Duration",
    }
    return " + ".join(short.get(t, t) for t in sorted(tokens))


def _combo_rankings(rows: list[TradeRow], *, top_n: int = 20) -> dict[str, list[dict[str, Any]]]:
    buckets: dict[tuple[str, ...], list[TradeRow]] = defaultdict(list)
    for r in rows:
        key = tuple(sorted(r.active_tokens))
        buckets[key].append(r)

    combos: list[dict[str, Any]] = []
    for key, grp in buckets.items():
        if len(grp) < MIN_COMBO_TRADES:
            continue
        m = _trade_metrics(grp)
        combos.append(
            {
                "tokens": list(key),
                "label": _combo_label(list(key)),
                **m,
            }
        )

    def _sort_key_pf(c: dict[str, Any]) -> float:
        return _pf_num(c.get("profit_factor"))

    def _sort_key_pnl(c: dict[str, Any]) -> float:
        return float(c.get("total_pnl_pct") or 0)

    def _sort_key_wr(c: dict[str, Any]) -> float:
        return float(c.get("win_rate") or 0)

    return {
        "by_profit_factor": sorted(combos, key=_sort_key_pf, reverse=True)[:top_n],
        "by_total_pnl_pct": sorted(combos, key=_sort_key_pnl, reverse=True)[:top_n],
        "by_win_rate": sorted(combos, key=_sort_key_wr, reverse=True)[:top_n],
    }


def _verdict(deltas: list[dict[str, Any]]) -> dict[str, Any]:
    ranked = sorted(
        deltas,
        key=lambda d: (
            float(d.get("delta_pnl_pct_with_minus_without") or 0),
            float(d.get("delta_pf_with_minus_without") or 0),
            float(d.get("delta_win_rate_with_minus_without") or 0),
        ),
        reverse=True,
    )
    least = sorted(
        deltas,
        key=lambda d: (
            float(d.get("delta_pnl_pct_with_minus_without") or 0),
            float(d.get("delta_pf_with_minus_without") or 0),
        ),
    )
    return {
        "most_profitable_tokens_top5": [d["token"] for d in ranked[:5]],
        "least_useful_tokens_top5": [d["token"] for d in least[:5]],
        "ranking_detail": ranked,
    }


def _json_safe(obj: Any) -> Any:
    if isinstance(obj, frozenset):
        return sorted(obj)
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


def _run_replay(
    p270: Any,
    p71: Any,
    sessions: list[dict[str, Any]],
    *,
    duration_p33: float,
    duration_p66: float,
) -> list[TradeRow]:
    sym_states: dict[str, Any] = {}
    active: dict[str, Any] = {}
    completed: list[TradeRow] = []
    ring = PriceRingTracker()
    pending_time: Optional[str] = None
    pending: list[tuple[dict[str, Any], int, list[str]]] = []
    day = ""
    universe_syms: set[str] = set()
    daytrade_state: Any = None
    price_guard = _price_guard_state()

    def _hard_exclude() -> frozenset[str]:
        return BASE_HARD_EXCLUDE | frozenset(AUX_FILTER.get("hard_exclude_extra") or [])

    def _pool_ok(ev: dict[str, Any]) -> bool:
        et = str(ev.get("event_type") or "")
        if et == "accepted":
            return True
        if et == "rejected":
            return str(ev.get("gate_reject_reason") or "") not in _hard_exclude()
        return False

    def _aux_fail(ev: dict[str, Any]) -> bool:
        sym = str(ev.get("symbol") or "")
        if AUX_FILTER.get("price_risk_universe") and universe_syms and sym not in universe_syms:
            return True
        if AUX_FILTER.get("price_risk_guard") and price_guard.check(ev).blocked:
            return True
        if daytrade_state is not None and daytrade_state.check(ev).blocked:
            return True
        return False

    def _try_open(item: tuple[dict[str, Any], int, list[str]]) -> None:
        ev, score, tokens = item
        sym = str(ev.get("symbol") or "")
        ent = str(ev.get("entry_time") or ev.get("event_time") or "")
        px = p270._float(ev.get("current_price")) or p270._float(ev.get("entry_price")) or 0.0
        if not sym or not ent or px <= 0 or score < V2_MIN or _aux_fail(ev):
            return
        if sym in active or len(active) >= MAX_POS:
            return
        ts = p71._parse_ts(ent)
        st = sym_states.setdefault(sym, p71.SymState())
        comps = p71._components(st, ts=ts, price=float(px), ev=ev)
        q = p270._float(ev.get("continuation_quality_score")) or 0.0
        tr = p71.StructuralTrade(sym, ent, float(px), float(q))
        act = p71.ActiveTrade(
            trade=tr,
            entry_ts=ts,
            rich_ticks=[
                {
                    "price": float(px),
                    "pnl_pct": 0.0,
                    "quality": comps["quality"],
                    "momentum": comps["momentum"],
                    "favorable": comps["favorable"],
                    "pure_price_momentum": comps["pure_price_momentum"],
                    "vwap_strength": comps["vwap_strength"],
                    "mfe_proxy": comps["mfe_proxy"],
                }
            ],
        )
        act._entry_score_v2 = score  # noqa: SLF001
        act._active_tokens = list(tokens)  # noqa: SLF001
        active[sym] = act

    def _flush() -> None:
        nonlocal pending
        if not pending:
            return
        for item in sorted(pending, key=lambda x: int(p270._float(x[0].get("message_index")) or 0)):
            _try_open(item)
        pending = []

    for i, meta in enumerate(sessions, 1):
        ring = PriceRingTracker()
        sym_states = {}
        active = {}
        pending = []
        pending_time = None
        day = meta["day"]
        universe_syms = (
            p270._load_universe_symbols(day, price_risk=True) if AUX_FILTER.get("price_risk_universe") else set()
        )
        daytrade_state = (
            _daytrade_state(p270, meta["session_id"], float(AUX_FILTER.get("daytrade_percentile") or 0.50))
            if AUX_FILTER.get("daytrade_mode") == "on"
            else None
        )
        events = p270._load_events(SMALL_PAPER / meta["session_id"])
        if not events:
            continue
        session_end = p71._session_end(events)
        ordered = sorted(
            events,
            key=lambda e: (
                p270._parse_ts(str(e.get("event_time") or "")),
                int(p270._float(e.get("message_index")) or 0),
            ),
        )
        for ev in ordered:
            ring.observe(ev, p270)
            et = str(ev.get("event_type") or "")
            sym = str(ev.get("symbol") or "")
            ent = str(ev.get("entry_time") or ev.get("event_time") or "")
            px = p270._float(ev.get("current_price")) or 0.0
            ev_time = str(ev.get("event_time") or "")
            if pending_time is None:
                pending_time = ev_time
            if ev_time != pending_time:
                _flush()
                pending_time = ev_time
            if et == "candidate":
                if sym not in active or px <= 0 or not ent:
                    continue
                ts = p71._parse_ts(ent)
                st = sym_states.setdefault(sym, p71.SymState())
                act = active[sym]
                comps = p71._components(st, ts=ts, price=float(px), ev=ev)
                act.rich_ticks.append(
                    {
                        "price": float(px),
                        "pnl_pct": p71._pnl_pct(act.trade.entry_price, float(px)),
                        "quality": comps["quality"],
                        "momentum": comps["momentum"],
                        "favorable": comps["favorable"],
                        "pure_price_momentum": comps["pure_price_momentum"],
                        "vwap_strength": comps["vwap_strength"],
                        "mfe_proxy": comps["mfe_proxy"],
                    }
                )
                sig = p71.simulate_combined_split(
                    act.rich_ticks,
                    act.trade.entry_price,
                    momentum_mode=V1_MODE,
                    ratio=V1_RATIO,
                    allow_session_end=False,
                )
                if sig:
                    _, reason, _ = sig
                    pnl = float(p71._pnl_pct(act.trade.entry_price, float(px)))
                    completed.append(
                        TradeRow(
                            pnl_pct=pnl,
                            win=pnl > 0,
                            symbol=sym,
                            day=day,
                            entry_score_v2=int(getattr(act, "_entry_score_v2", 0)),
                            active_tokens=list(getattr(act, "_active_tokens", [])),
                        )
                    )
                    active.pop(sym, None)
            elif _pool_ok(ev):
                score, tokens = _tokens_from_ev(
                    ev, ring, p270, duration_p33=duration_p33, duration_p66=duration_p66
                )
                pending.append((ev, score, tokens))
        _flush()
        for sym, act in list(active.items()):
            last_px = act.rich_ticks[-1]["price"] if act.rich_ticks else act.trade.entry_price
            pnl = float(p71._pnl_pct(act.trade.entry_price, float(last_px)))
            completed.append(
                TradeRow(
                    pnl_pct=pnl,
                    win=pnl > 0,
                    symbol=str(act.trade.symbol),
                    day=day,
                    entry_score_v2=int(getattr(act, "_entry_score_v2", 0)),
                    active_tokens=list(getattr(act, "_active_tokens", [])),
                )
            )
        if i % 5 == 0 or i == len(sessions):
            print(f"  [{i}/{len(sessions)}] trades={len(completed)}", flush=True)
    return completed


def main() -> int:
    p270 = _bootstrap()
    p71 = _load_p71()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    sessions, skipped = _discover_sessions(p270)
    dur = _accepted_entry_duration_cutoffs(p270, sessions)
    p33 = float(dur["p33"])
    p66 = float(dur["p66"])

    trade_rows: list[TradeRow] = []
    if CHECKPOINT.is_file():
        try:
            ck = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
            trade_rows = [
                TradeRow(
                    pnl_pct=float(r["pnl_pct"]),
                    win=bool(r["win"]),
                    symbol=str(r["symbol"]),
                    day=str(r["day"]),
                    entry_score_v2=int(r.get("entry_score_v2") or 0),
                    active_tokens=list(r.get("active_tokens") or []),
                )
                for r in ck.get("trades") or []
            ]
            if trade_rows:
                print(f"loaded {len(trade_rows)} trades from checkpoint", flush=True)
        except (OSError, json.JSONDecodeError):
            trade_rows = []

    if not trade_rows:
        print(f"sessions={len(sessions)} replay dyn_p66={p66}", flush=True)
        trade_rows = _run_replay(p270, p71, sessions, duration_p33=p33, duration_p66=p66)
        CHECKPOINT.write_text(
            json.dumps(
                _json_safe(
                    {
                        "trades": [
                            {
                                "pnl_pct": r.pnl_pct,
                                "win": r.win,
                                "symbol": r.symbol,
                                "day": r.day,
                                "entry_score_v2": r.entry_score_v2,
                                "active_tokens": r.active_tokens,
                            }
                            for r in trade_rows
                        ]
                    }
                ),
                indent=2,
            ),
            encoding="utf-8",
        )

    wins = [r for r in trade_rows if r.win]
    losses = [r for r in trade_rows if not r.win]

    fire_all = _fire_rates(trade_rows)
    fire_wins = _fire_rates(wins)
    fire_losses = _fire_rates(losses)

    win_contrib = [_token_yes_no(trade_rows, t) for t in TARGET_TOKENS]
    pnl_deltas = [_token_deltas(trade_rows, t) for t in TARGET_TOKENS]
    pnl_rank = sorted(
        pnl_deltas,
        key=lambda d: float(d.get("delta_pnl_pct_with_minus_without") or 0),
        reverse=True,
    )
    combos = _combo_rankings(trade_rows)
    verdict = _verdict(pnl_deltas)

    report = {
        "phase": 306,
        "title": "token_fire_rate_profit_attribution",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "constraint": "review only; HBRecent+Board pre-gate; no production changes",
        "replay_window": {"start": DATE_START, "end": DATE_END, "aligned_with": ["Phase272", "Phase273", "Phase274"]},
        "method": {
            "engine": "Phase71 virtual exit replay",
            "entry_gate": f"entry_score_v2>={V2_MIN} (SCORE_POINTS_V2 incl Duration:high +2)",
            "token_detection": "HBRecent+Board pre-gate; Duration dynamic accepted-entry p66",
            "duration_cutoffs": dur,
            "target_tokens": list(TARGET_TOKENS),
            "aux_filters": _json_safe(AUX_FILTER),
        },
        "sessions": {"count": len(sessions), "skipped": skipped},
        "trade_summary": _trade_metrics(trade_rows),
        "1_fire_rate_all_trades": fire_all,
        "2_fire_rate_winning_trades": fire_wins,
        "3_fire_rate_losing_trades": fire_losses,
        "4_win_rate_contribution_by_token": win_contrib,
        "5_pnl_attribution_ranking": {
            "by_delta_pnl": pnl_rank,
            "by_delta_pf": sorted(
                pnl_deltas, key=lambda d: float(d.get("delta_pf_with_minus_without") or 0), reverse=True
            ),
        },
        "6_token_combination_top20": combos,
        "verdict": verdict,
    }

    OUT.write_text(json.dumps(_json_safe(report), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT} trades={len(trade_rows)}")
    print(f"most={verdict['most_profitable_tokens_top5']} least={verdict['least_useful_tokens_top5']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
