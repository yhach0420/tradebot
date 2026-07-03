# Phase606 — Restore Pre-6/25 PBv2 Full Code Diff Audit

**Verdict:** `phase606_restore_pre625_pbv2_full_code_diff_audit_done`
**Pre-625 baseline commit:** `f50c5a7`

## Mandatory answers

### 1_code_diffs_pbv2_path
['stop_low_mfe_guard (Phase557) — NEW in exposure_gate.py after cluster_guard', 'entry_freshness_board_fallback (Phase603) — YAML key; 630 session runtime SHA drift', 'live_order/capital/adapter/notifier (Phase591-594) — post-accept hooks only', 'volume_gate_relaxation_shadow, vol_liq_cache — shadow/startup, not PBv2 gate', 'OR overlay reason overwrite — pre625 already present (design)']

### 2_direct_cause_pbv2_zero
COMPOUND: (a) entry_cluster_guard blocks PBv2 eval stream #1; (b) OR overlay masks internal reason; (c) 629/630 live accepts fail momentum_low even with guard OFF; (d) 630 config SHA drift (board_fallback=true at session). stop_low_mfe adds incremental blocks post-6/25 but NOT sole cause.

### 3_live_order_hook_pre_entry
NO — all hooks post-accept (_maybe_record_live_order_pipeline_entry after register_entry). Cannot block PBv2 evaluate_entry.

### 4_non_phase603_entry_changes
['stop_low_mfe_guard_enabled (Phase557)', 'live_order_adapter/capital/dry_run/wiring (post-accept)', 'entry_cluster_guard csub reject active (Phase549, present at 6/25 too)', 'OR overlay enabled (Phase538, present at 6/25)', 'session config SHA drift vs disk YAML']

### 5_625_pbv2_reproduce_head
HEAD disk YAML: 0/80 live accepts pass PBv2 replay

### 6_625_fail_conditions_head
{'entry_cluster_guard': 71, 'momentum_low_required': 6, 'near_day_high_low_momentum_dynamic40_guard': 3}

### 7_pre625_config_restores_pbv2
625 regression pre625_H: 71/80 pass; session_config replay: 0/80 pass

### 8_629_630_rollback_conditions
{'A': {'pbv2_live_pass': 0, 'top_blocker': 'entry_score_v2_below_threshold'}, 'B': {'pbv2_live_pass': 0, 'top_blocker': 'entry_score_v2_below_threshold'}, 'C': {'pbv2_live_pass': 0, 'top_blocker': 'entry_score_v2_below_threshold'}, 'D': {'pbv2_live_pass': 0, 'top_blocker': 'entry_score_v2_below_threshold'}, 'D2': {'pbv2_live_pass': 0, 'top_blocker': 'entry_score_v2_below_threshold'}, 'E': {'pbv2_live_pass': 0, 'top_blocker': 'entry_score_v2_below_threshold'}, 'F': {'pbv2_live_pass': 0, 'top_blocker': 'entry_score_v2_below_threshold'}, 'G': {'pbv2_live_pass': 0, 'top_blocker': 'entry_score_v2_below_threshold'}, 'H': {'pbv2_live_pass': 0, 'top_blocker': 'entry_score_v2_below_threshold'}, 'H2': {'pbv2_live_pass': 0, 'top_blocker': 'entry_score_v2_below_threshold'}}

### 9_or_overlay_replaces_pbv2
YES for audit — OR accepts when PBv2 fails; overwrites gate_reject_reason. 629/630 accepted_count == or_entry_count.

### 10_config_drift_cause
YES — all sessions SHA != current disk; 630 session SHA matches board_fallback=true variant per Phase604.

### 11_minimal_rollback
[{'candidate_id': '1_phase603_off', 'scenario_id': 'B', 'label': 'board_fallback OFF', 'pbv2_live_accept_recovery_629_630': 0, 'matched_pnl_629_630': 0.0, 'residual_blocker': 'entry_cluster_guard', '625_head_pass': 0, '625_pre625_pass': 71, 'rollback_scope': 'yaml', 'adopt_recommendation': 'NO_EFFECT', 'risk': 'LOW', 'side_effect': ''}, {'candidate_id': '2_cluster_csub_off', 'scenario_id': 'D', 'label': 'cluster reject_csubs=[]', 'pbv2_live_accept_recovery_629_630': 0, 'matched_pnl_629_630': 0.0, 'residual_blocker': 'entry_score_v2_below_threshold', '625_head_pass': 0, '625_pre625_pass': 71, 'rollback_scope': 'yaml', 'adopt_recommendation': 'NO', 'risk': 'MEDIUM', 'side_effect': ''}, {'candidate_id': '3_cluster_off', 'scenario_id': 'D2', 'label': 'cluster guard OFF', 'pbv2_live_accept_recovery_629_630': 0, 'matched_pnl_629_630': 0.0, 'residual_blocker': 'entry_score_v2_below_threshold', '625_head_pass': 0, '625_pre625_pass': 71, 'rollback_scope': 'yaml', 'adopt_recommendation': 'NO', 'risk': 'HIGH', 'side_effect': ''}, {'candidate_id': '4_guards_pre625', 'scenario_id': 'E', 'label': 'pullback/near_day/high_drift pre625', 'pbv2_live_accept_recovery_629_630': 0, 'matched_pnl_629_630': 0.0, 'residual_blocker': 'entry_cluster_guard', '625_head_pass': 0, '625_pre625_pass': 71, 'rollback_scope': 'yaml', 'adopt_recommendation': 'NO', 'risk': 'HIGH', 'side_effect': ''}, {'candidate_id': '5_or_off', 'scenario_id': 'C', 'label': 'OR overlay OFF', 'pbv2_live_accept_recovery_629_630': 0, 'matched_pnl_629_630': 0.0, 'residual_blocker': 'entry_cluster_guard', '625_head_pass': 0, '625_pre625_pass': 71, 'rollback_scope': 'yaml', 'adopt_recommendation': 'NO', 'risk': 'HIGH', 'side_effect': 'OR-only entries lost if OR off'}, {'candidate_id': '6_live_order_off', 'scenario_id': 'G', 'label': 'live order hooks OFF', 'pbv2_live_accept_recovery_629_630': 0, 'matched_pnl_629_630': 0.0, 'residual_blocker': 'entry_cluster_guard', '625_head_pass': 0, '625_pre625_pass': 71, 'rollback_scope': 'yaml+verify_hooks_post_accept', 'adopt_recommendation': 'NO_EFFECT', 'risk': 'LOW', 'side_effect': ''}, {'candidate_id': '7_pre625_full', 'scenario_id': 'H', 'label': 'pre625 ENTRY full', 'pbv2_live_accept_recovery_629_630': 0, 'matched_pnl_629_630': 0.0, 'residual_blocker': 'entry_cluster_guard', '625_head_pass': 0, '625_pre625_pass': 71, 'rollback_scope': 'yaml', 'adopt_recommendation': 'YES_CONFIG', 'risk': 'MEDIUM', 'side_effect': ''}, {'candidate_id': '8_pre625_or_cluster', 'scenario_id': 'H2', 'label': 'pre625 + csub off + OR off', 'pbv2_live_accept_recovery_629_630': 0, 'matched_pnl_629_630': 0.0, 'residual_blocker': 'entry_score_v2_below_threshold', '625_head_pass': 0, '625_pre625_pass': 71, 'rollback_scope': 'yaml', 'adopt_recommendation': 'NO', 'risk': 'HIGH', 'side_effect': ''}]

