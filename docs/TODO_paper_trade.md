# Paper trade — 未実装・低優先 TODO

本ドキュメントは **paper trade 周辺**について、(1) **次回運用のチェックリスト**、(2) **未実装のまま意図的に残す低優先 TODO** をまとめる。  
設計仕様は `docs/DESIGN.md`、全体ロードマップは `docs/TODO.md` と分離する。

---

## 次回ペーパートレード TODO（運用チェックリスト）

引け後などに `paper_trade_health_report.txt` / `paper_trade_runtime_state.json`（当日ディレクトリ）と照合する。

### 起動

```text
python yahoo_kabu_watch.py --paper-trade --paper-trade-force-start --paper-trade-dynamic-watchlist --paper-trade-opening-light --replay-config configs/replay_full_day_vwap2_dd30k_rlt50_hu2_vwap15.json
```

### 最重要確認項目

#### 1. poll / 遅延

確認する指標:

- `avg_poll_duration_sec`
- `max_poll_duration_sec`
- `signal_lag_sec`（および集計上の max 系があれば併記）
- `stale_signal_count`
- `fetch_parallelism_last`

目標:

- `avg_poll_duration_sec` が **10〜15秒台**
- `stale_signal_count` が **0**
- **`max_signal_lag_sec`（または同等の最大 lag 指標）が 30 秒以下**

#### 2. OPEN / CLOSE

確認:

- 同銘柄 **重複 Entry 抑止**
- **OPEN 維持**
- **CLOSE 通知**
- **runtime_state 保存**

見る指標:

- `suppressed_open_signal_count`
- `opened_positions_count`
- `closed_positions_count`

#### 3. Exit 品質

確認:

- `take_hit_count`
- `resistance_take_hit_count`
- `vwap_break_exit_count`
- `early_weak_exit_count`
- `take_adjust_trigger_count`

見るポイント:

- `EARLY_WEAK_EXIT` が多すぎないか
- `TAKE_ADJUST` が実戦でも発火するか
- 構造 Take が自然か

#### 4. Structure Take

確認:

- `take_selected_by`
- `take_exit_kind`
- `structure_take_selection`
- `nearest_resistance`
- `structure_take_best_rr`

見るポイント:

- 「壁手前 Take」になっているか
- 利幅が極端に小さすぎないか

#### 5. 低 RR suppress

確認:

- `low_structure_rr_suppressed_count`
- `dynamic_rr_fallback_count`
- `dynamic_rr_fallback_reason_counts`

見るポイント:

- 伸び代不足銘柄を除外できているか
- 強い銘柄まで落としていないか

### 明日以降の改善候補（今回は様子見）

- structure quality 分類（StrongStructure / MediumStructure / WeakStructure）
- `STRUCTURE_RELAXED` の細分化
- 超短距離 structure take の除外
- `TAKE_ADJUST` の高度化
- trailing stop / partial take

### 現時点の状態（メモ）

かなり実戦型に近づいている。

特に改善した点:

- 固定 4% 利確をほぼ排除
- 構造 Take 中心へ移行
- OPEN 重複抑止
- VWAP Exit
- `EARLY_WEAK` 誤発動抑制
- 低 RR suppress
- Tier2 最適化
- poll 高速化

---

## 現在の優先（低優先 TODO セクションは意図的に後回し）

**今すぐやること（優先）**は次のとおりである。  
そのため、本ページの TODO は **後回し（intentionally deferred）** とする。

- **Tier2 スコア強化**
- **poll 高速化**
- **POLL_TIMING_MISS 削減**
- **OPEN / CLOSE 安定化**

上記が一段落するまで、本ページの実装・仕様変更は着手しない（必要なら `docs/TODO.md` の運用セクションと整合を取る）。

---

## TODO 一覧（優先度：低 → 中の順で並べる）

優先度の目安: **P4** = 最後 / **P3** = 将来必須だが今は不要 / **P2** = UX・一貫性 / **P1** = 戦略コアに近いが今フェーズ外

---

### 1. FLAT 状態ラベルの明示

