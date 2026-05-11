# TODO / ロードマップ

本プロジェクトの開発・運用TODO。設計仕様（`docs/DESIGN.md`）とは分離して管理する。

## フェーズ（現在）

**目的**：「forward運用可能なロジック」を **replay 高速検証**で磨く。

- **paper trade**：運用耐久確認用。
- **期待値改善**：replay 主体で進める。
- **方針（重要）**：「時間帯」や「何回目entry」を主軸にしない。**状態変化・伸びすぎ（price extension）・モメンタム劣化**を主軸に移行。
- **補助分析**：`entry_hour_bucket` / **`symbol_daily_entry_index`（同日約定順＝疲労の代理）** は *補助* のみ。forward の主判定・**AUTO_BLOCK の主軸は price extension 系**へ（index ベースのブロック設計はしない）。
- **AUTO_BLOCK（extension 系フラグ）**：**既定は OFF のまま**。`extension_sweep_analysis` / `extension_hu_interaction_analysis` / `AUTO_BLOCK_EFFECT_ANALYSIS` で有効性を見てから採用判断。
- **基本思想**：「伸びたら危険」ではなく、**伸びた後に momentum が劣化した状態が危険**。

## TODO（現在の優先順位）

### 最優先

#### 1. STRONG × momentum deterioration / extension 相互作用（最優先）

- **目的**：最後の吹き上げ、伸びすぎ後の失速、買い継続不足を **定量化**する（**「何回目 entry」より extension の説明力**を優先）。
- **重点特徴量**
  - `price_change_pct_from_prev_signal`
  - `delta_high_update_count_before_entry`
  - `delta_entry_vwap_distance_pct`
  - `volume_efficiency_pct`
  - `market_regime`（3 軸 interaction の第 1 軸）
- **主軸（JSON / TXT）**
  - `extension_sweep_analysis` / `extension_hu_interaction_analysis`（forward 耐性の核）
  - `momentum_decay_analysis` / `CHASE_EXTENSION_ANALYSIS`（補助・従来軸）
  - `robustness_symbol_removal_analysis`（上位期待値銘柄依存の感度）
  - `AUTO_BLOCK_EFFECT_ANALYSIS`（採用前の仮想効果）

### 次点

#### 2. CHASE EXTENSION 分析・AUTO_BLOCK検証（複合条件化の候補）

- **方針**：`BLOCK_STRONG_CHASE_AFTER_EXTENSION` を単独採用ではなく、**複合条件化**の候補として扱う。
- **例（候補）**
  - `market_regime == STRONG`
  - AND `price_change_pct_from_prev_signal >= 0.5`
  - AND `delta_high_update_count_before_entry == +1`
- **重点分析**
  - `CHASE_EXTENSION_ANALYSIS`
  - `MOMENTUM_DECAY_ANALYSIS`
  - `momentum_decay_x_market_regime`
  - `AUTO_BLOCK_EFFECT_ANALYSIS`
- **確認項目**
  - 崩壊点（0.5 / 0.8 / 1.2）
  - expectancy / lose_worst10
  - STRONG/WEAK 別差異
  - blocked_total_pnl / pnl_improvement

#### 3. RISING_LT50 / rising_ratio_threshold（地合い単体ではなく組み合わせで見る）

- **方針**：地合い単体で結論を出さず、**状態遷移（伸びすぎ×劣化）**との組み合わせで評価する。
- **対象**
  - `REGIME_FILTER_RISING_LT50`（※最優先からは外す）
  - `rising_ratio_threshold`（30/35/40/45/50/55）
- **重点（例）**
  - WEAK_expectancy
  - lose_worst10
  - tail risk

#### 4. 現在の主軸分析（状態遷移ベースの事故条件分析）

- **目的**：単独featureではなく、**「事故る状態遷移」**を定量化する。
- **主軸**
  - `momentum_decay_analysis`
  - `CHASE_EXTENSION_ANALYSIS`
  - `AUTO_BLOCK_EFFECT_ANALYSIS`
  - 状態遷移ベースの事故条件分析
- **関連（必要に応じて）**
  - strong_combo_filter / weak_combo_filter（単独の固定候補ではなく、状態遷移の説明変数として扱う）

### 保留

#### 4. 銘柄追加

- **まだ行わない。**
- **理由**：ロジック要因と銘柄要因を分離したい。**まずロジック安定化**を優先。

### 運用

#### 5. paper trade

- **目的**：運用耐久テスト。
- **確認事項**
  - 落ちない / future leak なし / Discord 正常 / CSV 正常 / 重複防止 / session 制御
- **運用**：場中は基本放置。**確認は引け後のみ**。
- **出力**：`results/paper_trade/20260510/`（`paper_trade_log.csv` / `paper_trade_summary.txt` / `paper_trade_seen_ids.json`）

### 将来

#### 6. 株ステーション連携

- **目的**：Yahoo 制限回避。
- **取得したいもの**：リアルタイム 1分足 / 板 / 約定 / 出来高 / tick
- **タイミング**：ロジック固定後。

#### 7. 実売買

- **条件**：replay 安定 / paper trade 安定 / drawdown 許容 / signal 数安定
- **その後**：証券 API 検討。

**補足**：旧ランキングや詳細メモは **`## 長期バックログ（旧整理）`** を参照。

---

## 次フェーズ候補（未実装）

`forward_split` / `lifecycle` analysis の結果を確認したうえで、**必要になったときだけ**以下を検討する。

