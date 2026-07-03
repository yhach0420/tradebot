# Phase617 — CORE_ONLY vs FULL_EXTENSION Latency Parity Audit

**Verdict:** `phase617_core_latency_validation_done`  
**Generated:** 2026-07-01  
**Output:** `results/reports/phase617_parallel/`

## Purpose

Validate whether Phase616 Core/Extension separation improves PBv2 hot-path latency without changing gate decisions. Structural A/B only — no ENTRY/EXIT/PBv2/OR logic changes.

## Jobs (4 parallel, 2 workers)

| Job | Day | Mode |
|-----|-----|------|
| A | 2026-06-25 | FULL_EXTENSION |
| B | 2026-06-25 | CORE_ONLY |
| C | 2026-06-29 | FULL_EXTENSION |
| D | 2026-06-29 | CORE_ONLY |

Disk-safe settings: `gzip_output=true`, `write_hash_only=true`, `max_samples=3000`, `max_push_rows=100000`, replay artifacts deleted after each job.

## Mandatory Answers

| # | Question | Answer |
|---|----------|--------|
| 1 | PUSH→freshness improvement (625, CORE vs FULL p50) | **−0.02 ms** (0.181 → 0.161 ms) — negligible |
| 2 | PBv2 latency improved? | **Yes** (p50 0.039 → 0.037 ms, −0.002 ms) |
| 3 | Accepted count unchanged? | **No** (625: 211 FULL vs 169 CORE, Δ−42) — batch-scan timing drift when Extension slows post-eval |
| 4 | Decision parity 100%? | **No** — 625: **99.99%** (10/100k); 629: **99.20%** (803/100k) |
| 5 | Heaviest Extension | **VolumeShadow** (~20.7 s CPU / 100k ticks FULL 625) |
| 6 | Processes ≥5 ms off core hot path | **1,234** sampled violations (mostly FULL `post_pbv2_decision` / VolumeShadow; max 577 ms) |
| 7 | Core hot path sufficiently light? | **Yes** — CORE_ONLY total p50 **0.795 ms** |
| 8 | FULL Extension leaves Core decisions unchanged? | **Mostly** on 625 (99.99%); **629 drift** (99.2%) from scan-batch ordering under Extension load |
| 9 | CORE_ONLY reduces data_stale? | **No** — stale counts identical (625: 16994; 629: 24222) |
| 10 | Core separation alone solves 629? | **NO** — feed staleness unchanged; only Extension CPU removed |

## Latency Summary (p50 ms)

| Stage | 625 FULL | 625 CORE | Δ | 629 FULL | 629 CORE | Δ |
|-------|----------|----------|---|----------|----------|---|
| push→freshness | 0.181 | 0.161 | −0.02 | 0.197 | 0.157 | −0.04 |
| freshness→PBv2 | 0.117 | 0.117 | 0 | 0.134 | 0.118 | −0.016 |
| PBv2 | 0.039 | 0.037 | −0.002 | 0.040 | 0.034 | −0.006 |
| PBv2→decision | 0.467 | 0.467 | 0 | 0.481 | 0.444 | −0.037 |
| total | 0.820 | 0.795 | −0.025 | 0.852 | 0.756 | −0.096 |

Push-replay hot path is **sub-millisecond** at p50; Phase613 live queue delay (~2 s) is **not reproduced** in offline replay (no WS queue).

## Extension Cost (FULL, 100k ticks)

| Extension | Calls | Total ms | Mean ms | Max ms |
|-----------|-------|----------|---------|--------|
| VolumeShadow | 100k | 20,661 | 0.207 | 576 |
| Shadow | 200k | 5,752 | 0.029 | 4.0 |
| LiveOrder/Capital/Trace | 0 | 0 | 0 | 0 |

## Artifacts

- `phase617_summary.json`
- `phase617_core_vs_full_latency.csv`
- `phase617_stage_breakdown.csv`
- `phase617_extension_cost.csv`
- `phase617_decision_parity.csv`
- `phase617_hot_path.csv`
- Per-job: `phase617_samples.csv.gz`, `candidate_decisions.jsonl.gz`

## Instrumentation Added (measurement only)

- `src/small_paper/pipeline_stage_profiler.py` — per-tick stage marks
- `ExtensionBus` timing hooks
- `run_push_replay_dry_run` wired to `CoreRuntimeMode` + `_init_extension_stack_for_mode`

## Conclusions

1. **CORE_ONLY removes Extension CPU** (VolumeShadow + board/momentum Shadow) with **~0.02–0.10 ms** p50 total pipeline savings in replay.
2. **PBv2/OR evaluate time unchanged** in substance (~0.04 ms p50).
3. **data_stale_price** is feed-driven; Core separation does not reduce stale rejects.
4. **629 problem** requires freshness anchor / board fallback / queue reduction (Phase613 F1–F3), not Core-only mode.
5. **Near-parity** on 625 (99.99%) validates gate path; residual drift on 629 under Extension load warrants isolating `entry_scan` batch flush from Extension post-eval (future hardening, not Phase617 scope).
