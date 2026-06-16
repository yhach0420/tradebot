# Phase401 — Long Hold Loser Forensic Audit

Generated: 2026-06-15T23:41:56+09:00

## 長時間負け27件は、途中で崩れた・戻された（dead_from_start=9/27、MFE<0.2%=9件）が主因。

Verdict: **PASS**

Cohort: **27** trades (hold ≥ p90=1290.6s, pnl<0)
Total PnL: **¥-118740.04**

## 必須回答

1. MFE < 0.2%: **9** / 27
2. MFE < 0.5%: **20** / 27
3. 最初から上昇しなかった（dead_from_start）: **33.3%**
4. 一度は利益が出た（max_mfe≥0.2%）: **66.7%**
5. 時間EXITが救えた推定: **26** 件
6. Entry改善が効く推定: **11** 件
7. 推奨: **A_time_exit** (scores: {'A_time_exit': 26, 'B_entry_guard': 11, 'C_board_exit': 8, 'D_no_action': 0})

### MFEカテゴリ

- A (MFE<0.2%): 9
- B (0.2–0.5%): 11
- C (0.5–1.0%): 7
- D (≥1.0%): 0

### EXIT理由別

| bucket | count | total_pnl |
|--------|-------|-----------|
| overlap_replaced | 1 | ¥-5500.45 |
| session_close | 6 | ¥-11000.02 |
| stop_hit | 20 | ¥-102239.57 |

### Dynamic40 / Core10

- dynamic40: 2
- core10: 2
- unknown: 23

### 共通特徴 Top

| feature | count | share | total_pnl |
|---------|-------|-------|-----------|
| time_exit_would_save | 26 | 0.963 | ¥-116240.25 |
| mfe_lt_0p5 | 20 | 0.7407 | ¥-94940.13 |
| stop_hit_exit | 20 | 0.7407 | ¥-102239.57 |
| rose_then_faded | 15 | 0.5556 | ¥-64298.87 |
| entry_guard_would_help | 11 | 0.4074 | ¥-46540.46 |
| dead_from_start | 9 | 0.3333 | ¥-41040.5 |
| mfe_lt_0p2 | 9 | 0.3333 | ¥-41040.5 |
| vwap_below_entry | 7 | 0.2593 | ¥-18139.74 |
| high_update_none | 6 | 0.2222 | ¥-29800.58 |
| session_close_exit | 6 | 0.2222 | ¥-11000.02 |
| core10 | 2 | 0.0741 | ¥-4899.92 |
| dynamic40 | 2 | 0.0741 | ¥-7499.89 |

### CAP占有時間 Top10（本次コホート）

| symbol | hold_sec | pnl | mfe | trajectory | exit |
|--------|----------|-----|-----|------------|------|
| 4078.T | 7013.0 | ¥-8000.05 | 0.565% | rose_then_faded | stop_hit |
| 3444.T | 6299.0 | ¥-1600.01 | 0.5479% | rose_then_faded | stop_hit |
| 4078.T | 5976.0 | ¥-2499.79 | 0.5482% | rose_then_faded | session_close |
| 6996.T | 5196.0 | ¥-5000.14 | 0.5006% | rose_then_faded | stop_hit |
| 4667.T | 3672.0 | ¥-2400.02 | 0.4608% | flat | stop_hit |
| 4022.T | 3567.0 | ¥-2599.98 | 0.1916% | dead_from_start | stop_hit |
| 3915.T | 3273.0 | ¥-3099.94 | 0.3992% | rose_then_faded | stop_hit |
| 6055.T | 3149.0 | ¥-2599.91 | 0.5731% | rose_then_faded | stop_hit |
| 4588.T | 2553.0 | ¥-1499.99 | 0.5488% | rose_then_faded | session_close |
| 5802.T | 2431.0 | ¥-5500.45 | 0.4007% | flat | overlap_replaced |

## 推奨解釈

- **A 時間EXIT**: rose_then_faded + time_exit_would_save が多い場合
- **B Entry Guard**: MFE<0.2% + dead_from_start が過半の場合
- **C Board Exit**: board_low + VWAP下が集中する場合
- **D 何もしない**: 分散して主因が特定できない場合

## 成果物

- `results/reports/phase401_long_hold_loser_forensic.csv`
- `results/reports/phase401_long_hold_loser_clusters.csv`
- `results/reports/phase401_long_hold_loser_summary.json`
