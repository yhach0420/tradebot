# Phase474 — Frozen Trend Exit Validation

**Verdict:** `trend_exit_is_entry_cancellation`
**Frozen set:** Phase473 T-B accepted — **19** trades (exit-only, no CAP change)
**Period:** 20260529–20260619

## 必須回答

| # | 項目 | 結果 |
|---|------|------|
| 1 | frozen 19件で最良Exit | **D (VWAP Break confirm 3 (3 consecutive ticks below VWAP))** |
| 2 | Runtime Exit PnL | **-8,200** |
| 3 | 最良Exit PnL | **11,800** |
| 4 | 改善額 | **20,000** |
| 5 | PF (best) | **2.7353** |
| 6 | maxDD (best) | **4,500** |
| 7 | 即時Exit件数 (best) | **0** |
| 8 | zero-yen Exit (best) | **2** |
| 9 | 6976改善額 (best vs A) | **69,500** |
| 10 | 利益増加 vs 損失回避 | **loss_avoidance** |
| 11 | VWAP Break Trend Exit成立 | **False** |
| 12 | confirm tick必要 | **confirm_3_optional** |
| 13 | Trend Entry独立価値 | **False** |
| 14 | Runtime候補 | **False** |
| 15 | 次アクション | Verdict: trend_exit_is_entry_cancellation; Best frozen exit: D; VWAP Break on frozen set behaves as entry filter — not a runtime Trend Exit; Keep Pullback v2 primary; do not add Trend entry to runtime; Confirm tick note: confirm_3_optional; Runtime candidate: False |

## Exit比較 (frozen T-B)

| var | PnL | PF | maxDD | win% | avg_hold_sec | Δ vs A | 6976 | 4062 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A | -8200.0 | 0.9728 | 229000.0 | 0.5263 | 20845.79 | 0.0 | -66500.0 | 0.0 |
| B | 0.0 | None | 0.0 | 0.0 | 0.58 | 8200.0 | 0.0 | 0.0 |
| C | 0.0 | None | 0.0 | 0.0 | 0.58 | 8200.0 | 0.0 | 0.0 |
| D | 11800.0 | 2.7353 | 4500.0 | 0.5263 | 6.11 | 20000.0 | 3000.0 | 0.0 |
| E | -114200.0 | 0.1001 | 122700.0 | 0.2632 | 1881.21 | -106000.0 | -81000.0 | 0.0 |
| F | -35000.0 | 0.727 | 88000.0 | 0.2105 | 4562.37 | -26800.0 | -81000.0 | 0.0 |

## 即時Exit監査

| var | same-tick | ≤5 tick | zero PnL | VWAP欠損 | entry<VWAP |
|---|---:|---:|---:|---:|---:|
| A | 0 | 0 | 0 | 0 | 0 |
| B | 19 | 19 | 19 | 0 | 0 |
| C | 19 | 19 | 19 | 0 | 0 |
| D | 0 | 19 | 2 | 0 | 0 |
| E | 0 | 0 | 0 | 0 | 0 |
| F | 0 | 0 | 0 | 0 | 0 |

## Symbol Attribution

| bucket | A runtime | B VWAP | D confirm3 |
|---|---:|---:|---:|
| 6976 | -66500.0 | 0.0 | 3000.0 |
| 4062 | 0.0 | 0.0 | 0.0 |
| other | 58300.0 | 0.0 | 8800.0 |

## 6976 Detail

- Runtime PnL: **-66500.0** (3 trades)
- VWAP Break B PnL: **0.0** (same-tick cancel: **3**)
- Best (D) PnL: **3000.0** (profitable: **1** / 3)
- Δ vs runtime: **69500.0**

## 改善分解 (best vs A)

- Total Δ: **20000.0**
- Loss rescue (A-loser improved): **299300.0**
- Winner gain: **0.0**
- Winner give-up: **-279300.0**
- Dominant: **loss_avoidance**

## Method / VWAP proxy note

- Variant **A** uses observed runtime shadow PnL from CAP replay (matches Phase473 −8,200).
- Variants **B–F** re-simulate exit on fixed entry/time/price only.
- Tick `vwap_dev` proxy = `pnl_pct − entry_vwap_dev_pct`; at entry tick `pnl=0` so proxy is negative whenever T-B `vwap_dev_pct>0`.
- **B/C** therefore fire on tick 0 for all 19 trades (zero-yen exits) — entry cancellation, not a hold-time Trend Exit.
- **D** (confirm 3) avoids same-tick fire but still exits within 5 ticks on **19/19**; improvement is almost entirely loss avoidance on 6976/long-hold losers.

## Phase473 参照

- Phase473 runtime (unfrozen CAP): -8200 / 19 acc
- Phase473 VWAP Break (unfrozen, +CAP artifact): 182700 / 50 acc
- Phase474 frozen runtime reconcile: **-8200.0** / 19 acc

## 判定

**`trend_exit_is_entry_cancellation`** — frozen exit-only audit on T-B accepted set.