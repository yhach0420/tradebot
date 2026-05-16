# Kabu `MarketDataProvider` 接続検証（Phase 3）

**実施日**: 2026-05-16  
**目的**: Phase 2 の方針どおり、**paper_trade の現値（Quote）だけ** kabu に切り替えられること、**通常の Yahoo 経路を壊していない**こと、**kabu 失敗時の Yahoo fallback**、および**秘密情報の漏洩がない**ことを確認する。

**関連**: paper_trade の取引時間・staleness は [paper_trade_market_hours.md](paper_trade_market_hours.md) を参照。

---

## 検証コマンド

### 1. 構文チェック（`py_compile`）

リポジトリ直下で実行:

```powershell
cd C:\Users\yhach\Documents\tradebotfile
python -m py_compile `
  yahoo_kabu_watch.py `
  scripts\kabu_api_check.py `
  src\kabu_api_client.py `
  src\providers\base_provider.py `
  src\providers\yahoo_provider.py `
  src\providers\kabu_provider.py `
  src\providers\factory.py
```

**補足**: 依頼リストに `scripts/yahoo_kabu_watch.py` が含まれていましたが、本リポジトリでは **`yahoo_kabu_watch.py` がプロジェクト直下**にあり、`scripts/` 配下に同名ファイルは存在しません。上記の **`yahoo_kabu_watch.py`** が実体です。

### 2. プロバイダ解決ロジック（環境変数）

```powershell
cd C:\Users\yhach\Documents\tradebotfile
python -c "import os,sys; sys.path.insert(0,'.'); from requests import Session; import src.providers.factory as f; 
p=f.resolve_paper_trade_quote_provider(Session()); print(type(p).__name__)"
```

`MARKET_DATA_PROVIDER` を変えて `YahooProvider` / `KabuProvider` が出ることを確認します（下表参照）。

### 3. kabu 未起動時の fallback（スタブ）

kabustation なしでも再現できるよう、`KabuApiClient.issue_token` が `KabuApiError` を投げるスタブを注入し、`yahoo_kabu_watch.fetch_quote` をスタブに差し替えて **Yahoo に切り替わったこと**を検証しました（エージェント実行ログで `kabu_quote_fallback` を確認）。

### 4. 手動確認（kabu 起動環境）

ユーザー PC（kabuステーション起動済み）で:

```powershell
# 接続・/board 単体
python scripts\kabu_api_check.py --symbol 9984
```

```powershell
# paper_trade（日中・API有効時の想定）
$env:MARKET_DATA_PROVIDER='kabu'
# KABU_API_PASSWORD は .env に設定
python yahoo_kabu_watch.py --paper-trade --paper-trade-interval 60
```

起動直後の標準出力に **`[PAPER] MARKET_DATA_PROVIDER=kabu`** が出ること。

---

## 検証結果サマリ

| # | 確認内容 | 結果 | 備考 |
|---|----------|------|------|
| 1 | `py_compile` 対象一式 | **OK** | 上記パスで `exit code 0` |
| 2 | `MARKET_DATA_PROVIDER` 未設定 → `YahooProvider`／`fetch_quote` 相当 | **OK** | `factory` が `yahoo` と解釈し `YahooProvider` を返却。`fetch_quote` は `yahoo_provider` が遅延 import で呼び出し |
| 3 | `MARKET_DATA_PROVIDER=yahoo` | **OK** | `kabu` 以外はすべて `YahooProvider`（実装: `factory.resolve_paper_trade_quote_provider`） |
| 4 | `MARKET_DATA_PROVIDER=kabu` → Quote のみ Kabu、1分足/VWAP/MA25 は Yahoo | **OK** | コードレビュー + 実装一致。`fetch_latest_intraday_data_for_paper_trade` は `quote_provider.get_quote` のみ切替え、`fetch_vwap` / `fetch_intraday_1m_series` / `run_paper_trade` 内の `fetch_ma25` は従来どおり Yahoo |
| 5 | kabu 起動中: token・/board 成功、paper ログに provider 相当 | **未実施（手動）** | 本環境に kabu が無くエージェントからは未検証。コマンドと期待挙動を上に記載 |
| 6 | kabu 停止/API 失敗: paper 停止せず Yahoo fallback、`kabu_quote_fallback` | **OK（スタブ）** / **手動推奨** | スタブで `kabu_quote_fallback` 出力を確認。実 API では手動で再確認推奨 |
| 7 | token / `KABU_API_PASSWORD` が CSV/JSON/成果物ログに出ない | **OK（実装確認 + 軽微変更）** | 下記「セキュリティ」参照 |

**総合判定（エージェント実施分）**: **OK**（項目 5 と実 API 系の 6 はユーザー環境での最終確認として **残課題** に記載）

---

## 詳細

### 確認 2・3: `YahooProvider` と `fetch_quote`

- `src/providers/factory.py`: `MARKET_DATA_PROVIDER` が **`kabu`**（前後空白除去・小文字化）のときのみ `KabuProvider`。**未設定・空文字・その他の値はすべて `YahooProvider`**。
- `src/providers/yahoo_provider.py`: `get_quote` が `import yahoo_kabu_watch as yw; yw.fetch_quote(self._session, symbol)` を呼ぶ。

### 確認 4: paper_trade で「Quote だけ kabu」

`fetch_latest_intraday_data_for_paper_trade` は次の通り:

- `quote_provider` があれば `quote_provider.get_quote(symbol)`（kabu 時は `KabuProvider`）
- それ以外は従来どおり `fetch_quote`
- **常に** `fetch_vwap` → `fetch_intraday_1m_series`（Yahoo chart）で intraday・VWAP を構築  
- `run_paper_trade` は引き続き `fetch_ma25(session, sym)` を使用（Yahoo）

### 確認 5: provider 表示

- `run_paper_trade` 起動時に `[PAPER] MARKET_DATA_PROVIDER=<値>` を **標準出力**（＋利用者のコンソールログ）へ出力。

### 確認 6: fallback

- `KabuProvider.get_quote`: `KabuApiError` 時にトークン破棄のうえ 1 回再試行、その後も失敗なら `_yahoo_fallback_log` → `yahoo_kabu_watch.fetch_quote`。  
- `.T` 銘柄でない・`KABU_API_PASSWORD` 未設定 の場合も Yahoo 側へ落とす（ログ内容は条件により異なる）。

### 確認 7: セキュリティ

| 対象 | 内容 |
|------|------|
| `results/kabu_api/*/kabu_api_check_*.json` | **Token 本文は含めない**。`_note_response_keys` 等のみ |
| `scripts/kabu_api_check.py` の **ファイルログ** | **Token 先頭8文字のログを出さない**よう修正（診断でもトークン断片を残さない） |
| `paper_trade_log.csv` / `paper_trade_runtime_state.json` | **kabu の API パスワード・Token は書き込まない**（実装レビュー）。Discord 用の別トークンは従来どおりコード上存在するが、本件スコープ外 |
| `KabuProvider` のコンソール出力 | 例外 `repr` とシンボルのみ。パスワードは出さない |

※ **実データのログファイルをリポジトリにコミットしていない**ため、実行時ログの目視はローカル環境に委ねる。

---

## 自動実行ログ（エージェント）

- `factory_resolve: OK` — 未設定・空・`yahoo`・`YaHoo`・不明値 → `YahooProvider`；`' kabu '`（trim 後）→ `KabuProvider`
- `kabu_fallback_stub: OK` — スタブエラー後に `[PAPER] kabu_quote_fallback symbol=9984.T KabuApiError('simulated kabu down')` を確認

---

## 残課題（ユーザー環境で最終 OK を取りたい項目）

1. **kabuステーション起動中**の E2E  
   - `kabu_api_check` で HTTP 200／JSON 保存  
   - `MARKET_DATA_PROVIDER=kabu` で **paper_trade が 1 ポール以上**回り、異常終了しないこと  
2. **実ネットワーク**での `kabu_quote_fallback`（意図的に誤パスワード・kabu 停止など）の目視  
3. （任意）`paper_trade_runtime_state.json` に `market_data_provider` を書き込み、後から監査しやすくする — **今回は新規機能を増やさず未実施**

---

## 参照実装

- プロバイダ解決: `src/providers/factory.py`
- kabu / fallback: `src/providers/kabu_provider.py`
- paper 用 fetch 分岐: `yahoo_kabu_watch.py` の `fetch_latest_intraday_data_for_paper_trade` および `run_paper_trade`

関連ドキュメント: [kabu_response_mapping.md](kabu_response_mapping.md)
