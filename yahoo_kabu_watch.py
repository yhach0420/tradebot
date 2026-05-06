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
from datetime import datetime, timezone
from typing import Optional

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
# 出来高急増（5日平均出来高との比較）
# ----------------------------
MIN_VOLUME_SPIKE_RATIO = 2.0  # 現在出来高 >= 5日平均出来高 * この倍率

# 5日平均出来高は chart から計算します（毎秒取得は重いのでキャッシュ）。
VOL_AVG5_CACHE_TTL_SEC = 60 * 10  # 10分

# VWAP は日中に変わるので、比較的短めにキャッシュします
VWAP_CACHE_TTL_SEC = 60 * 5  # 5分

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
) -> str:
    """
    Discord に送る「仕様どおりの項目」を 1メッセージにまとめます。
    必須項目:
    - 銘柄コード
    - 現在値
    - 前日比
    - 出来高
    - 当日高値
    - エントリー候補
    - 損切り候補
    - 利確候補
    """
    chg = q.change_percent
    if chg is None:
        chg_s = "N/A"
    else:
        # 前日比は符号付きで表示します（例: +1.23%）。
        sign = "+" if chg >= 0 else ""
        chg_s = f"{sign}{chg:.2f}%"

    return (
        f"条件一致（WATCH）\n"
        f"- 銘柄: {q.symbol}\n"
        f"- 現在値: {_fmt_price(q.price)} {q.currency}\n"
        f"- 前日比: {chg_s}\n"
        f"- 出来高: {_fmt_volume(q.volume)}\n"
        f"- 5日平均出来高: {_fmt_volume(vol_avg5)}\n"
        f"- 出来高急増倍率: {('N/A' if vol_spike_ratio is None else f'{vol_spike_ratio:.2f}x')}\n"
        f"- 25日移動平均: {_fmt_price(ma25)}\n"
        f"- 当日高値: {_fmt_price(q.day_high)}\n"
        f"- VWAP: {_fmt_price(vwap)}\n"
        f"- 時価総額: {('取得不可' if market_cap is None else f'{int(round(market_cap)):,}')}\n"
        f"- エントリー候補: {_fmt_price(entry)}\n"
        f"- 損切り候補: {_fmt_price(stop)}\n"
        f"- 利確候補: {_fmt_price(take)}"
    )


def _discord_post(webhook_url: str, content: str) -> None:
    """
    requests.post で Discord Webhookへ送信します。
    """
    r = requests.post(webhook_url, json={"content": content}, timeout=20)
    r.raise_for_status()


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


def main(argv: list[str]) -> int:
    args = parse_args(argv)

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
    # - Webhook URL が設定されていれば通知する
    # - 「同じ銘柄を連続通知しない」ため、前回ループで候補に入っていた銘柄セットを保持する
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    discord_enabled = bool(webhook_url)
    last_discord_candidate_symbols: set[str] = set()

    # MA25 のキャッシュ:
    # - symbol ごとに (ma25, fetched_at_monotonic) を持ちます
    ma25_cache: dict[str, tuple[float, float]] = {}

    # 出来高5日平均（VOL_AVG5）のキャッシュ:
    avg5_cache: dict[str, tuple[float, float]] = {}

    # VWAP のキャッシュ:
    # - None もキャッシュして、短い時間での再試行を減らします。
    vwap_cache: dict[str, tuple[Optional[float], float]] = {}

    # watchlist.json のリアルタイム反映用:
    # - 前回の監視銘柄リスト（壊れたJSONを読んだ場合に「前回のリストを維持」するため）
    last_watch: list[str] = []

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

                quotes: list[Quote] = []
                for sym in watch:
                    try:
                        q = fetch_quote(session, sym)
                        quotes.append(q)
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

                    # 8) VWAP条件: 取得できれば price > VWAP
                    # 取得できない場合は警告のみで、現時点では条件からは除外しません（＝将来のkabuステーションAPI移行時に
                    # 「VWAPを必須条件」に切り替えやすくするための布石です）。
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
                            print(f"[{now_str()}] {q.symbol} VWAP取得不可（条件除外しない）")
                        else:
                            vwap_by_symbol[q.symbol] = vwap
                            if q.price <= vwap:
                                reasons.append("VWAP以下")
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
                if discord_enabled and candidates:
                    # candidates は list[Quote] なので、symbol 重複が無い前提で扱います（WATCH は通常ユニーク）。
                    to_notify = [q for q in candidates if q.symbol not in last_discord_candidate_symbols]
                    if to_notify:
                        # 見やすさのため、前日比が大きい順に送ります。
                        to_notify_sorted = sorted(
                            to_notify, key=lambda x: x.change_percent or -999, reverse=True
                        )
                        for q in to_notify_sorted:
                            # candidates 条件により day_high は None にならない想定です。
                            # エントリー/損切り/利確候補は「初心者でも理解しやすい簡易ルール」で計算します。
                            # デイトレ方針は人によって違うので、ここは必要に応じて調整してください。
                            #
                            # 簡易ルール:
                            # - エントリー候補: 当日高値（高値更新を狙うイメージ）
                            # - 損切り候補: エントリーの -2%（STOP_LOSS_PCT_FROM_ENTRY）
                            # - 利確候補: エントリーの +4%（TAKE_PROFIT_PCT_FROM_ENTRY）
                            entry = float(q.day_high)
                            stop = entry * (1.0 - STOP_LOSS_PCT_FROM_ENTRY)
                            take = entry * (1.0 + TAKE_PROFIT_PCT_FROM_ENTRY)
                            try:
                                ma25 = ma25_by_symbol.get(q.symbol)
                                if ma25 is None:
                                    # MA25 が無い銘柄は候補に残らない想定ですが、念のためガードします。
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
                                _discord_post(webhook_url, msg)
                            except Exception as e:
                                # 通知に失敗しても監視自体は止めない方が実用的です。
                                print(f"[{now_str()}] Discord通知失敗: {q.symbol} ({e})")

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

