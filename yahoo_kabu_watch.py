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
    # "9984.T",
]

# ----------------------------
# スクリーニング条件（ここを編集）
# ----------------------------
MIN_CHANGE_PCT = 1.0          # 前日比（%）がこの値以上
MIN_RATIO_TO_DAY_HIGH = 0.98  # 現在値が当日高値の何%以上か（0.98 = 98%）
REQUIRE_VOLUME = True         # 出来高が 0 より大きいことを必須にする

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
    change_percent: Optional[float]  # 前日比（%）
    day_high: Optional[float]        # 当日高値
    volume: Optional[float]          # 出来高（整数が多いが float で受ける）
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
    change_percent = q0.get("regularMarketChangePercent")
    day_high = q0.get("regularMarketDayHigh")
    volume = q0.get("regularMarketVolume")

    market_time = q0.get("regularMarketTime")
    market_time_utc = None
    if isinstance(market_time, (int, float)):
        market_time_utc = datetime.fromtimestamp(float(market_time), tz=timezone.utc)

    return Quote(
        symbol=symbol,
        price=float(price),
        currency=str(currency),
        change_percent=float(change_percent) if isinstance(change_percent, (int, float)) else None,
        day_high=float(day_high) if isinstance(day_high, (int, float)) else None,
        volume=float(volume) if isinstance(volume, (int, float)) else None,
        market_time_utc=market_time_utc,
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
    change_percent = meta.get("regularMarketChangePercent")
    day_high = meta.get("regularMarketDayHigh")
    volume = meta.get("regularMarketVolume")

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

    return Quote(
        symbol=symbol,
        price=float(price),
        currency=currency,
        change_percent=float(change_percent) if isinstance(change_percent, (int, float)) else None,
        day_high=float(day_high) if isinstance(day_high, (int, float)) else None,
        volume=float(volume) if isinstance(volume, (int, float)) else None,
        market_time_utc=market_time_utc,
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


def _build_discord_message(q: Quote, *, entry: float, stop: float, take: float) -> str:
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
        f"- 当日高値: {_fmt_price(q.day_high)}\n"
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


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    interval_sec: float = float(args.interval)
    print_all: bool = bool(args.print_all)
    only_changes: bool = bool(args.only_changes)
    watch_csv: str = str(args.watch or "")
    watch_file: str = str(args.watch_file or "")

    watch: list[str] = list(WATCH)
    if watch_file:
        try:
            watch = _load_watch_from_file(watch_file)
        except Exception as e:
            print(f"--watch-file の読み込みに失敗しました: {watch_file} ({e})")
            return 2
    elif watch_csv:
        watch = _parse_watch_csv(watch_csv)

    if interval_sec <= 0:
        print("--interval は 0 より大きい値にしてください。")
        return 2
    if not watch:
        print("WATCH が空です。ファイル上部の WATCH を編集するか、--watch / --watch-file で指定してください。")
        return 2

    print("=== Yahoo Finance 日本株 スクリーニング（発注なし） ===")
    print(f"- watch: {', '.join(watch)}")
    print(f"- interval: {interval_sec} sec")
    print(f"- 条件: 前日比 +{MIN_CHANGE_PCT}%以上 / 当日高値の {MIN_RATIO_TO_DAY_HIGH*100:.0f}%以上 / 出来高あり={REQUIRE_VOLUME}")
    print("- Ctrl+C で終了します。\n")

    # only_changes 用。直前に出した候補セットを覚えておきます。
    last_candidates: set[str] = set()

    # Discord通知用:
    # - Webhook URL が設定されていれば通知する
    # - 「同じ銘柄を連続通知しない」ため、前回ループで候補に入っていた銘柄セットを保持する
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    discord_enabled = bool(webhook_url)
    last_discord_candidate_symbols: set[str] = set()

    # requests.Session を使うと、接続の再利用ができて少し効率が良くなります。
    with requests.Session() as session:
        try:
            while True:
                loop_started = time.perf_counter()

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

                # 条件判定して「候補だけ」残す
                candidates: list[Quote] = []
                for q in quotes:
                    # 必須項目が欠ける場合は判定できないので除外（非公式APIなので起こり得ます）
                    if q.change_percent is None or q.day_high is None:
                        if print_all:
                            print(f"[{now_str()}] {q.symbol} 判定不可（change%/day_high が取得できない）")
                        continue

                    cond_change = q.change_percent >= MIN_CHANGE_PCT
                    cond_high = q.price >= (MIN_RATIO_TO_DAY_HIGH * q.day_high)

                    cond_volume = True
                    if REQUIRE_VOLUME:
                        # volume が None のときは「出来高あり」を満たせない扱いにします
                        cond_volume = (q.volume is not None) and (q.volume > 0)

                    if cond_change and cond_high and cond_volume:
                        candidates.append(q)
                    elif print_all:
                        # デバッグ用: なぜ落ちたかをざっくり見られるようにします
                        v = "N/A" if q.volume is None else str(int(q.volume))
                        print(
                            f"[{now_str()}] {q.symbol} NG "
                            f"(price={q.price}, chg%={q.change_percent:.2f}, high={q.day_high}, vol={v})"
                        )

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
                            print(
                                f"  - {q.symbol}: price={q.price} {q.currency}, "
                                f"chg%={q.change_percent:.2f}, "
                                f"day_high={q.day_high} (ratio={ratio*100:.2f}%), "
                                f"vol={v}, time_utc={mt}"
                            )
                        print()
                    else:
                        print(f"[{now_str()}] 条件一致: 0 銘柄")

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
                                msg = _build_discord_message(q, entry=entry, stop=stop, take=take)
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

