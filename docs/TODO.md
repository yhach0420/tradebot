# TODO / ロードマップ

本プロジェクトの開発・運用TODO。設計仕様（`docs/DESIGN.md`）とは分離して管理する。

## 更新版 TODOランキング

### Sランク

#### TODO 1：時間帯固定制限を撤廃する検証

- **目的**：`disable_afternoon_entry=True` が過学習か確認する。
- **やること**
  - `disable_afternoon_entry=False` の config を用意する
  - 無条件の後場売買ではなく、**地合い状態を記録**して分析する
  - 「後場という時間」ではなく **地合い悪化条件で止められるか** を確認する

#### TODO 2：regime adaptive control の実装

- **目的**：時間ではなく **地合いで売買強度** を変える。
- **必要な状態分類**：`STRONG` / `NORMAL` / `WEAK` / `CRASH`
- **使う指標**
  - TOPIX 変化率
  - `rising_ratio`
  - `high_update_count`
  - `BREADTH_WEAK`
  - `TOPIX_CRASH`
  - VWAP 下回り銘柄率
  - `fail_rate30`

#### TODO 3：地合い別ルールを config 化

例（`regime_controls`）：

```json
"regime_controls": {
  "STRONG": {
    "entry_enabled": true,
    "allow_gap_ge_pct": 3.0,
    "allow_vwap_distance_pct": 2.0,
    "exit_mode": "normal"
  },
  "NORMAL": {
    "entry_enabled": true,
    "allow_gap_ge_pct": 3.0,
    "allow_vwap_distance_pct": 1.5,
    "exit_mode": "normal"
  },
  "WEAK": {
    "entry_enabled": true,
    "allow_gap_ge_pct": 2.0,
    "allow_vwap_distance_pct": 1.0,
    "exit_mode": "fast"
  },
  "CRASH": {
    "entry_enabled": false
  }
}
```

### Aランク

#### TODO 4：一日通し Replay

- **目的**：朝だけ切り抜き戦略になっていないか確認する。
- **やること**
  - 後場も含めて Replay
  - 時間帯別ではなく **地合い状態別**に集計
  - 後場で負けた場合も「後場だから」ではなく **どの状態だったか** で分析

#### TODO 5：regime 別 exit 制御

- **目的**：弱地合いでは早逃げ、強地合いでは伸ばす。
- **候補**
  - `STRONG`：利確遅め / trailing 広め
  - `NORMAL`：現行
  - `WEAK`：早期撤退強化
  - `CRASH`：新規停止

### Bランク

#### TODO 6：固定日付セット Replay

- **目的**：日付ガチャを消す。
- **やること**
  - `--replay-fixed-dates`
  - sweep でも **同一日付**で比較

#### TODO 7：キャッシュ蓄積

- **目的**：4月依存を抜ける。
- **やること**
  - 毎日 1分足保存
  - 3月や弱相場データ確保
  - 5月以降蓄積

**補足**：ペーパートレード・半自動・証券 API・AI 最適化などは、このランキングから外れた項目は **`## 長期バックログ（旧整理）`** を参照。

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

- **「事故る条件」の特定が未完。**（単独 feature より **組み合わせ・交互作用** がボトルネック。）
- **設計面**：時間帯のハード封印（後場禁止など）が **地合いの代理変数になりすぎていないか**（Sランク TODO 1〜3 で是正）。
- **検証面**：日付ガチャ・単一月偏重・弱地合いデータ不足（Bランク TODO 6〜7、およびキャッシュ運用）。

### 今のフェーズ

```
事故る条件を削る段階
        ↓
時間固定 → 地合い適応（regime adaptive）への移行   ← いまここ（Sランク）
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
