"""
kabu_signal_v1 / kabu_exit_v1 構造分析（市場条件・クラスタ単位）。

個別銘柄チューニングは行わない。流動性・ETF・値嵩・時間帯で壊れやすさを集計する。
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from src.kabu_exit_engine import KabuExitEvalInput, KabuExitV1Config, evaluate_kabu_exit_v1
from src.kabu_signal_engine import (
    KabuSignalV1Config,
    PushHistoryRing,
    evaluate_kabu_signal_v1,
)
from src.kabu_signal_replay import (
    ReplayEvent,
    build_symbol_replay_events,
    exit_config_from_sweep,
    replay_signal_config,
)
from src.signal_engine import BreakoutStateTracker

# 市場構造上の ETF（銘柄固有チューニングではない）
ETF_SYMBOLS: frozenset[str] = frozenset({"1306.T", "1321.T"})
HIGH_PRICE_MEDIAN_CLOSE_YEN: float = 10_000.0
MFE_QUALITY_THRESHOLD_PCT: float = 0.25
IMMEDIATE_ADVERSE_PCT: float = -0.15
IMMEDIATE_ADVERSE_WINDOW_MIN: float = 2.0
STRICT_SPREAD_BPS_MAX: float = 15.0
STRICT_PUSH_MIN_PER_MIN: int = 8


class LiquidityCluster(str, Enum):
    ULTRA_HIGH = "ultra_high_liquidity"
    MID = "mid_liquidity"
    LOW = "low_liquidity"
    ETF = "etf"
    HIGH_PRICE = "high_price"


class TimeBucket(str, Enum):
    OPENING = "opening"  # 寄り直後 9:00-9:30 JST
    MORNING_MID = "morning_mid"  # 前場中盤 9:30-11:00
    AFTERNOON_OPEN = "afternoon_open"  # 後場寄り 12:30-13:00
    PRE_CLOSE = "pre_close"  # 引け前 14:30-15:00
    OTHER_SESSION = "other_session"


@dataclass
class SymbolDayMetrics:
    symbol: str
    median_close: float
    median_volume: float
    median_dollar_volume: float
    volatility_5m_median_pct: float


@dataclass
class DiagnosticTrade:
    symbol: str
    cluster: str
    time_bucket: str
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    pnl_pct: float
    exit_reason: str
    elapsed_min: float
    max_favorable_excursion_pct: float
    max_adverse_excursion_pct: float
    signal_score_at_entry: int
    timing_ok_at_entry: bool
    strict_timing_ok_at_entry: bool
    reject_reasons_at_entry: str
    entry_spread_bps: Optional[float] = None
    entry_push_density_1m: int = 0
    entry_trading_value: Optional[float] = None
    entry_volatility_5m_pct: Optional[float] = None
    exit_spread_bps: Optional[float] = None
    exit_push_density_1m: int = 0
    exit_trading_value: Optional[float] = None
    exit_volatility_5m_pct: Optional[float] = None
    time_since_breakout_min: Optional[float] = None
    immediate_adverse_2m: bool = False
    mfe_above_threshold: bool = False
    low_quality_entry: bool = False
    logic_stress_bf: bool = False

    def to_row(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "cluster": self.cluster,
            "time_bucket": self.time_bucket,
            "entry_time": self.entry_time.isoformat(),
            "exit_time": self.exit_time.isoformat(),
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "pnl_pct": round(self.pnl_pct, 6),
            "exit_reason": self.exit_reason,
            "elapsed_min": round(self.elapsed_min, 4),
            "max_favorable_excursion_pct": round(self.max_favorable_excursion_pct, 6),
            "max_adverse_excursion_pct": round(self.max_adverse_excursion_pct, 6),
            "signal_score_at_entry": self.signal_score_at_entry,
            "timing_ok_at_entry": self.timing_ok_at_entry,
            "strict_timing_ok_at_entry": self.strict_timing_ok_at_entry,
            "reject_reasons_at_entry": self.reject_reasons_at_entry,
            "entry_spread_bps": self.entry_spread_bps,
            "entry_push_density_1m": self.entry_push_density_1m,
            "entry_trading_value": self.entry_trading_value,
            "entry_volatility_5m_pct": self.entry_volatility_5m_pct,
            "exit_spread_bps": self.exit_spread_bps,
            "exit_push_density_1m": self.exit_push_density_1m,
            "exit_trading_value": self.exit_trading_value,
            "exit_volatility_5m_pct": self.exit_volatility_5m_pct,
            "time_since_breakout_min": self.time_since_breakout_min,
            "immediate_adverse_2m": self.immediate_adverse_2m,
            "mfe_above_threshold": self.mfe_above_threshold,
            "low_quality_entry": self.low_quality_entry,
            "logic_stress_bf": self.logic_stress_bf,
        }


@dataclass
class _OpenDiag:
    entry_time: datetime
    entry_price: float
    trigger_level: float
    entry_vwap_dist_pct: Optional[float]
    session_high_at_entry: float
    peak_price: float
    trough_price: float
    tier: str
    signal_score_at_entry: int
    imbalance_low_streak: int
    timing_ok_at_entry: bool
    strict_timing_ok_at_entry: bool
    reject_reasons_at_entry: str
    entry_spread_bps: Optional[float]
    entry_push_density_1m: int
    entry_trading_value: Optional[float]
    entry_volatility_5m_pct: Optional[float]
    cluster: str
    time_bucket: str
    min_pnl_first_2m: float = 0.0


def jst_time_bucket(ts: datetime) -> str:
    """UTC タイムスタンプ → 東証セッション時間帯（JST）。"""
    jst = ts.astimezone(timezone.utc).replace(tzinfo=timezone.utc)
    # CSV は UTC。JST = UTC + 9h
    from datetime import timedelta

    jst_dt = jst + timedelta(hours=9)
    hm = jst_dt.hour * 60 + jst_dt.minute
    if 9 * 60 <= hm < 9 * 60 + 30:
        return TimeBucket.OPENING.value
    if 9 * 60 + 30 <= hm < 11 * 60:
        return TimeBucket.MORNING_MID.value
    if 12 * 60 + 30 <= hm < 13 * 60:
        return TimeBucket.AFTERNOON_OPEN.value
    if 14 * 60 + 30 <= hm < 15 * 60:
        return TimeBucket.PRE_CLOSE.value
    if 9 * 60 <= hm < 15 * 60:
        return TimeBucket.OTHER_SESSION.value
    return "off_session"


def compute_symbol_day_metrics(yahoo_csv: Path) -> SymbolDayMetrics:
    import pandas as pd

    from src.signal_engine import normalize_ohlcv_dataframe

    df = normalize_ohlcv_dataframe(pd.read_csv(yahoo_csv))
    symbol = yahoo_csv.stem
    close = df["close"].astype(float)
    vol = df["volume"].fillna(0).astype(float)
    dollar = close * vol
    vol5 = []
    for i in range(len(df)):
        w = df.iloc[max(0, i - 4) : i + 1]
        if len(w) < 2:
            continue
        hi = float(w["high"].max())
        lo = float(w["low"].min())
        mid = (hi + lo) / 2.0
        if mid > 0:
            vol5.append((hi - lo) / mid * 100.0)
    return SymbolDayMetrics(
        symbol=symbol,
        median_close=float(close.median()),
        median_volume=float(vol.median()),
        median_dollar_volume=float(dollar.median()),
        volatility_5m_median_pct=float(statistics.median(vol5)) if vol5 else 0.0,
    )


def classify_symbols(
    metrics: list[SymbolDayMetrics],
) -> dict[str, str]:
    """
    横断面のみでクラスタ付与（個別銘柄ルールなし）。
    優先: ETF → 値嵩 → 流動性三分位（非 ETF）。
    """
    by_sym = {m.symbol: m for m in metrics}
    non_etf = [m for m in metrics if m.symbol not in ETF_SYMBOLS]
    dollar_vols = sorted(m.median_dollar_volume for m in non_etf)
    n = len(dollar_vols)
    if n == 0:
        return {m.symbol: LiquidityCluster.ETF.value for m in metrics if m.symbol in ETF_SYMBOLS}

    def _pctile(p: float) -> float:
        if n == 1:
            return dollar_vols[0]
        idx = int(p * (n - 1))
        return dollar_vols[idx]

    p33 = _pctile(0.33)
    p67 = _pctile(0.67)

    out: dict[str, str] = {}
    for m in metrics:
        if m.symbol in ETF_SYMBOLS:
            out[m.symbol] = LiquidityCluster.ETF.value
        elif m.median_close >= HIGH_PRICE_MEDIAN_CLOSE_YEN:
            out[m.symbol] = LiquidityCluster.HIGH_PRICE.value
        elif m.median_dollar_volume >= p67:
            out[m.symbol] = LiquidityCluster.ULTRA_HIGH.value
        elif m.median_dollar_volume <= p33:
            out[m.symbol] = LiquidityCluster.LOW.value
        else:
            out[m.symbol] = LiquidityCluster.MID.value
    return out


def _default_exit_cfg(tier: str) -> KabuExitV1Config:
    tk = tier.upper()
    hard = -1.35 if tk == "A" else -1.2
    t_stop = 12.0 if tk == "A" else 9.0
    vwap = -0.05 if tk == "A" else -0.03
    return exit_config_from_sweep(
        tier=tier,
        breakout_failure_minutes=2.0,
        breakout_failure_buffer_pct=0.12,
        hard_stop_pct=hard,
        time_stop_min=t_stop,
        vwap_exit_buffer_pct=vwap,
    )


def _volatility_5m_pct(ring: PushHistoryRing, as_of: datetime, price: float) -> Optional[float]:
    if not ring.samples:
        return None
    from datetime import timedelta

    cutoff = as_of.astimezone(timezone.utc) - timedelta(minutes=5)
    prices = [p for t, p, _ in ring.samples if t >= cutoff]
    if len(prices) < 2:
        return None
    hi, lo = max(prices), min(prices)
    mid = (hi + lo) / 2.0
    if mid <= 0:
        return None
    return (hi - lo) / mid * 100.0


def _is_low_quality_entry(
    *,
    strict_timing_ok: bool,
    spread_bps: Optional[float],
    push_1m: int,
    reject_reasons: list[str],
) -> bool:
    if not strict_timing_ok:
        return True
    if spread_bps is None or spread_bps > STRICT_SPREAD_BPS_MAX:
        return True
    if push_1m < STRICT_PUSH_MIN_PER_MIN:
        return True
    stress_codes = {"G8_PUSH_DENSITY", "G2_SPREAD", "G6_VOLUME_DELTA", "REST_ONLY_NO_PUSH_HISTORY"}
    if any(r in stress_codes for r in reject_reasons):
        return True
    return False


def replay_symbol_diagnostic(
    symbol: str,
    events: list[ReplayEvent],
    *,
    cluster: str,
    tier: str = "B",
    entry_score_min: int = 60,
    require_timing_ok: bool = True,
    replay_relaxed: bool = True,
    exit_cfg: Optional[KabuExitV1Config] = None,
) -> list[DiagnosticTrade]:
    ring = PushHistoryRing()
    strict_cfg = KabuSignalV1Config()
    sig_cfg = replay_signal_config(relaxed=replay_relaxed)
    tracker = BreakoutStateTracker()
    position: Optional[_OpenDiag] = None
    trades: list[DiagnosticTrade] = []
    imb_thr = 0.48 if tier.upper() == "B" else 0.46

    if exit_cfg is None:
        exit_cfg = _default_exit_cfg(tier)

    def _close(pos: _OpenDiag, exit_time: datetime, exit_price: float, reason: str, rd: dict[str, Any]) -> None:
        pnl = ((exit_price - pos.entry_price) / pos.entry_price) * 100.0 if pos.entry_price > 0 else 0.0
        mfe = ((pos.peak_price - pos.entry_price) / pos.entry_price) * 100.0 if pos.entry_price > 0 else 0.0
        mae = ((pos.trough_price - pos.entry_price) / pos.entry_price) * 100.0 if pos.entry_price > 0 else 0.0
        elapsed = (exit_time - pos.entry_time).total_seconds() / 60.0
        ts_bf = elapsed if reason == "breakout_failure" else None
        rejects = pos.reject_reasons_at_entry.split(";") if pos.reject_reasons_at_entry else []
        low_q = _is_low_quality_entry(
            strict_timing_ok=pos.strict_timing_ok_at_entry,
            spread_bps=pos.entry_spread_bps,
            push_1m=pos.entry_push_density_1m,
            reject_reasons=rejects,
        )
        logic_stress = (
            not low_q
            and reason == "breakout_failure"
            and pnl < 0
            and elapsed <= 5.0
        )
        trades.append(
            DiagnosticTrade(
                symbol=symbol,
                cluster=cluster,
                time_bucket=pos.time_bucket,
                entry_time=pos.entry_time,
                exit_time=exit_time,
                entry_price=pos.entry_price,
                exit_price=exit_price,
                pnl_pct=pnl,
                exit_reason=reason,
                elapsed_min=elapsed,
                max_favorable_excursion_pct=mfe,
                max_adverse_excursion_pct=mae,
                signal_score_at_entry=pos.signal_score_at_entry,
                timing_ok_at_entry=pos.timing_ok_at_entry,
                strict_timing_ok_at_entry=pos.strict_timing_ok_at_entry,
                reject_reasons_at_entry=pos.reject_reasons_at_entry,
                entry_spread_bps=pos.entry_spread_bps,
                entry_push_density_1m=pos.entry_push_density_1m,
                entry_trading_value=pos.entry_trading_value,
                entry_volatility_5m_pct=pos.entry_volatility_5m_pct,
                exit_spread_bps=rd.get("spread_bps"),
                exit_push_density_1m=int(rd.get("push_samples_1m") or 0),
                exit_trading_value=rd.get("trading_value"),
                exit_volatility_5m_pct=_volatility_5m_pct(ring, exit_time, exit_price),
                time_since_breakout_min=ts_bf,
                immediate_adverse_2m=pos.min_pnl_first_2m <= IMMEDIATE_ADVERSE_PCT,
                mfe_above_threshold=mfe >= MFE_QUALITY_THRESHOLD_PCT,
                low_quality_entry=low_q,
                logic_stress_bf=logic_stress,
            )
        )

    for ev in events:
        ring.add_from_board(ev.board)
        strict_res, _ = evaluate_kabu_signal_v1(
            ev.board,
            push_history=ring,
            breakout_tracker=BreakoutStateTracker(),
            tier=tier,
            evaluated_at=ev.ts,
            cfg=strict_cfg,
        )
        result, tracker = evaluate_kabu_signal_v1(
            ev.board,
            push_history=ring,
            breakout_tracker=tracker,
            tier=tier,
            evaluated_at=ev.ts,
            cfg=sig_cfg,
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
            elapsed_entry = (ev.ts - position.entry_time).total_seconds() / 60.0
            if elapsed_entry <= IMMEDIATE_ADVERSE_WINDOW_MIN:
                pnl_now = ((px - position.entry_price) / position.entry_price) * 100.0
                if pnl_now < position.min_pnl_first_2m:
                    position.min_pnl_first_2m = pnl_now

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
                _close(position, ev.ts, px, exit_res.exit_reason, rd)
                position = None
            continue

        eligible = bool(rd.get("breakout_event")) and int(rd.get("signal_score") or 0) >= entry_score_min
        if require_timing_ok:
            eligible = eligible and bool(rd.get("timing_ok"))
        if tier.upper() == "C" or not eligible:
            continue
        trigger = rd.get("trigger_level")
        if trigger is None:
            continue
        session_high = rd.get("high_price")
        strict_rejects = strict_res.reject_reasons
        position = _OpenDiag(
            entry_time=ev.ts,
            entry_price=px,
            trigger_level=float(trigger),
            entry_vwap_dist_pct=rd.get("vwap_distance_pct"),
            session_high_at_entry=float(session_high) if session_high is not None else px,
            peak_price=px,
            trough_price=px,
            tier=tier.upper(),
            signal_score_at_entry=int(rd.get("signal_score") or 0),
            imbalance_low_streak=0,
            timing_ok_at_entry=bool(rd.get("timing_ok")),
            strict_timing_ok_at_entry=bool(strict_res.timing_ok),
            reject_reasons_at_entry=";".join(strict_rejects),
            entry_spread_bps=rd.get("spread_bps"),
            entry_push_density_1m=int(rd.get("push_samples_1m") or 0),
            entry_trading_value=rd.get("trading_value"),
            entry_volatility_5m_pct=_volatility_5m_pct(ring, ev.ts, px),
            cluster=cluster,
            time_bucket=jst_time_bucket(ev.ts),
            min_pnl_first_2m=0.0,
        )

    if position is not None and events:
        last = events[-1]
        last_px = float(last.board.get("CurrentPrice") or position.peak_price)
        ring.add_from_board(last.board)
        last_res, _ = evaluate_kabu_signal_v1(
            last.board,
            push_history=ring,
            breakout_tracker=tracker,
            tier=tier,
            evaluated_at=last.ts,
            cfg=sig_cfg,
        )
        _close(position, last.ts, last_px, "eod_close", last_res.to_dict())

    return trades


def _bucket_stats(rows: list[DiagnosticTrade], key_fn: Any) -> dict[str, Any]:
    groups: dict[str, list[DiagnosticTrade]] = {}
    for t in rows:
        k = key_fn(t)
        groups.setdefault(k, []).append(t)

    out: dict[str, Any] = {}
    for k, items in sorted(groups.items()):
        pnls = [t.pnl_pct for t in items]
        bf = [t for t in items if t.exit_reason == "breakout_failure"]
        out[k] = {
            "trades": len(items),
            "win_rate": sum(1 for p in pnls if p > 0) / len(pnls) if pnls else None,
            "avg_pnl_pct": statistics.mean(pnls) if pnls else None,
            "median_pnl_pct": statistics.median(pnls) if pnls else None,
            "max_loss_pct": min(pnls) if pnls else None,
            "breakout_failure_rate": len(bf) / len(items) if items else None,
            "immediate_adverse_2m_rate": sum(1 for t in items if t.immediate_adverse_2m) / len(items)
            if items
            else None,
            "mfe_above_threshold_rate": sum(1 for t in items if t.mfe_above_threshold) / len(items)
            if items
            else None,
            "low_quality_entry_rate": sum(1 for t in items if t.low_quality_entry) / len(items)
            if items
            else None,
            "logic_stress_bf_rate": sum(1 for t in items if t.logic_stress_bf) / len(items) if items else None,
        }
    return out


def breakout_failure_distribution(trades: list[DiagnosticTrade]) -> list[dict[str, Any]]:
    bf = [t for t in trades if t.exit_reason == "breakout_failure"]
    if not bf:
        return []

    def _bins(vals: list[float], edges: list[float]) -> dict[str, int]:
        counts = {f"{edges[i]}_{edges[i+1]}": 0 for i in range(len(edges) - 1)}
        counts["nan"] = 0
        for v in vals:
            if v is None or v != v:
                counts["nan"] += 1
                continue
            placed = False
            for i in range(len(edges) - 1):
                if edges[i] <= v < edges[i + 1]:
                    counts[f"{edges[i]}_{edges[i+1]}"] += 1
                    placed = True
                    break
            if not placed:
                counts[f"{edges[-2]}_{edges[-1]}"] += 1
        return counts

    spreads = [t.exit_spread_bps for t in bf]
    push = [float(t.exit_push_density_1m) for t in bf]
    tv = [t.exit_trading_value for t in bf if t.exit_trading_value is not None]
    vol = [t.exit_volatility_5m_pct for t in bf if t.exit_volatility_5m_pct is not None]
    tsb = [t.time_since_breakout_min for t in bf if t.time_since_breakout_min is not None]

    return [
        {
            "dimension": "spread_bps_at_bf_exit",
            "count": len(bf),
            "mean": statistics.mean([s for s in spreads if s is not None]) if spreads else None,
            "median": statistics.median([s for s in spreads if s is not None]) if spreads else None,
            "histogram": _bins([s for s in spreads if s is not None], [0, 8, 12, 18, 30, 999]),
        },
        {
            "dimension": "push_density_1m_at_bf_exit",
            "count": len(bf),
            "mean": statistics.mean(push) if push else None,
            "histogram": _bins(push, [0, 3, 6, 8, 12, 999]),
        },
        {
            "dimension": "trading_value_at_bf_exit",
            "count": len(tv),
            "mean": statistics.mean(tv) if tv else None,
            "median": statistics.median(tv) if tv else None,
        },
        {
            "dimension": "volatility_5m_pct_at_bf_exit",
            "count": len(vol),
            "mean": statistics.mean(vol) if vol else None,
            "median": statistics.median(vol) if vol else None,
        },
        {
            "dimension": "time_since_breakout_min",
            "count": len(tsb),
            "mean": statistics.mean(tsb) if tsb else None,
            "median": statistics.median(tsb) if tsb else None,
            "histogram": _bins(tsb, [0, 0.5, 1, 2, 5, 999]),
        },
    ]


def entry_vs_exit_quality(trades: list[DiagnosticTrade]) -> dict[str, Any]:
    if not trades:
        return {}
    return {
        "entry_quality": {
            "immediate_adverse_2m_rate": sum(1 for t in trades if t.immediate_adverse_2m) / len(trades),
            "mfe_above_threshold_rate": sum(1 for t in trades if t.mfe_above_threshold) / len(trades),
            "avg_mfe_pct": statistics.mean([t.max_favorable_excursion_pct for t in trades]),
            "avg_mae_pct": statistics.mean([t.max_adverse_excursion_pct for t in trades]),
        },
        "exit_quality": {
            "breakout_failure_share": sum(1 for t in trades if t.exit_reason == "breakout_failure") / len(trades),
            "hard_stop_share": sum(1 for t in trades if t.exit_reason == "hard_stop") / len(trades),
            "time_stop_share": sum(1 for t in trades if t.exit_reason == "time_stop") / len(trades),
            "eod_close_share": sum(1 for t in trades if t.exit_reason == "eod_close") / len(trades),
            "avg_elapsed_min": statistics.mean([t.elapsed_min for t in trades]),
        },
    }


def logic_vs_event_separation(trades: list[DiagnosticTrade]) -> dict[str, Any]:
    low_q = [t for t in trades if t.low_quality_entry]
    strict_q = [t for t in trades if not t.low_quality_entry]
    return {
        "hypothesis": "low_quality_entry=イベント品質問題 / logic_stress_bf=ロジックストレス（厳格ゲート通過後のBF）",
        "low_quality_entry": {
            "trades": len(low_q),
            "bf_rate": sum(1 for t in low_q if t.exit_reason == "breakout_failure") / len(low_q) if low_q else None,
            "avg_pnl_pct": statistics.mean([t.pnl_pct for t in low_q]) if low_q else None,
            "immediate_adverse_rate": sum(1 for t in low_q if t.immediate_adverse_2m) / len(low_q) if low_q else None,
        },
        "strict_gate_passed": {
            "trades": len(strict_q),
            "bf_rate": sum(1 for t in strict_q if t.exit_reason == "breakout_failure") / len(strict_q)
            if strict_q
            else None,
            "logic_stress_bf_rate": sum(1 for t in strict_q if t.logic_stress_bf) / len(strict_q) if strict_q else None,
            "avg_pnl_pct": statistics.mean([t.pnl_pct for t in strict_q]) if strict_q else None,
        },
        "interpretation_guide": {
            "event_quality_dominant": "low_quality_entry の BF率・逆行率が strict より有意に高い",
            "logic_dominant": "strict_gate_passed で logic_stress_bf_rate が高い（厳格 ENTRY 後も BF）",
        },
    }


def run_structure_analysis(
    *,
    day: str,
    yahoo_csv_by_symbol: dict[str, Path],
    tier: str = "B",
    replay_relaxed: bool = True,
    synthetic_events_per_minute: int = 10,
) -> dict[str, Any]:
    metrics = [compute_symbol_day_metrics(p) for p in yahoo_csv_by_symbol.values()]
    clusters = classify_symbols(metrics)
    all_trades: list[DiagnosticTrade] = []

    for symbol, csv_path in sorted(yahoo_csv_by_symbol.items()):
        events, _ = build_symbol_replay_events(
            symbol=symbol,
            yahoo_csv=csv_path,
            synthetic_events_per_minute=synthetic_events_per_minute,
        )
        cluster = clusters.get(symbol, LiquidityCluster.MID.value)
        trades = replay_symbol_diagnostic(
            symbol,
            events,
            cluster=cluster,
            tier=tier,
            replay_relaxed=replay_relaxed,
        )
        all_trades.extend(trades)

    return {
        "day": day,
        "tier": tier,
        "replay_relaxed_gates": replay_relaxed,
        "symbol_count": len(yahoo_csv_by_symbol),
        "cluster_definitions": {
            "etf": sorted(ETF_SYMBOLS),
            "high_price": f"median_close>={HIGH_PRICE_MEDIAN_CLOSE_YEN}",
            "liquidity": "non-ETF dollar_volume tertiles (ultra_high/mid/low)",
            "note": "個別銘柄チューニングなし。横断面分位のみ。",
        },
        "symbol_clusters": clusters,
        "symbol_metrics": [m.__dict__ for m in metrics],
        "trades": all_trades,
        "by_cluster": _bucket_stats(all_trades, lambda t: t.cluster),
        "by_time_bucket": _bucket_stats(all_trades, lambda t: t.time_bucket),
        "breakout_failure_distribution": breakout_failure_distribution(all_trades),
        "entry_vs_exit_quality": entry_vs_exit_quality(all_trades),
        "logic_vs_event_quality": logic_vs_event_separation(all_trades),
    }


def write_structure_outputs(out_dir: Path, analysis: dict[str, Any]) -> None:
    import csv

    out_dir.mkdir(parents=True, exist_ok=True)
    trades: list[DiagnosticTrade] = analysis["trades"]
    rows = [t.to_row() for t in trades]

    if rows:
        with (out_dir / "trades_enriched.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)

    summary = {k: v for k, v in analysis.items() if k != "trades"}
    (out_dir / "structure_analysis_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "by_cluster_summary.json").write_text(
        json.dumps(analysis["by_cluster"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "by_time_bucket_summary.json").write_text(
        json.dumps(analysis["by_time_bucket"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "breakout_failure_distribution.json").write_text(
        json.dumps(analysis["breakout_failure_distribution"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "entry_vs_exit_quality.json").write_text(
        json.dumps(analysis["entry_vs_exit_quality"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "logic_vs_event_quality.json").write_text(
        json.dumps(analysis["logic_vs_event_quality"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "symbol_clusters.json").write_text(
        json.dumps(
            {
                "clusters": analysis["symbol_clusters"],
                "metrics": analysis["symbol_metrics"],
                "definitions": analysis["cluster_definitions"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    bf_rows = analysis.get("breakout_failure_distribution") or []
    if bf_rows:
        with (out_dir / "breakout_failure_distribution.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["dimension", "count", "mean", "median", "histogram"])
            w.writeheader()
            for row in bf_rows:
                w.writerow(
                    {
                        "dimension": row.get("dimension"),
                        "count": row.get("count"),
                        "mean": row.get("mean"),
                        "median": row.get("median"),
                        "histogram": json.dumps(row.get("histogram"), ensure_ascii=False),
                    }
                )
