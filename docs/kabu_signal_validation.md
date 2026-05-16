# kabu_signal_v1 検証（Phase 5C・ログ専用）

## 目的

`docs/kabu_signal_design.md` の **kabu_signal_v1** を、Yahoo シグナル（`signal_engine`）とは **別モジュール**で実装する。  
本フェーズは **評価結果のログ・JSON/CSV 保存のみ**。ENTRY 通知・paper_trade・発注には **接続しない**。

| モジュール | 役割 |
|------------|------|
| `src/signal_engine.py` | Yahoo プロファイル（**変更なし**） |
| `src/kabu_signal_engine.py` | kabu_signal_v1（**新規**） |
| `src/kabu_signal_shadow.py` | paper_trade シャドウ（**Phase 5D**） |
| `scripts/kabu_signal_probe.py` | 単体実行・成果物出力 |

---

## 使い方

```powershell
cd <project_root>

# kabu_api_check の JSON のみ（REST スナップショット評価）
python scripts/kabu_signal_probe.py `
  --api-check-json results/kabu_api/20260516/kabu_api_check_9984_1_121846.json `
  --tier B

# 複数 JSON
python scripts/kabu_signal_probe.py `
  --glob "results/kabu_api/20260516/kabu_api_check_*.json" `
  --tier A

# PUSH JSONL あり（rolling_high / push_density 有効）
python scripts/kabu_signal_probe.py `
  --api-check-json results/kabu_api/20260516/kabu_api_check_9984_1_121846.json `
  --push-jsonl results/kabu_push_probe/20260516/push_probe_9984_1_HHMMSS.jsonl `
  --tier A
