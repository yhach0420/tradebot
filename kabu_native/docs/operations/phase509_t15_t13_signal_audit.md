# Phase509 — T15/T13 Signal Definition Audit

**Verdict:** `signal_definition_audit_done`  
**Mode:** Research only — no adoption.

---

## Objective

For Phase507/508 top entry rules **T15** and **T13**, answer:

- Why did they win?
- Are the signals reproducible (defined, measurable, not artifact)?

| Item | Value |
|------|-------|
| Period | 20260529 – 20260622 |
| Universe | Phase507 replay pool (~159 symbols) |
| Runner | `python scripts/run_phase509_t15_t13_signal_audit.py` |

---

## Investigation 1 — Complete signal definitions (from code)

### T15

| Parameter | Value |
|-----------|-------|
| RSI period | **14** (`RSI14`) |
| RSI threshold | **> 50** |
| Stochastic period | **14** (%K lookback) |
| %D smooth | **3** (SMA of %K) |
| %K / %D fields | `STOCH_K`, `STOCH_D` |
| Fire condition | `STOCH_K > STOCH_D AND RSI14 > 50` |
| Bar resolution | 1m |
| Warmup | 30 bars |
| Cooldown | 300s per symbol-day |
| Window | `DEFAULT_ALLOWED_WINDOWS` |

```text
for each 1m bar i (after warmup=30, in allowed trading window):
  if RSI14 is None: skip
  if cooldown since last signal < 300s: skip
  if STOCH_K > STOCH_D AND RSI14 > 50:
    fire T15 signal at bar.close
```

Source: `phase507_classic_strategy_battle.py` T15 + `phase507_classic_indicators.py` `_stochastic(period=14, smooth=3)`.

### T13

| Parameter | Value |
|-----------|-------|
| EMA period | **20** (`EMA20`) |
| VWAP condition | `close > cumulative intraday VWAP` |
| ADX period | **14** |
| ADX threshold | **> 20** |
| Fire condition | `close > EMA20 AND close > VWAP AND ADX > 20` |

```text
for each 1m bar i (after warmup=30, in allowed trading window):
  if RSI14 is None: skip
  if cooldown since last signal < 300s: skip
  if close > EMA20 AND close > VWAP AND ADX > 20:
    fire T13 signal at bar.close
```

### BASELINE_RUNTIME

PBv2 entry (momentum P33 + board gate + shape guards) + Phase503 `classic_late_chase_rsi_guard` + runtime exit shadows.

---

## Investigation 2 — Signal frequency

| Rule | total_signals | active_days | active_symbols | per_day | per_symbol |
|------|---------------|-------------|----------------|---------|------------|
| T15 | **9,995** | 13 | 159 | 768.9 | 62.9 |
| T13 | **7,552** | 13 | 161 | 580.9 | 46.9 |

Signals are **high-frequency** on 1m bars; CAP=5 converts ~10k raw signals into ~184 T15 trades.

---

## Investigation 3 — Symbol distribution (top PnL)

### T15 top 5 (of 20 in CSV)

| Symbol | signals | trades | PnL |
|--------|---------|--------|-----|
| 6976 | 281 | 2 | **408,500** |
| 3110 | 65 | 1 | 84,000 |
| 6387 | 59 | 2 | 75,000 |
| 6227 | 212 | 4 | 57,000 |
| 3436 | 127 | 2 | 47,700 |

### T13 top 5 (of 20 in CSV)

| Symbol | signals | trades | PnL |
|--------|---------|--------|-----|
| 6976 | 232 | 4 | **493,000** |
| 6387 | 38 | 3 | 72,000 |
| 3687 | 168 | 5 | 43,400 |
| 3891 | 107 | 5 | 33,000 |
| 3436 | 119 | 2 | 21,400 |

Both rules share **6976** as dominant PnL driver (Phase508 confirmed).

---

## Investigation 4 — Day distribution

| Rule | 20260615 PnL | 20260615 share | Top-3 days share |
|------|--------------|----------------|------------------|
| T15 | **341,800** | **68.0%** | 120.4%* |
| T13 | **296,500** | **74.2%** | 156.7%* |

\* Top-3 share >100% because many days are net negative.

---

## Investigation 5 — PBv2 overlay

Subset of **PBv2 baseline trades** (440) where T15/T13 also fire at entry 1m bar:

| Group | Description | Trades | PnL | PF | Win% | hard_stop% | session_end% |
|-------|-------------|--------|-----|-----|------|------------|--------------|
| A | PBv2 only | 440 | 214,960 | 1.35 | 58.4% | 13.2% | 4.3% |
| B | PBv2 AND T15 | 77 | 27,269 | 1.41 | 59.7% | 14.3% | 9.1% |
| C | PBv2 AND T13 | 61 | 73,128 | **2.70** | **70.5%** | 11.5% | 14.8% |
| D | PBv2 AND T15 AND T13 | 22 | 28,969 | 2.41 | 68.2% | 13.6% | 18.2% |

Overlap: T15 **17.5%**, T13 **13.9%** of PBv2 entries.

| Question | Answer |
|----------|--------|
| T15 as PBv2 quality filter? | **weak_filter_signal** — PF/win_rate slightly up but avg PnL/trade down |
| T13 as PBv2 quality filter? | **potential_quality_filter** — PF 2.70, win 70.5% on 61-trade subset |

---

## Investigation 6 — Trend day dependency (T15)

Symbol-day comparison (519 with T15 signal vs 10 without):

| Metric | Signal sym-day | Non-signal sym-day |
|--------|----------------|---------------------|
| intraday_return % | -0.05 | -0.21 |
| range % | **9.50** | 3.56 |
| volume | **4,504** | 2,294 |

**T15 regime classification:** `momentum_continuation`  
(Higher range/volume on signal days; return delta positive vs non-signal sym-days.)

---

## Final answers

| Question | Answer |
|----------|--------|
| T15 complete definition | Stoch(14,3) cross + RSI14>50 on 1m, 300s cooldown |
| T13 complete definition | close>EMA20 + close>VWAP + ADX14>20 on 1m |
| Why T15 strong | Many raw signals; CAP selects few session_end winners; 6976 concentration |
| Why T13 strong | Explicit trend triple-filter; aligns with high-range days; same 6976 dependency |
| PBv2 relationship | Low overlap (14–18%); T13 overlay improves PF more than T15 |
| PBv2 integration research value? | **Moderate** — guard/filter research OK; production adoption blocked by Phase508 fragility |

---

## Outputs

- `results/reports/phase509_signal_definition_report.json`
- `results/reports/phase509_signal_frequency.csv`
- `results/reports/phase509_symbol_distribution.csv`
- `results/reports/phase509_day_distribution.csv`
- `results/reports/phase509_pbv2_overlay.csv`
- `results/reports/phase509_top_examples.csv`
