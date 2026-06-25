# Phase515A — Classic Entry Parameter Robustness

**Verdict:** `phase515a_classic_entry_parameter_robustness_done`
**Exit fixed:** PBv2 Exit
**Classical strategies:** 276
**Grid:** {'momentum': 150, 'trend': 85, 'breakout': 41}

## Top 10 by PnL (classical)

| ID | Family | PnL | PF | maxDD | ΔPnL |
|----|--------|-----|----|-------|------|
| BASELINE | PBv2 | 214959.61 | 1.3476 | 118600.0 | — |
| P515A_B_005 | breakout | 455150.0 | 1.9735 | 78000.0 | 240190.39 |
| P515A_B_010 | breakout | 455150.0 | 1.9735 | 78000.0 | 240190.39 |
| P515A_B_014 | breakout | 455150.0 | 1.9735 | 78000.0 | 240190.39 |
| P515A_B_017 | breakout | 455150.0 | 1.9735 | 78000.0 | 240190.39 |
| P515A_B_024 | breakout | 455150.0 | 1.9735 | 78000.0 | 240190.39 |
| P515A_B_027 | breakout | 455150.0 | 1.9735 | 78000.0 | 240190.39 |
| P515A_B_033 | breakout | 455150.0 | 1.9735 | 78000.0 | 240190.39 |
| P515A_M_002 | momentum | 387800.0 | 1.4638 | 83400.0 | 172840.39 |
| P515A_M_003 | momentum | 387800.0 | 1.4638 | 83400.0 | 172840.39 |
| P515A_M_005 | momentum | 387800.0 | 1.4638 | 83400.0 | 172840.39 |

## Mandatory answers

1. Beats PBv2 entry: **True**
2. PnL beats: **['P515A_M_002', 'P515A_M_003', 'P515A_M_005', 'P515A_M_006', 'P515A_M_007', 'P515A_M_008', 'P515A_M_010', 'P515A_M_011', 'P515A_M_013', 'P515A_M_014']**
3. PF beats: **['P515A_M_002', 'P515A_M_003', 'P515A_M_005', 'P515A_M_010', 'P515A_M_011', 'P515A_M_013', 'P515A_M_068', 'P515A_M_150', 'P515A_B_005', 'P515A_B_010']**
4. DD beats: **['P515A_M_002', 'P515A_M_003', 'P515A_M_005', 'P515A_M_033', 'P515A_M_068', 'P515A_M_076', 'P515A_M_148', 'P515A_B_005', 'P515A_B_010', 'P515A_B_014']**
5. Best momentum: **{'strategy_id': 'P515A_M_002', 'description': 'RSI>45 StochK>D+0 roc', 'pnl': 387800.0}**
6. Best trend: **{'strategy_id': 'P515A_T_078', 'description': 'ema20 & vwap & adx15 & di_bull', 'pnl': 258190.0}**
7. Best breakout: **{'strategy_id': 'P515A_B_005', 'description': 'day_high', 'pnl': 455150.0}**
8. RSI stability: **{'bands': {'45': {'median_pf': 1.1425, 'count': 32}, '50': {'median_pf': 1.1107, 'count': 32}, '55': {'median_pf': 1.0379, 'count': 32}, '60': {'median_pf': 1.0079, 'count': 32}, '65': {'median_pf': 0.9047, 'count': 22}}, 'most_stable_rsi': '45'}**
9. Stoch stability: **{'bands': {'0': 1.0988, '2': 1.0473, '5': 1.0956, '10': 0.9066}, 'most_stable_margin': '0'}**
10. ADX stability: **{'bands': {'adx15': 0.7728, 'adx20': 0.7449, 'adx25': 0.7497, 'adx30': 0.7802}, 'most_stable_adx': 'adx30'}**
11. Promising family: **breakout**
12. Neighborhood robust: **[]**
13. Non-fragile: **[]**
14. Next deep dive: **P515A_B_005** — day_high
