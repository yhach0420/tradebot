# Phase 159: overlap_replaced_review recommendation

**Verdict:** `overlap_mixed`

## Question

Does overlap exit destroy profit (vs holding the old leg)?

## Summary

- Overlap pairs analyzed: 354
- Hold better: 27.97% (99)
- Switch better: 22.03% (78)
- Neutral: 50.0%
- Avg delta (actual_pair - hold_old): 0.2499%
- Total PnL actual pairs: 0.1574%
- Total PnL if held old: -88.314%

## Notes

- hold_better=27.97% switch_better=22.03%
- avg_delta=0.2499 median=0.0
- hold/switch split 40-60%

## Constraints

- Review only; no production YAML / entry / exit changes.

## Interpretation

- Positive `delta` → switch (actual) better than hold-old counterfactual.
- Negative `delta` → overlap harmed vs simply holding.
- Cap5 candidacy (Phase158) is separate from overlap switch quality.
