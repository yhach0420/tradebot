# Phase392 — Results Directory Refactor Phase 1 Report

**Date:** 2026-06-15  
**Scope:** Dual-write copy only — legacy `results/reports/` unchanged as canonical read path

---

## Executive Summary

Phase 1 dual-write is implemented. All writers continue to emit to `kabu_native/results/reports/` first; after each write, eligible files are **copied** to the organized layout via `storage/results_paths.py`.

| Check | Status |
| --- | --- |
| Path resolver | **Done** |
| Daily runner dual-write | **Done** |
| Forward shadow dual-write | **Done** |
| Phase335 dual-write | **Done** |
| README_DEPRECATED | **Done** |
| Tests (`test_results_paths.py`) | **17/17 pass** |
| Legacy `reports/` maintained | **Yes** |
| Read path changed | **No** |
| Runtime / Universe / Entry / Exit / YAML | **No change** |

---

## Implementation Locations

| # | Component | File | Change |
| --- | --- | --- | --- |
| 1 | Path resolver | `src/storage/results_paths.py` | **New** |
| 2 | Daily runner | `src/runner/am_pm_daily_runner.py` | `_dual_write_runtime_artifacts()` after universe/safety/final writes |
| 3 | Phase255 logger | `src/research/market_sector_heat_forward_shadow_logger.py` | `write_outputs` → dual-write |
| 4 | Phase262 logger | `src/research/risk_sizing_forward_shadow_logger.py` | `write_outputs` → dual-write |
| 5 | Phase263 logger | `src/research/equity_dynamic_stop_shadow.py` | `write_outputs` → dual-write |
| 6 | Phase273 logger | `src/research/phase273_live_config_forward_shadow_logger.py` | `write_outputs` → dual-write |
| 7 | Phase274 logger | `src/research/phase274_live_config_auto_transition_shadow.py` | `write_outputs` → dual-write |
| 8 | Phase335 | `src/small_paper/realtime_board_exit_shadow.py` | `write_phase335_lite_outputs` → dual-write |
| 9 | Tests | `tests/test_results_paths.py` | **New** |
| 10 | README | `results/reports/README_DEPRECATED.md` | **New** |

Auto modules (`sector_heat_forward_shadow_auto`, etc.) inherit dual-write via logger `write_outputs` patches — no separate auto-module edits required.

---

## Dual-Write Targets

### Runtime → `daily/YYYYMMDD/runtime/` only

| Pattern | Trigger |
| --- | --- |
| `daily_runner_summary_*` | `write_final_reports` |
| `daily_runner_commands_*` | `write_final_reports` |
| `phase148_*` | `write_final_reports` |
| `universe_core10_dynamic40_*` | `build_am_universe`, `build_pm_universe`, intraday refresh |
| `universe_vol_liq_*` | `ensure_features_csv` (phase113 pipeline) |
| `features_*` | `ensure_features_csv` |
| `small_paper_safety_*` | `run_safety_check` |
| `phase113_*` | `dual_write_runtime_day_artifacts` scan |

### Research → `results/research/` + `daily/YYYYMMDD/research/`

| Pattern | Source |
| --- | --- |
| `phase255_*` | Sector heat forward shadow logger |
| `phase262_*` | Risk sizing forward shadow logger |
| `phase263_*` | Equity dynamic stop shadow |
| `phase335_*`, `phase335_lite_*` | Realtime board exit shadow |

### Live candidate → `results/live_candidate/` + `daily/YYYYMMDD/live_candidate/`

| Pattern | Source |
| --- | --- |
| `phase273_*` | Live config forward shadow logger |
| `phase274_*` | Live config auto transition shadow |

### Archive → `results/archive/` + `daily/YYYYMMDD/archive/`

All other filenames (e.g. `phase390_misc.json`).

---

## Copy Destinations (example day `20260615`)

```
kabu_native/results/daily/20260615/runtime/daily_runner_summary_20260615.json
kabu_native/results/daily/20260615/research/phase335_lite_realtime_board_shadow_ticks_20260615.csv
kabu_native/results/daily/20260615/live_candidate/phase273_live_config_shadow_summary.json
kabu_native/results/research/phase255_sector_heat_forward_shadow_summary.json
kabu_native/results/live_candidate/phase274_live_config_transition_summary.json
```

Legacy canonical copies remain at:

```
kabu_native/results/reports/<same-filename>
```

---

## Test Results

```bash
cd kabu_native
PYTHONPATH=src python -m pytest tests/test_results_paths.py -q
# 17 passed

PYTHONPATH=src python -m pytest tests/test_am_pm_daily_runner_session_dirs.py -q
# 7 passed, 1 failed (pre-existing: missing temp config YAML — unrelated to Phase392)
```

`test_results_paths.py` covers:

- `category_for_filename` (runtime / live_candidate / research / archive)
- `daily_target_for_file` / `cumulative_target_for_file`
- `copy_to_daily_and_category` (runtime single-dest, research dual-dest)
- Missing file warning
- `dual_write_output_paths`
- Unknown → archive

Manual smoke: research file copied to both `results/research/` and `daily/20260615/research/` — **OK**.

---

## Existing `reports/` Maintenance

| Rule | Status |
| --- | --- |
| Legacy write path | **Unchanged** — all `write_outputs` / `write_text` still target `reports_dir` |
| Legacy read path | **Unchanged** — no `resolve_read` fallback yet |
| File deletion | **None** |
| File move | **None** |
| `reports/` deprecation | **Not started** (README only) |

---

## Constraints Confirmation

| Layer | Changed |
| --- | --- |
| Runtime | **No** |
| Universe | **No** |
| Entry | **No** |
| Exit | **No** |
| YAML | **No** |
| Shadow logic | **No** (copy after write only) |
| Adoption gate | **No** |
| Discord | **No** |

---

## Phase 2 Read Fallback — Required Targets

Priority order for read-path migration (not implemented in Phase 1):

| Priority | Component | Current read |
| --- | --- | --- |
| P0 | `am_pm_daily_runner.reports_dir` | `results/reports/` |
| P0 | `pullback_misread_dynamic40_entry_guard.resolve_universe_meta_path` | refresh CSV in `reports/` |
| P0 | `market_sector_heat_forward_shadow_logger.resolve_am_universe_path` | AM universe CSV in `reports/` |
| P0 | `pilot_runner._load_symbol_universe_meta_for_day` | `reports/` |
| P1 | `small_paper/config.py` `phase43_diagnosis_glob` | YAML glob → `reports/` (**YAML change gated**) |
| P1 | 83 script `REPORTS` constants | hardcoded path |
| P2 | `run_full_phase_history_audit.py` evidence glob | `reports/` |
| P2 | `live_observer_readiness.py` | gate diagnosis scan |
| P3 | Historical phase scripts (one-off reads) | various |

**Recommended Phase 2 entry:** Add `resolve_read_path(repo_root, relative_name)` in `results_paths.py` and wire P0 runtime readers only.

---

## Verification Commands

```bash
# Unit tests
cd kabu_native && PYTHONPATH=src python -m pytest tests/test_results_paths.py -q

# After next paper day — confirm copies exist (example)
ls kabu_native/results/daily/20260616/runtime/
ls kabu_native/results/research/
ls kabu_native/results/live_candidate/

# Legacy still canonical
ls kabu_native/results/reports/daily_runner_summary_*.json
```

---

*Phase 1 complete. Proceed to Phase 2 read-fallback when approved.*
