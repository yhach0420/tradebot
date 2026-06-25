# Phase508 — Classic Top Strategy Robustness Audit

**Verdict:** `phase508_classic_robustness_audit`  
**Mode:** Research only — no adoption.

---

## Objective

Determine whether Phase507 top classical strategies (`C_T15_E1`, `C_T15_E2`, `C_T13_E2`) show **reproducible edge** or **few-big-wins fragility**, vs `BASELINE_RUNTIME`.

| Item | Value |
|------|-------|
| Period | 20260529 – 20260622 (Phase507) |
| Targets | BASELINE_RUNTIME, C_T15_E1, C_T15_E2, C_T13_E2 |
| Runner | `python scripts/run_phase508_classic_top_strategy_robustness_audit.py` |

---

## Mandatory verdict

| Verdict | Strategies |
|---------|------------|
| `classic_candidate_robust` | *(none)* |
| `classic_candidate_fragile` | BASELINE_RUNTIME, C_T15_E1, C_T15_E2, C_T13_E2 |

All four targets fail ≥2 fragility signals (profit concentration, symbol/day dependency, session_end dominance).

---

## Investigation 1 — Profit concentration

| Strategy | Top-1 | Top-5 | **Top-10** | Gini |
|----------|-------|-------|------------|------|
| BASELINE_RUNTIME | 7.9% | 25.3% | **35.8%** | 0.67 |
| C_T15_E1 | 23.8% | 58.0% | **71.1%** | 0.70 |
| C_T15_E2 | 21.3% | 41.9% | **52.8%** | 0.81 |
| C_T13_E2 | 22.2% | 59.2% | **73.4%** | 0.78 |

**Answer:** Top-10 trades account for **71.1%** (T15_E1), **52.8%** (T15_E2), **73.4%** (T13_E2) of gross winning PnL. Classical winners are **highly concentrated**; baseline is more dispersed.

Histograms: `phase508_report.json` → `histograms`.

---

## Investigation 2 — Symbol dependency

| Strategy | Excl top-1 | Excl top-3 | Excl top-5 | `single_symbol_dependency` |
|----------|------------|------------|------------|------------------------------|
| BASELINE_RUNTIME | 68,961 | -2,538 | -47,838 | **true** (6976 = 67.9%) |
| C_T15_E1 | 94,510 | -64,490 | -169,190 | **true** (6976 = 81.2%) |
| C_T15_E2 | -33,270 | -168,670 | -229,970 | **true** |
| C_T13_E2 | -93,590 | -208,990 | -263,390 | **true** |

**Answer:** Excluding top symbols collapses classical PnL to near-zero or negative. `C_T15_E1` top symbol **6976** alone contributes **408,500** (81% of total).

---

## Investigation 3 — Day dependency

| Strategy | Excl top-1 day | Excl top-3 days | `single_day_dependency` |
|----------|----------------|-----------------|-------------------------|
| BASELINE_RUNTIME | 120,060 | -8,239 | **true** |
| C_T15_E1 | 161,210 | -102,610 | **true** (20260615 = 68%) |
| C_T15_E2 | 2,730 | -220,170 | **true** |
| C_T13_E2 | 102,910 | -226,590 | **true** |

---

## Investigation 4 — Exit reason

| Strategy | session_end count | session_end PnL | session_end dep %* |
|----------|-------------------|-----------------|---------------------|
| BASELINE_RUNTIME | 19 | 55,500 | 25.8% |
| C_T15_E1 | 65 | 1,070,590 | 212.8% |
| C_T15_E2 | 61 | 884,680 | 282.9% |
| C_T13_E2 | 64 | 1,088,490 | 272.5% |

\* `session_end_pnl / net_total_pnl`. Values >100% mean hard_stop losses offset; **all net profit comes from session_end holds** while hard_stops are uniformly negative.

Classical E1/E2 pattern: **hard_stop** (100% loss rate) vs **session_end** (85% win rate, PF >> 1).

---

## Investigation 5 — Hold time

| Strategy | Mean | Median | P90 | P95 | Verdict |
|----------|------|--------|-----|-----|---------|
| BASELINE_RUNTIME | 12.1m | 5.5m | 28.4m | 47.1m | exit_failure |
| C_T15_E1 | 119.3m | 45.9m | 336.2m | 338.0m | **trend_capture** |
| C_T15_E2 | 24.0m | 1.1m | 62.2m | 180.0m | exit_failure |
| C_T13_E2 | 56.7m | 12.5m | 216.3m | 286.1m | exit_failure |

`C_T15_E1` is the only strategy with sustained holds consistent with trend capture.

---

## Investigation 6 — Baseline consistency

| Source | PnL | Trades | PF |
|--------|-----|--------|-----|
| strategy_battle_summary.csv | 214,959.61 | 440 | 1.3476 |
| strategy_battle_daily.csv (sum) | 214,959.61 | — | — |
| strategy_battle_trades.csv (sum) | **0** | 440 | — |
| Phase508 re-sim | 214,959.61 | 440 | 1.3476 |

**Root cause:** Phase507 baseline export uses `pnl_yen` in `_trade_summary_rows()` but CSV column is `pnl_yen_100`. Classical rows use `state_trade_logs()` which maps correctly. Summary/daily align; trades CSV baseline PnL is empty.

---

## T15 attribution (research answers)

| Question | Answer |
|----------|--------|
| Why was T15 strong? | Stoch %K>%D **plus** RSI>50 filters entries; E1 holds winners to session_end |
| RSI contribution? | **Partial** — RSI>50 necessary; RSI-only T1–T3 on E1 are deeply negative (-69k best) |
| Stochastic contribution? | **High** — T15 uniquely combines Stoch cross with RSI filter |
| Trend-follow? | **Plausible for T15_E1** — long median hold (46m), session_end captures large moves |
| vs PBv2 research value? | **Yes for signal isolation**, but fragile: higher headline PnL, worse maxDD/stability than baseline |

---

## Outputs

- `results/reports/phase508_robustness_summary.csv`
- `results/reports/phase508_symbol_dependency.csv`
- `results/reports/phase508_day_dependency.csv`
- `results/reports/phase508_exit_breakdown.csv`
- `results/reports/phase508_hold_time.csv`
- `results/reports/phase508_report.json`
