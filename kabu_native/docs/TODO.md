# kabu_native TODO 管理

**最終更新:** 2026-05-17  
**目的:** 完了状況・Yahoo 依存・次の実装優先度を一覧で追えるようにする。

---

## 1. プロジェクト概要

### kabu_native の目的

kabuステーション® API（REST / PUSH）を **一次データ源** とするデイトレ支援の新スタック。銘柄 universe・朝スクリーニング・リプレイ検証・shadow（発注なし live）を `kabu_native/` 配下に集約し、旧系を壊さず段階的に移行する。

### 旧 `yahoo_kabu_watch.py` との違い

| 項目 | 旧系（ルート） | kabu_native（新系） |
|------|----------------|---------------------|
| データ源 | Yahoo 非公式 API | kabu REST / PUSH |
| エントリ | `yahoo_kabu_watch.py` | `scripts/run_shadow.py` 等 |
| 銘柄リスト | `watchlist.json`（手動） | `data/universe/` + morning_screen |
| シグナル | `signal_engine`（Yahoo プロファイル） | `kabu_signal_v1`（ルート `src/kabu_signal_engine.py` 参照） |
| 成果物 | `results/`（ルート） | `kabu_native/results/` |
| 発注・Discord | paper_trade / 通知あり | **無効**（shadow は仮想ポジションのみ） |

旧系は **削除・大規模移動しない**。並行運用。

### 方針: 「リアルタイムは kabu native、履歴は段階的に自前化」

1. **場中（realtime）** — kabu `/board`（＋任意 PUSH）で判定。shadow / 将来 live は Yahoo を使わない。
2. **履歴（replay）** — 当面は旧系 `data/intraday_1m/`（Yahoo 由来 CSV）を read-only 参照し、合成 kabu board イベントで再生。
3. **自前化の順** — PUSH JSONL 常時保存 → 自前 1分足 → replay 入力の kabu 正規化（Phase 16 候補）。

---

## 2. 完了済みフェーズ

### Phase 1 — API 層

| 項目 | 内容 |
|------|------|
| **状態** | **DONE** |
| **実装内容** | `KabuNativeRestClient` / `KabuNativePushClient`、トークン発行、板取得、PUSH register・WS、リトライ・秘密情報マスク |
| **成果** | `check_api.py` で接続・板要約を JSON 保存可能 |
| **関連ファイル** | `src/api/rest_client.py`, `src/api/push_client.py`, `scripts/check_api.py`, `docs/api_layer.md` |
| **主要な学び** | 旧 `src/kabu_api_client.py` とは別パッケージとして新系に閉じる。土日・時間外は PUSH 不可のため `--push-spec-only` で仕様確認 |

---

### Phase 2 — Universe

| 項目 | 内容 |
|------|------|
| **状態** | **DONE** |
| **実装内容** | YAML 閾値 + kabu `/board` 実測で passed / 除外理由を CSV・JSON 出力 |
| **成果** | `universe_20260516.csv`（3 passed）、`universe_intraday_full.csv`（27 銘柄・リプレイ用） |
| **関連ファイル** | `src/universe/`, `scripts/build_universe.py`, `configs/universe.yaml`, `docs/universe.md` |
| **主要な学び** | 旧 `watchlist.json` は触らない。銘柄コード正規化は `symbols.py` に集約 |

---

### Phase 3 — Morning screen

| 項目 | 内容 |
|------|------|
| **状態** | **DONE** |
| **実装内容** | universe passed 銘柄を kabu board で 10 項目スコアリング、上位 N を出力 |
| **成果** | `results/morning_screen/20260516/`（例: 3 銘柄通過ランキング） |
| **関連ファイル** | `src/screening/morning_screen.py`, `scripts/run_morning_screen.py`, `configs/morning_screen.yaml`, `docs/morning_screen.md` |
| **主要な学び** | 旧 `--morning-screen` とは別パイプライン。shadow の watchlist ソースとして利用可能 |

---

### Phase 4 — Replay（基盤）

| 項目 | 内容 |
|------|------|
| **状態** | **DONE** |
| **実装内容** | intraday 1m CSV → 合成 PUSH → `kabu_signal_v1` + `kabu_exit_v1` バッチリプレイ、集計 CSV/JSON |
| **成果** | `run_replay.py`、日次・銘柄・トレードサマリ、`skipped_inputs.csv` |
| **関連ファイル** | `src/replay/runner.py`, `src/replay/intraday.py`, `src/replay/metrics.py`, `scripts/run_replay.py`, `configs/replay.yaml`, `docs/replay.md` |
| **主要な学び** | エンジン本体はルート `src/kabu_signal_replay.py` を参照（移動未実施）。入力は Yahoo CSV が主 |

---

