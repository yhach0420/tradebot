# Phase459 — Winner Pattern Audit

Generated: 2026-06-20  
Period: 20260529..20260619  
Baseline: Runtime (Momentum:low + Board mid|high + HD + WS + NP exit)

Research only — no Runtime changes.

**Verdict:** `winner_pattern_found`

---

## Mandatory answers

| # | Answer |
|---|--------|
| 1 | **勝ち共通TOP5 (actionable):** r30, vwap_above_ratio, high_update_count_30m, high_update_age, high_update_count_session |
| 2 | **分離TOP5:** 同上 (+ size proxy: price/trading_value) |
| 3 | **最強パターン分類:** **E_other** (293k) — A uptrend (237k), B VWAP stable (210k) も同等に強い |
| 4 | **6976勝ちパターン:** **B_vwap_stable_above** |
| 5 | **4062勝ちパターン:** **C_board_strength** |
| 6 | **Board:highは勝ちパターンか:** **No** (<15% of winners) |
| 7 | **VWAP安定は勝ちパターンか:** **Yes** (61 wins, +210k) |
| 8 | **高値更新継続は勝ちパターンか:** **Yes** (75 wins, +237k) |
| 9 | **rank bonus候補:** B_high_update_bonus (best label; **ΔPnL=0**) |
| 10 | **rank bonusでPnL改善:** **No** — 全variant ΔPnL=0 |
| 11 | **6/19取り逃し原因:** **gate_blocked** — candidate有り、ENTRY gate不通過 |
| 12 | **Runtime候補:** **No** |
| 13 | **次アクション:** ENTRY gate緩和/別枠でuptrend捕捉; rank bonusは効果なし |

---

## Part B — 勝ち vs 負け

| Feature | Cohen's d | 解釈 |
|---------|-----------|------|
| r30 | +0.23 | 勝ちは30分モメンタムやや高い |
| high_update_age | +0.15 | 勝ちは高値更新から時間経過 |
| vwap_above_ratio | −0.20 | 弱い逆相関 |

分離力 **弱〜中** (|d|≤0.24)。

---

## Part C — 勝ちパターン分類

| Pattern | Count | Total PnL |
|---------|-------|-----------|
| E_other | 71 | +293,100 |
| A_uptrend_continuation | 75 | +237,001 |
| B_vwap_stable_above | 61 | +209,701 |
| C_board_strength | 69 | +173,550 |

---

## Part E — Rank bonus

全variant **ΔPnL=0**。scan bucket reorder では CAP replay 不変。

---

## Part F — 6/19 取り逃し

3441/6492/7256/6466/7600 — 全て **gate_blocked** (rank/CAPではない)。

---

## Outputs

Run: `python scripts/run_phase459_winner_pattern_audit.py`
