# Phase506 — Live Pipeline Preflight

**Verdict:** `preflight_ready`  
**Purpose:** Block paper trade start if the Live PUSH ENTRY pipeline cannot complete without exceptions (Phase505 `total_seconds` class of bugs).

---

## What it checks

Uses **float epoch-second** `symbol_price_ring` (same as live `extended_entry_shadow` / `tick_ts_from_payload`) and runs:

1. Push payload parse (`_symbol_from_push`, `tick_ts_from_payload`)
2. Price ring update (`append_price_tick` probe + session ring)
3. Board/price freshness (`compute_entry_freshness`, `check_entry_data_freshness`)
4. Feature bridge (`LiveFeatureBridge.update`)
5. `_enrich_trade_for_entry_guards` (includes Phase503 `classic_late_chase_rsi_guard`)
6. `ExposureGate.evaluate_entry` via `_evaluate_gate_entry`

Discord / summary build is **not** included.

---

## Cases

| Case | Guard | Expect |
|------|-------|--------|
| `normal_candidate` | enabled | `rsi14` computed, `late_chase_flag=false`, not `classic_late_chase_rsi_over80`, `full_exposure_gate_reached=true` |
| `late_chase_rsi_block` | enabled | `classic_late_chase_rsi_over80` reject, RSI ≥ 80 |
| `late_chase_guard_disabled` | disabled | same enrich path, **no** `classic_late_chase_rsi_over80` |

---

## Run manually

```bash
cd kabu_native
set PYTHONPATH=src
python scripts/check_live_pipeline_preflight.py
```

Exit `0` → `[PREFLIGHT] live pipeline ok`  
Exit `1` → `[PREFLIGHT] live pipeline failed`

Optional: `--json` for full report.

Default config: `configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml` (same stack as `run_paper_trade.bat`).

---

## Paper trade integration

`run_paper_trade.bat` runs preflight **before** the daily runner. Non-zero exit aborts paper trade.

---

## Mandatory answers

| Question | Answer |
|----------|--------|
| Live float timestamp? | **Yes** — ring `(float, float)`, `tick_ts_from_payload` → `float` |
| ENTRY reaches end? | **Yes** — `full_exposure_gate_reached=true` on all cases |
| Phase503 guard safe on Live types? | **Yes** — enrich + RSI resample complete without `total_seconds` error |
| bat blocks on failure? | **Yes** — `exit /b 1` if preflight fails |
| Safe to start tomorrow with preflight? | **Yes** — after Phase505 fix + this gate |

---

## Tests

```bash
python -m pytest tests/test_phase503_classic_late_chase_rsi_guard.py tests/test_phase506_live_pipeline_preflight.py -q
```

---

## Files

- `src/small_paper/live_pipeline_preflight.py` — core logic
- `scripts/check_live_pipeline_preflight.py` — CLI
- `tests/test_phase506_live_pipeline_preflight.py`
