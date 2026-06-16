# Phase393 — Post-Paper Daily Artifact Organizer Report

**Date:** 2026-06-15  
**Scope:** Copy-only daily organization after paper / daily-runner session end

---

## Summary

`organize_daily_artifacts(repo_root, day)` scans `results/reports/`, copies same-day artifacts into `results/daily/YYYYMMDD/{category}/`, and writes `_daily_artifact_manifest.json`.

Legacy `results/reports/` is **not** deleted, moved, or removed from canonical read path.

| Check | Status |
| --- | --- |
| Organizer module | **Done** |
| Paper session hook (`pilot_runner`) | **Done** |
| Daily runner hook (`write_final_reports`) | **Done** |
| Tests (`test_daily_artifact_organizer.py`) | **5/5 pass** |
| Read path changed | **No** |
| Runtime / Universe / Entry / Exit / YAML | **No change** |

---

## Implementation Files

| File | Role |
| --- | --- |
| `src/storage/daily_artifact_organizer.py` | **New** — `organize_daily_artifacts` |
| `src/small_paper/pilot_runner.py` | `_organize_daily_artifacts_safe` after phase335 |
| `src/runner/am_pm_daily_runner.py` | `organize_daily_artifacts` after `write_final_reports` |
| `tests/test_daily_artifact_organizer.py` | **New** — unit tests |

---

## Classification Rules

### Dated files (`YYYYMMDD` in filename)

| Category | Patterns |
| --- | --- |
| **runtime** | `daily_runner_summary_*`, `daily_runner_commands_*`, `phase148_*`, `universe_*`, `features_*`, `small_paper_safety_*`, `phase113_*`, `opening_dynamic50_*`, `small_paper_gate_diagnosis_*` |
| **research** | `phase335_*`, `phase335_lite_*` (must include date in name) |
| **archive** | Any other dated file |

### Cumulative shadows (no date in filename)

| Category | Prefixes | Include when |
| --- | --- | --- |
| **live_candidate** | `phase273_*`, `phase274_*` | JST mtime = `day` **or** JSON summary contains `day` |
| **research** | `phase255_*`, `phase262_*`, `phase263_*` | Same |

Files without `day` in name and not qualifying cumulative → **skipped**.

---

## Manifest Example

Path: `kabu_native/results/daily/20260615/_daily_artifact_manifest.json`

```json
{
  "phase": "393-Daily-Artifact-Organizer",
  "day": "20260615",
  "generated_at": "2026-06-15T22:10:00+09:00",
  "copied_count": 24,
  "skipped_count": 1180,
  "warning_count": 0,
  "files_by_category": {
    "runtime": [
      "daily_runner_summary_20260615.json",
      "universe_core10_dynamic40_price_risk_am_20260615.csv",
      "features_20260615.csv"
    ],
    "live_candidate": [
      "phase273_live_config_shadow_summary.json"
    ],
    "research": [
      "phase255_sector_heat_forward_shadow_summary.json",
      "phase335_lite_realtime_board_shadow_ticks_20260615.csv"
    ],
    "archive": []
  },
  "warnings": [],
  "legacy_reports_dir": ".../kabu_native/results/reports",
  "daily_dir": ".../kabu_native/results/daily/20260615"
}
```

---

## Hook Order (live paper session)

```
canonical summary
→ sector heat shadow auto
→ risk sizing shadow auto
→ equity dynamic stop shadow auto
→ live config forward shadow auto
→ live config transition shadow auto
→ Discord session end
→ shadow finalize helpers
→ writer.finalize_batch
→ phase335 board shadow write
→ organize_daily_artifacts   ← Phase393
```

Failures → `warning` log only; paper session result unchanged.

---

## Test Results

```bash
cd kabu_native && PYTHONPATH=src python -m pytest tests/test_daily_artifact_organizer.py -q
# 5 passed
```

Covers: dated-only copy, legacy preserved, archive fallback, cumulative mtime, cumulative JSON day, phase335 research.

**Dry-run on repo (20260615):** copied **39** (runtime 12, live_candidate 8, research 18, archive 1), skipped 1190, warnings 0.

---

## Constraints Confirmation

| Layer | Changed |
| --- | --- |
| Runtime | **No** |
| Universe | **No** |
| Entry | **No** |
| Exit | **No** |
| YAML | **No** |
| Read paths | **No** |
| `results/reports/` deletion | **No** |

---

*Complements Phase392 dual-write; organizer provides end-of-day sweep + manifest.*
