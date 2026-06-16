# Results Directory Refactor Plan

**Status:** Investigation & design only (Phase 0)  
**Date:** 2026-06-15  
**Constraint:** Output-path organization only — Runtime / Universe / Entry / Exit / YAML **unchanged**

---

## 1. Problem

`kabu_native/results/reports/` has grown to **~1,228 files** (flat). Daily runner, universe CSVs, forward shadows, and one-off phase reviews all share one directory, making it hard to find today's operational artifacts.

| Metric | Count |
| --- | --- |
| Total files in `results/reports/` | ~1,228 |
| Date-stamped (`_*YYYYMMDD.*`) | ~368 |
| Undated / cumulative | ~860 |
| Python files referencing `results/reports` | **194** (`scripts` 188, `src` 5 literal, `tests` 1) |
| Scripts with `REPORTS = REPO / ... / reports` | **83** |

---

## 2. Current Output Inventory

### 2.1 Target categories (user-specified)

| Category | Prefix / pattern | Current count | Nature |
| --- | --- | ---: | --- |
| **runtime** | `daily_runner_summary_*` | 17 | Per-day JSON |
| | `daily_runner_commands_*` | 17 | Per-day JSON |
| | `phase148_am_pm_daily_runner_*` | 17 | Per-day JSON |
| | `universe_core10_dynamic40_*` | 74 | Per-day CSV (AM/PM/refresh) |
| | `features_*` | 17 | Per-day CSV (universe input) |
| | `small_paper_safety_*` | 17 | Per-day JSON |
| | `phase113_*` | 34 | Per-day universe pipeline |
| **live_candidate** | `phase273_live_config_shadow_*` | 4 | Cumulative CSV/JSON/MD |
| | `phase274_live_config_transition_*` | 4 | Cumulative CSV/JSON/MD |
| **research** | `phase255_sector_heat_forward_shadow_*` | 4 | Cumulative forward shadow |
| | `phase262_risk_sizing_forward_*` | 4 | Cumulative forward shadow |
| | `phase263_*` (equity dynamic stop) | 4 | Cumulative forward shadow |
| | `phase335_*` / `phase335_lite_*` | 36 | Per-day board shadow ticks/events |
| **archive** | All other `phase*` review JSON/MD/CSV | ~860 | Historical one-off studies |

### 2.2 Example — 2026-06-15 session (today)

Files produced on a typical paper day (from latest run):

```
daily_runner_summary_20260615.json
daily_runner_commands_20260615.json
phase148_am_pm_daily_runner_20260615.json
universe_core10_dynamic40_price_risk_am_20260615.csv
universe_core10_dynamic40_price_risk_pm_20260615.csv
universe_core10_dynamic40_price_risk_am_refresh1000_20260615.csv
universe_core10_dynamic40_price_risk_pm_refresh1430_20260615.csv
features_20260615.csv
small_paper_safety_20260615.json
phase113_vol_liq_dynamic50_universe_20260615.json
phase335_lite_realtime_board_shadow_*_20260615.*
phase335_realtime_board_shadow_*_20260615.*
```

Post-session forward shadows (cumulative, **no date in filename**):

```
phase255_sector_heat_forward_shadow_*.csv/json/md
phase262_risk_sizing_forward_*.csv/json/md
phase263_*.csv/json/md
phase273_live_config_shadow_*.csv/json/md
phase274_live_config_transition_*.csv/json/md
```

### 2.3 Other notable outputs (not in target taxonomy)

| Pattern | Role | Migration suggestion |
| --- | --- | --- |
| `phase376_*`, `phase377_*` | Post-close production monitoring | `daily/YYYYMMDD/runtime/` or `research/` |
| `phase267_*` … `phase272_*` | Capital-path research (superseded by 273/274) | `archive/` |
| `phase384_*` … `phase389_*` | CAP research (not runtime) | `research/` or `archive/` |
| `full_phase_history_report.md` | Audit snapshot (temporary) | Stay out of refactor scope |
| `small_paper_gate_diagnosis_*.json` | Phase43 safety gate | `runtime/` (referenced by YAML glob) |

### 2.4 Existing sibling directories (unchanged)

```
kabu_native/results/
├── reports/          ← refactor target
├── small_paper/      ← session events (live_session_*, structural_trades.csv)
├── paper_trade/      ← legacy trades
├── morning_screen/
├── replay/
└── shadow/
```

Session **events** stay in `results/small_paper/` — this refactor is **reports output only**.

---

## 3. Proposed Directory Layout

### 3.1 Top-level categories (cumulative / cross-day)

For artifacts that accumulate across days (forward shadows, live-candidate curves):

