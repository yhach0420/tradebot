# Phase605 — entry_cluster_guard PBv2 Block Counterfactual

**Verdict:** `phase605_entry_cluster_guard_counterfactual_done`  
**Scope:** Research only — no runtime / production YAML changes.

Run:

```bash
python kabu_native/scripts/run_phase605_entry_cluster_guard_counterfactual.py
```

Outputs: `results/reports/phase605_cluster_guard_counterfactual/`

## entry_cluster_guard 導入・条件

| Item | Value |
|------|-------|
| Phase | **Phase549** (V6 Balanced Reject + E4 Liquidity Burst) |
| Code | `exposure_gate.py` → `exposure_gate.py:551` via `entry_cluster_guard.py` |
| Scope | PBv2 only (`entry_score_v2_min > 0` block). OR overlay unaffected. |
| reject_clusters | `{5}` |
| reject_csubs | `{0, 2, 3, 5}` |
| E4 exception | `liquidity_burst >= 0.052267` |
| Model | `configs/entry_cluster_guard_model.json` |

## 発火率比較 (6/24–6/30 AM)

| Day | reached_cluster | cluster_reject | reject_rate | E4 exception | live cluster_guard_reject |
|-----|-----------------|----------------|-------------|--------------|---------------------------|
| 6/24 | 0 | 0 | — | — | (guard not active / pre-549) |
| 6/25 | 7,445 | 7,445 | 100% | 0 | — |
| 6/29 | 6,644 | 6,644 | 100% | 0 | 4,269 |
| 6/30 | 14,822 | 14,822 | 100% | 0 | 6,339 |

6/24 は cluster check 未到達（guard 未有効）。6/25 以降は cluster 到達行の **100% が reject**（E4 exception 0）。

## Counterfactual 結果サマリ

### Live accept probe（実際に約定した行のみ）

| Day | baseline PBv2 pass | guard OFF | OFF 成績 (matched) |
|-----|-------------------|-----------|-------------------|
| 6/25 | 0/53 | **44/53** | PnL +1225, PF 2.01, WR 68%, DD 818 |
| 6/29 | 0/12 | **0/12** | — |
| 6/30 | 0/6 | **0/6** | — |

### 緩和 variant (6/25 live accepts)

| Variant | PBv2 pass |
|---------|-----------|
| off | 44/53 |
| relax_cluster5_only (csub reject 解除) | 44/53 |
| relax_csub5_only | 0/53 |
| relax_exception_p35 | 0/53 |

**625 の PBv2 ブロック要因は csub reject `{0,2,3,5}`。** cluster 5 単独 reject では 44 件は通る。

## 必須回答

1. **entry_cluster_guard が PBv2=0 の主因か**  
   **Eval レベルでは YES** — 6/29–6/30 で #1 internal blocker（6644 / 14822 eval）。  
   **Live accept 復帰では NO（629/630）** — guard OFF でも 0/12, 0/6。実約定は `momentum_low` 等で PBv2 不通過（Phase604B 一致）。

2. **guard OFF で PBv2 accept は何件戻るか**  
   - 6/25 live probe: **44/53**（live pbv2_count=43 と整合）  
   - 6/29/6/30 live probe: **0**（戻らない）  
   - Eval uncapped incremental: 629=6644, 630=14822（cap/他 gate 未適用の理論値）

3. **戻った PBv2 の成績は良いか**  
   - 6/25 OFF: **PF 2.01, WR 68%, PnL +1225 yen/100** — 良好  
   - 629/630: live accept に該当なし

4. **完全 OFF vs 条件緩和**  
   - **完全 OFF 非推奨**（629/630 では効果なし、549 設計意図も失う）  
   - **緩和候補:** `reject_csubs` 縮小（`relax_cluster5_only` = csub reject 解除で 625 と同等 44/53）  
   - E4 threshold 緩和だけでは 625 live accepts 0 改善

5. **本線修正候補（runtime 未実施）**  
   - `pilot_runner.py`: OR 上書き前に `pbv2_internal_reason` 保存  
   - preflight: `config_sha256` mismatch で session 停止  
   - cluster guard: csub reject 見直し（Phase549 rollback パス利用）  
   - OR overlay 単独 rollback 禁止

## 6/25 PBv2 accepted 保全

- OFF replay: **44/53** live accept keys preserved（Phase604B probe 一致）  
- live pbv2_count=43 — **壊していない**

## Config SHA preflight

全 session で session SHA ≠ 現在 disk YAML（`2cd21ca2…`）。  
Counterfactual は **session 記録の config_path** を使用。本線では preflight stop 必須。

## pbv2_internal_reason

Research CSV: `phase605_pbv2_internal_reason_overwrites.csv`（OR 上書き前 internal reason 保存）。Runtime 未配線。
