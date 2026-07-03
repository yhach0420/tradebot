# Phase608 — PBv2 Gate Pass → Live Accepted Routing Gap Audit

**Verdict:** `phase608_pbv2_gate_pass_to_live_accepted_routing_gap_done`

### 1_replay_evaluated_in_live
NO for 27654/27654 replay-pass rows — live entry_decision was False; only 0 matched live decision.accept True (all OR path, 0 PBv2 gate_pass)

### 2_where_lost_if_evaluated
N/A as routing gap — replay false positives. Live loss is pre-accept: data_stale_price (30k) + pbv2 reject → or_overlay_not_candidate (18k)

### 3_cap_overlap_maxscan_after_pbv2_accept
NO on BAD replay-pass rows (0/27654 had live decision.accept). BAD live OR-only: 18 decision.accept → 18 accepted events (0 max_scan, 0 overlap). 625 GOOD AM: 813 decision.accept → 709 notify → 104 max_scan + 656 overlap → 53 accepted

### 4_or_overlay_overwrite_pbv2_accept
NO — _maybe_try_or_overlay_entry returns early when pbv2_decision.accept is True (pilot_runner:805). or_overlay_not_candidate (33357 audit rows) means pbv2 was False first

### 5_replay_wrong_tick_or_artifact
YES — replay artifact: uncapped cap, shared gate state, no freshness re-check, re-evaluates rejected event snapshots; 27654 replay-pass never had live decision.accept

### 6_first_diff_good_vs_bad_path
625: pbv2 accept → decision.accept True (813 AM) → batch flush → overlap/max_scan → 53 accepted. 629-630: pbv2 rarely passes at live tick (data_stale + guards); OR-only 12 accepts; 0 PBv2 gate_pass

### 7_high_drift_primary_cause
PARTIAL guard not routing — high_drift blocks 11599 replay-pass rows if OFF (+11599); but live PBv2=0 because live decision.accept never True for PBv2 (data_stale + pbv2 guards before OR)

### 8_root_cause_category
replay_diff + guard_overstrict + OR-only live path (NOT routing bug after accept)

### 9_minimal_fix
Fix replay methodology; investigate data_stale_price rate (30k/51k on 629); conditional high_drift relax on Dynamic40; restore PBv2 path before OR fallback

### 10_restore_pre625_pbv2
Phase606 rollback (stop_low_mfe off, cluster csub off) + freshness/board pipeline fix + high_drift conditional relax; no cap/overlap/max_scan change needed on BAD days
