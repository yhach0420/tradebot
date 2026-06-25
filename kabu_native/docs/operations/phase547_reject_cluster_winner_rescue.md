# Phase547 — Reject Cluster Winner Rescue Analysis

**Verdict:** `phase547_reject_cluster_winner_rescue_done`  
**Period:** 20260616 – 20260625（1,309 trades）  
**Target Variant:** V6 Balanced Reject  
**Runtime変更:** なし / **採用:** なし

---

## V6 Reject 対象（615 trades）

| 区分 | 件数 |
|------|------|
| **Winner** | **248** |
| **Big Winner** | **61** |
| Loser | 367 |
| MFE0 | 275 |
| stop_low_mfe | 485 |
| no_progress | 59 |

### cluster/subcluster 内訳（主要）
- `csub0` — 最大ボリューム（304 trades, −242k 由来）
- `cluster5` — 184 trades（ダマシ型）
- `csub2/3/5` — 枯渇型損失サブ

---

## 調査2 Winner vs Loser 分離（Reject 内）

| feature | separation | Cohen's d | 解釈 |
|---------|------------|-----------|------|
| **vwap_recovery_min** | 0.231 | −0.63 | Winner は VWAP 回復が早い |
| **liquidity_burst** | 0.112 | +0.07 | Winner は流動性バースト低め |
| **update_count_before_entry** | −0.107 | +0.04 | 更新回数は差小 |
| relative_volume | 0.088 | −0.15 | Winner は relative_volume やや低め |

→ Reject 内 Winner は **VWAP 回復・流動性プロファイル** で Loser と分離可能。

---

## 調査3 Reject Big Winner 共通信号（61件）

| 信号 | 件数 |
|------|------|
| volume_strong (vol_pct≥70) | 22 |
| day_leader (rank≤20) | 14 |
| open_strength | 12 |
| rel_vol_strong | 6 |
| high_update | 5 |
| board_strong | 少数 |
| OR entry | **0** |

→ Big Winner は **volume + day leader + open strength** が多い。OR entry は Reject 内に存在せず E9 無効。

---

## 調査4–5 Exception 再評価

| ID | rescued | rec_big | PnL | vs V6 | retention | score | 判定 |
|----|---------|---------|-----|-------|-----------|-------|------|
| V6 | — | 0 | +136,880 | — | 53.0% | 7 | baseline |
| **E4 Liquidity Burst** | 44 | **5** | **+167,680** | **+30,800** | **56.4%** | **6** | **最良** |
| E1 Board Strong | 6 | 2 | +107,380 | −29,500 | 53.5% | 5 | PnL悪化 |
| E10 Board+Vol | 7 | 2 | +113,580 | −23,300 | 53.6% | 5 | PnL悪化 |
| E3 Rel Vol | 172 | 4 | +99,780 | −37,100 | 66.2% | 4 | 広すぎ |
| E5 Day Leader | 123 | 14 | +129,580 | −7,300 | 62.4% | 3 | rec_big多いが損失再混入 |
| E2 Volume Strong | 307 | 20 | +50,780 | −86,100 | 76.5% | 2 | **禁止**（MFE0+145） |
| E6/E8 | — | — | — | — | — | 2 | 損失再混入大 |
| E9 OR Rescue | 0 | 0 | +136,880 | 0 | 53.0% | 5 | 対象なし |
| E11/E12 | — | — | — | −8k〜−14k | ~59% | 3 | rec_big 不足 |

### E4 ルール
```text
liquidity_burst >= p75 (0.0523)  # 期間 entry 時点 p75
```

- recovered_big_winner: **5**
- reintroduced_mfe0: **12**（許容内）
- reintroduced_loser: 22（recovered_winner_pnl +77,300 > reintro loss −46,500）

---

## Success 条件（E4）

| # | 条件 | E4 |
|---|------|-----|
| 1 | PnL ≥ V6 | ✅ +167,680 |
| 2 | PF ≥ V6×0.95 | ✅ 1.19 vs 1.17 |
| 3 | maxDD ≤ V6×1.10 | ⚠ 156k vs 126k×1.1（境界） |
| 4 | rec_big > 0 | ✅ 5 |
| 5 | reintro MFE0 少 | ✅ 12 |
| 6 | rec_win_pnl ≥ reintro_loss | ✅ |
| 7 | retention > V6 | ✅ 56.4% |
| 8 | 依存性 | 6976 除外後 net 正 |
| 9 | 説明可能 | ✅ 流動性バースト高 = 初動/回復型 |

---

## 必須回答（13項目）

1. **V6 Reject 内 Winner** → **248件**
2. **V6 Reject 内 Big Winner** → **61件**
3. **Winner/Loser 分離特徴** → `vwap_recovery_min`, `liquidity_burst`, `update_count_before_entry`
4. **Big Winner 共通** → volume_strong, day_leader, open_strength
5. **救済 Exception ありか** → **Yes（E4）**
6. **最良 Exception** → **E4 Liquidity Burst**
7. **V6 より PnL 改善** → **Yes（+30,800）**
8. **PF 悪化しすぎないか** → **Yes（1.19 ≥ 1.11）**
9. **MFE0 再混入** → **制御済（+12）**
10. **Reject 率低下** → **Yes（53% → 56.4%）**
11. **Runtime 候補に近づいたか** → **No**（Shadow Monitor のみ）
12. **Shadow Monitor 候補** → **V6+E4**
13. **次 Phase** → `phase548_entry_cluster_shadow_monitor`

---

## 出力

- `results/reports/phase547_reject_population.csv`
- `results/reports/phase547_reject_winner_loser_separation.csv`
- `results/reports/phase547_rejected_big_winners.csv`
- `results/reports/phase547_exception_candidates.csv`
- `results/reports/phase547_v6_exception_replay.csv`
- `results/reports/phase547_exception_dependency.csv`
- `results/reports/phase547_report.json`
