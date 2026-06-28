# Phase551A — Runtime Daily Attribution

**Verdict:** `phase551a_runtime_daily_attribution_done`
**Variant:** B_current_runtime
**Period:** 20260616-20260625
**Total PnL (full-path eval):** -30800.0 yen

Note: `phase551_runtime_daily.csv` uses per-day guard replay (cross-day reentry state resets).
Phase551A regroups accepted trades from full-path eval so daily PnL sums to the live total.

## Daily PnL ranking (trading days only, best → worst)

| Rank | Day | PnL | Trades | MFE0 | NoProgress |
| ---: | --- | ---: | ---: | ---: | ---: |
| 1 | 20260622 | 28600.0 | 7 | 2 | 2 |
| 2 | 20260616 | 7600.0 | 103 | 62 | 0 |
| 3 | 20260624 | 500.0 | 11 | 0 | 4 |
| 4 | 20260617 | -17300.0 | 12 | 0 | 0 |
| 5 | 20260618 | -50200.0 | 15 | 1 | 0 |

## Top 5 good days

- #1 **20260622**: 28600.0 yen (7 trades, MFE0=2, NoProgress=2)
- #2 **20260616**: 7600.0 yen (103 trades, MFE0=62, NoProgress=0)
- #3 **20260624**: 500.0 yen (11 trades, MFE0=0, NoProgress=4)

## Top 5 bad days

- #1 **20260618**: -50200.0 yen (15 trades, MFE0=1, NoProgress=0)
- #2 **20260617**: -17300.0 yen (12 trades, MFE0=0, NoProgress=0)

## -30,800 yen の主因営業日（損失寄与順）

1. **20260618** — -50200.0 yen (74.37% of loss days, cumulative -50200.0 yen)
2. **20260617** — -17300.0 yen (25.63% of loss days, cumulative -67500.0 yen)

## Output files

- `results/reports/phase551a_runtime_daily_attribution.csv`
- `results/reports/phase551a_runtime_daily_ranking.csv`
- `results/reports/phase551a_runtime_daily_top5.csv`
- `results/reports/phase551a_runtime_daily_equity_curve.csv`
- `results/reports/phase551a_runtime_daily_loss_cause_ranking.csv`
- `results/reports/phase551a_report.json`
