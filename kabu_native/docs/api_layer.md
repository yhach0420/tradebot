# kabu_native API 層

**対象:** `kabu_native/src/api/`（REST + PUSH）と `kabu_native/scripts/check_api.py`

## 旧系との違い

| 項目 | 旧系（ルート） | 新系（`kabu_native`） |
|------|----------------|------------------------|
| REST クライアント | `src/kabu_api_client.py` | `src/api/rest_client.py` (`KabuNativeRestClient`) |
| PUSH クライアント | `src/kabu_push_client.py` | `src/api/push_client.py` (`KabuNativePushClient`) |
| 接続チェック | `scripts/kabu_api_check.py` | `scripts/check_api.py` |
| 成果物 | `results/kabu_api/YYYYMMDD/` | `kabu_native/results/reports/api_check_*.json` |
| 例外名 | `KabuApiError` | `KabuNativeApiError` |
| リトライ | なし | ネットワーク / 502–504 で指数バックオフ |
| 依存 | 旧 `src` パッケージ | **`kabu_native/src` のみ**（旧ファイルは参照用・未削除） |

旧系 Yahoo コードは **`market/yahoo/`** に集約（エントリ: `python -m market.yahoo.watch`）。ルートの `yahoo_kabu_watch.py` は互換シム。paper_trade / watchdog は新エントリまたはシムのどちらでも可。

## 認証

1. kabuステーションを起動し、API を有効化する。
2. リポジトリ直下 `.env` に **`KABU_API_PASSWORD`** を設定する（`.env.example` 参照）。
3. `KabuNativeRestClient.issue_token()` または `issue_token_from_env()` でトークンを取得する。
4. 以降の REST / `register` はヘッダ **`X-API-KEY`** にトークンを付与する。

**セキュリティ:**

- トークンは **ログ・JSON・エラーメッセージに出力しない**（`check_api.py` は「取得成功」のみ）。
- `redact_secrets()` で HTTP エラー本文からトークン類をマスクする。
- API パスワードをソースに直書きしない。

任意の環境変数:

| 変数 | 意味 |
|------|------|
| `KABU_API_PASSWORD` | API パスワード（必須） |
| `KABU_API_BASE` | REST ベース URL（既定: `http://localhost:18080/kabusapi`） |
| `KABU_EXCHANGE` | 市場コード（`check_api.py` のデフォルト exchange） |

## REST

**モジュール:** `api/rest_client.py`

| API | メソッド | 説明 |
|-----|----------|------|
| `POST /token` | `issue_token(password)` | トークン発行 |
| `GET /board/{symbol@exchange}` | `get_board(symbol_key, token=...)` | 板・現値 |

補助:

- `build_symbol_key("9984", "1")` → `"9984@1"`
- `summarize_board(board)` → `current_quote` + `board_excerpt`（トークンなし要約）

**リトライ:** `max_retries`（既定 3）、`retry_backoff_sec`（既定 0.5s、指数バックオフ）。対象は `requests` のネットワーク例外と HTTP 502/503/504。

## PUSH

**モジュール:** `api/push_client.py`

| 操作 | 説明 |
|------|------|
| `PUT /register` | `KabuNativePushClient.register([(code, exchange), ...])` |
| `PUT /unregister/all` | `unregister_all()` |
| WebSocket | `ws://host/kabusapi/websocket`（`rest_base_to_websocket_url`） |
| メッセージ列 | `async for msg in push.iter_messages():` または `iter_messages_sync()` |

**土日・時間外:** 接続できない場合がある。`push_spec(base_url)` または:

```bash
python kabu_native/scripts/check_api.py --push-spec-only
```

で **WS URL・register エンドポイント・期待フィールド一覧** を JSON に含めて確認できる（トークン不要）。

PUSH 生ログの保存先（将来）: `kabu_native/data/push_jsonl/`（本 Phase では `check_api` は board チェック中心）。

## 保存先

| 種別 | パス |
|------|------|
| API チェック JSON | `kabu_native/results/reports/api_check_YYYYMMDD_HHMMSS.json` |
| エラー時 | 同ディレクトリ `api_check_YYYYMMDD_HHMMSS.error.json` |
| 実行ログ | `logs/runtime/kabu_native_check_api_YYYYMMDD.log`（リポジトリルート） |

`.gitignore` により `results/` 配下の実行結果は原則コミットしません。

## 実行例

リポジトリルートで:

```bash
# REST: トークン + 9984 板（東証=1）
python kabu_native/scripts/check_api.py --symbol 9984

# 検証用ポート
python kabu_native/scripts/check_api.py --symbol 9984 --base-url http://localhost:18081/kabusapi

# PUSH 仕様のみ（休場日でも可）
python kabu_native/scripts/check_api.py --push-spec-only
```

成功時、JSON に `current_quote.CurrentPrice` 等が含まれます。kabuステーション未起動時は `.error.json` が書かれ exit code `1` です。パスワード未設定は exit code `2` です。

## Python からの利用例

```python
from pathlib import Path
import sys

sys.path.insert(0, str(Path("kabu_native/src").resolve()))
from api.rest_client import KabuNativeRestClient, load_kabu_env, build_symbol_key
from api.push_client import KabuNativePushClient

load_kabu_env(repo_root=Path(".").resolve())
rest = KabuNativeRestClient()
token = rest.issue_token_from_env()
board = rest.get_board(build_symbol_key("9984", "1"), token=token)

push = KabuNativePushClient(rest, token)
push.register([("9984", 1)])
async for msg in push.iter_messages(recv_poll_sec=15.0):
    ...  # JSONL に 1 行ずつ書く
```

## 関連ドキュメント

- ルート `docs/kabu_station_setup.md` — 初回セットアップ
- ルート `docs/kabu_response_mapping.md` — 板フィールド対応
- `kabu_native/docs/architecture.md` — 全体フェーズ
