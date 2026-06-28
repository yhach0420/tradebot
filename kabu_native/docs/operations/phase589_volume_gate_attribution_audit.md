# Phase589 — Volume Gate Attribution Audit

**Verdict:** `phase589_volume_gate_attribution_done`
**Period:** 20260529–20260626

## Key finding

Production Volume Gate = **daytrade_suitability** (volatility_liquidity_score=atr_pct*log10(trading_value); threshold=median prior session scores (top50 rule)).
Phase588 `no_volume` replay disabled **phase364 near-day-high guard**, not this gate (ΔPnL proxy=268350.88).

## Mandatory answers

1. Volume Gate watches: **volatility_liquidity_score=atr_pct*log10(trading_value); threshold=median prior session scores (top50 rule)**
2. Top reject condition: **vol_liq_score_well_below_threshold** (137172 live rejects)
3. Truly unnecessary (partial): **['vol_liq_score_slightly_below_threshold', 'turnover_proxy_low']** — slightly-below-threshold band; well-below band blocks losers
4. All volume unnecessary: **False** (OFF ΔPnL=268350.88, quality-safe=False)
5. Partial unnecessary: **True** (best=V80)
6. Relaxation improves: **True** (V90 ΔPnL=69199.98)
7. Runtime candidate: **True**
8. Next phase: **phase590_volume_gate_relaxation_shadow_pilot**

Baseline threshold (pool median): 68.611429
Baseline replay PnL/PF: 235602.05 / 2.5054
Counterfactual match rate: 0.05%

## Outputs

- `results/reports/phase589_volume_algorithm.csv`
- `results/reports/phase589_volume_reject_breakdown.csv`
- `results/reports/phase589_volume_reject_counterfactual.csv`
- `results/reports/phase589_volume_relaxation_replay.csv`
- `results/reports/phase589_volume_daily_symbol_impact.csv`
- `results/reports/phase589_report.json`