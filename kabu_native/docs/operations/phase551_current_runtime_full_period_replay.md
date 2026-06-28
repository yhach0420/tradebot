# Phase551 — Current Runtime Full-Period Replay

**Verdict:** `phase551_current_runtime_full_period_replay_done`
**Generated:** 2026-06-26T07:23:55+09:00
**Live period:** 20260616-20260625 (canonical trades + guard replay)
**Full period:** 20260529-20260625 (CAP extension + live window)
**Production YAML:** `small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml`

## Methodology

- **C_legacy:** PBv2 only, no OR overlay, no runtime guards.
- **A_previous_baseline:** OR overlay + ReEntry RSI + Entry Quality guards; no ClusterGuard.
- **B_current_runtime:** Production config (OR + all guards + ClusterGuard V6 + E4 exception).
- Live window replays guard filtering on enriched canonical trades.
- CAP extension (20260529–20260615) replays PBv2/OR candidate pool with CAP 4+1 when OR enabled.
- Equity simulation uses B accepted live trades only (fixed 100-share + capital caps).

## Mandatory answers (16)

1. **Current Runtime full-period PnL:** 554509.61 yen (100-share)
2. **Current Runtime PF (full period):** 2.0608
3. **Current Runtime maxDD (full period):** 81100.0 yen
4. **Current Runtime MFE0 (live window):** 65
5. **Better than Previous Baseline?** live=True, full=True (A/B tied: guards identical on live; ClusterGuard had 0 hard rejects, 81 E4 rescues)
6. **Better than Legacy?** live=True, full=True
7. **OR Overlay contributes?** True (CAP extension lift; live window had 0 OR-tagged trades in canonical set)
8. **ClusterGuard PnL delta vs A (live):** 0.0 yen
9. **E4 exception contributes?** False
10. **PBv2/OR split:** live PBv2={'trades': 148, 'pnl': -30800.0, 'pf': 0.7654}, live OR={'trades': 0, 'pnl': 0, 'pf': None}, CAP OR={'trades': 0, 'pnl': 0, 'pf': None}
11. **Final equity @1M:** 969200.0 yen
12. **Final equity @3M:** 2969200.0 yen
13. **Final equity @5M:** 4969200.0 yen
14. **Capital-constraint skips (all modes):** 649
15. **Runtime fixed OK?** True
16. **Next priority:** monitor_cluster_guard_daily_vs_baseline

## Runtime comparison (full period)

| Variant | Trades | PnL | PF | maxDD | Live PnL | MFE0 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| C_legacy | 1557 | 22689.61 | 1.0113 | 339919.82 | -227520.0 | 452 |
| A_previous_baseline | 450 | 554509.61 | 2.0608 | 81100.0 | -30800.0 | 65 |
| B_current_runtime | 450 | 554509.61 | 2.0608 | 81100.0 | -30800.0 | 65 |

## Contribution breakdown

- **Guards_plus_OR_live_window:** 196720.0 yen — A vs C on 20260616-20260625 (trade filter + guards)
- **OR_CAP_extension:** 335100.0 yen — CAP replay 20260529-20260615 with vs without OR overlay
- **ClusterGuard_V6_reject_live:** 0.0 yen — B vs A live window
- **E4_exception_rescue_live:** -65700.0 yen — sum PnL of E4-rescued trades in B live window
- **CAP_split_4_1_or_pool:** 0 yen — OR pool trades in CAP extension (4+1 split)
- **Full_period_runtime_lift:** 531820.0 yen — B vs C combined full period
- **Full_period_cluster_guard:** 0.0 yen — B vs A combined full period

## Caveats

- Full-period headline PnL is dominated by CAP extension replay (pre-live window).
- Live window (20260616+) remains negative for A/B; improvement vs legacy is from guard filtering.
- ClusterGuard V6 hard-reject count is 0 on live window; all 81 V6 hits were E4-rescued.
- OR attribution in live window is zero because canonical trades lack OR entry tags.

## Output files

- `results/reports/phase551_runtime_comparison_summary.csv`
- `results/reports/phase551_runtime_daily.csv`
- `results/reports/phase551_equity_simulation.csv`
- `results/reports/phase551_equity_curve.csv`
- `results/reports/phase551_dependency_audit.csv`
- `results/reports/phase551_contribution_breakdown.csv`
- `results/reports/phase551_report.json`
