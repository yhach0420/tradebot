# Phase 15: shadow 安全チェック

## 目的

平日場中に `run_shadow.py` を動かす前に、次を **機械的に確認** する。

- **実発注・旧 `yahoo_kabu_watch` 接続が無効**
- Discord **[KABU_PAPER] 仮想売買通知**（任意）を使う場合も **発注と同時有効化されていない**
- `no_entry_until` 等の廃止パラメータが残っていない
- watchlist・API・1 ポール実行・CSV/JSONL 出力が問題ない

## 実行

```bash
python kabu_native/scripts/check_shadow_safety.py
```

オフライン一部スキップ:

```bash
python kabu_native/scripts/check_shadow_safety.py --skip-api --skip-run
```

## 確認項目

| ID | 内容 |
|----|------|
| `safety_flags` | **`order_enabled` / `legacy_yahoo_watch_enabled` が false**（`place_orders` / `connect_yahoo_watch` 同義）。Discord 3 フラグは **true でも可**（発注は不可） |
| `discord_order_mutex` | `discord_enabled` + `discord_shadow_notify` + `discord_paper_trade_notify` が ON のとき **`order_enabled` は必ず false** |
| `no_entry_until_absent` | 設定に `no_entry_until` が無いこと（あれば **警告**、使用しない） |
| `market_session_control` | `rules.market_session_control: true` |
| `output_paths` | `kabu_native/results/shadow/YYYYMMDD/` が作成可能 |
| `watchlist_morning_screen` | morning_screen から銘柄リスト構築 |
| `watchlist_universe` | universe CSV から銘柄リスト構築 |
| `api` | token 発行 + board 取得（**token をファイル保存しない**） |
| `shadow_run` | `--max-polls 1` 相当の 1 ポール（`continue_on_error` 維持） |
| `no_legacy_modules_loaded` | 当該プロセスで旧 shadow / yahoo モジュール未 import |

## Discord [KABU_PAPER] 仮想売買通知の安全

| 設定 | 既定 | 許可 |
|------|------|------|
| `discord_enabled` | false | true（明示時のみ） |
| `discord_shadow_notify` | false | true |
| `discord_paper_trade_notify` | false | **3 つすべて true で送信** |
| `discord_webhook_env` | `KABU_SHADOW_DISCORD_WEBHOOK_URL` | 旧 `DISCORD_WEBHOOK_URL` は **使わない** |
| `order_enabled` | false | **true 禁止** |

`check_shadow_safety.py` は **発注 ON + Discord 仮想売買通知 ON** の組み合わせを **不合格** にする。

## レポート

`kabu_native/results/reports/safety_report_YYYYMMDD.json`

- `overall_pass`: 全チェック合格
- `ready_for_weekday_shadow`: 場中 shadow へ進んでよいか

終了コード: **0** = 合格、**1** = 不合格

## 前提

- `configs/shadow.yaml` の `safety` セクションが正しいこと
- API チェック・実行テストには kabu ステーション起動と `.env` の `KABU_API_PASSWORD`
- 実行テストは **実発注しない**（shadow runner の仮想ポジションのみ）

## 合格後

```bash
python kabu_native/scripts/run_shadow.py
```

ログ: `logs/runtime/kabu_native_shadow_YYYYMMDD.log`  
イベント: `kabu_native/results/shadow/YYYYMMDD/shadow_events.{csv,jsonl}`

## 関連

- [shadow.md](shadow.md) — shadow 運用・Discord 参考通知
- [market_session_control.md](market_session_control.md) — ENTRY 09:05–14:50
