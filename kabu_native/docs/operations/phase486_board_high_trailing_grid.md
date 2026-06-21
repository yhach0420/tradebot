# Phase486 — Board High Trailing Grid Search

**Verdict:** `overfit_exit`
**Period:** 20260529-20260619
**Grid:** 6 activate x 5 giveback = 30

## Mandatory answers

1. Best grid: **A10_G80**
2. Optimal activate: **1.0%**
3. Optimal giveback: **80%**
4. Delta PnL: **41210.0**
5. Delta PF: **0.1279**
6. maxDD change: **-7900.0**
7. 6976: **{'variant_id': 'A10_G80', 'symbol': '6976', 'day': 'ALL', 'accepted_count': 15, 'total_pnl_yen': 170500.0, 'trailing_exit_count': 13, 'delta_pnl_vs_baseline': 15000.0}**
8. 4062: **{'variant_id': 'A10_G80', 'symbol': '4062', 'day': 'ALL', 'accepted_count': 17, 'total_pnl_yen': -8500.0, 'trailing_exit_count': 8, 'delta_pnl_vs_baseline': 5500.0}**
9. Overfit risk: **high**
10. Runtime candidate: **False**
11. Next actions: ['Verdict: overfit_exit', 'Grid optimum unstable under LOO - do not adopt']

## Top 5 grid cells

- **A10_G80** act=1.0% gb=80% PnL 285800.0 dPnL 41210.0
- **A10_G60** act=1.0% gb=60% PnL 244590.0 dPnL 0.0
- **A8_G80** act=0.8% gb=80% PnL 243400.0 dPnL -1190.0
- **A10_G70** act=1.0% gb=70% PnL 239000.0 dPnL -5590.0
- **A12_G80** act=1.2% gb=80% PnL 236000.0 dPnL -8590.0

**Verdict:** `overfit_exit`
