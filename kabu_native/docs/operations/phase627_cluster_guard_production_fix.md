# Phase627: PBv2 Cluster Guard Collapse — Production Fix

verdict: `phase627_cluster_guard_production_fix_done`

対象根本原因(fresh_pbv2_reanalysis):
Phase549 entry_cluster_guard の `reject_csubs=[0,2,3,5]` が、ライブで csub 特徴量欠損→0埋めにより
候補のほぼ100%を `c3_s5` に縮退分類して棄却(6/29–6/30 PBv2=0)。さらに OR overlay が PBv2 内部
reason を `or_overlay_not_candidate` で上書きし、異常が不可視だった。

禁止事項の遵守: ENTRY/PBv2 スコア式変更なし / EXIT 変更なし / 実注文なし / 高価格・時間帯・銘柄除外なし / 大規模CSVなし。

---

## 実装1: cluster guard feature completeness safety

`src/small_paper/entry_cluster_guard.py`

- reject 判定を出した分類段の特徴量(reject_clusters→cluster_features、reject_csubs→csub_features)の
  **raw 実在性**(0埋め・median埋め前)を `_reject_stage_missing_features()` で検査。
- 1つでも欠損があれば **reject しない**。`cluster_guard_status=FEATURE_INCOMPLETE`、
  tag `entry_cluster_guard_feature_incomplete` を付与し、summary に
  `cluster_guard_feature_incomplete_count` と欠損特徴 top10 を記録(tag/log のみ)。
- `reject_clusters: [5]` は現状維持。ただし feature incomplete 時は cluster reject も禁止。
- 本線 YAML は `entry_cluster_guard_reject_csubs: []` を維持(変更なし)。

## 実装2: PBv2 internal reason 永続化

`src/small_paper/pilot_runner.py`

- `_record_pbv2_internal_reject()` が **OR overlay 実行前に** `pbv2_internal_reason` /
  `pbv2_internal_gate` を trade に保存(以後上書きされない)。
- `or_overlay_reason`(OR overlay の判定理由)と `final_reject_reason`(最終 reject 理由)も記録。
- 4 フィールドを `EVENT_FIELDS` に追加 → `small_paper_events.csv` / `small_paper_rejects.csv` /
  `small_paper_events.jsonl` に出力される。
- Discord summary に「PBv2 Internal Breakdown」セクションを追加し、
  `or_overlay_not_candidate` の内部理由内訳(top8)を表示。

## 実装3: gate dominance alert

- `_gate_dominance_alert_fields()`: PBv2 内部理由カウント + stale reject カウントを合算し、
  単一理由の占有率を判定。**warning ≥80% / critical ≥95%**(最小サンプル 50)。
- summary JSON に `gate_dominance_alert_level` / `gate_dominance_top_reason` /
  `gate_dominance_top_share_pct` を記録。Discord に「Gate Dominance Alert」セクション表示。
- paper trade は停止しない(記録のみ)。6/29 の実カウント(4269/4281=99.72%)で critical 発火を確認。

## 実装4: preflight

`src/small_paper/phase627_preflight.py`(`live_pipeline_preflight` と
`production_startup_smoke_test` の両方に組み込み。run_paper_trade.bat は両方を実行し失敗で中断)

1. `entry_cluster_guard_reject_csubs == []` でなければ fail
2. feature completeness check の実動作確認(欠損候補を強制 reject 分類 → block されないこと)
3. `EVENT_FIELDS` に internal reason 4 フィールドが配線されていること
4. OR overlay mask シミュレーション後も `pbv2_internal_reason` が残ること

## 実装5: regression(全 PASS)

`python kabu_native\src\research\phase627_cluster_guard_production_fix.py`

| # | テスト | 結果 |
|---|---|---|
| T1 | feature incomplete → reject しない(tag のみ) | PASS |
| T2 | feature complete かつ reject 対象 → reject する | PASS |
| T3 | OR overlay が final reason を上書きしても pbv2_internal_reason が残る | PASS |
| T4 | 6/29 実データ 3,000 候補: 旧ロジック相当 100% reject 分類 → 新ロジック block **0 件**(全件 FEATURE_INCOMPLETE tag) | PASS |
| T5 | Phase621 freshness v2 本線設定と共存(YAML 読込+gate 構築+preflight クリーン) | PASS |
| T6 | run_paper_trade.bat の preflight 2 段(pipeline + smoke test)が exit 0 | PASS |

追加ネガティブ検証: csubs=[0,2,3,5] 復元 → preflight fail / 99.72%→critical / 85%→warning /
data_stale_price 99%→critical / n<50→alert なし / Discord 行レンダリング — すべて PASS。

## 必須回答

1. **本線 YAML の reject_csubs**: `[]`(空)。以後 `[]` 以外では preflight が fail し起動不可。
2. **feature incomplete 時の reject 禁止の保証**: guard 内部の不変条件として実装(reject 分類後、
   0埋め前の raw 特徴実在性を検査し欠損なら blocked=False)。T1(単体)、T4(6/29 実データ 3,000 件で
   block 0)、および毎回の起動時 preflight の機能チェックで担保。
3. **mask 後も PBv2 reason は残るか**: 残る。OR overlay 呼び出し前に trade へ保存し以後不変。
   events/rejects CSV・JSONL に `pbv2_internal_reason` / `pbv2_internal_gate` / `or_overlay_reason` /
   `final_reject_reason` が並んで出力される(T3+E2E で確認)。
4. **gate dominance alert は動くか**: 動く。80%/95% 閾値・最小 50 件で、summary JSON と Discord に記録。
   6/29 型カウントで critical 発火を実証。停止はしない。
5. **6/29 型 PBv2=0 の再発防止**: 3 層で防止 — (1) 危険設定は preflight で起動不可、
   (2) 特徴欠損分類は reject 不能(T4: 0/3000)、(3) 万一単一ゲートが支配しても critical alert で即可視化。
6. **Phase621 と競合しないか**: しない。freshness reject は PBv2 前段で別カウントに分離。
   T5/T6 で本線設定(freshness v2 有効)との共存を実行確認。
7. **rollback**: YAML 変更なしのため設定 rollback 不要。コードは対象 5 ファイルの git checkout +
   `phase627_preflight.py` 削除。safety のみ止める場合は `FEATURE_COMPLETENESS_CHECK_ENABLED=False`
   (この場合 preflight も連動して fail するため preflight 配線も外すこと)。
8. **起動前確認コマンド**:
   `run_paper_trade.bat`(preflight 2 段を自動実行・失敗時中断)。手動確認は
   `python kabu_native\scripts\check_live_pipeline_preflight.py` と
   `python kabu_native\scripts\run_production_startup_smoke_test.py --exit-policy-shadow trailing-mfe`、
   フル regression は `python kabu_native\src\research\phase627_cluster_guard_production_fix.py`。

## 成果物

- `results/reports/phase627_cluster_guard_production_fix.json`
- `results/reports/phase627_regression_summary.csv`
- `results/reports/phase627_regression_report.json`
- `docs/operations/phase627_cluster_guard_production_fix.md`(本書)
