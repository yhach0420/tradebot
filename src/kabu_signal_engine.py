"""
kabu_signal_v1 — kabu 板/PUSH 前提のエントリータイミング評価（ログ専用 Phase 5C）。

Yahoo 向け signal_engine とは独立。Discord / paper_trade には接続しない。
仕様: docs/kabu_signal_design.md
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional

from src.kabu_bar_builder import parse_push_time_to_utc
from src.signal_engine import BreakoutStateTracker

PROFILE_NAME = "kabu_signal_v1"

# --- thresholds (docs/kabu_signal_design.md §6) ---
QUOTE_AGE_MAX_SEC = 20.0
QUOTE_AGE_MAX_SEC_REST_FALLBACK = 45.0
SPREAD_BPS_MAX = 15.0
VWAP_DISTANCE_PCT_MIN = 0.35
VWAP_DISTANCE_PCT_SCORE_BONUS = 0.8
HIGH_PRICE_PROXIMITY_MIN = 0.985
HIGH_PRICE_SCORE_RATIO = 0.995
TRIGGER_BUFFER = 0.0005
NEAR_RATIO = 0.998
BREAKOUT_RESET_PCT = 0.5

MIN_TRADING_VALUE = 500_000_000.0
MIN_TRADING_VOLUME = 300_000.0
MIN_PUSH_SAMPLES_PER_MIN = 8
PUSH_SAMPLES_SCORE_BONUS = 15

BOOK_IMBALANCE_SCORE_MIN = 0.55
SCORE_NOTIFY_MIN = 60
SCORE_NEAR_MIN = 50

VOLUME_DELTA_FLOOR = 5_000.0
VOLUME_DELTA_TRADING_VALUE_RATIO = 0.001
VOLUME_DELTA_TIER_B_MULT = 1.2

ROLLING_HIGH_WINDOW = timedelta(minutes=5)
PUSH_DENSITY_WINDOW = timedelta(seconds=60)
VOLUME_DELTA_WINDOW = timedelta(seconds=30)
VOLUME_P75_LOOKBACK = timedelta(minutes=30)


@dataclass(frozen=True)
class KabuSignalV1Config:
    quote_age_max_sec: float = QUOTE_AGE_MAX_SEC
    quote_age_max_sec_rest: float = QUOTE_AGE_MAX_SEC_REST_FALLBACK
    spread_bps_max: float = SPREAD_BPS_MAX
    vwap_distance_pct_min: float = VWAP_DISTANCE_PCT_MIN
    high_price_proximity_min: float = HIGH_PRICE_PROXIMITY_MIN
    min_trading_value: float = MIN_TRADING_VALUE
    min_trading_volume: float = MIN_TRADING_VOLUME
    min_push_samples_per_min: float = MIN_PUSH_SAMPLES_PER_MIN
    trigger_buffer: float = TRIGGER_BUFFER
    near_ratio: float = NEAR_RATIO
    breakout_reset_pct: float = BREAKOUT_RESET_PCT


@dataclass
class PushHistoryRing:
    """PUSH / board 更新の価格・累積出来高リング。"""

    samples: list[tuple[datetime, float, Optional[float]]] = field(default_factory=list)
    volume_deltas: list[tuple[datetime, float]] = field(default_factory=list)
    max_samples: int = 5000

    def add(
        self,
        *,
        ts: datetime,
        price: float,
        cum_volume: Optional[float] = None,
    ) -> None:
        ts = ts.astimezone(timezone.utc)
        if self.samples:
            _, _, last_cv = self.samples[-1]
            if cum_volume is not None and last_cv is not None:
                dv = float(cum_volume) - float(last_cv)
                if dv > 0:
                    self.volume_deltas.append((ts, dv))
                    if len(self.volume_deltas) > self.max_samples:
                        self.volume_deltas = self.volume_deltas[-self.max_samples :]
        self.samples.append((ts, float(price), cum_volume))
        if len(self.samples) > self.max_samples:
            self.samples = self.samples[-self.max_samples :]

    def add_from_board(self, board: Mapping[str, Any]) -> None:
        ts = board_time_utc(board)
        price = board_current_price(board)
        if ts is None or price is None:
            return
        cv = _as_float(board.get("TradingVolume"))
        self.add(ts=ts, price=price, cum_volume=cv)

    def rolling_high_5m(self, *, as_of: datetime, current_price: float) -> Optional[float]:
        if not self.samples:
            return None
        cutoff = as_of.astimezone(timezone.utc) - ROLLING_HIGH_WINDOW
        prices = [
            p
            for t, p, _ in self.samples
            if cutoff <= t < as_of.astimezone(timezone.utc)
        ]
        if not prices:
            return None
        return float(max(prices))

    def push_samples_per_minute(self, *, as_of: datetime) -> int:
        cutoff = as_of.astimezone(timezone.utc) - PUSH_DENSITY_WINDOW
        return sum(1 for t, _, _ in self.samples if t >= cutoff)

    def volume_delta_30s(self, *, as_of: datetime) -> Optional[float]:
        if not self.volume_deltas:
            return None
        cutoff = as_of.astimezone(timezone.utc) - VOLUME_DELTA_WINDOW
        return sum(dv for t, dv in self.volume_deltas if t >= cutoff)

    def volume_delta_p75_30m(self, *, as_of: datetime) -> Optional[float]:
        if len(self.volume_deltas) < 4:
            return None
        cutoff = as_of.astimezone(timezone.utc) - VOLUME_P75_LOOKBACK
        vals = [dv for t, dv in self.volume_deltas if t >= cutoff]
        if len(vals) < 4:
            return None
        vals_sorted = sorted(vals)
        idx = int(0.75 * (len(vals_sorted) - 1))
        return float(vals_sorted[idx])

    @property
    def has_push_history(self) -> bool:
        return len(self.samples) >= 2


def _as_float(v: Any) -> Optional[float]:
    if isinstance(v, (int, float)):
        return float(v)
    return None


def board_current_price(board: Mapping[str, Any]) -> Optional[float]:
    p = _as_float(board.get("CurrentPrice"))
    if p is not None:
        return p
    return _as_float(board.get("CalcPrice"))


def board_time_utc(board: Mapping[str, Any]) -> Optional[datetime]:
    raw = board.get("CurrentPriceTime")
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.astimezone(timezone.utc)
    if isinstance(raw, str):
        return parse_push_time_to_utc({"CurrentPriceTime": raw})
    return None


def _level_qty(board: Mapping[str, Any], prefix: str, depth: int = 5) -> float:
    total = 0.0
    for i in range(1, depth + 1):
        key = f"{prefix}{i}"
        cell = board.get(key)
        if isinstance(cell, dict):
            q = _as_float(cell.get("Qty"))
        else:
            q = _as_float(board.get(f"{key}Qty")) if board.get(f"{key}Qty") is not None else None
        if q is not None and q > 0:
            total += q
    return total


def compute_book_imbalance(board: Mapping[str, Any], *, depth: int = 5) -> Optional[float]:
    bid = _as_float(board.get("BidQty")) or 0.0
    ask = _as_float(board.get("AskQty")) or 0.0
    bid += _level_qty(board, "Buy", depth)
    ask += _level_qty(board, "Sell", depth)
    den = bid + ask
    if den <= 0:
        return None
    return bid / den


def compute_spread_bps(board: Mapping[str, Any]) -> tuple[Optional[float], Optional[float]]:
    bid = _as_float(board.get("BidPrice"))
    ask = _as_float(board.get("AskPrice"))
    if bid is None or ask is None or ask <= bid:
        return None, None
    mid = (bid + ask) / 2.0
    if mid <= 0:
        return None, None
    spread_yen = ask - bid
    spread_bps = (spread_yen / mid) * 10_000.0
    return spread_yen, spread_bps


def flatten_board_dict(data: Mapping[str, Any]) -> dict[str, Any]:
    """
    kabu_api_check.json / board API レスポンスを評価用フラット dict に正規化。
    """
    if "CurrentPrice" in data and "Symbol" in data:
        return dict(data)

    out: dict[str, Any] = {}
    cq = data.get("current_quote")
    if isinstance(cq, dict):
        out.update(cq)
    excerpt = data.get("board_excerpt")
    if isinstance(excerpt, dict):
        out.update(excerpt)
    board = data.get("board")
    if isinstance(board, dict):
        out.update(board)
    if not out and isinstance(data, dict):
        out.update(data)
    return out


def volume_threshold(
    trading_value: Optional[float],
    *,
    tier: str,
    cfg: KabuSignalV1Config,
) -> float:
    base = VOLUME_DELTA_FLOOR
    if trading_value is not None and trading_value > 0:
        base = max(base, float(trading_value) * VOLUME_DELTA_TRADING_VALUE_RATIO)
    if tier.upper() == "B":
        base *= VOLUME_DELTA_TIER_B_MULT
    return base


@dataclass
class KabuSignalEvalResult:
    profile: str
    symbol: str
    exchange: Optional[int]
    evaluated_at_utc: str
    current_price: Optional[float]
    current_price_time: Optional[str]
    quote_age_sec: Optional[float]
    spread_yen: Optional[float]
    spread_bps: Optional[float]
    board_imbalance: Optional[float]
    vwap: Optional[float]
    vwap_distance_pct: Optional[float]
    high_price: Optional[float]
    high_proximity_ratio: Optional[float]
    trading_value: Optional[float]
    trading_volume: Optional[float]
    rolling_high_5m: Optional[float]
    trigger_level: Optional[float]
    volume_delta_30s: Optional[float]
    push_samples_1m: int
    has_push_history: bool
    timing_ok: bool
    reject_reasons: list[str]
    signal_score: int
    tier: str
    breakout_event: bool
    breakout_state_after: bool
    near_ok: bool
    notify_breakout_eligible: bool
    notify_near_eligible: bool
    data_mode: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "symbol": self.symbol,
            "exchange": self.exchange,
            "evaluated_at_utc": self.evaluated_at_utc,
            "current_price": self.current_price,
            "current_price_time": self.current_price_time,
            "quote_age_sec": self.quote_age_sec,
            "spread_yen": self.spread_yen,
            "spread_bps": self.spread_bps,
            "board_imbalance": self.board_imbalance,
            "vwap": self.vwap,
            "vwap_distance_pct": self.vwap_distance_pct,
            "high_price": self.high_price,
            "high_proximity_ratio": self.high_proximity_ratio,
            "trading_value": self.trading_value,
            "trading_volume": self.trading_volume,
            "rolling_high_5m": self.rolling_high_5m,
            "trigger_level": self.trigger_level,
            "volume_delta_30s": self.volume_delta_30s,
            "push_samples_1m": self.push_samples_1m,
            "has_push_history": self.has_push_history,
            "timing_ok": self.timing_ok,
            "reject_reasons": self.reject_reasons,
            "signal_score": self.signal_score,
            "tier": self.tier,
            "breakout_event": self.breakout_event,
            "breakout_state_after": self.breakout_state_after,
            "near_ok": self.near_ok,
            "notify_breakout_eligible": self.notify_breakout_eligible,
            "notify_near_eligible": self.notify_near_eligible,
            "data_mode": self.data_mode,
        }


def evaluate_kabu_signal_v1(
    board: Mapping[str, Any],
    *,
    push_history: Optional[PushHistoryRing] = None,
    breakout_tracker: Optional[BreakoutStateTracker] = None,
    tier: str = "B",
    evaluated_at: Optional[datetime] = None,
    rest_fallback: bool = False,
    cfg: Optional[KabuSignalV1Config] = None,
) -> tuple[KabuSignalEvalResult, BreakoutStateTracker]:
    """
    kabu_signal_v1 を 1 回評価する。

    push_history が無い / 空のときは REST 項目のみで評価し、
    PUSH 依存ゲートは reject_reasons にコードを追加する。
    """
    cfg = cfg or KabuSignalV1Config()
    tracker = breakout_tracker or BreakoutStateTracker()

    if isinstance(board, dict) and "current_quote" in board:
        flat = flatten_board_dict(board)
    else:
        flat = dict(board)
    symbol = str(flat.get("Symbol") or flat.get("symbol") or "")
    ex = flat.get("Exchange")
    exchange = int(ex) if isinstance(ex, (int, float)) else None

    price = board_current_price(flat)
    high = _as_float(flat.get("HighPrice"))
    vwap = _as_float(flat.get("VWAP"))
    tv = _as_float(flat.get("TradingValue"))
    tvol = _as_float(flat.get("TradingVolume"))

    board_ts = board_time_utc(flat)
    now = evaluated_at or datetime.now(timezone.utc)
    now = now.astimezone(timezone.utc)

    quote_age: Optional[float] = None
    if board_ts is not None:
        quote_age = max(0.0, (now - board_ts.astimezone(timezone.utc)).total_seconds())

    spread_yen, spread_bps = compute_spread_bps(flat)
    imbalance = compute_book_imbalance(flat)

    vwap_dist: Optional[float] = None
    if price is not None and vwap is not None and vwap > 0:
        vwap_dist = ((price - vwap) / vwap) * 100.0

    high_prox: Optional[float] = None
    if price is not None and high is not None and high > 0:
        high_prox = price / high

    history = push_history or PushHistoryRing()
    has_history = history.has_push_history
    data_mode = "push_and_rest" if has_history else "rest_only"

    rolling_high = None
    if has_history and price is not None:
        rolling_high = history.rolling_high_5m(as_of=now, current_price=price)

    push_per_min = history.push_samples_per_minute(as_of=now) if has_history else 0
    vol_delta_30s = history.volume_delta_30s(as_of=now) if has_history else None
    vol_p75 = history.volume_delta_p75_30m(as_of=now) if has_history else None

    trigger: Optional[float] = None
    if price is not None and high is not None:
        rh = rolling_high if rolling_high is not None else 0.0
        base = max(rh, high)
        if base > 0:
            trigger = base * (1.0 + cfg.trigger_buffer)

    rejects: list[str] = []

    age_limit = cfg.quote_age_max_sec_rest if rest_fallback else cfg.quote_age_max_sec
    if quote_age is None:
        rejects.append("G1_FRESHNESS_UNKNOWN")
    elif quote_age > age_limit:
        rejects.append("G1_FRESHNESS")

    if spread_bps is None:
        rejects.append("G2_SPREAD_UNKNOWN")
    elif spread_bps > cfg.spread_bps_max:
        rejects.append("G2_SPREAD")

    if vwap_dist is None:
        rejects.append("G3_VWAP_DIST_UNKNOWN")
    elif vwap_dist < cfg.vwap_distance_pct_min:
        rejects.append("G3_VWAP_DIST")

    if high_prox is None:
        rejects.append("G4_HIGH_PROXIMITY_UNKNOWN")
    elif high_prox < cfg.high_price_proximity_min:
        rejects.append("G4_HIGH_PROXIMITY")

    if not has_history:
        rejects.append("REST_ONLY_NO_PUSH_HISTORY")
        rejects.append("G5_ROLLING_HIGH_UNAVAILABLE")
        rejects.append("G8_PUSH_DENSITY_UNAVAILABLE")
    else:
        if rolling_high is None:
            rejects.append("G5_ROLLING_HIGH_INSUFFICIENT")
        elif price is not None and price <= rolling_high:
            rejects.append("G5_ROLLING_HIGH")

        if push_per_min < cfg.min_push_samples_per_min:
            rejects.append("G8_PUSH_DENSITY")

    vol_thr = volume_threshold(tv, tier=tier, cfg=cfg)
    if not has_history:
        rejects.append("G6_VOLUME_DELTA_UNAVAILABLE")
    elif vol_delta_30s is None:
        rejects.append("G6_VOLUME_DELTA_UNKNOWN")
    elif vol_delta_30s < vol_thr:
        rejects.append("G6_VOLUME_DELTA")

    if tv is None:
        rejects.append("G7_TRADING_VALUE_UNKNOWN")
    elif tv < cfg.min_trading_value:
        rejects.append("G7_TRADING_VALUE")

    if tvol is None:
        rejects.append("G7B_TRADING_VOLUME_UNKNOWN")
    elif tvol < cfg.min_trading_volume:
        rejects.append("G7B_TRADING_VOLUME")

    timing_ok = len(rejects) == 0

    score = 0
    if timing_ok:
        score = 40
        if vwap_dist is not None and vwap_dist >= VWAP_DISTANCE_PCT_SCORE_BONUS:
            score += 15
        if vol_delta_30s is not None and vol_p75 is not None and vol_delta_30s > vol_p75:
            score += 15
        if imbalance is not None and imbalance >= BOOK_IMBALANCE_SCORE_MIN:
            score += 10
        if price is not None and high is not None and price >= high * HIGH_PRICE_SCORE_RATIO:
            score += 10
        if push_per_min >= PUSH_SAMPLES_SCORE_BONUS:
            score += 10
        score = min(100, score)

    near_ok = False
    breakout_event = False
    if price is not None and trigger is not None:
        near_ok = price >= trigger * cfg.near_ratio
        breakout_event = tracker.step(
            price=price,
            entry=trigger,
            reset_pct=cfg.breakout_reset_pct,
        )

    tier_u = tier.upper()
    notify_breakout = (
        breakout_event
        and timing_ok
        and score >= SCORE_NOTIFY_MIN
        and tier_u in ("A", "B")
    )
    notify_near = (
        near_ok
        and not tracker.breakout_state
        and timing_ok
        and score >= SCORE_NEAR_MIN
        and tier_u in ("A", "B")
    )

    cpt_str = board_ts.isoformat() if board_ts else None

    result = KabuSignalEvalResult(
        profile=PROFILE_NAME,
        symbol=symbol,
        exchange=exchange,
        evaluated_at_utc=now.astimezone(timezone.utc).isoformat(),
        current_price=price,
        current_price_time=cpt_str,
        quote_age_sec=quote_age,
        spread_yen=spread_yen,
        spread_bps=spread_bps,
        board_imbalance=imbalance,
        vwap=vwap,
        vwap_distance_pct=vwap_dist,
        high_price=high,
        high_proximity_ratio=high_prox,
        trading_value=tv,
        trading_volume=tvol,
        rolling_high_5m=rolling_high,
        trigger_level=trigger,
        volume_delta_30s=vol_delta_30s,
        push_samples_1m=push_per_min,
        has_push_history=has_history,
        timing_ok=timing_ok,
        reject_reasons=rejects,
        signal_score=score,
        tier=tier_u,
        breakout_event=breakout_event,
        breakout_state_after=tracker.breakout_state,
        near_ok=near_ok,
        notify_breakout_eligible=notify_breakout,
        notify_near_eligible=notify_near,
        data_mode=data_mode,
    )
    return result, tracker


def ingest_push_jsonl_messages(path: str) -> PushHistoryRing:
    """JSONL 1 行 1 PUSH を履歴リングに読み込む。"""
    import json
    from pathlib import Path

    ring = PushHistoryRing()
    p = Path(path)
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            msg = json.loads(line)
            if isinstance(msg, dict):
                ring.add_from_board(msg)
    return ring
