# Phase628: Paper Trade Runtime Architecture

対象: 最新HEAD(Phase621 freshness semantics v2 / Phase627 cluster guard production fix /
Phase616 CoreRuntimeMode + ExtensionBus 反映後)。
図はすべて本線コードを実際に読んで作成した(推測なし)。本線コード・ロジックは一切変更していない。

## 成果物

| 種類 | ファイル |
|---|---|
| シーケンス図(3パターン) | `results/reports/phase628_architecture/phase628_sequence.drawio` / `.md` |
| クラス図 | `phase628_class.drawio` / `.md` |
| フローチャート | `phase628_flowchart.drawio` / `.md` |
| 状態遷移図(Session / Position) | `phase628_state.drawio` / `.md` |
| ファイルマップ | `phase628_file_map.csv` |
| 責務マトリクス | `phase628_responsibility_matrix.csv` |
| レポート | `phase628_report.json` |

## ブロック構成(A〜K)

- **A. 起動・Preflight**: `run_paper_trade.bat` → `check_live_pipeline_preflight.py`(config SHA pin + `phase627_preflight_checks` + historical case)→ `run_production_startup_smoke_test.py`(gate/guard実構築 + Phase627チェック)→ `run_core10_dynamic40_am_pm_daily_runner.py` → `runner/am_pm_daily_runner.py` の `preflight()`(`verify_config_safety` + `run_safety_check` + core10 + kabu register pre-clear)。いずれかの fail で起動しない。
- **B. Config / Mode**: production YAML → `load_pilot_config` → `SmallPaperPilotConfig`(frozen)。`finalize_core_runtime_config` が `CoreRuntimeMode`(CORE_ONLY / CORE_PLUS_AUDIT / FULL_EXTENSION)を解決し、FULL_EXTENSION 以外では `EXTENSION_FLAGS_OFF` を強制適用。`production_config_sha256.pin` と `config_file_sha256` の照合は preflight で実施。
- **C. Universe / Kabu API**: `build_am_universe` / `build_pm_universe` / `build_intraday_refresh_universes`(10:00/14:30 差し替えは `_maybe_intraday_refresh`)。kabu は `issue_token_from_env` → `KabuNativePushClient` → `register_symbols_cleared`(4002006 は unregister/all → リトライ)→ WebSocket `iter_messages`。切断時は `_reconnect_push`。
- **D. Push Ingest / Feature Build**: `_process_push_payload` が銘柄別 throttle 済み payload を受け、`LiveFeatureBridge` の `enrich_payload` と price ring / board ring 更新、`attach_entry_metrics_to_trade` で candidate trade を構築。
- **E. Freshness v2**: `entry_scan_controller._evaluate_freshness_semantics_v2`。優先順: `event_stale`(recorded_at age>3s, reject)→ `board_stale`(>3s, reject)→ `trade_stale`(>10s, `trade_stale_mode=tag_only` なら `liquidity_stale_trade` タグのみで通過)。
- **F. ENTRY / PBv2**: `ExposureGate.evaluate_entry` のチェーン(profile → window → cooloff → price_risk → pullback_misread → high_drift → weak_shape → near_day_high → suitability → momentum cutoff / board mid+high / score_v2 → late_chase → RSI guard×2 → entry_quality(spread/update)→ **entry_cluster_guard**(Phase627: `_reject_stage_missing_features` で欠損検出時は `FEATURE_INCOMPLETE` tag のみ・reject 禁止)→ stop_low_mfe → risk_cluster → daily_loss → CAP)。
- **G. OR Overlay**: PBv2 reject 時のみ `_maybe_try_or_overlay_entry` → `evaluate_or_overlay_entry`(session gates → O_R003 day-high → `or_overlay_not_candidate` / `or_cap_full` / accept)。Phase627 により `pbv2_internal_reason` / `pbv2_internal_gate` は OR 呼び出し前に保存され、`or_overlay_reason` / `final_reject_reason` と併記される。
- **H. Accept / Position**: `queue_accepted_candidate` → `maybe_flush_after_eval`(`max_entries_per_scan`、score5 優先)→ `_execute_accepted_entry` → `ObserverPositionTracker.register_entry`(same-symbol overlap は cap kwargs / `has_open` で防止)→ `append_position_row` / events。
- **I. EXIT**: `observer.on_tick` で stop_hit(`price <= stop_price`)、board dynamic trailing(`trailing_mfe_params` による tier 別 activate/giveback)、take/hold 判定。shadow exit monitors は Extension(記録のみ)。セッション終了時は `close_all(session_end / am_pm force close)`。
- **J. Extension Layer**: `ExtensionBus.maybe_create`(CORE_ONLY では None)。hooks は `on_push_tick`(trace/shadow)、`mark_pbv2_start/end`、`on_post_eval`(volume shadow)、`on_session_end`(shadow finalize、step 毎 try/except で error 隔離)。audit は CORE_PLUS_AUDIT 以上。live_order/capital/notifier と Discord は FULL_EXTENSION のみ。
- **K. Logging / Reports / Alerts**: `LiveSessionWriter` が `small_paper_events` / `small_paper_rejects`(`pbv2_internal_reason` 等4フィールド含む)/ `small_paper_positions` / `small_paper_summary` を incremental 書き出し。summary で `_gate_dominance_alert_fields`(warning≥80% / critical≥95%、最小50件)と `FEATURE_INCOMPLETE` カウントを記録し Discord に表示。

