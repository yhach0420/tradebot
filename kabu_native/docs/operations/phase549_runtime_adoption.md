# Phase549 — V6 Balanced Reject + E4 Liquidity Burst Runtime Adoption

Production adoption of Phase546–548 validated **V6 Balanced Reject + E4 (Liquidity Burst)** on **PBv2 ENTRY only**. OR overlay is unchanged.

## Adopted configuration

| Item | Value |
|------|-------|
| Scope | PBv2 ENTRY only (`entry_score_v2_min` gate block) |
| Reject clusters | Phase545 cluster **5** |
| Reject csubs | Phase545C sub **0, 2, 3, 5** |
| Exception | `liquidity_burst >= 0.052267` (Phase548 p75) |
| Model | `configs/entry_cluster_guard_model.json` (frozen centroids) |
| Rollback (full) | `entry_cluster_guard_enabled: false` |
| Rollback (exception only) | `entry_cluster_guard_exception_enabled: false` |

```yaml
entry_cluster_guard_enabled: true
entry_cluster_guard_exception_enabled: true
entry_cluster_guard_liquidity_burst_threshold: 0.052267
entry_cluster_guard_reject_clusters: [5]
entry_cluster_guard_reject_csubs: [0, 2, 3, 5]
```

## ENTRY flow (PBv2)

```text
PBv2 Candidate → Cluster classify → Reject target?
  No  → ENTRY
  Yes → liquidity_burst >= threshold?
    Yes → ENTRY (EXCEPTION)
    No  → Reject (debug log only)
```

## Runtime changes

- `entry_cluster_classifier.py` — nearest-centroid classify (cluster / csub)
- `entry_cluster_guard.py` — V6 reject + E4 exception, summary metrics
- `exposure_gate.py` — guard inside PBv2 v2 block only
- `config.py` / production YAML — config keys + rollback
- `pilot_runner.py` — enrich, reject debug log, summary, exit attribution
- `discord_message_builder.py` — ENTRY `ClusterGuard: PASSED|EXCEPTION`; daily summary line

## Daily summary fields

- `cluster_guard_reject_count`
- `cluster_guard_exception_count`
- `cluster_guard_rejected_pnl`
- `cluster_guard_exception_pnl`
- `cluster_guard_exception_win_rate`
- `cluster_guard_exception_pf`
- `cluster_guard_exception_big_winner`
- `cluster_guard_exception_mfe0`
- `cluster_guard_blocked_cluster_counts`

## Runtime monitoring (daily vs baseline)

Compare: PnL, PF, WinRate, MFE0, StopLowMFE, NoProgress, BigWinner, ClusterReject count, Exception count.

## Verdict

```text
phase549_runtime_v6_e4_adopted
```

Run:

```bash
python kabu_native/scripts/run_phase549_runtime_ready.py
```

## Mandatory answers

1. **Runtimeへ導入されたか** — Yes. Guard wired in `ExposureGate` PBv2 block, production YAML enabled.
2. **ORへ影響しないか** — Yes. OR uses `evaluate_or_overlay_entry`; cluster guard not invoked on OR path.
3. **PBv2のみ対象か** — Yes. Guard runs only inside `entry_score_v2_min > 0` block.
4. **Reject数** — `cluster_guard_reject_count` in daily JSON / Discord summary.
5. **Exception数** — `cluster_guard_exception_count` in daily JSON / Discord summary.
6. **Rollback可能か** — Yes. `entry_cluster_guard_enabled: false` stops all; `entry_cluster_guard_exception_enabled: false` stops E4 only.
7. **Config変更可能か** — Yes. Threshold, reject lists, exception flag are YAML-configurable.
8. **Summary追加完了か** — Yes. Daily / JSON / Discord summary include all cluster guard fields.
9. **Discord追加完了か** — Yes. ENTRY notify shows `ClusterGuard: PASSED|EXCEPTION`; rejects debug-only.
10. **Runtime Readyか** — Run `run_phase549_runtime_ready.py`; verdict `phase549_runtime_v6_e4_adopted`.

## Research basis

| Metric | Baseline | V6+E4 |
|--------|----------|-------|
| PnL | −227,520 | +167,680 |
| PF | 0.865 | 1.194 |
| MFE0 | 452 | reduced (12 reintro via exception) |
| Big winner rescue | — | 5 via E4 |
