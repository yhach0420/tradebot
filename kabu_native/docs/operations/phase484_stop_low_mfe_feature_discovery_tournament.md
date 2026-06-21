# Phase484 — Stop Low MFE Feature Discovery Tournament

**Verdict:** `new_feature_found`
**Period:** 20260529-20260619

## Mandatory answers

1. Strongest feature: **A2_r15_minus_r5** (d=0.2687)
2. Best 2-condition: **{'pattern_id': 'P2_A2_r15_minus_r5_D2_board_change_10m', 'conditions': 'A2_r15_minus_r5>0.0000@p40 AND D2_board_change_10m>-0.0620@p33', 'separation_score': 0.1971}**
3. SLM capture: **0.3571**
4. Winner capture: **0.16**
5. Expected delta: **-11311.85**
6. Runtime candidate: **False**
7. Next actions: ['Verdict: new_feature_found', 'Strongest feature: A2_r15_minus_r5 d=0.2687', 'Lead 2-condition: P2_A2_r15_minus_r5_D2_board_change_10m sep=0.1971', 'Separation found but expected_delta negative - shadow only']

## Top 20 features

- **1. A2_r15_minus_r5** (A_momentum_decay): d=0.2687 KS=0.183333 MI=0.0077
- **2. A1_r30_minus_r5** (A_momentum_decay): d=-0.2253 KS=0.189091 MI=0.0065
- **3. B2_vwap_extension_rate** (B_vwap_extension): d=0.2244 KS=0.166667 MI=0.0049
- **4. D2_board_change_10m** (D_board_deterioration): d=0.1488 KS=0.159583 MI=0.0006
- **5. D3_board_decay_score** (D_board_deterioration): d=0.1218 KS=0.107619 MI=0.0003
- **6. B3_vwap_reversion_score** (B_vwap_extension): d=-0.1205 KS=0.111242 MI=0.0007
- **7. B1_vwap_dev_pct** (B_vwap_extension): d=0.1202 KS=0.184762 MI=0.0161
- **8. A4_momentum_slope** (A_momentum_decay): d=-0.119 KS=0.162226 MI=0.0002
- **9. D1_board_change_5m** (D_board_deterioration): d=-0.0991 KS=0.133375 MI=0.0008
- **10. A3_r30_over_r5** (A_momentum_decay): d=-0.0627 KS=0.19697 MI=0.0129
- **11. C1_high_update_count_30m** (C_high_exhaustion): d=None KS=None MI=None
- **12. C2_high_update_count_session** (C_high_exhaustion): d=None KS=None MI=None
- **13. C3_high_update_density** (C_high_exhaustion): d=None KS=None MI=None

## Top patterns

- **P2_A2_r15_minus_r5_D2_board_change_10m**: sep 0.1971 slm 15 sw 12 delta -11311.85
- **P2_B2_vwap_extension_rate_D2_board_change_10m**: sep 0.1648 slm 17 sw 18 delta -26212.01
- **P1_A2_r15_minus_r5**: sep 0.1591 slm 19 sw 22 delta -91911.75
- **P1_D2_board_change_10m**: sep 0.14 slm 21 sw 27 delta -158912.87
- **P2_A1_r30_minus_r5_D2_board_change_10m**: sep 0.14 slm 7 sw 2 delta 11000.42
- **P2_A1_r30_minus_r5_B2_vwap_extension_rate**: sep 0.1372 slm 8 sw 4 delta 16519.95
- **P1_A1_r30_minus_r5**: sep 0.1314 slm 10 sw 8 delta -14649.99
- **P2_A2_r15_minus_r5_B2_vwap_extension_rate**: sep 0.12 slm 14 sw 16 delta -61011.25

**Verdict:** `new_feature_found`
