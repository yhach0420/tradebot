# Market Capture Test Traceability (Phase687W9)

| Requirement | Test / artifact |
|-------------|-----------------|
| Sidecar separate PID | `test_sidecar_separate_pid_and_seal` |
| Paper not required for capture | synthetic sidecar run |
| Paper preflight fail → capture continues | W8/W9 runner harness `paper_blocked_capture_continues` |
| Double-start prevention | PID file in sidecar |
| 15:35 auto end | `scheduled_end_dt` + operator_stop in tests |
| Registration ≤50 | `test_registration_limit_50` |
| Registration lock | `test_registration_lock` |
| No unregister/all from sidecar | `test_unregister_all_not_used` + import scan |
| Generation change | `test_generation_change_recorded` |
| original_payload + secrets | `test_writer_original_payload_and_secrets` |
| Sequence | writer sequence field |
| Rotation | `test_rotation_by_bytes` |
| Queue overflow / gap | `test_queue_overflow_records_gap` |
| Separate output root | path asserts in sidecar test |
| Capture seal | seal file in sidecar test |
| Dual WS classification | `test_dual_ws_*` |
| Gateway parity | `test_gateway_parity_100k` + research 100k |
| Default KABU_DIRECT | `test_default_push_source_unchanged` |
| submit/cancel=0 | summary fields |
| Design docs | ADR + docs/market_capture/* |
| W4S not polluted | capture under `data/market_capture`, provenance not LIVE_PAPER_RUNTIME |

Research dir: `results/reports/phase687w9_market_capture_sidecar/`
