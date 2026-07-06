# Phase629: ENTRY Pipeline Stage Refactoring（挙動完全一致）

## 目的

`pilot_runner.py` の ENTRY パイプライン（`_process_push_payload`、旧853行の単一関数）を
Stage0〜Stage6 に分割し、**ロジックを一切変更せず構造のみ整理**した。
利益・ENTRY件数・EXIT件数・PBv2/OR/Freshness判定・CSV出力・Discord出力は完全一致を維持する。

- 新規モジュール: `src/small_paper/entry_pipeline_stages.py`（Stage dataclass + StageTraceLogger のみ。ロジックなし）
- 変更モジュール: `src/small_paper/pilot_runner.py`（コード移動のみ。whitespace無視диffで**削除された実行行 0**）

## Stage構成（実装）

| Stage | 関数 | 入力 | 出力 dataclass | 責務 |
|---|---|---|---|---|
| Stage0 | `_stage0_normalize_payload` | WebSocket PUSH payload | `Stage0NormalizedPayload` | scan begin/flush_on_begin、symbol解決、`ExtensionBus.on_push_tick`、tick/gapカウンタ、price ring、`feature_bridge.update`/`enrich_payload`、candidate trade生成（HBRecent/board imbalance含む） |
| （非stage） | `_observer_open_position_tick` | Stage0 | （exit events） | 保有中銘柄のEXIT tick（`observer.on_tick`）。Stage0→Stage1間、元コードと同順序 |
| Stage1 | `_stage1_evaluate_freshness` | Stage0 | `Stage1FreshnessResult` | am_pm/universe pre-gate、expectancyフィールド、`compute_entry_freshness`、`evaluate_entry_data_freshness`（v2: event/board/trade stale + tag）、staleカウンタ。stale/pre-gate時は `short_circuit_decision` を返す |
| Stage2 | `_stage2_evaluate_pbv2` | Stage0 + Stage1 | `Stage2PBv2Result` | `_enrich_trade_for_pullback_guard` → `ExposureGate.evaluate_entry`（PBv2全ガード連鎖）→ `_record_pbv2_internal_reject`（Phase627）。**GateDecisionはここで生成後 immutable（どのStageも変異しない。理由差し替えは `dataclasses.replace` による新インスタンス）** |
| Stage3 | `_stage3_cluster_decision` | Stage2 + trade | `Stage3ClusterDecision`（frozen） | cluster guard結果の**read-only分類**（PASS / REJECT / FEATURE_INCOMPLETE）。guard本体はExposureGate内で実行される（Phase629はロジック移動禁止のため、実行位置は不変。Stage3は形式化・可観測性のみ） |
| Stage4 | `_stage4_finalize_decision` | Stage2（またはshort-circuit）+ Stage1 | `Stage4FinalEntryDecision` | `_maybe_try_or_overlay_entry`（PBv2 reject時のみ）、`or_overlay_reason`/`final_reject_reason` 記録、`gate_evaluations` 増分。**PBv2 reason（`pbv2_internal_reason`/`pbv2_internal_gate`）はここで変更禁止・変更なし** |
| Stage5 | `_stage5_execute_entry` | Stage0 + Stage4 + Stage6Record | （positions/accepted rows） | accept時: batch queue（`queue_accepted_candidate` → `maybe_flush_after_eval` → `_process_scan_flush`）または直接 `_execute_accepted_entry`（register_entry / positions / accepted rows） |
| Stage6① | `_stage6_record_candidate` | Stage0/1/4 | `Stage6CandidateRecord` | candidate event書込、profiler finish、latency trace finish、score5 ordinal、`record_symbol_eval` audit、`ExtensionBus.on_post_eval` |
| Stage6② | `_stage6_record_reject` | Stage0 + Stage4 + Stage6Record | （rejects/events/Discord） | 理由別reject row（+errors.jsonl）、rejected event、Discord notify |

Stage間の受け渡しは **dataclassのみ**（`entry_pipeline_stages.py` の
`Stage0NormalizedPayload` / `Stage1FreshnessResult` / `Stage2PBv2Result` /
`Stage3ClusterDecision` / `Stage4FinalEntryDecision` / `Stage6CandidateRecord`）。

実行順序は旧コードと完全同一:

```
_process_push_payload (orchestrator)
  → Stage0 (symbol未解決なら終了)
  → _observer_open_position_tick (EXIT tick)
  → Stage1 → (short-circuitでなければ Stage2 → Stage3) → Stage4
  → Stage6① (candidate記録)
  → accept: Stage5 / reject: Stage6②
```

Stage6が①②に分かれるのは、旧コードで candidate 記録が accept/reject 分岐の**前**に
実行されていたため（順序保存のための構成。責務は両方とも writer/audit/Bus/Discord）。

