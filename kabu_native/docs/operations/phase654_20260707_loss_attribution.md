# Phase654: 20260707 Loss Attribution After Shadow

Research-only audit decomposing 7/7 AM/PM losses into Flat-band / Rise5 shadow coverage
and residual loss patterns.

## Scope

- **Day:** `20260707`
- **Sessions:** `live_session_081844` (AM), `live_session_122539` (PM)
- **Inputs:** session summaries (`small_paper_summary_{am,pm}.json` or legacy `small_paper_{am,pm}_summary.json`), events, `structural_trades.csv`
- **No runtime / YAML / ENTRY / EXIT changes**

## Run

```bash
cd kabu_native
python scripts/run_phase654_20260707_loss_attribution.py
```

## Outputs

`results/reports/phase654_20260707_loss_attribution/`

| File | Content |
|------|---------|
| `phase654_report.json` | Verdict + mandatory answers |
| `phase654_loss_top20.csv` | Worst 20 losses not blocked by either shadow |
| `phase654_shadow_coverage.csv` | Flat-band / Rise5 virtual PnL by session |
| `phase654_symbol_loss_breakdown.csv` | Per-symbol PnL contribution |
| `phase654_exit_reason_breakdown.csv` | Loss reason classification |

## Mandatory answers (summary)

1. **Flat-band prevented how much?** Combined `delta_yen_100` from session shadow KPIs (AM + PM).
2. **Largest unblocked loss pattern** — top row in `phase654_loss_top20.csv` + dominant residual class.
3. **Next shadow candidates** — no_progress entry quality, stop reentry, scan-cap ranking, pullback misread, volume gate.
4. **Mainline change needed?** Partial — flat-band net positive on 7/7 but AM/PM split; residual stop/no_progress needs other guards.
5. **Logic vs market vs ops** — primarily logic (stop_hit / no_progress clusters); secondary difficult tape + latency alerts.

## Verdict

`phase654_20260707_loss_attribution_done`
