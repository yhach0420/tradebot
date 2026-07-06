#!/usr/bin/env python3
"""Phase630: Runtime Parity CI (manual / preflight-ready).

Runs HEAD_BASELINE and CURRENT push-replays on the Phase629A fixture days
with identical PUSH data, production YAML, and shared vol_liq cache key, then
compares ENTRY/EXIT behavior metrics.

Usage:
    python scripts/check_runtime_parity.py
    python scripts/check_runtime_parity.py --days 2026-06-25
    python scripts/check_runtime_parity.py --baseline-root PATH
    python scripts/check_runtime_parity.py --reuse
    python scripts/check_runtime_parity.py --no-volatile   # show wall-clock noise

Exit codes:
    0  ALL_MATCH=True
    1  ALL_MATCH=False or setup error
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import shutil
import sys
import time
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Optional

SCRIPT = Path(__file__).resolve()
NATIVE_ROOT = SCRIPT.parents[1]
REPO_ROOT = NATIVE_ROOT.parent

for p in (NATIVE_ROOT / "src", REPO_ROOT):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

DAYS = ("2026-06-25", "2026-06-29", "2026-06-30", "2026-07-01")
PROD_YAML = (
    NATIVE_ROOT
    / "configs"
    / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
)
POLL_INTERVAL_SEC = 5.0
RUN_ROOT = NATIVE_ROOT / "results" / "small_paper" / "_phase630"
REPORT_DIR = NATIVE_ROOT / "results" / "reports" / "phase630_runtime_parity_ci"
PHASE630_VERDICT_DONE = "phase630_runtime_parity_ci_done"
PHASE630_VERDICT_FAIL = "phase630_runtime_parity_ci_failed"

# ---------------------------------------------------------------------------
# Volatile field whitelist (excluded from parity compare)
# ---------------------------------------------------------------------------

VOLATILE_SUMMARY_KEYS = frozenset(
    {
        # wall_clock / runtime
        "generated_at",
        "runtime_sec",
        "started_at",
        "ended_at",
        "session_started_at",
        "session_ended_at",
        "push_replay_runtime_sec",
        "eval_latency_ms_p50",
        "eval_latency_ms_p95",
        "eval_latency_ms_max",
        # run_id / output path
        "output_dir",
        "daytrade_suitability_run_session_key",
        # vol_liq cache metadata (thresholds identical when cache key shared)
        "vol_liq_cache_elapsed_sec",
        "vol_liq_cache_seconds_saved",
        "vol_liq_cache_baseline_elapsed_sec",
        "vol_liq_cache_path",
        "vol_liq_cache_hit",
        "vol_liq_cache_status",
        "vol_liq_cache_fallback",
        "vol_liq_cache_fallback_reason",
    }
)

VOLATILE_WHITELIST_ROWS = [
    {"category": "wall_clock", "field": "generated_at", "reason": "run wall clock"},
    {"category": "wall_clock", "field": "runtime_sec", "reason": "run duration"},
    {"category": "wall_clock", "field": "started_at", "reason": "run wall clock"},
    {"category": "wall_clock", "field": "ended_at", "reason": "run wall clock"},
    {"category": "wall_clock", "field": "session_started_at", "reason": "run wall clock"},
    {"category": "wall_clock", "field": "session_ended_at", "reason": "run wall clock"},
    {"category": "wall_clock", "field": "push_replay_runtime_sec", "reason": "run duration"},
    {"category": "wall_clock", "field": "eval_latency_ms_p50", "reason": "CPU timing"},
    {"category": "wall_clock", "field": "eval_latency_ms_p95", "reason": "CPU timing"},
    {"category": "wall_clock", "field": "eval_latency_ms_max", "reason": "CPU timing"},
    {"category": "run_id", "field": "output_dir", "reason": "tag/path differs by design"},
    {
        "category": "run_id",
        "field": "daytrade_suitability_run_session_key",
        "reason": "embeds output_dir session key",
    },
    {
        "category": "vol_liq_cache_metadata",
        "field": "vol_liq_cache_elapsed_sec",
        "reason": "cache build timing",
    },
    {
        "category": "vol_liq_cache_metadata",
        "field": "vol_liq_cache_seconds_saved",
        "reason": "cache build timing",
    },
    {
        "category": "vol_liq_cache_metadata",
        "field": "vol_liq_cache_baseline_elapsed_sec",
        "reason": "cache build timing",
    },
    {
        "category": "vol_liq_cache_metadata",
        "field": "vol_liq_cache_path",
        "reason": "path string only",
    },
    {
        "category": "vol_liq_cache_metadata",
        "field": "vol_liq_cache_hit",
        "reason": "hit vs fallback on sequential runs",
    },
    {
        "category": "vol_liq_cache_metadata",
        "field": "vol_liq_cache_status",
        "reason": "hit vs fallback on sequential runs",
    },
    {
        "category": "vol_liq_cache_metadata",
        "field": "vol_liq_cache_fallback",
        "reason": "hit vs fallback on sequential runs",
    },
    {
        "category": "vol_liq_cache_metadata",
        "field": "vol_liq_cache_fallback_reason",
        "reason": "hit vs fallback on sequential runs",
    },
]

# Core behavior metrics (must match for ALL_MATCH)
CORE_METRIC_KEYS = (
    "candidates",
    "gate_evaluations",
    "pbv2_accepted",
    "or_accepted",
    "accepted_total",
    "exits",
    "pnl_yen_100",
    "reject_reason_counts",
)


def _purge_modules() -> None:
    for mod in list(sys.modules):
        if mod.startswith("small_paper") or mod.startswith("research"):
            del sys.modules[mod]


def _run_replay(*, src_root: Path, staging_dir: Path, day: str, label: str) -> Path:
    _purge_modules()
    for p in (src_root / "kabu_native" / "src", src_root):
        s = str(p)
        if s in sys.path:
            sys.path.remove(s)
    for p in (src_root / "kabu_native" / "src", src_root):
        sys.path.insert(0, str(p))

    from small_paper.config import load_pilot_config
    from small_paper.pilot_runner import run_push_replay_dry_run
    import small_paper.pilot_runner as pr

    print(f"[phase630] REPLAY label={label} day={day} pilot={pr.__file__}", flush=True)
    if not PROD_YAML.is_file():
        raise FileNotFoundError(f"config not found: {PROD_YAML}")
    push_dir = NATIVE_ROOT / "data" / "push_jsonl" / day
    if not push_dir.is_dir():
        raise FileNotFoundError(f"push fixture not found: {push_dir}")

    cfg = replace(
        load_pilot_config(PROD_YAML),
        discord_enabled=False,
        entry_latency_trace_enabled=False,
    )
    if staging_dir.exists():
        shutil.rmtree(staging_dir, ignore_errors=True)
    staging_dir.mkdir(parents=True, exist_ok=True)
    run_push_replay_dry_run(
        cfg,
        push_dir=push_dir,
        output_dir=staging_dir,
        repo_root=REPO_ROOT,
        poll_interval_sec=POLL_INTERVAL_SEC,
        streaming_push_replay=True,
        enable_discord=False,
        write_board_shadow_reports=False,
    )
    return staging_dir


def _copy_out(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst)


def _load_summary(run_dir: Path) -> dict[str, Any]:
    fp = run_dir / "small_paper_summary.json"
    if not fp.is_file():
        raise FileNotFoundError(f"missing summary: {fp}")
    return json.loads(fp.read_text(encoding="utf-8"))


def _extract_core_metrics(summary: Mapping[str, Any]) -> dict[str, Any]:
    """Behavior metrics used for parity (from summary only — no huge events load)."""
    accepted = int(summary.get("accepted_count") or 0)
    or_accepted = int(summary.get("or_count") or summary.get("or_entry_count") or 0)
    pbv2 = summary.get("pbv2_count")
    if pbv2 is None:
        pbv2 = accepted - or_accepted
    reject_reasons = summary.get("reject_reason_counts") or {}
    if not isinstance(reject_reasons, dict):
        reject_reasons = {}
    # stable key order for JSON equality
    reject_reasons = {k: reject_reasons[k] for k in sorted(reject_reasons)}
    exits = summary.get("observer_exit_count_with_pnl")
    if exits is None:
        exits = summary.get("exit_shadow_monitor_trade_count")
    if exits is None:
        exits = accepted
    pnl = summary.get("total_pnl_yen_100")
    if isinstance(pnl, float):
        pnl = round(pnl, 4)
    return {
        "candidates": int(summary.get("candidate_count") or summary.get("gate_evaluations") or 0),
        "gate_evaluations": int(summary.get("gate_evaluations") or 0),
        "pbv2_accepted": int(pbv2),
        "or_accepted": int(or_accepted),
        "accepted_total": accepted,
        "exits": int(exits),
        "pnl_yen_100": pnl,
        "reject_reason_counts": reject_reasons,
    }


def _summary_behavior_diff(
    a: Mapping[str, Any],
    b: Mapping[str, Any],
    *,
    volatile: frozenset[str],
) -> dict[str, Any]:
    diffs: dict[str, Any] = {}
    keys = sorted(set(a) | set(b))
    for k in keys:
        if k in volatile:
            continue
        va, vb = a.get(k), b.get(k)
        if isinstance(va, float) and isinstance(vb, float):
            if round(va, 9) == round(vb, 9):
                continue
        if va != vb:
            diffs[k] = {"HEAD_BASELINE": va, "CURRENT": vb}
    return diffs


def _volatile_only_diff(
    a: Mapping[str, Any],
    b: Mapping[str, Any],
    *,
    volatile: frozenset[str],
) -> dict[str, Any]:
    diffs: dict[str, Any] = {}
    for k in sorted(volatile):
        va, vb = a.get(k), b.get(k)
        if va != vb:
            diffs[k] = {"HEAD_BASELINE": va, "CURRENT": vb}
    return diffs


def compare_day_dirs(
    day: str,
    baseline_dir: Path,
    current_dir: Path,
    *,
    apply_volatile: bool = True,
) -> dict[str, Any]:
    sum_a = _load_summary(baseline_dir)
    sum_b = _load_summary(current_dir)
    volatile = VOLATILE_SUMMARY_KEYS if apply_volatile else frozenset()

    core_a = _extract_core_metrics(sum_a)
    core_b = _extract_core_metrics(sum_b)
    core_diffs = {
        k: {"HEAD_BASELINE": core_a[k], "CURRENT": core_b[k]}
        for k in CORE_METRIC_KEYS
        if core_a.get(k) != core_b.get(k)
    }
    summary_diffs = _summary_behavior_diff(sum_a, sum_b, volatile=volatile)
    volatile_diffs = _volatile_only_diff(sum_a, sum_b, volatile=VOLATILE_SUMMARY_KEYS)

    pos_a = baseline_dir / "small_paper_positions.csv"
    pos_b = current_dir / "small_paper_positions.csv"
    pos_rows_a = _count_csv_rows(pos_a)
    pos_rows_b = _count_csv_rows(pos_b)
    positions_match = pos_rows_a == pos_rows_b

    core_match = not core_diffs
    summary_match = not summary_diffs
    match = core_match and summary_match and positions_match

    return {
        "day": day,
        "match": match,
        "core_metrics_match": core_match,
        "summary_match": summary_match,
        "positions_match": positions_match,
        "positions_rows_baseline": pos_rows_a,
        "positions_rows_current": pos_rows_b,
        "core_metrics_baseline": core_a,
        "core_metrics_current": core_b,
        "core_diffs": core_diffs,
        "summary_diffs": summary_diffs,
        "volatile_diffs_if_not_excluded": volatile_diffs,
        "apply_volatile": apply_volatile,
    }


def _count_csv_rows(fp: Path) -> int:
    if not fp.is_file():
        return -1
    with fp.open(encoding="utf-8", newline="") as f:
        # header + data
        return max(0, sum(1 for _ in f) - 1)


def _write_volatile_whitelist(report_dir: Path) -> Path:
    fp = report_dir / "phase630_volatile_whitelist.csv"
    with fp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["category", "field", "reason"])
        w.writeheader()
        w.writerows(VOLATILE_WHITELIST_ROWS)
    return fp


def _write_by_day_csv(report_dir: Path, day_results: list[dict[str, Any]]) -> Path:
    fp = report_dir / "phase630_parity_by_day.csv"
    rows = []
    for r in day_results:
        mb = r["core_metrics_baseline"]
        mc = r["core_metrics_current"]
        rows.append(
            {
                "day": r["day"],
                "match": r["match"],
                "core_metrics_match": r["core_metrics_match"],
                "summary_match": r["summary_match"],
                "positions_match": r["positions_match"],
                "HEAD_candidates": mb["candidates"],
                "CURRENT_candidates": mc["candidates"],
                "HEAD_gate_evaluations": mb["gate_evaluations"],
                "CURRENT_gate_evaluations": mc["gate_evaluations"],
                "HEAD_pbv2_accepted": mb["pbv2_accepted"],
                "CURRENT_pbv2_accepted": mc["pbv2_accepted"],
                "HEAD_or_accepted": mb["or_accepted"],
                "CURRENT_or_accepted": mc["or_accepted"],
                "HEAD_accepted_total": mb["accepted_total"],
                "CURRENT_accepted_total": mc["accepted_total"],
                "HEAD_exits": mb["exits"],
                "CURRENT_exits": mc["exits"],
                "HEAD_pnl_yen_100": mb["pnl_yen_100"],
                "CURRENT_pnl_yen_100": mc["pnl_yen_100"],
            }
        )
    with fp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["day", "match"])
        w.writeheader()
        w.writerows(rows)
    return fp


def _write_diff_gz(report_dir: Path, day_results: list[dict[str, Any]], *, all_match: bool) -> Path:
    fp = report_dir / "phase630_diff_if_failed.csv.gz"
    rows: list[dict[str, str]] = []
    if not all_match:
        for r in day_results:
            if r["match"]:
                continue
            day = r["day"]
            for metric, pair in (r.get("core_diffs") or {}).items():
                rows.append(
                    {
                        "day": day,
                        "section": "core_metrics",
                        "field": metric,
                        "HEAD_BASELINE": json.dumps(pair["HEAD_BASELINE"], ensure_ascii=False),
                        "CURRENT": json.dumps(pair["CURRENT"], ensure_ascii=False),
                    }
                )
            for field, pair in (r.get("summary_diffs") or {}).items():
                rows.append(
                    {
                        "day": day,
                        "section": "summary",
                        "field": field,
                        "HEAD_BASELINE": json.dumps(pair["HEAD_BASELINE"], ensure_ascii=False),
                        "CURRENT": json.dumps(pair["CURRENT"], ensure_ascii=False),
                    }
                )
            if not r.get("positions_match"):
                rows.append(
                    {
                        "day": day,
                        "section": "positions",
                        "field": "row_count",
                        "HEAD_BASELINE": str(r.get("positions_rows_baseline")),
                        "CURRENT": str(r.get("positions_rows_current")),
                    }
                )
    # Always write the file (empty header-only when pass — no huge payload)
    with gzip.open(fp, "wt", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f, fieldnames=["day", "section", "field", "HEAD_BASELINE", "CURRENT"]
        )
        w.writeheader()
        w.writerows(rows)
    return fp


def _dir_size_mb(path: Path) -> float:
    if not path.exists():
        return 0.0
    total = 0
    for fp in path.rglob("*"):
        if fp.is_file():
            try:
                total += fp.stat().st_size
            except OSError:
                pass
    return round(total / (1024 * 1024), 2)


def run_parity(
    *,
    days: list[str],
    baseline_root: Path,
    current_root: Path,
    reuse: bool,
    apply_volatile: bool,
) -> dict[str, Any]:
    t0 = time.monotonic()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    RUN_ROOT.mkdir(parents=True, exist_ok=True)

    day_results: list[dict[str, Any]] = []
    all_match = True
    for day in days:
        day_key = day.replace("-", "")
        staging = RUN_ROOT / "staging" / day_key
        baseline_out = RUN_ROOT / "head_baseline" / day_key
        current_out = RUN_ROOT / "current" / day_key

        need_run = not (
            reuse
            and (baseline_out / "small_paper_summary.json").is_file()
            and (current_out / "small_paper_summary.json").is_file()
        )
        if need_run:
            # Shared staging path => identical run_session_key / vol_liq cache.
            _run_replay(
                src_root=baseline_root,
                staging_dir=staging,
                day=day,
                label="HEAD_BASELINE",
            )
            _copy_out(staging, baseline_out)
            _run_replay(
                src_root=current_root,
                staging_dir=staging,
                day=day,
                label="CURRENT",
            )
            _copy_out(staging, current_out)
        else:
            print(f"[phase630] REUSE day={day}", flush=True)

        r = compare_day_dirs(
            day, baseline_out, current_out, apply_volatile=apply_volatile
        )
        day_results.append(r)
        all_match = all_match and bool(r["match"])
        print(
            f"[phase630] {day}: match={r['match']} "
            f"accepted={r['core_metrics_baseline']['accepted_total']}/"
            f"{r['core_metrics_current']['accepted_total']}",
            flush=True,
        )

    elapsed_sec = round(time.monotonic() - t0, 1)
    # Preserve full-run timing when --reuse only re-compares existing outputs.
    if reuse:
        prev_report = REPORT_DIR / "phase630_report.json"
        if prev_report.is_file():
            try:
                prev = json.loads(prev_report.read_text(encoding="utf-8"))
                prev_elapsed = float((prev.get("answers") or {}).get("6_elapsed_sec") or 0)
                if prev_elapsed > elapsed_sec:
                    elapsed_sec = prev_elapsed
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                pass
    disk_mb = _dir_size_mb(RUN_ROOT)

    # Volatile-noise probe (same outputs, no exclusion) for the report Q2.
    volatile_noise: dict[str, Any] = {}
    for r in day_results:
        day = r["day"]
        day_key = day.replace("-", "")
        probe = compare_day_dirs(
            day,
            RUN_ROOT / "head_baseline" / day_key,
            RUN_ROOT / "current" / day_key,
            apply_volatile=False,
        )
        volatile_noise[day] = {
            "match_without_volatile_exclusion": probe["match"],
            "summary_diff_keys": sorted(probe["summary_diffs"].keys()),
            "volatile_field_diffs": sorted(probe["volatile_diffs_if_not_excluded"].keys()),
        }

    _write_volatile_whitelist(REPORT_DIR)
    _write_by_day_csv(REPORT_DIR, day_results)
    _write_diff_gz(REPORT_DIR, day_results, all_match=all_match)

    parity_summary = {
        "phase": "phase630_runtime_parity_ci",
        "all_match": all_match,
        "apply_volatile": apply_volatile,
        "days": [r["day"] for r in day_results],
        "day_results": [
            {
                "day": r["day"],
                "match": r["match"],
                "core_metrics_match": r["core_metrics_match"],
                "summary_match": r["summary_match"],
                "positions_match": r["positions_match"],
                "core_metrics_baseline": r["core_metrics_baseline"],
                "core_metrics_current": r["core_metrics_current"],
                "core_diffs": r["core_diffs"],
                "summary_diff_keys": sorted(r["summary_diffs"].keys()),
            }
            for r in day_results
        ],
        "volatile_noise_probe": volatile_noise,
        "elapsed_sec": elapsed_sec,
        "disk_usage_mb": disk_mb,
        "baseline_root": str(baseline_root),
        "current_root": str(current_root),
        "reuse": reuse,
    }
    (REPORT_DIR / "phase630_parity_summary.json").write_text(
        json.dumps(parity_summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    report = {
        "phase": "phase630_runtime_parity_ci",
        "verdict": PHASE630_VERDICT_DONE if all_match else PHASE630_VERDICT_FAIL,
        "all_match": all_match,
        "exit_code": 0 if all_match else 1,
        "answers": {
            "1_all_days_match": all_match,
            "2_volatile_noise_without_exclusion": {
                day: v["volatile_field_diffs"] or v["summary_diff_keys"]
                for day, v in volatile_noise.items()
            },
            "3_diff_after_volatile_exclusion": not all_match,
            "4_exit_code": 0 if all_match else 1,
            "5_preflight_ready": all_match,
            "6_elapsed_sec": elapsed_sec,
            "6_disk_usage_mb": disk_mb,
            "7_rollback": (
                "Remove scripts/check_runtime_parity.py, "
                "docs/operations/phase630_runtime_parity_ci.md, "
                "results/reports/phase630_runtime_parity_ci/, "
                "results/small_paper/_phase630/. No ENTRY/EXIT logic was changed."
            ),
        },
        "artifacts": {
            "parity_summary": str(REPORT_DIR / "phase630_parity_summary.json"),
            "parity_by_day": str(REPORT_DIR / "phase630_parity_by_day.csv"),
            "diff_if_failed": str(REPORT_DIR / "phase630_diff_if_failed.csv.gz"),
            "volatile_whitelist": str(REPORT_DIR / "phase630_volatile_whitelist.csv"),
            "report": str(REPORT_DIR / "phase630_report.json"),
            "docs": str(NATIVE_ROOT / "docs" / "operations" / "phase630_runtime_parity_ci.md"),
        },
        "parity_summary": parity_summary,
    }
    (REPORT_DIR / "phase630_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[phase630] ALL_MATCH={all_match} elapsed_sec={elapsed_sec} disk_mb={disk_mb}", flush=True)
    print(f"[phase630] report -> {REPORT_DIR / 'phase630_report.json'}", flush=True)
    return report


def _write_docs() -> None:
    docs = NATIVE_ROOT / "docs" / "operations" / "phase630_runtime_parity_ci.md"
    docs.parent.mkdir(parents=True, exist_ok=True)
    docs.write_text(
        """# Phase630: Runtime Parity CI

