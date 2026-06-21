# Phase456C — VWAP Structure Tournament

Generated: 2026-06-20T01:17:42+09:00  
Period: 20260529..20260619  
Population: **Phase452 Runtime** — Momentum:low + (Board:mid|high) + High Drift + Weak Shape Reject + NP exit

Research only — no Runtime / YAML / Entry / Exit / Order / Discord changes.

**Goal:** Replace time-based `vwap_above_duration_min` (~41 sec) with tick/count-based VWAP structure features.

**Verdict:** `vwap_structure_candidate`

---

## Mandatory answers

| # | Answer |
|---|--------|
| 1 | **Best single feature:** `vwap_structure_score` (D1, +40,050 yen) |
| 2 | **Best composite:** `D4_stability_plus_distance` — `consecutive_above_ticks` AND `vwap_dev_pct` (+47,401 yen) |
| 3 | **vs Phase456 H:** tick best **−1,900 yen** vs H (+47,401 vs +49,301); **96% of H PnL lift** with no time feature |
| 4 | **PnL improvement:** **+47,401 yen** (best tick-based) |
| 5 | **PF improvement:** **+0.0835** (1.1925 → 1.2760) |
| 6 | **MaxDD improvement:** **−7,350 yen** (133,650 → 126,300) |
| 7 | **6976:** **+15,500 yen** (same as H) |
| 8 | **6920:** **0** (no trades) |
| 9 | **4062:** **+12,501 yen** (same as H) |
| 10 | **Time dependency removed:** **Yes** — D4 matches H profile (18 vs 22 blocks, same symbol/day deltas) |
| 11 | **Runtime candidate:** **Yes (shadow)** — tick-based, 6976 preserved |
| 12 | **Overfit risk:** **Low** — 18 blocks, top_day_share 0.17, top_symbol_share 0.11 |

---

## Part A — Reference: Phase456 H (time-based)

| Metric | Baseline | H_ref (duration < 0.69 min) |
|--------|----------|----------------------------|
| PnL | 147,412 | **196,713** (+49,301) |
| PF | 1.1925 | **1.2772** |
| MaxDD | 133,650 | **126,200** |
| blocked | — | 22 (14L / 8W) |
| 6/18 Δ | — | +400 |
| 6/19 Δ | — | +2,100 |
| 6976 Δ | — | +15,500 |

---

## Part B — Feature Group Summary

| Group | Best variant | ΔPnL | blocked | Notes |
|-------|--------------|------|---------|-------|
| Reclaim | A2 (tie) | 0 | 0 | A1/A3 harmful; reclaim alone insufficient |
| Stability | B2 consecutive_above | +8,801 | 35 | Partial lift |
| Distance | C1 vwap_dev_pct | −55,153 | 199 | Too aggressive alone |
| **Structure** | **D4 stability+distance** | **+47,401** | **18** | **Near-parity with H** |
| Structure (single) | D1 structure_score | +40,050 | 72 | Strong single proxy |

---

## Part C — Top variants (ΔPnL vs baseline)

| Variant | Feature(s) | ΔPnL | vs H | blocked | 6976 Δ |
|---------|------------|------|------|---------|--------|
| **D4** | B2 ∧ C1 | **+47,401** | −1,900 | 18 | +15,500 |
| **H_ref** | duration_min | +49,301 | — | 22 | +15,500 |
| D1 | structure_score | +40,050 | −9,251 | 72 | +14,000 |
| B2 | consecutive_above_ticks | +8,801 | −40,500 | 35 | +14,000 |
| B1 | vwap_above_ratio | +931 | −48,370 | 56 | −7,000 |
| A2/C2/others | — | ≤ 0 | — | — | — |

**D4 rule:** reject if `consecutive_above_ticks < 20.5` **AND** `vwap_dev_pct < 0.209%`  
(intersection filters weak VWAP reclaim without meaningful distance — same loss cluster as H).

---

## Part D — Time vs tick equivalence

| Aspect | Phase456 H | Phase456C D4 |
|--------|------------|--------------|
| Uses clock time | Yes (minutes) | **No** |
| Mechanism | Sustained above VWAP | Consecutive ticks above + dev from VWAP |
| blocked | 22 | 18 |
| blocked_pnl | −48,101 | −47,801 |
| Net PnL gap | — | **−1.9k (−3.9%)** |

**Conclusion:** tick-based structure captures the same entry-quality phenomenon without a fragile 41-second threshold.

---

## Part E — Target symbols (D4 vs baseline)

| Symbol | Baseline | D4 | Δ |
|--------|----------|-----|---|
| 6976 | +150,500 | +166,000 | **+15,500** |
| 6920 | 0 | 0 | 0 |
| 4062 | −40,998 | −28,497 | **+12,501** |

Identical symbol impact profile to Phase456 H.

---

## Part F — Adoption verdict

| Criterion | D4 |
|-----------|-----|
| PnL vs baseline | ✓ (+47k) |
| Near H parity | ✓ (96%) |
| No time feature | ✓ |
| 6976 preserved | ✓ |
| Low concentration | ✓ (18 blocks) |
| Walk-forward | ✗ pending Phase456B |

**Next:** Shadow-eval `D4_stability_plus_distance`; walk-forward on tick thresholds; prefer D4 over H for production (no minute-based overfit).

---

## Outputs

- `results/reports/phase456c_vwap_structure_tournament.csv`
- `results/reports/phase456c_vwap_structure_detail.csv`
- `results/reports/phase456c_vwap_structure_summary.json`

Run: `python scripts/run_phase456c_vwap_structure_tournament.py`