```
kabu_native/results/
├── daily/                    # per-day tree (primary for dated outputs)
├── runtime/                  # optional symlink / mirror for latest cumulative runtime index
├── live_candidate/           # phase273/274 cumulative
├── research/                 # phase255/262/263 cumulative (+ phase335 index if needed)
└── archive/                  # retired phase reviews, large tick dumps
```

### 3.2 Per-day tree (primary for dated outputs)

```
kabu_native/results/daily/20260615/
├── runtime/
│   ├── daily_runner_summary_20260615.json
│   ├── phase148_am_pm_daily_runner_20260615.json
│   ├── universe_core10_dynamic40_price_risk_am_20260615.csv
│   ├── features_20260615.csv
│   ├── small_paper_safety_20260615.json
│   └── ...
├── live_candidate/
│   └── (optional daily snapshot copies of 273/274 if dual-written)
├── research/
│   ├── phase335_lite_realtime_board_shadow_*_20260615.*
│   ├── phase335_realtime_board_shadow_*_20260615.*
│   └── (optional daily snapshot of forward-shadow summary deltas)
└── archive/
    └── (large tick CSVs moved here after N days)
```

### 3.3 Placement rules

| Category | Patterns |
| --- | --- |
| `runtime/` | `daily_runner_summary_*`, `daily_runner_commands_*`, `phase148_*`, `universe_core10_dynamic40_*`, `universe_vol_liq_*`, `features_*`, `small_paper_safety_*`, `phase113_*`, `opening_dynamic50_*` |
| `live_candidate/` | `phase273_*`, `phase274_*` |
| `research/` | `phase255_*`, `phase262_*`, `phase263_*`, `phase335_*`, `phase335_lite_*` |
| `archive/` | Legacy `phase*` reviews, superseded capital studies, oversized shadow tick files |

### 3.4 Cumulative vs per-day resolution

| Type | Example | Canonical location after Phase 3 |
| --- | --- | --- |
| Per-day | `universe_*_20260615.csv` | `daily/20260615/runtime/` |
| Cumulative | `phase255_sector_heat_forward_shadow_summary.json` | `research/` (top-level) **and** daily snapshot optional |
| Per-day shadow ticks | `phase335_lite_*_20260615.csv` | `daily/20260615/research/` |

**Design decision (recommended):** Per-day files live **only** under `daily/YYYYMMDD/`. Cumulative forward-shadow files live in top-level `research/` or `live_candidate/` with upsert semantics unchanged.

---

## 4. Reference Survey — Who Uses `results/reports/`?

### 4.1 Runtime (critical path — paper trade must not break)

| Component | File | Usage |
| --- | --- | --- |
| Daily runner | `src/runner/am_pm_daily_runner.py` | `REPORTS_REL = "kabu_native/results/reports"`; writes `daily_runner_summary_*`, `phase148_*`, reads/writes universe via `reports_dir` |
| Universe paths | `src/universe/core10_dynamic40*.py`, `intraday_refresh.py`, `daily_features.py` | All `*_path(reports_dir, day_stamp)` builders |
| Pilot runner | `src/small_paper/pilot_runner.py` | Hardcoded `reports_dir` for universe meta (Phase355/364 guards), phase335 write hook |
| Universe meta resolver | `src/small_paper/pullback_misread_dynamic40_entry_guard.py` | `resolve_universe_meta_path()` reads refresh CSV from `reports_dir` |
| Board shadow | `src/small_paper/realtime_board_exit_shadow.py` | `write_phase335_lite_outputs()` → `results/reports/phase335_*` |
| Pilot config / YAML | `src/small_paper/config.py` + all production YAMLs | `phase43_diagnosis_glob: kabu_native/results/reports/small_paper_gate_diagnosis_*.json` |
| Readiness | `src/small_paper/live_observer_readiness.py` | Scans reports for gate diagnosis |

**Impact:** **HIGH** — wrong path = universe not found, entry guards fail, daily runner cannot start pilot.

### 4.2 Shadow (post-session auto hooks)

| Component | File | Output prefix |
| --- | --- | --- |
| Sector heat auto | `src/small_paper/sector_heat_forward_shadow_auto.py` | phase255 (via logger) |
| Risk sizing auto | `src/small_paper/risk_sizing_forward_shadow_auto.py` | phase262 |
| Equity dynamic stop auto | `src/small_paper/equity_dynamic_stop_shadow_auto.py` | phase263 |
| Live config auto | `src/small_paper/live_config_forward_shadow_auto.py` | phase273 |
| Transition auto | `src/small_paper/live_config_transition_shadow_auto.py` | phase274 |

Default: `reports_dir or repo_root / "kabu_native" / "results" / "reports"`.

**Impact:** **MEDIUM** — shadows fail silently (`status=warning`); paper session unaffected but research pipeline gaps.

