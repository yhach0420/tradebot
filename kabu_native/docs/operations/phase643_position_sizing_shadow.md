# Phase643: Position Sizing Shadow Research

## Scope

Research-only counterfactual study comparing position-sizing policies on **unchanged ENTRY/EXIT/PBv2/OR logic**. Main line remains **100 shares fixed**. No real orders, no YAML/runtime trading changes.

## Data sources

| Source | Coverage |
|--------|----------|
| Phase630 parity replay | 2026-06-25, 06-29, 06-30, 07-01 (`_phase630/current`) |
| Phase627+ live sessions | Appended when not overlapping Phase630 days (dedupe by day/symbol/entry_time) |

Run: `2026-07-05` — **16,648 trades** from Phase630 replay (live supplement: 0 sessions in overlap window).

## Variants compared

| ID | Key | Policy |
|----|-----|--------|
| A | `fixed_100` | Baseline 100 shares |
| B | `equity_10pct` … `equity_50pct` | Equity ratio, 100-share lot, skip if unaffordable |
| C | `risk_0.25pct` … `risk_1.00pct` | Fixed risk % of equity / 1.20% hard-stop width |
| D | `pbv2_score_linked` | Score 3→100, 4→200, 5→300 (`PBV2_SCORE_SHARES` configurable) |
| E | `liquidity_tv_band` | TradingValue tertile → 100/200/300 |
| E | `liquidity_turnover_band` | Turnover tertile → 100/200/300 |
| E | `liquidity_update_freq` | Board update count tertile → 100/200/300 |

Equity levels: **1M / 3M / 5M / 10M yen**.

## Key results (3M equity reference)

| Variant | PnL (yen) | PF | MaxDD (yen) | Skips |
|---------|-----------|-----|-------------|-------|
| `fixed_100` | +1,520 | 1.0009 | 123,100 | 911 |
| `liquidity_tv_band` | **+32,990** | **1.0107** | 168,900 | 911 |
| `pbv2_score_linked` | +1,520 | 1.0009 | 123,100 | 911 |
| `equity_30pct` | −371,920 | 0.8799 | 388,920 | 2,004 |
| `risk_1.00pct` | −967,250 | 0.8860 | 1,013,290 | 1,469 |

## Mandatory answers

1. **Highest profit:** `liquidity_tv_band` at 3M (+32,990 yen vs baseline +1,520)
2. **Best PF:** `liquidity_tv_band` (1.0107 at 3M)
3. **Lowest DD:** `fixed_100` (123,100 yen at 3M) — sizing up increases drawdown
4. **Operationally feasible?** **No** — equity/risk variants generate large skip counts and deep losses at 1M–3M; min-lot + capital constraints dominate
5. **Beats 100-share fixed?** **Marginally yes** — only `liquidity_tv_band` at 3M (+31k delta); not robust at 1M/5M/10M
6. **Mainline candidate?** **None** — no variant beats fixed on PnL + PF + DD jointly
7. **Continue shadow monitoring:** `liquidity_tv_band`, `pbv2_score_linked`, `liquidity_update_freq`

### Additional analysis

| Question | Result |
|----------|--------|
| High-price dependency improved? | Yes — `liquidity_tv_band` high-price PnL +122,700 vs +63,700 fixed |
| DD shallower? | No — all upsizing variants deepen DD |
| Profit higher? | Yes for `liquidity_tv_band` at 3M only |
| ENTRY count reduced? | Yes for equity/risk tiers (capital skips) |
| Win rate changed? | Yes where skips alter sample |
| Symbol concentration worse? | No material worsening |
| Min-lot feasible? | Partial — score-linked equals fixed (all score≈3); liquidity upsizing feasible but DD cost |

**Note:** `pbv2_score_linked` ≡ `fixed_100` because replay trades predominantly carry score 3; score 4/5 upsizing untested until higher-score cohort appears.

## Artifacts

```
results/reports/phase643_position_sizing_shadow/
  phase643_report.json
  phase643_variant_comparison.csv
  phase643_daily_breakdown.csv
  phase643_symbol_breakdown.csv
  phase643_equity_curve.csv
  phase643_skip_analysis.csv
```

## Run

```bash
python -m pytest tests/test_phase643_position_sizing_shadow.py -q
python scripts/run_phase643_position_sizing_shadow.py
```

## Module

`src/research/phase643_position_sizing_shadow.py` — trade loader, variant simulator, report writer.

## Verdict

`phase643_position_sizing_shadow_done`

## Recommendation

**Hold 100-share mainline.** Continue **Shadow4 `liquidity_tv_band`** observation on future paper sessions; revisit when PBv2 score 4/5 cohort grows or capital base ≥ 5M with DD tolerance validated.