- `forward_reproducibility_candidate_survival_analysis`
- `cluster_decay_curve_analysis`
- `interaction_forward_consistency_analysis`
- `danger_feature_forward_consistency`

**目的**：train で抽出した danger interaction / cluster / feature の向き・強さが、**validation / forward でも維持されるか**確認する。

**重要**

- **現時点では未実装**。
- まず現在の **`FORWARD_SPLIT_VALIDATION_ANALYSIS`** / **`CLUSTER_LIFECYCLE_ANALYSIS`** で十分な情報が得られるか確認する。

**禁止**

- 先行実装
- AUTO_BLOCK 化
- score tuning
- feature 追加

**現在**：**「forward で再現するか確認するフェーズ」**を優先する。

---

## 次フェーズ（paper_trade 後に実施）

**目的**：`forward_risk_virtual_block_sweep` で有効だった **「危険構造BLOCK候補」**を、paper_trade のリアルタイム挙動確認のあとに **shadow mode → AUTO_BLOCK 候補**へ進める。

**重要**：**paper_trade 完了までは AUTO_BLOCK 本実装しない。**

### 有力候補

#### 1. `STRONG_EXTENSION_GE_05`

- **条件**：`market_regime == "STRONG"` かつ `price_change_pct_from_prev_signal >= 0.5`

`forward_risk_virtual_block_sweep` 結果：

- `blocked_expectancy` ≈ -4980円
- lw10 大幅改善
- max_losing_run 大幅改善
- expectancy_after 改善

**現時点で最有力。**

---

#### 2. `STRONG_DELTA_HU_MINUS1`

- **条件**：`market_regime == "STRONG"` かつ `delta_high_update_count_before_entry == -1`

`forward_risk_virtual_block_sweep` 結果：

- `blocked_expectancy` ≈ -10559円
- tail risk 改善
- extension≥0.5 より効果は弱いが有力

---

### 不採用候補

#### `STRONG_GAP_GE_3`

- **理由**：`blocked_expectancy` が正。良い signal も除外している可能性。

---

### 次段階（paper_trade後）

1. **shadow block mode** 実装（signal は出すが BLOCK 候補として別記録）
2. paper_trade 上で次を確認：
   - shadow hit 回数
   - shadow signal expectancy
   - tail risk
   - missed winner
3. 問題なければ **AUTO_BLOCK 候補へ昇格**

**禁止**

- 現時点で AUTO_BLOCK 本実装
- score tuning
- feature 追加
- `symbol_daily_entry_index` 利用
- 時間帯 block
- 銘柄固定ルール

---

## 現時点の戦略評価（更新版）

### 良い点

- 朝特化はかなり良い
- DD30k は有効
- gap filter に有効性あり
- rising_ratio filter が効いている
- VWAP距離 filter が効いている
- lose_worst10 が改善傾向
- max_lose_run が改善

### 現在の最大課題

- **単独featureではなく「状態遷移（モメンタム劣化）」の定量化が未完。**
- 「伸びたら危険」ではなく、**伸びた後に momentum が劣化した状態が危険**を定量化したい。
- 特に次の組み合わせで、**「伸びすぎ追いかけ」の崩壊点**を特定する。
  - `price_change_pct_from_prev_signal`
  - `delta_entry_vwap_distance_pct`
  - `delta_high_update_count_before_entry`
  - `volume_efficiency_pct`
- **補足**：`entry_hour_bucket` / `symbol_daily_entry_index` は補助分析。**「時間帯だから危険」「4回目だから悪い」**を主軸にしない。

### 今のフェーズ

```
期待値・ロジック → replay で高速磨き（forward で再現するか確認するフェーズを優先）
        ‖（並行）
運用耐久・漏れ・安定性 → paper trade（期待値改善の主戦場ではない）
```

## 長期バックログ（旧整理）

### 優先度A：実運用移行

- ペーパートレードモード
  - 実注文なし
  - リアルタイム監視
  - 仮想Entry/Exit記録
  - Discord通知
  - `paper_trade_log.csv` 保存
  - Replay条件をそのまま使用
  - 日次DD停止対応
- 日次サマリー出力
  - 当日signal数
  - 勝率
  - 仮想PnL
  - DD
  - skipped理由
  - Discord出力

### 優先度B：半自動化

- 手動承認モード
  - Botが「Entry候補」を通知
  - Discordボタン or コマンドで承認
  - 承認時のみ注文
  - 自動Exit監視
    - VWAP割れ
    - 5分安値割れ
    - DD停止
    - 地合い悪化

### 優先度C：完全自動化

- 証券API接続
  - 株ステーション等
- 発注
- 建玉管理
- 注文取消
- 約定取得
- 小ロット自動売買
  - 最初は100株固定
- 最大損失制限
- 同時保有制限
- ロット管理
  - DDベース
  - ボラベース
  - Kelly簡易版など

### 優先度D：高度化

- 地合いモード別戦略
  - 強地合い
  - 弱地合い
  - レンジ
- 銘柄クラスタ分類
  - 半導体
  - 防衛
  - 金融
  - 重工
  - 値が軽い銘柄 etc
- AI最適化・特徴量分析
  - replay結果学習
  - signal特徴量保存
  - 勝ちpattern分析

### モメンタム状態遷移分析

- signal間価格変化
- volume efficiency
- breakout exhaustion
- pullback quality
- regime別崩壊点分析

**目的**：固定時間ではなく、状態変化ベースで forward 判定可能にする。
