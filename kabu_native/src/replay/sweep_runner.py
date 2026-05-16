"""
Phase 8: parameterized replay with cached intraday events (common rules only).
"""

from __future__ import annotations

import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from replay.session_control import entry_allowed as session_entry_allowed

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore[misc, assignment]

JST = ZoneInfo("Asia/Tokyo") if ZoneInfo else None

OPENING_BAND = "09:00-09:30"


@dataclass(frozen=True)
class SweepParams:
    sweep_id: str
    sweep_group: str
    fail_window_min: float = 2.0
    fail_buffer_pct: float = 0.10
    bf_confirm_count: int = 1
    market_session_control: bool = False
    hard_stop_pct: float = 1.20

    def to_dict(self) -> dict[str, Any]:
        return {
            "sweep_id": self.sweep_id,
            "sweep_group": self.sweep_group,
            "fail_window_min": self.fail_window_min,
            "fail_buffer_pct": self.fail_buffer_pct,
            "bf_confirm_count": self.bf_confirm_count,
            "market_session_control": self.market_session_control,
            "hard_stop_pct": self.hard_stop_pct,
        }


@dataclass
class CachedDaySymbol:
    trade_date: str
    symbol: str
    events: list[Any]


@dataclass
class _Position:
    symbol: str
    entry_time: datetime
    entry_price: float
    trigger_level: float
    entry_vwap_dist_pct: Optional[float]
    session_high_at_entry: float
    peak_price: float
    trough_price: float
    tier: str
    signal_score_at_entry: int
    imbalance_low_streak: int = 0
    bf_streak: int = 0


def _ensure_repo(repo_root: Path) -> None:
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))


def classify_opening_jst(ts: datetime) -> bool:
    if JST is None:
        return False
    dt = ts.astimezone(JST)
    start = 9 * 60
    end = 9 * 60 + 30
    m = dt.hour * 60 + dt.minute
    return start <= m < end


def build_event_cache(
    *,
    repo_root: Path,
    symbols: list[str],
    start_date: str,
    end_date: str,
    data_roots: list[Path],
    synthetic_push_keep: float = 1.0,
    synthetic_spread_bps: float = 8.0,
    synthetic_events_per_minute: int = 10,
) -> list[CachedDaySymbol]:
    _ensure_repo(repo_root)
    from replay.intraday import load_intraday_csv, resolve_intraday_csv
    from src.kabu_signal_replay import (
        DATA_SOURCE_YAHOO_SYNTHETIC,
        events_from_push_messages,
        push_messages_from_yahoo_df,
        yahoo_symbol_code,
    )
    d0 = date.fromisoformat(start_date)
    d1 = date.fromisoformat(end_date)
    cache: list[CachedDaySymbol] = []
    cur = d0
    while cur <= d1:
        trade_date = cur.isoformat()
        for symbol in symbols:
            sym = symbol if symbol.endswith(".T") else f"{yahoo_symbol_code(symbol)}.T"
            csv_path = resolve_intraday_csv(data_roots, trade_date, sym)
            if csv_path is None:
                continue
            loaded = load_intraday_csv(csv_path)
            if not loaded.ok or loaded.df is None:
                continue
            seed = hash((trade_date, sym)) % (2**31)
            msgs = push_messages_from_yahoo_df(
                loaded.df,
                symbol=sym,
                keep_fraction=synthetic_push_keep,
                seed=seed,
                spread_bps=synthetic_spread_bps,
                events_per_minute=synthetic_events_per_minute,
            )
            events = events_from_push_messages(msgs, source=DATA_SOURCE_YAHOO_SYNTHETIC)
            cache.append(CachedDaySymbol(trade_date=trade_date, symbol=sym, events=events))
        cur += timedelta(days=1)
    return cache


