# Phase460 — Entry Gate Failure Audit

Generated: 2026-06-20T11:40:45+09:00
Period: 20260529..20260619

## 判定

**Verdict:** `combined_gate_problem`

Phase459で判明した上昇銘柄（3441/6492/7256/6466/7600）の取り逃しは **Dynamic40ではなく Entry Gate** が原因。

## Part A — Gate Failure集計

- accepted: **604**
- rejected: **187857**

| gate_failure | count | share |
|---|---:|---:|
| Other | 70910 | 0.3775 |
| Momentum | 56344 | 0.2999 |
| Board | 30472 | 0.1622 |
| High Drift | 23191 | 0.1235 |
| Capacity | 6937 | 0.0369 |
| Weak Shape | 3 | 0.0 |

## Part B — 上昇銘柄監査（6/19 重点）

| symbol | was_candidate | outcome | primary_gate | reject_reason |
|---|---|---|---|---|
| 3441.T | True | rejected | Momentum | near_day_high_low_momentum_dynamic40_guard |
| 6492.T | True | rejected | Momentum | near_day_high_low_momentum_dynamic40_guard |
| 7256.T | True | rejected | Momentum | near_day_high_low_momentum_dynamic40_guard |
| 6466.T | True | rejected | Other | daytrade_suitability |
| 7600.T | True | rejected | Board | near_day_high_low_momentum_dynamic40_guard |

## Part C — Momentum Gate監査

- accepted median momentum: **0.1526**
- rejected median momentum: **0.2086**
- p33 cutoff: **0.2546**
- uptrend winners blocked: **183**

## Part D — Board Gate監査

| bucket | accepted | total_pnl | PF |
|---|---:|---:|---:|
| low | 0 | 0 | None |
| mid | 694 | -204390.23 | 0.8715 |
| high | 30 | -4988.71 | 0.7964 |
| unknown | 0 | 0 | None |

## Part E — Gate除去シミュレーション

| variant | PnL | ΔPnL | PF | maxDD | accepted |
|---|---:|---:|---:|---:|---:|
| A_baseline | 147412.22 | 0.0 | 1.1925 | 133650.0 | 496 |
| B_no_momentum_gate | 133801.31 | -13610.91 | 1.1647 | 137650.0 | 547 |
| C_no_board_gate | 147412.22 | 0.0 | 1.1925 | 133650.0 | 496 |
| D_momentum_relaxed | 123174.04 | -24238.18 | 1.4687 | 77500.0 | 162 |
| E_board_relaxed | 142561.95 | -4850.27 | 1.1816 | 133650.0 | 501 |

## Part F — Mandatory answers

1. 上昇銘柄取り逃し件数: **31**
2. Momentum起因: **1**
3. Board起因: **0**
4. 両方起因: **49**
5. Momentum gateの価値: **13610.91** yen
6. Board gateの価値: **-0.0** yen
7. Momentum除去時PnL: **133801.31**
8. Board除去時PnL: **147412.22**
9. 最も改善余地のあるgate: **Board**
10. Runtime候補: **False**
11. 次アクション: ['Entry Gate (Momentum+Board+score) blocks 6/19 uptrend symbols — not Dynamic40 selection', 'Shadow-test gate relaxation (Board_relaxed or Momentum shadow) before runtime change', 'Do not remove Momentum gate wholesale — simulation shows −13k PnL impact']

## 成果物

- `results/reports/phase460_entry_gate_failure_audit.csv`
- `results/reports/phase460_gate_reject_analysis.csv`
- `results/reports/phase460_uptrend_missed_symbols.csv`
- `results/reports/phase460_gate_simulation.csv`
- `results/reports/phase460_summary.json`
