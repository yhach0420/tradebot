# Phase488 — Current Runtime Full Replay & Equity Simulation

**Verdict:** `runtime_concentration_risk`
**Period:** 20260529-20260619 (requested 20250529-20260619)

## Mandatory answers

1. Total PnL: **244962.83**
2. PF: **1.6171**
3. maxDD: **53899.13**
4. Win rate: **0.5734**
5. Trade count: **286**
6. Equity 1M: **{'initial_equity_yen': 1000000, 'final_equity_yen': 1144863.65, 'total_pnl_yen': 144863.65, 'return_pct': 14.4864, 'max_drawdown_yen': 56298.41, 'max_drawdown_pct': 5.6298, 'cagr_pct': 951.7097, 'accepted_count': 270, 'profit_factor': 1.3668}**
7. Equity 1.5M: **{'initial_equity_yen': 1500000, 'final_equity_yen': 1744962.83, 'total_pnl_yen': 244962.83, 'return_pct': 16.3309, 'max_drawdown_yen': 53899.13, 'max_drawdown_pct': 3.5933, 'cagr_pct': 1288.7446, 'accepted_count': 286, 'profit_factor': 1.6171}**
8. Equity 3M: **{'initial_equity_yen': 3000000, 'final_equity_yen': 3244962.83, 'total_pnl_yen': 244962.83, 'return_pct': 8.1654, 'max_drawdown_yen': 53899.13, 'max_drawdown_pct': 1.7966, 'cagr_pct': 291.648, 'accepted_count': 286, 'profit_factor': 1.6171}**
9. Equity 5M: **{'initial_equity_yen': 5000000, 'final_equity_yen': 5244962.83, 'total_pnl_yen': 244962.83, 'return_pct': 4.8993, 'max_drawdown_yen': 53899.13, 'max_drawdown_pct': 1.078, 'cagr_pct': 129.7691, 'accepted_count': 286, 'profit_factor': 1.6171}**
10. 6976 share: **0.6225**
11. 4062 share: **0.0367**
12. top_symbol_share: **0.1872**
13. top_day_share: **0.1959**
14. Worst day: **{'day': '20260615', 'trade_count': 33, 'total_pnl_yen': -18899.99, 'profit_factor': 0.5457, 'win_rate': 0.5455}**
15. Best day: **{'day': '20260611', 'trade_count': 39, 'total_pnl_yen': 97100.26, 'profit_factor': 2.8252, 'win_rate': 0.5897}**
16. Phase472 impact: **{'pre472_pnl': 202663.04, 'pre472_pf': 1.4426, 'pre472_maxdd': 50098.95, 'pre472_trade_count': 311, 'current_pnl': 244962.83, 'current_pf': 1.6171, 'current_maxdd': 53899.13, 'current_trade_count': 286, 'delta_pnl_yen': 42299.79, 'delta_pf': 0.1745, 'delta_maxdd_yen': 3800.18, 'delta_trade_count': -25}**
17. Runtime valid: **False**
18. Weakness: **6976 concentration**
19. Next actions: ['Verdict: runtime_concentration_risk', 'Monitor symbol/day concentration; consider shadow guards from Phase483–487 only', 'Phase472 delta PnL: 42299.79', 'Total replay PnL: 244962.83']

**Verdict:** `runtime_concentration_risk`
