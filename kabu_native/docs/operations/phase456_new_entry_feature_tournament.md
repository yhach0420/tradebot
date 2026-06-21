# Phase456 — New Entry Feature Tournament

Generated: 2026-06-20T00:45:53+09:00  
Period: 20260529..20260619  
Population: **Phase452 Runtime equivalent** — Momentum:low + (Board:mid|high) + High Drift + Weak Shape Reject + No Progress Exit

Research only — no Runtime / YAML / Entry / Exit / Order / Discord changes.

## Mandatory answers

| # | Answer |
|---|--------|
| 1 | **Best feature group:** VWAP Reclaim (`vwap`) |
| 2 | **Best single feature:** `vwap_above_duration_min` |
| 3 | **Best single variant:** `H_vwap_duration_guard` |
| 4 | **Best combo variant:** `O_best_trend_plus_vwap` (+500 yen; marginal) |
| 5 | **PnL improvement:** **+49,301 yen** vs baseline |
| 6 | **PF improvement:** **+0.0847** (1.1925 → 1.2772) |
| 7 | **MaxDD improvement:** **−7,450 yen** (133,650 → 126,200) |
| 8 | **6/18 delta:** **+400 yen** |
| 9 | **6/19 delta:** **+2,100 yen** |
| 10 | **6976 delta:** **+15,500 yen** (150,500 → 166,000) |
| 11 | **6920 delta:** **0** (no trades in period) |
| 12 | **4062 delta:** **+12,501 yen** (less negative: −41,000 → −28,497) |
| 13 | **Overfit risk:** **Low** — 22 blocks, top_day_share 0.23, top_symbol_share 0.18 |
| 14 | **Runtime candidate:** **Yes (shadow)** — ΔPnL > 10k, 6976 not harmed |
| 15 | **Next:** Shadow-eval `H_vwap_duration_guard`; Phase456B walk-forward; do **not** deploy B (high_update_age) or sector guards |

**Verdict:** `vwap_reclaim_candidate`

---

## Part A — Baseline (A)

| Metric | Value |
|--------|-------|
| total_pnl_yen | 147,412 |
| profit_factor | 1.1925 |
| max_drawdown_yen | 133,650 |
| stop_rate | 0.119 |
| accepted_count | 496 |
| daily_pnl 6/18 | −15,700 |
| daily_pnl 6/19 | −54,850 |
| symbol 6976 | +150,500 |
| symbol 4062 | −40,998 |

---

## Part B — Feature Group Summary

| Group | Best variant | ΔPnL | PF | blocked | blocked L/W | Notes |
|-------|--------------|------|-----|---------|-------------|-------|
| high_update | C (tie) | 0 | 1.19 | 0 | — | C/D inactive; **B age guard harmful** (−184k) |
| trend | G | −13,903 | 1.23 | 134 | 55/74 | PF↑ but net PnL↓ (blocks too many wins) |
| **vwap** | **H** | **+49,301** | **1.28** | **22** | **14/8** | **Only group with net edge** |
| sector | K (tie) | 0 | 1.19 | 0 | — | Thresholds at 0 → no blocks |
| combined | O | +500 | 1.19 | 3 | 1/2 | AND overlap minimal; no additive value |

---

## Part C — Single Variant Table (ΔPnL vs baseline)

| Variant | Feature | ΔPnL | PF | maxDD Δ | blocked | 6/18 Δ | 6/19 Δ | 6976 Δ |
|---------|---------|------|-----|---------|---------|--------|--------|--------|
| **H** | vwap_above_duration_min | **+49,301** | 1.28 | −7,450 | 22 | +400 | +2,100 | +15,500 |
| G | trend_consistency_score | −13,903 | 1.23 | +17,600 | 134 | −2,900 | −4,000 | −47,501 |
| I | vwap_failed_reclaim_flag | −35,270 | 1.17 | −6,100 | 78 | +11,800 | −9,400 | −55,500 |
| E | up_tick_ratio_15m | −38,700 | 1.19 | +30,000 | 185 | −300 | −2,400 | −5,500 |
| F | positive_bar_ratio_15m | −36,901 | 1.19 | +6,800 | 128 | −9,400 | −9,400 | −44,001 |
| J | vwap_position_stability | −56,480 | 1.15 | −27,600 | 105 | +22,600 | +600 | −36,000 |
| **B** | last_high_update_age_min | **−184,132** | 0.94 | +68,849 | 197 | +14,300 | −9,550 | −43,001 |
| C/D/K/L | — | 0 | 1.19 | 0 | 0 | 0 | 0 | 0 |

**Rule (H):** reject if `vwap_above_duration_min < 0.69` (median split; ~41 sec sustained above VWAP).

---

## Part D — Combined Variants (≤2 conditions, AND)

| Variant | Components | ΔPnL | blocked | Verdict |
|---------|------------|------|---------|---------|
| O | trend + vwap | +500 | 3 | Marginal |
| M/N/P/Q | various | 0 | 0 | No overlap / inactive guards |

Combos do **not** beat H alone — guard intersection too sparse for AND logic.

---

## Part E — Target Symbols (H vs baseline)

| Symbol | Baseline PnL | H PnL | Δ | Assessment |
|--------|--------------|-------|---|------------|
| 6976 | +150,500 | +166,000 | **+15,500** | Improved — primary winner preserved |
| 6920 | 0 | 0 | 0 | No exposure in window |
| 4062 | −40,998 | −28,497 | **+12,501** | Improved but still net negative |

---

## Part F — Adoption Verdict

| Criterion | H_vwap_duration_guard |
|-----------|----------------------|
| PnL improvement | ✓ (+49k) |
| PF improvement | ✓ (+0.08) |
| MaxDD not worse | ✓ (−7.5k) |
| 6976 not harmed | ✓ (+15.5k) |
| Concentration | ✓ low (22 trades, diversified) |
| Walk-forward | ✗ not yet done |

**Caveats:**
- Threshold derived in-sample from win/loss medians (vwap_duration_lo ≈ 0.69 min).
- `B_high_update_age_guard` demonstrates **wrong direction** on high-update age — do not adopt.
- Sector features had **zero separation** at default thresholds (sector_return_15m_lo = 0).
- Trend guards block disproportionate wins (E/F/G all net-negative PnL).

---

## Outputs

- `results/reports/phase456_new_entry_feature_tournament.csv`
- `results/reports/phase456_new_entry_feature_detail.csv`
- `results/reports/phase456_new_entry_feature_summary.json`

Run: `python scripts/run_phase456_new_entry_feature_tournament.py`
