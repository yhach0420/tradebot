# kabu_native shadow 運用

## 目的

Phase 13 で採用した **market_session_plus_B** を、**実発注・旧 yahoo_kabu_watch 非接続** で live 検証する。

| 項目 | 方針 |
|------|------|
| シグナル | `kabu_signal_v1` |
| EXIT | `kabu_exit_v1`（仮想ポジションのみ） |
| ENTRY 時間 | [市場セッション制御](market_session_control.md) **09:05–14:50** |
| BF | `bf_confirm_count=2` |
| 廃止 | `no_entry_until`（09:30 等の時間最適化ゲート） |

## 安全制約（必須）

`configs/shadow.yaml` の `safety` で次を守る。

| フラグ | 既定 | 説明 |
|--------|------|------|
| `order_enabled` / `place_orders` | **false** | 実発注禁止（常時） |
| `legacy_yahoo_watch_enabled` | **false** | 旧 `yahoo_kabu_watch` 非接続 |
| Discord 3 フラグ | **false** | [KABU_PAPER] 仮想売買通知（任意・既定 OFF） |

**発注は絶対に有効化しない。** Discord 仮想売買通知を ON にしても `order_enabled` は **false のまま**。

## Discord 仮想売買通知 [KABU_PAPER]（任意）

旧 Yahoo 系（`DISCORD_WEBHOOK_URL` / `market.yahoo.watch`）とは **完全分離**。

| 項目 | 値 |
|------|-----|
| Webhook 環境変数 | **`KABU_SHADOW_DISCORD_WEBHOOK_URL`**（`.env` リポジトリ直下） |
| 実装 | `kabu_native/src/notify/discord.py` |
| 有効化 | `discord_enabled` + `discord_shadow_notify` + **`discord_paper_trade_notify`** がすべて **true** |

### 通知種別

| 種別 | タイトル | タイミング |
|------|----------|------------|
| ENTRY | `[KABU_PAPER] ENTRY EXECUTED <symbol>` | **仮想ポジション作成時**（`shadow_virtual_entry=true`） |
| EXIT | `[KABU_PAPER] EXIT EXECUTED <symbol>` | **仮想決済時**（`shadow_virtual_exit=true`・`kabu_exit_v1` 成立） |

ENTRY 内容: symbol, symbol_name, entry_price, trigger_level, signal_score, tier, vwap_distance_pct, spread_bps, board_imbalance, stop_price, take_price, note（仮想売買 / 発注なし）

EXIT 内容: symbol, exit_price, entry_price, pnl_pct, pnl_yen_100shares, exit_reason, mfe_pct, elapsed_min, note（仮想決済 / 発注なし）

### 重複防止

- **同一ポジション（symbol + entry_time）で ENTRY 1 回・EXIT 1 回**
- **cooldown:** `discord_cooldown_sec`（既定 300 秒）
- **dedupe:** 上記キーをプロセス内で記録

### 設定例（`configs/shadow.yaml`）

```yaml
discord_enabled: true
discord_shadow_notify: true
discord_paper_trade_notify: true
discord_webhook_env: KABU_SHADOW_DISCORD_WEBHOOK_URL
discord_cooldown_sec: 300
discord_dedupe: true
```

`.env` に Webhook URL を設定:

```env
KABU_SHADOW_DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

エラー時は `[KABU_NOTIFY] error ...` をログに出し、**shadow ループは継続**する。

## 構成

| パス | 役割 |
|------|------|
| `configs/shadow.yaml` | ルール・watchlist・Discord・ポール間隔 |
| `src/shadow/` | config / watchlist / runner |
| `src/notify/` | kabu_native 専用 Discord |
| `scripts/run_shadow.py` | エントリポイント |

## watchlist

| source | 説明 |
|--------|------|
| `morning_screen` | `results/morning_screen/` 最新 CSV の上位 N（既定） |
| `universe` | `universe_intraday_full.csv` 等の passed 銘柄（最大 N） |

## データ取得

1. **REST `/board`**（既定）— 銘柄ごとにポール
2. **PUSH**（任意）— `--use-push` で WebSocket を裏スレッド接続し PUSH 履歴リングへ投入

## 出力

`kabu_native/results/shadow/YYYYMMDD/`

- `shadow_events.csv`
- `shadow_events.jsonl`

主要列: `signal_score`, `breakout_event`, `entry_allowed_session`, `shadow_virtual_entry` / `exit`, `shadow_discord_entry_notified`, `shadow_discord_exit_notified`, `exit_reason`, `bf_confirm_streak`

## 実行前チェック（Phase 15）

平日場中に動かす前に必ず:

```bash
python kabu_native/scripts/check_shadow_safety.py
```

詳細: [shadow_safety.md](shadow_safety.md) → `results/reports/safety_report_YYYYMMDD.json`

## 実行例

```bash
# watchlist 確認のみ
python kabu_native/scripts/run_shadow.py --dry-run

# 2 ポールだけ試す（kabu ステーション起動・.env 必須）
python kabu_native/scripts/run_shadow.py --max-polls 2

# universe 上位 5 銘柄
python kabu_native/scripts/run_shadow.py --watchlist-source universe --top-n 5 --max-polls 3
```

## 環境

- リポジトリ直下 `.env` に `KABU_API_PASSWORD`
- 任意 `KABU_API_BASE`（既定 `http://localhost:18080/kabusapi`）
- Discord 参考通知 ON 時: `KABU_SHADOW_DISCORD_WEBHOOK_URL`（**`DISCORD_WEBHOOK_URL` とは別**）

## 旧系との関係

| 系統 | パス | shadow |
|------|------|--------|
| 旧 Yahoo | `market/yahoo/watch.py` + `DISCORD_WEBHOOK_URL` | **使わない** |
| 旧 bridge | `src/kabu_signal_shadow.py` + paper_trade | **使わない** |
| 新 | `kabu_native/scripts/run_shadow.py` | **こちらのみ** |

## ログ

`logs/runtime/kabu_native_shadow_YYYYMMDD.log`（`[KABU_NOTIFY]` 行あり）
