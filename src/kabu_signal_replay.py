"""
kabu_signal_v1 / kabu_exit_v1 リプレイ検証エンジン（paper_trade 非接続）。

Yahoo 1分足・kabu PUSH JSONL・合成 PUSH から時系列イベントを再生し、
仮想 ENTRY/EXIT と損益集計を行う。
"""

from __future__ import annotations

import json
import random
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from src.kabu_exit_engine import KabuExitEvalInput, KabuExitV1Config, evaluate_kabu_exit_v1
from src.kabu_signal_engine import (
    SCORE_NOTIFY_MIN,
    KabuSignalV1Config,
    PushHistoryRing,
    board_time_utc,
    evaluate_kabu_signal_v1,
    flatten_board_dict,
)
from src.signal_engine import BreakoutStateTracker

DATA_SOURCE_YAHOO_SYNTHETIC = "yahoo_synthetic_push"
DATA_SOURCE_PUSH_JSONL = "kabu_push_jsonl"
DATA_SOURCE_HYBRID = "hybrid_yahoo_plus_rest"


@dataclass
class ReplayEvent:
    ts: datetime
    board: dict[str, Any]
    source: str = "push"


@dataclass
class OpenPosition:
    symbol: str
    entry_time: datetime
    entry_price: float
    trigger_level: float
    entry_vwap_dist_pct: Optional[float]
    session_high_at_entry: float
    peak_price: float
    trough_price: float
    tier: str
    signal_score_at_entry: int = 0
    imbalance_low_streak: int = 0


@dataclass
class ClosedTrade:
    symbol: str
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    pnl_pct: float
    exit_reason: str
    max_favorable_excursion_pct: float
    max_adverse_excursion_pct: float
    elapsed_min: float
    signal_score_at_entry: int
    data_source: str

    def to_row(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "entry_time": self.entry_time.isoformat(),
            "entry_price": self.entry_price,
            "exit_time": self.exit_time.isoformat(),
            "exit_price": self.exit_price,
            "pnl_pct": round(self.pnl_pct, 6),
            "exit_reason": self.exit_reason,
            "max_favorable_excursion_pct": round(self.max_favorable_excursion_pct, 6),
            "max_adverse_excursion_pct": round(self.max_adverse_excursion_pct, 6),
            "elapsed_min": round(self.elapsed_min, 4),
            "signal_score_at_entry": self.signal_score_at_entry,
            "data_source": self.data_source,
        }


@dataclass
class SymbolReplayResult:
    symbol: str
    trades: list[ClosedTrade] = field(default_factory=list)
    eval_count: int = 0
    entry_signals: int = 0
    data_source: str = DATA_SOURCE_YAHOO_SYNTHETIC


