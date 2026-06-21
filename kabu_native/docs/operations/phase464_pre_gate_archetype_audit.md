# Phase464 — Pre-Gate Archetype Audit

Generated: 2026-06-20T19:58:08+09:00
Period: 20260529..20260619
Dynamic40 total: **188461** | actionable: **85624**

**Verdict:** `trend_pre_gate_edge`

## Part B — Archetype would PnL (close proxy)

| label | count | would_pnl | median | win_rate | PF | accepted |
|---|---:|---:|---:|---:|---:|---:|
| Trend-following | 29460 | 388123900.0 | 4000.0 | 0.6109 | 2.2456 | 86 |
| Near-high continuation | 13 | 443300.0 | 10400.0 | 0.6923 | 7.4715 | 13 |
| Pullback-reversal | 13 | -299700.0 | -10400.0 | 0.3077 | 0.2337 | 13 |
| VWAP-stable | 16580 | -1423900.0 | 100.0 | 0.5064 | 0.9899 | 166 |
| Range/Other | 39558 | -146338100.0 | 0.0 | 0.2208 | 0.502 | 326 |

## Part C — Gate pass rates (pre-gate population)

| label | count | momentum | board | near_high_reject | drift | weak_shape | accepted |
|---|---:|---:|---:|---:|---:|---:|---:|
| Trend-following | 29,460 | 52.7% | 58.9% | 44.8% | 0% | 0% | 0.3% |
| Near-high continuation | 13 | 100% | 100% | 92.3% | 0% | 0% | 100% |
| Pullback-reversal | 13 | 100% | 100% | 0% | 53.8% | 84.6% | 100% |
| VWAP-stable | 16,580 | 54.2% | 54.4% | 37.2% | 0% | 0% | 1.0% |
| Range/Other | 39,558 | 90.0% | 84.4% | 3.5% | 0.1% | 0.0% | 0.8% |

**Key:** Trend-following の 47.3% は Momentum gate 不通過。near_high guard reject 44.8% で Phase461/463 の near-high 問題と一致。

## Notes

- Part B `would_pnl_close_proxy` は inject 候補の close_proxy 合算のため絶対値が大きい（方向性・median/win_rate を参照）
- Part D replay は inject 中心 pool（67k）で baseline accepted=31。Phase463 canon 中心 replay（278 accepted）とは pool 構成が異なる
- 6/19 missed は gate block 時点の特徴量不足で **Range/Other** に分類（near-high ラベル未付与）


| variant | PnL | Δvs A | accepted |
|---|---:|---:|---:|
| A_baseline_current_runtime | 91600.0 | 0.0 | 31 |
| D_near_high_continuation_rescue | 91600.0 | 0.0 | 31 |
| C_pullback_reversal_rescue | 91600.0 | 0.0 | 31 |
| B_trend_following_rescue | -7500.0 | -99100.0 | 28 |
| F_best_two_archetype_rescue | -7500.0 | -99100.0 | 28 |
| E_vwap_stable_rescue | -27800.0 | -119400.0 | 29 |

## Mandatory answers

1. Most profitable: **Trend-following**
2. Most loss: **Range/Other**
3. Trend profit source: **True**
4. Pullback profit source: **False**
5. Near-high profit source: **True**
6. VWAP-stable profit source: **False**
7. Momentum drops: **Trend-following**
8. Runtime picks: **[('Range/Other', 31086), ('Trend-following', 7311), ('VWAP-stable', 4204)]**
9. 6/19 missed: **{'3441.T': 'Range/Other', '6492.T': 'Range/Other', '7256.T': 'Range/Other', '6466.T': '', '7600.T': 'Range/Other'}**
10. Rescue improved: **D_near_high_continuation_rescue**
11. Runtime candidate: **None**
12. Next: ['Shadow D_near_high_continuation_rescue if delta vs A > 5k', 'Review momentum gate vs Trend-following pre-gate edge', '6/19 missed: near-high rescue path for uptrend symbols']
