"""
Yahoo Finance（非公式API）で日本株の現在値を監視するツール（発注機能なし）
=============================================================

このスクリプトは、Yahoo Finance の「非公式API」から現在値（regularMarketPrice）を取得して、
指定した価格を上抜けたら「買い候補」として表示します。

注意:
- 非公式APIなので、仕様変更・アクセス制限で動かなくなる可能性があります。
- 取引判断や損益については自己責任でお願いします（本ツールは発注しません）。

要件:
- requests を使う
- 銘柄コードは 7203.T の形式（例: トヨタ = 7203.T）
- 1秒ごとに株価取得
- 指定価格を上抜けたら買い候補として表示
- 発注機能なし
- 1つのPythonファイルで完結

動作確認の目安:
  Python 3.10+（3.9でもたぶん動きます）
  pip install requests
"""

from __future__ import annotations

import argparse
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
    market_time_utc: Optional[datetime]


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

    market_time = q0.get("regularMarketTime")
    market_time_utc = None
    if isinstance(market_time, (int, float)):
        market_time_utc = datetime.fromtimestamp(float(market_time), tz=timezone.utc)

    return Quote(symbol=symbol, price=float(price), currency=str(currency), market_time_utc=market_time_utc)


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

    market_time = meta.get("regularMarketTime")
    market_time_utc = None
    if isinstance(market_time, (int, float)):
        market_time_utc = datetime.fromtimestamp(float(market_time), tz=timezone.utc)

    return Quote(symbol=symbol, price=float(price), currency=currency, market_time_utc=market_time_utc)


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
        "symbol",
        help="銘柄コード（例: 7203.T / 9984.T）",
    )
    p.add_argument(
        "target_price",
        type=float,
        help="上抜け判定する価格（例: 3000）",
    )
    p.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="取得間隔（秒）。デフォルト 1.0",
    )
    p.add_argument(
        "--print-every",
        type=int,
        default=1,
        help="何回に1回、通常ログ（現在値）を表示するか。デフォルト 1（毎回表示）",
    )
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    symbol: str = args.symbol.strip()
    target_price: float = float(args.target_price)
    interval_sec: float = float(args.interval)
    print_every: int = int(args.print_every)

    if not symbol:
        print("symbol が空です。例: 7203.T")
        return 2
    if interval_sec <= 0:
        print("--interval は 0 より大きい値にしてください。")
        return 2
    if print_every <= 0:
        print("--print-every は 1 以上にしてください。")
        return 2

    print("=== Yahoo Finance 日本株 価格監視（発注なし） ===")
    print(f"- symbol: {symbol}")
    print(f"- target_price: {target_price}")
    print(f"- interval: {interval_sec} sec")
    print("- Ctrl+C で終了します。\n")

    # 上抜け検知のために「直前の価格」を覚えておきます。
    # 例: 前回 2999（未達）→今回 3001（達成）なら「上抜け」扱い。
    prev_price: Optional[float] = None

    # 一度上抜けを通知したあと、価格がターゲット以下に戻るまで再通知しないためのフラグ。
    # これがないと、上抜け後にずっと「買い候補」が出続けてうるさくなります。
    already_notified: bool = False

    # requests.Session を使うと、接続の再利用ができて少し効率が良くなります。
    with requests.Session() as session:
        n = 0
        try:
            while True:
                loop_started = time.perf_counter()
                n += 1

                try:
                    q = fetch_quote(session, symbol)
                except requests.HTTPError as e:
                    # 429（Too Many Requests）などが起きたら、間隔を伸ばす等が必要かもしれません。
                    print(f"[{now_str()}] HTTPエラー: {e}")
                    q = None
                except Exception as e:
                    print(f"[{now_str()}] 取得エラー: {e}")
                    q = None

                if q is not None:
                    # 通常ログ（見たい人向け）
                    if n % print_every == 0:
                        mt = q.market_time_utc.isoformat() if q.market_time_utc else "N/A"
                        print(f"[{now_str()}] price={q.price} {q.currency} (market_time_utc={mt})")

                    # 上抜け判定（「前回が target 以下」かどうかがポイント）
                    crossed_up = (
                        prev_price is not None
                        and prev_price <= target_price
                        and q.price > target_price
                    )

                    if crossed_up and not already_notified:
                        print(f"\n[{now_str()}] 買い候補: {symbol}")
                        print(f"  - 現在値: {q.price} {q.currency}")
                        print(f"  - 指定価格: {target_price} を上抜け")
                        print()
                        already_notified = True

                    # いったん上抜け後、価格が target 以下に戻ったら「次の上抜け」で再通知できるようにする
                    if already_notified and q.price <= target_price:
                        already_notified = False

                    prev_price = q.price

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

