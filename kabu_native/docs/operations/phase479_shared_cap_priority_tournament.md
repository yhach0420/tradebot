# Phase479 — Shared CAP Priority Tournament

**Verdict:** `pb_not_needed`
**Period:** 20260529–20260619

## 必須回答

| # | 項目 | 結果 |
|---|------|------|
| 1 | 最良priority variant | **A (PBv2 only CAP5 (baseline))** |
| 2 | A比PnL | **0.0** |
| 3 | PF | **1.9886** |
| 4 | maxDD | **71000.0** |
| 5 | PB採用件数 | **0** |
| 6 | PB寄与 | **0.0** |
| 7 | PBv2勝ち置換減 | **None** |
| 8 | PB補欠独立価値 | **True** |
| 9 | 6976影響 | {'variant': 'A', 'symbol': '6976', 'pbv2_pnl_yen': 221001.28, 'pb_pnl_yen': 0.0, 'total_symbol_pnl_yen': 221001.28, 'pbv2_trades': 15, 'pb_trades': 0, 'pb_session_hold_trades': 0, 'captured': True} |
| 10 | 4062影響 | {'variant': 'A', 'symbol': '4062', 'pbv2_pnl_yen': 9001.55, 'pb_pnl_yen': 0.0, 'total_symbol_pnl_yen': 9001.55, 'pbv2_trades': 17, 'pb_trades': 0, 'pb_session_hold_trades': 0, 'captured': True} |
| 11 | 3441等捕捉 | {'3441': False, '6492': False, '7256': False, '7600': False} |
| 12 | Runtime候補 | **False** |
| 13 | Shadow候補 | **None** |
| 14 | 次アクション | Verdict: pb_not_needed; Keep PBv2-only; PB backup adds no value under any priority rule; Best A PnL 402962.82 vs A 402962.82 vs shared OR 75462.82 |

## Tournament

| Var | PnL | PBv2 | PB | PF | maxDD | acc | PB acc | Δ vs A |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 402962.82 | 402962.82 | 0.0 | 1.9886 | 71000.0 | 256 | 0 | 0.0 |
| B | 75462.82 | 314462.82 | -239000.0 | 1.1066 | 132240.28 | 311 | 61 | -327500.0 |
| C | 75462.82 | 314462.82 | -239000.0 | 1.1066 | 132240.28 | 311 | 61 | -327500.0 |
| D | 75562.82 | 314462.82 | -238900.0 | 1.1068 | 132140.28 | 310 | 60 | -327400.0 |
| E | 75462.82 | 314462.82 | -239000.0 | 1.1066 | 132240.28 | 311 | 61 | -327500.0 |
| F | 116262.82 | 326562.82 | -210300.0 | 1.1744 | 133440.28 | 300 | 50 | -286700.0 |

**判定:** `pb_not_needed`