## Purpose

Automatically detect unintended ENTRY/EXIT behavior changes after refactors or
feature work, using the Phase629A parity method (same PUSH fixtures, same
production YAML, shared vol_liq cache key, market-time scan clock).

## Manual command

```bash
python scripts/check_runtime_parity.py
```

Options:

| Flag | Meaning |
|---|---|
| `--days 2026-06-25 ...` | Subset of fixture days (default: 6/25, 6/29, 6/30, 7/1) |
| `--baseline-root PATH` | Source tree for HEAD_BASELINE (default: current repo) |
| `--current-root PATH` | Source tree for CURRENT (default: current repo) |
| `--reuse` | Reuse existing `_phase630/head_baseline` and `current` outputs |
| `--no-volatile` | Do **not** exclude volatile fields (expect wall-clock noise) |

Exit codes: `0` = ALL_MATCH, `1` = mismatch or setup error.

## What is compared

From `small_paper_summary.json` (no full events.jsonl load):

- candidates (`candidate_count`)
- gate_evaluations
- PBv2 accepted (`pbv2_count`)
- OR accepted (`or_count` / `or_entry_count`)
- accepted total (`accepted_count`)
- exits (`observer_exit_count_with_pnl`)
- pnl_yen_100 (`total_pnl_yen_100`)
- reject reason counts (`reject_reason_counts`)
- remaining summary metrics except the volatile whitelist
- positions.csv row count

