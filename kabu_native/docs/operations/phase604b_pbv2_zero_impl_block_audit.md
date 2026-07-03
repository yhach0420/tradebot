# Phase604B — PBv2=0 Implementation Block Audit

**Verdict:** `phase604b_pbv2_zero_impl_block_audit_done`

## Mandatory answers

1. **1_pbv2_evaluate_entry_calls_630:** 7660
2. **2_pbv2_pre_accept_reached_630:** 7660
3. **3_pbv2_accept_branch_live_630:** 0
4. **3b_pbv2_accept_branch_replay_630:** 0
5. **4_pbv2_accept_post_crushed_630:** 0
6. **5_or_overwrites_pbv2_internal_reason_630:** 7572
7. **6_true_first_blocker_630_replay:** [('entry_cluster_guard', 4289), ('momentum_low_required', 1115), ('high_drift_pullback', 1011), ('near_day_high_low_momentum_dynamic40_guard', 591), ('entry_score_v2_below_threshold', 342), ('entry_quality_guard_update_count', 218), ('late_chase_guard', 67), ('entry_quality_guard_spread', 27)]
8. **6b_live_630_accepts_all_fail_pbv2:** momentum_low_required (6/6 OR-only accepts)
9. **7_diff_vs_625:** {'625_live_pbv2_count': 43, '625_live_or_count': 10, '629_live_pbv2_count': 0, '630_live_pbv2_count': 0, '625_replay_with_cluster_off': '44/53 live accepts would pass PBv2 (probe)', '630_live_accepts_or_only': True}
10. **8_cap_duplicate_max_scan:** NOT blocking PBv2 — overlap=0, max_concurrent=0 on 629/630
11. **9_runtime_config:** [{'day': '20260630', 'session': 'live_session_091118', 'config_path': 'C:\\Users\\yhach\\Documents\\tradebotfile\\kabu_native\\configs\\small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml', 'config_sha256_session': '4e82dc8753e89ec9a48018918690cc856ef323f15a82a96baac81b866b6d38c2', 'config_sha256_disk_yaml': '2cd21ca2d5721544ef4835e45df577b546183d961d5227f8e8de16d0bde3602f', 'config_sha_match_disk': False, 'or_overlay_enabled': True, 'cap_pbv2': 4, 'cap_or': 1, 'max_concurrent_positions': 5, 'entry_score_v2_min': 3, 'momentum_score_cutoff_max': 0.2546, 'min_continuation_quality': 0.7, 'reject_below_quality': False, 'entry_freshness_board_fallback_enabled': False, 'enable_pullback_misread_dynamic40_guard': False, 'enable_near_day_high_low_momentum_dynamic40_guard': True, 'stop_low_mfe_guard_enabled': True, 'position_cap_mode': True, 'same_symbol_open_policy': 'no_overlap_replace', 'daytrade_suitability_enabled': True, 'daytrade_suitability_threshold': 54.695739, 'pbv2_count_live': 0, 'or_entry_count_live': 6, 'accepted_count_live': 6}, {'day': '20260629', 'session': 'live_session_080236', 'config_path': 'C:\\Users\\yhach\\Documents\\tradebotfile\\kabu_native\\configs\\small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml', 'config_sha256_session': '1281308bb811110d09fc9919ccd431b4ea4d6fa9f3822ad31f2273388c14d2a6', 'config_sha256_disk_yaml': '2cd21ca2d5721544ef4835e45df577b546183d961d5227f8e8de16d0bde3602f', 'config_sha_match_disk': False, 'or_overlay_enabled': True, 'cap_pbv2': 4, 'cap_or': 1, 'max_concurrent_positions': 5, 'entry_score_v2_min': 3, 'momentum_score_cutoff_max': 0.2546, 'min_continuation_quality': 0.7, 'reject_below_quality': False, 'entry_freshness_board_fallback_enabled': False, 'enable_pullback_misread_dynamic40_guard': False, 'enable_near_day_high_low_momentum_dynamic40_guard': True, 'stop_low_mfe_guard_enabled': True, 'position_cap_mode': True, 'same_symbol_open_policy': 'no_overlap_replace', 'daytrade_suitability_enabled': True, 'daytrade_suitability_threshold': 54.695739, 'pbv2_count_live': 0, 'or_entry_count_live': 12, 'accepted_count_live': 12}, {'day': '20260629', 'session': 'live_session_122526', 'config_path': 'C:\\Users\\yhach\\Documents\\tradebotfile\\kabu_native\\configs\\small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml', 'config_sha256_session': '1281308bb811110d09fc9919ccd431b4ea4d6fa9f3822ad31f2273388c14d2a6', 'config_sha256_disk_yaml': '2cd21ca2d5721544ef4835e45df577b546183d961d5227f8e8de16d0bde3602f', 'config_sha_match_disk': False, 'or_overlay_enabled': True, 'cap_pbv2': 4, 'cap_or': 1, 'max_concurrent_positions': 5, 'entry_score_v2_min': 3, 'momentum_score_cutoff_max': 0.2546, 'min_continuation_quality': 0.7, 'reject_below_quality': False, 'entry_freshness_board_fallback_enabled': False, 'enable_pullback_misread_dynamic40_guard': False, 'enable_near_day_high_low_momentum_dynamic40_guard': True, 'stop_low_mfe_guard_enabled': True, 'position_cap_mode': True, 'same_symbol_open_policy': 'no_overlap_replace', 'daytrade_suitability_enabled': True, 'daytrade_suitability_threshold': 54.695739, 'pbv2_count_live': 0, 'or_entry_count_live': 0, 'accepted_count_live': 0}, {'day': '20260625', 'session': 'live_session_080340', 'config_path': 'C:\\Users\\yhach\\Documents\\tradebotfile\\kabu_native\\configs\\small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml', 'config_sha256_session': '244aa7685dde31547220414d5e7a71d5022ee927687b35a0273f71352816689f', 'config_sha256_disk_yaml': '2cd21ca2d5721544ef4835e45df577b546183d961d5227f8e8de16d0bde3602f', 'config_sha_match_disk': False, 'or_overlay_enabled': True, 'cap_pbv2': 4, 'cap_or': 1, 'max_concurrent_positions': 5, 'entry_score_v2_min': 3, 'momentum_score_cutoff_max': 0.2546, 'min_continuation_quality': 0.7, 'reject_below_quality': False, 'entry_freshness_board_fallback_enabled': False, 'enable_pullback_misread_dynamic40_guard': False, 'enable_near_day_high_low_momentum_dynamic40_guard': True, 'stop_low_mfe_guard_enabled': True, 'position_cap_mode': True, 'same_symbol_open_policy': 'no_overlap_replace', 'daytrade_suitability_enabled': True, 'daytrade_suitability_threshold': 54.695739, 'pbv2_count_live': 43, 'or_entry_count_live': 10, 'accepted_count_live': 53}, {'day': '20260625', 'session': 'live_session_122535', 'config_path': 'C:\\Users\\yhach\\Documents\\tradebotfile\\kabu_native\\configs\\small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml', 'config_sha256_session': '244aa7685dde31547220414d5e7a71d5022ee927687b35a0273f71352816689f', 'config_sha256_disk_yaml': '2cd21ca2d5721544ef4835e45df577b546183d961d5227f8e8de16d0bde3602f', 'config_sha_match_disk': False, 'or_overlay_enabled': True, 'cap_pbv2': 4, 'cap_or': 1, 'max_concurrent_positions': 5, 'entry_score_v2_min': 3, 'momentum_score_cutoff_max': 0.2546, 'min_continuation_quality': 0.7, 'reject_below_quality': False, 'entry_freshness_board_fallback_enabled': False, 'enable_pullback_misread_dynamic40_guard': False, 'enable_near_day_high_low_momentum_dynamic40_guard': True, 'stop_low_mfe_guard_enabled': True, 'position_cap_mode': True, 'same_symbol_open_policy': 'no_overlap_replace', 'daytrade_suitability_enabled': True, 'daytrade_suitability_threshold': 54.695739, 'pbv2_count_live': 27, 'or_entry_count_live': 0, 'accepted_count_live': 27}, {'day': '20260624', 'session': 'live_session_081514', 'config_path': 'C:\\Users\\yhach\\Documents\\tradebotfile\\kabu_native\\configs\\small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml', 'config_sha256_session': '8853dabba019968360b34a1ce3c37f782c4bcf62921dd1e9d02ad43a02f2f53e', 'config_sha256_disk_yaml': '2cd21ca2d5721544ef4835e45df577b546183d961d5227f8e8de16d0bde3602f', 'config_sha_match_disk': False, 'or_overlay_enabled': True, 'cap_pbv2': 4, 'cap_or': 1, 'max_concurrent_positions': 5, 'entry_score_v2_min': 3, 'momentum_score_cutoff_max': 0.2546, 'min_continuation_quality': 0.7, 'reject_below_quality': False, 'entry_freshness_board_fallback_enabled': False, 'enable_pullback_misread_dynamic40_guard': False, 'enable_near_day_high_low_momentum_dynamic40_guard': True, 'stop_low_mfe_guard_enabled': True, 'position_cap_mode': True, 'same_symbol_open_policy': 'no_overlap_replace', 'daytrade_suitability_enabled': True, 'daytrade_suitability_threshold': 54.695739, 'pbv2_count_live': None, 'or_entry_count_live': None, 'accepted_count_live': 61}]
12. **10_suspicious_code_diff_since_625:** [{'file': 'src/research/exposure_gate.py', 'commit_line': '924bb1e kabutrade0628'}, {'file': 'src/research/exposure_gate.py', 'commit_line': 'f50c5a7 kabutrade0626'}, {'file': 'src/small_paper/pilot_runner.py', 'commit_line': '924bb1e kabutrade0628'}, {'file': 'src/small_paper/pilot_runner.py', 'commit_line': 'f50c5a7 kabutrade0626'}, {'file': 'src/small_paper/or_overlay_entry.py', 'commit_line': 'f50c5a7 kabutrade0626'}, {'file': 'src/small_paper/or_overlay_cap.py', 'commit_line': 'f50c5a7 kabutrade0626'}, {'file': 'src/small_paper/config.py', 'commit_line': '924bb1e kabutrade0628'}, {'file': 'src/small_paper/config.py', 'commit_line': 'f50c5a7 kabutrade0626'}, {'file': 'configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml', 'commit_line': '924bb1e kabutrade0628'}, {'file': 'configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml', 'commit_line': 'f50c5a7 kabutrade0626'}]
13. **11_classification:** design_or_reason_overwrite (pilot_runner.py:2269-2377) + guard_stack_blocks_pbv2 (entry_cluster_guard exposure_gate.py:551, momentum_low exposure_gate.py:452); 6/29-6/30 all accepts OR-only (or_entry_count=accepted_count); NOT cap/post-accept crush
14. **12_immediate_actions:** ['pilot_runner.py:2377 — preserve pbv2_internal_reason before OR overwrite in gate_reject_reason', 'pilot_runner.py:2269 — OR overlay must not hide PBv2 first blocker in audit/events', 'entry_cluster_guard (exposure_gate.py:551) — primary PBv2 blocker on replay; 44/53 6/25 accepts pass with cluster OFF', 'Session startup — assert config_sha256 matches intended YAML (630 had board_fallback=true drift)', 'Do NOT rollback OR overlay alone — 6/30 entries are OR-only by design when PBv2 fails']

