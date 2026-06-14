# Tomorrow Paper Trade & Shadow Pipeline — Preflight Audit

**Generated:** 2026-06-14 (static inspection)  
**Scope:** Stack C runtime wiring, post-session shadow hooks, Discord routing, adoption gates  
**Method:** Source review only — no Runtime / Universe / Entry / Exit / YAML changes

---

# Executive Summary

| Check | Status | Notes |
| --- | --- | --- |
| 1. Runtime本線 (Stack C config) | PASS | `trailing_mfe_shadow.yaml` matches SoT layers |
| 2. Post-session shadow hook order (live) | PASS | 256→262→266→273→274 in `run_live_dry_run` before Discord |
| 3. Shadow output paths | PASS | All expected files under `results/reports/` |
| 4. Upsert / idempotency | PASS | Phase255 upsert; Phase262 day-replace; 263/273/274 full recompute overwrite |
| 5. structural_trades.csv auto-gen | PASS | live-only; push-replay excluded; research-only backfill |
| 6. Discord routing | PASS | trade-notify + cap-blocked split; Research Shadow in Daily Summary |
| 7. Phase274 auto transition logic | PASS | entry-equity band; exit uses stored stop_policy; transition gated at 2M |
| 8. Adoption gate | PASS | day_count&lt;10 → adopt_not_allowed; final_equity primary; PF logged not sole gate |
| 9. Error handling | PASS | shadow exceptions → status=warning; paper close continues |
| 10. Command / SoT alignment | WARN | Daily runner **default** config ≠ Stack C unless flags set |
| push-replay shadow hooks | WARN | `run_push_replay` does not run post-session shadows (live only) |
| Forward shadow sample size | WARN | day_count=9 → adopt_not_allowed=True (expected until day 10) |
| Discord shadow warning detail | WARN | `warning=` text not appended to Research Shadow embed (status only) |

---

# Runtime Path

## Stack C canonical config

**File:** `kabu_native/configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml`

| Layer | Expected | Verified |
| --- | --- | --- |
| Universe (vol-liq top50) | `daytrade_suitability_rule: volatility_liquidity_top50` | PASS |
| Universe (core10+d40 price-risk) | via daily runner `--universe-mode core10-dynamic40-price-risk-filter-shadow` | PASS (orchestration) |
| AM/PM refresh | via `--enable-intraday-refresh` (10:00 / 14:30) | PASS (orchestration) |
| Entry score v2 | `entry_score_v2_min: 3`; tokens `Momentum:low` + `Board:mid` in `entry_expectancy_score_shadow.py` | PASS |
| Price risk guard | `entry_price_risk_guard_enabled: true`, apply_mode reject | PASS |
| Phase355 pullback guard | `enable_pullback_misread_dynamic40_guard: true` | PASS |
| Phase364 near-high guard | `enable_near_day_high_low_momentum_dynamic40_guard: true` | PASS |
| Entry scan freshness / batch | `entry_freshness_guard_enabled`, `entry_scan_batch_enabled`, age limits | PASS |
| Exit Phase332 | `structural_exit_policy: combined_structural_exit_v1_trailing_mfe_shadow` | PASS |
| hard_stop / overlap / session | structural v1 + observer session_close | PASS (policy chain) |
| Position cap | `max_concurrent_positions: 3` | PASS |
| Mode | `paper_only: true`, `shadow_only: true`, `order_enabled: false` | PASS |
| CAP=2 | not in runtime YAML; research only (387/388/389) | PASS |

## Production launch command (must match SoT)

Documented in `full_system_development_history.md` / `kabu_station_system_design.md`:

```bash
python kabu_native/scripts/run_core10_dynamic40_am_pm_daily_runner.py \
  --universe-mode core10-dynamic40-price-risk-filter-shadow \
  --enable-intraday-refresh \
  --exit-policy-shadow trailing-mfe
```

**WARN:** Without `--exit-policy-shadow trailing-mfe`, daily runner defaults to `entry_price_risk_guard_shadow.yaml` (structural v1 exit, **no** Phase355/364/freshness guards). That is **not** Stack C.

**WARN:** Standalone `run_small_paper_pilot.py` defaults to `universe_intraday_full.csv` unless `--universe-csv` is passed. Use daily runner for production.

---

# Post-Session Shadow Hooks

## Live session path (`pilot_runner.run_live_dry_run`)

