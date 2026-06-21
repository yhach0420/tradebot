# Phase455 — D Group Win/Loss Feature Audit

Generated: 2026-06-19  
Period: 20260529..20260619  
Population: **Phase454 D-group** — 518 trades (Momentum:low + Board:mid, HD/WS both pass)

Research only — no Runtime changes.

## Mandatory answers

| # | Answer |
|---|--------|
| 1 | **D_win 287 / D_loss 213** (flat 18) |
| 2 | **D net PnL: −91,291 yen** |
| 3 | **TOP5 features (|Cohen's d|):** r30, r10, r5, high_update_age, entry_order_book_imbalance — **all weak (|d|≤0.20)** |
| 4 | **6920 separable:** **No** — global rules block wins with losses |
| 5 | **4062 separable:** **Yes** — best single blocks majority of 4062 losses |
| 6 | **6976 preserved:** **No** under best-PnL single rule; **Yes** under adoptable combo |
| 7 | **Best single:** `r10 < 0.3719` — ΔPnL **+105,479** (blocks 196: 84L/104W) |
| 8 | **Best adoptable combo:** `(r10 < 0.3719) AND (day_high_distance < 1.1872)` — ΔPnL **+90,199**, adopt_pass |
| 9 | **Expected improvement:** **+90k–105k yen** (in-sample D-group; walk-forward required) |
| 10 | **Runtime candidate:** **Yes (shadow)** — combo passes Part F filters |
| 11 | **Overfit risk:** **Low–medium** — effect sizes weak; combo blocks only 27 trades |
| 12 | **Next:** Shadow-eval combo rule; Phase455B walk-forward; do **not** deploy r10-only (6976 harm) |

**Verdict:** `actionable_pattern_found`

---

## Part A — D_win vs D_loss

| | D_win | D_loss |
|--|-------|--------|
| count | 287 | 213 |
| PnL | +1,076,498 | −1,167,789 |
| avg | +3,751 | −5,483 |
| PF | 2.35 | 0.0 |
| stop_rate | 0.087 | 0.324 |
| symbols | 118 | 95 |

Losses have **3.7× higher stop rate**; gross loss mass concentrated in 213 trades.

---

## Part B — Feature comparison (key insight)

Within D-group, **winners have higher r30/r10/r5** than losers (opposite of D-vs-all-winners in Phase454).

| Feature | d (loss−win) | Interpretation |
|---------|--------------|----------------|
| r30 | −0.20 | Wins chase more; losses enter flatter |
| r10 | −0.07 | weak |
| day_high_distance | losses slightly **closer** to high in losers |
| board imbalance | ~no difference |

**Conclusion:** separation exists but **effect sizes are small** — no single strong discriminator.

---

## Part C/D — Rule sweep

### Best single (PnL, not adoptable)
`r10 < 0.3719` — removes weak 10m momentum entries; **104 wins blocked** → hurts 6976 (−23k remaining).

### Best adoptable combo (Part F pass)
`(r10 < 0.3719) AND (day_high_distance < 1.1872)`

| Metric | Baseline | After combo |
|--------|----------|-------------|
| Blocked | — | 27 (15L / 10W) |
| Net PnL | −91,291 | −1,092 |
| PF | 0.92 | 0.999 |
| MaxDD | 372,800 | 367,000 |
| 6976 remaining | +72,000 | **+126,498** |
| 6920 remaining | −71,000 | −71,000 |
| 4062 remaining | −75,499 | −60,000 |

Pattern: **weak r10 + very near day high** — late chase near high without momentum confirmation.

---

## Part E — Target symbols

| Symbol | D_win/L | Net | Separable | Notes |
|--------|---------|-----|-----------|-------|
| 6920.T | 0/8 | −71,000 | No | All losses; combo doesn't fix |
| 4062.T | 3/31 | −75,499 | Yes | Many D losses; partial block |
| 6976.T | 19/36 | +71,999 | — | Combo **preserves** profit |

6920 losses are **not explained** by global feature rules → symbol-specific or idiosyncratic (6/19).

---

## Part F — Adoption verdict

**Adoptable combo found** meeting:
- PnL improvement ✓
- PF improvement ✓ (marginal)
- MaxDD not worse ✓
- 6976 not harmed ✓
- Not single-symbol / 6/19 dependent ✓

**Caveats:** weak effect sizes, 27-trade sample for combo, in-sample only.

---

## Outputs

- `phase455_d_group_win_loss_features.csv`
- `phase455_d_group_rule_candidates.csv`
- `phase455_d_group_symbol_breakdown.csv`
- `phase455_d_group_summary.json`

Run: `python scripts/run_phase455_d_group_win_loss_audit.py`
