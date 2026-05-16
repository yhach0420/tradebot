# Phase 15: shadow 安全チェック

## 目的

平日場中に `run_shadow.py` を動かす前に、次を **機械的に確認** する。

- 実 Discord 通知・実発注・旧 `yahoo_kabu_watch` 接続が **無効**
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
| `safety_flags` | `discord_enabled` / `order_enabled` / `legacy_yahoo_watch_enabled` がすべて **false**（`shadow.yaml` の `discord_notify` / `place_orders` / `connect_yahoo_watch` も同義で受理） |
| `no_entry_until_absent` | 設定に `no_entry_until` が無いこと（あれば **警告**、使用しない） |
| `market_session_control` | `rules.market_session_control: true` |
| `output_paths` | `kabu_native/results/shadow/YYYYMMDD/` が作成可能 |
| `watchlist_morning_screen` | morning_screen から銘柄リスト構築 |
| `watchlist_universe` | universe CSV から銘柄リスト構築 |
| `api` | token 発行 + board 取得（**token をファイル保存しない**） |
| `shadow_run` | `--max-polls 1` 相当の 1 ポール（`continue_on_error` 維持） |
| `no_legacy_modules_loaded` | 当該プロセスで旧 shadow / yahoo モジュール未 import |

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

- [shadow.md](shadow.md) — shadow 運用
- [market_session_control.md](market_session_control.md) — ENTRY 09:05–14:50
