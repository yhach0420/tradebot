# Phase609 — high_drift_pullback Root Cause Audit

**Verdict:** `phase609_high_drift_pullback_root_cause_audit_done`

### 1_introduced_when
Phase439, commit 95e70e1, 2026-06-19

### 2_existed_before_625
YES — deployed before 6/25 sessions

### 3_changed_after_625
NO code/threshold change; YAML other guards changed (stop_low_mfe, cluster)

### 4_fire_rate_diff
live reject: GOOD=9404 vs BAD=6; replay first_blocker rate mean: GOOD=21.94% vs BAD=22.35%

### 5_features_used
day_high_distance_pct, entry_rise_5min/10/15_pct, universe_slot dynamic40

### 6_input_anomaly
BAD higher replay hd block rate; live 629 hd=0 due data_stale pre-gate; missing r5/r10 more common on pullback patterns

### 7_impl_bug_likelihood
LOW formula bug; MEDIUM replay/live parity + r5=None over-block edge

### 8_pbv2_recovery_hd_off
replay +11599 accept keys on BAD sessions (uncapped)

### 9_recovered_performance
incremental PnL yen*100=-139.5; false_positive_proxy=202/5002 missed_winner=18/5002

### 10_recommendation
CONDITIONAL_RELAX — not full OFF; tighten r5=None path; fix freshness before hd

### 11_minimal_fix_pre625_pbv2
data_stale fix + high_drift r5=None guard + Phase606 rollback guards

### 12_deploy_today
NO runtime change; monitor data_stale; plan conditional hd relax after freshness fix