### Phase 5 — Intraday 在庫監査

| 項目 | 内容 |
|------|------|
| **状態** | **DONE** |
| **実装内容** | `data/intraday_1m` の日付×銘柄在庫を走査・集計 |
| **成果** | 20 営業日 × 27 銘柄 = 540 CSV（2026-04-10〜05-15）。3 月データなし |
| **関連ファイル** | `scripts/audit_intraday_data.py`, `docs/data_inventory.md`, `results/reports/intraday_inventory_20260516.*` |
| **主要な学び** | `kabu_native/data/intraday_1m/` は空。実データは旧系 `data/intraday_1m/`（Yahoo 由来） |

---

### Phase 6 — 構造分析（小規模リプレイ）

| 項目 | 内容 |
|------|------|
| **状態** | **DONE** |
| **実装内容** | 少数銘柄リプレイ結果の構造分析（BF 比率・集中度フラグ等） |
| **成果** | 2 銘柄・20 trades で **9984 損失集中**（`pnl_concentrated_in_one_symbol`）を検出 |
| **関連ファイル** | `scripts/analyze_replay_results.py`, `docs/structure_analysis.md`（Phase 6 表） |
| **主要な学び** | 単銘柄最適化ではなく構造単位で見る。9984 単体では一般化できない |

---

### Phase 7 — 全銘柄リプレイ

| 項目 | 内容 |
|------|------|
| **状態** | **DONE** |
| **実装内容** | `universe_intraday_full.csv`（27 銘柄）× 在庫期間のフルリプレイ + 構造分析 |
| **成果** | 83 trades、10/27 銘柄で発生、total_pnl **-70.34%**、BF exit **76%** |
| **関連ファイル** | `results/replay/20260516/replay_20260516_221700/`, `results/reports/structure_analysis_20260516.*`, `docs/structure_analysis.md` |
| **主要な学び** | 9984 シェアは ~48% に低下するが依然最大損失銘柄。**構造的問題（BF 過剰・寄り直後ノイズ）が主因** |

---

### Phase 8 — パラメータスイープ

| 項目 | 内容 |
|------|------|
| **状態** | **DONE** |
| **実装内容** | 27 銘柄共通ルールの OFAT スイープ（EXIT 窓・BF confirm・hard_stop 等） |
| **成果** | `bf_confirm_count=2` が損失・MFE 改善に有効。`no_entry_until` 系は trades 減のみの候補もあり |
| **関連ファイル** | `src/replay/sweep_runner.py`, `scripts/run_phase8_sweep.py`, `docs/phase8_sweep.md`, `results/reports/phase8_sweep_20260516.*` |
| **主要な学び** | `trades` 下限で「trade 数だけ減らした改善」を除外するルールを導入 |

---

### Phase 9 — ENTRY 品質分析

| 項目 | 内容 |
|------|------|
| **状態** | **DONE** |
| **実装内容** | MFE / MAE / breakout 継続率 / hold 時間で candidate と baseline を比較 |
| **成果** | **candidate_b（bf_confirm=2）** が MFE・継続率を大幅改善（trade 数減だけではない） |
| **関連ファイル** | `src/replay/entry_quality.py`, `scripts/run_phase9_entry_quality.py`, `docs/phase9_entry_quality.md`, `results/reports/phase9_entry_quality_20260516.*` |
| **主要な学び** | 寄りゲート（09:30 等）はノイズ trade 削除に効くが、**時間最適化としての採用は Phase 13 で見送り** |

---

### Phase 10 — 組み合わせ候補

| 項目 | 内容 |
|------|------|
| **状態** | **DONE** |
| **実装内容** | B（bf_confirm=2）+ 寄りゲート（A: 09:30 / C: 09:15）の組み合わせ検証 |
| **成果** | **A_plus_B** が最良（46 trades, total_pnl **-28.81%**, PF 0.075）— ただし 09:30 ゲートは過学習寄り |
| **関連ファイル** | `src/replay/combined_candidates.py`, `scripts/run_phase10_combined_candidates.py`, `docs/phase10_combined_candidates.md`, `results/reports/phase10_combined_candidates_20260517.*` |
| **主要な学び** | 数値は良いが **特定時刻最適化** のため shadow 正式採用には使わない（Phase 13 参照） |

---

### Phase 11 — Screen × Replay 統合

| 項目 | 内容 |
|------|------|
| **状態** | **DONE** |
| **実装内容** | A+B 固定で universe 全体 vs walk-forward top-N screen のリプレイ比較 |
| **成果** | top5/10 で PnL **-10%** vs universe **-28.8%**（流動性 proxy）。9984 偏重は **減らない** |
| **関連ファイル** | `src/replay/screen_replay.py`, `scripts/run_phase11_screen_replay.py`, `docs/phase11_screen_replay.md`, `results/reports/phase11_screen_replay_20260517.*` |
| **主要な学び** | screen は品質改善するが超高流動性バイアス。live morning_screen との差は shadow で要確認 |