def replay_cached(
    cache: list[CachedDaySymbol],
    params: SweepParams,
    *,
    repo_root: Path,
    tier: str = "B",
    entry_score_min: int = 60,
    require_timing_ok: bool = True,
    relaxed_signal: bool = True,
) -> list[Any]:
    from src.kabu_exit_engine import KabuExitEvalInput, KabuExitV1Config, evaluate_kabu_exit_v1
    from src.kabu_signal_engine import PushHistoryRing, evaluate_kabu_signal_v1
    from src.kabu_signal_replay import replay_signal_config
    from src.signal_engine import BreakoutStateTracker

    _ensure_repo(repo_root)

    exit_cfg = KabuExitV1Config(
        hard_stop_pct_a=params.hard_stop_pct,
        hard_stop_pct_b=params.hard_stop_pct,
        fail_buffer_pct_a=params.fail_buffer_pct,
        fail_buffer_pct_b=params.fail_buffer_pct,
        fail_window_sec=params.fail_window_min * 60.0,
    )
    signal_cfg = replay_signal_config(relaxed=relaxed_signal)
    imb_thr = 0.48 if tier.upper() == "B" else 0.46
    confirm_n = max(1, int(params.bf_confirm_count))

    all_trades: list[Any] = []

    for item in cache:
        if not item.events:
            continue
        ring = PushHistoryRing()
        tracker = BreakoutStateTracker()
        position: Optional[_Position] = None

        def _close(pos: _Position, exit_time: datetime, exit_price: float, reason: str) -> None:
            from src.kabu_signal_replay import ClosedTrade, DATA_SOURCE_YAHOO_SYNTHETIC

            entry = pos.entry_price
            pnl = ((exit_price - entry) / entry) * 100.0 if entry > 0 else 0.0
            mfe = ((pos.peak_price - entry) / entry) * 100.0 if entry > 0 else 0.0
            mae = ((pos.trough_price - entry) / entry) * 100.0 if entry > 0 else 0.0
            elapsed = (exit_time - pos.entry_time).total_seconds() / 60.0
            all_trades.append(
                ClosedTrade(
                    symbol=pos.symbol,
                    entry_time=pos.entry_time,
                    entry_price=entry,
                    exit_time=exit_time,
                    exit_price=exit_price,
                    pnl_pct=pnl,
                    exit_reason=reason,
                    max_favorable_excursion_pct=mfe,
                    max_adverse_excursion_pct=mae,
                    elapsed_min=elapsed,
                    signal_score_at_entry=pos.signal_score_at_entry,
                    data_source=DATA_SOURCE_YAHOO_SYNTHETIC,
                )
            )

        for ev in item.events:
            board = ev.board
            ring.add_from_board(board)
            result, tracker = evaluate_kabu_signal_v1(
                board,
                push_history=ring,
                breakout_tracker=tracker,
                tier=tier,
                evaluated_at=ev.ts,
                cfg=signal_cfg,
            )
            rd = result.to_dict()
            price = rd.get("current_price")
            if price is None:
                continue
            px = float(price)

            if position is not None:
                if px > position.peak_price:
                    position.peak_price = px
                if px < position.trough_price:
                    position.trough_price = px

                imbalance = rd.get("board_imbalance")
                if imbalance is not None and float(imbalance) <= imb_thr:
                    position.imbalance_low_streak += 1
                else:
                    position.imbalance_low_streak = 0

                push_3m = ring.push_samples_avg_per_minute(as_of=ev.ts)
                exit_res = evaluate_kabu_exit_v1(
                    KabuExitEvalInput(
                        entry_price=position.entry_price,
                        current_price=px,
                        entry_time=position.entry_time,
                        now_time=ev.ts,
                        high_since_entry=position.peak_price,
                        current_vwap=rd.get("vwap"),
                        entry_vwap_dist_pct=position.entry_vwap_dist_pct,
                        spread_bps=rd.get("spread_bps"),
                        board_imbalance=imbalance,
                        push_density_1m=int(rd.get("push_samples_1m") or 0),
                        push_density_3m_avg=push_3m,
                        tier=position.tier,
                        breakout_trigger_level=position.trigger_level,
                        session_high_at_entry=position.session_high_at_entry,
                        session_high_now=rd.get("high_price"),
                        imbalance_low_streak=position.imbalance_low_streak,
                        max_price_since_entry=position.peak_price,
                    ),
                    has_position=True,
                    cfg=exit_cfg,
                )

                if exit_res.would_exit and exit_res.exit_reason == "breakout_failure":
                    position.bf_streak += 1
                    if position.bf_streak >= confirm_n:
                        _close(position, ev.ts, px, "breakout_failure")
                        position = None
                    continue
                position.bf_streak = 0

                if exit_res.would_exit:
                    _close(position, ev.ts, px, exit_res.exit_reason)
                    position = None
                continue

            if not session_entry_allowed(ev.ts, market_session_control=params.market_session_control):
                continue

            eligible = bool(rd.get("breakout_event")) and int(rd.get("signal_score") or 0) >= entry_score_min
            if require_timing_ok:
                eligible = eligible and bool(rd.get("timing_ok"))
            if not eligible:
                continue
            trigger = rd.get("trigger_level")
            if trigger is None:
                continue
            session_high = rd.get("high_price")
            position = _Position(
                symbol=item.symbol,
                entry_time=ev.ts,
                entry_price=px,
                trigger_level=float(trigger),
                entry_vwap_dist_pct=rd.get("vwap_distance_pct"),
                session_high_at_entry=float(session_high) if session_high is not None else px,
                peak_price=px,
                trough_price=px,
                tier=tier.upper(),
                signal_score_at_entry=int(rd.get("signal_score") or 0),
                bf_streak=0,
            )

        if position is not None and item.events:
            last = item.events[-1]
            last_px = float(last.board.get("CurrentPrice") or position.peak_price)
            _close(position, last.ts, last_px, "eod_close")

    return all_trades


