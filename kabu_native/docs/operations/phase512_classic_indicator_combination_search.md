# Phase512 — Classic Indicator Combination Search

**Verdict:** `phase512_classic_indicator_combination_search_done`  
**Mode:** Research only — no adoption, no PBv2 synthesis.

| Item | Value |
|------|-------|
| Period | 20260529 – 20260622 |
| Strategies | 300 classical (75 × 4 families) + BASELINE_RUNTIME |
| Runner | `python scripts/run_phase512_classic_indicator_combination_search.py` |

---

## Top classical vs BASELINE

| Rank | Strategy | Family | PnL | PF | maxDD | Entry | Exit |
|------|----------|--------|-----|-----|-------|-------|------|
| — | **BASELINE_RUNTIME** | — | **214,960** | 1.35 | 118,600 | PBv2 | PBv2 |
| 1 | P512_MC_M_E08_M_X05 | momentum | **503,010** | 1.88 | 176,610 | rsi>50+stoch K>D | session_end |
| 2 | P512_BO_B_E10_B_X03 | breakout | 448,860 | 2.12 | — | donchian+day_high | vwap_break |
| 3 | P512_TF_T_E01_T_X03 | trend | 399,410 | 1.53 | 226,900 | ema20+vwap+adx20 | vwap_break |

Best combo **M_E08 + session_end_only** = Phase507 **T15 + E1** (rsi>50, Stoch K>D, hold to session).

---

## Mandatory answers (14)

| # | Question | Answer |
|---|----------|--------|
| 1 | PBv2超え古典単独ありか | **Yes（見かけ上）** — 複数戦略がPnL/PFで超過 |
| 2 | PnLでPBv2超え | **Yes** — 10+ strategies (best +288k vs baseline) |
| 3 | PFでPBv2超え | **Yes** |
| 4 | DDでPBv2超え | **Yes** — 主にbreakout/session_end系 |
| 5 | 日別安定性でPBv2超え | **Yes** — 少数のbreakout戦略のみ |
| 6 | 最良流派 | **momentum_continuation** |
| 7 | 最良ENTRY | **rsi_gt_50 + stoch_k_gt_d** |
| 8 | 最良EXIT | **session_end_only** |
| 9 | session_end保有有効か | **Yes** — session_end_only exit群の中央PnL > 全体 |
| 10 | ATR trailing有効か | **No** |
| 11 | VWAP exit有効か | **Yes** — vwap_break exit群の中央PnL > 全体 |
| 12 | RSI/Stoch exit有効か | **No** |
| 13 | 古典単独がPBv2に対抗可能か | **Headline Yes / Robust No** — 過学習監査で集中度高 |
| 14 | 次に深掘り流派 | **momentum_continuation** |

---

## Overfitting audit (top strategy — do NOT adopt)

**P512_MC_M_E08_M_X05** (+503,010):

| Check | Value |
|-------|-------|
| Top-10 trade profit share | **71.1%** |
| Top-1 symbol excluded PnL | +94,510 (81% from one symbol) |
| Top-3 days excluded PnL | **-102,610** |
| single_symbol_dependency | **true** |
| single_day_dependency | **true** |

→ 即採用禁止。Phase508と同一の脆弱性パターン。

---

## Family Top-5 PnL leaders

| Family | Best | PnL |
|--------|------|-----|
| momentum_continuation | M_E08 + session_end | 503,010 |
| breakout | B_E10 + vwap_break | 448,860 |
| trend_following | T_E01 + vwap_break | 399,410 |
| pullback_recovery | P_E03 + vwap_break | 277,900 |

---

## Key findings

1. **session_end_only** exit dominates top PnL (Phase507 E1再現).
2. **ATR trailing** strategies uniformly lose — median below grid average.
3. **VWAP break** exit works for trend/breakout families but not momentum top.
4. **Breakout** achieves high PF but extreme concentration and worse stability than baseline.
5. **BASELINE** still leads on **win_rate (58%)** and **daily_stability (0.69)**.

---

## Outputs

- `results/reports/phase512_classic_combo_summary.csv`
- `results/reports/phase512_classic_combo_daily.csv`
- `results/reports/phase512_classic_combo_trades.csv`
- `results/reports/phase512_classic_combo_report.json`
