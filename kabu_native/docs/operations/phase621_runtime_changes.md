# Phase621 Runtime Changes

## Files modified

| File | Change |
|------|--------|
| `src/small_paper/entry_scan_controller.py` | v2 freshness path; `event_stale_price` / `liquidity_stale_trade`; audit flags |
| `src/small_paper/config.py` | YAML fields + policy summary |
| `src/small_paper/pilot_runner.py` | `recorded_at` inject; v2 counters; session summary |
| `src/small_paper/discord_message_builder.py` | Discord Freshness Semantics v2 block |
| `src/small_paper/live_pipeline_preflight.py` | Pass v2 params through preflight freshness |
| `configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml` | Enable v2 + thresholds |

## New files

| File | Purpose |
|------|---------|
| `src/research/phase621_freshness_semantics_v2.py` | Verification + report |
| `scripts/run_phase621_freshness_semantics_v2.py` | Runner |
| `tests/test_phase621_freshness_semantics_v2.py` | Unit tests |
| `docs/operations/phase621_freshness_semantics_v2.md` | Operations doc |

## Unchanged

- PBv2 gate (`entry_v2` / `_evaluate_gate_entry`)
- OR overlay
- Structural EXIT
- ExposureGate post-freshness logic

## Live event lag

Live uses `t0_push_received_at` injected as `payload.recorded_at` when absent, so `event_stale` reflects queue delay at eval time.

## Rollback procedure

1. Set `freshness_semantics_v2_enabled: false` in production YAML
2. Update `production_config_sha256.pin` if used
3. Restart paper session — no code deploy required
