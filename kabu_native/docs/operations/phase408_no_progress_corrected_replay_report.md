# Phase408 — No Progress Exit Corrected Replay

Generated: 2026-06-16T01:17:51+09:00
Verdict: **adopt_candidate**

Phase408 corrected: hold=900.0s mfe<0.8% pnl<0.2% delta=¥68371.76 ADOPT audit_PASS

## Price path rule

- entry_time to baseline structural exit_time only
- If no shadow rule fires before baseline exit → baseline PnL
- Post-baseline candidate prices are **forbidden**

## Mandatory answers

1. **Corrected best policy:** hold=900.0s max_mfe<0.8% pnl<0.2% hi=none vwap=none
2. **Corrected net_delta:** ¥68371.76
3. **Corrected PF:** 1.2056
4. **Corrected maxDD:** ¥92851.55
5. **Corrected final_equity:** ¥1695839.36
6. **vs Phase404 uncorrected:** ¥-206540.64 (Phase404 was ¥274912.4)
7. **Phase407A capped match:** True (404-best corrected ¥68371.76 vs 407A ref ¥67872.4)
8. **Adopt candidate:** True
9. **Forward shadow continue:** True

## Replay audit

- Status: **PASS**
- post_baseline_usage_count: 0
- peak_mfe / price / pnl / multi-exit violations: 0 / 0 / 0 / 0
- tick_sparse_samples: 2

## Baseline

| total_pnl | ¥127467.6 |
| PF | 1.1236 |
| maxDD | ¥105301.93 |

## Corrected portfolio ranking (Phase406 redo)

| Rank | Policy | Tier | Rec | PnL | PF | maxDD | net_delta |
|------|--------|------|-----|-----|----|-------|-----------|
| 1 | phase405_corrected | Tier S | A_adopt_candidate | ¥272357.92 | 1.341 | ¥78350.58 | ¥144890.32 |
| 2 | phase402_corrected | Tier S | B_shadow_continue | ¥199267.33 | 1.2062 | ¥90501.64 | ¥71799.73 |
| 3 | phase404_corrected | Tier S | B_shadow_continue | ¥195839.36 | 1.2056 | ¥92851.55 | ¥68371.76 |
| 4 | phase403_corrected | Tier S | B_shadow_continue | ¥185247.22 | 1.1889 | ¥92501.64 | ¥57779.62 |

## Conclusion

Phase404 +¥274,912 used post-baseline prices and must not drive adoption. Corrected replay caps the path at baseline exit; use corrected metrics for decisions.

- Runtime / YAML / Entry / Exit / Discord unchanged
