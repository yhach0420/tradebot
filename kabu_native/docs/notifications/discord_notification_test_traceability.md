# Discord Notification Test Traceability (Phase687W10)

| Requirement | Test |
|-------------|------|
| ENTRY dedupe | `test_entry_dedupe_once` |
| EXIT dedupe | `test_exit_dedupe_once` |
| Restart dedupe | `test_dedupe_survives_reload` |
| CRITICAL upgrade | `test_critical_severity_upgrade_allows_renotify` |
| Rate limit | `test_rate_limit_operations_15m` |
| Webhook missing skip | `test_webhook_missing_skip_no_fallback` |
| Capture no legacy fallback | `test_capture_no_legacy_fallback_env` |
| Worker queue / mock send | `test_worker_queue_and_mock_send` |
| HTTP failure fail-open | `test_worker_fail_open_on_http_error` |
| Actual/shadow separation | `test_actual_shadow_separation_formatter` |
| Readiness no external | `test_readiness_cli_no_external_send` |
| Discord fail ≠ Paper stop | `test_discord_failure_does_not_break_paper_path` |
