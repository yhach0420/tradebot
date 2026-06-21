# Phase454 — Board:mid Loss Pattern Audit

Generated: 2026-06-19T23:58:33+09:00  
Period: 20260529..20260619  
Population: **Momentum:low + Board:mid only** (n=694, losses=284)

Research only — Phase439 High Drift + Phase452 Weak Shape Reject 導入後の **残存負け** を分析。

## Mandatory answers

| # | Answer |
|---|--------|
| 1 | Board:mid **負け件数: 284** |
| 2 | **A** (HD only): **9** |
| 3 | **B** (WS only): **49** |
| 4 | **C** (both): **13** |
| 5 | **D** (neither): **213** (75% of losses) |
| 6 | **D群 net PnL: −91,291 yen** (518 trades all classes D; PF 0.92) |
| 7 | **6920:** 8 trades, **all D**, net −71,000 yen |
| 8 | **6976:** D=36, B=10, A=6, C=3; net **+71,999 yen** (losses mostly D) |
| 9 | **4062:** D=31, B=1, A=2; net **−75,499 yen** |
| 10 | **未解決パターン: Yes** — 213 losses slip both guards |
| 11 | **次ガード候補:** see Part F |
| 12 | **期待改善額:** gross D-loss upper bound **1,167,789 yen**; realistic CAP5 subset TBD |

**Verdict:** `remaining_pattern_found`

---

## Part A — Loss TOP100

See `phase454_board_mid_loss_audit.csv`. Worst: **6920.T** −67,000 / −66,000 (both class **D**).

---

## Part B — Guard leak (on 284 losses)

| Class | Meaning | Count | Share |
|-------|---------|-------|-------|
| A | HD only would block | 9 | 3.2% |
| B | WS only would block | 49 | 17.3% |
| C | Both would block | 13 | 4.6% |
| **D** | **Both miss** | **213** | **75.0%** |

Existing guards explain **71 / 284** losses (25%); **213 remain unresolved**.

---

## Part C — D group (true leak)

| Metric | All D (518) | D losses only (213) |
|--------|-------------|------------------------|
| Count | 518 | 213 |
| PnL | −91,291 | −1,167,789 (gross) |
| PF | 0.92 | 0.0 |
| Stop rate | 0.135 | 0.324 |

**Worst symbols in D:** 6920, 4062, 6976 (see summary JSON).

---

## Part D — D group vs winners (feature means)

| Feature | D group | Winners | Delta |
|---------|---------|---------|-------|
| high_update_age | 81.9 min | 93.4 min | D fresher |
| r15 | **+0.69%** | −0.02% | **D higher** |
| day_high_distance | **4.05%** | 4.65% | D closer to high |
| r30 | +0.39% | −0.15% | D higher |
| vwap_dev | −0.34% | −0.79% | D less below VWAP |

**Pattern:** D losses often **positive r15/r30 near day high** — late chase / false continuation, not classic weak-shape or high-drift.

---

## Part E — Target symbols

### 6920.T (8 trades, −71,000)
All **D**. Large single-day losses 6/19. Neither guard fires (dist ~5–6%, positive r5).

### 6976.T (55 trades, +71,999 net)
Losses skew **D** (36); guards catch some (B=10, A=6, C=3). Profitable net despite D losses.

### 4062.T (34 trades, −75,499)
**D=31** dominates; persistent mid-board leak symbol.

---

## Part F — Candidate rules (TOP10 feature gaps)

See `phase454_unsolved_pattern_candidates.csv`.

**Suggested next guards (research):**

1. **mid_board_late_chase** — Board:mid + r15>0.5% + r30>0 + day_high_dist<4.5% (exhaustion entry)
2. **near_day_high_mid_board** — tighten Board:mid when closer to day high than winners
3. **6920/4062 symbol-specific review** — D concentration, not board tertile alone
4. **PM board:mid filter** — afternoon entries over-represented in D losses (6920)

---

## Interpretation

- Phase439+452 **do not fully solve** Board:mid losses; **75% of losses are class D**.
- D pattern ≠ opening_peak / high_drift — it is **mid-board chase near highs with positive short-term momentum**.
- **6920 / 4062** are unresolved; **6976** net positive but D-heavy on losers.

## Outputs

- `results/reports/phase454_board_mid_loss_audit.csv`
- `results/reports/phase454_unsolved_pattern_candidates.csv`
- `results/reports/phase454_board_mid_loss_summary.json`

Run: `python scripts/run_phase454_board_mid_loss_audit.py`
