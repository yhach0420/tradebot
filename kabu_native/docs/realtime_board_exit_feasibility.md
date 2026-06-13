# Phase334 — 保有中リアルタイム板監視 EXIT 実装可能性レビュー

**更新:** 2026-06-09  
**前提:** [board_data_inventory.md](board_data_inventory.md) · Phase332 board-dynamic trailing（**ENTRY 時点**の percentile で trailing パラメータを固定）

---

## 1. 結論（Executive Summary）

| 観点 | 判定 |
|------|------|
| **データ取得** | **可能** — 保有銘柄は既に PUSH 登録済み（最大 50）で、板更新は `_process_push_payload` に届いている |
| **追加 API コスト** | **不要（PUSH 経路）** — 新規 register なしで、既存ストリームから `calc_board_imbalance` 可能 |
| **実装コスト** | **小〜中** — `observer.on_tick` への板状態追加と EXIT ルール配線（既存研究コードの再利用可） |
| **本番適用** | **要 shadow 検証** — Phase332 は entry-frozen tier のみ採用済み；リアルタイム板 EXIT は未検証 |

**総合:** 保有中リアルタイム板監視による EXIT は **技術的に実装可能**。marginal なデータ取得コストはほぼゼロ（PUSH 既契約）。リスクはルール設計・バックテスト・本番 EXIT との相互作用。

---

## 2. 現状アーキテクチャ

### 2.1 板が使われている箇所（ENTRY のみ）

```
PUSH → compute_entry_order_book_imbalance_field
     → entry_imbalance_percentile（accept 時凍結）
     → Phase332 trailing_mfe_params(percentile)  # board_high / board_low
```

- `entry_imbalance_percentile >= 47.62` → activate 1.0%, giveback 60%
- `< 47.62` → activate 0.6%, giveback 40%
- **保有中の板変化は trailing 閾値に影響しない**

### 2.2 板が使われていない箇所（HOLD）

`observer_position_tracker.on_tick`:

- 入力: `current_price`, `trade`（quality 系）, `payload`（渡されるが板未参照）
- EXIT: `combined_exit_signal_on_latest_tick` — 価格・時間・hard_stop・trailing MFE
- `LiveFeatureBridge` も `CurrentPrice` / `VWAP` のみ

### 2.3 参考実装（既存だが本番未配線）

| 実装 | 経路 | リアルタイム板 EXIT |
|------|------|---------------------|
| `shadow/runner.py` | REST 15s ポール | `imbalance_low_streak` + `board_imbalance_deterioration` |
| `src/kabu_exit_engine.py` | 汎用 EXIT v1 | `imb_low_streak_required` + `imb_exit_max` |
| `microstructure_runtime.py` | リプレイ研究 | `imbalance_collapse_streak`（entry 比 -Δ） |

---

## 3. 実装可能な経路

### 3.1 推奨: PUSH パス（ゼロ追加 API）

**根拠:**

- `max_concurrent_positions: 3` — 全ポジションは dynamic50 ユニバース内 → **既に PUSH 登録済み**
- `pilot_runner` は保有中も同じ `_process_push_payload` を呼ぶ（L920–937）
- `payload` / `enriched` に `BidQty`, `AskQty`, `Buy1`–`Sell10` が含まれる（push_jsonl 実測 100%）

**変更イメージ:**

```text
_process_push_payload (既存)
  └─ observer.on_tick(..., payload=enriched)
       └─ [NEW] imb = calc_board_imbalance(payload)
       └─ [NEW] pos.imbalance_low_streak 更新
       └─ [NEW] board_exit ルール評価（shadow または production）
```

**工数目安:** 1–2 モジュール（例: `board_realtime_exit_shadow.py`）、`observer_position_tracker` への配線、テスト。

### 3.2 代替: REST ポーリング（非推奨・本番 small paper では不要）

