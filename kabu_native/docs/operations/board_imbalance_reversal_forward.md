# Board Imbalance Reversal Forward (H_board_ts)

**Canonical ID:** `board_imbalance_reversal_shadow`  
**Class:** TEMP_FORWARD  
**Env:** `BOARD_IMBALANCE_REVERSAL_SHADOW` (Paper default ON; Live forced OFF)

## Fixed Spec (SoT)

- Feature: `f_np_imb_chg_60` (`np_imb_chg_60s`)
- Threshold: `-0.038599` from `results/research/cost_aware_v2/report.json` (`thresholds.t_imb_chg`)
- Reject if `f_np_imb_chg_60 <= -0.038599`
- Missing / short board history: **Fail-open** (no virtual reject, no zero-fill)

## Not revived

- Cost-Aware V1 → RETIRED
- Cost-Aware V2 → DISABLED_RESEARCH
- I_price_board / Winner Enrichment / STOP Risk composites
- Old `board_imbalance_shadow` (RETIRED)

## Discord

Not shown in usual Shadow Summary (keeps 3: E1_X5 / Flat Weak / Board Dynamic).  
Listed in startup Shadow Portfolio TEMP_FORWARD line.
