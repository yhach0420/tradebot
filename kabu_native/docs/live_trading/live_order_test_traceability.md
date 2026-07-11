# Live Order Test Traceability

Maps requirements → code → tests → evidence.  
Results refreshed by `python -m research.phase687w3_e2e_readonly_reconciliation` and W2 audit.

| Requirement ID | Requirement | Source file | Test | Result | Evidence |
|----------------|-------------|-------------|------|--------|----------|
| REQ-ORDER-001 | ENTRY lifecycle SIGNAL→FILLED dry-run | `live_order_safety_sm.py` `handle_entry_signal` | `test_full_fill_and_exit`; Scenario A | PASS | `phase687w2_dryrun_scenarios.json` |
| REQ-ORDER-002 | EXIT caps to holdings | `handle_exit_signal` | fault EXIT qty capped; `test_full_fill_and_exit` | PASS | `phase687w2_fault_injection_results.csv` |
| REQ-ORDER-003 | Illegal transitions rejected | `transition` / `ENTRY_ALLOWED` | illegal FILLED→SUBMITTED fault; `test_illegal_transition_rejected` | PASS | fault CSV |
| REQ-IDEM-001 | Duplicate ENTRY same key → one order | `make_idempotency_key` + by_idempotency | `test_duplicate_entry_no_second_order`; duplicate ENTRY fault | PASS | `phase687w2_idempotency_test.json` |
| REQ-IDEM-002 | Duplicate EXIT same key → one order | `handle_exit_signal` idempotency-first | duplicate EXIT fault | PASS | fault CSV |
| REQ-IDEM-003 | UNKNOWN no blind resubmit | timeout_after + `reconcile_unknown` | Scenario C | PASS | scenarios JSON |
| REQ-CAP-001 | Reserve then release on reject | `CapitalLedger` | timeout before / broker reject faults | PASS | fault CSV |
| REQ-CAP-002 | No reservation leak after full fill | `release_remainder` | capital reservation test | PASS | `phase687w2_capital_reservation_test.json` |
| REQ-CAP-003 | Partial cancel releases remainder | `cancel` + `release_remainder` | partial then cancel | PASS | partial fill test JSON |
| REQ-CAP-004 | Zero BP ≠ API unavailable | `precheck` reasons | buying power zero fault | PASS | fault CSV |
| REQ-RECON-001 | Broker-only position → recovery | `startup_reconciliation` | Scenario D; broker only fault | PASS | `phase687w2_reconciliation_test.json` |
| REQ-RECON-002 | Local-only position → recovery | `startup_reconciliation` | local only fault | PASS | fault CSV |
| REQ-RECON-003 | Journal restore without resubmit | `restore_from_journal` | `test_journal_restore_no_resubmit` | PASS | W3 report |
| REQ-KILL-001 | Kill switch blocks ENTRY | `activate_kill_switch` | kill switch fault; Scenario E | PASS | `phase687w2_kill_switch_test.json` |
| REQ-PARTIAL-001 | 30/100 partial then additional fill | `additional_fill` | partial + 70/100 faults | PASS | `phase687w2_partial_fill_test.json` |
| REQ-PARTIAL-002 | Partial then stop EXIT | Scenario B | Scenario B | PASS | scenarios JSON |
| REQ-JOURNAL-001 | Append-only intents/events/recon | `AppendOnlyStore` | W2 audit writes; journal restore test | PASS | session JSONL |
| REQ-JOURNAL-002 | JSONL write failure cancels + releases | `jsonl_write_fail` path | JSONL write failure fault | PASS | fault CSV |
| REQ-READONLY-001 | Kabu submit hard-fail | `KabuBrokerAdapter` | `test_kill_switch_and_kabu_hard_fail`; W3 readonly test | PASS | W3 report |
| REQ-READONLY-002 | actual broker submit count = 0 | `actual_broker_submit_count` | W2/W3 audits | PASS | report JSON |
| REQ-DISCORD-001 | Discord failure isolated | `_notify` | Discord failure fault | PASS | fault CSV |
| REQ-STALE-001 | Stale price/board reject | `precheck` | stale price/board faults | PASS | fault CSV |
| REQ-DESIGN-001 | Design schema matches code enums/methods | `docs/live_trading/schema/live_order_design_schema.json` | `test_phase687w3_design_consistency` | PASS | `phase687w3_design_consistency.json` |
| REQ-DESIGN-002 | Docs mark NOT_IMPLEMENTED honestly | design docs | documentation review | PASS | `phase687w3_documentation_review.json` |
| REQ-TOKEN-001 | Fine-grained token failure classification | `kabu_readonly_readiness.py` | `test_classify_token_exceptions` | PASS | `phase687w4t_fault_injection.csv` |
| REQ-TOKEN-002 | Credential masking | `mask_secret_text` | masking tests | PASS | `phase687w4t_credential_masking_test.json` |
| REQ-TOKEN-003 | AUTH_FAILED no auto-retry | `acquire_token_with_policy` | auth_no_auto_retry | PASS | `phase687w4t_retry_policy_test.json` |
| REQ-TOKEN-004 | Readiness CLI exit codes | `check_kabu_readonly_readiness` | readiness_exit_code tests | PASS | `phase687w4t_readiness_probe.json` |
| REQ-TOKEN-005 | Submit hard-fail independent of token | `KabuBrokerAdapter` | hard fail tests | PASS | W4T report |
| REQ-W5-001 | Intent/Policy/Request separation | `kabu_order_execution_policy.py` | W5 unit + runner | PASS | `phase687w5_execution_policy_schema.json` |
| REQ-W5-002 | Request builder from wiring SoT | `kabu_order_request_builder.py` | `test_valid_entry_request` | PASS | `phase687w5_valid_request_examples.json` |
| REQ-W5-003 | Response parser mock | `kabu_order_response_parser.py` | parser matrix | PASS | `phase687w5_response_parser_results.csv` |
| REQ-W5-004 | Fingerprint stability | `compute_fingerprint` | `test_fingerprint_stable` | PASS | `phase687w5_fingerprint_test.json` |
| REQ-W5-005 | Request mutation → RECOVERY_REQUIRED | `REQUEST_MUTATION_DETECTED` | `test_request_mutation_detected` | PASS | `phase687w5_request_mutation_test.json` |
| REQ-W5-006 | Network isolation | builder has no HTTP | `test_network_isolation_no_http_in_builder_source` | PASS | `phase687w5_network_isolation_test.json` |
| REQ-W5-007 | Credential masking | `mask_payload_for_audit` | masking tests | PASS | `phase687w5_credential_masking_test.json` |
| REQ-W5-008 | Station operational_api_available | `kabu_readonly_readiness.py` | design consistency | PASS | readiness fields |
| REQ-W5-009 | Timeout no auto-resubmit | `OrderResponseParser` | Scenario E | PASS | `phase687w5_e2e_scenarios.json` |
| REQ-W5-010 | Design consistency W5 | schema + ADR-687W5 | consistency checker | PASS | `phase687w5_design_consistency.json` |
| REQ-W5A-001 | Official snapshot SoT | `vendor/kabusapi_sendorder_contract.json` | load test | PASS | `phase687w5a_official_contract_snapshot.json` |
| REQ-W5A-002 | Normal NEW Exchange=1 forbidden | ExchangePolicy | W5A tests | PASS | invalid cases CSV |
| REQ-W5A-003 | SOR/TSE+ margin entry fixtures | OrderRequestBuilder | W5A tests | PASS | valid_margin_entry.json |
| REQ-W5A-004 | Repay exchange match position | REPAY_MATCH_* | W5A tests | PASS | valid_margin_exit.json |
| REQ-W5A-005 | Cash orders NOT_IMPLEMENTED | TransactionType | W5A tests | PASS | invalid cases |
| REQ-W5A-006 | FundType omit audited | FundTypeMode | fund_type_audit | PASS | phase687w5a_fund_type_audit.json |
| REQ-W5A-007 | ClosePositions XOR | validate_close_position_xor | W5A tests | PASS | close_position_audit.json |
| REQ-W5A-008 | Official/internal consistency | check_kabu_sendorder_contract_consistency.py | checker | PASS | contract_consistency.json |
| REQ-W5B-001 | Config-only MTT not verified | `kabu_account_capability.py` | W5B tests | PASS | account_capability.json |
| REQ-W5B-002 | Live position MTT observation | parse_position_lots | W5B tests | PASS | margin_trade_type_matrix.csv |
| REQ-W5B-003 | Exact HoldID close | `decide_close_policy` | W5B tests | PASS | close_policy_matrix.csv |
| REQ-W5B-004 | No silent Order=0 fallback | close policy | W5B tests | PASS | fault_injection.csv |
| REQ-W5B-005 | HoldID masking | `mask_hold_id` | masking test | PASS | credential_masking.json |
| REQ-W5B-006 | SOR/TSE+ exchange shadow | `shadow_entry_exchange_candidates` | W5B tests | PASS | exchange_shadow.csv |
| REQ-W5B-007 | Future path not policy input | entry style shadow | W5B tests | PASS | entry_policy_shadow.csv |
| REQ-W5B-008 | W4S soak W5B fields | `write_soak_session_snapshot` | design consistency | PASS | soak fields |
| REQ-W5B-009 | Production policy selection forbidden | schema | consistency | PASS | ADR-687W5B |
| REQ-W5B1-001 | Fixture not live-verified | `normalize_provenance` | W5B1 tests | PASS | corrected_capability.json |
| REQ-W5B1-002 | Live evidence required for VERIFIED | `can_verify_from_live_position` | W5B1 tests | PASS | verification_tests.json |
| REQ-W5B1-003 | Zero positions MTT not verified | LIVE_API_NO_POSITIONS | W5B1 tests | PASS | verification_tests.json |
| REQ-W5B1-004 | Stale/malformed not verified | evidence gates | W5B1 tests | PASS | verification_tests.json |
| REQ-W5B1-005 | Fixture/live mix CONFLICT | build profile | W5B1 tests | PASS | verification_tests.json |
| REQ-W5B1-006 | Soak provenance fields | `soak_provenance_fields` | W5B1 tests | PASS | soak_snapshot_test.json |
| REQ-W6-001 | 0/3 soak blocks | `evaluate_production_enablement` | W6 tests | PASS | fail_closed_tests.json |
| REQ-W6-002 | Fixture provenance blocks | gate | W6 tests | PASS | fail_closed_tests.json |
| REQ-W6-003 | MTT unverified blocks | gate | W6 tests | PASS | fail_closed_tests.json |
| REQ-W6-004 | Policy unselected blocks | gate | W6 tests | PASS | fail_closed_tests.json |
| REQ-W6-005 | Approval missing/expired blocks | gate | W6 tests | PASS | fail_closed_tests.json |
| REQ-W6-006 | SHA mismatch blocks | gate | W6 tests | PASS | fail_closed_tests.json |
| REQ-W6-007 | Tech PASS still NOT_AUTHORIZED | gate + CLI | W6 tests | PASS | readiness_probe.json |
| REQ-W6-008 | CLI does not mutate flags | readiness CLI | W6 tests | PASS | network_isolation.json |
| REQ-W6-009 | Submit/cancel HARD_FAIL / count=0 | KabuBrokerAdapter | W6 tests | PASS | network_isolation.json |
| REQ-W6-010 | Write adapter absent | schema + gate | consistency | PASS | ADR-687W6 |
| REQ-W7-001 | Session manifest create/update | `create_session_manifest` | W7 tests | PASS | session_manifest_example.json |
| REQ-W7-002 | Session seal hash verify | `write_session_seal` | W7 tests | PASS | session_seal_test.json |
| REQ-W7-003 | Journal integrity blocks ENTRY | `check_journal_integrity` | W7 tests | PASS | journal_integrity_tests.json |
| REQ-W7-004 | Kill switch drills A–E | `run_kill_switch_drills` | W7 tests | PASS | kill_switch_drill.json |
| REQ-W7-005 | Restart no resubmit | `run_restart_drills` | W7 tests | PASS | restart_drill.json |
| REQ-W7-006 | File failure blocks would-submit | `run_file_failure_tests` | W7 tests | PASS | file_failure_tests.csv |
| REQ-W7-007 | Disk guard no auto-delete | `disk_guard_report` | W7 tests | PASS | disk_guard.json |
| REQ-W7-008 | Clock diagnose only | `diagnose_clock` | W7 tests | PASS | clock_integrity.json |
| REQ-W7-009 | Operator ack SAMPLE_ONLY | ack schema | W7 tests | PASS | operator_ack_schema.json |
| REQ-W7-010 | Recovery CLI exit 0 not authorize | readiness CLI | W7 tests | PASS | smoke_result.json |
| REQ-W7A-001 | intent_created restored_orders=1 | stateful matrix | W7A tests | PASS | stateful_restart_results.csv |
| REQ-W7A-002 | partial fill qty restore | restore_from_journal | W7A tests | PASS | position_recovery.json |
| REQ-W7A-003 | reservation remaining 70 | capital journal | W7A tests | PASS | reservation_recovery.json |
| REQ-W7A-004 | kill switch restore no NORMAL | kill journal | W7A tests | PASS | kill_switch_restore.json |
| REQ-W7A-005 | automatic resubmit=0 | restore | W7A tests | PASS | stateful_restart_results.csv |
| REQ-W7A-006 | runtime manifest/finalize hooks | pilot_runner | W7A tests | PASS | runtime_manifest_test.json |
| REQ-W7A-007 | full seal + post-mutation | write_full_session_seal | W7A tests | PASS | seal_mutation_tests.csv |
| REQ-W7A-008 | W4S W7A soak fields | write_soak_session_snapshot | W7A tests | PASS | w4s_snapshot_test.json |
| REQ-W7A1-001 | Strict expected/actual AND | recovery_assertion_oracle | W7A1 tests | PASS | corrected_restart_results.csv |
| REQ-W7A1-002 | Negative oracle detects FAIL | run_negative_oracle_tests | W7A1 tests | PASS | negative_oracle_tests.json |
| REQ-W7A1-003 | capital_reserved semantics | CAPITAL_RESERVED_SEMANTICS | W7A1 tests | PASS | reservation_semantics.json |
| REQ-W7A1-004 | Kill switch Policy A hold | KILL_SWITCH_RESERVATION_POLICY | W7A1 tests | PASS | kill_switch_reservation_policy.json |
| REQ-W7A1-005 | W4S assertion gate | w4s_ready_extra_ok | W7A1 tests | PASS | w4s_snapshot_test.json |
| REQ-W7A2-001 | Seal→snapshot SoT copy | w4s_seal_propagation | W7A2 tests | PASS | corrected_w4s_snapshot.json |
| REQ-W7A2-002 | Cross-artifact match | compare_seal_snapshot | W7A2 tests | PASS | seal_snapshot_comparison.json |
| REQ-W7A2-003 | Negative mismatch FAIL | run_negative_seal_mismatch_tests | W7A2 tests | PASS | negative_mismatch_tests.json |
| REQ-W7A2-004 | Finalize order / no circular invalidation | finalize_session_seal_propagation | W7A2 tests | PASS | finalize_order_test.json |

## Invariant → test mapping

| Invariant | Tests |
|-----------|-------|
| INV-001 | REQ-IDEM-001/002 |
| INV-002 | REQ-READONLY-001/002 |
| INV-003 | `test_flags_remain_disabled`; precheck order_enabled |
| INV-004 | REQ-RECON-001 |
| INV-005 | REQ-IDEM-003 / Scenario C |
| INV-006 | REQ-RECON-001 |
| INV-007 | REQ-CAP-001/002/003 |
| INV-008 | REQ-ORDER-002 |
| INV-009 | documentation review (Runtime NOT_CONNECTED) |
| INV-010 | REQ-DISCORD-001 |
| INV-011 | Phase687 logger tests |
| INV-012 | REQ-STALE-001 + W1 latency semantics |
| INV-013 | REQ-W5-005 |
| INV-014 | REQ-W5-006 |

## Phase687W2 fault injection (24)

All cases in `phase687w2_fault_injection_results.csv` must be PASS for W2 READY and are prerequisites for W3 READY.