### 12_immediate_code_config_restore
['YAML: stop_low_mfe_guard_enabled=false (Phase557 rollback)', 'YAML: entry_freshness_board_fallback_enabled=false + preflight SHA assert', 'YAML: entry_cluster_guard_reject_csubs=[] OR tune csub (Phase605: restores 625 44/53)', 'Code: pilot_runner save pbv2_internal_reason before OR overwrite', 'NOT required for ENTRY: live_order hooks OFF (post-accept only)']

### 13_restore_before_pm_today
YES for config: stop_low_mfe OFF + cluster csub relax + SHA preflight. Code pbv2_internal_reason can wait.

### 14_safe_ops_tomorrow
Session start preflight SHA check; monitor pbv2_count vs or_entry_count; disable board_fallback at runtime if disk false.

## Rollback matrix (629/630)

- 20260629 A: live PBv2 pass=0 blocker=entry_cluster_guard
- 20260629 B: live PBv2 pass=0 blocker=entry_cluster_guard
- 20260629 C: live PBv2 pass=0 blocker=entry_cluster_guard
- 20260629 D: live PBv2 pass=0 blocker=entry_score_v2_below_threshold
- 20260629 D2: live PBv2 pass=0 blocker=entry_score_v2_below_threshold
- 20260629 E: live PBv2 pass=0 blocker=entry_cluster_guard
- 20260629 F: live PBv2 pass=0 blocker=entry_cluster_guard
- 20260629 G: live PBv2 pass=0 blocker=entry_cluster_guard
- 20260629 H: live PBv2 pass=0 blocker=entry_cluster_guard
- 20260629 H2: live PBv2 pass=0 blocker=entry_score_v2_below_threshold
- 20260630 A: live PBv2 pass=0 blocker=entry_score_v2_below_threshold
- 20260630 B: live PBv2 pass=0 blocker=entry_score_v2_below_threshold
- 20260630 C: live PBv2 pass=0 blocker=entry_score_v2_below_threshold
- 20260630 D: live PBv2 pass=0 blocker=entry_score_v2_below_threshold
- 20260630 D2: live PBv2 pass=0 blocker=entry_score_v2_below_threshold
- 20260630 E: live PBv2 pass=0 blocker=entry_score_v2_below_threshold
- 20260630 F: live PBv2 pass=0 blocker=entry_score_v2_below_threshold
- 20260630 G: live PBv2 pass=0 blocker=entry_score_v2_below_threshold
- 20260630 H: live PBv2 pass=0 blocker=entry_score_v2_below_threshold
- 20260630 H2: live PBv2 pass=0 blocker=entry_score_v2_below_threshold

## Config timeline

- 20260624 pbv2=None or=None sha_match=False
- 20260625 pbv2=43 or=10 sha_match=False
- 20260625 pbv2=27 or=0 sha_match=False
- 20260629 pbv2=0 or=12 sha_match=False
- 20260630 pbv2=0 or=6 sha_match=False

## Minimal rollback plan

- 1_phase603_off board_fallback OFF: adopt=NO_EFFECT risk=LOW
- 2_cluster_csub_off cluster reject_csubs=[]: adopt=NO risk=MEDIUM
- 3_cluster_off cluster guard OFF: adopt=NO risk=HIGH
- 4_guards_pre625 pullback/near_day/high_drift pre625: adopt=NO risk=HIGH
- 5_or_off OR overlay OFF: adopt=NO risk=HIGH
- 6_live_order_off live order hooks OFF: adopt=NO_EFFECT risk=LOW
- 7_pre625_full pre625 ENTRY full: adopt=YES_CONFIG risk=MEDIUM
- 8_pre625_or_cluster pre625 + csub off + OR off: adopt=NO risk=HIGH