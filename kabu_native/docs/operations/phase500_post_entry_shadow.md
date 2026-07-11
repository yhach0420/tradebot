# Phase500 — Post Entry Forward Shadow

**Verdict:** `forward_shadow_started`
**Data source:** phase499_replay_bootstrap
**Forward days collected:** 0 / 10

## 必須回答

| # | 回答 |
|---|------|
| 1 score>=3件数 | **0** |
| 2 score>=3 pnl | **0** |
| 3 score>=4件数 | **0** |
| 4 score>=4 pnl | **0** |
| 5 stop_hit一致率 | **0.0** |
| 6 no_progress一致率 | **0.0** |
| 7 6976影響 | **0** |
| 8 6/22影響 | **0** |
| 9 Runtime候補 | **False** |
| 10 次アクション | Continue forward shadow collection; evaluate after 10 trading days |

## 成果物

- `results/reports/phase500_post_entry_forward_shadow_trades.csv`
- `results/reports/phase500_post_entry_shadow_summary.json`

## 実行

```powershell
cd kabu_native
$env:PYTHONPATH="src"
python scripts/run_phase500_post_entry_shadow.py
```

**注意:** 研究専用。Entry / Exit / Gate には一切使わない。