## Stage Trace Logger（DEBUG時のみ）

`entry_pipeline_stages.StageTraceLogger`。Stage0〜Stage6 の開始/終了を記録する。

- 有効化: 環境変数 `ENTRY_PIPELINE_STAGE_TRACE=1` または
  logger `small_paper.entry_pipeline_stages.trace` を DEBUG レベルに設定
- 無効時（本番デフォルト）は**完全 no-op**（recordsも空、ログ出力なし）
- 出力: `[stage_trace] stage=<name> phase=start|end symbol=<sym> msg_i=<n> note=<...>`（log.debug）

## 挙動保存に関する重要事項（Phase640で修正）

旧コードには潜在バグがあった: `am_pm_entry_stop` / `outside_refresh_universe` 分岐では
ローカル変数 `ref_now` が未代入のまま audit ブロックに到達し、
**`UnboundLocalError` ... が毎回発生**、`_loop` 側で `push_unexpected` として捕捉されていた
（実例: 2026-07-01 live_session 11:20以降に29件記録）。
この結果、entry stop後の候補は rejected イベント・reject row が記録されない。

Phase629は**挙動変更ゼロ**が要件のため、この挙動を意図的に保存した。
**Phase640** で logging-only fix を適用（`ref_now` 代入 + audit/reject writer 保護）。
accepted件数・ENTRY/EXIT/PBv2/OR判定は変更なし。新規 reject row/event のみ差分許容。
詳細: `docs/operations/phase640_entry_stop_reject_logging_fix.md`

## Regression（Replay完全一致検証）

- 対象日: 2026-06-25 / 06-29 / 06-30 / 07-01（`data/push_jsonl/<day>` 全行、poll 5.0s、本番YAML）
- baseline（リファクタ前 HEAD コード）と after（Stage化後）で
  `run_push_replay_dry_run` を実行し、以下を比較:
  - `small_paper_events.jsonl` 全行・全フィールド（壁時計由来 `event_time` 等の揮発フィールドと
    `scan_id` のタイムスタンプ部を除外。行数・順序含め完全一致要求）
  - `small_paper_rejects.csv` / `small_paper_positions.csv` 全行
  - `entry_scan_audit.jsonl` 全行
  - `small_paper_summary.json`（runtime系キー除外で全キー一致）
  - Discord出力（summaryから `discord_message_builder` の決定的関数で導出した本文行の一致）
  - キー指標: candidate数 / ENTRY件数 / PBv2 accepted / OR accepted / EXIT件数 /
    reject reason分布 / stale reason分布 / pbv2_internal_reason分布
- 結果: `regression_summary.csv` / `phase629_report.json` を参照（全日 match=True で完了）

## Stage単体テスト

`src/research/phase629_stage_refactoring.py stagetest`（S1〜S9、全PASS）:

1. S1: Stage0が `Stage0NormalizedPayload` を返す（symbol解決・enrich・trade生成）
2. S2: symbol未解決PUSHで None
3. S3: fresh入力で Stage1 が short-circuit しない
4. S4: stale入力で Stage1 が stale GateDecision + カウンタ
5. S5: Stage2 GateDecision が後段で変異しない + internal reason永続化
6. S6: Stage3 ClusterDecision は frozen dataclass（変異不可）
7. S7: Stage4 が pbv2_internal_reason を保持し final_reject_reason を記録
8. S8: orchestrator end-to-end（candidateイベント1件）+ trace無効時no-op
9. S9: `ENTRY_PIPELINE_STAGE_TRACE=1` で start/end 記録

## Preflight

- `scripts/check_live_pipeline_preflight.py` → `[PREFLIGHT] live pipeline ok`
- `scripts/run_production_startup_smoke_test.py` → `[SMOKE] production startup ok`
（Phase627 preflight含む。リファクタ後コードで両方PASS）

## Rollback

```
git checkout -- kabu_native/src/small_paper/pilot_runner.py
del kabu_native\src\small_paper\entry_pipeline_stages.py
del kabu_native\src\research\phase629_stage_refactoring.py
```

YAML・閾値・Discord文面・他モジュールへの変更は一切ないため、上記のみで完全に戻る。

## 成果物

- `results/reports/phase629_stage_refactoring/phase629_report.json`
- `results/reports/phase629_stage_refactoring/stage_dependency_graph.drawio`
- `results/reports/phase629_stage_refactoring/stage_responsibility.csv`
- `results/reports/phase629_stage_refactoring/stage_io_matrix.csv`
- `results/reports/phase629_stage_refactoring/before_after_callgraph.csv`
- `results/reports/phase629_stage_refactoring/regression_summary.csv`
- `docs/operations/phase629_stage_refactoring.md`（本書）
