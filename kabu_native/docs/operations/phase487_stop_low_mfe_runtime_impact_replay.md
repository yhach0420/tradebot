# Phase487 — stop_low_mfe Runtime Impact Replay

**Verdict:** `overfit_guard`
**Period:** 20260529-20260619

## Mandatory answers

1. Best candidate: **P2_A1_r30_minus_r5_B2_vwap_extension_rate**
2. PnL improvement: **76323.43**
3. PF improvement: **0.4634**
4. maxDD change: **-85950.98**
5. stop_low_mfe reduction: **{'count_delta': -14, 'pnl_delta': 54739.0, 'baseline_count': 56, 'candidate_count': 42}**
6. 6976 impact: **{'guard_id': 'P2_A1_r30_minus_r5_B2_vwap_extension_rate', 'symbol': '6976', 'day': 'ALL', 'accepted_count': 14, 'total_pnl_yen': 137501.28, 'stop_low_mfe_count': 1, 'stop_low_mfe_pnl_yen': -21000.0, 'delta_pnl_vs_baseline': 12502.75}**
7. 4062 impact: **{'guard_id': 'P2_A1_r30_minus_r5_B2_vwap_extension_rate', 'symbol': '4062', 'day': 'ALL', 'accepted_count': 14, 'total_pnl_yen': 15500.55, 'stop_low_mfe_count': 1, 'stop_low_mfe_pnl_yen': -21500.0, 'delta_pnl_vs_baseline': 6000.15}**
8. Runtime candidate: **False**
9. Shadow candidate: **P2_A1_r30_minus_r5_B2_vwap_extension_rate**
10. Next actions: ['Verdict: overfit_guard', 'Guard P2_A1_r30_minus_r5_B2_vwap_extension_rate improves in-sample but LOO unstable — shadow only', 'Best delta PnL vs baseline: 76323.43']

## Top guards by PnL

- **P2_A1_r30_minus_r5_B2_vwap_extension_rate** PnL 240582.65 dPnL 76323.43 slm -14 blkW 115
- **P2_A1_r30_minus_r5_D2_board_change_10m** PnL 239963.3 dPnL 75704.08 slm -13 blkW 107
- **P2_A2_r15_minus_r5_D2_board_change_10m** PnL 231250.94 dPnL 66991.72 slm -22 blkW 138
- **P1_A1_r30_minus_r5** PnL 209412.71 dPnL 45153.49 slm -16 blkW 122
- **P2_A2_r15_minus_r5_B1_vwap_dev_pct** PnL 204552.69 dPnL 40293.47 slm -21 blkW 136
- **P2_B2_vwap_extension_rate_D2_board_change_10m** PnL 200650.88 dPnL 36391.66 slm -24 blkW 164
- **P2_A2_r15_minus_r5_D3_board_decay_score** PnL 182751.73 dPnL 18492.51 slm -20 blkW 145
- **P2_A2_r15_minus_r5_B2_vwap_extension_rate** PnL 181951.54 dPnL 17692.32 slm -22 blkW 147

**Verdict:** `overfit_guard`
