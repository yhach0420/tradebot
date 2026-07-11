"""
PUSH メッセージ（板ベース・更新時のみ）から 1 分足風 OHLCV と指標近似を構築する PoC。

kabu PUSH は約定ティックではなく値更新時の board 相当スナップショットである点に注意。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Optional

JST = timezone(timedelta(hours=9))


def parse_push_time_to_utc(msg: Mapping[str, Any]) -> Optional[datetime]:
    """CurrentPriceTime 等の ISO 文字列を UTC に正規化。"""
    raw = msg.get("CurrentPriceTime")
    if not raw or not isinstance(raw, str):
        return None
    t = raw.strip()
    try:
        if t.endswith("Z"):
            dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(t)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JST)
    return dt.astimezone(timezone.utc)


def floor_minute_utc(dt: datetime) -> datetime:
    u = dt.astimezone(timezone.utc)
    return u.replace(second=0, microsecond=0)


@dataclass
class MinuteBar:
    """1 分バケット（PUSH の CurrentPrice サンプルから組み立て）。"""

    minute_start_utc: datetime
    open: float
    high: float
    low: float
    close: float
    volume_delta: float  # TradingVolume 累積の差分合算


class MinuteBarBuilderFromPush:
    """
    同一分内の PUSH を集約し、分が変わったら確定バーを返す。
    volume_delta は直前メッセージからの TradingVolume 差分の合算（負や欠損は 0 扱い）。
    """

    def __init__(self) -> None:
        self._bucket_start: Optional[datetime] = None
        self._o = self._h = self._l = self._c = 0.0
        self._vol_acc = 0.0
        self._last_cum_vol: Optional[float] = None
        self._has_px = False

    def _price(self, msg: Mapping[str, Any]) -> Optional[float]:
        p = msg.get("CurrentPrice")
        if p is None:
            p = msg.get("CalcPrice")
        if isinstance(p, (int, float)):
            return float(p)
        return None

    def _cum_volume(self, msg: Mapping[str, Any]) -> Optional[float]:
        v = msg.get("TradingVolume")
        if isinstance(v, (int, float)):
            return float(v)
        return None

    def feed(self, msg: Mapping[str, Any]) -> list[MinuteBar]:
        ts = parse_push_time_to_utc(msg)
        px = self._price(msg)
        if ts is None or px is None:
            return []

        minute = floor_minute_utc(ts)
        out: list[MinuteBar] = []

        if self._bucket_start is None:
            self._bucket_start = minute
            self._o = self._h = self._l = self._c = px
            self._vol_acc = 0.0
            self._has_px = True
        elif minute != self._bucket_start:
            if self._has_px:
                out.append(
                    MinuteBar(
                        minute_start_utc=self._bucket_start,
                        open=self._o,
                        high=self._h,
                        low=self._l,
                        close=self._c,
                        volume_delta=self._vol_acc,
                    )
                )
            self._bucket_start = minute
            self._o = self._h = self._l = self._c = px
            self._vol_acc = 0.0
            self._has_px = True
        else:
            self._h = max(self._h, px)
            self._l = min(self._l, px)
            self._c = px
            self._has_px = True

        cv = self._cum_volume(msg)
        if cv is not None and self._last_cum_vol is not None:
            dv = cv - self._last_cum_vol
            if dv > 0:
                self._vol_acc += dv
        if cv is not None:
            self._last_cum_vol = cv

        return out

    def flush(self) -> Optional[MinuteBar]:
        if not self._has_px or self._bucket_start is None:
            return None
        return MinuteBar(
            minute_start_utc=self._bucket_start,
            open=self._o,
            high=self._h,
            low=self._l,
            close=self._c,
            volume_delta=self._vol_acc,
        )


def recent_n_minute_high_excluding_current(
    completed_bars: list[MinuteBar], *, n: int = 5
) -> Optional[float]:
    """
    Yahoo `recent_5m_high` 近似: 確定済み n 本の high の最大（最新の未確定分は含めない）。
    """
    if n <= 0 or len(completed_bars) < n:
        return None
    window = completed_bars[-n:]
    return max(b.high for b in window)


def vwap_from_push_field(msg: Mapping[str, Any]) -> Optional[float]:
    """API が付与するセッション VWAP（PUSH に含まれる場合）。"""
    v = msg.get("VWAP")
    if isinstance(v, (int, float)) and float(v) > 0:
        return float(v)
    return None


def vwap_typical_from_bars(bars: Iterable[MinuteBar]) -> Optional[float]:
    """典型価格×出来高 での単純累積 VWAP（completed bars のみ）。"""
    num = 0.0
    den = 0.0
    for b in bars:
        tp = (b.high + b.low + b.close) / 3.0
        v = b.volume_delta
        if v > 0:
            num += tp * v
            den += v
    if den <= 0:
        return None
    return num / den
