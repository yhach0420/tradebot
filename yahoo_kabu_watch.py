"""
Yahoo Finance（非公式API）で日本株を監視・スクリーニングするツール（発注機能なし）
=================================================================

このスクリプトは、Yahoo Finance の「非公式API」からデータを取得して、
WATCHリストに入れた複数銘柄を 1 秒ごとに監視し、条件に合う銘柄だけ表示します。

Issue #1 の要件（スクリーニング条件）:
- WATCHリストで複数銘柄指定（例: 7203.T）
- 前日比 +1%以上
- 当日高値の 98%以上
- 出来高あり（0より大きい）

追加（Discord通知）:
- 条件に一致した銘柄を `DISCORD_WEBHOOK_URL` の Discord Webhook に通知します。
- 「同じ銘柄を連続通知しない」ため、直前ループで条件一致していた銘柄は通知しません。

注意:
- 非公式APIなので、仕様変更・アクセス制限で動かなくなる可能性があります。
- 取引判断や損益については自己責任でお願いします（本ツールは発注しません）。

動作確認の目安:
  Python 3.10+（3.9でもたぶん動きます）
  pip install requests
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

# requests は標準ライブラリではありません。無い場合は分かりやすく案内します。
try:
    import requests
except ImportError:  # pragma: no cover
    print("このツールは 'requests' が必要です。次を実行して入れてください:")
    print("  pip install requests")
    raise


# ----------------------------
# Yahoo Finance 非公式APIのURL
# ----------------------------
# 例:
# https://query1.finance.yahoo.com/v7/finance/quote?symbols=7203.T
YAHOO_QUOTE_URL = "https://query1.finance.yahoo.com/v7/finance/quote"

# quote が弾かれる環境向けの代替エンドポイント（こちらの方が通ることがあります）
# 例:
# https://query1.finance.yahoo.com/v8/finance/chart/7203.T?interval=1m&range=1d
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

# ----------------------------
# 過去データの仮想リプレイ（テスト用）
# ----------------------------
# 目的:
# - 相場時間外でも「過去の1分足」を1秒ごとに再生し、
#   いつもの判定ロジック（条件一致/条件外れ/候補価格変更）とDiscord通知を確認するための機能です。
#
# 重要:
# - TEST_REPLAY_MODE = False のときは、既存のリアルタイム監視（現在値の取得）を一切変えません。
# - コマンドラインで `python yahoo_kabu_watch.py --replay` を付けた時だけ True になります。
TEST_REPLAY_MODE = False

# ----------------------------
# 監視したい銘柄（ここを編集）
# ----------------------------
# 銘柄コードは「7203.T」の形式です。
# 例: トヨタ 7203.T / ソフトバンクG 9984.T
WATCH: list[str] = [
    "7203.T",
    "9984.T",
    "6232.T",
    "8053.T"
]

# ----------------------------
# スクリーニング条件（ここを編集）
# ----------------------------
MIN_CHANGE_PCT = 1.0          # 前日比（%）がこの値以上
MAX_CHANGE_PCT = 8.0          # 前日比（%）がこの値以上なら「急騰しすぎ」として除外（未満だけ通す）
MIN_RATIO_TO_DAY_HIGH = 0.98  # 現在値が当日高値の何%以上か（0.98 = 98%）
MIN_VOLUME = 300_000          # 最低出来高（この値以上だけ通す）

# ----------------------------
# エントリータイミング検知（追加条件）
# ----------------------------
# 目的:
# - 「強い銘柄っぽい」ではなく「エントリーに近い瞬間」だけ通知したい。
#
# 追加する必須条件:
# - 直近5分高値ブレイク（price > recent_5m_high）
# - 上昇傾向（price > price_5min_ago）
# - VWAP乖離（price が VWAP より 0.2%以上 上）
#
# 注意:
# - これらは 1分足系列が必要です（リアルタイムでは chart の 1m データを参照します）
VWAP_DISTANCE_PCT = 0.5  # (price - vwap) / vwap * 100 >= 0.5

# Entry候補（= entry）への接近条件:
# - 「ブレイクした！」だけで通知が出ると、entry候補から遠い場面でも通知が増えがちです。
# - そこで「entry候補の99.5%以上まで近づいたとき」だけ条件一致にします。
ENTRY_NEAR_RATIO = 0.996

# Entryの算出方法（新仕様）:
# - 以前: entry = 当日高値(day_high)
# - 変更: entry = 直近5分高値(recent_5m_high) * ENTRY_BREAKOUT_BUFFER
#
# ねらい:
# - 当日高値は「すでに遠い」ことが多く、強い上昇でも Entry未成立 になりやすい
# - 直近5分高値ベースにすると、ブレイクの“今この瞬間”に寄せやすい
ENTRY_BREAKOUT_BUFFER = 1.001  # 0.1%上抜け（例: 5000円 → 5005円）

# ----------------------------
# 出来高急増（5日平均出来高との比較）
# ----------------------------
MIN_VOLUME_SPIKE_RATIO = 2.0  # 現在出来高 >= 5日平均出来高 * この倍率

# 5日平均出来高は chart から計算します（毎秒取得は重いのでキャッシュ）。
VOL_AVG5_CACHE_TTL_SEC = 60 * 10  # 10分

# VWAP は日中に変わるので、比較的短めにキャッシュします
VWAP_CACHE_TTL_SEC = 60 * 5  # 5分

# 1分足系列（直近シグナル計算用）のキャッシュ:
# - 毎秒 chart を取りに行くと重いので、短いTTLでキャッシュします。
INTRADAY_SERIES_CACHE_TTL_SEC = 20

# ----------------------------
# 時価総額フィルタ
# ----------------------------
MIN_MARKET_CAP = 30_000_000_000     # 300億円以上
MAX_MARKET_CAP = 500_000_000_000   # 5000億円以下

# ----------------------------
# Discord 通知（Issue #1 追加要件）
# ----------------------------
# - Webhook URL は環境変数 `DISCORD_WEBHOOK_URL` から読みます。
# - 条件一致した銘柄のうち「前回ループでは候補に入っていなかった銘柄」だけ通知します
#   （= 同じ銘柄を連続通知しないための仕組みです）。
# - 初心者向けに、エントリー/損切り/利確候補は“分かりやすい簡易ルール”で計算します。
#
# 簡易ルール（必要なら調整してください）:
# - エントリー候補: 当日高値（break想定で「高値更新」を狙うイメージ）
# - 損切り候補: エントリーの -2% 下
# - 利確候補: エントリーの +4% 上（リスクリワードを 1:2 くらいの形に）
STOP_LOSS_PCT_FROM_ENTRY = 0.02
TAKE_PROFIT_PCT_FROM_ENTRY = 0.04

# ----------------------------
# 候補価格の大幅変更通知（Discord再通知）
# ----------------------------
# 条件一致中の銘柄でも、エントリー/損切り/利確の候補価格が大きく動いたら再通知します。
# 判定は「%」と「円」の両方で見ます（どちらかに引っかかれば通知）。
LEVEL_CHANGE_PCT = 1.0  # 1%以上変わったら通知
LEVEL_CHANGE_YEN = 10   # 10円以上変わったら通知

# ----------------------------
# 条件外れ通知の安定化（連続不一致で確定）
# ----------------------------
# 目的:
# - 一瞬だけ条件を割った（特にVWAP付近のチョン）で「条件外れ通知」が出てしまうのを防ぎたい。
#
# 仕様:
# - 2〜3分連続で条件不一致になった場合のみ条件外れ通知する
EXIT_CONFIRM_COUNT = 3

# ----------------------------
# Entry上抜け（breakout）状態のリセット条件
# ----------------------------
# 目的:
# - breakout_state が「昔のentry突破」を引きずると、新しいentry候補で再度ブレイクしても
#   🚀通知が出なくなります。
#
# 仕様:
# - entry が前回「突破済みになった時のentry」から 0.3%以上変わったら breakout_state を False に戻す
BREAKOUT_ENTRY_RESET_PCT = 0.3

# ----------------------------
# 25日移動平均（MA25）取得
# ----------------------------
# 25日移動平均は「日足の終値」を25本ぶん集めて平均します。
# 毎秒APIを叩くと重くなるので、一定時間キャッシュして使い回します。
MA25_CACHE_TTL_SEC = 60 * 10  # 10分に1回だけ取り直す（必要なら調整）


def _browser_headers(referer: Optional[str] = None) -> dict[str, str]:
    """
    Yahoo側に「普通のブラウザ」っぽく見せるためのヘッダ。
    401/403 を避ける目的で Referer/Accept も入れます。
    """
    h = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
    }
    if referer:
        h["Referer"] = referer
    return h


def warmup_session(session: requests.Session, symbol: str, timeout_sec: float = 10.0) -> None:
    """
    セッションにクッキー等を載せるための「肩慣らし」アクセス。
    環境によっては、先にWebページを見た扱いにすると API が通ることがあります。
    """
    # 公式ではないので、どのURLが効くかは環境次第です。失敗しても監視は続けます。
    url = f"https://finance.yahoo.com/quote/{symbol}"
    try:
        session.get(url, headers=_browser_headers(referer="https://finance.yahoo.com/"), timeout=timeout_sec)
    except Exception:
        pass


def now_str() -> str:
    """ログ表示用の現在時刻（ローカル時刻・見やすい形式）"""
    return datetime.now().strftime("%H:%M:%S")


@dataclass(frozen=True)
class Quote:
    """取得した株価の最小セット（初心者向けに構造をはっきりさせる）"""

    symbol: str
    price: float
    currency: str
    previous_close: Optional[float]  # 前日終値（previousClose）
    change_percent: Optional[float]  # 前日比（%）
    day_high: Optional[float]        # 当日高値
    # 当日安値（朝スクリーニング用に追加）
    # - Yahooのレスポンスによっては欠けることがあるため Optional にします。
    day_low: Optional[float]
    volume: Optional[float]          # 出来高（整数が多いが float で受ける）
    market_time_utc: Optional[datetime]
    market_cap: Optional[float]  # 時価総額（Yahoo Financeの値）


@dataclass(frozen=True)
class ReplayBar:
    """
    リプレイで使う「1分足1本」の最小セットです。
    - timestamp_utc: ローソク足の時刻（UTC）
    - open/high/low/close: OHLC
    - volume: その1分の出来高（バー出来高）
    """

    timestamp_utc: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class IntradaySignals:
    """
    「エントリータイミング検知」に必要な、直近の1分足由来シグナル。

    - recent_5m_high:
        直近5分の高値（※現在の足は含めない想定）
    - price_5min_ago:
        5分前の価格（1分足 close を基準）
    - vwap:
        日中VWAP（概算）
    - vwap_distance_pct:
        VWAP乖離率(%) = (price - vwap) / vwap * 100
    - vol_3m_gt_prev_3m:
        直近3分出来高合計 > その前の3分出来高合計（加点用）
    """

    recent_5m_high: Optional[float]
    price_5min_ago: Optional[float]
    vwap: Optional[float]
    vwap_distance_pct: Optional[float]
    vol_3m_gt_prev_3m: Optional[bool]


@dataclass
class ReplaySignalEval:
    """
    Replayモード専用:
    🚀 Entry上抜け（breakout）が出た瞬間を「signal」として記録し、
    その後の価格推移から期待値（伸びたか/耐えたか）を簡易検証します。

    注意:
    - 通常監視モードには影響させない（Replayモード内だけで使う）
    - Discord通知は不要（ターミナル表示のみ）
    """

    symbol: str

    # signal時点の情報
    signal_time_utc: datetime
    signal_price: float
    entry_price: float
    stop_price: float
    take_price: float

    # signal後の推移（signal時点から更新していく）
    max_price_after: float
    min_price_after: float
    last_price_after: float

    # 到達判定（固定take/stopは参考情報として残す）
    take_hit: bool = False
    stop_hit: bool = False

    # 新しい利確ロジック:
    # 1) signal_price から +1.0% 到達したら partial_take_hit=True
    partial_take_hit: bool = False
    partial_take_time_utc: Optional[datetime] = None

    # 2) partial_take_hit 後のトレーリング決済
    trailing_exit_price: Optional[float] = None
    trailing_exit_time_utc: Optional[datetime] = None
    trailing_exit_reason: Optional[str] = None  # "VWAP" / "recent_5m_low"

    # 最終損益（entry基準）
    final_profit_pct: Optional[float] = None

    resolved: bool = False
    result: str = "HOLD"  # WIN / LOSE / HOLD

    # ポジション種別（期待値検証の見分け用）
    # - "BASE": 通常のEntry上抜け（最初のポジション）
    # - "ADD1": 追加ポジション1
    # - "ADD2": 追加ポジション2
    position_kind: str = "BASE"

    # 決済スタイル:
    # - "trailing": 既存の「partial_take(+1%) → トレーリング決済」ロジック
    # - "fixed":    take_price 到達で WIN、stop_price 到達で LOSE（追加ポジション用）
    exit_style: str = "trailing"

    # =========================
    # 期待値検証の「除外フラグ」（追加仕様）
    # =========================
    # 初心者向けポイント:
    # - signal は検出されたとしても「期待値検証の集計対象に含めない」ことがあります。
    # - 例えば「同一銘柄は1日に1回まで採用」という制限モードでは、2回目以降を除外します。
    excluded_from_eval: bool = False
    excluded_reason: str = ""

    def update_with_price(
        self,
        *,
        time_utc: datetime,
        price: float,
        vwap: Optional[float],
        recent_5m_low: Optional[float],
    ) -> None:
        """
        価格更新（新しい利確ロジック版）

        - max/min を更新
        - stop は常に最優先で LOSE（仕様）
        - 固定take_price は参考として take_hit だけ記録（結果判定には使わない）
        - 新しい利確:
            1) signal_price から +1% 到達で partial_take_hit
            2) partial_take_hit 後は (price < VWAP) または (price < recent_5m_low) で EXIT
        """
        p = float(price)
        self.last_price_after = p
        if p > self.max_price_after:
            self.max_price_after = p
        if p < self.min_price_after:
            self.min_price_after = p

        if self.resolved:
            return

        # -----------------------------
        # 固定決済（追加ポジション向け）
        # -----------------------------
        # 初心者向けポイント:
        # - 追加ポジションは「浅い利確」を明確に検証したいので、
        #   take_price 到達でWIN、stop_price 到達でLOSEとして解決します。
        if str(self.exit_style) == "fixed":
            # stop は最優先（仕様）
            if p <= float(self.stop_price):
                self.stop_hit = True
                self.resolved = True
                self.result = "LOSE"
                if self.entry_price > 0:
                    self.final_profit_pct = ((p - self.entry_price) / self.entry_price) * 100.0
                return

            if p >= float(self.take_price):
                self.take_hit = True
                self.resolved = True
                self.result = "WIN"
                if self.entry_price > 0:
                    self.final_profit_pct = ((p - self.entry_price) / self.entry_price) * 100.0
                return

        # stop は最優先（仕様: stop到達なら LOSE）
        if p <= float(self.stop_price):
            self.stop_hit = True
            self.resolved = True
            self.result = "LOSE"
            if self.entry_price > 0:
                self.final_profit_pct = ((p - self.entry_price) / self.entry_price) * 100.0
            return

        # partial take（signal_price +1% 到達）
        if not self.partial_take_hit:
            target = float(self.signal_price) * 1.01
            if p >= target:
                self.partial_take_hit = True
                self.partial_take_time_utc = time_utc
                # partial_take_hit=True なら最低 HOLD 以上（仕様）
                self.result = "HOLD"
            return

        # partial_take_hit 後のトレーリング決済
        if self.partial_take_hit:
            if isinstance(vwap, (int, float)) and p < float(vwap):
                self.trailing_exit_price = p
                self.trailing_exit_time_utc = time_utc
                self.trailing_exit_reason = "VWAP"
                self.resolved = True
            elif isinstance(recent_5m_low, (int, float)) and p < float(recent_5m_low):
                self.trailing_exit_price = p
                self.trailing_exit_time_utc = time_utc
                self.trailing_exit_reason = "recent_5m_low"
                self.resolved = True

            if self.resolved:
                if self.entry_price > 0:
                    self.final_profit_pct = ((p - self.entry_price) / self.entry_price) * 100.0
                # partial_take_hit=True なら最低HOLD。利益が残っていればWIN。
                if self.final_profit_pct is not None and self.final_profit_pct > 0:
                    self.result = "WIN"
                else:
                    self.result = "HOLD"
                return

    def max_profit_pct(self) -> float:
        """
        最大利益率（%）:
        - entry を基準にします（エントリー基準の期待値を見るため）
        """
        if self.entry_price <= 0:
            return 0.0
        return ((self.max_price_after - self.entry_price) / self.entry_price) * 100.0

    def max_drawdown_pct(self) -> float:
        """
        最大ドローダウン（%）:
        - entry を基準に、最悪（min）を見ます（負の値になります）。
        """
        if self.entry_price <= 0:
            return 0.0
        return ((self.min_price_after - self.entry_price) / self.entry_price) * 100.0


@dataclass(frozen=True)
class MorningScreenResult:
    """
    朝スクリーニングの集計結果（1銘柄ぶん）。

    初心者向けポイント:
    - まず「必要な値をまとめた箱」を作っておくと、後の整形/Discord出力が楽になります。
    - Quote（現在値など）だけでは足りない指標（VWAP/MA25など）をここで追加保持します。
    """

    symbol: str
    score: int
    quote: Quote
    vwap: Optional[float]
    ma25: Optional[float]
    avg_vol5: Optional[float]
    vol_spike_ratio: Optional[float]
    day_range_pct: Optional[float]
    reasons: list[str]


def calc_entry_from_signals(sig: Optional[IntradaySignals]) -> Optional[float]:
    """
    Entry候補価格を「直近5分高値ベース」で計算します（新仕様）。

    entry = recent_5m_high * ENTRY_BREAKOUT_BUFFER

    - recent_5m_high が取れない場合は None（= entry も作れない）
    """
    if sig is None:
        return None
    if sig.recent_5m_high is None:
        return None
    base = float(sig.recent_5m_high)
    if base <= 0:
        return None
    return base * float(ENTRY_BREAKOUT_BUFFER)


# 最新の「直近シグナル」を、通知/再計算のために保持します。
# 重要:
# - Quote には recent_5m_high 等の情報が入っていないため、
#   entry の計算には「そのループで計算した IntradaySignals」が必要です。
# - そこで、各ループで計算した IntradaySignals を symbol ごとにここへ保存し、
#   calculate_entry(q) はそれを参照して entry を統一します。
_LATEST_INTRADAY_SIGNALS: dict[str, IntradaySignals] = {}


def calculate_entry(q: Quote) -> Optional[float]:
    """
    entry 計算の共通関数（仕様: すべてここを通す）。

    新仕様:
    - entry = recent_5m_high * ENTRY_BREAKOUT_BUFFER

    注意:
    - recent_5m_high は 1分足系列から計算するため、Quote単体では分かりません。
    - そのため、この関数は `_LATEST_INTRADAY_SIGNALS[q.symbol]` を参照します。
    - day_high は「参考表示」だけにし、entry計算には使いません。
    """
    sig = _LATEST_INTRADAY_SIGNALS.get(q.symbol)
    return calc_entry_from_signals(sig)


def _calc_change_percent(*, price: float, previous_close: Optional[float]) -> Optional[float]:
    """
    前日比（%）を previousClose から計算します。

    要件:
    - previousClose が存在する場合:
        change_percent = ((price - previousClose) / previousClose) * 100
    - previousClose が無い / 0 以下などで計算できない場合:
        None を返す（= 見送り理由「前日終値取得失敗」につながる）
    """
    if previous_close is None:
        return None
    pc = float(previous_close)
    if pc <= 0:
        return None
    return ((float(price) - pc) / pc) * 100.0


def fetch_ma25(session: requests.Session, symbol: str, timeout_sec: float = 10.0) -> Optional[float]:
    """
    25日移動平均（MA25）を取得します。

    ポイント（初心者向け）:
    - Yahooの quote API では移動平均が取れないことがあるため、chart（日足）から計算します。
    - 「過去25日分の終値」を集めて平均 = MA25（とてもベーシックなSMAです）
    - データが足りない/取れない場合は None を返します（= 見送り理由になる）
    """
    url = YAHOO_CHART_URL.format(symbol=symbol)
    params = {"interval": "1d", "range": "6mo"}  # 25本以上欲しいので6ヶ月ぶん取る
    referer = f"https://finance.yahoo.com/quote/{symbol}"
    headers = _browser_headers(referer=referer)

    r = session.get(url, params=params, headers=headers, timeout=timeout_sec)
    r.raise_for_status()
    data = r.json()

    chart = data.get("chart") or {}
    if chart.get("error"):
        return None
    results = chart.get("result") or []
    if not results:
        return None

    r0 = results[0] or {}
    indicators = r0.get("indicators") or {}
    quotes = indicators.get("quote") or []
    q0 = quotes[0] if quotes else {}
    closes = (q0 or {}).get("close") or []

    # close 配列には None が混ざることがあります（取引が無い日/欠損など）。
    # 後ろ（最新側）から 25 本ぶんの「数値」を集めます。
    last_25: list[float] = []
    for v in reversed(closes):
        if isinstance(v, (int, float)):
            last_25.append(float(v))
            if len(last_25) >= 25:
                break

    if len(last_25) < 25:
        return None
    return sum(last_25) / 25.0


def fetch_avg_volume_5(session: requests.Session, symbol: str, timeout_sec: float = 10.0) -> Optional[float]:
    """
    5日平均出来高（SMA5）を取得します。

    Yahoo Finance の chart（日足）から出来高配列を取り、
    「直近の5営業日分の出来高」を平均します。
    - データ不足なら None（= 表示/Discordでは N/A。現状は必須条件ではありません）
    """
    url = YAHOO_CHART_URL.format(symbol=symbol)
    params = {"interval": "1d", "range": "3mo"}  # 5営業日分を確実に確保（閑散期/祝日対策）
    referer = f"https://finance.yahoo.com/quote/{symbol}"
    headers = _browser_headers(referer=referer)

    r = session.get(url, params=params, headers=headers, timeout=timeout_sec)
    r.raise_for_status()
    data = r.json()

    chart = data.get("chart") or {}
    if chart.get("error"):
        return None
    results = chart.get("result") or []
    if not results:
        return None

    r0 = results[0] or {}
    indicators = r0.get("indicators") or {}
    quotes = indicators.get("quote") or []
    q0 = quotes[0] if quotes else {}
    volumes = (q0 or {}).get("volume") or []

    last_5: list[float] = []
    for v in reversed(volumes):
        if isinstance(v, (int, float)):
            vv = float(v)
            if vv > 0:
                last_5.append(vv)
                if len(last_5) >= 5:
                    break

    if len(last_5) < 5:
        return None
    return sum(last_5) / 5.0


def fetch_vwap(session: requests.Session, symbol: str, timeout_sec: float = 10.0) -> Optional[float]:
    """
    VWAP（出来高加重平均価格）を取得/推定します。

    Yahoo chart API が VWAP を直接返さない場合があるため、
    ここでは「1分足の典型価格（(高値+安値+終値)/3）×出来高」を累積して概算 VWAP を計算します。

    取得できない場合は None を返します（要件どおり、取得失敗でも条件からは除外しません）。
    """
    url = YAHOO_CHART_URL.format(symbol=symbol)
    params = {"interval": "1m", "range": "1d"}
    referer = f"https://finance.yahoo.com/quote/{symbol}"
    headers = _browser_headers(referer=referer)

    r = session.get(url, params=params, headers=headers, timeout=timeout_sec)
    r.raise_for_status()
    data = r.json()

    chart = data.get("chart") or {}
    if chart.get("error"):
        return None
    results = chart.get("result") or []
    if not results:
        return None

    r0 = results[0] or {}
    indicators = r0.get("indicators") or {}
    quotes = indicators.get("quote") or []
    q0 = quotes[0] if quotes else {}

    # 1) もし vwap 配列がそのまま返ってくるなら、それを使います
    if isinstance((q0 or {}).get("vwap"), list):
        vwap_arr = q0.get("vwap") or []
        for v in reversed(vwap_arr):
            if isinstance(v, (int, float)):
                vvp = float(v)
                if vvp > 0:
                    return vvp

    # 2) 返ってこない場合は概算 VWAP
    high_arr = (q0 or {}).get("high") or []
    low_arr = (q0 or {}).get("low") or []
    close_arr = (q0 or {}).get("close") or []
    vol_arr = (q0 or {}).get("volume") or []

    n = min(len(high_arr), len(low_arr), len(close_arr), len(vol_arr))
    if n <= 0:
        return None

    total_pv = 0.0
    total_v = 0.0
    # 最新側がいいが、順序はどちらでも計算できるので簡単に走査します。
    for i in range(n):
        h = high_arr[i]
        l = low_arr[i]
        c = close_arr[i]
        v = vol_arr[i]
        if not isinstance(h, (int, float)) or not isinstance(l, (int, float)) or not isinstance(c, (int, float)):
            continue
        if not isinstance(v, (int, float)):
            continue
        vv = float(v)
        if vv <= 0:
            continue
        # 典型価格（typical price）
        tp = (float(h) + float(l) + float(c)) / 3.0
        total_pv += tp * vv
        total_v += vv

    if total_v <= 0:
        return None
    return total_pv / total_v


def _fetch_quote_v7(session: requests.Session, symbol: str, timeout_sec: float = 10.0) -> Quote:
    """
    Yahoo Finance（非公式API v7/finance/quote）から現在値を取得します。

    返ってくるJSONはざっくりこんな形です（必要なところだけ）:
      {
        "quoteResponse": {
          "result": [
            {
              "symbol": "7203.T",
              "regularMarketPrice": 3000.0,
              "currency": "JPY",
              "regularMarketTime": 1710000000
            }
          ],
          "error": null
        }
      }

    - regularMarketPrice: 現在値（市場が開いていれば「今」の値に近い）
    - regularMarketTime: 価格の時刻（Unix秒, UTC）
    """

    referer = f"https://finance.yahoo.com/quote/{symbol}"
    headers = _browser_headers(referer=referer)
    params = {"symbols": symbol}

    r = session.get(YAHOO_QUOTE_URL, params=params, headers=headers, timeout=timeout_sec)
    r.raise_for_status()
    data = r.json()

    quote_resp = data.get("quoteResponse") or {}
    results = quote_resp.get("result") or []
    if not results:
        # symbol が間違っている / データが無い / API側仕様変更 などで起きます
        raise RuntimeError(f"価格データが見つかりませんでした。symbol={symbol} レスポンス={quote_resp!r}")

    q0 = results[0] or {}
    price = q0.get("regularMarketPrice")
    if price is None:
        # 取引時間外や、該当フィールドが欠けたケース
        raise RuntimeError(f"regularMarketPrice が取得できませんでした。symbol={symbol} データ={q0!r}")

    currency = q0.get("currency") or ""
    # previousClose（前日終値）: これが取れれば「前日比%」を自前で計算できます。
    # Yahoo側のキー名は環境/銘柄で揺れることがあるので複数候補を見ます。
    previous_close = q0.get("regularMarketPreviousClose")
    if previous_close is None:
        previous_close = q0.get("previousClose")
    if previous_close is None:
        previous_close = q0.get("chartPreviousClose")

    # 前日比（%）:
    # - Yahooが regularMarketChangePercent を返すこともありますが、環境/銘柄によって欠けることがあるため
    #   previousClose から計算した値を“正”として採用します（要件）。
    change_percent = _calc_change_percent(price=float(price), previous_close=float(previous_close) if isinstance(previous_close, (int, float)) else None)
    day_high = q0.get("regularMarketDayHigh")
    day_low = q0.get("regularMarketDayLow")
    volume = q0.get("regularMarketVolume")

    # 時価総額（marketCap）
    # Yahooのレスポンスキーは環境で揺れることがあるので複数候補を試します。
    market_cap_raw = q0.get("marketCap")
    if market_cap_raw is None:
        market_cap_raw = q0.get("marketCapFloat")
    market_cap: Optional[float]
    if isinstance(market_cap_raw, (int, float)):
        market_cap = float(market_cap_raw)
    elif isinstance(market_cap_raw, str):
        try:
            market_cap = float(market_cap_raw.replace(",", ""))
        except Exception:
            market_cap = None
    else:
        market_cap = None

    market_time = q0.get("regularMarketTime")
    market_time_utc = None
    if isinstance(market_time, (int, float)):
        market_time_utc = datetime.fromtimestamp(float(market_time), tz=timezone.utc)

    return Quote(
        symbol=symbol,
        price=float(price),
        currency=str(currency),
        previous_close=float(previous_close) if isinstance(previous_close, (int, float)) else None,
        change_percent=float(change_percent) if isinstance(change_percent, (int, float)) else None,
        day_high=float(day_high) if isinstance(day_high, (int, float)) else None,
        day_low=float(day_low) if isinstance(day_low, (int, float)) else None,
        volume=float(volume) if isinstance(volume, (int, float)) else None,
        market_time_utc=market_time_utc,
        market_cap=market_cap,
    )


def _fetch_quote_v8_chart(session: requests.Session, symbol: str, timeout_sec: float = 10.0) -> Quote:
    """
    Yahoo Finance（非公式API v8/finance/chart）から現在値を取得します（fallback用）。

    chart は「ローソク足系列」も返しますが、ここでは以下のどちらかで現在値を取ります:
    - meta.regularMarketPrice（あれば最優先）
    - indicators.quote[0].close の末尾（最後の終値/現在値に近い値）
    """
    url = YAHOO_CHART_URL.format(symbol=symbol)
    # interval/range は最小限でOK。細かい足が欲しいわけではなく「今の値」を取りたいだけです。
    params = {"interval": "1m", "range": "1d"}

    referer = f"https://finance.yahoo.com/quote/{symbol}"
    headers = _browser_headers(referer=referer)

    r = session.get(url, params=params, headers=headers, timeout=timeout_sec)
    r.raise_for_status()
    data = r.json()

    chart = (data.get("chart") or {})
    error = chart.get("error")
    if error:
        raise RuntimeError(f"chart error: {error!r}")

    results = chart.get("result") or []
    if not results:
        raise RuntimeError(f"chart result が空です。symbol={symbol}")

    r0 = results[0] or {}
    meta = r0.get("meta") or {}

    currency = str(meta.get("currency") or "")
    # chart の meta は previousClose のキー名が環境で揺れることがあります。
    # よくある候補を順に試します。
    previous_close = (
        meta.get("previousClose")
        if isinstance(meta.get("previousClose"), (int, float))
        else meta.get("chartPreviousClose")
        if isinstance(meta.get("chartPreviousClose"), (int, float))
        else None
    )
    day_high = meta.get("regularMarketDayHigh")
    day_low = meta.get("regularMarketDayLow")
    volume = meta.get("regularMarketVolume")
    market_cap_raw = meta.get("marketCap")
    if market_cap_raw is None:
        market_cap_raw = meta.get("marketCapFloat")
    market_cap: Optional[float]
    if isinstance(market_cap_raw, (int, float)):
        market_cap = float(market_cap_raw)
    elif isinstance(market_cap_raw, str):
        try:
            market_cap = float(market_cap_raw.replace(",", ""))
        except Exception:
            market_cap = None
    else:
        market_cap = None

    # 1) meta.regularMarketPrice があればそれを使う（「現在値」として最もそれっぽい）
    price = meta.get("regularMarketPrice")

    # 2) 無ければ close 配列の最後の非nullを探す
    if price is None:
        indicators = r0.get("indicators") or {}
        quotes = indicators.get("quote") or []
        q0 = quotes[0] if quotes else {}
        closes = (q0 or {}).get("close") or []
        for v in reversed(closes):
            if v is not None:
                price = v
                break

    if price is None:
        raise RuntimeError(f"現在値が見つかりませんでした（chart）。symbol={symbol}")

    # 前日比（%）は previousClose から計算します（要件）。
    change_percent = _calc_change_percent(price=float(price), previous_close=float(previous_close) if isinstance(previous_close, (int, float)) else None)

    market_time = meta.get("regularMarketTime")
    market_time_utc = None
    if isinstance(market_time, (int, float)):
        market_time_utc = datetime.fromtimestamp(float(market_time), tz=timezone.utc)

    return Quote(
        symbol=symbol,
        price=float(price),
        currency=currency,
        previous_close=float(previous_close) if isinstance(previous_close, (int, float)) else None,
        change_percent=float(change_percent) if isinstance(change_percent, (int, float)) else None,
        day_high=float(day_high) if isinstance(day_high, (int, float)) else None,
        day_low=float(day_low) if isinstance(day_low, (int, float)) else None,
        volume=float(volume) if isinstance(volume, (int, float)) else None,
        market_time_utc=market_time_utc,
        market_cap=market_cap,
    )


def fetch_quote(session: requests.Session, symbol: str, timeout_sec: float = 10.0) -> Quote:
    """
    現在値取得の「本体」。
    まず v7/quote を試し、401/403 などで弾かれたら warmup → 再試行 → だめなら chart にフォールバックします。
    """
    try:
        return _fetch_quote_v7(session, symbol, timeout_sec=timeout_sec)
    except requests.HTTPError as e:
        status = getattr(e.response, "status_code", None)
        # 401/403 は環境依存で出ることがあります（今回の検証環境もこれ）。
        if status in (401, 403):
            warmup_session(session, symbol, timeout_sec=timeout_sec)
            # warmup 後にもう一度 quote
            try:
                return _fetch_quote_v7(session, symbol, timeout_sec=timeout_sec)
            except requests.HTTPError as e2:
                status2 = getattr(e2.response, "status_code", None)
                if status2 in (401, 403):
                    # quote がダメなら chart を試す
                    return _fetch_quote_v8_chart(session, symbol, timeout_sec=timeout_sec)
                raise
            except Exception:
                # quote の再試行で別のエラーになったら chart を試す
                return _fetch_quote_v8_chart(session, symbol, timeout_sec=timeout_sec)
        raise
    except Exception:
        # quote のパース失敗などは chart を試す（非公式なのでフィールド欠損があり得ます）
        return _fetch_quote_v8_chart(session, symbol, timeout_sec=timeout_sec)


def fetch_history_1m(
    session: requests.Session,
    symbol: str,
    *,
    range_str: str,
    timeout_sec: float = 20.0,
) -> tuple[list[ReplayBar], dict]:
    """
    Yahoo Finance の chart API から「過去1分足」を取得します（リプレイ用）。

    仕様:
    - interval=1m
    - range は "1d" / "5d" / "10d" / "20d" / "60d"
      - ただし Yahoo Finance 側の仕様都合で、そのまま通らないことがあります。
      - その場合は、内部で「近いYahooのrange」へマップして取得します（検証用途向け）。

    戻り値:
    - bars: 1分足の配列（欠損データは除外）
    - meta: chart.result[0].meta（currency/previousClose等が入ることがあります）
    """
    # Yahoo Finance の chart API は、range に "1d","5d","1mo","3mo"... のような値を要求します。
    # 一方でユーザー要件として「10d/20d/60d」を指定したいので、近い値へマップして互換を持たせます。
    range_map = {
        "1d": "1d",
        "5d": "5d",
        # 10d/20d は概ね 1ヶ月(1mo) に含まれる想定
        "10d": "1mo",
        "20d": "1mo",
        # 60d は概ね 3ヶ月(3mo) に含まれる想定
        "60d": "3mo",
    }
    if range_str not in range_map:
        raise ValueError("range_str は '1d','5d','10d','20d','60d' を指定してください")

    url = YAHOO_CHART_URL.format(symbol=symbol)
    params = {"interval": "1m", "range": range_map[range_str]}
    referer = f"https://finance.yahoo.com/quote/{symbol}"
    headers = _browser_headers(referer=referer)

    r = session.get(url, params=params, headers=headers, timeout=timeout_sec)
    r.raise_for_status()
    data = r.json()

    chart = (data.get("chart") or {})
    error = chart.get("error")
    if error:
        raise RuntimeError(f"chart error: {error!r}")

    results = chart.get("result") or []
    if not results:
        raise RuntimeError(f"chart result が空です。symbol={symbol}")

    r0 = results[0] or {}
    meta = r0.get("meta") or {}
    ts_arr = r0.get("timestamp") or []
    indicators = r0.get("indicators") or {}
    quotes = indicators.get("quote") or []
    q0 = quotes[0] if quotes else {}

    opens = (q0 or {}).get("open") or []
    highs = (q0 or {}).get("high") or []
    lows = (q0 or {}).get("low") or []
    closes = (q0 or {}).get("close") or []
    vols = (q0 or {}).get("volume") or []

    n = min(len(ts_arr), len(opens), len(highs), len(lows), len(closes), len(vols))
    bars: list[ReplayBar] = []

    # 欠損（None）が混ざりやすいので、数値が揃っている行だけ採用します。
    for i in range(n):
        ts = ts_arr[i]
        o = opens[i]
        h = highs[i]
        l = lows[i]
        c = closes[i]
        v = vols[i]
        if not isinstance(ts, (int, float)):
            continue
        if not all(isinstance(x, (int, float)) for x in (o, h, l, c, v)):
            continue
        dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
        bars.append(
            ReplayBar(
                timestamp_utc=dt,
                open=float(o),
                high=float(h),
                low=float(l),
                close=float(c),
                volume=float(v),
            )
        )

    return bars, meta


def fetch_intraday_1m_series(
    session: requests.Session,
    symbol: str,
    *,
    timeout_sec: float = 20.0,
) -> tuple[list[float], list[float], list[float]]:
    """
    リアルタイム監視用に「直近の1分足系列」を取ります。

    返すもの:
    - closes: close配列（Noneは除外しないでそのまま入る可能性があるので呼び出し側で注意）
    - highs:  high配列
    - vols:   volume配列（1分ごとの出来高）

    なぜ必要？
    - recent_5m_high（直近5分高値）
    - price_5min_ago（5分前の価格）
    - 直近3分出来高 vs その前3分出来高（加点）
    を出すためです。
    """
    url = YAHOO_CHART_URL.format(symbol=symbol)
    params = {"interval": "1m", "range": "1d"}
    referer = f"https://finance.yahoo.com/quote/{symbol}"
    headers = _browser_headers(referer=referer)

    r = session.get(url, params=params, headers=headers, timeout=timeout_sec)
    r.raise_for_status()
    data = r.json()

    chart = (data.get("chart") or {})
    if chart.get("error"):
        raise RuntimeError(f"chart error: {chart.get('error')!r}")
    results = chart.get("result") or []
    if not results:
        raise RuntimeError("chart result が空です")

    r0 = results[0] or {}
    indicators = r0.get("indicators") or {}
    quotes = indicators.get("quote") or []
    q0 = quotes[0] if quotes else {}

    closes = (q0 or {}).get("close") or []
    highs = (q0 or {}).get("high") or []
    vols = (q0 or {}).get("volume") or []

    # 型をそろえる（Noneが混ざり得るので list[float] ではなく list を返したいが、
    # 既存コードの型と合わせるため、数値だけをfloatに寄せつつ、Noneはそのまま残します）
    def _as_float_or_none(x):
        return float(x) if isinstance(x, (int, float)) else None

    closes2 = [_as_float_or_none(x) for x in closes]
    highs2 = [_as_float_or_none(x) for x in highs]
    vols2 = [_as_float_or_none(x) for x in vols]
    return closes2, highs2, vols2


def calc_intraday_signals_from_series(
    *,
    price: float,
    closes: list[Optional[float]],
    highs: list[Optional[float]],
    vols: list[Optional[float]],
    vwap: Optional[float],
) -> IntradaySignals:
    """
    1分足の配列から、エントリー判定に必要なシグナルを計算します。

    仕様対応:
    - recent_5m_high: 直近5分の高値（最新足は除外して計算）
    - price_5min_ago: 5分前の価格（close）
    - VWAP乖離率
    - 出来高増加（直近3分合計 > その前3分合計）
    """
    # Noneを除いた「末尾の有効データ列」を作ります（欠損があるときの耐性）
    highs_valid = [x for x in highs if isinstance(x, (int, float))]
    closes_valid = [x for x in closes if isinstance(x, (int, float))]
    vols_valid = [x for x in vols if isinstance(x, (int, float))]

    recent_5m_high: Optional[float] = None
    price_5min_ago: Optional[float] = None
    vol_inc: Optional[bool] = None

    # recent_5m_high:
    # - 「直近5分」= 直近5本の1分足
    # - 「上抜け」判定に使うので、現在の足（最新1本）は除外して max を取ります
    if len(highs_valid) >= 6:
        window = highs_valid[-6:-1]  # 5本
        if window:
            recent_5m_high = float(max(window))

    # price_5min_ago:
    # - close の 5本前（最新を含めた時系列の -6 番目）
    if len(closes_valid) >= 6:
        price_5min_ago = float(closes_valid[-6])

    # 出来高増加（加点用）:
    # - 直近3分合計 vs その前3分合計
    if len(vols_valid) >= 6:
        last3 = sum(float(x) for x in vols_valid[-3:])
        prev3 = sum(float(x) for x in vols_valid[-6:-3])
        vol_inc = last3 > prev3

    # VWAP乖離率
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

def parse_args(argv: list[str]) -> argparse.Namespace:
    """
    コマンドライン引数の解析。

    初心者向けポイント:
    - コマンドで指定できるようにすると、毎回ファイルを編集しなくて良くなります。
    """
    p = argparse.ArgumentParser(
        prog="yahoo_kabu_watch.py",
        description="Yahoo Finance（非公式API）で日本株の現在値を1秒ごとに監視し、上抜けを表示します（発注なし）。",
    )
    p.add_argument(
        "--morning-screen",
        action="store_true",
        help=(
            "朝スクリーニング機能を実行します。"
            " デイトレ開始前に『その日触るべき監視候補』を自動選定して、ターミナルとDiscordに出力します。"
            " 通常監視やReplayには影響しません。"
        ),
    )
    p.add_argument(
        "--replay",
        action="store_true",
        help=(
            "過去データの仮想リプレイ（テスト）を有効化します。"
            " Yahoo Finance の過去1分足を取得し、1秒ごとに1分ずつ再生して通常の判定/Discord通知を動かします。"
        ),
    )
    p.add_argument(
        "--replay-range",
        type=str,
        default="1d",
        choices=["1d", "5d", "10d", "20d", "60d"],
        help="リプレイで取得する期間。1d/5d/10d/20d/60d。デフォルト 1d",
    )
    p.add_argument(
        "--replay-morning-screen",
        type=str,
        default="",
        help=(
            "Replay中に、指定したJST時刻（HH:MM）で『Morning Screen → 監視銘柄へ自動追加 → その後のsignal検証』を行います。"
            " 例: --replay-morning-screen 09:07"
        ),
    )
    p.add_argument(
        "--one-trade-per-symbol-per-day",
        action="store_true",
        help=(
            "Replay期待値検証で『同一銘柄は1日に最大1回まで』Entry signal を採用します（JST日付基準）。"
            " このモードでは、同じJST日付で同じsymbolの2回目以降のsignalは、検出ログは出してもよいが集計対象外にします。"
        ),
    )
    p.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="取得間隔（秒）。デフォルト 1.0",
    )
    p.add_argument(
        "--watch",
        type=str,
        default="",
        help="監視銘柄をカンマ区切りで指定（例: 7203.T,9984.T）。指定するとファイル上部の WATCH を上書きします。",
    )
    p.add_argument(
        "--watch-file",
        type=str,
        default="",
        help="監視銘柄を1行1銘柄で書いたファイルパス。空行と # から始まる行は無視します。--watch より優先。",
    )
    p.add_argument(
        "--print-all",
        action="store_true",
        help="条件に合わない銘柄も含めて毎回ログを出します（デバッグ用）。",
    )
    p.add_argument(
        "--only-changes",
        action="store_true",
        help="候補リストが変わった時だけ表示します（出力を減らしたい場合）。",
    )
    return p.parse_args(argv)


def _fmt_price(x: Optional[float]) -> str:
    """
    価格を見やすく表示するための整形です。
    - 四捨五入して整数っぽく見える場合は整数で返します
    - 少数が必要なら小数2桁まで出します
    """
    if x is None:
        return "N/A"
    xf = float(x)
    if abs(xf - round(xf)) < 1e-9:
        return str(int(round(xf)))
    return f"{xf:.2f}"


def _fmt_volume(v: Optional[float]) -> str:
    if v is None:
        return "N/A"
    # 出来高は整数が多いので、表示は整数に寄せます。
    return str(int(round(float(v))))


# =========================
# Discord通知の表示（Embed用の整形）
# =========================
# Discord Embed は「title / color / fields」などを持てるので、
# 長い文章よりも「重要情報を上に」「補足は下に」まとめやすくなります。


def _fmt_yen(x: Optional[float]) -> str:
    """価格は「小数なし・円表示」に統一します。"""
    if x is None:
        return "N/A"
    return f"{int(round(float(x)))}円"


def _fmt_pct(x: Optional[float]) -> str:
    """前日比%は小数2桁、符号付きで表示します。"""
    if x is None:
        return "N/A"
    xf = float(x)
    sign = "+" if xf >= 0 else ""
    return f"{sign}{xf:.2f}%"


def _fmt_ratio_pct(numer: Optional[float], denom: Optional[float]) -> str:
    """例: 高値接近率 = price/day_high * 100（小数1桁）"""
    if numer is None or denom is None:
        return "N/A"
    d = float(denom)
    if d <= 0:
        return "N/A"
    return f"{(float(numer) / d) * 100.0:.1f}%"


def _fmt_volume_man(v: Optional[float]) -> str:
    """
    出来高は「万株」表示にします。
    例: 8,940,000 → 894万株
    """
    if v is None:
        return "N/A"
    man = int(round(float(v) / 10_000.0))
    return f"{man}万株"


def _embed_field(name: str, value: str, *, inline: bool = False) -> dict[str, Any]:
    """Embed fields の最小構造。"""
    return {"name": name, "value": value, "inline": bool(inline)}


JST = timezone(timedelta(hours=9))


def _fmt_dt_jst(dt_utc: Optional[datetime]) -> str:
    """
    UTCの datetime を JST 表示にします（リプレイの視認性改善用）。
    例: 2026-05-01 15:23:00
    """
    if dt_utc is None:
        return "N/A"
    # dt_utc は tz=UTC の想定。念のため tz が無い場合も UTC として扱います。
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    return dt_utc.astimezone(JST).strftime("%Y-%m-%d %H:%M:%S")


def _fmt_dt_jst_short(dt_utc: Optional[datetime]) -> str:
    """Embed内用の短いJST表示。例: 2026-05-01 15:23 JST"""
    if dt_utc is None:
        return "N/A"
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    return dt_utc.astimezone(JST).strftime("%Y-%m-%d %H:%M JST")


def build_embed_match(
    q: Quote,
    *,
    entry: float,
    stop: float,
    take: float,
    vwap: Optional[float],
    ma25: float,
    replay_time_jst: Optional[str] = None,
    recent_5m_high: Optional[float] = None,
    price_5min_ago: Optional[float] = None,
    vwap_distance_pct: Optional[float] = None,
    vol_increase: Optional[bool] = None,
    entry_near_ratio: Optional[float] = None,
    entry_crossed: Optional[bool] = None,
    cross_target_entry: Optional[float] = None,
    prev_entry_snapshot: Optional[float] = None,
    breakout_state: Optional[bool] = None,
) -> dict[str, Any]:
    """
    【条件一致】Embed
    - 重要情報を上にまとめ、補足は下に置きます。
    """
    fields: list[dict[str, Any]] = []
    # リプレイ時は「この通知がどの時刻の再生か」を最上段に出します。
    # 通常モードでは None で渡せば表示されません。
    if replay_time_jst:
        fields.append(_embed_field("Replay時刻", replay_time_jst, inline=False))
    fields.append(_embed_field("現在値", _fmt_yen(q.price), inline=True))
    fields.append(_embed_field("前日比", _fmt_pct(q.change_percent), inline=True))
    fields.append(_embed_field("出来高", _fmt_volume_man(q.volume), inline=True))
    fields.append(_embed_field("高値接近率", _fmt_ratio_pct(q.price, q.day_high), inline=True))

    # 追加情報（エントリー判断に必要なもの）
    fields.append(_embed_field("直近5分高値", _fmt_yen(recent_5m_high), inline=True))
    fields.append(_embed_field("5分前価格", _fmt_yen(price_5min_ago), inline=True))
    if vwap_distance_pct is None:
        vwap_dist_s = "N/A"
    else:
        vwap_dist_s = f"{vwap_distance_pct:.2f}%"
    fields.append(_embed_field("VWAP乖離率", vwap_dist_s, inline=True))
    if vol_increase is None:
        vol_inc_s = "N/A"
    else:
        vol_inc_s = "あり" if vol_increase else "なし"
    fields.append(_embed_field("出来高増加", vol_inc_s, inline=True))

    # Entry算出元（新仕様）:
    # - Entryは「直近5分高値 × バッファ」で計算しています。
    # - 当日高値(day_high)は参考情報として残します。
    fields.append(_embed_field("Entry算出元", f"直近5分高値 × {ENTRY_BREAKOUT_BUFFER:.3f}", inline=False))
    fields.append(_embed_field("当日高値(参考)", _fmt_yen(q.day_high), inline=True))

    # Entry接近率 / Entry上抜け（クロス）:
    # - 実戦で「今エントリー判断しやすいか」を最短で見られるように追加します。
    _ = entry_near_ratio  # 引数は「しきい値」の表示など将来拡張用（現状は entry から計算）
    if entry > 0:
        fields.append(_embed_field("Entry接近率", f"{(float(q.price)/float(entry))*100.0:.2f}%", inline=True))
    else:
        fields.append(_embed_field("Entry接近率", "N/A", inline=True))
    if entry_crossed is None:
        crossed_s = "N/A"
    else:
        crossed_s = "成立" if entry_crossed else "未成立"
    fields.append(_embed_field("Entry上抜け", crossed_s, inline=True))

    # Entry上抜け（クロス）の判定ターゲット:
    # - 今回の仕様では「前ループ時点の entry」をターゲットにします。
    # - そのため、通知で「どのentryを上抜け判定に使ったか」を明示します。
    if cross_target_entry is not None:
        fields.append(_embed_field("cross_target_entry", _fmt_yen(cross_target_entry), inline=True))
    if prev_entry_snapshot is not None:
        fields.append(_embed_field("prev_entry_snapshot", _fmt_yen(prev_entry_snapshot), inline=True))

    # breakout_state（突破済み/未突破）:
    # - 「なぜ🚀が出ないのか？」を見分けやすくするために表示します。
    if breakout_state is None:
        st_s = "N/A"
    else:
        st_s = "突破済み" if breakout_state else "未突破"
    fields.append(_embed_field("breakout_state", st_s, inline=True))

    fields.append(
        _embed_field(
            "売買候補",
            "\n".join(
                [
                    f"Entry: {_fmt_yen(entry)}",
                    f"Stop: {_fmt_yen(stop)}",
                    f"Take: {_fmt_yen(take)}",
                ]
            ),
            inline=False,
        )
    )
    fields.append(
        _embed_field(
            "補足",
            "\n".join([f"VWAP: {_fmt_yen(vwap)}", f"25MA: {_fmt_yen(ma25)}"]),
            inline=False,
        )
    )

    return {
        "title": f"🟢 条件一致: {q.symbol}",
        "color": 0x2ECC71,  # green
        "fields": fields,
    }


def build_embed_entry_cross(
    q: Quote,
    *,
    entry: float,
    stop: float,
    take: float,
    vwap: Optional[float],
    ma25: float,
    replay_time_jst: Optional[str],
    recent_5m_high: Optional[float],
    price_5min_ago: Optional[float],
    vwap_distance_pct: Optional[float],
    vol_increase: Optional[bool],
    entry_crossed: bool,
    cross_target_entry: Optional[float] = None,
    prev_entry_snapshot: Optional[float] = None,
) -> dict[str, Any]:
    """
    🚀 Entry上抜け（クロス）を強調するEmbed。
    - 条件一致Embedと同じ情報を持ちつつ、title と color を変えて目立たせます。
    """
    embed = build_embed_match(
        q,
        entry=entry,
        stop=stop,
        take=take,
        vwap=vwap,
        ma25=ma25,
        replay_time_jst=replay_time_jst,
        recent_5m_high=recent_5m_high,
        price_5min_ago=price_5min_ago,
        vwap_distance_pct=vwap_distance_pct,
        vol_increase=vol_increase,
        entry_near_ratio=float(ENTRY_NEAR_RATIO),
        entry_crossed=bool(entry_crossed),
        cross_target_entry=cross_target_entry,
        prev_entry_snapshot=prev_entry_snapshot,
        breakout_state=True,
    )
    embed["title"] = f"🚀 Entry上抜け: {q.symbol}"
    embed["color"] = 0x3498DB  # blue
    return embed


def build_embed_levels_change(
    *,
    symbol: str,
    price: float,
    change_percent: Optional[float],
    old_entry: float,
    new_entry: float,
    old_stop: float,
    new_stop: float,
    old_take: float,
    new_take: float,
) -> dict[str, Any]:
    """【候補価格変更】Embed"""
    fields: list[dict[str, Any]] = []
    fields.append(_embed_field("現在値", _fmt_yen(price), inline=True))
    fields.append(_embed_field("前日比", _fmt_pct(change_percent), inline=True))
    fields.append(
        _embed_field(
            "候補価格",
            "\n".join(
                [
                    f"Entry: {_fmt_yen(old_entry)} → {_fmt_yen(new_entry)}",
                    f"Stop: {_fmt_yen(old_stop)} → {_fmt_yen(new_stop)}",
                    f"Take: {_fmt_yen(old_take)} → {_fmt_yen(new_take)}",
                ]
            ),
            inline=False,
        )
    )
    return {
        "title": f"🟡 候補価格変更: {symbol}",
        "color": 0xF1C40F,  # yellow
        "fields": fields,
    }


def build_embed_out(
    *,
    symbol: str,
    price: Optional[float],
    change_percent: Optional[float],
    reasons: list[str],
) -> dict[str, Any]:
    """【条件外れ】Embed"""
    reason_text = " / ".join(reasons) if reasons else "条件から外れました"
    fields: list[dict[str, Any]] = []
    fields.append(_embed_field("現在値", _fmt_yen(price), inline=True))
    fields.append(_embed_field("前日比", _fmt_pct(change_percent), inline=True))
    fields.append(_embed_field("外れた理由", reason_text, inline=False))
    return {
        "title": f"🔴 条件外れ: {symbol}",
        "color": 0xE74C3C,  # red
        "fields": fields,
    }


def _build_discord_message(
    q: Quote,
    *,
    entry: float,
    stop: float,
    take: float,
    ma25: float,
    vol_avg5: Optional[float],
    vol_spike_ratio: Optional[float],
    vwap: Optional[float],
    market_cap: Optional[float],
) -> dict[str, Any]:
    # 互換のため関数名は残しますが、通常通知では使わない情報を省略した Embed に置き換えます。
    # - market_cap / 出来高急増倍率の詳細 / 長い説明文 は通常通知から外します（要件）。
    _ = (vol_avg5, vol_spike_ratio, market_cap)  # 引数互換維持（未使用）
    # 通常モードでは Replay時刻は付けません（リプレイ側は build_embed_match を直接呼んで付けます）。
    embed = build_embed_match(q, entry=entry, stop=stop, take=take, vwap=vwap, ma25=ma25, replay_time_jst=None)
    return {"embeds": [embed]}


def _discord_post(webhook_url: str, content: str) -> None:
    """
    requests.post で Discord Webhookへ送信します。
    """
    r = requests.post(webhook_url, json={"content": content}, timeout=20)
    r.raise_for_status()


def _discord_post_webhook_payload(webhook_url: str, payload: dict[str, Any]) -> None:
    """Webhookへ Embed payload を送ります。"""
    r = requests.post(webhook_url, json=payload, timeout=20)
    r.raise_for_status()


def _parse_channel_id(raw: str) -> Optional[int]:
    """
    環境変数で渡される Discord のチャンネルID（文字列）を int に変換します。
    - 未設定（空文字）の場合は None
    - 数字以外が混ざっていた場合も None（= チャンネル送信を使わずフォールバック）
    """
    s = (raw or "").strip()
    if not s:
        return None
    try:
        return int(s)
    except Exception:
        return None


def _discord_send_to_channel(
    *,
    bot_token: str,
    channel_id: int,
    content: str,
) -> None:
    """
    Discord Bot Token を使って、指定チャンネルへメッセージ送信します（WebhookではなくBot送信）。

    重要:
    - これは「Webhookの送信先」ではなく、channel_id で指定したチャンネルに送ります。
    - Bot がそのチャンネルを閲覧/送信できる権限が必要です。
    """
    url = f"https://discord.com/api/v10/channels/{int(channel_id)}/messages"
    headers = {
        "Authorization": f"Bot {bot_token}",
        "Content-Type": "application/json",
    }
    r = requests.post(url, headers=headers, json={"content": content}, timeout=20)
    r.raise_for_status()


def _discord_send_to_channel_payload(
    *,
    bot_token: str,
    channel_id: int,
    payload: dict[str, Any],
) -> None:
    """Bot送信（Embed payload 対応版）。"""
    url = f"https://discord.com/api/v10/channels/{int(channel_id)}/messages"
    headers = {
        "Authorization": f"Bot {bot_token}",
        "Content-Type": "application/json",
    }
    r = requests.post(url, headers=headers, json=payload, timeout=20)
    r.raise_for_status()


def discord_notify(
    payload: dict[str, Any],
    *,
    webhook_url: str,
    alert_channel_id: Optional[int],
    bot_token: str,
) -> None:
    """
    通知を「最終的にどこへ送るか」を1か所にまとめた関数です。

    優先順位（方針）:
    1) ALERT_CHANNEL_ID + DISCORD_BOT_TOKEN があるなら、Bot送信に統一（推奨）
    2) それが無理なら、従来どおり Webhook へ送信（互換）

    運用メモ（初心者向け）:
    - 当面いちばん簡単なのは「通知用チャンネルで作ったWebhook URL」を DISCORD_WEBHOOK_URL に入れる方法です。
      そうすると Webhook でも通知ログを分離できます。
    """
    if alert_channel_id is not None and bot_token.strip():
        _discord_send_to_channel_payload(
            bot_token=bot_token.strip(),
            channel_id=alert_channel_id,
            payload=payload,
        )
        return
    if webhook_url.strip():
        _discord_post_webhook_payload(webhook_url.strip(), payload)
        return
    # どちらも無ければ「通知なし」（discord_enabled 側で制御する想定）


def _get_discord_token_with_compat_warning() -> str:
    """
    DiscordのBotトークンを環境変数から取得します（環境変数名の整理版）。

    仕様:
    - 正: DISCORD_TOKEN
    - 旧: DISCORD_BOT_TOKEN（廃止予定）
      - もし残っていたら警告を表示しつつ、その値で動作を継続します（互換）。
    """
    tok = os.getenv("DISCORD_TOKEN", "").strip()
    old = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    if old:
        print(f"[{now_str()}] 警告: DISCORD_BOT_TOKEN は廃止予定です。DISCORD_TOKEN に移行してください。")
        if not tok:
            tok = old
    return tok


def _level_changed(*, old: float, new: float) -> bool:
    """
    候補価格が「大幅に変わったか」を判定します。

    判定ルール（仕様）:
    - 価格差が 1%以上変わったら通知
    - または 値幅が 10円以上変わったら通知
    """
    diff = abs(float(new) - float(old))
    if diff >= float(LEVEL_CHANGE_YEN):
        return True

    # %判定は old を基準にします（old=0 などの異常値は安全に弾く）
    if float(old) <= 0:
        return False
    pct = (diff / float(old)) * 100.0
    return pct >= float(LEVEL_CHANGE_PCT)


def _build_levels_change_message(
    *,
    symbol: str,
    price: float,
    currency: str,
    change_percent: Optional[float],
    old_entry: float,
    new_entry: float,
    old_stop: float,
    new_stop: float,
    old_take: float,
    new_take: float,
) -> dict[str, Any]:
    _ = currency  # 引数互換維持（Embedは円固定表示）
    embed = build_embed_levels_change(
        symbol=symbol,
        price=price,
        change_percent=change_percent,
        old_entry=old_entry,
        new_entry=new_entry,
        old_stop=old_stop,
        new_stop=new_stop,
        old_take=old_take,
        new_take=new_take,
    )
    return {"embeds": [embed]}


def _build_discord_out_message(symbol: str, *, price: Optional[float], currency: str) -> dict[str, Any]:
    """
    条件外れ通知（Discord用）。
    - 仕様: 条件一致していた銘柄が条件から外れたら通知する
    """
    _ = currency  # 引数互換維持（Embedは円固定表示）
    embed = build_embed_out(symbol=symbol, price=price, change_percent=None, reasons=[])
    return {"embeds": [embed]}


def _load_watch_from_file(path: str) -> list[str]:
    with open(path, "r", encoding="utf-8") as f:
        out: list[str] = []
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            out.append(s)
        return out


def _parse_watch_csv(s: str) -> list[str]:
    # "7203.T, 9984.T" のような入力を想定
    items = [x.strip() for x in s.split(",")]
    return [x for x in items if x]


# =========================
# 監視銘柄の読み込み（段階的拡張）
# =========================
# 優先順位:
# 1) watchlist.json
# 2) symbols.csv（列: symbol,name）
# 3) このファイル上部の WATCH（既存の動作）
WATCHLIST_JSON_PATH = os.path.join(os.path.dirname(__file__), "watchlist.json")
SYMBOLS_CSV_PATH = os.path.join(os.path.dirname(__file__), "symbols.csv")


def _load_watchlist_json(path: str) -> tuple[list[str], Optional[str]]:
    """
    watchlist.json を読み込みます。
    - 想定: JSON 配列 ["7203.T", ...]
    - dict で来る場合も {"symbols": [...]} などを軽く吸収します。
    """
    if not os.path.exists(path):
        return ([], None)
    try:
        raw = json.loads(open(path, "r", encoding="utf-8").read())
        if isinstance(raw, list):
            return ([str(s).strip() for s in raw if str(s).strip()], None)
        if isinstance(raw, dict):
            maybe = raw.get("symbols") or raw.get("watchlist") or []
            if isinstance(maybe, list):
                return ([str(s).strip() for s in maybe if str(s).strip()], None)
    except Exception as e:
        # JSON が壊れている等の理由を返します（呼び出し側でログ出しして前回のリストを維持する）
        return ([], str(e))
    return ([], "watchlist.json の形式が想定外です（JSON配列を期待しています）")


def _load_symbols_csv(path: str) -> list[str]:
    """
    symbols.csv を読み込みます。
    - 期待する列: symbol,name
    - symbol 列だけを使います
    """
    if not os.path.exists(path):
        return []
    try:
        out: list[str] = []
        with open(path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                sym = (row.get("symbol") or row.get("Symbol") or "").strip()
                if sym:
                    out.append(sym)
        # 重複なしにします
        return sorted({s for s in out if s})
    except Exception:
        return []


def _load_morning_screen_symbols() -> list[str]:
    """
    朝スクリーニング用の「対象銘柄」を決めます。

    仕様（ユーザー要件）:
    - symbols.csv があれば symbols.csv の銘柄を対象
    - なければ watchlist.json の銘柄を対象
    - どちらも無ければ 既存WATCH を対象

    初心者向けポイント:
    - 通常監視の優先順位（watchlist.json優先）とは “別仕様” なので、
      朝スクリーニングでは専用の関数を作って分離します（既存挙動を壊さないため）。
    """
    if os.path.exists(SYMBOLS_CSV_PATH):
        syms = _load_symbols_csv(SYMBOLS_CSV_PATH)
        if syms:
            return syms

    if os.path.exists(WATCHLIST_JSON_PATH):
        watch_loaded, err = _load_watchlist_json(WATCHLIST_JSON_PATH)
        if err:
            # 壊れていても朝スクリーニング自体は止めず、WATCHへフォールバックします。
            print(f"[{now_str()}] watchlist.json 読み込みエラー（朝スクリーニングはWATCHへフォールバック）: {err}")
        if watch_loaded:
            return watch_loaded

    return list(WATCH)


def _calc_day_range_pct(*, day_high: Optional[float], day_low: Optional[float], previous_close: Optional[float], price: float) -> Optional[float]:
    """
    当日値幅（%）を計算します。

    仕様（ユーザー要件）:
    - 「当日値幅が 1%以上」なら加点

    初心者向けポイント:
    - 「%」にするには、何かの基準値で割る必要があります。
    - ここでは、前日終値が取れるなら前日終値を基準（より自然）、
      取れないなら現在値を基準にします（安全なフォールバック）。
    """
    if not isinstance(day_high, (int, float)) or not isinstance(day_low, (int, float)):
        return None
    hi = float(day_high)
    lo = float(day_low)
    if hi <= 0 or lo <= 0:
        return None
    if hi < lo:
        # 変なデータが来た時の安全策
        hi, lo = lo, hi

    base: float
    if isinstance(previous_close, (int, float)) and float(previous_close) > 0:
        base = float(previous_close)
    else:
        base = float(price) if float(price) > 0 else 0.0
    if base <= 0:
        return None

    return ((hi - lo) / base) * 100.0


def _morning_screen_score(
    *,
    q: Quote,
    vwap: Optional[float],
    ma25: Optional[float],
    avg_vol5: Optional[float],
    day_range_pct: Optional[float],
) -> tuple[int, list[str], Optional[float]]:
    """
    朝スクリーニング用のスコアリングを行います。

    仕様（ユーザー要件）:
    - 前日比 +1%以上 +5%未満：+2点
    - 前日比 +5%以上 +8%未満：+1点
    - 前日比 +8%以上：-2点
    - 出来高30万株以上：+1点
    - 出来高が5日平均出来高の1.5倍以上：+2点
    - 現在値 > VWAP：+2点
    - 現在値 > 25日移動平均：+1点
    - 当日高値の98%以上：+2点
    - 当日値幅が1%以上：+1点

    戻り値:
    - score: 合計点
    - reasons: 表示用の理由（主に“加点に効いた要素”）
    - vol_spike_ratio: 出来高 / 5日平均出来高（表示用）
    """
    score = 0
    reasons: list[str] = []

    chg = q.change_percent
    if isinstance(chg, (int, float)):
        chg_f = float(chg)
        if 1.0 <= chg_f < 5.0:
            score += 2
            reasons.append("前日比+1-5%")
        elif 5.0 <= chg_f < 8.0:
            score += 1
            reasons.append("前日比+5-8%")
        elif chg_f >= 8.0:
            score -= 2
            reasons.append("急騰(8%+)")  # マイナスも「注意点」として理由に残す

    vol = q.volume
    if isinstance(vol, (int, float)):
        vol_f = float(vol)
        if vol_f >= 300_000.0:
            score += 1
            reasons.append("出来高30万+")

    vol_spike_ratio: Optional[float] = None
    if isinstance(vol, (int, float)) and isinstance(avg_vol5, (int, float)) and float(avg_vol5) > 0:
        vol_spike_ratio = float(vol) / float(avg_vol5)
        if vol_spike_ratio >= 1.5:
            score += 2
            reasons.append("出来高急増")

    if isinstance(vwap, (int, float)) and float(vwap) > 0 and float(q.price) > float(vwap):
        score += 2
        reasons.append("VWAP上")

    if isinstance(ma25, (int, float)) and float(ma25) > 0 and float(q.price) > float(ma25):
        score += 1
        reasons.append("MA25上")

    if isinstance(q.day_high, (int, float)) and float(q.day_high) > 0:
        if float(q.price) >= float(q.day_high) * 0.98:
            score += 2
            reasons.append("高値付近")

    if isinstance(day_range_pct, (int, float)):
        if float(day_range_pct) >= 1.0:
            score += 1
            reasons.append("値幅1%+")

    return score, reasons, vol_spike_ratio


def _format_morning_screen_message(results: list[MorningScreenResult]) -> str:
    """
    Discord/ターミナルに出す「朝スクリーニング結果」テキストを作ります。

    仕様（ユーザー要件）:
    - 上位10銘柄まで表示
    - 指定のフォーマットに近い形で（順位/スコア/現在値/前日比/出来高/VWAP/理由）
    """
    lines: list[str] = []
    lines.append("📊 朝スクリーニング結果")
    lines.append("")

    if not results:
        lines.append("該当なし（除外条件により0件でした）")
        return "\n".join(lines)

    top = results[:10]
    for i, r in enumerate(top, start=1):
        q = r.quote
        reason_text = " / ".join(r.reasons) if r.reasons else "—"
        lines.append(f"{i}位 {r.symbol} スコア: {r.score}")
        lines.append(f"現在値: {_fmt_yen(q.price)}")
        lines.append(f"前日比: {_fmt_pct(q.change_percent)}")
        lines.append(f"出来高: {_fmt_volume_man(q.volume)}")
        lines.append(f"VWAP: {_fmt_yen(r.vwap)}")
        lines.append(f"理由: {reason_text}")
        lines.append("")

    return "\n".join(lines).rstrip()


def run_morning_screen() -> int:
    """
    朝スクリーニングを実行します（通常監視とは完全に別ルート）。

    除外条件（ユーザー要件）:
    - 出来高が10万株未満は除外
    - 前日比がマイナスは除外
    - 価格が取得できない銘柄は除外
    """
    symbols = _load_morning_screen_symbols()
    if not symbols:
        print(f"[{now_str()}] 朝スクリーニング: 対象銘柄が空です。symbols.csv / watchlist.json / WATCH を確認してください。")
        return 2

    webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    alert_channel_id = _parse_channel_id(os.getenv("ALERT_CHANNEL_ID", ""))
    bot_token = _get_discord_token_with_compat_warning()
    discord_enabled = bool((alert_channel_id is not None and bot_token) or webhook_url)

    # 朝は1回実行が多いので、ここで「何を対象にしたか」をターミナルに出します。
    print("=== 朝スクリーニング ===")
    print(f"- 対象銘柄数: {len(symbols)}")
    if os.path.exists(SYMBOLS_CSV_PATH):
        print("- ソース: symbols.csv（最優先）")
    elif os.path.exists(WATCHLIST_JSON_PATH):
        print("- ソース: watchlist.json（symbols.csvが無い場合の次点）")
    else:
        print("- ソース: WATCH（フォールバック）")
    print("")

    results: list[MorningScreenResult] = []

    with requests.Session() as session:
        for sym in symbols:
            try:
                q = fetch_quote(session, sym)
            except Exception as e:
                # 仕様: 価格が取得できない銘柄は除外（落とさず続行）
                print(f"[{now_str()}] {sym} 価格取得失敗（除外）: {e}")
                continue

            # 除外条件: 出来高 < 10万
            if not isinstance(q.volume, (int, float)) or float(q.volume) < 100_000.0:
                continue

            # 除外条件: 前日比がマイナス
            if not isinstance(q.change_percent, (int, float)) or float(q.change_percent) < 0.0:
                continue

            # 指標取得（可能な限り）:
            # - 失敗しても「その指標はN/A」でスコア計算を続行します。
            vwap: Optional[float]
            ma25: Optional[float]
            avg5: Optional[float]
            try:
                vwap = fetch_vwap(session, sym)
            except Exception:
                vwap = None
            try:
                ma25 = fetch_ma25(session, sym)
            except Exception:
                ma25 = None
            try:
                avg5 = fetch_avg_volume_5(session, sym)
            except Exception:
                avg5 = None

            day_range_pct = _calc_day_range_pct(
                day_high=q.day_high,
                day_low=q.day_low,
                previous_close=q.previous_close,
                price=float(q.price),
            )

            score, reasons, vol_spike_ratio = _morning_screen_score(
                q=q,
                vwap=vwap,
                ma25=ma25,
                avg_vol5=avg5,
                day_range_pct=day_range_pct,
            )

            results.append(
                MorningScreenResult(
                    symbol=sym,
                    score=int(score),
                    quote=q,
                    vwap=vwap,
                    ma25=ma25,
                    avg_vol5=avg5,
                    vol_spike_ratio=vol_spike_ratio,
                    day_range_pct=day_range_pct,
                    reasons=reasons,
                )
            )

    # 並び順: スコア降順 → 前日比降順 → 出来高降順（同点のときの見やすさ用）
    def _sort_key(r: MorningScreenResult) -> tuple:
        chg = float(r.quote.change_percent) if isinstance(r.quote.change_percent, (int, float)) else 0.0
        vol = float(r.quote.volume) if isinstance(r.quote.volume, (int, float)) else 0.0
        return (int(r.score), chg, vol)

    results_sorted = sorted(results, key=_sort_key, reverse=True)

    msg = _format_morning_screen_message(results_sorted)
    print(msg)

    if discord_enabled:
        try:
            discord_notify(
                {"content": msg},
                webhook_url=webhook_url,
                alert_channel_id=alert_channel_id,
                bot_token=bot_token,
            )
        except Exception as e:
            print(f"[{now_str()}] Discord送信失敗（朝スクリーニング）: {e}")

    return 0


def run_replay(
    *,
    interval_sec: float,
    only_changes: bool,
    fixed_watch: Optional[list[str]],
    replay_range: str,
    replay_morning_screen_hhmm: str = "",
    one_trade_per_symbol_per_day: bool = False,
) -> int:
    """
    過去データの仮想リプレイ（テストモード）。

    仕様（ユーザー要件）:
    - `python yahoo_kabu_watch.py --replay` の時だけ有効（TEST_REPLAY_MODE=True）
    - Yahoo Finance から過去の1分足データを取得（range: 1d/5d）
    - 対象銘柄は watchlist.json（通常と同じ監視銘柄決定ルール）
    - 1分足データを 1 秒ごとに仮想的に再生する
    - 各時点の price/high/low/volume を使って通常の判定ロジックを動かす
    - 条件一致通知 / 条件外れ通知 / 候補価格変更通知 を Discord に送る
    """
    if replay_range not in ("1d", "5d", "10d", "20d", "60d"):
        print("--replay-range は 1d/5d/10d/20d/60d を指定してください。")
        return 2

    webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    alert_channel_id = _parse_channel_id(os.getenv("ALERT_CHANNEL_ID", ""))
    # Bot送信用トークンは DISCORD_TOKEN に統一します（旧: DISCORD_BOT_TOKEN は互換で吸収）
    bot_token = _get_discord_token_with_compat_warning()
    discord_enabled = bool((alert_channel_id is not None and bot_token) or webhook_url)

    if webhook_url:
        print(f"[{now_str()}] Discord通知: DISCORD_WEBHOOK_URL が設定されています（Webhookの送信先チャンネルに送られます）")
    if alert_channel_id is not None and bot_token:
        print(f"[{now_str()}] Discord通知: ALERT_CHANNEL_ID={alert_channel_id} へ Bot送信します（推奨）")
    last_discord_candidate_symbols: set[str] = set()
    # 条件外れ通知で「最後に見た価格」を出すために覚えておきます。
    last_quote_by_symbol: dict[str, Quote] = {}
    last_notified_levels: dict[str, tuple[float, float, float]] = {}
    # 条件外れ通知の安定化（連続不一致をカウント）
    exit_miss_count: dict[str, int] = {}
    # Entry突破状態（最終仕様）:
    # - False: まだentry未突破
    # - True:  entry突破済み（突破中は再通知しない）
    breakout_state_by_symbol: dict[str, bool] = {}
    # breakout_state を True にした時点の entry（基準）を覚えます。
    last_breakout_entry_by_symbol: dict[str, Optional[float]] = {}
    # Entry上抜け（クロス）判定用に「前回価格」を覚えておきます。
    prev_price_by_symbol: dict[str, Optional[float]] = {}
    # （注）以前の型の残骸があるとバグるので、prev_price_by_symbol は上の Optional 版だけに統一します。

    # =========================
    # Replay期待値検証（ターミナル表示のみ）
    # =========================
    # - 🚀 Entry上抜けが出た瞬間を signal として記録
    # - signal後の価格推移で take/stop 到達や最大利益/最大逆行を集計
    replay_signals: list[ReplaySignalEval] = []
    # symbol -> active signal indices（同一銘柄で複数signalが出た場合も追えるようにする）
    active_signal_indices_by_symbol: dict[str, list[int]] = {}

    # 表示抑制用
    last_candidates: set[str] = set()

    # 通常モード同様、MA25/出来高5日平均はキャッシュします（毎秒取り直すと重い）
    ma25_cache: dict[str, tuple[float, float]] = {}
    avg5_cache: dict[str, tuple[float, float]] = {}

    # リプレイで「当日高値」「当日出来高（累積）」「VWAP（概算）」を作るための状態
    running_day_high: dict[str, float] = {}
    running_day_volume: dict[str, float] = {}
    running_vwap_pv: dict[str, float] = {}  # (typical price * volume) 累積
    running_vwap_v: dict[str, float] = {}   # volume 累積

    # 日ごとの previous close を切り替えるための状態（簡易実装）
    current_day_key: dict[str, str] = {}
    prev_close_by_day: dict[str, Optional[float]] = {}

    # watchlist.json の読み込み失敗時の保険（通常と同様）
    last_watch: list[str] = []

    # =========================
    # Replay Morning Screen（追加仕様）
    # =========================
    # 初心者向けポイント:
    # - 「未来データ」を使うと検証にならないので、指定時刻までの1分足だけでスクリーニングします。
    # - 指定時刻になったら、その日だけ監視対象にTOP10を追加して、以降のsignal/期待値を検証します。

    def _parse_hhmm(hhmm: str) -> Optional[tuple[int, int]]:
        s = (hhmm or "").strip()
        if not s:
            return None
        if ":" not in s:
            return None
        a, b = s.split(":", 1)
        try:
            hh = int(a)
            mm = int(b)
        except Exception:
            return None
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            return None
        return (hh, mm)

    ms_time = _parse_hhmm(replay_morning_screen_hhmm)
    if replay_morning_screen_hhmm.strip() and ms_time is None:
        print("--replay-morning-screen は HH:MM 形式で指定してください（例: 09:07）")
        return 2

    # スクリーニング対象のユニバース（仕様: symbols.csv > watchlist.json > WATCH）
    ms_universe: list[str] = _load_morning_screen_symbols() if ms_time is not None else []

    # 1分足は「必要になった銘柄だけ」取ってキャッシュします（全部取ると重いので）
    ms_bars_cache: dict[str, list[ReplayBar]] = {}
    ms_meta_cache: dict[str, dict] = {}

    # 日別のMorning Screen結果（Replay終了時にまとめて表示するため）
    # key は JST日付 "YYYY-MM-DD"
    ms_daily: dict[str, dict[str, Any]] = {}

    # 日付が変わったときのリセット用（JST基準）
    ms_current_day_jst: Optional[str] = None

    # =========================
    # 前日継続監視（追加仕様）
    # =========================
    # 初心者向けポイント:
    # - 前日に強かった銘柄が「翌日も続伸」するかを検証したい場合、
    #   前日のReplay結果から“継続候補”を作り、翌日の監視開始時点で追加します。
    #
    # - 「継続候補」は Morning Screen TOP10 とは別枠で管理し、
    #   Summary出力でも「当日選出」と「前日継続」を分けて見られるようにします。
    ms_carryover_by_day: dict[str, list[str]] = {}  # day_jst -> carryover symbols

    def _pnl_yen_100_shares_replay(s: ReplaySignalEval) -> float:
        """
        100株あたり損益（円）を計算します（Replay共通）。

        NOTE:
        - Morning Screen Replay Summary と同じルールに揃えます。
        """
        if isinstance(s.final_profit_pct, (int, float)):
            return float(s.signal_price) * 100.0 * (float(s.final_profit_pct) / 100.0)
        if s.result == "WIN":
            return (float(s.take_price) - float(s.entry_price)) * 100.0
        if s.result == "LOSE":
            return (float(s.stop_price) - float(s.entry_price)) * 100.0
        return 0.0

    def _day_jst_str(dt_utc: datetime) -> str:
        """UTC datetime を JST日付（YYYY-MM-DD）に変換します。"""
        t = dt_utc
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return t.astimezone(JST).strftime("%Y-%m-%d")

    # 同一銘柄の重複エントリー制限（追加仕様）:
    # - key: JST日付（YYYY-MM-DD）
    # - value: その日に「採用済み」の銘柄セット
    accepted_entry_symbols_by_day: dict[str, set[str]] = {}

    # 追加ポジション（買い増し）用の状態:
    # - key: (JST日付, symbol)
    # - value: 追加回数（0,1,2）
    add_count_by_day_symbol: dict[tuple[str, str], int] = {}
    # - key: (JST日付, symbol)
    # - value: 直近のEntry価格（BASE/ADD含む“最後に建てたポジション”の価格）
    last_entry_price_by_day_symbol: dict[tuple[str, str], float] = {}
    # - value: 前回ADDの時刻（UTC datetime）。最低5分間隔を作るために使います。
    last_add_time_by_day_symbol: dict[tuple[str, str], datetime] = {}
    # - value: 出来高増加が「継続」しているかを見るための前回値
    prev_vol_inc_by_day_symbol: dict[tuple[str, str], bool] = {}

    with requests.Session() as session:
        # -----------------------------
        # 監視銘柄の決定（通常と同じ）
        # -----------------------------
        watch: list[str] = []
        if fixed_watch is not None:
            watch = list(fixed_watch)
        else:
            if os.path.exists(WATCHLIST_JSON_PATH):
                watch_loaded, err = _load_watchlist_json(WATCHLIST_JSON_PATH)
                if err:
                    print(f"[{now_str()}] watchlist.json 読み込みエラー（前回の監視リストを維持）: {err}")
                    watch = list(last_watch)
                else:
                    watch = watch_loaded
                    last_watch = list(watch)
            else:
                loaded_symbols = _load_symbols_csv(SYMBOLS_CSV_PATH)
                watch = loaded_symbols if loaded_symbols else list(WATCH)

        if not watch:
            print(f"[{now_str()}] 監視銘柄なし。watchlist.json を用意してください。")
            return 2

        print("=== TEST REPLAY MODE ===")
        print(f"- replay_range: {replay_range}")
        # リプレイ速度の見せ方を「直感的」にします。
        # interval_sec=1.0 なら「1秒 = 1分」
        # interval_sec=0.5 なら「0.5秒 = 1分」など。
        if abs(interval_sec - 1.0) < 1e-9:
            speed_s = "1秒 = 1分"
        else:
            speed_s = f"{interval_sec:.2f}秒 = 1分"
        print(f"- replay_speed: {speed_s}")
        print(f"- watch: {', '.join(watch)}\n")

        # -----------------------------
        # 過去1分足の取得（最初にまとめて取る）
        # -----------------------------
        bars_by_symbol: dict[str, list[ReplayBar]] = {}
        meta_by_symbol: dict[str, dict] = {}
        for sym in watch:
            try:
                bars, meta = fetch_history_1m(session, sym, range_str=replay_range)
                if not bars:
                    print(f"[{now_str()}] {sym} リプレイ用の1分足が空でした（スキップ）")
                    continue
                bars_by_symbol[sym] = bars
                meta_by_symbol[sym] = meta
            except Exception as e:
                print(f"[{now_str()}] {sym} 過去1分足の取得に失敗（スキップ）: {e}")

        if not bars_by_symbol:
            print(f"[{now_str()}] リプレイ対象のデータが1つも取得できませんでした。")
            return 2

        # -----------------------------
        # リプレイ対象の「開始/終了時刻」を表示（JST）
        # -----------------------------
        all_bars = [b for bars in bars_by_symbol.values() for b in bars]
        start_utc = min((b.timestamp_utc for b in all_bars), default=None)
        end_utc = max((b.timestamp_utc for b in all_bars), default=None)
        print("=== Replay対象 ===")
        print(f"- 対象日(目安): {_fmt_dt_jst(start_utc)[:10]} ～ {_fmt_dt_jst(end_utc)[:10]}")
        print(f"- 開始時刻(JST): {_fmt_dt_jst(start_utc)}")
        print(f"- 終了時刻(JST): {_fmt_dt_jst(end_utc)}")
        print(f"- 対象銘柄: {', '.join(sorted(bars_by_symbol.keys()))}\n")

        # 銘柄ごとの再生ポインタ
        idx_by_symbol: dict[str, int] = {sym: 0 for sym in bars_by_symbol.keys()}

        # base_watch: 元々のReplay監視銘柄（毎日これがベース）
        # active_watch: 当日の監視銘柄（Morning Screenで追加される可能性あり）
        base_watch: set[str] = set(bars_by_symbol.keys())
        active_watch: set[str] = set(base_watch)

        # 進行率用:
        # - 全銘柄・全バーの総数に対して、何本再生したかで%を出します。
        total_bars = sum(len(bars) for bars in bars_by_symbol.values())
        progressed_bars = 0

        # 初回 previousClose（取れれば使う）
        for sym, meta in meta_by_symbol.items():
            pc = meta.get("previousClose")
            prev_close_by_day[f"{sym}::INIT"] = float(pc) if isinstance(pc, (int, float)) else None

        try:
            while True:
                loop_started = time.perf_counter()

                # -----------------------------
                # 1秒ぶん進める（各銘柄の1分足を1本進める）
                # -----------------------------
                quotes: list[Quote] = []
                # 重要: “当日の監視対象”だけ進めます（Morning Screenで追加された銘柄もここに入ります）
                for sym in sorted(active_watch):
                    bars = bars_by_symbol.get(sym) or []
                    i = idx_by_symbol.get(sym, 0)
                    if i >= len(bars):
                        continue
                    bar = bars[i]
                    idx_by_symbol[sym] = i + 1
                    progressed_bars += 1

                    # 日付キー（UTC日付で管理する簡易版）
                    day_key = bar.timestamp_utc.strftime("%Y-%m-%d")
                    if current_day_key.get(sym) != day_key:
                        # 日が変わったら日内状態をリセット
                        current_day_key[sym] = day_key
                        running_day_high[sym] = float("-inf")
                        running_day_volume[sym] = 0.0
                        running_vwap_pv[sym] = 0.0
                        running_vwap_v[sym] = 0.0

                        # 前日終値（previous close）を切り替え
                        if i == 0:
                            prev_close_by_day[f"{sym}::{day_key}"] = prev_close_by_day.get(f"{sym}::INIT")
                        else:
                            # 直前バーの close を “前日終値” として扱う（簡易）
                            prev_close_by_day[f"{sym}::{day_key}"] = float(bars[i - 1].close)

                    running_day_high[sym] = max(running_day_high.get(sym, float("-inf")), float(bar.high))
                    running_day_volume[sym] = float(running_day_volume.get(sym, 0.0)) + float(bar.volume)

                    # VWAP（概算）: typical price * volume の累積
                    tp = (float(bar.high) + float(bar.low) + float(bar.close)) / 3.0
                    running_vwap_pv[sym] = float(running_vwap_pv.get(sym, 0.0)) + tp * float(bar.volume)
                    running_vwap_v[sym] = float(running_vwap_v.get(sym, 0.0)) + float(bar.volume)

                    meta = meta_by_symbol.get(sym) or {}
                    currency = str(meta.get("currency") or "JPY")
                    prev_close = prev_close_by_day.get(f"{sym}::{day_key}")
                    chg = _calc_change_percent(price=float(bar.close), previous_close=prev_close)

                    # Entry上抜け（クロス）判定用に「前回価格」を保持します（Replayでも同じ判定にする）。
                    prev_price_by_symbol.setdefault(sym, None)

                    quotes.append(
                        Quote(
                            symbol=sym,
                            price=float(bar.close),
                            currency=currency,
                            previous_close=float(prev_close) if isinstance(prev_close, (int, float)) else None,
                            change_percent=float(chg) if isinstance(chg, (int, float)) else None,
                            day_high=float(running_day_high[sym]),
                            # Replayは最小限の再現でOKなので day_low は概算（running min）ではなく None にします。
                            # 朝スクリーニングで必要な値ですが、Replay要件には入っていません。
                            day_low=None,
                            volume=float(running_day_volume[sym]),  # 日内累積出来高として扱う
                            market_time_utc=bar.timestamp_utc,
                            market_cap=None,
                        )
                    )

                    # 今回の価格を保存（次ループで prev として使う）
                    prev_price_by_symbol[sym] = float(bar.close)

                # 全銘柄が再生し終えたら終了
                if not quotes:
                    # Replay終了時に「期待値検証結果」をまとめて表示します。
                    # （Discordは不要、ターミナルだけでOK）
                    print("\n=== Replay期待値検証（signals summary） ===")
                    if not replay_signals:
                        print("- signal は0件でした（🚀 Entry上抜けが発生していません）")
                    else:
                        for s in replay_signals:
                            t_jst = _fmt_dt_jst_short(s.signal_time_utc)
                            pt_t = _fmt_dt_jst_short(s.partial_take_time_utc) if s.partial_take_time_utc else "N/A"
                            te_p = _fmt_yen(s.trailing_exit_price) if s.trailing_exit_price is not None else "N/A"
                            te_r = s.trailing_exit_reason or "N/A"
                            # final_profit_pct が未確定（HOLDで終了）なら、最後に見た価格で暫定計算します。
                            if s.final_profit_pct is None and s.entry_price > 0:
                                fp = ((float(s.last_price_after) - float(s.entry_price)) / float(s.entry_price)) * 100.0
                            else:
                                fp = float(s.final_profit_pct or 0.0)
                            excluded_s = "EXCLUDED" if bool(getattr(s, "excluded_from_eval", False)) else "OK"
                            print(
                                f"- {s.symbol} | "
                                f"pos={getattr(s, 'position_kind', 'BASE')} | "
                                f"signal_time_jst={t_jst} | "
                                f"signal_price={_fmt_yen(s.signal_price)} | "
                                f"entry_price={_fmt_yen(s.entry_price)} | "
                                f"max_price_after={_fmt_yen(s.max_price_after)} | "
                                f"min_price_after={_fmt_yen(s.min_price_after)} | "
                                f"max_profit_pct={s.max_profit_pct():.2f}% | "
                                f"max_drawdown_pct={s.max_drawdown_pct():.2f}% | "
                                f"partial_take_hit={'Y' if s.partial_take_hit else 'N'} | "
                                f"partial_take_time={pt_t} | "
                                f"trailing_exit_price={te_p} | "
                                f"trailing_exit_reason={te_r} | "
                                f"final_profit_pct={fp:.2f}% | "
                                f"take_hit={'Y' if s.take_hit else 'N'} | "
                                f"stop_hit={'Y' if s.stop_hit else 'N'} | "
                                f"result={s.result} | "
                                f"eval={excluded_s}"
                            )
                    print()

                    # =========================
                    # 期待値検証サマリー（追加仕様）
                    # =========================
                    # 初心者向けポイント:
                    # - 「各signalの詳細」だけだと全体の傾向が掴みにくいので、
                    #   最後に集計（勝率・平均値・損益）をまとめて見られるようにします。

                    def _safe_avg(xs: list[float]) -> float:
                        """空リストでも落ちない平均（空なら0）。"""
                        return (sum(xs) / len(xs)) if xs else 0.0

                    def _signal_time_bucket_jst(t_utc: datetime) -> str:
                        """
                        時間帯分類（JST）:
                        - 前場寄り直後: 09:00〜09:30
                        - 前場:       09:30〜11:30
                        - 後場前半:   12:30〜14:00
                        - 後場後半:   14:00〜15:30
                        """
                        t = t_utc
                        if t.tzinfo is None:
                            t = t.replace(tzinfo=timezone.utc)
                        jst = t.astimezone(JST)
                        hm = jst.hour * 60 + jst.minute
                        if (9 * 60) <= hm < (9 * 60 + 30):
                            return "前場寄り直後(09:00-09:30)"
                        if (9 * 60 + 30) <= hm < (11 * 60 + 30):
                            return "前場(09:30-11:30)"
                        if (12 * 60 + 30) <= hm < (14 * 60):
                            return "後場前半(12:30-14:00)"
                        if (14 * 60) <= hm < (15 * 60 + 30):
                            return "後場後半(14:00-15:30)"
                        return "時間外"

                    def _profit_pct_for_summary(s: ReplaySignalEval) -> float:
                        """
                        サマリー用の“最終損益%”を返します。

                        仕様（ユーザー要件）:
                        - final_profit_pct があればそれを採用
                        - 無い場合:
                          - WIN: take_price到達時の利益（entry基準）
                          - LOSE: stop_price到達時の損失（entry基準）
                          - HOLD: 取れなければ 0%
                        """
                        if isinstance(s.final_profit_pct, (int, float)):
                            return float(s.final_profit_pct)
                        if s.entry_price <= 0:
                            return 0.0
                        if s.result == "WIN":
                            return ((float(s.take_price) - float(s.entry_price)) / float(s.entry_price)) * 100.0
                        if s.result == "LOSE":
                            return ((float(s.stop_price) - float(s.entry_price)) / float(s.entry_price)) * 100.0
                        return 0.0

                    def _pnl_yen_100_shares(s: ReplaySignalEval) -> float:
                        """
                        100株あたり損益（円）を計算します。

                        仕様（ユーザー要件）:
                        - final_profit_pct がある場合:
                          pnl_yen = signal_price * 100 * final_profit_pct / 100
                        - final_profit_pct がない場合:
                          - WIN: take_price到達時の利益（円）
                          - LOSE: stop_price到達時の損失（円）
                          - HOLD: 取れなければ 0円
                        """
                        if isinstance(s.final_profit_pct, (int, float)):
                            return float(s.signal_price) * 100.0 * (float(s.final_profit_pct) / 100.0)
                        if s.result == "WIN":
                            return (float(s.take_price) - float(s.entry_price)) * 100.0
                        if s.result == "LOSE":
                            return (float(s.stop_price) - float(s.entry_price)) * 100.0
                        return 0.0

                    # 集計対象（重複エントリー制限などで除外されたsignalは除く）
                    eval_signals = [s for s in replay_signals if not bool(getattr(s, "excluded_from_eval", False))]
                    excluded_n = len(replay_signals) - len(eval_signals)

                    total = len(eval_signals)
                    win = sum(1 for s in eval_signals if s.result == "WIN")
                    lose = sum(1 for s in eval_signals if s.result == "LOSE")
                    hold = sum(1 for s in eval_signals if s.result == "HOLD")
                    win_rate = (win / total * 100.0) if total > 0 else 0.0

                    # 利益率/損失率の平均（WIN/LOSEに分けて集計）
                    profit_pcts = [_profit_pct_for_summary(s) for s in eval_signals if s.result == "WIN"]
                    loss_pcts = [_profit_pct_for_summary(s) for s in eval_signals if s.result == "LOSE"]
                    avg_profit_pct = _safe_avg(profit_pcts)
                    avg_loss_pct = _safe_avg(loss_pcts)

                    # 最大利益率 / 最大下落率の平均（全signal対象）
                    max_profit_pcts = [float(s.max_profit_pct()) for s in eval_signals]
                    max_drawdown_pcts = [float(s.max_drawdown_pct()) for s in eval_signals]
                    avg_max_profit_pct = _safe_avg(max_profit_pcts)
                    avg_max_drawdown_pct = _safe_avg(max_drawdown_pcts)

                    pnls = [_pnl_yen_100_shares(s) for s in eval_signals]
                    total_pnl_yen = sum(pnls)
                    avg_pnl_yen = (total_pnl_yen / total) if total > 0 else 0.0

                    print("【期待値検証サマリー】")
                    print(f"総signal数: {total}")
                    if excluded_n > 0:
                        print(f"除外signal数: {excluded_n}（例: 同一銘柄1日1回モードなど）")
                    print(f"WIN数: {win}")
                    print(f"LOSE数: {lose}")
                    print(f"HOLD数: {hold}")
                    print(f"勝率: {win_rate:.1f}%")
                    print(f"平均利益率: {avg_profit_pct:.2f}%")
                    print(f"平均損失率: {avg_loss_pct:.2f}%")
                    print(f"平均最大利益率: {avg_max_profit_pct:.2f}%")
                    print(f"平均最大下落率: {avg_max_drawdown_pct:.2f}%")
                    print(f"100株あたり合計損益: {total_pnl_yen:+,.0f}円")
                    print(f"100株あたり平均損益: {avg_pnl_yen:+,.0f}円")
                    print()

                    # =========================
                    # 銘柄別サマリー（追加仕様）
                    # =========================
                    by_symbol: dict[str, list[ReplaySignalEval]] = {}
                    for s in eval_signals:
                        by_symbol.setdefault(s.symbol, []).append(s)

                    print("【銘柄別サマリー】")
                    for sym in sorted(by_symbol.keys()):
                        xs = by_symbol[sym]
                        t = len(xs)
                        w = sum(1 for s in xs if s.result == "WIN")
                        l = sum(1 for s in xs if s.result == "LOSE")
                        h = sum(1 for s in xs if s.result == "HOLD")
                        wr = (w / t * 100.0) if t > 0 else 0.0
                        pnl = sum(_pnl_yen_100_shares(s) for s in xs)
                        print(sym)
                        print(f"signal数: {t}")
                        print(f"WIN: {w}")
                        print(f"LOSE: {l}")
                        print(f"HOLD: {h}")
                        print(f"勝率: {wr:.1f}%")
                        print(f"100株損益: {pnl:+,.0f}円")
                        print()

                    # =========================
                    # 時間帯別サマリー（追加仕様）
                    # =========================
                    by_bucket: dict[str, list[ReplaySignalEval]] = {}
                    for s in eval_signals:
                        by_bucket.setdefault(_signal_time_bucket_jst(s.signal_time_utc), []).append(s)

                    # 表示順を固定（時間外は最後）
                    bucket_order = [
                        "前場寄り直後(09:00-09:30)",
                        "前場(09:30-11:30)",
                        "後場前半(12:30-14:00)",
                        "後場後半(14:00-15:30)",
                        "時間外",
                    ]

                    print("【時間帯別サマリー】")
                    for b in bucket_order:
                        xs = by_bucket.get(b) or []
                        if not xs:
                            continue
                        t = len(xs)
                        w = sum(1 for s in xs if s.result == "WIN")
                        l = sum(1 for s in xs if s.result == "LOSE")
                        wr = (w / t * 100.0) if t > 0 else 0.0
                        pnl = sum(_pnl_yen_100_shares(s) for s in xs)
                        print(b)
                        print(f"signal数: {t}")
                        print(f"WIN: {w}")
                        print(f"LOSE: {l}")
                        print(f"勝率: {wr:.1f}%")
                        print(f"100株損益: {pnl:+,.0f}円")
                        print()

                    # =========================
                    # Morning Screen Replay Summary（追加仕様）
                    # =========================
                    # ターミナル表示のみでOK（Discordは不要）
                    if ms_time is not None:
                        print("【Morning Screen Replay Summary】")
                        if not ms_daily:
                            print("- Morning Screen は1度も実行されませんでした（指定時刻が再生範囲に無い等）")
                            print()
                        else:
                            def _pnl_yen_100_shares_ms(s: ReplaySignalEval) -> float:
                                if isinstance(s.final_profit_pct, (int, float)):
                                    return float(s.signal_price) * 100.0 * (float(s.final_profit_pct) / 100.0)
                                if s.result == "WIN":
                                    return (float(s.take_price) - float(s.entry_price)) * 100.0
                                if s.result == "LOSE":
                                    return (float(s.stop_price) - float(s.entry_price)) * 100.0
                                return 0.0

                            by_day_stats: dict[str, dict[str, Any]] = {
                                day: {
                                    "selected": {"signals": 0, "win": 0, "lose": 0, "hold": 0, "pnl": 0.0},
                                    "carryover": {"signals": 0, "win": 0, "lose": 0, "hold": 0, "pnl": 0.0},
                                }
                                for day in ms_daily.keys()
                            }

                            by_symbol_ms: dict[str, dict[str, Any]] = {}
                            for day, info in ms_daily.items():
                                for sym in (info.get("symbols") or []):
                                    agg = by_symbol_ms.setdefault(
                                        sym,
                                        {
                                            "picked": 0,
                                            "signals": 0,
                                            "win": 0,
                                            "lose": 0,
                                            "hold": 0,
                                            "pnl": 0.0,
                                            # 継続監視（追加仕様）
                                            "carry_picked": 0,
                                            "carry_signals": 0,
                                            "carry_win": 0,
                                            "carry_lose": 0,
                                            "carry_hold": 0,
                                            "carry_pnl": 0.0,
                                        },
                                    )
                                    agg["picked"] += 1
                                for sym in (info.get("carryover_symbols") or []):
                                    agg = by_symbol_ms.setdefault(
                                        sym,
                                        {
                                            "picked": 0,
                                            "signals": 0,
                                            "win": 0,
                                            "lose": 0,
                                            "hold": 0,
                                            "pnl": 0.0,
                                            "carry_picked": 0,
                                            "carry_signals": 0,
                                            "carry_win": 0,
                                            "carry_lose": 0,
                                            "carry_hold": 0,
                                            "carry_pnl": 0.0,
                                        },
                                    )
                                    agg["carry_picked"] += 1

                            # Morning Screen Summary も「期待値検証の集計対象」に合わせます
                            # （= 同一銘柄1日1回モード等で excluded_from_eval な signal は除外）
                            ms_eval_signals = [s for s in replay_signals if not bool(getattr(s, "excluded_from_eval", False))]
                            for s in ms_eval_signals:
                                t = s.signal_time_utc
                                if t.tzinfo is None:
                                    t = t.replace(tzinfo=timezone.utc)
                                day_jst = t.astimezone(JST).strftime("%Y-%m-%d")
                                info = ms_daily.get(day_jst)
                                if not info:
                                    continue
                                selected_set = set(info.get("symbols") or [])
                                carry_set = set(info.get("carryover_symbols") or [])

                                # 分類ルール（初心者向けにシンプルに）:
                                # - carryover に入っている銘柄は carryover 側へ寄せる（重複カウント防止）
                                # - selected は “screen_time以降” のsignalだけを対象にする
                                bucket: Optional[str] = None
                                if s.symbol in carry_set:
                                    bucket = "carryover"
                                elif s.symbol in selected_set:
                                    st_utc = info.get("screen_time_utc")
                                    if isinstance(st_utc, datetime):
                                        st2 = st_utc
                                        if st2.tzinfo is None:
                                            st2 = st2.replace(tzinfo=timezone.utc)
                                        if t < st2:
                                            continue
                                    bucket = "selected"
                                else:
                                    continue

                                dd = by_day_stats.get(day_jst)
                                if dd is not None and bucket is not None:
                                    ddb = dd.get(bucket) or {}
                                    ddb["signals"] = int(ddb.get("signals") or 0) + 1
                                    ddb["pnl"] = float(ddb.get("pnl") or 0.0) + float(_pnl_yen_100_shares_ms(s))
                                    if s.result == "WIN":
                                        ddb["win"] = int(ddb.get("win") or 0) + 1
                                    elif s.result == "LOSE":
                                        ddb["lose"] = int(ddb.get("lose") or 0) + 1
                                    else:
                                        ddb["hold"] = int(ddb.get("hold") or 0) + 1
                                    dd[bucket] = ddb

                                agg = by_symbol_ms.setdefault(
                                    s.symbol,
                                    {
                                        "picked": 0,
                                        "signals": 0,
                                        "win": 0,
                                        "lose": 0,
                                        "hold": 0,
                                        "pnl": 0.0,
                                        "carry_picked": 0,
                                        "carry_signals": 0,
                                        "carry_win": 0,
                                        "carry_lose": 0,
                                        "carry_hold": 0,
                                        "carry_pnl": 0.0,
                                    },
                                )
                                if bucket == "carryover":
                                    agg["carry_signals"] += 1
                                    agg["carry_pnl"] += float(_pnl_yen_100_shares_ms(s))
                                    if s.result == "WIN":
                                        agg["carry_win"] += 1
                                    elif s.result == "LOSE":
                                        agg["carry_lose"] += 1
                                    else:
                                        agg["carry_hold"] += 1
                                else:
                                    agg["signals"] += 1
                                    agg["pnl"] += float(_pnl_yen_100_shares_ms(s))
                                    if s.result == "WIN":
                                        agg["win"] += 1
                                    elif s.result == "LOSE":
                                        agg["lose"] += 1
                                    else:
                                        agg["hold"] += 1

                            for day in sorted(ms_daily.keys()):
                                info = ms_daily[day]
                                hhmm = str(info.get("hhmm") or "")
                                syms = list(info.get("symbols") or [])
                                carry_syms = list(info.get("carryover_symbols") or [])
                                scores = dict(info.get("scores") or {})
                                stt = by_day_stats.get(day) or {}
                                sel = stt.get("selected") or {}
                                car = stt.get("carryover") or {}
                                picked = ", ".join([f"{s}({int(scores.get(s, 0))})" for s in syms]) if syms else "(none)"
                                carry_picked = ", ".join(carry_syms) if carry_syms else "(none)"
                                print(f"- 日付: {day}")
                                print(f"  指定時刻: {hhmm} JST")
                                print(f"  当日選出(TOP10): {picked}")
                                print(f"  前日継続: {carry_picked}")
                                print(
                                    "  [当日選出] "
                                    f"signal数: {int(sel.get('signals') or 0)}  "
                                    f"WIN/LOSE/HOLD: {int(sel.get('win') or 0)}/{int(sel.get('lose') or 0)}/{int(sel.get('hold') or 0)}  "
                                    f"100株損益: {float(sel.get('pnl') or 0.0):+,.0f}円"
                                )
                                print(
                                    "  [前日継続] "
                                    f"signal数: {int(car.get('signals') or 0)}  "
                                    f"WIN/LOSE/HOLD: {int(car.get('win') or 0)}/{int(car.get('lose') or 0)}/{int(car.get('hold') or 0)}  "
                                    f"100株損益: {float(car.get('pnl') or 0.0):+,.0f}円"
                                )
                                print()

                            print("【Morning Screen 銘柄別サマリー】")
                            for sym in sorted(by_symbol_ms.keys()):
                                a = by_symbol_ms[sym]
                                picked_n = int(a.get("picked") or 0)
                                sig_n = int(a.get("signals") or 0)
                                w = int(a.get("win") or 0)
                                pnl = float(a.get("pnl") or 0.0)
                                wr = (w / sig_n * 100.0) if sig_n > 0 else 0.0
                                carry_picked_n = int(a.get("carry_picked") or 0)
                                carry_sig_n = int(a.get("carry_signals") or 0)
                                carry_w = int(a.get("carry_win") or 0)
                                carry_pnl = float(a.get("carry_pnl") or 0.0)
                                carry_wr = (carry_w / carry_sig_n * 100.0) if carry_sig_n > 0 else 0.0
                                print(sym)
                                print(f"選出回数: {picked_n}")
                                print(f"signal数: {sig_n}")
                                print(f"勝率: {wr:.1f}%")
                                print(f"100株損益: {pnl:+,.0f}円")
                                print(f"継続監視回数: {carry_picked_n}")
                                print(f"継続監視時signal数: {carry_sig_n}")
                                print(f"継続監視時勝率: {carry_wr:.1f}%")
                                print(f"継続監視時100株損益: {carry_pnl:+,.0f}円")
                                print()
                    print(f"\n[{now_str()}] リプレイ完了（全銘柄のデータを再生し終えました）")
                    return 0

                # -----------------------------
                # 毎ループ表示する「リプレイ時刻(JST)・進行率」
                # -----------------------------
                replay_t = max((q.market_time_utc for q in quotes if q.market_time_utc), default=None)
                replay_t_jst = _fmt_dt_jst(replay_t)

                # =========================
                # Replay Morning Screen（指定時刻）を実行
                # =========================
                # 実行条件:
                # - --replay-morning-screen が指定されている
                # - 現在のReplay時刻（JST）が指定時刻に到達した
                # - その日（JST日付）ではまだ実行していない
                if ms_time is not None and replay_t is not None:
                    rt = replay_t
                    if rt.tzinfo is None:
                        rt = rt.replace(tzinfo=timezone.utc)
                    jst_now = rt.astimezone(JST)
                    day_jst = jst_now.strftime("%Y-%m-%d")
                    hh, mm = ms_time

                    # 日付が変わったら当日の追加監視をリセット（ベースwatchに戻す）
                    if ms_current_day_jst != day_jst:
                        prev_day_jst = ms_current_day_jst
                        ms_current_day_jst = day_jst
                        active_watch = set(base_watch)

                        # -----------------------------
                        # 前日継続監視銘柄の抽出 → 当日監視へ追加
                        # -----------------------------
                        # 条件（ユーザー要件）:
                        # - WIN（= 前日にWINが存在）
                        # - 100株損益 > 0（銘柄合計）
                        # - signal数 >= 2（銘柄合計）
                        # - max_profit_pct >= 1.0（銘柄内の最大）
                        if ms_time is not None and prev_day_jst:
                            # 前日シグナルを銘柄ごとに集計
                            prev_syms: dict[str, list[ReplaySignalEval]] = {}
                            # 継続候補抽出も「期待値検証の集計対象」に合わせます
                            # （= excluded_from_eval な signal は除外）
                            carry_eval_signals = [s for s in replay_signals if not bool(getattr(s, "excluded_from_eval", False))]
                            for ss in carry_eval_signals:
                                if _day_jst_str(ss.signal_time_utc) != prev_day_jst:
                                    continue
                                prev_syms.setdefault(ss.symbol, []).append(ss)

                            carry: list[str] = []
                            for sym, xs in prev_syms.items():
                                sig_n = len(xs)
                                if sig_n < 2:
                                    continue
                                win_n = sum(1 for x in xs if x.result == "WIN")
                                if win_n <= 0:
                                    continue
                                pnl = sum(float(_pnl_yen_100_shares_replay(x)) for x in xs)
                                if pnl <= 0:
                                    continue
                                max_profit = max(float(x.max_profit_pct()) for x in xs) if xs else 0.0
                                if max_profit < 1.0:
                                    continue
                                carry.append(sym)

                            # 翌営業日（当日）に適用するcarryoverリストとして保存
                            ms_carryover_by_day[day_jst] = sorted(set(carry))

                            # 当日の監視対象へ追加（Morning Screen TOP10 とは別枠）
                            carry_today = ms_carryover_by_day.get(day_jst) or []
                            if carry_today:
                                for sym in carry_today:
                                    if sym in active_watch:
                                        continue
                                    hist = _ensure_ms_history(sym)
                                    if hist is None:
                                        continue
                                    b, m = hist
                                    bars_by_symbol.setdefault(sym, b)
                                    meta_by_symbol.setdefault(sym, m)

                                    # idx を “今の時刻” に合わせる（timestamp>=rt の最初）
                                    seek = 0
                                    for i2, bb in enumerate(b):
                                        if bb.timestamp_utc >= rt:
                                            seek = i2
                                            break
                                    idx_by_symbol[sym] = seek

                                    # 前日終値INIT（metaにあれば）
                                    pc = m.get("previousClose")
                                    prev_close_by_day[f"{sym}::INIT"] = float(pc) if isinstance(pc, (int, float)) else None

                                    # 当日状態の初期化（簡易）
                                    running_day_high[sym] = float("-inf")
                                    running_day_volume[sym] = 0.0
                                    running_vwap_pv[sym] = 0.0
                                    running_vwap_v[sym] = 0.0
                                    day_key = rt.strftime("%Y-%m-%d")
                                    current_day_key[sym] = day_key
                                    for bb in b[:seek]:
                                        if bb.timestamp_utc.strftime("%Y-%m-%d") != day_key:
                                            continue
                                        running_day_high[sym] = max(float(running_day_high.get(sym, float("-inf"))), float(bb.high))
                                        running_day_volume[sym] = float(running_day_volume.get(sym, 0.0)) + float(bb.volume)
                                        tp = (float(bb.high) + float(bb.low) + float(bb.close)) / 3.0
                                        running_vwap_pv[sym] = float(running_vwap_pv.get(sym, 0.0)) + tp * float(bb.volume)
                                        running_vwap_v[sym] = float(running_vwap_v.get(sym, 0.0)) + float(bb.volume)

                                    if seek > 0:
                                        prev_price_by_symbol[sym] = float(b[seek - 1].close)
                                    else:
                                        prev_price_by_symbol.setdefault(sym, None)

                                    active_watch.add(sym)

                                print(
                                    f"[{now_str()}][Replay Carryover] {day_jst} 継続監視追加: "
                                    f"{', '.join(carry_today)}"
                                )

                    if (jst_now.hour, jst_now.minute) >= (hh, mm) and day_jst not in ms_daily:
                        # -----------------------------
                        # 1) Morning Screen（Replay版）
                        # -----------------------------
                        # 初心者向けポイント:
                        # - 「その時点の市場状況」を再現するには、rt 以前のバーだけを使います。
                        # - 以降のバー（未来）は絶対に使いません。

                        def _ensure_ms_history(sym: str) -> Optional[tuple[list[ReplayBar], dict]]:
                            if sym in ms_bars_cache:
                                return (ms_bars_cache[sym], ms_meta_cache.get(sym) or {})
                            try:
                                b, m = fetch_history_1m(session, sym, range_str=replay_range)
                                if not b:
                                    return None
                                ms_bars_cache[sym] = b
                                ms_meta_cache[sym] = m
                                return (b, m)
                            except Exception:
                                return None

                        def _bars_until_time_jst_day(
                            bars: list[ReplayBar],
                            *,
                            t_utc: datetime,
                        ) -> tuple[list[ReplayBar], Optional[float]]:
                            """
                            指定時刻までの「その日（JST日付）」のバーを返します。
                            - future（t_utcより後）は含めません
                            - prev_close は「当日最初のバーの直前のclose」を返します（取れれば）
                            """
                            if t_utc.tzinfo is None:
                                t_utc = t_utc.replace(tzinfo=timezone.utc)
                            target_day = t_utc.astimezone(JST).date()

                            day_bars: list[ReplayBar] = []
                            first_t: Optional[datetime] = None
                            for b in bars:
                                bt = b.timestamp_utc
                                if bt.tzinfo is None:
                                    bt = bt.replace(tzinfo=timezone.utc)
                                if bt.astimezone(JST).date() != target_day:
                                    continue
                                if first_t is None:
                                    first_t = bt
                                if bt <= t_utc:
                                    day_bars.append(b)
                            if not day_bars or first_t is None:
                                return ([], None)

                            prev_close: Optional[float] = None
                            prev_bar: Optional[ReplayBar] = None
                            for b in bars:
                                if b.timestamp_utc < first_t:
                                    prev_bar = b
                                else:
                                    break
                            if prev_bar is not None:
                                prev_close = float(prev_bar.close)
                            return (day_bars, prev_close)

                        ms_results: list[MorningScreenResult] = []
                        for sym in ms_universe:
                            hist = _ensure_ms_history(sym)
                            if hist is None:
                                continue
                            b, meta = hist
                            day_bars, prev_close = _bars_until_time_jst_day(b, t_utc=rt)
                            if not day_bars:
                                continue

                            last_bar = day_bars[-1]
                            price = float(last_bar.close)
                            day_high = max(float(x.high) for x in day_bars)
                            day_low = min(float(x.low) for x in day_bars)
                            vol = sum(float(x.volume) for x in day_bars)

                            # 除外条件（朝スクリーニング仕様）
                            if vol < 100_000.0:
                                continue

                            chg = _calc_change_percent(price=price, previous_close=prev_close)
                            if not isinstance(chg, (int, float)) or float(chg) < 0.0:
                                continue

                            # VWAP（概算）
                            pv = 0.0
                            vv = 0.0
                            for x in day_bars:
                                v = float(x.volume)
                                if v <= 0:
                                    continue
                                tp = (float(x.high) + float(x.low) + float(x.close)) / 3.0
                                pv += tp * v
                                vv += v
                            vwap = (pv / vv) if vv > 0 else None

                            # MA25 / 5日平均出来高（通常と同じ関数を再利用）
                            ma25: Optional[float] = None
                            avg5: Optional[float] = None
                            try:
                                cached = ma25_cache.get(sym)
                                if cached:
                                    cached_ma25, fetched_at = cached
                                    if (time.perf_counter() - fetched_at) < MA25_CACHE_TTL_SEC:
                                        ma25 = cached_ma25
                                if ma25 is None:
                                    fetched = fetch_ma25(session, sym)
                                    if fetched is not None:
                                        ma25_cache[sym] = (float(fetched), time.perf_counter())
                                        ma25 = float(fetched)
                            except Exception:
                                ma25 = None
                            try:
                                cached = avg5_cache.get(sym)
                                if cached:
                                    cached_avg5, fetched_at = cached
                                    if (time.perf_counter() - fetched_at) < VOL_AVG5_CACHE_TTL_SEC:
                                        avg5 = cached_avg5
                                if avg5 is None:
                                    fetched = fetch_avg_volume_5(session, sym)
                                    if fetched is not None:
                                        avg5_cache[sym] = (float(fetched), time.perf_counter())
                                        avg5 = float(fetched)
                            except Exception:
                                avg5 = None

                            q_ms = Quote(
                                symbol=sym,
                                price=price,
                                currency=str(meta.get("currency") or "JPY"),
                                previous_close=float(prev_close) if isinstance(prev_close, (int, float)) else None,
                                change_percent=float(chg) if isinstance(chg, (int, float)) else None,
                                day_high=float(day_high),
                                day_low=float(day_low),
                                volume=float(vol),
                                market_time_utc=rt,
                                market_cap=None,
                            )

                            day_range_pct = _calc_day_range_pct(
                                day_high=q_ms.day_high,
                                day_low=q_ms.day_low,
                                previous_close=q_ms.previous_close,
                                price=float(q_ms.price),
                            )
                            score, reasons, vol_spike_ratio = _morning_screen_score(
                                q=q_ms,
                                vwap=vwap,
                                ma25=ma25,
                                avg_vol5=avg5,
                                day_range_pct=day_range_pct,
                            )
                            ms_results.append(
                                MorningScreenResult(
                                    symbol=sym,
                                    score=int(score),
                                    quote=q_ms,
                                    vwap=vwap,
                                    ma25=ma25,
                                    avg_vol5=avg5,
                                    vol_spike_ratio=vol_spike_ratio,
                                    day_range_pct=day_range_pct,
                                    reasons=reasons,
                                )
                            )

                        ms_sorted = sorted(
                            ms_results,
                            key=lambda r: (r.score, float(r.quote.change_percent or 0.0), float(r.quote.volume or 0.0)),
                            reverse=True,
                        )
                        top10 = ms_sorted[:10]
                        selected_syms = [r.symbol for r in top10]
                        scores_by_symbol = {r.symbol: int(r.score) for r in top10}

                        ms_daily[day_jst] = {
                            "date": day_jst,
                            "hhmm": f"{hh:02d}:{mm:02d}",
                            "screen_time_utc": rt,
                            "symbols": selected_syms,
                            "scores": scores_by_symbol,
                            # 前日継続監視（別枠）
                            "carryover_symbols": list(ms_carryover_by_day.get(day_jst) or []),
                        }

                        # -----------------------------
                        # 2) 監視銘柄へ自動追加（当日ぶん）
                        # -----------------------------
                        for sym in selected_syms:
                            if sym in active_watch:
                                continue
                            hist = _ensure_ms_history(sym)
                            if hist is None:
                                continue
                            b, m = hist
                            bars_by_symbol.setdefault(sym, b)
                            meta_by_symbol.setdefault(sym, m)

                            # idx を “今の時刻” に合わせる（timestamp>=rt の最初）
                            seek = 0
                            for i2, bb in enumerate(b):
                                if bb.timestamp_utc >= rt:
                                    seek = i2
                                    break
                            idx_by_symbol[sym] = seek

                            # 前日終値INIT（metaにあれば）
                            pc = m.get("previousClose")
                            prev_close_by_day[f"{sym}::INIT"] = float(pc) if isinstance(pc, (int, float)) else None

                            # 当日状態の初期化（簡易）
                            running_day_high[sym] = float("-inf")
                            running_day_volume[sym] = 0.0
                            running_vwap_pv[sym] = 0.0
                            running_vwap_v[sym] = 0.0
                            day_key = rt.strftime("%Y-%m-%d")
                            current_day_key[sym] = day_key
                            for bb in b[:seek]:
                                if bb.timestamp_utc.strftime("%Y-%m-%d") != day_key:
                                    continue
                                running_day_high[sym] = max(float(running_day_high.get(sym, float("-inf"))), float(bb.high))
                                running_day_volume[sym] = float(running_day_volume.get(sym, 0.0)) + float(bb.volume)
                                tp = (float(bb.high) + float(bb.low) + float(bb.close)) / 3.0
                                running_vwap_pv[sym] = float(running_vwap_pv.get(sym, 0.0)) + tp * float(bb.volume)
                                running_vwap_v[sym] = float(running_vwap_v.get(sym, 0.0)) + float(bb.volume)

                            if seek > 0:
                                prev_price_by_symbol[sym] = float(b[seek - 1].close)
                            else:
                                prev_price_by_symbol.setdefault(sym, None)

                            active_watch.add(sym)

                        picked = ", ".join([f"{s}({scores_by_symbol.get(s, 0)})" for s in selected_syms]) if selected_syms else "(none)"
                        print(f"[{now_str()}][Replay MorningScreen] {day_jst} {hh:02d}:{mm:02d} JST picked: {picked}")

                pct = 0
                if total_bars > 0:
                    pct = int((progressed_bars / total_bars) * 100)
                    if pct > 100:
                        pct = 100
                # 例: [15:23:00][Replay 72%] replay_time_jst=2026-05-01 15:23:00
                print(f"[{now_str()}][Replay {pct}%] replay_time_jst={replay_t_jst}")

                # -----------------------------
                # signal後の価格推移を更新（期待値検証）
                # -----------------------------
                # このループの「現在値」で、未解決signalの max/min と take/stop 到達を更新します。
                for q in quotes:
                    idxs = active_signal_indices_by_symbol.get(q.symbol) or []
                    if not idxs:
                        continue
                    # この時点では「その日の累積VWAP」は running_vwap_* から計算できます。
                    vv = float(running_vwap_v.get(q.symbol, 0.0))
                    vwap_now = (float(running_vwap_pv.get(q.symbol, 0.0)) / vv) if vv > 0 else None

                    # recent_5m_low は「再生済みバー」から作ります（直近5分・最新足は除外）
                    played = idx_by_symbol.get(q.symbol, 0)
                    bars_played = bars_by_symbol.get(q.symbol, [])[:played]
                    recent_5m_low = None
                    if len(bars_played) >= 6:
                        lows_window = [float(b.low) for b in bars_played[-6:-1]]
                        if lows_window:
                            recent_5m_low = float(min(lows_window))
                    for idx in list(idxs):
                        s = replay_signals[idx]
                        s.update_with_price(
                            time_utc=(q.market_time_utc or datetime.now(tz=timezone.utc)),
                            price=float(q.price),
                            vwap=vwap_now,
                            recent_5m_low=recent_5m_low,
                        )
                        if s.resolved:
                            # 解決済みは active から外す（次ループ以降の更新を省略）
                            try:
                                idxs.remove(idx)
                            except Exception:
                                pass
                    if idxs:
                        active_signal_indices_by_symbol[q.symbol] = idxs
                    else:
                        active_signal_indices_by_symbol.pop(q.symbol, None)

                # -----------------------------
                # ここから下は「通常の判定ロジック」と同じ流れ
                # （データ供給源が realtime(quote) か replay(1m) かの違いだけ）
                # -----------------------------
                candidates: list[Quote] = []
                skip_reasons_by_symbol: dict[str, list[str]] = {}
                ma25_by_symbol: dict[str, float] = {}
                avg5_by_symbol: dict[str, float] = {}
                vol_spike_ratio_by_symbol: dict[str, Optional[float]] = {}
                vwap_by_symbol: dict[str, Optional[float]] = {}
                intraday_by_symbol: dict[str, IntradaySignals] = {}
                entry_cross_by_symbol: dict[str, bool] = {}

                for q in quotes:
                    reasons: list[str] = []

                    if q.change_percent is None:
                        reasons.append("前日終値取得失敗")
                    if q.day_high is None:
                        reasons.append("当日高値が取得できない")
                    if q.volume is None:
                        reasons.append("出来高が取得できない")

                    if q.change_percent is not None:
                        if q.change_percent < MIN_CHANGE_PCT:
                            reasons.append("前日比不足")
                        if q.change_percent >= MAX_CHANGE_PCT:
                            reasons.append("急騰しすぎ")

                    if q.day_high is not None:
                        if q.price < (MIN_RATIO_TO_DAY_HIGH * q.day_high):
                            reasons.append("高値付近ではない")

                    if q.volume is not None:
                        if q.volume < float(MIN_VOLUME):
                            reasons.append("出来高不足")

                    # 5日平均出来高（通常と同じく API 取得 + キャッシュ）
                    avg5: Optional[float] = None
                    if q.volume is not None:
                        try:
                            cached = avg5_cache.get(q.symbol)
                            if cached:
                                cached_avg5, fetched_at = cached
                                if (time.perf_counter() - fetched_at) < VOL_AVG5_CACHE_TTL_SEC:
                                    avg5 = cached_avg5
                            if avg5 is None:
                                fetched = fetch_avg_volume_5(session, q.symbol)
                                if fetched is not None:
                                    avg5_cache[q.symbol] = (float(fetched), time.perf_counter())
                                    avg5 = float(fetched)
                        except Exception:
                            avg5 = None

                    if avg5 is not None:
                        avg5_by_symbol[q.symbol] = avg5
                        ratio = q.volume / avg5 if avg5 > 0 else 0.0
                        vol_spike_ratio_by_symbol[q.symbol] = ratio
                    else:
                        vol_spike_ratio_by_symbol[q.symbol] = None

                    # MA25（通常と同じく API 取得 + キャッシュ）
                    ma25: Optional[float] = None
                    try:
                        cached = ma25_cache.get(q.symbol)
                        if cached:
                            cached_ma25, fetched_at = cached
                            if (time.perf_counter() - fetched_at) < MA25_CACHE_TTL_SEC:
                                ma25 = cached_ma25
                        if ma25 is None:
                            fetched = fetch_ma25(session, q.symbol)
                            if fetched is not None:
                                ma25_cache[q.symbol] = (float(fetched), time.perf_counter())
                                ma25 = float(fetched)
                    except Exception:
                        ma25 = None

                    if ma25 is None:
                        reasons.append("25日線が取得できない")
                    else:
                        ma25_by_symbol[q.symbol] = ma25
                        if q.price <= ma25:
                            reasons.append("25日線以下")

                    # VWAP（リプレイデータから概算）
                    vv = running_vwap_v.get(q.symbol, 0.0)
                    vwap = (running_vwap_pv.get(q.symbol, 0.0) / vv) if vv > 0 else None
                    vwap_by_symbol[q.symbol] = vwap
                    # ----------------------------------------
                    # 追加条件（エントリータイミング検知）
                    # ----------------------------------------
                    # リプレイでは「今までに再生したバー」を使って直近シグナルを計算します。
                    # - ここでは簡単に、bars_by_symbol の先頭から「今のindexまで」を渡して計算します。
                    #   （銘柄数が多い場合は最適化余地がありますが、まずは分かりやすさ優先）
                    played = idx_by_symbol.get(q.symbol, 0)
                    bars_played = bars_by_symbol.get(q.symbol, [])[:played]
                    sig = calc_intraday_signals_from_series(
                        price=float(q.price),
                        closes=[b.close for b in bars_played],
                        highs=[b.high for b in bars_played],
                        vols=[b.volume for b in bars_played],
                        vwap=vwap,
                    )
                    intraday_by_symbol[q.symbol] = sig
                    _LATEST_INTRADAY_SIGNALS[q.symbol] = sig

                    # =========================
                    # 追加ポジション（ADD）判定（改善版 / Replay期待値検証に反映）
                    # =========================
                    # 目的:
                    # - 弱い銘柄や失速局面でのADDを抑制し、損失拡大を防ぎたい。
                    #
                    # 追加条件（ユーザー要件）:
                    # - current_price > average_entry_price
                    # - current_price > vwap
                    # - current_price >= recent_5m_high * 1.001
                    # - 出来高増加継続（前回も今回も増加=True）
                    # - 最大ADD回数 = 2
                    # - 前回ADDから最低5分経過
                    # - VWAP乖離率 > 3.0% の場合ADD禁止
                    #
                    # ADD発生時ログ:
                    # - ADD理由 / 平均取得単価 / 現在保有数 / ADD回数 / VWAP乖離率 / 含み損益率
                    try:
                        # “保有中”判定: active に未解決ポジションがある
                        active_idxs = active_signal_indices_by_symbol.get(q.symbol) or []
                        holding = bool(active_idxs)

                        # 時刻（JST）
                        tcur = q.market_time_utc
                        if tcur is None:
                            tcur = datetime.now(tz=timezone.utc)
                        if tcur.tzinfo is None:
                            tcur = tcur.replace(tzinfo=timezone.utc)
                        jst_cur = tcur.astimezone(JST)

                        # 14:30 以降は禁止
                        after_1430 = (jst_cur.hour * 60 + jst_cur.minute) >= (14 * 60 + 30)

                        day_jst = _day_jst_str(tcur)
                        key = (day_jst, q.symbol)

                        add_count = int(add_count_by_day_symbol.get(key, 0))
                        # 前回ADDからの経過
                        last_add_t = last_add_time_by_day_symbol.get(key)
                        min_5min_passed = True
                        if isinstance(last_add_t, datetime):
                            dt_sec = (tcur - last_add_t).total_seconds()
                            min_5min_passed = dt_sec >= 5 * 60

                        # 平均取得単価（未解決ポジションを「保有」とみなし、各ポジション=100株として平均）
                        total_qty = 0
                        total_cost = 0.0
                        for idx in active_idxs:
                            try:
                                ps = replay_signals[int(idx)]
                            except Exception:
                                continue
                            qty = 100
                            total_qty += qty
                            total_cost += float(ps.entry_price) * float(qty)
                        avg_entry: Optional[float] = None
                        if total_qty > 0:
                            avg_entry = total_cost / float(total_qty)

                        # 出来高増加「継続」
                        vol_inc_now = (sig.vol_3m_gt_prev_3m is True)
                        vol_inc_prev = bool(prev_vol_inc_by_day_symbol.get(key, False))
                        vol_inc_cont = bool(vol_inc_prev and vol_inc_now)
                        prev_vol_inc_by_day_symbol[key] = bool(vol_inc_now)  # 次ループ用に更新

                        # VWAP乖離率（禁止条件）
                        vwap_dist = sig.vwap_distance_pct if isinstance(sig.vwap_distance_pct, (int, float)) else None
                        vwap_dist_block = (vwap_dist is not None) and (float(vwap_dist) > 3.0)

                        # recent_5m_high の上に「少しだけ上抜け」しているか（1.001倍）
                        rebreak_strong = False
                        if isinstance(sig.recent_5m_high, (int, float)):
                            rebreak_strong = float(q.price) >= float(sig.recent_5m_high) * 1.001

                        # 追加条件チェック
                        conds_ok = True
                        conds_ok = conds_ok and holding
                        conds_ok = conds_ok and (not after_1430)
                        conds_ok = conds_ok and (add_count < 2)
                        conds_ok = conds_ok and min_5min_passed
                        conds_ok = conds_ok and (avg_entry is not None and float(q.price) > float(avg_entry))
                        conds_ok = conds_ok and (isinstance(vwap, (int, float)) and float(q.price) > float(vwap))
                        conds_ok = conds_ok and bool(rebreak_strong)
                        conds_ok = conds_ok and bool(vol_inc_cont)
                        conds_ok = conds_ok and (not vwap_dist_block)

                        if conds_ok:
                            next_add = add_count + 1
                            # 追加ポジションの利確幅（前の仕様のまま）
                            if next_add == 1:
                                tp_pct = 0.025
                                kind = "ADD1"
                            else:
                                tp_pct = 0.015
                                kind = "ADD2"

                            entry2 = float(q.price)
                            stop2 = entry2 * (1.0 - STOP_LOSS_PCT_FROM_ENTRY)
                            take2 = entry2 * (1.0 + tp_pct)

                            s2 = ReplaySignalEval(
                                symbol=q.symbol,
                                signal_time_utc=tcur,
                                signal_price=float(q.price),
                                entry_price=float(entry2),
                                stop_price=float(stop2),
                                take_price=float(take2),
                                max_price_after=float(q.price),
                                min_price_after=float(q.price),
                                last_price_after=float(q.price),
                                position_kind=str(kind),
                                exit_style="fixed",
                            )
                            replay_signals.append(s2)
                            idx2 = len(replay_signals) - 1
                            active_signal_indices_by_symbol.setdefault(q.symbol, []).append(idx2)

                            # 状態更新
                            add_count_by_day_symbol[key] = next_add
                            last_add_time_by_day_symbol[key] = tcur

                            # 含み損益率（平均取得単価ベース）
                            upnl_pct = 0.0
                            if avg_entry is not None and float(avg_entry) > 0:
                                upnl_pct = ((float(q.price) - float(avg_entry)) / float(avg_entry)) * 100.0

                            # ADD理由ログ（要求項目）
                            reason_parts: list[str] = []
                            reason_parts.append("price>avg_entry")
                            reason_parts.append("price>VWAP")
                            reason_parts.append(">=recent5m_high*1.001")
                            reason_parts.append("vol_inc_cont")
                            reason_parts.append("cooldown>=5m")
                            reason_parts.append("vwap_dist<=3.0")
                            reason_text = " / ".join(reason_parts)

                            vd_s = "N/A" if vwap_dist is None else f"{float(vwap_dist):.2f}%"
                            print(f"[{now_str()}][ADD] {day_jst} {q.symbol} {kind}")
                            print(f"  理由: {reason_text}")
                            print(f"  平均取得単価: {_fmt_yen(avg_entry)}")
                            print(f"  現在保有数: {int(total_qty)}株")
                            print(f"  ADD回数: {next_add}/2")
                            print(f"  VWAP乖離率: {vd_s}")
                            print(f"  含み損益率: {upnl_pct:+.2f}%")
                    except Exception:
                        # 追加ポジションは“検証補助”なので、失敗しても通常の判定は継続します。
                        pass

                    # 1) VWAP乖離（必須）
                    if sig.vwap_distance_pct is None:
                        reasons.append("VWAP取得不可")
                    else:
                        if sig.vwap_distance_pct < float(VWAP_DISTANCE_PCT):
                            reasons.append("VWAP乖離不足")

                    # 2) 直近5分高値ブレイク（必須）
                    if sig.recent_5m_high is None:
                        reasons.append("直近5分高値が取れない")
                    else:
                        if float(q.price) <= float(sig.recent_5m_high):
                            reasons.append("5分高値ブレイク未成立")

                    # 3) 上昇傾向（必須）
                    if sig.price_5min_ago is None:
                        reasons.append("5分前価格が取れない")
                    else:
                        if float(q.price) <= float(sig.price_5min_ago):
                            reasons.append("上昇傾向なし")

                    if not reasons:
                        candidates.append(q)
                    else:
                        skip_reasons_by_symbol[q.symbol] = reasons

                candidate_symbols = {q.symbol for q in candidates}
                should_print = (not only_changes) or (candidate_symbols != last_candidates)
                if should_print:
                    # ここは「候補が何件か」だけを短く表示（時刻/進行率は上で毎回表示済み）
                    print(f"[{now_str()}] 条件一致: {len(candidates)} 銘柄")
                last_candidates = candidate_symbols

                # -----------------------------
                # Discord通知（3種類）
                # -----------------------------
                if discord_enabled:
                    # 条件一致（新規だけ）
                    to_notify = [q for q in candidates if q.symbol not in last_discord_candidate_symbols]
                    for q in to_notify:
                        entry_calc = calculate_entry(q)
                        if entry_calc is None:
                            continue
                        entry = float(entry_calc)
                        stop = entry * (1.0 - STOP_LOSS_PCT_FROM_ENTRY)
                        take = entry * (1.0 + TAKE_PROFIT_PCT_FROM_ENTRY)
                        ma25 = ma25_by_symbol.get(q.symbol)
                        if ma25 is None:
                            continue
                        try:
                            sig = intraday_by_symbol.get(q.symbol)
                            # Entry上抜け（最終仕様・シンプル版）:
                            # - 判定は「price >= entry」
                            # - ただし、同じ銘柄で連続通知しないために breakout_state を使います。
                            st = bool(breakout_state_by_symbol.get(q.symbol, False))
                            crossed = False
                            if entry > 0:
                                # 1) entry が大きく変わったら「別のentry候補」とみなして state をリセット
                                #    （古い突破状態を引きずらないため）
                                last_entry = last_breakout_entry_by_symbol.get(q.symbol)
                                if st and last_entry is not None and float(last_entry) > 0:
                                    diff_pct = (abs(float(entry) - float(last_entry)) / float(last_entry)) * 100.0
                                    if diff_pct >= float(BREAKOUT_ENTRY_RESET_PCT):
                                        breakout_state_by_symbol[q.symbol] = False
                                        st = False
                                        last_breakout_entry_by_symbol.pop(q.symbol, None)

                                if float(q.price) >= float(entry) and st is False:
                                    crossed = True
                                    breakout_state_by_symbol[q.symbol] = True
                                    last_breakout_entry_by_symbol[q.symbol] = float(entry)
                                if float(q.price) < float(entry):
                                    breakout_state_by_symbol[q.symbol] = False
                                    last_breakout_entry_by_symbol.pop(q.symbol, None)

                            # リプレイ時は「Replay時刻(JST)」をEmbedに入れます。
                            if crossed:
                                embed = build_embed_entry_cross(
                                    q,
                                    entry=entry,
                                    stop=stop,
                                    take=take,
                                    vwap=(sig.vwap if sig else vwap_by_symbol.get(q.symbol)),
                                    ma25=float(ma25),
                                    replay_time_jst=_fmt_dt_jst_short(q.market_time_utc),
                                    recent_5m_high=(sig.recent_5m_high if sig else None),
                                    price_5min_ago=(sig.price_5min_ago if sig else None),
                                    vwap_distance_pct=(sig.vwap_distance_pct if sig else None),
                                    vol_increase=(sig.vol_3m_gt_prev_3m if sig else None),
                                    entry_crossed=True,
                                )
                            else:
                                embed = build_embed_match(
                                    q,
                                    entry=entry,
                                    stop=stop,
                                    take=take,
                                    vwap=vwap_by_symbol.get(q.symbol),
                                    ma25=float(ma25),
                                    replay_time_jst=_fmt_dt_jst_short(q.market_time_utc),
                                    recent_5m_high=(sig.recent_5m_high if sig else None),
                                    price_5min_ago=(sig.price_5min_ago if sig else None),
                                    vwap_distance_pct=(sig.vwap_distance_pct if sig else None),
                                    vol_increase=(sig.vol_3m_gt_prev_3m if sig else None),
                                    entry_near_ratio=float(ENTRY_NEAR_RATIO),
                                    entry_crossed=False,
                                    breakout_state=bool(breakout_state_by_symbol.get(q.symbol, False)),
                                )
                            msg = {"embeds": [embed]}
                            discord_notify(
                                msg,
                                webhook_url=webhook_url,
                                alert_channel_id=alert_channel_id,
                                bot_token=bot_token,
                            )
                            last_notified_levels[q.symbol] = (float(entry), float(stop), float(take))

                            # -----------------------------
                            # 🚀 の瞬間を signal として記録（期待値検証用）
                            # -----------------------------
                            # 仕様:
                            # - 🚀 Entry上抜け が出たタイミングを signal とする
                            # - signal_price は通知時の現在値
                            # - entry/stop/take も保存
                            # - 以降の価格で max/min, take/stop 到達を追跡
                            if crossed:
                                # 同一銘柄で「同じ足で二重に記録」しない保険:
                                # breakout_state により crossed は初回だけ True になる想定ですが、念のため入れます。
                                sig_time = (q.market_time_utc or datetime.now(tz=timezone.utc))
                                day_jst = _day_jst_str(sig_time)

                                # 重複エントリー制限:
                                # - 同じJST日付で同じsymbolの2回目以降は「期待値検証から除外」します。
                                # - signal自体（検出ログ/詳細）は残しても良いので、excluded フラグで管理します。
                                exclude = False
                                exclude_reason = ""
                                if one_trade_per_symbol_per_day:
                                    seen = accepted_entry_symbols_by_day.setdefault(day_jst, set())
                                    if q.symbol in seen:
                                        exclude = True
                                        exclude_reason = "同一銘柄は1日1回まで（2回目以降は除外）"
                                    else:
                                        seen.add(q.symbol)

                                s = ReplaySignalEval(
                                    symbol=q.symbol,
                                    signal_time_utc=sig_time,
                                    signal_price=float(q.price),
                                    entry_price=float(entry),
                                    stop_price=float(stop),
                                    take_price=float(take),
                                    max_price_after=float(q.price),
                                    min_price_after=float(q.price),
                                    last_price_after=float(q.price),
                                    position_kind="BASE",
                                    exit_style="trailing",
                                    excluded_from_eval=bool(exclude),
                                    excluded_reason=str(exclude_reason),
                                )
                                replay_signals.append(s)
                                # 除外signalは“期待値検証の追跡”もしない（集計対象外なので）。
                                if not exclude:
                                    idx = len(replay_signals) - 1
                                    active_signal_indices_by_symbol.setdefault(q.symbol, []).append(idx)
                                    # 追加ポジション判定の基準となる「直近Entry価格」を更新します
                                    day_jst2 = _day_jst_str(sig_time)
                                    last_entry_price_by_day_symbol[(day_jst2, q.symbol)] = float(entry)
                        except Exception as e:
                            print(f"[{now_str()}] Discord通知失敗(replay): {q.symbol} ({e})")

                    # 条件外れ（安定化: 連続不一致が EXIT_CONFIRM_COUNT に達した時だけ通知）
                    out_symbols = sorted(last_discord_candidate_symbols - candidate_symbols)
                    for sym in out_symbols:
                        last_q = next((qq for qq in quotes if qq.symbol == sym), None)
                        try:
                            exit_miss_count[sym] = int(exit_miss_count.get(sym, 0)) + 1
                            if exit_miss_count[sym] < int(EXIT_CONFIRM_COUNT):
                                continue

                            embed_out = build_embed_out(
                                symbol=sym,
                                price=(last_q.price if last_q else None),
                                change_percent=(last_q.change_percent if last_q else None),
                                reasons=skip_reasons_by_symbol.get(sym, []),
                            )
                            msg_out = {"embeds": [embed_out]}
                            discord_notify(
                                msg_out,
                                webhook_url=webhook_url,
                                alert_channel_id=alert_channel_id,
                                bot_token=bot_token,
                            )
                            # 通知したらカウントをリセットし、候補集合からも外します（スパム防止）
                            exit_miss_count.pop(sym, None)
                            last_discord_candidate_symbols.discard(sym)
                            # 条件外れが確定したら breakout_state もリセットします（仕様）。
                            breakout_state_by_symbol[sym] = False
                            last_breakout_entry_by_symbol.pop(sym, None)
                        except Exception as e:
                            print(f"[{now_str()}] Discord条件外れ通知失敗(replay): {sym} ({e})")

                    # 候補に戻った銘柄はカウントをリセット
                    for sym in candidate_symbols:
                        exit_miss_count.pop(sym, None)

                    # 候補価格変更（条件一致中のみ）
                    candidates_by_symbol = {q.symbol: q for q in candidates}
                    new_notified_symbols = {q.symbol for q in to_notify}
                    for sym, (old_entry, old_stop, old_take) in list(last_notified_levels.items()):
                        q = candidates_by_symbol.get(sym)
                        if q is None or sym in new_notified_symbols:
                            continue
                        new_entry_calc = calculate_entry(q)
                        if new_entry_calc is None:
                            continue
                        new_entry = float(new_entry_calc)
                        new_stop = new_entry * (1.0 - STOP_LOSS_PCT_FROM_ENTRY)
                        new_take = new_entry * (1.0 + TAKE_PROFIT_PCT_FROM_ENTRY)
                        if not (
                            _level_changed(old=old_entry, new=new_entry)
                            or _level_changed(old=old_stop, new=new_stop)
                            or _level_changed(old=old_take, new=new_take)
                        ):
                            continue
                        try:
                            msg2 = _build_levels_change_message(
                                symbol=q.symbol,
                                price=float(q.price),
                                currency=str(q.currency),
                                change_percent=q.change_percent,
                                old_entry=float(old_entry),
                                new_entry=float(new_entry),
                                old_stop=float(old_stop),
                                new_stop=float(new_stop),
                                old_take=float(old_take),
                                new_take=float(new_take),
                            )
                            discord_notify(
                                msg2,
                                webhook_url=webhook_url,
                                alert_channel_id=alert_channel_id,
                                bot_token=bot_token,
                            )
                            last_notified_levels[q.symbol] = (float(new_entry), float(new_stop), float(new_take))
                        except Exception as e:
                            print(f"[{now_str()}] Discord再通知失敗(replay): {q.symbol} ({e})")

                    last_discord_candidate_symbols = candidate_symbols

                # 1秒ごとの再生
                elapsed = time.perf_counter() - loop_started
                sleep_sec = interval_sec - elapsed
                if sleep_sec > 0:
                    time.sleep(sleep_sec)

        except KeyboardInterrupt:
            print("\nCtrl+C を検知しました。終了します。")
            return 0


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    # -----------------------------
    # 朝スクリーニング（新機能）
    # -----------------------------
    # 重要:
    # - 通常監視やReplayに影響させないため、ここで早期returnします。
    # - これにより「監視ループ」や「Replayの挙動」を一切変更せずに機能追加できます。
    if bool(getattr(args, "morning_screen", False)):
        return run_morning_screen()

    # --replay が指定されたときだけ TEST_REPLAY_MODE を有効化します。
    # （指定しなければ False のまま = いつものリアルタイム監視に戻ります）
    global TEST_REPLAY_MODE
    TEST_REPLAY_MODE = bool(getattr(args, "replay", False))
    replay_range: str = str(getattr(args, "replay_range", "1d"))
    replay_morning_screen_hhmm: str = str(getattr(args, "replay_morning_screen", "") or "")
    one_trade_per_symbol_per_day: bool = bool(getattr(args, "one_trade_per_symbol_per_day", False))

    interval_sec: float = float(args.interval)
    print_all: bool = bool(args.print_all)
    only_changes: bool = bool(args.only_changes)
    watch_csv: str = str(args.watch or "")
    watch_file: str = str(args.watch_file or "")

    # 監視銘柄の決め方（初心者向けに整理）
    #
    # 1) --watch-file / --watch が指定された場合は「固定リスト」として扱います。
    #    → Discord の !watch add/remove とは独立です（コマンドライン指定が最優先）。
    #
    # 2) コマンドライン指定が無い場合:
    #    → watchlist.json が存在するなら、それを「正」として毎ループ読み直します（今回の仕様）。
    #    → watchlist.json が無い場合だけ、symbols.csv → WATCH にフォールバックします。
    fixed_watch: Optional[list[str]] = None
    if watch_file:
        try:
            fixed_watch = _load_watch_from_file(watch_file)
        except Exception as e:
            print(f"--watch-file の読み込みに失敗しました: {watch_file} ({e})")
            return 2
    elif watch_csv:
        fixed_watch = _parse_watch_csv(watch_csv)

    if interval_sec <= 0:
        print("--interval は 0 より大きい値にしてください。")
        return 2

    # -----------------------------
    # テスト用リプレイモード
    # -----------------------------
    # 注意:
    # - 相場時間外でも、過去の1分足を「1秒ごとに1分」進めて判定/Discord通知を確認できます。
    # - 通常モード（TEST_REPLAY_MODE=False）側は、既存コードをできるだけ触らない方針です。
    if TEST_REPLAY_MODE:
        return run_replay(
            interval_sec=interval_sec,
            only_changes=only_changes,
            fixed_watch=fixed_watch,
            replay_range=replay_range,
            replay_morning_screen_hhmm=replay_morning_screen_hhmm,
            one_trade_per_symbol_per_day=one_trade_per_symbol_per_day,
        )

    print("=== Yahoo Finance 日本株 スクリーニング（発注なし） ===")
    if fixed_watch is not None:
        print(f"- watch(固定): {', '.join(fixed_watch) if fixed_watch else '(empty)'}")
    else:
        print(f"- watch: (watchlist.json があれば毎回読み直します)")
    print(f"- interval: {interval_sec} sec")
    print(
        f"- 条件: 前日比 {MIN_CHANGE_PCT}%以上 {MAX_CHANGE_PCT}%未満 / "
        f"当日高値の {MIN_RATIO_TO_DAY_HIGH*100:.0f}%以上 / "
        f"出来高 {MIN_VOLUME:,}以上 / "
        f"現在値がMA25より上"
    )
    print("- Ctrl+C で終了します。\n")

    # only_changes 用。直前に出した候補セットを覚えておきます。
    last_candidates: set[str] = set()

    # Discord通知用:
    # - これまでは DISCORD_WEBHOOK_URL（Webhook）だけでしたが、
    #   今回から ALERT_CHANNEL_ID を指定すると「そのチャンネルへ送る」ことができます。
    #
    # 使い分け（初心者向け）:
    # - まず簡単に分離したい場合:
    #     通知用チャンネルでWebhookを作り、そのURLを DISCORD_WEBHOOK_URL に設定してください。
    #     → これだけで通知ログが分離できます（Bot権限などの難しい話が不要）。
    # - Bot送信に統一したい場合:
    #     ALERT_CHANNEL_ID と DISCORD_BOT_TOKEN を設定してください。
    #     → Webhookに依存せず、指定チャンネルへ直接送れます。
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    alert_channel_id = _parse_channel_id(os.getenv("ALERT_CHANNEL_ID", ""))
    # Bot送信用トークンは DISCORD_TOKEN に統一します（旧: DISCORD_BOT_TOKEN は互換で吸収）
    bot_token = _get_discord_token_with_compat_warning()

    # 通知が有効かどうか:
    # - Bot送信（ALERT_CHANNEL_ID + DISCORD_BOT_TOKEN）か、
    # - Webhook（DISCORD_WEBHOOK_URL）
    # のどちらかが使えれば True です。
    discord_enabled = bool((alert_channel_id is not None and bot_token) or webhook_url)

    # ログ（仕様）:
    # - Webhook URL が設定されている場合は「Webhookの送信先に送られる」ことを表示します。
    if webhook_url:
        print(f"[{now_str()}] Discord通知: DISCORD_WEBHOOK_URL が設定されています（Webhookの送信先チャンネルに送られます）")
    if alert_channel_id is not None and bot_token:
        print(f"[{now_str()}] Discord通知: ALERT_CHANNEL_ID={alert_channel_id} へ Bot送信します（推奨）")
    last_discord_candidate_symbols: set[str] = set()

    # MA25 のキャッシュ:
    # - symbol ごとに (ma25, fetched_at_monotonic) を持ちます
    ma25_cache: dict[str, tuple[float, float]] = {}

    # 出来高5日平均（VOL_AVG5）のキャッシュ:
    avg5_cache: dict[str, tuple[float, float]] = {}

    # VWAP のキャッシュ:
    # - None もキャッシュして、短い時間での再試行を減らします。
    vwap_cache: dict[str, tuple[Optional[float], float]] = {}

    # 1分足系列（直近シグナル計算用）のキャッシュ:
    # - symbol ごとに (closes, highs, vols, fetched_at)
    intraday_series_cache: dict[str, tuple[list[Optional[float]], list[Optional[float]], list[Optional[float]], float]] = {}

    # watchlist.json のリアルタイム反映用:
    # - 前回の監視銘柄リスト（壊れたJSONを読んだ場合に「前回のリストを維持」するため）
    last_watch: list[str] = []

    # 候補価格の変更通知用:
    # - 銘柄ごとに「前回Discordへ通知した entry/stop/take」を覚えておきます。
    # - 条件一致中でも、この値が大きく変化したら再通知します。
    last_notified_levels: dict[str, tuple[float, float, float]] = {}
    # 条件外れ通知の安定化（連続不一致をカウント）
    exit_miss_count: dict[str, int] = {}

    # requests.Session を使うと、接続の再利用ができて少し効率が良くなります。
    with requests.Session() as session:
        try:
            while True:
                loop_started = time.perf_counter()

                # =========================
                # 監視銘柄の決定（毎ループ）
                # =========================
                watch: list[str] = []

                if fixed_watch is not None:
                    # コマンドライン指定がある場合は固定です（リアルタイム反映しません）。
                    watch = list(fixed_watch)
                else:
                    # watchlist.json が存在する場合は「常にそれを正」として読み直します。
                    if os.path.exists(WATCHLIST_JSON_PATH):
                        watch_loaded, err = _load_watchlist_json(WATCHLIST_JSON_PATH)
                        if err:
                            # 壊れていた場合はエラーログを出して前回のリストを維持します。
                            print(f"[{now_str()}] watchlist.json 読み込みエラー（前回の監視リストを維持）: {err}")
                            watch = list(last_watch)
                        else:
                            watch = watch_loaded
                            last_watch = list(watch)

                        # 空配列 [] は「監視銘柄なし」という明示状態。
                        # フォールバックはせず、メッセージを出して待機します。
                        if not watch:
                            print(f"[{now_str()}] 監視銘柄なし。Discordで !watch add <symbol> してください。")
                            time.sleep(interval_sec)
                            continue
                    else:
                        # watchlist.json が無い場合だけ symbols.csv → WATCH にフォールバック
                        loaded_symbols = _load_symbols_csv(SYMBOLS_CSV_PATH)
                        watch = loaded_symbols if loaded_symbols else list(WATCH)

                # 念のため: 完全に空なら待機（固定watchで empty 指定など）
                if not watch:
                    print(f"[{now_str()}] 監視銘柄なし。Discordで !watch add <symbol> してください。")
                    time.sleep(interval_sec)
                    continue

                # Entry上抜け（クロス）判定用:
                # - このループで「前回価格」を参照できるように、読み取り用のスナップショットを作ります。
                prev_price_snapshot: dict[str, Optional[float]] = dict(prev_price_by_symbol)

                quotes: list[Quote] = []
                for sym in watch:
                    try:
                        q = fetch_quote(session, sym)
                        quotes.append(q)
                        last_quote_by_symbol[sym] = q
                        # 取得後に「今回の価格」を保存（次ループの prev になります）
                        prev_price_by_symbol[sym] = float(q.price)
                    except requests.HTTPError as e:
                        # 429（Too Many Requests）などが起きたら、間隔を伸ばす等が必要かもしれません。
                        print(f"[{now_str()}] {sym} HTTPエラー: {e}")
                    except Exception as e:
                        print(f"[{now_str()}] {sym} 取得エラー: {e}")

                # 条件判定して「候補だけ」残す（見送り理由も同時に作る）
                candidates: list[Quote] = []
                skip_reasons_by_symbol: dict[str, list[str]] = {}
                ma25_by_symbol: dict[str, float] = {}
                avg5_by_symbol: dict[str, float] = {}
                vol_spike_ratio_by_symbol: dict[str, Optional[float]] = {}
                vwap_by_symbol: dict[str, Optional[float]] = {}
                score_by_symbol: dict[str, int] = {}
                intraday_by_symbol: dict[str, IntradaySignals] = {}
                # 「このループで計算した entry」（= 直近5分高値ベース）を保存しておき、
                # 通知や状態管理（breakout_state）で同じ値を使います。
                current_entry_by_symbol: dict[str, Optional[float]] = {}
                # Entry上抜け（breakout）判定結果（このループ内で通知表示に使う）
                entry_cross_by_symbol: dict[str, bool] = {}

                for q in quotes:
                    reasons: list[str] = []

                    # 1) 必須項目チェック（欠けていると判定できないので見送り）
                    if q.change_percent is None:
                        # 要件: previousClose が取れず前日比が計算できない場合は、この理由を追加します。
                        reasons.append("前日終値取得失敗")
                    if q.day_high is None:
                        reasons.append("当日高値が取得できない")
                    if q.volume is None:
                        reasons.append("出来高が取得できない")
                    # 時価総額（marketCap）は Yahoo 側で取れない銘柄があるため、
                    # 取得できないこと自体では見送りにしません（仕様変更）。

                    # 2) 前日比: MIN <= chg < MAX
                    if q.change_percent is not None:
                        if q.change_percent < MIN_CHANGE_PCT:
                            reasons.append("前日比不足")
                        if q.change_percent >= MAX_CHANGE_PCT:
                            reasons.append("急騰しすぎ")

                    # 3) 高値付近: 現在値 >= 0.98 * 当日高値
                    if q.day_high is not None:
                        if q.price < (MIN_RATIO_TO_DAY_HIGH * q.day_high):
                            reasons.append("高値付近ではない")

                    # 4) 出来高: volume >= MIN_VOLUME
                    if q.volume is not None:
                        if q.volume < float(MIN_VOLUME):
                            reasons.append("出来高不足")

                    # 5) 出来高急増（スコア加点）:
                    # 以前は「必須条件」でしたが、仕様変更で「加点条件」にしました。
                    # - 出来高30万以上（MIN_VOLUME）は必須のまま（別条件で判定）
                    # - 出来高急増は必須ではない
                    # - 5日平均出来高が取れた場合だけ倍率を計算して、
                    #   倍率が MIN_VOLUME_SPIKE_RATIO 以上なら +1点します。
                    #
                    # 5日平均出来高が取れない場合:
                    # - 見送りにはしません（必須条件ではないため）
                    # - 表示/Discordでは N/A 扱いになります
                    avg5: Optional[float] = None
                    if q.volume is not None:
                        try:
                            cached = avg5_cache.get(q.symbol)
                            if cached:
                                cached_avg5, fetched_at = cached
                                if (time.perf_counter() - fetched_at) < VOL_AVG5_CACHE_TTL_SEC:
                                    avg5 = cached_avg5

                            if avg5 is None:
                                fetched = fetch_avg_volume_5(session, q.symbol)
                                if fetched is not None:
                                    avg5_cache[q.symbol] = (float(fetched), time.perf_counter())
                                    avg5 = float(fetched)
                        except Exception:
                            avg5 = None

                    # スコアの初期値（今は出来高急増だけですが、将来増やせます）
                    score = 0

                    if avg5 is not None:
                        avg5_by_symbol[q.symbol] = avg5
                        ratio = q.volume / avg5 if avg5 > 0 else 0.0
                        vol_spike_ratio_by_symbol[q.symbol] = ratio
                        if ratio >= MIN_VOLUME_SPIKE_RATIO:
                            score += 1
                    else:
                        # 取れない場合は N/A 表示になるようにしておく
                        vol_spike_ratio_by_symbol[q.symbol] = None

                    score_by_symbol[q.symbol] = score

                    # 6) MA25: 取れないなら見送り、取れたら price > ma25 だけ通す
                    ma25: Optional[float] = None
                    try:
                        cached = ma25_cache.get(q.symbol)
                        if cached:
                            cached_ma25, fetched_at = cached
                            if (time.perf_counter() - fetched_at) < MA25_CACHE_TTL_SEC:
                                ma25 = cached_ma25

                        if ma25 is None:
                            fetched = fetch_ma25(session, q.symbol)
                            if fetched is not None:
                                ma25_cache[q.symbol] = (float(fetched), time.perf_counter())
                                ma25 = float(fetched)
                    except Exception:
                        ma25 = None

                    if ma25 is None:
                        reasons.append("25日線が取得できない")
                    else:
                        ma25_by_symbol[q.symbol] = ma25
                        if q.price <= ma25:
                            reasons.append("25日線以下")

                    # 7) 時価総額レンジ: MIN_MARKET_CAP <= marketCap <= MAX_MARKET_CAP
                    # marketCap が取得できた場合だけレンジ判定します。
                    if q.market_cap is not None:
                        if q.market_cap < MIN_MARKET_CAP or q.market_cap > MAX_MARKET_CAP:
                            reasons.append("時価総額レンジ外")

                    # 8) VWAP/1分足由来の「エントリータイミング」条件
                    #
                    # ここが今回の改善ポイントです。
                    # - 以前: "price > VWAP"（= 強い銘柄っぽい）だけで候補になることがあり、レンジ往復でも通知が出やすい
                    # - 変更後: 「直近5分高値ブレイク」「5分前より上」「VWAPより0.2%以上上」を満たすときだけ候補にする
                    #
                    # これらを計算するために「1分足系列（close/high/volume）」も一緒に参照します。
                    vwap: Optional[float] = None
                    used_cache = False
                    if not reasons:
                        # ここまでの条件で「ほぼ通る」銘柄だけ VWAP を取りにいきます（負荷を下げるため）
                        try:
                            cached = vwap_cache.get(q.symbol)
                            used_cache = False
                            if cached:
                                cached_vwap, fetched_at = cached
                                if (time.perf_counter() - fetched_at) < VWAP_CACHE_TTL_SEC:
                                    # 期限内ならキャッシュをそのまま使います（None でもOK）
                                    vwap = cached_vwap
                                    used_cache = True

                            # 期限切れ or キャッシュ無しなら取り直し
                            if not used_cache:
                                vwap_fetched = fetch_vwap(session, q.symbol)
                                vwap_cache[q.symbol] = (vwap_fetched, time.perf_counter())
                                vwap = vwap_fetched
                        except Exception:
                            vwap = None

                        if vwap is None and not used_cache:
                            # 今回からVWAP乖離が必須条件になるので、取得できない場合は候補になりません。
                            print(f"[{now_str()}] {q.symbol} VWAP取得不可（この銘柄は候補になりません）")
                        else:
                            vwap_by_symbol[q.symbol] = vwap
                            # 1) VWAP乖離条件（必須）
                            sig_tmp = calc_intraday_signals_from_series(
                                price=float(q.price),
                                closes=[],
                                highs=[],
                                vols=[],
                                vwap=vwap,
                            )
                            intraday_by_symbol[q.symbol] = sig_tmp
                            if sig_tmp.vwap_distance_pct is None:
                                reasons.append("VWAP取得不可")
                            else:
                                if sig_tmp.vwap_distance_pct < float(VWAP_DISTANCE_PCT):
                                    reasons.append("VWAP乖離不足")

                            # 2) 1分足系列を取って recent_5m_high / price_5min_ago / 出来高増加 を計算
                            # - 毎秒叩くと重いので短いTTLキャッシュを使います
                            closes_1m: list[Optional[float]] = []
                            highs_1m: list[Optional[float]] = []
                            vols_1m: list[Optional[float]] = []
                            try:
                                cached = intraday_series_cache.get(q.symbol)
                                if cached:
                                    c_closes, c_highs, c_vols, fetched_at = cached
                                    if (time.perf_counter() - fetched_at) < INTRADAY_SERIES_CACHE_TTL_SEC:
                                        closes_1m, highs_1m, vols_1m = c_closes, c_highs, c_vols
                                if not closes_1m:
                                    c2, h2, v2 = fetch_intraday_1m_series(session, q.symbol)
                                    closes_1m, highs_1m, vols_1m = c2, h2, v2
                                    intraday_series_cache[q.symbol] = (closes_1m, highs_1m, vols_1m, time.perf_counter())
                            except Exception:
                                closes_1m, highs_1m, vols_1m = [], [], []

                            sig = calc_intraday_signals_from_series(
                                price=float(q.price),
                                closes=closes_1m,
                                highs=highs_1m,
                                vols=vols_1m,
                                vwap=vwap,
                            )
                            intraday_by_symbol[q.symbol] = sig
                            _LATEST_INTRADAY_SIGNALS[q.symbol] = sig
                            # このループの entry（新仕様）を確定して保存
                            current_entry_by_symbol[q.symbol] = calculate_entry(q)

                            # 3) 直近5分高値ブレイク（必須）
                            if sig.recent_5m_high is None:
                                reasons.append("直近5分高値が取れない")
                            else:
                                if float(q.price) <= float(sig.recent_5m_high):
                                    reasons.append("5分高値ブレイク未成立")

                            # 4) 上昇傾向（必須）
                            if sig.price_5min_ago is None:
                                reasons.append("5分前価格が取れない")
                            else:
                                if float(q.price) <= float(sig.price_5min_ago):
                                    reasons.append("上昇傾向なし")

                            # 5) 出来高増加（加点）
                            # 仕様変更: 出来高増加を必須化
                            if sig.vol_3m_gt_prev_3m is not True:
                                reasons.append("出来高増加なし")
                            else:
                                # 必須を満たした上で、スコアとしても +1（将来の並び替え等に使える）
                                score_by_symbol[q.symbol] = score_by_symbol.get(q.symbol, 0) + 1

                            # 追加条件: Entry候補（新仕様: 直近5分高値ベース）への接近
                            # entry = recent_5m_high * ENTRY_BREAKOUT_BUFFER
                            entry_calc = calc_entry_from_signals(sig)
                            if entry_calc is None:
                                reasons.append("Entry計算不可")
                            else:
                                if float(q.price) < (float(entry_calc) * float(ENTRY_NEAR_RATIO)):
                                    reasons.append("Entry候補から遠い")

                            # Entry上抜け（breakout）判定（最終仕様・シンプル版）:
                            # - まずは「price >= entry」で突破とします。
                            # - ただし、同じ銘柄で連続通知しないために breakout_state を使います。
                            #
                            # 状態:
                            # - breakout_state=False: まだentry未突破
                            # - breakout_state=True : entry突破済み
                            entry_now = current_entry_by_symbol.get(q.symbol)
                            if entry_now is None:
                                entry_cross_by_symbol[q.symbol] = False
                            else:
                                st = bool(breakout_state_by_symbol.get(q.symbol, False))
                                # entry が大きく変わったら state をリセット（新しいentry候補に追従する）
                                last_entry = last_breakout_entry_by_symbol.get(q.symbol)
                                if st and last_entry is not None and float(last_entry) > 0:
                                    diff_pct = (abs(float(entry_now) - float(last_entry)) / float(last_entry)) * 100.0
                                    if diff_pct >= float(BREAKOUT_ENTRY_RESET_PCT):
                                        breakout_state_by_symbol[q.symbol] = False
                                        st = False
                                        last_breakout_entry_by_symbol.pop(q.symbol, None)
                                if float(q.price) >= float(entry_now) and st is False:
                                    # 初回突破 → 🚀通知対象
                                    entry_cross_by_symbol[q.symbol] = True
                                    breakout_state_by_symbol[q.symbol] = True
                                    last_breakout_entry_by_symbol[q.symbol] = float(entry_now)
                                else:
                                    entry_cross_by_symbol[q.symbol] = False
                                # entry を下回ったら未突破へ戻す（次の突破でまた通知できる）
                                if float(q.price) < float(entry_now):
                                    breakout_state_by_symbol[q.symbol] = False
                                    last_breakout_entry_by_symbol.pop(q.symbol, None)
                    # vwap が None のままでも理由は追加しない（通過扱い）
                    if vwap is None:
                        vwap_by_symbol[q.symbol] = None

                    # 最終判定: reasons が空なら条件一致
                    if not reasons:
                        candidates.append(q)
                    else:
                        skip_reasons_by_symbol[q.symbol] = reasons

                # 出力: 条件に合う銘柄だけ表示
                candidate_symbols = {q.symbol for q in candidates}
                should_print = (not only_changes) or (candidate_symbols != last_candidates)
                if should_print:
                    if candidates:
                        print(f"\n[{now_str()}] 条件一致: {len(candidates)} 銘柄")
                        # 見やすいように、前日比%が大きい順に並べます
                        for q in sorted(candidates, key=lambda x: x.change_percent or -999, reverse=True):
                            v = "N/A" if q.volume is None else str(int(q.volume))
                            ratio = (q.price / q.day_high) if q.day_high else 0.0
                            mt = q.market_time_utc.isoformat() if q.market_time_utc else "N/A"
                            ma25 = ma25_by_symbol.get(q.symbol)
                            ma25_s = _fmt_price(ma25) if ma25 is not None else "N/A"
                            avg5 = avg5_by_symbol.get(q.symbol)
                            avg5_s = _fmt_volume(avg5) if avg5 is not None else "N/A"
                            vol_spike_ratio = vol_spike_ratio_by_symbol.get(q.symbol)
                            vol_spike_ratio_s = "N/A" if vol_spike_ratio is None else f"{vol_spike_ratio:.2f}x"
                            vwap = vwap_by_symbol.get(q.symbol)
                            vwap_s = _fmt_price(vwap) if vwap is not None else "N/A"
                            mcap_s = "取得不可" if q.market_cap is None else f"{int(round(q.market_cap)):,}"
                            score = score_by_symbol.get(q.symbol, 0)
                            prev_s = _fmt_price(q.previous_close)
                            chg_s = "N/A" if q.change_percent is None else f"{q.change_percent:.2f}%"
                            print(
                                f"  - {q.symbol}: "
                                f"price={_fmt_price(q.price)} {q.currency}, "
                                f"prevClose={prev_s}, "
                                f"chg%={chg_s}, "
                                f"day_high={q.day_high} (ratio={ratio*100:.2f}%), "
                                f"vol={v}, vol_avg5={avg5_s}, spike={vol_spike_ratio_s}, "
                                f"score={score}, "
                                f"vwap={vwap_s}, mcap={mcap_s}, ma25={ma25_s}, time_utc={mt}"
                            )
                        print()
                    else:
                        print(f"[{now_str()}] 条件一致: 0 銘柄")

                    # 見送り理由も表示（指定された理由を出す）
                    # 既存の --print-all の概念は残しつつ、
                    # 追加要件に合わせて「見送り理由」を表示します。
                    for q in sorted(quotes, key=lambda x: x.symbol):
                        reasons = skip_reasons_by_symbol.get(q.symbol)
                        if not reasons:
                            continue
                        prev_s = _fmt_price(q.previous_close)
                        chg_s = "N/A" if q.change_percent is None else f"{q.change_percent:.2f}%"
                        print(
                            f"  - {q.symbol}: 見送り（{' / '.join(reasons)}）"
                            f" / price={_fmt_price(q.price)} prevClose={prev_s} chg%={chg_s}"
                        )

                last_candidates = candidate_symbols

                # ※ prev_entry_snapshot は一旦使わない方針なので、prev_entry_by_symbol の更新も停止します。

                # Discord通知:
                # candidates の中から「前回ループで候補に入っていなかった銘柄」だけ送ります。
                # これにより、同じ銘柄が条件一致し続けても毎秒スパム通知されません。
                if discord_enabled:
                    # candidates は list[Quote] なので、symbol 重複が無い前提で扱います（WATCH は通常ユニーク）。
                    to_notify = [q for q in candidates if q.symbol not in last_discord_candidate_symbols]

                    # -----------------------------
                    # 1) 条件一致通知（新規だけ）
                    # -----------------------------
                    if to_notify:
                        # 見やすさのため、前日比が大きい順に送ります。
                        to_notify_sorted = sorted(to_notify, key=lambda x: x.change_percent or -999, reverse=True)
                        for q in to_notify_sorted:
                            # Entry候補は「直近5分高値ベース」で計算します（新仕様）。
                            sig = intraday_by_symbol.get(q.symbol)
                            entry_calc = calc_entry_from_signals(sig)
                            if entry_calc is None:
                                # ここまで来る時点で recent_5m_high はあるはずですが、念のためガード
                                continue
                            entry = float(entry_calc)
                            stop = entry * (1.0 - STOP_LOSS_PCT_FROM_ENTRY)
                            take = entry * (1.0 + TAKE_PROFIT_PCT_FROM_ENTRY)
                            try:
                                ma25 = ma25_by_symbol.get(q.symbol)
                                if ma25 is None:
                                    continue
                                vol_avg5 = avg5_by_symbol.get(q.symbol)
                                vol_spike_ratio = vol_spike_ratio_by_symbol.get(q.symbol)
                                vwap = vwap_by_symbol.get(q.symbol)
                                market_cap = q.market_cap

                                msg = _build_discord_message(
                                    q,
                                    entry=entry,
                                    stop=stop,
                                    take=take,
                                    ma25=float(ma25),
                                    vol_avg5=vol_avg5,
                                    vol_spike_ratio=vol_spike_ratio,
                                    vwap=vwap,
                                    market_cap=(float(market_cap) if market_cap is not None else None),
                                )
                                # 通常モードの通知にも、今回追加した「エントリー判断に必要な情報」を載せます。
                                crossed = bool(entry_cross_by_symbol.get(q.symbol, False))
                                if sig:
                                    # 通常の条件一致Embed（🟢）か、Entry上抜けEmbed（🚀）かを切り替えます。
                                    if crossed:
                                        embed = build_embed_entry_cross(
                                            q,
                                            entry=entry,
                                            stop=stop,
                                            take=take,
                                            vwap=sig.vwap,
                                            ma25=float(ma25),
                                            replay_time_jst=None,
                                            recent_5m_high=sig.recent_5m_high,
                                            price_5min_ago=sig.price_5min_ago,
                                            vwap_distance_pct=sig.vwap_distance_pct,
                                            vol_increase=sig.vol_3m_gt_prev_3m,
                                            entry_crossed=True,
                                        )
                                        msg = {"embeds": [embed]}
                                    else:
                                        msg["embeds"][0]["fields"] = [
                                            _embed_field("現在値", _fmt_yen(q.price), inline=True),
                                            _embed_field("前日比", _fmt_pct(q.change_percent), inline=True),
                                            _embed_field("出来高", _fmt_volume_man(q.volume), inline=True),
                                            _embed_field("高値接近率", _fmt_ratio_pct(q.price, q.day_high), inline=True),
                                            _embed_field("直近5分高値", _fmt_yen(sig.recent_5m_high), inline=True),
                                            _embed_field("5分前価格", _fmt_yen(sig.price_5min_ago), inline=True),
                                            _embed_field("VWAP乖離率", ("N/A" if sig.vwap_distance_pct is None else f"{sig.vwap_distance_pct:.2f}%"), inline=True),
                                            _embed_field("出来高増加", ("N/A" if sig.vol_3m_gt_prev_3m is None else ("あり" if sig.vol_3m_gt_prev_3m else "なし")), inline=True),
                                            _embed_field("Entry接近率", f"{(float(q.price)/float(entry))*100.0:.2f}%", inline=True),
                                            _embed_field("Entry上抜け", ("成立" if crossed else "未成立"), inline=True),
                                            _embed_field("売買候補", "\n".join([f"Entry: {_fmt_yen(entry)}", f"Stop: {_fmt_yen(stop)}", f"Take: {_fmt_yen(take)}"]), inline=False),
                                            _embed_field("補足", "\n".join([f"VWAP: {_fmt_yen(sig.vwap)}", f"25MA: {_fmt_yen(float(ma25))}"]), inline=False),
                                        ]
                                discord_notify(
                                    msg,
                                    webhook_url=webhook_url,
                                    alert_channel_id=alert_channel_id,
                                    bot_token=bot_token,
                                )

                                # 新規条件一致通知を出した時点の候補価格を覚えておきます。
                                last_notified_levels[q.symbol] = (float(entry), float(stop), float(take))
                            except Exception as e:
                                print(f"[{now_str()}] Discord通知失敗: {q.symbol} ({e})")

                    # -----------------------------
                    # 2) 条件外れ通知
                    # -----------------------------
                    # 前回は候補だったが、今回は候補ではない銘柄に対して通知します。
                    out_symbols = sorted(last_discord_candidate_symbols - candidate_symbols)
                    for sym in out_symbols:
                        try:
                            exit_miss_count[sym] = int(exit_miss_count.get(sym, 0)) + 1
                            if exit_miss_count[sym] < int(EXIT_CONFIRM_COUNT):
                                continue
                            last_q = last_quote_by_symbol.get(sym)
                            embed_out = build_embed_out(
                                symbol=sym,
                                price=(last_q.price if last_q else None),
                                change_percent=(last_q.change_percent if last_q else None),
                                reasons=skip_reasons_by_symbol.get(sym, []),
                            )
                            msg_out = {"embeds": [embed_out]}
                            discord_notify(
                                msg_out,
                                webhook_url=webhook_url,
                                alert_channel_id=alert_channel_id,
                                bot_token=bot_token,
                            )
                            exit_miss_count.pop(sym, None)
                            last_discord_candidate_symbols.discard(sym)
                            # 条件外れが確定したら breakout_state もリセットします（仕様）。
                            breakout_state_by_symbol[sym] = False
                            last_breakout_entry_by_symbol.pop(sym, None)
                        except Exception as e:
                            print(f"[{now_str()}] Discord条件外れ通知失敗: {sym} ({e})")

                    # 候補に戻った銘柄はカウントをリセット
                    for sym in candidate_symbols:
                        exit_miss_count.pop(sym, None)

                    # -----------------------------
                    # 3) 候補価格の大幅変更通知（再通知）
                    # -----------------------------
                    # 条件一致している銘柄だけ対象（candidates の中だけ）
                    candidates_by_symbol = {q.symbol: q for q in candidates}
                    new_notified_symbols = {qq.symbol for qq in to_notify}
                    for sym, (old_entry, old_stop, old_take) in list(last_notified_levels.items()):
                        q = candidates_by_symbol.get(sym)
                        if q is None:
                            continue
                        if sym in new_notified_symbols:
                            continue
                        new_entry_calc = calculate_entry(q)
                        if new_entry_calc is None:
                            continue
                        new_entry = float(new_entry_calc)
                        new_stop = new_entry * (1.0 - STOP_LOSS_PCT_FROM_ENTRY)
                        new_take = new_entry * (1.0 + TAKE_PROFIT_PCT_FROM_ENTRY)
                        changed = (
                            _level_changed(old=old_entry, new=new_entry)
                            or _level_changed(old=old_stop, new=new_stop)
                            or _level_changed(old=old_take, new=new_take)
                        )
                        if not changed:
                            continue
                        try:
                            msg2 = _build_levels_change_message(
                                symbol=q.symbol,
                                price=float(q.price),
                                currency=str(q.currency),
                                change_percent=q.change_percent,
                                old_entry=float(old_entry),
                                new_entry=float(new_entry),
                                old_stop=float(old_stop),
                                new_stop=float(new_stop),
                                old_take=float(old_take),
                                new_take=float(new_take),
                            )
                            discord_notify(
                                msg2,
                                webhook_url=webhook_url,
                                alert_channel_id=alert_channel_id,
                                bot_token=bot_token,
                            )
                            last_notified_levels[q.symbol] = (float(new_entry), float(new_stop), float(new_take))
                        except Exception as e:
                            print(f"[{now_str()}] Discord再通知失敗: {q.symbol} ({e})")

                # どの銘柄を「前回候補」とみなすかを更新します。
                # これで「同じ銘柄を連続通知しない」を満たします。
                if discord_enabled:
                    last_discord_candidate_symbols = candidate_symbols

                # 「1秒ごと」に近づけるために、処理時間を差し引いて sleep します。
                elapsed = time.perf_counter() - loop_started
                sleep_sec = interval_sec - elapsed
                if sleep_sec > 0:
                    time.sleep(sleep_sec)
                else:
                    # APIが遅い / ネットが遅いと、処理が1秒を超えることがあります。
                    # その場合は待たずに次のループへ進みます（間隔はズレます）。
                    pass

        except KeyboardInterrupt:
            print("\nCtrl+C を検知しました。終了します。")
            return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

