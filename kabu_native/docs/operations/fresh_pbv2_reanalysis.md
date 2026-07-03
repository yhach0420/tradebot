# Fresh PBv2 Collapse Root Cause Re-analysis

verdict: `fresh_pbv2_reanalysis_done`

対象: 2026-06-24 / 06-25 (正常), 06-29 / 06-30 (異常), 07-01 (freshness semantics v2 適用後)。
過去Phaseの結論を前提とせず、`results/small_paper/` のセッション成果物・`entry_scan_audit.jsonl`・
`volume_gate_shadow_eval.jsonl`・git 履歴のみから再構成した。本線コード変更・実注文・full dump なし。

成果物: `results/reports/fresh_pbv2_reanalysis/`(daily_funnel / config_diff / code_diff /
input_distribution / pbv2_internal_blockers / score3_fresh_trace.csv.gz / structure_parity /
first_divergence.csv.gz / freshness_counterfactual / case_trace.csv.gz / report.json)

---

## 結論(要旨)

**PBv2 accepted 消滅の根本原因は Phase549 entry_cluster_guard(csub 棄却 [0,2,3,5])である。**
ライブ環境では csub 分類器の入力特徴(relative_board_*, volume_accel_*, momentum_decay_* 等 20 特徴)が
ほぼ欠損し 0 埋めされるため、PBv2 の最終段に到達した候補のほぼ 100% が `c3_s5` に分類され棄却された。
この棄却理由は、6/26 に導入された OR overlay が「ポジション未保有銘柄の PBv2 reject 理由を
`or_overlay_not_candidate` で上書きする」実装のためログから完全に不可視だった。

| 日 | accepted | PBv2 pool | OR pool | cluster_guard棄却 (全て c3_s5) | 最終段到達数 |
|---|---|---|---|---|---|
| 6/25 | 53+27 | 43 | 10 | (guard未導入) | – |
| 6/29 AM | 12 | **0** | 12 | 4,269 | 4,281 (99.7%棄却) |
| 6/29 PM | 0 | **0** | 0 | 5,519 | 5,519 (100%棄却) |
| 6/30 | 6 | **0** | 6 | 6,339 | 6,345 (99.9%棄却) |
| 7/1 (csubs=[] rollback) | 43 | **37** | 6 | 0 | 7,444 (0%棄却) |

## 証拠と検証方法

- **マスキングの定量化**: 7/1 から `pbv2_internal_reason` がログされる。final=`or_overlay_not_candidate`
  34,651 件の内訳は high_drift 11,595 / suitability 8,715 / pbv2_cap 4,870 / near_day 4,831 /
  momentum 2,219 / reentry_rsi 1,466 など。6/29 PM はポジション 0 のため PBv2 内部理由は 100% マスクされ、
  reject_reason_counts が 4 種(stale/or_overlay/am_pm/stale_board)しか無い異常な形になった。
- **再構成分類器の検証**: イベント項目+volume_gate_shadow(vol_liq スコア実測)+凍結クラスタモデルで
  first-blocker を再構成し、7/1 の ground truth と照合 → 混同行列はほぼ完全対角
  (high_drift 12600/12600, suitability 8744/8751, near_day 5178/5178, cap 4869/4871)。
- **構造パリティ (調査E)**: 同一の記録済み候補列に対し、S1=6/29構造(csubs 0/2/3/5)、S2=Core only、
  S3=6/25相当(cluster guardなし)、S4=現HEAD(csubs=[])の判定を計算。ゲート前段は全構造共通で、
  分岐は entry_cluster_guard の 1 段のみに局在。S1 pass=0 / S3=S4 pass=100%(4,271/5,915/6,345/7,444)。
- **正常日の勝ち筋も c3_s5**: 6/25 の accepted 代表 10 件は全て凍結モデルで `c3_s5` に分類される
  → 6/29 構造ではその全てが棄却されていた(case_trace.csv.gz 参照)。

## freshness の影響 (調査F)

- price age p50 は全日 4–7 秒、board age p50 は 0.6 秒で安定。v1 定義 (price≤3s AND board≤3s) の
  通過率は 6/24–7/1 で 31–45% と異常なし。**6/29 の stale 率(60–67%)は 6/24–25(53–67%)の範囲内。**
- 6/30 は board fallback が有効(fallback 16,571 回)で stale reject が 60%→5% に激減したが
  **PBv2 は 0 のまま** → freshness 改善だけでは復活しないことを実データが直接証明。
- 7/1 の v2 semantics は 15,981 評価を trade_stale タグで救済したが、accepted 43 件のうち
  v2 救済経由は 7 件(16%)。残り 36 件は v1 でも通過していた。**復活の主因は csub rollback。**

