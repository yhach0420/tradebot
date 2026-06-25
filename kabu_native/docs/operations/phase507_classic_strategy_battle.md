# Phase507 — Classical Technical Strategy Battle

**Verdict:** `classic_strategy_battle_done`  
**Mode:** Research only — no Runtime adoption, no YAML changes.

---

## Objective

Compare **BASELINE_RUNTIME** (PBv2 + Phase503 classic_late_chase_rsi_guard + Board Dynamic Trailing exit) against **126 classical technical strategies** on the same universe, period, and CAP=5 conditions.

| Item | Value |
|------|-------|
| Universe | core10-dynamic40-price-risk-filter-shadow (~50 symbols from replay pool) |
| Period | 20260529 – 20260622 |
| CAP | 5 (leverage 2x, fixed_stop_1p2) |
| Classical strategies | 18 entry rules × 7 exit rules (E1,E2,E3,E4,E6,E7,E12) |
| Parallel | `--parallel --max-workers 4`, jobs = strategy × day |

---

## BASELINE_RUNTIME

- Entry: PBv2 (Momentum:low + Board:mid/high) + `classic_late_chase_rsi_over80` guard
- Exit: Hard Stop → No Progress → Board Dynamic Trailing (runtime shadows)
- Sim: `_replay_with_extra_block` (Phase504 production stack)

---

## Classical indicators logged

RSI14, MACD/signal/histogram, SMA5/20/25, EMA5/20, VWAP, Bollinger Bands, ADX/+DI/-DI, ATR14, Stochastic %K/%D, Williams %R, CCI20, ROC10, Momentum10, MFI14, OBV, Donchian high/low/mid, Ichimoku tenkan/kijun/cloud position.

Module: `src/research/phase507_classic_indicators.py`

---

## Outputs

| File | Description |
|------|-------------|
| `results/reports/strategy_battle_summary.csv` | Per-strategy metrics + baseline diffs + ranks |
| `results/reports/strategy_battle_daily.csv` | Daily PnL by strategy |
| `results/reports/strategy_battle_trades.csv` | Trade log with indicator snapshot at entry |
| `results/reports/strategy_battle_report.json` | Full JSON report + mandatory answers |
| `docs/operations/top5_strategy_review.md` | Top-5 PnL review |

---

## Run

```bash
cd kabu_native
set PYTHONPATH=src
python scripts/run_phase507_classic_strategy_battle.py --parallel --max-workers 4
```

---

## Rankings (in summary CSV)

1. `rank_pf` — profit factor
2. `rank_pnl` — total PnL
3. `rank_dd` — max drawdown (lower is better)
4. `rank_stability` — daily_stability_score
5. `rank_baseline_diff` — baseline_diff_pnl

---

## Mandatory answers (latest run)

See `strategy_battle_report.json` → `mandatory_answers`. Summary:

- **Beats baseline (any metric):** Yes (PnL and PF on subset)
- **PnL beaters:** C_T13_E2, C_T15_E1, C_T15_E2
- **PF beaters:** same three
- **DD beaters:** None (classical strategies had larger drawdowns)
- **RSI effective:** Yes — T15 (Stoch %K>%D + RSI>50) best classical PnL
- **VWAP effective:** Mixed — T13+VWAP combo in top PnL beaters; standalone T4 negative
- **ADX effective:** T13 (EMA+VWAP+ADX) in PnL beaters
- **MACD effective:** No MACD-entry strategy beat baseline PnL
- **Boardless can win:** Yes on raw PnL/PF (C_T15_E1), but with higher maxDD and lower daily stability vs baseline

**No adoption** — research comparison only.

---

## Files

- `src/research/phase507_classic_indicators.py`
- `src/research/phase507_classic_strategy_battle.py`
- `scripts/run_phase507_classic_strategy_battle.py`
