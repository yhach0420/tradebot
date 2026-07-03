# Phase603 Entry Freshness Board Fallback Fix

**Verdict:** `phase603_entry_freshness_board_fallback_fix_done`  
**Adoption:** **不採用 (rejected)** — full-period backtest aborted; board fallback disabled in production YAML and config default.

## Rejection rationale

| Check | Phase602 (OFF) | Phase603 (ON) | 6/29 smoke |
|-------|----------------|---------------|------------|
| Accepts | — | — | 19 → 50 |
| data_stale_price | — | — | 67,477 → 5,296 |
| PnL (yen_100) | — | — | +31,848 → +2,499 |
| PF | — | — | 34.53 → 1.38 |

Fallback trades showed poor quality (4265.T overlap/stop_hit). PnL delta −29,349 on 6/29 alone → **採用不可**.

**Operational state:** `entry_freshness_board_fallback_enabled: false` in production YAML; code path remains for research/shadow only.

---

`evaluate_entry_data_freshness()` in `entry_scan_controller.py` extends price freshness beyond `CurrentPriceTime` alone:

| Path | Condition | Result |
|------|-----------|--------|
| Primary | `CurrentPriceTime` present and age ≤ 3s | PASS — `price_freshness_source=current_price_time` |
| Fallback | Price ts missing/stale **and** board age ≤ 3s **and** CalcPrice **and** Bid/Ask **and** spread ≤ 50bps | PASS — `price_freshness_source=board_fallback` |
| Reject | Fallback conditions fail | `data_stale_price` — `price_freshness_source=stale_reject` |

Board-only stale still rejects via `data_stale_board` when price ts is fresh (unchanged).

**Wired in:** `pilot_runner.py`, `live_pipeline_preflight.py`, audit logging (`entry_scan_audit.jsonl`).

**Config (production YAML):**

```yaml
entry_freshness_board_fallback_enabled: false
entry_freshness_board_fallback_max_spread_bps: 50.0
```

Rollback: already disabled (Phase603 rejected).

---

## Mandatory validation

| # | Check | Result |
|---|-------|--------|
| 1 | CurrentPriceTime fresh PASS preserved | **PASS** — 3,062 non-stale evals remain `current_price_time`; unit test `test_current_price_time_fresh_unchanged` |
| 2 | 6/29 data_stale_price rescues | **7,464** rescued (8 focus symbols AM+PM); 1,442 still stale |
| 3 | Focus symbols reach PBv2/OR gate | **YES** — per symbol below |
| 4 | Accepted increase | Live 6/29 had 0 accepts (all blocked pre-gate). **7,464 evals** now pass freshness → downstream gate reachable; full accept delta needs live/shadow replay |
| 5 | spread > 50bps not rescued | **PASS** — 1,419 blocked by `spread_above_max`; unit test `test_board_fallback_rejects_wide_spread` |
| 6 | Paper runtime stable | **PASS** — 9/9 `test_entry_scan_controller` |
| 7 | Phase594 / LiveOrderAdapter | **No impact** — post-accept only; freshness is pre-gate |
| 8 | No real orders | **PASS** — paper shadow config unchanged |

### Per-symbol rescue (6/29, focus set)

| Symbol | AM rescued / stale | PM rescued / stale | Notes |
|--------|-------------------:|-------------------:|-------|
| 4265.T | 542 / 553 | 613 / 631 | High rescue; PM last trade frozen but board fresh |
| 5592.T | 471 / 562 | 570 / 575 | High rescue |
| 9417.T | 17 / 207 | 9 / 103 | Low rescue — wide spread blocks most |
| 3192.T | 35 / 246 | 3 / 96 | Low rescue — spread blocked |
| 7352.T | 608 / 701 | 805 / 865 | High rescue |
| 6327.T | 870 / 870 | 945 / 947 | Full rescue |
| 4664.T | 346 / 611 | 463 / 771 | Partial — spread blocks ~40% |
| 6522.T | 614 / 614 | 553 / 554 | Full rescue |

### Audit log fields added

- `price_freshness_source`: `current_price_time` | `board_fallback` | `stale_reject`
- `current_price_age_sec`, `board_age_sec`, `spread_bps`
- `fallback_used`, `fallback_reject_reason`

---

## Outputs

- `results/reports/phase603_entry_freshness_board_fallback.csv`
- `results/reports/phase603_fallback_rescue_counts.csv`
- `results/reports/phase603_symbol_rescue_breakdown.csv`
- `results/reports/phase603_accept_delta.csv`
- `results/reports/phase603_regression_checks.csv`
- `results/reports/phase603_report.json`

**Run:** `python scripts/run_phase603_entry_freshness_board_fallback_fix.py`

**Next:** Stay with Phase602 `data_stale_price` guard (CurrentPriceTime only). No board fallback in live/paper accept path.
