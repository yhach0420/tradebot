# Phase405 — Time-Based MFE / STOP Boundary Inference

Generated: 2026-07-12T02:11:50+09:00
Period: 20260529 – 20260615
Trades analyzed: 755

## Mandatory answers

### 10m: MFE exit < 1.0% | STOP < -1.0% | trail activate 0.4%
- MFE rule delta: ¥45090.57 | STOP rule delta: ¥3398.98

### 15m: MFE exit < 1.0% | STOP < -1.0% | trail activate 0.4%
- MFE rule delta: ¥48851.81 | STOP rule delta: ¥600.01

### 20m: MFE exit < 1.0% | STOP < -0.8% | trail activate 0.3%
- MFE rule delta: ¥50301.55 | STOP rule delta: ¥5199.05

### 30m: MFE exit < 0.6% | STOP < -0.8% | trail activate 0.3%
- MFE rule delta: ¥1520.25 | STOP rule delta: ¥2529.75

**Most effective time bucket:** 5min (combined estimate ¥108900.8)

## Inferred rules

- At 5min: if max_mfe_so_far < 1.0% → exit (loser_rate_below=0.5049)
- At 5min: if current_pnl < -0.6% → stop exit
- At 5min: if peak>=0.5% and pnl<=peak*0.5 → trail exit
- At 10min: if max_mfe_so_far < 1.0% → exit (loser_rate_below=0.5621)
- At 10min: if current_pnl < -1.0% → stop exit
- At 10min: if peak>=0.4% and pnl<=peak*0.5 → trail exit
- At 15min: if max_mfe_so_far < 1.0% → exit (loser_rate_below=0.5312)
- At 15min: if current_pnl < -1.0% → stop exit
- At 15min: if peak>=0.4% and pnl<=peak*0.5 → trail exit
- At 20min: if max_mfe_so_far < 1.0% → exit (loser_rate_below=0.5556)
- At 20min: if current_pnl < -0.8% → stop exit
- At 20min: if peak>=0.3% and pnl<=peak*0.5 → trail exit
- At 30min: if max_mfe_so_far < 0.6% → exit (loser_rate_below=0.6207)
- At 30min: if current_pnl < -0.8% → stop exit
- At 30min: if peak>=0.3% and pnl<=peak*0.5 → trail exit
- At 45min: if max_mfe_so_far < 0.6% → exit (loser_rate_below=0.6429)
- At 45min: if current_pnl < -0.2% → stop exit
- At 45min: if peak>=0.3% and pnl<=peak*0.5 → trail exit
- At 60min: if max_mfe_so_far < 0.6% → exit (loser_rate_below=0.75)
- At 60min: if current_pnl < -0.2% → stop exit
- At 60min: if peak>=0.3% and pnl<=peak*0.5 → trail exit

## Policy table

| bucket | MFE< | STOP< | trail | mfe_Δ | stop_Δ |
|--------|------|-------|-------|-------|--------|
| 5m | 1.0 | -0.6 | 0.5 | ¥95001.21 | ¥13899.59 |
| 10m | 1.0 | -1.0 | 0.4 | ¥45090.57 | ¥3398.98 |
| 15m | 1.0 | -1.0 | 0.4 | ¥48851.81 | ¥600.01 |
| 20m | 1.0 | -0.8 | 0.3 | ¥50301.55 | ¥5199.05 |
| 30m | 0.6 | -0.8 | 0.3 | ¥1520.25 | ¥2529.75 |
| 45m | 0.6 | -0.2 | 0.3 | ¥18039.65 | ¥3499.85 |
| 60m | 0.6 | -0.2 | 0.3 | ¥7199.91 | ¥6400.0 |

- shadow / research only