After `_attach_canonical_summary_fields`, **before** `notify_discord_session_end`:

| Order | Function | Phase |
| --- | --- | --- |
| 1 | `_run_sector_heat_forward_shadow_auto` | 256 → 255 logger |
| 2 | `_run_risk_sizing_forward_shadow_auto` | 262 |
| 3 | `_run_equity_dynamic_stop_shadow_auto` | 266 → 263 outputs |
| 4 | `_run_live_config_forward_shadow_auto` | 273 |
| 5 | `_run_live_config_transition_shadow_auto` | 274 |

Then: `notify_discord_session_end` → shadow finalize helpers → `writer.finalize_batch`.

**Source:** `pilot_runner.py` lines ~3368–3420.

## push-replay path

`run_push_replay` calls Discord **before** finalize and **does not** invoke the five auto-shadow hooks.

**Impact:** push-replay smoke tests will not exercise forward shadows. Tomorrow live paper (via `--source live`) is unaffected.

---

# Output Files

All paths: `kabu_native/results/reports/`

| Phase | Files | Code anchor | On-disk |
| --- | --- | --- | --- |
| 255/256 SectorHeat | `phase255_sector_heat_forward_shadow_universe_by_day.csv` | `MarketSectorHeatForwardShadowLogger.paths()` | present |
| | `phase255_sector_heat_forward_shadow_trade_by_day.csv` | | present |
| | `phase255_sector_heat_forward_shadow_summary.json` | | present |
| | `phase255_sector_heat_report.md` | | present |
| 262 RiskAware | `phase262_risk_sizing_forward_entry_by_day.csv` | `RiskSizingForwardShadowLogger.paths()` | expected at run |
| | `phase262_risk_sizing_forward_summary_by_day.csv` | | |
| | `phase262_risk_sizing_forward_summary.json` | | |
| | `phase262_risk_sizing_report.md` | | |
| 266/263 Equity Dynamic Stop | `phase263_entry_level_dynamic_stop.csv` | `EquityDynamicStopShadow.paths()` | expected at run |
| | `phase263_summary_by_equity_risk_pct.csv` | | |
| | `phase263_equity_dynamic_stop_summary.json` | | |
| | `phase263_report.md` | | |
| 273 LiveConfig | `phase273_live_config_shadow_daily_equity.csv` | `LiveConfigForwardShadowLogger.paths()` | present |
| | `phase273_live_config_shadow_trade_events.csv` | | |
| | `phase273_live_config_shadow_summary.json` | | present (day_count=9) |
| | `phase273_live_config_shadow_report.md` | | |
| 274 Auto Transition | `phase274_live_config_transition_equity_curve.csv` | `LiveConfigAutoTransitionShadow.paths()` | present |
| | `phase274_live_config_transition_daily_equity.csv` | | |
| | `phase274_live_config_transition_summary.json` | | present |
| | `phase274_live_config_transition_report.md` | | |

---

# Idempotency & Data Hygiene

| Artifact | Mechanism | Re-run same day |
| --- | --- | --- |
| Phase255 CSVs | `_upsert_rows(..., key_fields=("day","pattern"))` | no duplicate keys |
| Phase262 entry CSV | `_replace_day_rows(..., day=day)` | day rows replaced |
| Phase262/263/273/274 JSON | full period recompute + overwrite | deterministic replace |
| Phase273/274 CSV | full recompute write (not append-only) | no row duplication |
| Trade population | `load_trades_by_day` skips `source=push-replay` | push-replay excluded |
| Backfill | `classify_session` skips push-replay, debug, non-live | live sessions only |

**structural_trades.csv:** Each auto module calls `_ensure_structural_trades_csv` when missing — Phase265 `backfill_session` then `build_and_write_structural_observer_review`. Does not mutate paper event logs.

---

# Discord Routing

| Channel | Events | Verified |
| --- | --- | --- |
| trade-notify (`KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL`) | ENTRY (`notify_entry`), EXIT (`notify_exit`), AM/PM/Daily Summary (`notify_daily_summary`, `trade_notify=True`) | PASS |
| trade-cap-blocked (`KABU_SMALL_PAPER_CAP_BLOCKED_WEBHOOK_URL`) | REJECT_MAX_CONCURRENT via `notify_entry_cap_blocked` — includes active_positions, position_cap, entry_score_v2, entry reasons | PASS |
| Daily Summary embed | `format_research_shadow_daily_summary_lines` → field **Research Shadow** with all 5 shadow blocks | PASS |

