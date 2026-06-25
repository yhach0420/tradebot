# Phase511 — Entry / Exit Cross Battle

**Verdict:** `phase511_entry_exit_cross_battle_done`  
**Mode:** Research only — no PBv2 modification, no adoption.

---

## Objective

Decompose why PBv2 wins: Entry strength, Exit strength, or both.  
Test whether classical entries are valid but classical exits are the problem.

| Item | Value |
|------|-------|
| Period | 20260529 – 20260622 |
| Universe / CAP | Same as Phase510 |
| Runner | `python scripts/run_phase511_entry_exit_cross_battle.py` |

---

## Cross matrix results

| Combo | Entry | Exit | PnL | PF | maxDD | Trades | Win% | Stability |
|-------|-------|------|-----|-----|-------|--------|------|-----------|
| **CROSS_BASELINE** | E_PB | X_PB | **+214,960** | **1.35** | **118,600** | 440 | 58.4% | **0.69** |
| CROSS_PB_TREND_EXIT | E_PB | X_TREND | +58,580 | 1.04 | 122,500 | 2,122 | 35.7% | 0.67 |
| CROSS_PB_MOMENTUM_EXIT | E_PB | X_MOMENTUM | +15,130 | 1.01 | 187,200 | 1,984 | 40.1% | 0.50 |
| CROSS_MOMENTUM_PB_EXIT | E_MOMENTUM | X_PB | +79,890 | 1.09 | 187,200 | 655 | 51.0% | 0.46 |
| CROSS_TREND_PB_EXIT | E_TREND | X_PB | -422,630 | 0.60 | 479,530 | 681 | 47.9% | 0.31 |
| CROSS_TREND_TREND | E_TREND | X_TREND | -284,970 | 0.84 | 354,420 | 2,630 | 24.9% | 0.38 |
| CROSS_MOMENTUM_MOMENTUM | E_MOMENTUM | X_MOMENTUM | -193,680 | 0.90 | 252,300 | 2,107 | 29.1% | 0.46 |

---

## Mandatory answers

| # | Question | Answer |
|---|----------|--------|
| 1 | PBv2 Entry は優秀か | **Yes** — E_PB alone preserves +58k~+215k vs classical entries with X_PB |
| 2 | PBv2 Exit は優秀か | **Yes** — X_PB on E_PB (+215k) vs E_PB+X_TREND (+59k) / +X_MOM (+15k) |
| 3 | Momentum Entry + PB Exit 成立するか | **Partially** — +79,890, PF 1.09 (positive but below baseline) |
| 4 | Trend Entry + PB Exit 成立するか | **No** — -422,630, PF 0.60 |
| 5 | PBv2優位性の源泉 | **Both (Entry + Exit)** |
| 6 | 古典EntryでPBv2超え | **None** |
| 7 | 古典ExitでPBv2超え | **None** |
| 8 | 次に深掘り | **CROSS_MOMENTUM_PB_EXIT** — best non-baseline combo |

---

## Decomposition analysis

### Entry axis (hold Exit = X_PB)

| Entry | PnL with X_PB |
|-------|---------------|
| E_PB (baseline) | **+214,960** |
| E_MOMENTUM | +79,890 |
| E_TREND | -422,630 |

→ PBv2 Entry is strongly superior. Momentum entry retains partial edge with PB exit; Trend entry does not.

### Exit axis (hold Entry = E_PB)

| Exit | PnL with E_PB |
|------|---------------|
| X_PB (baseline) | **+214,960** |
| X_TREND | +58,580 |
| X_MOMENTUM | +15,130 |

→ PBv2 Exit adds ~+156k~+200k vs classical exits on same PBv2 entries. Classical exits are not "bad entries" problem — they cut winners early (low session_end rate ~0% vs 4.3% baseline).

### Classical entry + classical exit (Phase510 equivalent)

| Combo | PnL |
|-------|-----|
| E_TREND + X_TREND | -284,970 |
| E_MOMENTUM + X_MOMENTUM | -193,680 |

→ Classical exits destroy value even without PBv2 entry. **Exit is the dominant failure mode for classical systems.**

### Key insight

Phase507/508 T15/T13 headline PnL used **E1 (session_end-heavy) exits**, not Phase510/511 ATR/RSI composite exits. PBv2 advantage is **both** board-gated entry selection **and** board-dynamic trailing exit.

---

## Exit reason profile (baseline vs crosses)

| Combo | hard_stop% | session_end% |
|-------|------------|--------------|
| CROSS_BASELINE | 13.2% | 4.3% |
| CROSS_PB_TREND_EXIT | 1.2% | 0.05% |
| CROSS_MOMENTUM_PB_EXIT | 37.3% | 9.8% |

Classical exits on PBv2 entries rarely reach session_end (winners cut early). PB exit on momentum entries has higher stop rate but still profitable.

---

## Outputs

- `results/reports/phase511_cross_battle.csv`
- `results/reports/phase511_cross_battle_daily.csv`
- `results/reports/phase511_cross_battle_trades.csv`
- `results/reports/phase511_cross_battle_report.json`