---

### Phase 13 — 市場セッション制御

| 項目 | 内容 |
|------|------|
| **状態** | **DONE** |
| **実装内容** | `no_entry_until` 廃止 → **ENTRY 09:05–14:50**（市場制度ベース）+ bf_confirm=2 |
| **成果** | `market_session_plus_B`: 66 trades, total_pnl **-48.56%**（baseline -70.34% より改善、A+B よりは悪いが過学習回避） |
| **関連ファイル** | `src/replay/session_control.py`, `scripts/run_phase13_session_control.py`, `configs/session_control.yaml`, `docs/phase13_session_control.md`, `docs/market_session_control.md` |
| **主要な学び** | **shadow 正式ルールは Phase 13 採用**（Phase 10 の 09:30 ゲートは採用しない） |

---

### Phase 14 — Shadow

| 項目 | 内容 |
|------|------|
| **状態** | **DONE** |
| **実装内容** | REST ポール（任意 PUSH）、仮想 ENTRY/EXIT、イベント CSV/JSONL。発注・Discord・yahoo 非接続 |
| **成果** | `run_shadow.py` 動作確認済み（2026-05-17 ログ: 3 銘柄 watchlist 等） |
| **関連ファイル** | `src/shadow/`, `scripts/run_shadow.py`, `configs/shadow.yaml`, `docs/shadow.md` |
| **主要な学び** | シグナル/EXIT はルート `src/kabu_*_engine.py` を import。`src/signals/` は未移植 |

---

### Phase 15 — Shadow 安全チェック

| 項目 | 内容 |
|------|------|
| **状態** | **DONE** |
| **実装内容** | safety フラグ・`no_entry_until` 不在・watchlist・API・1 ポール・legacy import 未使用を機械検証 |
| **成果** | `check_shadow_safety.py` → `results/reports/safety_report_YYYYMMDD.json` |
| **関連ファイル** | `scripts/check_shadow_safety.py`, `docs/shadow_safety.md` |
| **主要な学び** | 平日場中 shadow の前に必ず実行。token はファイル保存しない |

---

### 未着手・スキップ（参考）

| Phase | 状態 | メモ |
|-------|------|------|
| Phase 12 | **未定義** | ドキュメント・スクリプトなし |
| `src/signals/` 移植 | **未着手** | `.gitkeep` のみ。エンジンはルート `src/` 参照 |
| `src/storage/` | **未着手** | PUSH/minute bar 永続化は Phase 16 候補 |

---

## 3. 現在の状態（重要）

| 項目 | 状態 |
| ------------- | ------------ |
| realtime 判定 | **kabu native**（shadow: REST `/board`、任意 `--use-push`） |
| replay ロジック | **ほぼ統一**（`kabu_signal_v1` + `kabu_exit_v1`、ルート replay モジュール参照） |
| replay 入力 | **Yahoo 由来 CSV あり**（`data/intraday_1m/` → 合成 board） |
| PUSH 蓄積 | **未完成**（`data/push_jsonl/` は `.gitkeep` のみ） |
| 自前 minute bars | **未完成**（`kabu_native/data/intraday_1m/` 空） |
| Discord 通知 | **無効** |
| 発注 | **無効** |
| shadow | **動作可能**（Phase 13 ルール、`check_shadow_safety.py` 推奨） |

### 採用中のルール（shadow / replay 検証の正）

- **ENTRY 枠:** 09:05–14:50 JST（`market_session_control`）
- **EXIT:** `bf_confirm_count=2`, `fail_buffer_pct=0.12`, tier B
- **廃止:** `no_entry_until`（09:30 等の時間最適化ゲート）
- **watchlist:** `morning_screen` 上位 N（shadow 既定 top 10）

---

## 4. 残っている Yahoo 依存

| 依存箇所 | 内容 | realtime への影響 |
|----------|------|-------------------|
| **intraday_1m CSV** | 旧系 `data/intraday_1m/YYYY-MM-DD/<symbol>.csv`（Yahoo EOD 保存） | **未使用** |
| **replay 履歴** | 上記 CSV を `push_messages_from_yahoo_df` で合成 kabu イベント | **未使用** |
| **過去 1 分足** | 約 20 営業日 × 27 銘柄（2026-04-10〜05-15）。3 月なし | **未使用** |
| **symbol 表記** | replay / watchlist で `.T`  suffix 変換（Yahoo 慣習） | 表示・パスのみ |

**まとめ:** Yahoo は **履歴リプレイの入力** にのみ残る。場中 shadow の板・判定には使わない。

