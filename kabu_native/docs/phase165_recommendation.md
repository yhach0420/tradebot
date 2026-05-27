# Phase 165: overlap close policy review

**Verdict:** `overlap_not_primary_after_correct_replay`

## Scenario summary (subset=all)

| Scenario | PF | total PnL | overlap close | suppressed | saved old | missed good new |
|----------|-----|-----------|--------------:|-----------:|----------:|----------------:|
| A_baseline | 0.8823 | -7.3341 | 393 | 0 | 0 | 0 |
| B_hold_old | 0.859 | -7.5365 | 0 | 381 | 165 | 144 |
| C_protect_profitable_old | 0.8591 | -8.0407 | 101 | 281 | 168 | 100 |
| D_protect_mfe_old | 0.8618 | -7.9878 | 231 | 154 | 154 | 55 |
| E_delayed_replace_60s | 0.859 | -7.5365 | 0 | 83 | 21 | 144 |
| F_priority_qgap_005 | 0.859 | -7.5365 | 0 | 381 | 165 | 144 |
| G_fade_watch_protect | 0.8823 | -7.3341 | 393 | 0 | 0 | 0 |
| H_combined | 0.859 | -7.5365 | 0 | 381 | 165 | 144 |

## Notes

- baseline_pf=0.8823 baseline_overlap_close=393
- overlap_close_reduced_but_pf_flat

## Design principle

- Accepted events are **never discarded**; when not opened as a position, they are tracked as virtual entries with would-be PnL.
- Only `overlap_replaced_review` **close timing** changes; structural exit rules unchanged.