| 項目 | 内容 |
|------|------|
| **目的** | 未保有時も **position 状態が機械可読**になるよう統一し、ログ解析・ヘルスレポート・外部ツール連携を単純化する。 |
| **現状** | 未保有時は CSV 上で該当フィールドが **空** のまま（FLAT を明示する列・値がない、または空表現）。 |
| **未実装理由** | 当面は signal / skip / shadow の観測が主で、**ポジション状態の正規化**は Tier2・poll 安定化より優先度が低い。 |
| **想定設計** | 例: `position_status` 列を追加し、常に `FLAT` / `OPEN` / `CLOSED` 等の列挙で出力。未保有・当日未エントリーでも `FLAT` を1行またはヘッダ付きスナップショットで出す方針を決め、CSV と summary の両方で揃える。 |
| **優先度** | **P2**（UX・一貫性。戦略ロジック非依存） |

---

### 2. TAKE_ADJUST mode C（「次の弱い足で成行決済」）

| 項目 | 内容 |
|------|------|
| **目的** | 利確調整を **足の質（弱い足）に基づいて** 遅延成行決済し、急いだ成行より不利滑りを抑える。 |
| **現状** | **reason 文字列のみ** で mode C の意図が示される程度。実際の「弱い足」の判定ロジックと、その足での **成行 Exit 実行**は未実装。 |
| **未実装理由** | 弱い足定義（実体・出来高・VWAP 位置など）の合意と、paper trade の **タイム軸・約定モデル**が必要。今は POLL / OPEN-CLOSE 安定が先。 |
| **想定設計** | (1) 「弱い足」の判定関数（例: 陰線かつ前足比出来高減など）を config 化。(2) mode C トリガー後、次の該当足の open または close で仮想成行を1回だけ発火。(3) CSV に `take_adjust_mode`, `exit_bar_ts`, `weak_bar_reason` を記録。 |
| **優先度** | **P3**（戦略寄りだが、基盤安定後でないと検証が歪む） |

---

### 3. 高度な trailing execution

| 項目 | 内容 |
|------|------|
| **目的** | ボラティリティや部分利確に応じて **exit を細かく最適化**し、replay / paper で同じルールを検証可能にする。 |
| **現状** | 基本的な exit（例: VWAP 割れ・固定割合など）中心。**ATR trailing / partial take / scaling out / volatility adaptive stop** は未実装またはスコープ外。 |
| **未実装理由** | パラメータ空間が広く **過学習・再現性検証コスト**が高い。まず poll・タイミング・OPEN/CLOSE の再現性を固める。 |
| **想定設計** | trailing は ATR 倍率を config 化。partial は 段階的な `target_pct` と `remaining_qty` を paper の仮想建玉で管理。vol adaptive は regime または短期 realized vol で stop 距離をスケール。いずれも **replay と同一式**を前提にする。 |
| **優先度** | **P4**（execution 複雑度が高く、現フェーズの「観測・安定化」とは別ライン） |

---

### 4. 自動売買 execution layer

| 項目 | 内容 |
|------|------|
| **目的** | 証券 API 経由で **実注文・約定・取消・再接続**を安全に扱う本番用レイヤー。 |
| **現状** | paper trade は **実注文なし**。注文 ID 管理、部分約定、cancel/replace、API reconnect の本番レイヤーは未実装。 |
| **未実装理由** | ロジック・観測・forward 耐性が固まる前に execution を足すと、**不具合切り分けが困難**。意図的に後段。 |
| **想定設計** | 注文状態機械（NEW → PARTIAL → FILLED / CANCELED）、冪等キー、リトライとレート制限、paper と同じシグナル ID で **ドライラン → 小ロット**の段階導入。Discord は監査ログ用に残す。 |
| **優先度** | **P4**（最終段。paper・shadow・AUTO_BLOCK 方針確定後） |

---

## メンテナンス

- 本リストから項目を **実装済み**にした場合は、**目的・完了日・PR/コミット参照**を1行追記してから削除または `docs/TODO.md` へ移す。
- 優先度を上げる場合は、**必ず「なぜ今か」**（Tier2 / poll / POLL_TIMING_MISS / OPEN-CLOSE のどれが緩んだか）を1行書く。
