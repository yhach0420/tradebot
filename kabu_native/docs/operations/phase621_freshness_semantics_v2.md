# Phase621: Freshness Semantics v2 (Temporary Production)

**Verdict:** `phase621_freshness_semantics_v2_done`  
**Status:** Temporary production until Phase620 backtest completes

## Purpose

Apply Phase619 split semantics to live/paper runtime without waiting for Phase620 full-period PnL comparison.

## Scope (changed only)

| Layer | Changed |
|-------|---------|
| Freshness semantics | **Yes** |
| ENTRY / PBv2 / EXIT / OR | **No** |

## Semantics

| Class | Definition | Action |
|-------|------------|--------|
| **event_stale** | `eval_ts − push.recorded_at > event_stale_threshold_sec` | reject `event_stale_price` |
| **board_stale** | min(BidTime, AskTime) age > board_stale_threshold_sec | reject `data_stale_board` |
| **trade_stale** | CurrentPriceTime age > trade_stale_threshold_sec (or missing) | tag `liquidity_stale_trade` only |

Replaces legacy `data_stale_price` reject on CurrentPriceTime-only staleness.

## YAML (production pilot)

```yaml
freshness_semantics_v2_enabled: true
event_stale_threshold_sec: 3.0
board_stale_threshold_sec: 3.0
trade_stale_threshold_sec: 10.0
trade_stale_mode: tag_only
```

## Rollback

```yaml
freshness_semantics_v2_enabled: false
```

Restores legacy `evaluate_entry_data_freshness` (CurrentPriceTime > `entry_max_price_age_sec` reject + optional board fallback).

## Observability

Session summary + Discord Daily Summary embed block **Freshness Semantics v2**:

- `event_stale_reject_count`
- `board_stale_reject_count`
- `trade_stale_tag_count`

Audit rows (`entry_symbol_eval`) include `event_stale`, `board_stale`, `trade_stale` booleans.

## Verification

```powershell
cd kabu_native
python -m unittest tests.test_phase621_freshness_semantics_v2 tests.test_entry_scan_controller
python scripts/check_live_pipeline_preflight.py
python scripts/run_phase621_freshness_semantics_v2.py
```

## Artifacts

- `results/reports/phase621_report.json`
- `docs/operations/phase621_runtime_changes.md`

## Post-Phase620

Tune thresholds (e.g. event 5s, trade 15s, or `trade_stale_mode: off`) via YAML only — no code change required.
