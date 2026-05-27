# Phase 156 recommendation

**Verdict:** `refresh_promising_cap3_enough`

## Summary

- Review-only: no production YAML, no live refresh wiring.
- Entry policy: price-risk universe filter + `entry_price_risk_guard` on candidates.
- Refresh exit policy: **candidate 1** (hold positions; update new-entry universe only).
- Register: always include `open_symbols` before Core10/Dynamic trim to 50.

## Cap comparison (price-risk guarded candidates)

- cap=3: accepted=732, pnl_proxy=28.8355, pf_mean=1.2511, overlap_proxy=361, delta_vs_cap3=0.0
- cap=5: accepted=1216, pnl_proxy=31.4675, pf_mean=1.2132, overlap_proxy=762, delta_vs_cap3=2.632
- cap=7: accepted=1695, pnl_proxy=21.6134, pf_mean=1.1524, overlap_proxy=1190, delta_vs_cap3=-7.2221

## Scenario what-if (S0–S3)

- S0 (AM/PM only cap3): pnl_proxy=28.8355, with_refresh=28.8355, uplift=0.0
- S1 (AM/PM only cap5): pnl_proxy=31.4675, with_refresh=31.4675, uplift=0.0
- S2 (10:00/14:30 refresh proxy cap3): pnl_proxy=28.8355, with_refresh=349.1795, uplift=320.344
- S3 (10:00/14:30 refresh proxy cap5): pnl_proxy=31.4675, with_refresh=347.3009, uplift=315.8334

## Notes

- cap5_pnl_delta_vs_cap3=2.632
- cap5_overlap_delta=401
- refresh_uplift_s2=320.344
- cap5 overlap_proxy_delta=401 too high
- refresh post-window MC proxy positive but keep cap3 until overlap policy

## Methodology caveats

- Cap/overlap metrics are **counterfactual ExposureGate** sims (virtual-hold PnL), not live fills.
- Refresh uplift uses **15% of positive** post-10:00/14:30 MC-reject would-be PnL (upper-bound proxy).
- True refresh benefit needs `*_refresh1000_*` / `*_refresh1430_*` universe CSVs + shadow days.

## Next steps

1. Shadow YAML added: `small_paper_pilot_q070_cap5_entry_price_risk_guard_shadow.yaml` (do not wire prod).
2. Implement register merge (open_symbols > Core10 > Dynamic) before any refresh live trial.
3. Implement refresh CSV writers + daily_runner feature flag (candidate 1 exit policy).
4. Run cap5 shadow only after overlap policy review; refresh before cap5 if verdict C holds.