def _parse_ts(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    return None


def _pct_change(current: float, base: float) -> float:
    if base <= 0:
        return 0.0
    return ((float(current) - float(base)) / float(base)) * 100.0


def yahoo_symbol_code(symbol: str) -> str:
    s = symbol.strip().upper()
    if s.endswith(".T"):
        return s[:-2]
    return s


def push_messages_from_yahoo_df(
    df: Any,
    *,
    symbol: str,
    keep_fraction: float = 1.0,
    seed: int = 0,
    emit_high_low: bool = True,
    spread_bps: float = 8.0,
    events_per_minute: int = 10,
) -> list[dict[str, Any]]:
    """
    Yahoo 1分足 DataFrame から kabu board 相当 PUSH を生成。

    検証用のみ。実 kabu PUSH の品質・タイミングとは別物。
    """
    rng = random.Random(seed)
    code = yahoo_symbol_code(symbol)
    msgs: list[dict[str, Any]] = []
    cum_vol = 0.0
    vwap_num = 0.0
    vwap_den = 0.0
    session_high = 0.0
    session_low = float("inf")

    for _, row in df.iterrows():
        if keep_fraction < 1.0 and rng.random() > keep_fraction:
            continue
        close = float(row["close"])
        high = float(row["high"])
        low = float(row["low"])
        try:
            vol = float(row["volume"])
        except (TypeError, ValueError):
            vol = 0.0
        if vol != vol:
            vol = 0.0
        # kabu の HighPrice は「直前までのセッション高値」相当。当バー高値を入れると
        # trigger が常に CurrentPrice より上になり breakout が発火しにくい。
        board_session_high = session_high if session_high > 0 else high
        board_session_low = session_low if session_low != float("inf") else low
        cum_vol += max(0.0, vol)
        tp = (high + low + close) / 3.0
        if vol > 0:
            vwap_num += tp * vol
            vwap_den += vol
        vwap = (vwap_num / vwap_den) if vwap_den > 0 else close

        ts = row["timestamp"]
        if hasattr(ts, "to_pydatetime"):
            bar_ts = ts.to_pydatetime()
        elif isinstance(ts, datetime):
            bar_ts = ts
        else:
            bar_ts = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if bar_ts.tzinfo is None:
            bar_ts = bar_ts.replace(tzinfo=timezone.utc)
        else:
            bar_ts = bar_ts.astimezone(timezone.utc)

        n_sub = max(1, int(events_per_minute))
        sub_vol = vol / float(n_sub)

        half = close * (spread_bps / 10_000.0) / 2.0
        bid = close - half
        ask = close + half
        imb_bias = 1.08 if close >= float(row["open"]) else 0.92
        bid_qty = 50_000.0 * imb_bias
        ask_qty = 50_000.0 / imb_bias

        def _board(price: float, *, event_ts: datetime, cum: float, incr_vol: float) -> dict[str, Any]:
            tstr = event_ts.isoformat()
            return {
                "Symbol": code,
                "CurrentPrice": price,
                "CurrentPriceTime": tstr,
                "HighPrice": board_session_high,
                "LowPrice": board_session_low,
                "VWAP": vwap,
                "TradingVolume": cum,
                # 累積出来高×価格は G6 閾値を不必要に巨大化するため、当該イベントの増分のみ
                "TradingValue": max(incr_vol, 1.0) * price,
                "BidPrice": bid,
                "AskPrice": ask,
                "BidQty": bid_qty,
                "AskQty": ask_qty,
                "Buy1": {"Price": bid, "Qty": bid_qty},
                "Sell1": {"Price": ask, "Qty": ask_qty},
            }

        prices = [close]
        if emit_high_low:
            if high != close:
                prices.append(high)
            if low != close:
                prices.append(low)

        for i in range(n_sub):
            offset = timedelta(seconds=int(58 * i / max(1, n_sub - 1)) if n_sub > 1 else 0)
            event_ts = bar_ts + offset
            cum_vol += max(0.0, sub_vol)
            px = prices[i % len(prices)]
            msgs.append(_board(px, event_ts=event_ts, cum=cum_vol, incr_vol=sub_vol))

        session_high = max(session_high, high, close)
        session_low = min(session_low, low, close)
    return msgs


def events_from_push_messages(msgs: Iterable[dict[str, Any]], *, source: str) -> list[ReplayEvent]:
    events: list[ReplayEvent] = []
    for msg in msgs:
        ts = board_time_utc(msg)
        if ts is None:
            continue
        events.append(ReplayEvent(ts=ts, board=dict(msg), source=source))
    events.sort(key=lambda e: e.ts)
    return events


def load_push_jsonl_events(path: Path, *, source: str = DATA_SOURCE_PUSH_JSONL) -> list[ReplayEvent]:
    msgs: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if isinstance(obj, dict):
                msgs.append(obj)
    return events_from_push_messages(msgs, source=source)


def merge_rest_board_template(events: list[ReplayEvent], rest_payload: dict[str, Any]) -> list[ReplayEvent]:
    """REST スナップショットの板深さ等で合成イベントの Sell/Buy レベルを補強（任意）。"""
    flat = flatten_board_dict(rest_payload)
    if not flat or not events:
        return events
    for ev in events:
        for k, v in flat.items():
            if k.startswith("Sell") or k.startswith("Buy") or k in ("BidPrice", "AskPrice", "BidQty", "AskQty"):
                ev.board.setdefault(k, v)
    return events


def replay_signal_config(*, relaxed: bool = False) -> KabuSignalV1Config:
    """リプレイ専用: 合成 PUSH では G8/G6 をやや緩和（本番ゲートとは別）。"""
    if not relaxed:
        return KabuSignalV1Config()
    return KabuSignalV1Config(
        min_push_samples_per_min=3.0,
        min_trading_value=0.0,
        min_trading_volume=0.0,
        vwap_distance_pct_min=-100.0,
        high_price_proximity_min=0.0,
    )


def replay_symbol_events(
    symbol: str,
    events: list[ReplayEvent],
    *,
    tier: str = "B",
    entry_score_min: int = SCORE_NOTIFY_MIN,
    require_timing_ok: bool = True,
    data_source: str = DATA_SOURCE_YAHOO_SYNTHETIC,
    eod_exit_reason: str = "eod_close",
    signal_cfg: Optional[KabuSignalV1Config] = None,
    exit_cfg: Optional[KabuExitV1Config] = None,
) -> SymbolReplayResult:
    ring = PushHistoryRing()
    tracker = BreakoutStateTracker()
    position: Optional[OpenPosition] = None
    trades: list[ClosedTrade] = []
    entry_signals = 0

    imb_thr = 0.48 if tier.upper() == "B" else 0.46

    def _close(pos: OpenPosition, exit_time: datetime, exit_price: float, reason: str) -> None:
        pnl = _pct_change(exit_price, pos.entry_price)
        mfe = _pct_change(pos.peak_price, pos.entry_price)
        mae = _pct_change(pos.trough_price, pos.entry_price)
        elapsed = (exit_time - pos.entry_time).total_seconds() / 60.0
        trades.append(
            ClosedTrade(
                symbol=symbol,
                entry_time=pos.entry_time,
                entry_price=pos.entry_price,
                exit_time=exit_time,
                exit_price=exit_price,
                pnl_pct=pnl,
                exit_reason=reason,
                max_favorable_excursion_pct=mfe,
                max_adverse_excursion_pct=mae,
                elapsed_min=elapsed,
                signal_score_at_entry=pos.signal_score_at_entry,
                data_source=data_source,
            )
        )

    eval_count = 0
    for ev in events:
        eval_count += 1
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
            if exit_res.would_exit:
                _close(position, ev.ts, px, exit_res.exit_reason)
                position = None
            continue

        eligible = bool(rd.get("breakout_event")) and int(rd.get("signal_score") or 0) >= entry_score_min
        if require_timing_ok:
            eligible = eligible and bool(rd.get("timing_ok"))
        if tier.upper() == "C":
            eligible = False
        if not eligible:
            continue
        trigger = rd.get("trigger_level")
        if trigger is None:
            continue
        entry_signals += 1
        session_high = rd.get("high_price")
        position = OpenPosition(
            symbol=symbol,
            entry_time=ev.ts,
            entry_price=px,
            trigger_level=float(trigger),
            entry_vwap_dist_pct=rd.get("vwap_distance_pct"),
            session_high_at_entry=float(session_high) if session_high is not None else px,
            peak_price=px,
            trough_price=px,
            tier=tier.upper(),
            signal_score_at_entry=int(rd.get("signal_score") or 0),
        )

    if position is not None and events:
        last = events[-1]
        last_px = float(position.peak_price)
        if last.board.get("CurrentPrice") is not None:
            last_px = float(last.board["CurrentPrice"])
        _close(position, last.ts, last_px, eod_exit_reason)

    return SymbolReplayResult(
        symbol=symbol,
        trades=trades,
        eval_count=eval_count,
        entry_signals=entry_signals,
        data_source=data_source,
    )


def exit_config_from_sweep(
    *,
    tier: str,
    breakout_failure_minutes: float,
    breakout_failure_buffer_pct: float,
    hard_stop_pct: float,
    time_stop_min: float,
    vwap_exit_buffer_pct: float,
) -> KabuExitV1Config:
    """パラメータスイープ用 EXIT 設定（hard_stop_pct は負値表記可）。"""
    hard = abs(float(hard_stop_pct))
    t_stop = float(time_stop_min)
    buf = float(breakout_failure_buffer_pct)
    vwap_below = float(vwap_exit_buffer_pct)
    fail_sec = float(breakout_failure_minutes) * 60.0
    tk = tier.upper()
    if tk == "A":
        return KabuExitV1Config(
            hard_stop_pct_a=hard,
            hard_stop_pct_b=hard,
            fail_buffer_pct_a=buf,
            fail_buffer_pct_b=buf,
            fail_window_sec=fail_sec,
            vwap_exit_below_pct_a=vwap_below,
            vwap_exit_below_pct_b=vwap_below,
            time_stop_max_a=t_stop,
            time_stop_max_b=t_stop,
        )
    return KabuExitV1Config(
        hard_stop_pct_a=hard,
        hard_stop_pct_b=hard,
        fail_buffer_pct_a=buf,
        fail_buffer_pct_b=buf,
        fail_window_sec=fail_sec,
        vwap_exit_below_pct_a=vwap_below,
        vwap_exit_below_pct_b=vwap_below,
        time_stop_max_a=t_stop,
        time_stop_max_b=t_stop,
    )


def summarize_trades(trades: list[ClosedTrade]) -> dict[str, Any]:
    if not trades:
        return {
            "trades": 0,
            "win_rate": None,
            "avg_pnl_pct": None,
            "median_pnl_pct": None,
            "max_loss_pct": None,
            "avg_loss_pct": None,
            "stop_exit_count": 0,
            "breakout_failure_exit_count": 0,
            "time_stop_count": 0,
            "vwap_exit_count": 0,
            "by_exit_reason": [],
        }

    pnls = [t.pnl_pct for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    def _count_reason(name: str) -> int:
        return sum(1 for t in trades if t.exit_reason == name)

    by_reason: dict[str, list[float]] = {}
    for t in trades:
        by_reason.setdefault(t.exit_reason, []).append(t.pnl_pct)

    reason_rows = []
    for reason, vals in sorted(by_reason.items()):
        reason_rows.append(
            {
                "exit_reason": reason,
                "count": len(vals),
                "win_rate": sum(1 for v in vals if v > 0) / len(vals) if vals else None,
                "avg_pnl_pct": statistics.mean(vals) if vals else None,
                "median_pnl_pct": statistics.median(vals) if vals else None,
                "max_loss_pct": min(vals) if vals else None,
            }
        )

    return {
        "trades": len(trades),
        "win_rate": len(wins) / len(trades) if trades else None,
        "avg_pnl_pct": statistics.mean(pnls),
        "median_pnl_pct": statistics.median(pnls),
        "max_loss_pct": min(pnls),
        "avg_loss_pct": statistics.mean(losses) if losses else None,
        "stop_exit_count": _count_reason("hard_stop"),
        "breakout_failure_exit_count": _count_reason("breakout_failure"),
        "time_stop_count": _count_reason("time_stop"),
        "vwap_exit_count": _count_reason("vwap_reclaim_failure"),
        "by_exit_reason": reason_rows,
    }


def summarize_trades_for_sweep(trades: list[ClosedTrade]) -> dict[str, Any]:
    """パラメータスイープ用 KPI（total_pnl_pct / profit_factor / eod 等）。"""
    base = summarize_trades(trades)
    pnls = [t.pnl_pct for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    if gross_loss > 0:
        profit_factor: Optional[float] = gross_profit / gross_loss
    elif gross_profit > 0:
        profit_factor = None  # JSON では inf 回避のため null + note
    else:
        profit_factor = None

    base["total_pnl_pct"] = sum(pnls) if pnls else 0.0
    base["profit_factor"] = profit_factor
    base["hard_stop_exit_count"] = base.get("stop_exit_count", 0)
    base["time_stop_exit_count"] = base.get("time_stop_count", 0)
    base["eod_close_count"] = sum(1 for t in trades if t.exit_reason == "eod_close")
    if gross_loss == 0 and gross_profit > 0:
        base["profit_factor_note"] = "no_losses"
    return base


def build_symbol_replay_events(
    *,
    symbol: str,
    yahoo_csv: Path,
    push_jsonl: Optional[Path] = None,
    rest_json: Optional[Path] = None,
    synthetic_keep: float = 1.0,
    synthetic_seed: int = 0,
    synthetic_spread_bps: float = 8.0,
    synthetic_events_per_minute: int = 10,
) -> tuple[list[ReplayEvent], str]:
    """1 銘柄分のリプレイイベント列を構築（スイープで再利用）。"""
    import pandas as pd

    from src.signal_engine import normalize_ohlcv_dataframe

    df = normalize_ohlcv_dataframe(pd.read_csv(yahoo_csv))
    if push_jsonl is not None and push_jsonl.is_file():
        events = load_push_jsonl_events(push_jsonl)
        data_source = DATA_SOURCE_PUSH_JSONL
        if rest_json is not None and rest_json.is_file():
            rest_payload = json.loads(rest_json.read_text(encoding="utf-8"))
            events = merge_rest_board_template(events, rest_payload)
            data_source = "hybrid_push_jsonl_plus_rest"
    else:
        msgs = push_messages_from_yahoo_df(
            df,
            symbol=symbol,
            keep_fraction=synthetic_keep,
            seed=synthetic_seed,
            spread_bps=synthetic_spread_bps,
            events_per_minute=synthetic_events_per_minute,
        )
        events = events_from_push_messages(msgs, source=DATA_SOURCE_YAHOO_SYNTHETIC)
        data_source = DATA_SOURCE_YAHOO_SYNTHETIC
        if rest_json is not None and rest_json.is_file():
            rest_payload = json.loads(rest_json.read_text(encoding="utf-8"))
            events = merge_rest_board_template(events, rest_payload)
    return events, data_source


def compare_with_yahoo_replay_signals(
    kabu_trades: list[ClosedTrade],
    yahoo_signals_csv: Path,
) -> dict[str, Any]:
    """Yahoo paper_trade / watch リプレイの signals CSV と並べて比較。"""
    import csv

    yahoo_rows: list[dict[str, str]] = []
    with yahoo_signals_csv.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yahoo_rows.append(row)

    def _is_closed_eval(row: dict[str, str]) -> bool:
        closed = str(row.get("position_closed", "")).lower() in ("true", "1", "yes")
        excluded = str(row.get("excluded_from_eval", "")).lower() in ("true", "1", "yes")
        return closed and not excluded

    y_closed = [r for r in yahoo_rows if _is_closed_eval(r)]

    def _pnl(row: dict[str, str]) -> Optional[float]:
        raw = row.get("profit_pct") or row.get("pnl_pct")
        if raw is None or raw == "":
            return None
        return float(raw)

    y_pnls = [p for p in (_pnl(r) for r in y_closed) if p is not None]
    k_pnls = [t.pnl_pct for t in kabu_trades]

    y_symbols = {r.get("symbol", "") for r in y_closed}
    k_symbols = {t.symbol for t in kabu_trades}

    return {
        "yahoo_signals_csv": str(yahoo_signals_csv),
        "yahoo_closed_trades": len(y_closed),
        "kabu_virtual_trades": len(kabu_trades),
        "yahoo_win_rate": (sum(1 for p in y_pnls if p > 0) / len(y_pnls)) if y_pnls else None,
        "kabu_win_rate": (sum(1 for p in k_pnls if p > 0) / len(k_pnls)) if k_pnls else None,
        "yahoo_avg_pnl_pct": statistics.mean(y_pnls) if y_pnls else None,
        "kabu_avg_pnl_pct": statistics.mean(k_pnls) if k_pnls else None,
        "yahoo_median_pnl_pct": statistics.median(y_pnls) if y_pnls else None,
        "kabu_median_pnl_pct": statistics.median(k_pnls) if k_pnls else None,
        "symbols_yahoo_only": sorted(y_symbols - k_symbols),
        "symbols_kabu_only": sorted(k_symbols - y_symbols),
        "symbols_both": sorted(y_symbols & k_symbols),
    }