## Volatile whitelist

See `results/reports/phase630_runtime_parity_ci/phase630_volatile_whitelist.csv`.

Categories:

- wall_clock / runtime timestamps and latency percentiles
- run_id / output_dir / session key
- vol_liq cache metadata (hit/path/timing; thresholds stay identical via shared staging key)

## Artifacts

`results/reports/phase630_runtime_parity_ci/`

- `phase630_parity_summary.json`
- `phase630_parity_by_day.csv`
- `phase630_diff_if_failed.csv.gz` (header-only when pass; compact diffs when fail)
- `phase630_volatile_whitelist.csv`
- `phase630_report.json`

Replay outputs (large): `results/small_paper/_phase630/{head_baseline,current,staging}/`

## Preflight

Optional. Do **not** wire into `run_paper_trade.bat` until a few manual green runs
are confirmed. When ready:

```bat
python scripts\\check_runtime_parity.py --reuse
```

Use `--reuse` only when outputs are known fresh; otherwise omit it for a full check.

## Rollback

Delete:

- `scripts/check_runtime_parity.py`
- `docs/operations/phase630_runtime_parity_ci.md`
- `results/reports/phase630_runtime_parity_ci/`
- `results/small_paper/_phase630/`

No ENTRY/EXIT/PBv2/OR/Freshness/YAML logic is modified by Phase630.
""",
        encoding="utf-8",
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Phase630 Runtime Parity CI")
    parser.add_argument(
        "--days",
        nargs="*",
        default=list(DAYS),
        help="Fixture days (default: 2026-06-25 2026-06-29 2026-06-30 2026-07-01)",
    )
    parser.add_argument(
        "--baseline-root",
        type=Path,
        default=REPO_ROOT,
        help="Repo root for HEAD_BASELINE source (default: current repo)",
    )
    parser.add_argument(
        "--current-root",
        type=Path,
        default=REPO_ROOT,
        help="Repo root for CURRENT source (default: current repo)",
    )
    parser.add_argument(
        "--reuse",
        action="store_true",
        help="Reuse existing _phase630 replay outputs",
    )
    parser.add_argument(
        "--no-volatile",
        action="store_true",
        help="Do not exclude volatile fields",
    )
    args = parser.parse_args(argv)

    _write_docs()
    try:
        report = run_parity(
            days=list(args.days),
            baseline_root=args.baseline_root.resolve(),
            current_root=args.current_root.resolve(),
            reuse=bool(args.reuse),
            apply_volatile=not bool(args.no_volatile),
        )
    except Exception as exc:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        fail = {
            "phase": "phase630_runtime_parity_ci",
            "verdict": PHASE630_VERDICT_FAIL,
            "all_match": False,
            "error": str(exc),
            "exit_code": 1,
        }
        (REPORT_DIR / "phase630_report.json").write_text(
            json.dumps(fail, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[phase630] ERROR: {exc}", flush=True)
        return 1

    return int(report.get("exit_code", 1))


if __name__ == "__main__":
    raise SystemExit(main())
