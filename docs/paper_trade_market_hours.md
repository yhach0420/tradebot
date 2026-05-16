# paper_trade — 市場時間・休場日・1分足 fresh ガード

`yahoo_kabu_watch.py` の `--paper-trade` に、**東証現物を前提にした取引時間・休場日のスキップ**と、**Yahoo 1分足の最終バー時刻が古い場合の ENTRY 抑止**を追加しました。

## 取引時間（当面・分単位）

| セッション | JST |
|------------|-----|
| 前場 | 09:00〜11:30（含む） |
| 後場 | 12:30〜15:30（含む） |
| 昼休 | 11:31〜12:29（`lunch_break`） |

この時間外（および下記休場日）では、**ENTRY 判定・ENTRY 用 Discord 通知は行いません**（1行ログのみ）。

## 休場

- **土曜・日曜**: `reason=weekend` でスキップ。
- **日本の祝日（平日）**: `jpholiday` で判定し、`reason=holiday` でスキップ。  
  - `pip install -r requirements.txt` で `jpholiday` を入れた環境を前提とします。

## ログ（取引時間外）

```
[PAPER] market_closed skip poll reason=weekend|holiday|before_market_open|lunch_break|after_market_close|outside_session
```

既存の **EOD 専用**（当日 15:30 前後の1回サマリ、`[paper_trade] market closed — idle`）の流れは、**平日の取引終了後**の挙動として維持しています。

## 1分足データが古い場合

Yahoo chart `timestamp` 配列の**最後の要素**を **1分足バー終端（UTC）** とみなし、現在時刻（UTC）との差が `PAPER_TRADE_STALE_INTRADAY_MAX_AGE_SEC`（既定 **900 秒**＝15分）を超えた場合:

- ENTRY 通知は出さない（`reason` に「1分足データが古い」を付与し `skipped=true`）。
- コンソールに次を出力:

```
[PAPER] stale_intraday_data skip symbol=... last_ts=... (age_sec=...|no_timestamp)
```

明示オプションなしで「前日の足のまま ENTRY」になることを防ぎます。

## 明示オプション（検証用）

| フラグ | 意味 |
|--------|------|
| `--paper-trade-force-run` または `--ignore-market-hours` | 土日・祝日・取引時間外でも **ポーリングループ自体**は回す（開発・検証）。**ENTRY は stale チェックの対象**のまま。 |

起動時に次が出力されます:

```
[PAPER] clock_jst=... ignore_market_hours=true|false stale_intraday_max_age_sec=900
```

## 関連コード

- `run_paper_trade(..., ignore_market_hours=...)`
- `_paper_trade_in_regular_session_jst` / `_paper_trade_intraday_is_stale`
- `fetch_intraday_1m_series` が返す `last_bar_end_utc`