## Branch stats 6/30

- PBv2 branch reached: 7660
- PBv2 accept branch (replay): 0
- OR overwrite count: 7572
- Top internal blockers: [('entry_cluster_guard', 4289), ('momentum_low_required', 1115), ('high_drift_pullback', 1011), ('near_day_high_low_momentum_dynamic40_guard', 591), ('entry_score_v2_below_threshold', 342), ('entry_quality_guard_update_count', 218), ('late_chase_guard', 67), ('entry_quality_guard_spread', 27)]

## vs 6/25 AM

- PBv2 accept branch replay: 0
- Top internal blockers: [('entry_cluster_guard', 7445), ('high_drift_pullback', 6642), ('momentum_low_required', 4788), ('near_day_high_low_momentum_dynamic40_guard', 4236), ('entry_score_v2_below_threshold', 1299), ('entry_quality_guard_update_count', 490), ('late_chase_guard', 287), ('entry_quality_guard_spread', 127)]

## Effective runtime configs

```json
[
  {
    "day": "20260630",
    "session": "live_session_091118",
    "config_path": "C:\\Users\\yhach\\Documents\\tradebotfile\\kabu_native\\configs\\small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml",
    "config_sha256_session": "4e82dc8753e89ec9a48018918690cc856ef323f15a82a96baac81b866b6d38c2",
    "config_sha256_disk_yaml": "2cd21ca2d5721544ef4835e45df577b546183d961d5227f8e8de16d0bde3602f",
    "config_sha_match_disk": false,
    "or_overlay_enabled": true,
    "cap_pbv2": 4,
    "cap_or": 1,
    "max_concurrent_positions": 5,
    "entry_score_v2_min": 3,
    "momentum_score_cutoff_max": 0.2546,
    "min_continuation_quality": 0.7,
    "reject_below_quality": false,
    "entry_freshness_board_fallback_enabled": false,
    "enable_pullback_misread_dynamic40_guard": false,
    "enable_near_day_high_low_momentum_dynamic40_guard": true,
    "stop_low_mfe_guard_enabled": true,
    "position_cap_mode": true,
    "same_symbol_open_policy": "no_overlap_replace",
    "daytrade_suitability_enabled": true,
    "daytrade_suitability_threshold": 54.695739,
    "pbv2_count_live": 0,
    "or_entry_count_live": 6,
    "accepted_count_live": 6
  },
  {
    "day": "20260629",
    "session": "live_session_080236",
    "config_path": "C:\\Users\\yhach\\Documents\\tradebotfile\\kabu_native\\configs\\small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml",
    "config_sha256_session": "1281308bb811110d09fc9919ccd431b4ea4d6fa9f3822ad31f2273388c14d2a6",
    "config_sha256_disk_yaml": "2cd21ca2d5721544ef4835e45df577b546183d961d5227f8e8de16d0bde3602f",
    "config_sha_match_disk": false,
    "or_overlay_enabled": true,
    "cap_pbv2": 4,
    "cap_or": 1,
    "max_concurrent_positions": 5,
    "entry_score_v2_min": 3,
    "momentum_score_cutoff_max": 0.2546,
    "min_continuation_quality": 0.7,
    "reject_below_quality": false,
    "entry_freshness_board_fallback_enabled": false,
    "enable_pullback_misread_dynamic40_guard": false,
    "enable_near_day_high_low_momentum_dynamic40_guard": true,
    "stop_low_mfe_guard_enabled": true,
    "position_cap_mode": true,
    "same_symbol_open_policy": "no_overlap_replace",
    "daytrade_suitability_enabled": true,
    "daytrade_suitability_threshold": 54.695739,
    "pbv2_count_live": 0,
    "or_entry_count_live": 12,
    "accepted_count_live": 12
  },
  {
    "day": "20260629",
    "session": "live_session_122526",
    "config_path": "C:\\Users\\yhach\\Documents\\tradebotfile\\kabu_native\\configs\\small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml",
    "config_sha256_session": "1281308bb811110d09fc9919ccd431b4ea4d6fa9f3822ad31f2273388c14d2a6",
    "config_sha256_disk_yaml": "2cd21ca2d5721544ef4835e45df577b546183d961d5227f8e8de16d0bde3602f",
    "config_sha_match_disk": false,
    "or_overlay_enabled": true,
    "cap_pbv2": 4,
    "cap_or": 1,
    "max_concurrent_positions": 5,
    "entry_score_v2_min": 3,
    "momentum_score_cutoff_max": 0.2546,
    "min_continuation_quality": 0.7,
    "reject_below_quality": false,
    "entry_freshness_board_fallback_enabled": false,
    "enable_pullback_misread_dynamic40_guard": false,
    "enable_near_day_high_low_momentum_dynamic40_guard": true,
    "stop_low_mfe_guard_enabled": true,
    "position_cap_mode": true,
    "same_symbol_open_policy": "no_overlap_replace",
    "daytrade_suitability_enabled": true,
    "daytrade_suitability_threshold": 54.695739,
    "pbv2_count_live": 0,
    "or_entry_count_live": 0,
    "accepted_count_live": 0
  },
  {
    "day": "20260625",
    "session": "live_session_080340",
    "config_path": "C:\\Users\\yhach\\Documents\\tradebotfile\\kabu_native\\configs\\small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml",
    "config_sha256_session": "244aa7685dde31547220414d5e7a71d5022ee927687b35a0273f71352816689f",
    "config_sha256_disk_yaml": "2cd21ca2d5721544ef4835e45df577b546183d961d5227f8e8de16d0bde3602f",
    "config_sha_match_disk": false,
    "or_overlay_enabled": true,
    "cap_pbv2": 4,
    "cap_or": 1,
    "max_concurrent_positions": 5,
    "entry_score_v2_min": 3,
    "momentum_score_cutoff_max": 0.2546,
    "min_continuation_quality": 0.7,
    "reject_below_quality": false,
    "entry_freshness_board_fallback_enabled": false,
    "enable_pullback_misread_dynamic40_guard": false,
    "enable_near_day_high_low_momentum_dynamic40_guard": true,
    "stop_low_mfe_guard_enabled": true,
    "position_cap_mode": true,
    "same_symbol_open_policy": "no_overlap_replace",
    "daytrade_suitability_enabled": true,
    "daytrade_suitability_threshold": 54.695739,
    "pbv2_count_live": 43,
    "or_entry_count_live": 10,
    "accepted_count_live": 53
  },
  {
    "day": "20260625",
    "session": "live_session_122535",
    "config_path": "C:\\Users\\yhach\\Documents\\tradebotfile\\kabu_native\\configs\\small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml",
    "config_sha256_session": "244aa7685dde31547220414d5e7a71d5022ee927687b35a0273f71352816689f",
    "config_sha256_disk_yaml": "2cd21ca2d5721544ef4835e45df577b546183d961d5227f8e8de16d0bde3602f",
    "config_sha_match_disk": false,
    "or_overlay_enabled": true,
    "cap_pbv2": 4,
    "cap_or": 1,
    "max_concurrent_positions": 5,
    "entry_score_v2_min": 3,
    "momentum_score_cutoff_max": 0.2546,
    "min_continuation_quality": 0.7,
    "reject_below_quality": false,
    "entry_freshness_board_fallback_enabled": false,
    "enable_pullback_misread_dynamic40_guard": false,
    "enable_near_day_high_low_momentum_dynamic40_guard": true,
    "stop_low_mfe_guard_enabled": true,
    "position_cap_mode": true,
    "same_symbol_open_policy": "no_overlap_replace",
    "daytrade_suitability_enabled": true,
    "daytrade_suitability_threshold": 54.695739,
    "pbv2_count_live": 27,
    "or_entry_count_live": 0,
    "accepted_count_live": 27
  },
  {
    "day": "20260624",
    "session": "live_session_081514",
    "config_path": "C:\\Users\\yhach\\Documents\\tradebotfile\\kabu_native\\configs\\small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml",
    "config_sha256_session": "8853dabba019968360b34a1ce3c37f782c4bcf62921dd1e9d02ad43a02f2f53e",
    "config_sha256_disk_yaml": "2cd21ca2d5721544ef4835e45df577b546183d961d5227f8e8de16d0bde3602f",
    "config_sha_match_disk": false,
    "or_overlay_enabled": true,
    "cap_pbv2": 4,
    "cap_or": 1,
    "max_concurrent_positions": 5,
    "entry_score_v2_min": 3,
    "momentum_score_cutoff_max": 0.2546,
    "min_continuation_quality": 0.7,
    "reject_below_quality": false,
    "entry_freshness_board_fallback_enabled": false,
    "enable_pullback_misread_dynamic40_guard": false,
    "enable_near_day_high_low_momentum_dynamic40_guard": true,
    "stop_low_mfe_guard_enabled": true,
    "position_cap_mode": true,
    "same_symbol_open_policy": "no_overlap_replace",
    "daytrade_suitability_enabled": true,
    "daytrade_suitability_threshold": 54.695739,
    "pbv2_count_live": null,
    "or_entry_count_live": null,
    "accepted_count_live": 61
  }
]
```