BASELINE_FAIL_WINDOW_MIN = 2.0
BASELINE_FAIL_BUFFER_PCT = 0.10
BASELINE_BF_CONFIRM = 1
BASELINE_HARD_STOP_PCT = 1.20


def baseline_sweep_params() -> SweepParams:
    return SweepParams(
        sweep_id="baseline",
        sweep_group="baseline",
        fail_window_min=BASELINE_FAIL_WINDOW_MIN,
        fail_buffer_pct=BASELINE_FAIL_BUFFER_PCT,
        bf_confirm_count=BASELINE_BF_CONFIRM,
        market_session_control=False,
        hard_stop_pct=BASELINE_HARD_STOP_PCT,
    )


def iter_phase8_sweeps() -> list[SweepParams]:
    """One-factor-at-a-time grid: BF (18) + opening (5) + hard_stop (4), plus baseline."""
    base = baseline_sweep_params()
    out: list[SweepParams] = [base]
    seen: set[str] = {base.sweep_id}

    for fw in (1, 2, 3):
        for buf in (0.05, 0.12, 0.20):
            for cc in (1, 2):
                sid = f"bf_fw{fw}_buf{buf:.2f}_cc{cc}"
                if sid in seen:
                    continue
                seen.add(sid)
                out.append(
                    SweepParams(
                        sweep_id=sid,
                        sweep_group="breakout_failure",
                        fail_window_min=float(fw),
                        fail_buffer_pct=buf,
                        bf_confirm_count=cc,
                        market_session_control=False,
                        hard_stop_pct=BASELINE_HARD_STOP_PCT,
                    )
                )

    sid = "market_session"
    if sid not in seen:
        seen.add(sid)
        out.append(
            SweepParams(
                sweep_id=sid,
                sweep_group="market_session",
                fail_window_min=BASELINE_FAIL_WINDOW_MIN,
                fail_buffer_pct=BASELINE_FAIL_BUFFER_PCT,
                bf_confirm_count=BASELINE_BF_CONFIRM,
                market_session_control=True,
                hard_stop_pct=BASELINE_HARD_STOP_PCT,
            )
        )

    for hs in (0.8, 1.0, 1.2, 1.35):
        sid = f"hs_{hs:.2f}".replace(".", "p")
        if sid in seen:
            continue
        seen.add(sid)
        out.append(
            SweepParams(
                sweep_id=sid,
                sweep_group="hard_stop",
                fail_window_min=BASELINE_FAIL_WINDOW_MIN,
                fail_buffer_pct=BASELINE_FAIL_BUFFER_PCT,
                bf_confirm_count=BASELINE_BF_CONFIRM,
                market_session_control=False,
                hard_stop_pct=hs,
            )
        )

    return out