### 4.3 Report generators (scripts)

| Area | Count | Pattern |
| --- | ---: | --- |
| `scripts/run_phase*.py` | ~188 files | `REPORTS = REPO / "kabu_native" / "results" / "reports"` |
| One-off reviews | ~860 undated files | Write-only; read by humans / audit grep |

**Impact:** **LOW per script, HIGH in aggregate** — bulk path update needed in Phase 3.

### 4.4 Docs generators

| Component | File | Usage |
| --- | --- | --- |
| Phase audit | `scripts/run_full_phase_history_audit.py` | `REPORTS.glob("phase{N}_*summary.json")` for evidence |
| SoT generator | `scripts/run_full_system_development_history.py` | Documents `results/reports/` as audit snapshot path |
| Audit CSV | `docs/audits/full_phase_history_audit.csv` | **~306** embedded `results/reports/` evidence paths |
| Architecture docs | `docs/kabu_station_system_design.md`, `directory_structure.md` | Human references |

**Impact:** **MEDIUM** — audit evidence paths stale after move unless dual-written or path-normalized.

### 4.5 Discord notifier

| Component | File | Usage |
| --- | --- | --- |
| `discord_notifier.py` | — | **No** direct `results/reports` reference |
| `discord_message_builder.py` | — | **No** direct reference |

Discord reads **session summary dict** from pilot_runner, not report files.

**Impact:** **NONE** for path refactor.

### 4.6 Research modules (`src/research/`)

**~100 modules** accept `reports_dir: Path` and embed filenames in `.paths()`. All default to caller-provided `reports_dir` (scripts pass `REPORTS`).

**Impact:** **MEDIUM** — centralized resolver fixes all callers at once.

### 4.7 Tests

| File | Usage |
| --- | --- |
| `tests/test_am_pm_daily_runner_session_dirs.py` | Hardcoded universe CSV paths under `results/reports/` |

**Impact:** **LOW** — update fixtures with resolver.

### 4.8 Configs (YAML — read-only constraint)

These **read** paths at runtime; cannot change without YAML edit exception:

```yaml
phase43_diagnosis_glob: kabu_native/results/reports/small_paper_gate_diagnosis_*.json
```

**Phase 1–2 workaround:** Keep writing `small_paper_gate_diagnosis_*.json` to **both** old and new locations, or use a symlink `reports/` → shim directory.

---

## 5. Phased Migration

### Phase 1 — Dual-write (copy output, no deletion)

**Goal:** New tree populated; `results/reports/` remains canonical for reads.

1. Add path resolver module (see §6).
2. For each writer, call `write_dual(dest_new, dest_legacy)` or write to legacy then `shutil.copy2` to new path.
3. Categories in scope first:
   - Daily runner outputs → `daily/YYYYMMDD/runtime/`
   - Post-session shadows → top-level `research/` + `live_candidate/` **and** copy to `daily/YYYYMMDD/...`
   - Phase335 per-day → `daily/YYYYMMDD/research/`
4. Add `results/reports/README_DEPRECATED.md` pointing to new layout.
5. **Do not delete** anything in `reports/`.

**Validation:** Compare file hashes old vs new after one full paper day.

### Phase 2 — Read fallback (survey + switch reads)

**Goal:** Code reads new path first, falls back to legacy.

1. Update **runtime-critical** readers:
   - `am_pm_daily_runner` `reports_dir` initialization
   - `resolve_universe_meta_path`
   - `market_sector_heat_forward_shadow_logger.resolve_am_universe_path`
   - `pilot_runner._load_symbol_universe_meta_for_day`
2. Update shadow auto modules to pass resolved `reports_dir`.
3. Update `run_full_phase_history_audit.py` to glob both trees.
4. Log `DEPRECATED_REPORTS_PATH` once per process when fallback used.
5. Run full test suite + tomorrow preflight audit.

**YAML:** Still points at `results/reports/` for phase43 — keep dual-write to that glob until Phase 3 YAML migration approved separately.

### Phase 3 — Full migration

**Goal:** New paths canonical; archive flat `reports/`.

1. Bulk-update 83 script `REPORTS` constants → import resolver.
2. Move ~860 undated files to `archive/` (scripted, with manifest CSV).
3. Update `directory_structure.md`, SoT docs (maintenance-only).
4. Regenerate audit CSV evidence paths (maintenance event).
5. Remove dual-write; keep `reports/` as symlink to `daily/latest/` or delete after 30-day soak.
6. Optional: `phase43_diagnosis_glob` YAML update (requires explicit YAML change approval).

---

## 6. Recommended Implementation — Path Resolver

**New module (Phase 1):** `kabu_native/src/storage/results_paths.py`

