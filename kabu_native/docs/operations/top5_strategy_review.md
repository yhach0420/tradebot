# Phase507 Top-5 Strategy Review

**Verdict:** `classic_strategy_battle_done`
**Period:** 20260529 – 20260622
**Universe symbols:** 172

## BASELINE_RUNTIME

- PnL: 214959.61
- PF: 1.3476
- maxDD: 118600.0

## Top 5 by PnL

1. **C_T15_E1** — PnL=503010.0 PF=1.8777 DD=176610.0 trades=184 (entry=T15 exit=E1)
2. **C_T13_E2** — PnL=399410.0 PF=1.5289 DD=226900.0 trades=374 (entry=T13 exit=E2)
3. **C_T15_E2** — PnL=312730.0 PF=1.3507 DD=242900.0 trades=877 (entry=T15 exit=E2)
4. **BASELINE_RUNTIME** — PnL=214959.61 PF=1.3476 DD=118600.0 trades=440 (entry=PBv2 exit=RUNTIME)
5. **C_T10_E2** — PnL=140630.0 PF=1.1879 DD=173400.0 trades=380 (entry=T10 exit=E2)

## Mandatory answers (summary)

- Beats baseline (any metric): True
- PnL beaters (sample): ['C_T13_E2', 'C_T15_E1', 'C_T15_E2']
- PF beaters (sample): ['C_T13_E2', 'C_T15_E1', 'C_T15_E2']
- Boardless best: {'strategy_id': 'C_T15_E1', 'entry_rule_id': 'T15', 'exit_rule_id': 'E1', 'total_pnl_yen_100': 503010.0, 'profit_factor': 1.8777, 'max_drawdown_yen_100': 176610.0, 'trades': 184, 'win_rate': 0.2989, 'avg_pnl_yen_100': 2733.75, 'positive_day_count': 7, 'negative_day_count': 6, 'worst_day_pnl': -88100.0, 'best_day_pnl': 341800.0, 'daily_stability_score': 0.5385, 'baseline_diff_pnl': 288050.39, 'baseline_diff_pf': 0.5301, 'baseline_diff_dd': -58010.0, 'rank_pf': 1, 'rank_pnl': 1, 'rank_dd': 5, 'rank_stability': 12, 'rank_baseline_diff': 1}

Research only — no runtime adoption.