## 必須回答

1. **Core hot path は何か** — PUSH 受信(`push.iter_messages`)→ `_process_push_payload` → `enrich_payload`(LiveFeatureBridge)→ `evaluate_entry_data_freshness`(v2)→ `ExposureGate.evaluate_entry`(PBv2)→(reject 時)`_record_pbv2_internal_reject` + `_maybe_try_or_overlay_entry` →(accept 時)scan flush → `_execute_accepted_entry` → `observer.register_entry` / `on_tick`(EXIT)→ writer 出力。この経路はすべて Core で、CORE_ONLY モードでも同一。

2. **Extension はどこから呼ばれるか** — `_process_push_payload` 内の `ExtensionBus` hooks のみ: `on_push_tick`(enrich 後)、`mark_pbv2_start/end`(gate 前後)、`on_post_eval`(判定記録後)、`on_session_end`(summary 構築時、`_build_live_summary` 経由)。`ExtensionBus.maybe_create` が CORE_ONLY で None を返すため、hook 呼び出し自体が消える。

3. **ENTRY判断を変えられるコンポーネントはどれか** — (a) freshness v2(event_stale/board_stale reject)、(b) `EntryScanController` の batch 選抜(`max_entries_per_scan`)、(c) `ExposureGate.evaluate_entry` のチェーン全 guard(cluster guard 含む)、(d) OR overlay(PBv2 reject の最終判定を accept に変え得る)、(e) CAP/overlap(`pbv2_cap_kwargs` / observer open 状況)。この5つのみ、すべて Core。

4. **ENTRY判断を変えてはいけない Extension はどれか** — ExtensionBus の全 hook(latency trace、board/momentum/volume/quality/trading_value/board_imbalance/expectancy 各 shadow)、exit shadow monitors、live_order adapter/capital/notifier、Discord。これらは記録・通知のみで `GateDecision` に触らない(`extension_bus.py` の docstring にも "never changes Core gate decisions" と明記)。

5. **freshness v2 はどこにあるか** — `src/small_paper/entry_scan_controller.py` の `_evaluate_freshness_semantics_v2`(`evaluate_entry_data_freshness` から `freshness_semantics_v2_enabled=true` で分岐)。閾値は event/board=3s、trade=10s、`trade_stale_mode=tag_only`。

6. **cluster guard safety はどこにあるか** — `src/small_paper/entry_cluster_guard.py` の `EntryClusterGuardState.check`。reject 分類が出た段の特徴量を `_reject_stage_missing_features` で 0埋め前の raw 実在性検査し、欠損があれば `blocked=False` + `CLUSTER_GUARD_FEATURE_INCOMPLETE` tag のみ(Phase627)。起動時は `phase627_preflight.py` の `_check_feature_completeness_safety` が実動作を確認。

7. **OR overlay reason mask はどう防いでいるか** — `_evaluate_gate_entry` の reject 直後・OR overlay 呼び出し前に `_record_pbv2_internal_reject` が `trade["pbv2_internal_reason"]` / `pbv2_internal_gate` を保存し `pbv2_internal_reason_counts` を加算。OR の結果は `or_overlay_reason` / `final_reject_reason` として別フィールドに記録され、`EVENT_FIELDS` に4フィールドとも配線済み。preflight `_check_or_overlay_mask_preserves_internal_reason` が mask 後の残存を毎起動検証する。

8. **gate dominance alert はどこで出るか** — `pilot_runner._gate_dominance_alert_fields`(PBv2 internal + stale reason の合算、warning≥80% / critical≥95%、最小50件)。summary JSON(`small_paper_summary.json`)に記録され、`discord_message_builder.format_gate_dominance_alert_lines` が Discord embed に表示。取引は停止しない。

9. **session_end で落ちない設計になっているか** — なっている。(a) `ExtensionBus.on_session_end` は shadow finalize を step 毎に try/except で隔離し `extension_errors` に集約、(b) PUSH ループの `finally` で scan flush と `unregister_all` を実施(unregister 失敗も握りつぶし)、(c) `observer.close_all` → summary 書き出しはループ外で実行、(d) daily runner 側も `run_daily_runner` の try/except で `runner_exception` として verdict/outputs を必ず書く。session end の失敗が summary 欠損に直結しない。

10. **次にリファクタすべき箇所はどこか** — 最有力は `pilot_runner.py`(5,600行超)の `_process_push_payload` / `run_live_dry_run`。ENTRY パイプライン(freshness → PBv2 → internal reason → OR → flush → accept)がクロージャと巨大関数に密結合しており、Phase627 で保証した「reason 保存順序」がコード配置に依存している。段階を明示した pipeline stage 化(pure function 列)に分離すれば、preflight の機能テストが構造保証に変わる。次点は `_LiveRunState` の肥大(shadow state と core カウンタの混在)の分離。

## Rollback / 検証

- 本Phaseは読み取り専用(図・CSV・レポート生成のみ)。本線コードの変更は無い(`git status` で対象コード無変更を確認可能)。
- drawio は app.diagrams.net / VSCode Draw.io Integration で開ける(XML パース検証済み)。mermaid md は GitHub / VSCode でレンダリング可能。

verdict = phase628_paper_trade_runtime_architecture_done