```python
# Sketch — not implemented yet
RESULTS_ROOT = repo / "kabu_native" / "results"
LEGACY_REPORTS = RESULTS_ROOT / "reports"

def daily_dir(day: str) -> Path: ...
def runtime_dir(day: str | None = None) -> Path: ...
def live_candidate_dir() -> Path: ...
def research_dir() -> Path: ...
def archive_dir() -> Path: ...

def resolve_read(path: Path) -> Path:
    """New path if exists, else legacy reports/."""

def category_for_filename(name: str) -> str: ...
```

**Single import point** avoids editing 194 files repeatedly.

---

## 7. Risks

| Risk | Severity | Mitigation |
| --- | --- | --- |
| Universe CSV not found at entry | **Critical** | Phase 2 read-fallback; never delete legacy until soak complete |
| Phase43 YAML glob mismatch | **High** | Dual-write gate diagnosis to legacy path throughout Phase 1–2 |
| Sector heat reads wrong AM universe | **High** | `resolve_am_universe_path` must use resolver |
| Audit CSV 306 stale paths | **Medium** | Regenerate audit after Phase 3; not blocking runtime |
| 860 orphan files — wrong archive move | **Medium** | Manifest + dry-run; exclude last 30 days |
| Duplicate disk usage (dual-write) | **Low** | Acceptable short-term; phase335 ticks largest |
| External tooling / muscle memory | **Low** | `reports/README_DEPRECATED.md` |

---

## 8. Recommended Implementation Order

| Step | Work | Phase | Effort |
| --- | --- | ---: | ---: |
| 1 | `results_paths.py` + unit tests | 1 | S |
| 2 | Dual-write in `am_pm_daily_runner` (runtime daily) | 1 | M |
| 3 | Dual-write universe `*_path()` builders | 1 | M |
| 4 | Dual-write 5 post-session shadow autos | 1 | S |
| 5 | Dual-write `realtime_board_exit_shadow` (phase335) | 1 | S |
| 6 | Read-fallback in universe meta + sector heat | 2 | M |
| 7 | Switch `pilot_runner` reports_dir to resolver | 2 | S |
| 8 | Audit script dual-glob | 2 | S |
| 9 | Script `REPORTS` bulk migration (83 files) | 3 | L |
| 10 | Archive move script + manifest | 3 | M |
| 11 | Deprecate `results/reports/` | 3 | S |

**Do not start with** archive migration or deleting flat files.

**Do not change** Runtime / Universe / Entry / Exit / YAML in Phase 1–2 except read-path fallback in Python (output paths only).

---

## 9. Out of Scope (this plan)

- Moving `results/small_paper/` session directories
- Changing shadow **logic** or adoption gates
- Documentation expansion beyond this plan + `directory_structure.md` maintenance in Phase 3
- `full_phase_history_report.md` relocation (stays temporary in `reports/` until audit workflow updated)

---

## 10. Success Criteria

After Phase 3:

1. `ls results/daily/20260615/runtime/` shows today's universe + daily_runner artifacts.
2. Forward shadows in `results/research/` and `results/live_candidate/`.
3. `results/reports/` empty or symlink-only.
4. Paper trade + shadow pipeline pass preflight audit with zero FAIL.
5. No Runtime / Universe / Entry / Exit / trading YAML changes.

---

## Appendix A — File prefix histogram (top 20)

| Count | Prefix |
| ---: | --- |
| 74 | `universe_core10` |
| 34 | `daily_runner` |
| 21 | `small_paper` |
| 18 | `phase335_lite` |
| 18 | `phase335_realtime` |
| 17 | `phase154_daily` |
| 17 | `phase148_am` |
| 17 | `phase113_vol` |
| 17 | `phase113_runner` |
| 17 | `universe_vol` |
| 12 | `opening_dynamic50` |
| 8 | `phase336_realtime` |
| 8 | `phase381_winner` |
| 7 | `phase382_capital` |
| 7 | `phase159_overlap` |
| 7 | `phase379_low` |
| 6 | `phase383_realistic` |
| 6 | `phase353_pullback` |
| 6 | `phase348_20260612` |
| 6 | `phase352_limit` |

---

## Appendix B — Central constants today

| Constant | Location |
| --- | --- |
| `REPORTS_REL = "kabu_native/results/reports"` | `src/runner/am_pm_daily_runner.py` |
| `repo_root / "kabu_native" / "results" / "reports"` | 5 shadow auto modules, `pilot_runner`, `realtime_board_exit_shadow` |
| `REPORTS = REPO / "kabu_native" / "results" / "reports"` | 83 scripts |
| `phase43_diagnosis_glob` | Production YAML + `config.py` default |

---

*Investigation only. No code or output paths were modified in producing this document.*
