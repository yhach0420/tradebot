# kabu_native shadow 運用

## 目的

Phase 13 で採用した **market_session_plus_B** を、**発注・Discord・yahoo_kabu_watch なし** で live 検証する。

| 項目 | 方針 |
|------|------|
| シグナル | `kabu_signal_v1` |
| EXIT | `kabu_exit_v1`（仮想ポジションのみ） |
| ENTRY 時間 | [市場セッション制御](market_session_control.md) **09:05–14:50** |
| BF | `bf_confirm_count=2` |
| 廃止 | `no_entry_until`（09:30 等の時間最適化ゲート） |

## 安全制約（必須）

`configs/shadow.yaml` の `safety` はすべて **false** 固定。起動時に検証する。

- Discord 通知なし
- 発注なし
- `yahoo_kabu_watch.py` 非接続

## 構成

| パス | 役割 |
|------|------|
| `configs/shadow.yaml` | ルール・watchlist・ポール間隔 |
| `src/shadow/` | config / watchlist / runner |
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

主要列: `signal_score`, `breakout_event`, `entry_allowed_session`, `shadow_virtual_entry` / `exit`, `exit_reason`, `bf_confirm_streak`

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

## 旧系との関係

| 系統 | パス | shadow |
|------|------|--------|
| 旧 | `src/kabu_signal_shadow.py` + paper_trade | **使わない** |
| 新 | `kabu_native/scripts/run_shadow.py` | **こちらのみ** |

## ログ

`logs/runtime/kabu_native_shadow_YYYYMMDD.log`