## 必須回答(15項目)

1. **どの日から発生したか**: 最後の正常日は 6/25。6/26–6/28 はセッション出力が空(この間に
   cluster guard 導入と Phase552 model path 修正)。観測上の初回崩壊は 6/29 AM(PBv2 pool=0)、
   6/29 PM で accepted=0。
2. **最初に大きく変化した指標**: `pbv2_count` 43→0 と `cluster_guard_reject_count`(全て c3_s5)の出現。
   上流のファネル指標(freshness 通過率、score3 fresh 数、入力分布)は変化なし。
3. **実行構造は同じか**: 異なる。6/24=OR overlay 以前 / 6/25=OR overlay 導入後 / 6/29–30=cluster+
   stop_low_mfe+cache+live-order dry-run 追加後 / 7/1=Phase616 コア再構成+freshness v2。全日
   uncommitted working tree で実行され config sha も毎日異なる。
4. **設定差分**: あり。6/25→6/29: +entry_cluster_guard(csubs 0/2/3/5), +stop_low_mfe, +vol_liq cache,
   +volume gate shadow, +live order dry-run。6/30→7/1: csubs=[], stop_low_mfe=false, freshness v2 有効。
   ゲート閾値(v2 min=3, momentum 0.2546, suitability 54.6957, CAP 4+1)は全日不変。
5. **入力データ差分**: 実質なし(momentum p50 0.05–0.25, imbalance p50 0.51–0.56, board mid/high 比率、
   push 量、feature bridge complete 率 93–96% すべて安定)。
6. **最多 blocker**: 前段では high_drift / daytrade_suitability(正常日と同構成)。決定打は最終段の
   entry_cluster_guard(最終段到達候補の 99.7–100% を棄却)。7/1 実測: high_drift 12,600 >
   suitability 8,751 > near_day 5,178 > cap 4,871 > momentum 2,922、cluster 0。
7. **score=3 かつ fresh が落ちる理由**: score3+fresh は必要条件にすぎず、high_drift/near_day/
   suitability/RSI/quality で大半が落ち、6/29–30 は生き残り全員が c3_s5 で棄却された。
8. **構造差だけで PBv2=0 を説明できるか**: できる(パリティ計算で S1 のみ pass=0、divergence は
   cluster guard 1 段に 100% 局在)。
9. **freshness 変更だけで復活を説明できるか**: できない(6/30 が反例。7/1 の v2 救済は 43 件中 7 件)。
10. **bridge/state/batch/prior_trades の影響**: 集計上の異常なし。ただし csub 特徴のライブ欠損
    (0 埋め)が cluster guard 誤動作の構成要素。cap/overlap reject の消失はポジションが開かない結果
    であって原因ではない。
11. **OR overlay のマスキング**: あり(実装仕様)。`_maybe_try_or_overlay_entry` が PBv2 reject 理由を
    上書き。6/29 PM は 100% マスク。これが原因究明を遅らせた観測性バグ。
12. **最も支持される仮説**: 実装問題 — cluster guard csub 棄却 × ライブ特徴欠損 × 理由マスキング。
13. **棄却される仮説**: 市場要因、データ(フィード)劣化、freshness 定義自体が崩壊原因、
    freshness v2 が復活主因、OR overlay が受入抑制の原因、bridge/state/batch/prior_trades 干渉。
    (それぞれの根拠は report.json の q13 参照)
14. **本線に入れるべき修正**: csubs=[] の維持(または csub 特徴実在性の前提チェック追加)、
    マスクされた internal reason の恒久ログ化+reject_reason_counts への露出、単一ゲート棄却率
    >X% のセッション内アラート、本番実行コード/設定のコミット徹底、cluster 判定フィールドの
    candidate イベント記録。
15. **追加検証**: csub モデルのライブ特徴可用性監査と再学習、6/26–28 の空セッションの原因確認、
    freshness v2 の PnL/PF/DD 影響の多日検証、stop_low_mfe の妥当性、OR pool の挙動継続監視。

## 制約遵守メモ

- 本線コード変更なし / 実注文なし / raw payload 全保存なし / candidate 全件保存なし(トレースは
  セッションあたり上限付きサンプル、csv.gz)。
- 並列は集計ワーカー 4 shell まで。
- ディスク: 分析開始前から C: 使用率 90.8%(76% 制約は既存データで既に超過)。本分析の追加出力は
  数 MB のみ、temp 集計は完了後に削除(cleanup log: `results/reports/fresh_pbv2_reanalysis/cleanup_log.txt`)。
