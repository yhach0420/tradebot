# Phase649: PBv2 Flat-band Guard Counterfactual

Research-only validation of Phase648 worst cell as post-PBv2 filter candidate.

## Variants

| ID | Block condition |
|----|-----------------|
| `flat_cell_only` | Rise5 Flat × Rise10 Flat (-0.5~0.5% both) |
| `flat_band_narrow` | Rise5 0~0.5% AND Rise10 -0.5~0.5% |
| `flat_band_wide` | Rise5 0~0.5% AND Rise10 -0.5~1.0% |
| `weak_motion_guard` | abs(Rise5)<0.5% AND abs(Rise10)<0.5% |
| `flat_plus_overheat` | flat_band_narrow OR Rise5>2% |

## Run

```bash
python scripts/run_phase649_flat_band_guard_counterfactual.py
python -m pytest tests/test_phase649_flat_band_guard_counterfactual.py -q
```

## Artifacts

```
results/reports/phase649_flat_band_guard/
  phase649_report.json
  phase649_variant_comparison.csv
  phase649_daily_breakdown.csv
  phase649_symbol_breakdown.csv
  phase649_blocked_trades.csv
  phase649_leave_one_symbol_out.csv
```

## Constraints

- PBv2 score unchanged; OR excluded from dataset
- No ENTRY/EXIT/YAML changes — counterfactual only

## Shadow path (future)

`src/small_paper/pbv2_flat_band_guard_shadow.py` at PBv2 accept hook in `pilot_runner.py`.

## Verdict

`phase649_flat_band_guard_counterfactual_done`

## Findings (2026-07-06 run)

- **PBv2 baseline:** 3,074 trades, PnL +98,090 / PF 1.02 / max DD -621k
- **All 5 guard variants improve ΔPnL** (best: `flat_plus_overheat` +460,820, ΔDD +166k)
- **Best PnL:** `flat_band_wide` +455,090; **best DD:** `flat_cell_only` +253k
- **flat_cell_only ≡ weak_motion_guard** in this dataset (914 blocked, identical metrics)
- **pre625/post625:** all variants positive pre625; post625 mixed (flat_cell -51k)
- **Worst day:** 2026-06-25 ΔPnL -23,800 under flat_plus_overheat
- **Symbol concentration:** low (max LOO share 14%); not single-symbol dependent
- **Phase635 overlap:** 107 trades blocked by both; mostly complementary
- **SUMCO 2026-07-06:** no variant blocks (rise10 missing / bands don't match)
- **Recommendation:** **HOLD** — proceed to shadow guard
