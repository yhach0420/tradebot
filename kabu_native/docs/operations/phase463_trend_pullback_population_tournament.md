# Phase463 — Trend/Pullback Population Tournament

Generated: 2026-06-20T16:49:54+09:00  
Period: **20260529..20260619**  
Exit stack: Hard Stop → No Progress → Board Dynamic Trailing  
Baseline: Momentum:low + (Board:mid OR Board:high) + High Drift Guard + Weak Shape Reject

## Population scope

| metric | count |
|---|---:|
| dynamic40 total (raw) | 188,461 |
| actionable (excl. data_stale / suitability / overlap) | 85,624 |
| replay pool (canon + gate/cap rescue inject, full enrich) | 67,967 |
| canonical stream (NP shadow eval_ok) | 958 |
| inject rescue (close_proxy NP shadow) | 67,009 |

Regime Part D labels computed on actionable dynamic40 with light enrich (tick features absent → most land in **Other** or **Pullback-like** only).

---

**Verdict:** `hybrid_candidate`

---

## Tournament leaderboard (PnL)

| rank | variant | group | accepted | PnL | PF | maxDD | stop_rate | Δvs A0 |
|---:|---|---|---:|---:|---:|---:|---:|---:|
| 1 | A3_pullback_vwap_stable | A | 20 | 566,400 | 18.70 | 15,000 | 0.00 | +208,637 |
| 2 | C1_a0_or_b1 | C | 328 | 390,852 | 1.77 | 71,000 | 0.19 | +33,089 |
| 3 | C4_a0_or_best_trend | C | 328 | 390,852 | 1.77 | 71,000 | 0.19 | +33,089 |
| 4 | C5_a2_or_best_trend | C | 328 | 390,852 | 1.77 | 71,000 | 0.19 | +33,089 |
| 5 | A0_baseline_pullback | A | 278 | 357,763 | 1.76 | 71,000 | 0.18 | 0 |
| 6 | A2_pullback_near_high_exception | A | 278 | 357,763 | 1.76 | 71,000 | 0.18 | 0 |
| 7 | C3_a0_or_b3 | C | 274 | 342,863 | 1.72 | 74,400 | 0.17 | −14,900 |
| 8 | C2_a0_or_b2 | C | 274 | 257,063 | 1.51 | 73,600 | 0.17 | −100,700 |
| 9 | A1_strict_pullback | A | 20 | 248,100 | 4.89 | 40,500 | 0.00 | −109,663 |
| 10 | B1_trend_r15_r30 | B | 167 | 71,000 | 1.36 | 88,600 | 0.15 | −286,762 |
| 11 | B3_trend_vwap_stable | B | 20 | 12,700 | 1.06 | 173,600 | 0.00 | −345,063 |
| 12 | B4_trend_composite | B | 0 | 0 | — | 0 | — | −357,763 |
| 13 | B2_trend_high_update | B | 18 | −61,100 | 0.75 | 162,800 | 0.00 | −418,863 |

### Symbol deltas vs A0

| variant | 6976 Δ | 4062 Δ | 3441 | 6492 | 7256 | 7600 |
|---|---:|---:|---|---|---|---|
| A0 | 0 | 0 | ✗ | ✗ | ✗ | ✗ |
| A3 | +92,000 | +166,498 | ✗ | ✗ | ✗ | ✗ |
| C1 | −0.4 | 0 | ✗ | ✗ | ✗ | ✗ |
| B1 | −270,001 | +32,498 | ✗ | ✗ | ✗ | ✗ |

---

## Mandatory answers

1. **最良 Pullback variant:** `A3_pullback_vwap_stable` (A0 + vwap_above_ratio ≥ 0.5) — PnL 566,400 yen, **20 trades only**, top_symbol_share 49.7% → **過集中リスク大**
2. **最良 Trend variant:** `B1_trend_r15_r30` — PnL 71,000 yen (A0比 −286,762)
3. **最良 Hybrid variant:** `C1_a0_or_b1` — PnL 390,852 yen (A0比 +33,089)
4. **Trend-only は独立利益源か:** **No** — B1単独は A0 を大幅に下回る。Trend-only で正の独立エッジなし
5. **Pullback-only は主力か:** **Yes** — Pullback系 (A0/A3) が Trend/Hybrid の実質PnL源。Hybrid の上乗せは +33k 程度
6. **Hybrid は Pullback を上回るか:** **A0 baseline には上回る** (+33k)。ただし最良PnLは A3 Pullback
7. **3441/6492/7256/7600 capture:** **なし** (全 variant `captured_*=False`)
8. **6976 を壊さない variant:** A3, C1, C4, C5, A0, A2, C3 (Δ6976 ≥ −5,000)
9. **4062 を改善する variant:** A3, A1, B1, B3, B4, B2 (Δ4062 > 0)
10. **PnL 順位:** A3 > C1=C4=C5 > A0=A2 > C3 > C2 > A1 > B1 > B3 > B4 > B2
11. **PF 順位:** A3 > A1 > C1=C4=C5 > A0=A2 > C3 > C2 > B1 > B3 > B2 > B4
12. **maxDD 順位 (昇順=良):** B4 > A3 > A1 > C1=C4=C5 > A0=A2 > C2 > C3 > B1 > B2 > B3
13. **過学習リスク:** **True** — A3/A1/B系は accepted≤20、top_day/top_symbol > 0.5。inject の close_proxy shadow も replay PnL にノイズ
14. **Runtime 候補:** **C1_a0_or_b1** (Hybrid) — A0維持 + B1 OR で +33k、6976安全。A3 は shadow 再検証必須
15. **次アクション:**
    - C1 (A0 OR B1) の paper shadow（Phase462 dual 系の延長）
    - A2 near-high exception は本 replay pool では A0 と完全一致 → 6/19 inject 限定再監査 (Phase461 知見)
    - Trend 系 (B2–B4) は却下。regime split 不要
    - 3441/6492/7256/7600 は Entry Gate 問題が残存 (Phase460 継続)

---

## Part D — Regime label (actionable population, light features)

| regime | count | accepted (A0) | would_pnl | replay_pnl | PF |
|---|---:|---:|---:|---:|---:|
| Pullback-like | 51,790 | 42,604 | −88.4M | +186,200 | 3.53 |
| Other | 33,834 | 0 | +328.5M | 0 | — |

Trend-like / High-update-like / VWAP-stable-like は tick enrich 不足のため light population では分類不可（→ Other に吸収）。

---

## Method notes

- Population: dynamic40 ENTRY 候補 (accepted/rejected/cap/gate) from `small_paper_events.csv`
- Inject rescue: near_high guard, high_drift, pullback_misread, momentum_low, cap blocks
- NP shadow: canonical 958 eval_ok; inject 67,009 close_proxy EOD fill
- No Runtime / YAML / Entry / Exit / Order / Discord changes

## Outputs

- `results/reports/phase463_trend_pullback_population_tournament.csv`
- `results/reports/phase463_regime_label_analysis.csv`
- `results/reports/phase463_symbol_capture_analysis.csv`
- `results/reports/phase463_summary.json`
