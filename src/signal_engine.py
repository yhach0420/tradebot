"""
yahoo_kabu_watch の「1分足由来エントリータイミング」判定コア（signals_eval 相当）。

paper_trade プロセス不要で DataFrame から単体検証できるよう、監視ループ以外の純関数を集約する。
yahoo_kabu_watch と数値整合する既定定数をここで一元管理する。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

import pandas as pd

# --- defaults (sync with historical yahoo_kabu_watch.py) ---

# (price - vwap) / vwap * 100 >= VWAP_DISTANCE_PCT
VWAP_DISTANCE_PCT = 0.5

ENTRY_NEAR_RATIO = 0.996

# entry = recent_5m_high * ENTRY_BREAKOUT_BUFFER
ENTRY_BREAKOUT_BUFFER = 1.001

# entry が前回突破時の entry からこの%以上変わったら breakout_state をリセット
BREAKOUT_ENTRY_RESET_PCT = 0.3


@dataclass(frozen=True)
class IntradaySignals:
    recent_5m_high: Optional[float]
    price_5min_ago: Optional[float]
    vwap: Optional[float]
    vwap_distance_pct: Optional[float]
    vol_3m_gt_prev_3m: Optional[bool]


def calc_intraday_signals_from_series(
    *,
    price: float,
    closes: list[Optional[float]],
    highs: list[Optional[float]],
    vols: list[Optional[float]],
    vwap: Optional[float],
) -> IntradaySignals:
    highs_valid = [x for x in highs if isinstance(x, (int, float))]
    closes_valid = [x for x in closes if isinstance(x, (int, float))]
    vols_valid = [x for x in vols if isinstance(x, (int, float))]

    recent_5m_high: Optional[float] = None
    price_5min_ago: Optional[float] = None
    vol_inc: Optional[bool] = None

    if len(highs_valid) >= 6:
        window = highs_valid[-6:-1]
        if window:
            recent_5m_high = float(max(window))

    if len(closes_valid) >= 6:
        price_5min_ago = float(closes_valid[-6])

    if len(vols_valid) >= 6:
        last3 = sum(float(x) for x in vols_valid[-3:])
        prev3 = sum(float(x) for x in vols_valid[-6:-3])
        vol_inc = last3 > prev3

    vwap_distance_pct: Optional[float] = None
    if isinstance(vwap, (int, float)) and float(vwap) > 0:
        vwap_distance_pct = ((float(price) - float(vwap)) / float(vwap)) * 100.0

    return IntradaySignals(
        recent_5m_high=recent_5m_high,
        price_5min_ago=price_5min_ago,
        vwap=(float(vwap) if isinstance(vwap, (int, float)) else None),
        vwap_distance_pct=vwap_distance_pct,
        vol_3m_gt_prev_3m=vol_inc,
    )


def calc_entry_from_signals(
    sig: Optional[IntradaySignals],
    *,
    entry_breakout_buffer: float = ENTRY_BREAKOUT_BUFFER,
) -> Optional[float]:
    if sig is None:
        return None
    if sig.recent_5m_high is None:
        return None
    base = float(sig.recent_5m_high)
    if base <= 0:
        return None
    return base * float(entry_breakout_buffer)


def collect_watch_timing_reject_reasons(
    *,
    price: float,
    sig: IntradaySignals,
    entry_candidate: Optional[float],
    vwap_distance_pct_min: float = VWAP_DISTANCE_PCT,
    entry_near_ratio: float = ENTRY_NEAR_RATIO,
) -> list[str]:
    """
    yahoo_kabu_watch 監視ループにおける「エントリータイミング」拒否理由を再現。
    （MA25・時価総額・スクリーニングなどは含めない）
    """
    reasons: list[str] = []

    if sig.vwap_distance_pct is None:
        reasons.append("VWAP取得不可")
    elif sig.vwap_distance_pct < float(vwap_distance_pct_min):
        reasons.append("VWAP乖離不足")

    if sig.recent_5m_high is None:
        reasons.append("直近5分高値が取れない")
    elif float(price) <= float(sig.recent_5m_high):
        reasons.append("5分高値ブレイク未成立")

    if sig.price_5min_ago is None:
        reasons.append("5分前価格が取れない")
    elif float(price) <= float(sig.price_5min_ago):
        reasons.append("上昇傾向なし")

    if sig.vol_3m_gt_prev_3m is not True:
        reasons.append("出来高増加なし")

    if entry_candidate is None:
        reasons.append("Entry計算不可")
    elif float(price) < (float(entry_candidate) * float(entry_near_ratio)):
        reasons.append("Entry候補から遠い")

    return reasons


def signal_score_from_gates(*, reasons: list[str], sig: IntradaySignals) -> int:
    """
    本番ループは出来高条件通過時に +1 するが、いまは全条件必須なので
    「全ゲート通過なら 1、否则 0」をスコアとして返す。
    """
    if reasons:
        return 0
    return 1 if sig.vol_3m_gt_prev_3m is True else 0


@dataclass
class BreakoutStateTracker:
    """entry 突破状態（🚀 相当）をバー送りでシミュレーション。"""

    breakout_state: bool = False
    last_breakout_entry: Optional[float] = None

    def step(self, *, price: float, entry: Optional[float], reset_pct: float = BREAKOUT_ENTRY_RESET_PCT) -> bool:
        crossed_now = False
        if entry is None:
            self.breakout_state = False
            self.last_breakout_entry = None
            return False

        ent = float(entry)
        px = float(price)

        st = self.breakout_state
        last = self.last_breakout_entry
        if st and last is not None and float(last) > 0:
            diff_pct = (abs(ent - float(last)) / float(last)) * 100.0
            if diff_pct >= float(reset_pct):
                self.breakout_state = False
                self.last_breakout_entry = None

        if px >= ent and not self.breakout_state:
            crossed_now = True
            self.breakout_state = True
            self.last_breakout_entry = ent

        if px < ent:
            self.breakout_state = False
            self.last_breakout_entry = None

        return crossed_now


def session_vwap_typical_to_index(df: pd.DataFrame, end_ix: int) -> Optional[float]:
    """先頭〜 end_ix までの (H+L+C)/3 * volume でセッション VWAP 近似。"""
    if end_ix < 0:
        return None
    sl = df.iloc[: end_ix + 1]
    tp = (sl["high"].astype(float) + sl["low"].astype(float) + sl["close"].astype(float)) / 3.0
    v = sl["volume"].astype(float).fillna(0.0)
    den = float(v.sum())
    if den <= 0:
        return None
    return float((tp * v).sum() / den)


def normalize_ohlcv_dataframe(df: pd.DataFrame, *, timestamp_column: str | None = None) -> pd.DataFrame:
    """
    期待列: open, high, low, close, volume + (任意) vwap
    時刻列: timestamp / timestamp_utc / DatetimeIndex
    """
    work = df.copy()
    ts_candidates = []
    if timestamp_column:
        ts_candidates.append(timestamp_column)
    ts_candidates.extend(["timestamp", "timestamp_utc", "time", "datetime"])

    renamed = False
    for cand in ts_candidates:
        if cand in work.columns:
            if cand != "timestamp":
                work = work.rename(columns={cand: "timestamp"})
            renamed = True
            break

    if not renamed:
        if isinstance(work.index, pd.DatetimeIndex):
            work = work.reset_index()
            first = str(work.columns[0])
            work = work.rename(columns={first: "timestamp"})
        else:
            raise ValueError(
                "時刻が必要です: 列 timestamp / timestamp_utc / DatetimeIndex のいずれかを用意してください。"
            )

    need = {"open", "high", "low", "close", "volume"}
    missing = need - set(work.columns)
    if missing:
        raise ValueError(f"列が不足: {sorted(missing)} — 与えられた列={list(work.columns)}")

    work["timestamp"] = pd.to_datetime(work["timestamp"], utc=True, errors="coerce")
    for c in ("open", "high", "low", "close", "volume"):
        work[c] = pd.to_numeric(work[c], errors="coerce")
    return work


def eval_signals_on_ohlcv_dataframe(
    df: pd.DataFrame,
    *,
    vwap_mode: str = "session_typical",
    vwap_column: str = "vwap",
    breakout_tracker: BreakoutStateTracker | None = None,
    vwap_distance_pct_min: float = VWAP_DISTANCE_PCT,
    entry_near_ratio: float = ENTRY_NEAR_RATIO,
    entry_breakout_buffer: float = ENTRY_BREAKOUT_BUFFER,
    reset_pct: float = BREAKOUT_ENTRY_RESET_PCT,
    vwap_resolver: Callable[[pd.DataFrame, int], Optional[float]] | None = None,
) -> tuple[pd.DataFrame, BreakoutStateTracker]:
    """
    各行を「そのバー終端の close が現値」のときのシグナルとして評価。

    vwap_mode:
      - "session_typical": 当該行までの typical×volume 累積 VWAP
      - "column": 列 vwap_column をその行のセッション VWAP として使用
      - "custom": vwap_resolver(df, i) を使用
    """
    work = normalize_ohlcv_dataframe(df)
    work = work.sort_values("timestamp").reset_index(drop=True)
    n = len(work)
    tracker = breakout_tracker or BreakoutStateTracker()

    rows: list[dict[str, Any]] = []

    for i in range(n):
        sub = work.iloc[: i + 1]
        closes = [float(x) if pd.notna(x) else None for x in sub["close"].tolist()]
        highs = [float(x) if pd.notna(x) else None for x in sub["high"].tolist()]
        vols = [float(x) if pd.notna(x) else None for x in sub["volume"].tolist()]
        price = float(sub["close"].iloc[-1])

        if vwap_mode == "session_typical":
            vwap = session_vwap_typical_to_index(work, i)
        elif vwap_mode == "column":
            if vwap_column not in work.columns:
                raise ValueError(f"vwap_column={vwap_column!r} が DataFrame にありません")
            raw = work[vwap_column].iloc[i]
            vwap = float(raw) if pd.notna(raw) else None
        elif vwap_mode == "custom":
            if vwap_resolver is None:
                raise ValueError("vwap_mode=custom には vwap_resolver が必要です")
            vwap = vwap_resolver(work, i)
        else:
            raise ValueError(f"unknown vwap_mode: {vwap_mode!r}")

        sig = calc_intraday_signals_from_series(
            price=price,
            closes=closes,
            highs=highs,
            vols=vols,
            vwap=vwap,
        )
        entry = calc_entry_from_signals(sig, entry_breakout_buffer=entry_breakout_buffer)
        reasons = collect_watch_timing_reject_reasons(
            price=price,
            sig=sig,
            entry_candidate=entry,
            vwap_distance_pct_min=vwap_distance_pct_min,
            entry_near_ratio=entry_near_ratio,
        )
        score = signal_score_from_gates(reasons=reasons, sig=sig)
        crossed = tracker.step(price=price, entry=entry, reset_pct=reset_pct)

        rows.append(
            {
                "timestamp": work["timestamp"].iloc[i],
                "price": price,
                "vwap_used": vwap,
                "vwap_distance_pct": sig.vwap_distance_pct,
                "recent_5m_high": sig.recent_5m_high,
                "price_5min_ago": sig.price_5min_ago,
                "vol_3m_gt_prev_3m": sig.vol_3m_gt_prev_3m,
                "entry_candidate": entry,
                "breakout_cross_now": crossed,
                "breakout_state_after": tracker.breakout_state,
                "reject_reasons": ";".join(reasons),
                "all_timing_gates_pass": len(reasons) == 0,
                "signal_score": score,
            }
        )

    return pd.DataFrame(rows), tracker


def compare_signal_eval_runs(
    left: pd.DataFrame,
    right: pd.DataFrame,
    *,
    suffixes: tuple[str, str] = ("_yahoo", "_kabu"),
    on: str = "timestamp",
) -> pd.DataFrame:
    """同一 timestamp をキーに左右の評価結果を横結合。"""
    return pd.merge(left, right, on=on, how="outer", suffixes=suffixes)