def apply_trade_floor(
    rows: list[dict[str, Any]],
    *,
    baseline_trades: int,
    min_ratio: float = 0.55,
    min_absolute: int = 45,
) -> list[dict[str, Any]]:
    floor = max(min_absolute, int(baseline_trades * min_ratio))
    for row in rows:
        trades = int(row.get("trades") or 0)
        row["trade_floor"] = floor
        row["excluded_low_trades"] = trades < floor
    return rows


def pick_candidates(
    rows: list[dict[str, Any]],
    *,
    max_n: int = 3,
) -> list[dict[str, Any]]:
    eligible = [r for r in rows if not r.get("excluded_low_trades") and int(r.get("trades") or 0) > 0]
    if not eligible:
        return []

    def score(r: dict[str, Any]) -> tuple[float, float, float]:
        pf = r.get("profit_factor")
        pf_v = float(pf) if pf is not None else 0.0
        return (
            float(r.get("total_pnl_pct") or 0.0),
            pf_v,
            float(r.get("max_loss_pct") or -999.0),
        )

    ranked = sorted(eligible, key=score, reverse=True)
    return ranked[:max_n]


def summarize_sweep(trades: list[Any], params: SweepParams) -> dict[str, Any]:
    if not trades:
        return {
            **params.to_dict(),
            "trades": 0,
            "symbols_with_trades": 0,
            "win_rate": None,
            "total_pnl_pct": 0.0,
            "avg_pnl_pct": None,
            "median_pnl_pct": None,
            "max_loss_pct": None,
            "profit_factor": None,
            "breakout_failure_exit_count": 0,
            "hard_stop_count": 0,
            "opening_trade_count": 0,
            "pnl_concentration_top_symbol": None,
            "pnl_concentration_top_share": None,
            "exit_reason_counts": {},
        }

    pnls = [float(t.pnl_pct) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    if gross_loss > 0:
        pf: Optional[float] = gross_profit / gross_loss
    elif gross_profit > 0:
        pf = None
    else:
        pf = 0.0

    reasons = Counter(t.exit_reason for t in trades)
    by_sym: dict[str, float] = defaultdict(float)
    for t in trades:
        by_sym[t.symbol] += float(t.pnl_pct)

    opening_count = sum(1 for t in trades if classify_opening_jst(t.entry_time))
    abs_total = sum(abs(v) for v in by_sym.values()) or 1e-9
    top_sym = max(by_sym, key=lambda k: abs(by_sym[k]))
    top_share = abs(by_sym[top_sym]) / abs_total

    sym_set = {t.symbol for t in trades}

    return {
        **params.to_dict(),
        "trades": len(trades),
        "symbols_with_trades": len(sym_set),
        "win_rate": len(wins) / len(trades),
        "total_pnl_pct": sum(pnls),
        "avg_pnl_pct": statistics.mean(pnls),
        "median_pnl_pct": statistics.median(pnls),
        "max_loss_pct": min(pnls),
        "profit_factor": pf,
        "breakout_failure_exit_count": reasons.get("breakout_failure", 0),
        "hard_stop_count": reasons.get("hard_stop", 0),
        "opening_trade_count": opening_count,
        "pnl_concentration_top_symbol": top_sym,
        "pnl_concentration_top_share": round(top_share, 4),
        "exit_reason_counts": dict(reasons),
    }