- shadow runner パターン: 保有銘柄のみ 5s ポール → 最大 3 銘柄 × 約 4,000 req/セッション
- レジスト 50 上限と HTTP 429 リスク（`dynamic_build.py` の rate limit 処理参照）
- PUSH で足りるため **コスト対効果が低い**

### 3.3 ハイブリッド: ENTRY 凍結 + リアルタイム悪化

Phase332 との共存案:

| レイヤ | 役割 |
|--------|------|
| ENTRY percentile | trailing パラメータ（現行 Phase332） |
| リアルタイム imb | **追加** EXIT シグナル（例: `imbalance_collapse_streak >= 3`） |
| hard_stop 1.2% | 変更なし |

`kabu_exit_v1` の `board_imbalance_deterioration` または `microstructure_runtime` の collapse ロジックを shadow から移植可能。

---

## 4. 制約・リスク

### 4.1 レジスト上限（50）

- AM/PM dynamic50 で **ほぼ満杯**（core10 + dynamic40 等）
- 保有銘柄がユニバース外になるケースは `outside_refresh_universe_reject` で ENTRY 拒否 — **通常は登録済み**
- 追加 register は **不要**（既存 50 のストリームを共有）

### 4.2 PUSH の性質

- 「値が変わったとき」配信 — 薄商い銘柄は更新頻度が低い（push_jsonl: 150 行/日の銘柄あり）
- 昼休み・引け後は PUSH 停止の可能性（`push_client.push_spec` notes）
- `stale_tick_sec: 120`（live config）— 板も価格も古くなると feature / EXIT 品質低下

### 4.3 板定義の一貫性

- `calc_board_imbalance` は深度込み — PUSH 実測では深度あり
- ENTRY 時と HOLD 時で同一定義を使えば **比較可能**
- `EXPECTED_PUSH_FIELDS_STOCK` に深度が無い点は監視用に spec 更新推奨

### 4.4 検証ギャップ

- Phase331 replay は **entry-frozen board tier** の trailing のみ
- リアルタイム板 EXIT のリプレイには push_jsonl から tick ごとに `calc_board_imbalance` を再計算するパイプラインが必要（データは在庫あり）

---

## 5. 推奨ロードマップ

| Phase | 内容 | 本番影響 |
|-------|------|----------|
| **334**（本レビュー） | 在庫・コスト・可行性の文書化 | なし |
| **335-lite** | `board_realtime_exit_shadow` — `on_tick` で imb 計算・ログのみ | なし |
| **335** | push_jsonl リプレイで shadow vs actual EXIT 比較 | なし |
| **336** | 採用判断（`board_imbalance_deterioration` 等） | 要承認 |

Phase332-lite / Phase332 と同型の **shadow → replay → production** パターンを推奨。

---

## 6. 実装チェックリスト（335-lite 向け）

- [ ] `observer.on_tick` で `calc_board_imbalance(payload)` を毎 tick 計算
- [ ] `ObserverPosition` に `current_imbalance`, `imbalance_low_streak`, `imbalance_collapse_streak` を保持
- [ ] EXIT イベントに shadow フィールド追加（`shadow_board_exit_reason` 等）
- [ ] `small_paper_events.csv` に `hold_imbalance_*` 列を検討（事後分析用）
- [ ] push_jsonl リプレイで Phase213c 型のコホート検証
- [ ] Discord EXIT に board 悪化デバッグ（任意）

---

## 7. 判定まとめ

| 質問 | 回答 |
|------|------|
| 現在取得可能な板情報は足りるか？ | **はい**（最良気配＋深度、push_jsonl で実証済み） |
| 保有中リアルタイム監視は可能か？ | **はい**（PUSH 経路・コード配線のみ） |
| 追加データ取得コストは？ | **PUSH 経路なら実質ゼロ**（[estimated_runtime_cost.md](estimated_runtime_cost.md)） |
| 今すぐ本番 EXIT に入れるか？ | **いいえ** — shadow + replay 検証が先 |
