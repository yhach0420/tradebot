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
    - range は "1d" または "5d"

    戻り値:
    - bars: 1分足の配列（欠損データは除外）
    - meta: chart.result[0].meta（currency/previousClose等が入ることがあります）
    """
    if range_str not in ("1d", "5d"):
        raise ValueError("range_str は '1d' または '5d' を指定してください")

    url = YAHOO_CHART_URL.format(symbol=symbol)
    params = {"interval": "1m", "range": range_str}
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
        choices=["1d", "5d"],
        help="リプレイで取得する期間。1d（直近1日）または 5d（直近5日）。デフォルト 1d",
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


def run_replay(
    *,
    interval_sec: float,
    only_changes: bool,
    fixed_watch: Optional[list[str]],
    replay_range: str,
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
    if replay_range not in ("1d", "5d"):
        print("--replay-range は 1d または 5d を指定してください。")
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
    # Entry上抜け（クロス）判定用に「前回価格」を覚えておきます。
    prev_price_by_symbol: dict[str, Optional[float]] = {}
    # Entry上抜け（クロス）判定用に「前回価格」を覚えておきます。
    prev_price_by_symbol: dict[str, float] = {}

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
                for sym, bars in bars_by_symbol.items():
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
                            volume=float(running_day_volume[sym]),  # 日内累積出来高として扱う
                            market_time_utc=bar.timestamp_utc,
                            market_cap=None,
                        )
                    )

                    # 今回の価格を保存（次ループで prev として使う）
                    prev_price_by_symbol[sym] = float(bar.close)

                # 全銘柄が再生し終えたら終了
                if not quotes:
                    print(f"\n[{now_str()}] リプレイ完了（全銘柄のデータを再生し終えました）")
                    return 0

                # -----------------------------
                # 毎ループ表示する「リプレイ時刻(JST)・進行率」
                # -----------------------------
                replay_t = max((q.market_time_utc for q in quotes if q.market_time_utc), default=None)
                replay_t_jst = _fmt_dt_jst(replay_t)
                pct = 0
                if total_bars > 0:
                    pct = int((progressed_bars / total_bars) * 100)
                    if pct > 100:
                        pct = 100
                # 例: [15:23:00][Replay 72%] replay_time_jst=2026-05-01 15:23:00
                print(f"[{now_str()}][Replay {pct}%] replay_time_jst={replay_t_jst}")

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
                        entry = float(q.day_high)
                        stop = entry * (1.0 - STOP_LOSS_PCT_FROM_ENTRY)
                        take = entry * (1.0 + TAKE_PROFIT_PCT_FROM_ENTRY)
                        ma25 = ma25_by_symbol.get(q.symbol)
                        if ma25 is None:
                            continue
                        try:
                            sig = intraday_by_symbol.get(q.symbol)
                            crossed = False
                            prev_p = prev_price_by_symbol.get(q.symbol)
                            if prev_p is not None and entry > 0:
                                crossed = (float(prev_p) <= float(entry) and float(q.price) >= float(entry))

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
                                )
                            msg = {"embeds": [embed]}
                            discord_notify(
                                msg,
                                webhook_url=webhook_url,
                                alert_channel_id=alert_channel_id,
                                bot_token=bot_token,
                            )
                            last_notified_levels[q.symbol] = (float(entry), float(stop), float(take))
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
                        new_entry = float(q.day_high)
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

    # --replay が指定されたときだけ TEST_REPLAY_MODE を有効化します。
    # （指定しなければ False のまま = いつものリアルタイム監視に戻ります）
    global TEST_REPLAY_MODE
    TEST_REPLAY_MODE = bool(getattr(args, "replay", False))
    replay_range: str = str(getattr(args, "replay_range", "1d"))

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

                            # 追加条件: Entry候補（entry=当日高値）への接近
                            if q.day_high is not None:
                                entry = float(q.day_high)
                                if entry > 0:
                                    if float(q.price) < (entry * float(ENTRY_NEAR_RATIO)):
                                        reasons.append("Entry候補から遠い")

                                    # Entry上抜け（クロス）判定:
                                    # - prev_price_snapshot は「前回ループの価格」です。
                                    # - prev <= entry かつ now >= entry のとき「上抜け成立」とします。
                                    prev_p = prev_price_snapshot.get(q.symbol)
                                    crossed = bool(prev_p is not None and float(prev_p) <= entry and float(q.price) >= entry)
                                    entry_cross_by_symbol[q.symbol] = crossed
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
                            entry = float(q.day_high)
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
                                sig = intraday_by_symbol.get(q.symbol)
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
                        if q.day_high is None:
                            continue
                        new_entry = float(q.day_high)
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

