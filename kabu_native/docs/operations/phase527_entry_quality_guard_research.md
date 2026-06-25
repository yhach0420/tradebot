# Phase527 — Entry Quality Guard Research

**Verdict:** `phase527_entry_quality_guard_research_done`
**Period:** 20260616 – 20260624 (live paper only)
**Live trades:** 1229

## Hypothesis

stop_low_mfe is driven by **late chase** (high ADX / spread / day-high update count),
not mistaken downtrend recognition.

## Mandatory answers

- **1. stop_low_mfe reducer:** G10_adx30_spread50_update5
- **2. MFE0 reducer:** G10_adx30_spread50_update5
- **3. PnL maintainer:** G3_spread_le40
- **4. PnL+PF+DD combined best:** G9_spread50_update5
- **5. late_breakout reducer:** G10_adx30_spread50_update5
- **6. high_chase reducer:** G7_adx30_spread50
- **7. lowest blocked future_mfe:** G1_adx_le25
- **8. excludes bad trades only?:** True
- **9. operational candidate?:** True
- **10. next shadow guard:** G9_spread50_update5

## Guards tested

- A: baseline
- G1: ADX14 <= 25
- G2: ADX14 <= 30
- G3: spread <= 40bps
- G4: spread <= 50bps
- G5: update_count <= 3
- G6: update_count <= 5
- G7: ADX<=30 AND spread<=50
- G8: ADX<=30 AND update<=5
- G9: spread<=50 AND update<=5
- G10: ADX<=30 AND spread<=50 AND update<=5

Research only — no Runtime adoption.