### データ在庫（2026-05-16 監査時点）

- 有効: **540** symbol-days（27 × 20 日）
- `kabu_native/data/intraday_1m/`: **0 ファイル**
- 3 月分: **リポジトリ内に存在しない**

---

## 5. 今後の優先 TODO

### HIGH

| TODO | 目的 | 備考 |
|------|------|------|
| **PUSH JSONL 常時保存** | kabu PUSH 生ログの永続化 | `data/push_jsonl/` + `src/storage/`（未実装） |
| **minute bars 自動生成** | PUSH/REST から確定 1 分足を構築 | `kabu_native/data/intraday_1m/` を正とする |
| **market data recorder** | 場中の board/PUSH を欠損なく記録 | shadow と並走または専用プロセス |
| **replay 入力の自前化** | Yahoo CSV 参照を段階的に廃止 | 自前 JSONL / 1m 足が十分溜まってから切替 |

### MEDIUM

| TODO | 目的 |
|------|------|
| **PUSH replay** | 保存 JSONL からのリプレイ経路（`runner.py` 拡張） |
| **replay 高速化** | 540 symbol-days キャッシュ・並列の最適化 |
| **screen 改善** | live `morning_screen` と walk-forward proxy の差分検証 |
| **universe 拡張** | `include_symbols` 拡大・ETF 除外・流動性閾値の見直し |

### LOW

| TODO | 目的 |
|------|------|
| **Discord 通知** | shadow とは分離した通知チャネル（安全フラグ維持） |
| **発注連携** | API 調査・限定発注（本番自動化は非目標のまま） |
| **GUI / monitoring** | ランタイム可視化・watchdog 連携 |

---

## 6. 設計原則（重要）

リプレイ改善・ルール採用時は **必ず** 以下を守る。

1. **特定銘柄最適化禁止** — 9984 等1銘柄向けパッチで全体を改善したと見なさない。
2. **特定日最適化禁止** — 1 日・数日出しの成績だけで採用しない。
3. **特定時刻最適化禁止** — `no_entry_until=09:30` 等、寄り後の「都合の良い時刻」ゲートは shadow 正式ルールにしない（Phase 13 で廃止済み）。
4. **市場構造ベースのみ許可** — 例: 寄り板安定化（09:05 以降）、大引け前 ENTRY 停止（14:50）、BF confirm（イベント品質）。
5. **全銘柄 replay で改善確認** — 原則 `universe_intraday_full.csv`（27 銘柄）× 在庫期間。
6. **trades 減少だけの改善は禁止** — `trades < max(45, baseline×0.55)` 等で除外（Phase 8 ルール）。
7. **PF / MFE / 継続率も確認** — total_pnl だけでなく、ENTRY 品質（Phase 9）と EXIT 内訳を見る。

---

## 7. 次フェーズ候補

### Phase 16 — Market data & 完全自前 replay

| コンポーネント | 内容 |
|----------------|------|
| **market data recorder** | 場中 REST/PUSH の継続記録 |
| **push_jsonl 永続化** | `data/push_jsonl/YYYYMMDD/<symbol>.jsonl` |
| **minute bar builder** | 確定 1 分足 → `kabu_native/data/intraday_1m/` |
| **完全自前 replay** | Yahoo 合成経路をフォールバック化し、PUSH/自前 1m を正とする |

#### 完了条件（Phase 16）

- [ ] 進捗が時系列で追える（recorder ログ・日次在庫 audit）
- [ ] 今後の優先順位が明確（本 doc §5 と整合）
- [ ] Yahoo 依存がどこに残っているか分かる（§4 更新）
- [ ] 過学習防止原則が明文化されている（§6 — 変更時も維持）

#### 推奨実装順

1. PUSH JSONL recorder（shadow と共存、書き込みのみ）
2. minute bar builder（EOD バッチ）
3. `audit_intraday_data.py` で新系パスを primary に
4. replay `data_roots` で kabu 正 → Yahoo フォールバック
5. PUSH 直接リプレイ（十分な JSONL が溜まった後）

---

## 関連ドキュメント

| ファイル | 内容 |
|----------|------|
| [architecture.md](architecture.md) | レイヤ構成・データフロー |
| [directory_structure.md](directory_structure.md) | パス規約 |
| [data_inventory.md](data_inventory.md) | intraday 在庫詳細 |
| [shadow.md](shadow.md) | shadow 運用 |
| ルート `docs/kabu_signal_design.md` | `kabu_signal_v1` 設計 |

---

## 変更履歴

| 日付 | 内容 |
|------|------|
| 2026-05-17 | 初版作成（Phase 1–15 完了状況・Yahoo 依存・Phase 16 候補） |
