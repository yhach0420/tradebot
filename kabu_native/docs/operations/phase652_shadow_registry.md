# Phase652: Shadow Registry and Dashboard

Audit-only inventory of runtime shadows, forward-shadow auto jobs, and research counterfactuals.

**Constraints:** No ENTRY/EXIT/PBv2/OR logic changes. No YAML threshold changes. No real orders.

## Run

```bash
python scripts/run_phase652_shadow_registry.py
python -m pytest tests/test_phase652_shadow_registry.py -q
```

## Artifacts

```
results/reports/phase652_shadow_registry/
  phase652_shadow_registry.csv      # master registry
  phase652_shadow_dashboard.json    # aggregated KPIs from session summaries
  phase652_shadow_summary.csv       # per-session per-shadow metrics
  phase652_report.json              # mandatory answers
```

## Registry columns

`shadow_id`, `phase`, `name`, `category`, `runtime_or_research`, `entry_or_exit`, `target_pool`, `enabled`, `config_keys`, `implementation_files`, `summary_fields`, `discord_section`, `status`, `decision`, `last_evaluated_date`, `mainline_effect`, `owner_layer`, `risk_if_left_enabled`, `recommended_next_action`

### Status values

| Status | Meaning |
|--------|---------|
| `running` | Active in production YAML or always-on runtime |
| `disabled` | Config off or rolled back |
| `research_only` | Batch counterfactual only |
| `adopted` | Promoted to mainline (guard may still log shadow fields) |
| `deprecated` | Rollback candidate |
| `unknown` | Could not determine from config/docs |

## Discord operator sections (Phase637 layout)

| Section | Shadows |
|---------|---------|
| Rise5 Shadow Summary | `pbv2_rise5_shadow` |
| Flat-band Shadow Summary | `pbv2_flat_band_shadow` |
| Shadow Summary | Rise5, Flat-band, PullbackMisread, BoardDynamic, EXIT T2·T3 |
| Research blocks | SectorHeat, RiskAware, Equity Dynamic Stop, LiveConfig, Boundary, PostEntry, Classic Momentum |

## KPI to watch (5–10 sessions)

| Shadow | KPI | Promotion threshold |
|--------|-----|---------------------|
| `pbv2_flat_band_shadow` | `net_effect_yen`, blocked_winners/losers | net_effect > 0, blocked_losers > blocked_winners |
| `pbv2_rise5_shadow` | `net_effect_yen`, overlap with flat-band | consistent positive net; no OR impact |
| `exit_shadow_monitor_t2_t3` | `shadow_exit_t3_delta` | T3 improves loss days without hurting profit days |
| `pullback_misread_guard_shadow` | `delta_yen` vs high_drift production | shadow beats production pullback path |
| Forward shadows | `trade_overlap_days`, `adopt_not_allowed` | min 10 days + adoption_review_allowed |

## Rules for adding new shadows

1. **Logging-only** at first — must not block ENTRY/EXIT without adoption phase.
2. **Config rollback key** required (`*_enabled: false`).
3. **Register here** before merge (`phase652_shadow_registry.csv` row).
4. **Discord** only if operator has a measurable daily KPI.
5. **5+ forward sessions** before mainline candidacy.
6. **Config pin** update if production YAML keys change.

## Mainline promotion conditions

- Counterfactual or runtime shadow shows **positive net_effect** over ≥5 sessions.
- **No parity regression** (accepted count, observer exits unchanged).
- **Rollback documented** in ops doc + YAML comment.
- Phase adoption doc with `verdict=*_adopted` or explicit HOLD→shadow path completed.

## Deprecation conditions

- Rolled back from mainline (e.g. `stop_low_mfe_guard`).
- Superseded by newer shadow (Phase634 → Phase635).
- `adoption_review_allowed=False` with negative forward delta (Boundary).
- Extension shadow with no KPI movement over 20 sessions.

## Verdict

`phase652_shadow_registry_done`