```

### 出力先（既定）

`results/kabu_signal/YYYYMMDD/`

| ファイル | 内容 |
|----------|------|
| `kabu_signal_probe_<stamp>.json` | サマリ配列 + メタ |
| `kabu_signal_probe_<stamp>.csv` | サマリ 1 行 1 評価 |
| `kabu_signal_probe_<stamp>_timeline.csv` | PUSH 再生時のみ（`--push-jsonl`） |

---

## 入力例

### kabu_api_check JSON

`scripts/kabu_api_check.py` の出力形式:

```json
{
  "meta": { "symbol": "9984", "symbol_key": "9984@1", ... },
  "current_quote": {
    "Symbol": "9984",
    "CurrentPrice": 5745.0,
    "CurrentPriceTime": "2026-05-15T15:30:00+09:00",
    "VWAP": 5861.6951,
    "HighPrice": 6020.0,
    "TradingVolume": 69924000.0,
    "TradingValue": 409873170400.0,
    "BidPrice": 5749.0,
    "AskPrice": 5745.0,
    ...
  },
  "board_excerpt": {
    "Sell1": { "Price": 5749.0, "Qty": 2000.0 },
    "Buy1": { "Price": 5745.0, "Qty": 92800.0 },
    ...
  }
}
```

`flatten_board_dict()` が `current_quote` と `board_excerpt` をマージして評価する。

### PUSH JSONL（任意）

`kabu_push_probe` と同形式: 1 行 1 JSON（PUSH / board 相当）。

---

## 出力例（REST のみ）

市場終了後の板スナップショットでは **鮮度・VWAP 上・PUSH 履歴なし** により `timing_ok=false` になるのが正常。

```json
{
  "profile": "kabu_signal_v1",
  "symbol": "9984",
  "eval_kind": "rest_snapshot",
  "data_mode": "rest_only",
  "current_price": 5745.0,
  "quote_age_sec": 86400.0,
  "spread_bps": 6.96,
  "board_imbalance": 0.61,
  "vwap_distance_pct": -1.99,
  "high_proximity_ratio": 0.954,
  "timing_ok": false,
  "reject_reasons": [
    "G1_FRESHNESS",
    "G3_VWAP_DIST",
    "G4_HIGH_PROXIMITY",
    "REST_ONLY_NO_PUSH_HISTORY",
    "G5_ROLLING_HIGH_UNAVAILABLE",
    "G8_PUSH_DENSITY_UNAVAILABLE",
    "G6_VOLUME_DELTA_UNAVAILABLE"
  ],
  "signal_score": 0,
  "tier": "B",
  "breakout_event": false,
  "notify_breakout_eligible": false,
  "notify_near_eligible": false
}
```

`eval_kind`:

| 値 | 意味 |
|----|------|
| `rest_snapshot` | API JSON のみ・`rest_fallback`（鮮度上限 45 秒） |
| `rest_plus_push_final` | JSONL 再生後 + API 板で最終評価 |
| `push_timeline` | JSONL 各行の評価（timeline CSV） |

---

## 評価項目と reject コード

| 項目 | フィールド | ゲートコード |
|------|------------|--------------|
| 鮮度 | `quote_age_sec` | `G1_FRESHNESS` |
| スプレッド | `spread_bps` | `G2_SPREAD` |
| 板バランス | `board_imbalance` | （スコア加点のみ） |
| VWAP 乖離 | `vwap_distance_pct` | `G3_VWAP_DIST` |
| 高値接近 | `high_proximity_ratio` | `G4_HIGH_PROXIMITY` |
| 短期高値 | `rolling_high_5m` | `G5_*`（要 PUSH 履歴） |
| 出来高 | `volume_delta_30s` | `G6_*` |
| 売買代金 | `trading_value` | `G7_TRADING_VALUE` |
| 出来高累積 | `trading_volume` | `G7B_TRADING_VOLUME` |
| PUSH 密度 | `push_samples_1m` | `G8_PUSH_DENSITY` |
| breakout | `breakout_event` | （状態機械・ログのみ） |
| スコア | `signal_score` | 0〜100 |
| tier | `tier` | A / B / C |

`notify_breakout_eligible` / `notify_near_eligible` は **算出のみ**（Discord には送らない）。

---

## 既知の限界（Phase 5C）

1. **REST 単体では `timing_ok` になりにくい** — PUSH 履歴必須ゲート（G5/G6/G8）が意図的に落ちる。
2. **終場後スナップショット** — `G1_FRESHNESS`・`G3_VWAP_DIST`（終値が VWAP 下）が付きやすい。
3. **Tier C** — スコアは出るが `notify_*_eligible` は Tier A/B のみ true になり得る設計（paper_trade 未接続）。
4. **volume p75 加点** — 30 分以上の PUSH 履歴が無いと出来高ボーナス +15 は付かない。
5. **Yahoo シグナルとの数値一致は目標にしない** — 別プロファイル。

---

## paper_trade シャドウ運用（Phase 5D）

Yahoo 版 paper_trade の **ENTRY / EXIT / Discord は変更しない**。各ポール後に kabu `/board` を取得し `kabu_signal_v1` を評価して **別ディレクトリ**へ保存する。

### 有効化

`.env` または CLI のいずれか:

```env
KABU_SIGNAL_SHADOW=1
KABU_SIGNAL_SHADOW_TIER=B
```

```powershell
python yahoo_kabu_watch.py --paper-trade --kabu-signal-shadow
```

起動ログに次が出れば有効:

```text
[PAPER] KABU_SIGNAL_SHADOW=1 tier=B (kabu_signal_v1 log only — no ENTRY/EXIT/Discord)
```

### 保存先

`results/kabu_signal_shadow/YYYYMMDD/`

| ファイル | 内容 |
|----------|------|
| `shadow_eval_YYYYMMDD.csv` | 1 ポール × 1 銘柄 1 行（追記） |
| `shadow_eval_YYYYMMDD.jsonl` | 同上（JSON Lines） |

**`results/paper_trade/` の CSV には一切書き込まない。**

### 出力列（CSV）

`timestamp`, `poll_ts_jst`, `poll_number`, `symbol`, `current_price`, `signal_score`, `notify_entry_eligible`（= `notify_breakout_eligible` の算出値）, `timing_ok`, `tier`, `reject_reasons`, `quote_age_sec`, `spread_bps`, `board_imbalance`, `vwap_distance_pct`, `high_proximity_ratio`, `push_samples_1m`, `rolling_high_5m`, `trigger_level`, `breakout_event`, `data_mode`, `evaluated_at_utc`

### PUSH 履歴について

- ポールごとに `/board` を `PushHistoryRing` へ追加するため、**60 秒間隔の REST ポール**では `push_samples_1m` は主に 1 前後になりやすい。
- 将来 WebSocket PUSH を別タスクで `ingest_push_message()` へ流せば、設計どおりの密度で評価できる。

### 失敗時

- paper_trade ループは継続する。
- 標準出力: `[KABU_SHADOW] error symbol=9984.T err=...`
- Yahoo の fetch / ENTRY 判定 / Discord には影響しない。

### シャドウ出力例（JSONL 1 行）

```json
{
  "timestamp": "2026-05-16T11:00:05+00:00",
  "poll_ts_jst": "2026-05-16 20:00:04",
  "poll_number": 3,
  "symbol": "9984.T",
  "current_price": 5920.0,
  "signal_score": 0,
  "notify_entry_eligible": false,
  "timing_ok": false,
  "tier": "B",
  "reject_reasons": "G3_VWAP_DIST;G8_PUSH_DENSITY",
  "quote_age_sec": 4.2,
  "spread_bps": 8.1,
  "data_mode": "push_and_rest"
}
```

---

## 次フェーズ（仮想エントリー接続）の条件

| # | 条件 |
|---|------|
| 1 | シャドウ CSV を数日分蓄積し、取引時間中の `timing_ok` / `notify_entry_eligible` が意図どおりか確認 |
| 2 | Tier A/B で `push_samples_1m >= 8` を満たす経路（実 PUSH または高頻度ポール）を確立 |
| 3 | 誤検知率が許容範囲と判断できたら **B2: paper_trade 仮想エントリー**（Yahoo 判定は維持） |
| 4 | デフォルトプロファイル切替は合意後 |

---

## 関連ドキュメント

- [kabu_signal_design.md](kabu_signal_design.md) — 仕様
- [kabu_bar_quality.md](kabu_bar_quality.md) — 1 分足品質（Phase 5A）
