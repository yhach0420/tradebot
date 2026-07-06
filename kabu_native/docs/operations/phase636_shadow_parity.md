# Phase636: Shadow Parity after Phase635

## Purpose

Verify Phase635 PBv2 rise5 shadow guard does not change ENTRY/EXIT behavior.

Compares push-replay on Phase629A fixture days with:
- `pbv2_rise5_shadow_enabled: false` (shadow_off)
- `pbv2_rise5_shadow_enabled: true` (shadow_on, production default)

## Command

```bash
python scripts/check_phase636_shadow_parity.py
python scripts/check_phase636_shadow_parity.py --reuse
```

Exit `0` = ALL_MATCH.

## Compared (must match)

- accepted / PBv2 / OR counts
- events (shadow columns stripped)
- positions.csv (full content)
- summary (volatile + `pbv2_rise5_shadow_*` excluded)

## Artifacts

`results/reports/phase636_shadow_parity/`

Replay outputs: `results/small_paper/_phase636/{shadow_off,shadow_on}/`
