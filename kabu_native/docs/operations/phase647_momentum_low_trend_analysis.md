# Phase647: Momentum Low Trend Attribution

Research-only analysis of PBv2 `momentum_low` entries (momentum_continuation_score <= 0.2546).
OR overlay entries excluded.

## Data sources

- `results/small_paper/_phase630/current/*` (parity replay)
- `results/small_paper/<YYYYMMDD>/live_session_*` (live paper)

## Trend labels (entry-time features)

| Label | Heuristic |
|-------|-----------|
| Up Trend | r5>0.05, r10>0, VWAP not deeply negative |
| Sideways | default / mixed |
| Down Trend | r5<0 and r10<=0, or r10<-0.2 |
| Strong Down | r5<=-0.5 or (r5<=-0.3 and r10<=-0.8) |

Proxies: `entry_rise_5min_pct`, `entry_rise_10min_pct`, `entry_vwap_dev_pct`, `day_high_distance_pct`.

## Run

```bash
python scripts/run_phase647_momentum_low_trend_attribution.py
python -m pytest tests/test_phase647_momentum_low_trend_attribution.py -q
```

## Artifacts

```
results/reports/phase647_momentum_low_trend_attribution/
  phase647_report.json
  trend_distribution.csv
  trend_counterfactual.csv
  trend_feature_importance.csv
  sumco_case_study.md
```

## Constraints

- No ENTRY / YAML / shadow changes
- Analysis and counterfactual only

## Verdict

`phase647_momentum_low_trend_attribution_done`

## Findings (2026-07-06 run)

- **Sessions:** 39 | **PBv2 momentum_low trades:** 2,614 (OR excluded)
- **Pullback-like (Up+Sideways):** 66.2% | **Decline-like (Down+Strong Down):** 33.8%
- **Profit source:** Sideways (PnL +360,220, PF 1.17)
- **Loss source:** Up Trend (PnL -208,760, PF 0.70) — not decline buckets
- **Baseline:** PnL +143,970 yen | PF ~1.03 | max DD ~-621k (100-share units)
- **Best counterfactual:** exclude Down+Strong Down → ΔPnL +7,490, ΔPF +0.021, ΔDD +243k, but 456 wrongly-blocked winners vs 362 rescued losers
- **Recommendation:** **HOLD** — DD改善は有望だが in-sample・勝ち誤ブロック多い
- **SUMCO 2026-07-06:** 09:12 Sideways (-700), 09:13 Strong Down (-7,600, stop_hit) → Strong-Down除外でブロック対象
