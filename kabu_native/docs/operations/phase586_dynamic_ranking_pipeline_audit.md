# Phase586 — Dynamic Ranking Pipeline Attribution Audit

**Verdict:** `phase586_dynamic_ranking_pipeline_audit_done`
**Period:** 20260529–20260626
**Sessions:** 41

## Ranking algorithm (AM production)

```
volatility_liquidity_score = atr_pct * log10(trading_value_jpy)
eligible = passes_dynamic_price_risk(close>=300, tick_ratio<=5%)
dynamic_rank = sort_index(volatility_liquidity_score DESC) among eligible \ Core10
```

## Mandatory answers

1. How ranking is built: AM: volatility_liquidity_score=atr_pct*log10(trading_value); sort DESC; price_risk filter; top40 dynamic
2. Final rank formula: dynamic_rank = index in sorted(volatility_liquidity_score DESC | passes_price_risk) excluding Core10
3. Score components: volatility_liquidity_score (AM); pm_composite_score (PM); price_risk hard filter; no sector in production rank
4. Contributes to universe: True
5. Contributes to entry candidate rate: True
6. Contributes to entry accept rate: False
7. Contributes to profit rate: False
8. Phase585 = ranking only issue: False
9. Primary bottleneck: ENTRY
10. Proceed ranking score research: False
11. Runtime change candidate: False
12. Next phase: phase587_entry_gate_attribution_research

- Global eval→accept: 0.3973%
- Global accept→win: 46.7118%