# Phase610 — Runtime Structure Diff Audit

**Verdict:** `phase610_runtime_structure_diff_audit_done`

### 1_payload_structure_same
YES — events CSV schema identical; trade field pipeline unchanged f50c5a7→HEAD

### 2_pbv2_eval_candidate_construction_same
YES — same _process_push_payload → freshness → _evaluate_gate_entry path

### 3_replay_live_candidate_parity
NO — 625 AM overlap 800/813; 629 AM replay_pass=6650 vs live_decision_true=12

### 4_session_config_cache_refresh_diff
YES structural adds: vol_liq cache (629/630), live_order hooks (629/630), config_sha drift; SAME: poll_interval=5, intraday_refresh pattern, batch/scan settings

### 5_or_liveorder_indirect_state
NO — OR only after PBv2 reject; LiveOrder hooks post-accept only (phase606/608 confirmed)

### 6_pre625_config_on_629_630_restores_pbv2
NO live — replay pass uncapped=6650 but live_pbv2=0; live accepts remain OR-only

### 7_head_on_625_maintains_pbv2
PARTIAL replay — head replay pass=8112 live_pbv2=43; session frozen config matches live outcome

### 8_structural_root_cause
ADDED_POST_625 modules (vol_liq cache, live_order wiring, stop_low_mfe) do not alter PBv2 eval path; collapse = live freshness short-circuit (data_stale_price) + OR-only live accepts, NOT pipeline reorder

### 9_minimal_structural_rollback
Disable vol_liq_startup_cache + live_order dry-run wiring for parity test; primary fix is freshness/timestamp pipeline (Phase602/603), not PBv2 call order rollback
