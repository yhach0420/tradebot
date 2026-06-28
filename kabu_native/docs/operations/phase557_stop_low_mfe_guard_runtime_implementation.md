# Phase557 — stop_low_mfe Guard (G554_022) Runtime Implementation

**Verdict:** `phase557_stop_low_mfe_guard_runtime_ready`

## Part A — Reject Overlap (B_current_runtime, 148 accepted, 20260616–20260625)

Analysis universe: PBv2 guard-stage accepted trades (ClusterGuard already applied in live).

| Segment | Trades | PnL | PF | MFE0 | stop_low_mfe | Winners | Big winners | blocked_loss | blocked_winner | net_contribution |
|---------|--------|-----|-----|------|--------------|---------|-------------|--------------|----------------|------------------|
| both_reject | 0 | 0 | — | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| cluster_only_reject | 0 | 0 | — | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| slm_only_reject | 47 | -27,400 | 0.33 | 24 | 41 | 13 | 1 | 40,800 | 13,400 | **+27,400** |
| both_pass | 101 | -3,400 | 0.96 | 41 | 79 | 38 | 15 | 90,500 | 87,100 | +3,400 |

### Part A answers

1. **Overlap:** 0% — no accepted trade would be rejected by both guards (ClusterGuard already filtered at entry).
2. **SLM-only reject exists:** Yes — 47 trades.
3. **SLM-only PnL:** Bad (-27,400 if taken); blocking improves PnL by +27,400.
4. **SLM-only stop_low_mfe rate:** 87.2% (41/47).
5. **Independent value:** Yes — net +27,400 shadow on trades ClusterGuard already passed.
6. **Recommendation:** **Separate guard** (not merged into ClusterGuard).

Outputs: `results/reports/phase557_reject_overlap_summary.csv`, `phase557_reject_overlap_detail.csv`

## Part B — Runtime Implementation

### Config (production YAML)

```yaml
stop_low_mfe_guard_enabled: true
stop_low_mfe_guard_threshold: 0.009
stop_low_mfe_guard_missing_policy: pass
stop_low_mfe_guard_pbv2_only: true
```

Rollback: `stop_low_mfe_guard_enabled: false`

### Guard order (PBv2 ENTRY)

```text
PBv2 pass → ClusterGuard → stop_low_mfe Guard → CAP
```

OR overlay: **exempt** (no SLM guard).

### Feature

- `volume_acceleration_5m` from `PushMinuteBarBuilder` at live ENTRY
- Causal: completed minute bars only (`bars[:entry_time]`)
- Missing → pass
- AM/PM: separate session instances; intraday refresh calls `reset_session()`

### Summary / Discord fields

- `stop_low_mfe_guard_reject_count`, `missing_count`, `blocked_loss`, `blocked_winner`, `blocked_big_winner`, `net_shadow`, `volume_accel_threshold`
- Discord: `StopLowMFEGuard: reject={n} missing={m} net_shadow={x}`

### Code touchpoints

- `src/small_paper/stop_low_mfe_guard.py` — guard module
- `src/research/exposure_gate.py` — after ClusterGuard
- `src/small_paper/pilot_runner.py` — push ingest, enrich, reject log, summary
- `src/small_paper/config.py` — YAML load + gate wiring

## Verification

| Check | Result |
|-------|--------|
| Unit tests (24) | PASS |
| `run_phase557_stop_low_mfe_guard_ready.py` | PASS |
| Production startup smoke test | PASS |
| Live pipeline preflight | PASS |

Run:

```bash
python scripts/run_phase557_stop_low_mfe_guard_ready.py
python -m pytest tests/test_phase557_stop_low_mfe_guard_runtime.py -v
```

## Paper trade readiness

**Yes** — guard is wired, tested, and rollback-ready. Monitor first session for `stop_low_mfe_guard_missing_count` and `lost_big_winner` shadow (1 big winner in SLM-only bucket historically).