Research Shadow blocks: SectorHeat, RiskAware Sizing, Equity Dynamic Stop, LiveConfig, LiveConfig Transition.

---

# Auto Transition (Phase274)

**Policy (`resolve_policy_band`):**

| current_equity | band | cap | stop_policy |
| --- | --- | --- | --- |
| &lt; 2,000,000 | 1500k | 3 | fixed_stop_1p2 |
| ≥ 2,000,000 | 2000k+ | 5 | dynamic_stop_risk_1p0 |

**Verified behaviors:**

- `try_entry`: band/cap/stop resolved from **entry-time** `current_equity`; stored on open position.
- `_close_position`: uses **stored** `stop_policy` from entry, not re-resolved at exit.
- `transition_day_to_2000k` recorded when equity first crosses 2M.
- Current snapshot (`phase274_live_config_transition_summary.json`): equity=1,650,270, `transition_to_2000k=false`, cap=3, stop=fixed — correct for sub-2M path.

**Research only** — does not alter runtime cap3 / fixed stop.

---

# Adoption Gate

Shared forward-shadow rules (Phase273/274):

| Rule | Implementation |
| --- | --- |
| day_count &lt; 10 | `adopt_not_allowed=True`, verdict `observe` |
| day_count ≥ 10 | still blocked if `final_equity ≤ starting_equity` or `days_below_50pct > 0` |
| max_drawdown | `caution` if &gt;20%; not sole hard block (separate from adopt_not_allowed) |
| PF alone | explicit note: *"Research PF alone must not drive adoption"*; recommendation uses `adopt_not_allowed` not PF |

**Current state:** day_count=9, adopt_not_allowed=True — expected. Not a paper-start blocker.

---

# Error Handling

Each `_run_*_shadow_auto` wrapper in `pilot_runner.py`:

- Inner module: `try/except` → returns `status=warning`, logs, **never raises**.
- Outer wrapper: second `try/except` for unexpected errors → same pattern.
- Paper session continues: Discord summary, canonical summary, `writer.finalize_batch` always run after hooks.
- No order / runtime mutation on shadow failure.

**WARN:** Discord Research Shadow shows `status=warning` but not the `warning=` exception string.

---

# Final Verdict

## 明日 paper trade を開始してよいか

**Yes — 条件付きで開始可。**

Stack C runtime wiring（config + live post-session shadows + Discord）は静的に整合。明日は **daily runner 本番コマンド**（上記3フラグ必須）で起動すること。

## 開始前に直すべき FAIL

**なし**（コード変更は要求されていない。FAIL 項目なし。）

## 注意すべき WARN

1. **コマンド必須フラグ:** `--exit-policy-shadow trailing-mfe` と `--enable-intraday-refresh` を省略すると Stack C ではない config / universe になる。
2. **push-replay:** 場外 smoke では forward shadow が走らない（live 本番のみ）。
3. **day_count=9:** 明日で 10 日目になっても `final_equity > starting_equity` 等を満たさなければ adopt_not_allowed のまま（research observe 継続）。
4. **Webhook:** daily runner preflight が Discord env を検証 — `.env` に NOTIFY / CAP_BLOCKED URL を設定すること。
5. **YAML コメント:** `trailing_mfe_shadow.yaml` 行7コメントは「>=4」と記載あるが実値は `entry_score_v2_min: 3`（コメントのみの齟齬）。

## 変更なし確認

| Layer | Changed |
| --- | --- |
| Runtime | **No** |
| Universe | **No** |
| Entry | **No** |
| Exit | **No** |
| YAML | **No** |

---

## Reference commands (pre-open)

```bash
# Optional: Phase317 preflight
python kabu_native/scripts/run_phase317_tomorrow_paper_trade_preflight.py

# Production day (Stack C)
python kabu_native/scripts/run_core10_dynamic40_am_pm_daily_runner.py \
  --universe-mode core10-dynamic40-price-risk-filter-shadow \
  --enable-intraday-refresh \
  --exit-policy-shadow trailing-mfe
```

**Audit artifacts reviewed:** `pilot_runner.py`, shadow auto modules, research loggers, Discord builder/notifier, Stack C YAML, `full_system_development_history.md`, existing `phase273/274/255` report outputs.
