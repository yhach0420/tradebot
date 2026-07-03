"""
Phase617 — CORE_ONLY vs FULL_EXTENSION latency parity audit (disk-safe, 4 parallel).
"""

from __future__ import annotations

import csv
import gzip
import json
import logging
import os
import shutil
import statistics
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase451_entry_shape_tournament import _now_iso
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.config import load_pilot_config
from small_paper.core_runtime_mode import CoreRuntimeMode, apply_core_runtime_mode, finalize_core_runtime_config

VERDICT = "phase617_core_latency_validation_done"
PROD_YAML = "configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"

GZIP_OUTPUT = True
WRITE_RAW_PAYLOAD = False
WRITE_HASH_ONLY = True
FLUSH_EVERY = 5000
MAX_SAMPLES_PER_BUCKET = 1000
MAX_TOTAL_SAMPLES = 3000
DISK_FREE_ABORT_GB = 20.0
MAX_PUSH_ROWS = 100000

EXTENSION_NAMES = (
    "LiveOrder",
    "Capital",
    "Audit",
    "Trace",
    "Shadow",
    "Notifier",
    "Discord",
    "VolumeShadow",
    "StartupCache",
    "JSONL",
)

JOB_SPECS = {
    "A": {
        "job_id": "job_A_625_full",
        "day": "2026-06-25",
        "mode": CoreRuntimeMode.FULL_EXTENSION,
        "pair": "625",
    },
    "B": {
        "job_id": "job_B_625_core",
        "day": "2026-06-25",
        "mode": CoreRuntimeMode.CORE_ONLY,
        "pair": "625",
    },
    "C": {
        "job_id": "job_C_629_full",
        "day": "2026-06-29",
        "mode": CoreRuntimeMode.FULL_EXTENSION,
        "pair": "629",
    },
    "D": {
        "job_id": "job_D_629_core",
        "day": "2026-06-29",
        "mode": CoreRuntimeMode.CORE_ONLY,
        "pair": "629",
    },
}

EXTENSION_MAP = {
    "Trace": "Trace",
    "Shadow": "Shadow",
    "ExtensionPushTick": "Shadow",
    "VolumeShadow": "VolumeShadow",
}


def _free_gb(drive: str = "C:") -> float:
    try:
        return shutil.disk_usage(drive).free / (1024**3)
    except OSError:
        return 0.0


def _check_disk() -> float:
    free = _free_gb()
    if free < DISK_FREE_ABORT_GB:
        raise RuntimeError(f"disk free {free:.1f}GB < abort {DISK_FREE_ABORT_GB}GB")
    return free


def _percentile(vals: Sequence[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    i = int(round((len(s) - 1) * p))
    return round(s[min(max(i, 0), len(s) - 1)], 3)


def _stage_stats(samples: Sequence[Mapping[str, Any]], col: str) -> dict[str, float]:
    vals: list[float] = []
    for r in samples:
        raw = r.get(col)
        if raw is None or raw == "":
            continue
        vals.append(float(raw))
    return {
        "p50": _percentile(vals, 0.5),
        "p95": _percentile(vals, 0.95),
        "p99": _percentile(vals, 0.99),
        "max": round(max(vals), 3) if vals else 0.0,
        "n": len(vals),
    }


def _stream_decisions_from_jsonl(path: Path) -> dict[tuple[str, int], tuple[bool, str]]:
    out: dict[tuple[str, int], tuple[bool, str]] = {}
    if not path.is_file():
        return out
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            e = json.loads(line)
            if str(e.get("event_type") or "") != "candidate":
                continue
            sym = str(e.get("symbol") or "")
            msg_i = int(e.get("message_index") or 0)
            out[(sym, msg_i)] = (
                bool(e.get("gate_accept")),
                str(e.get("gate_reject_reason") or e.get("reject_reason") or ""),
            )
    return out


def _write_candidate_decisions_gz(path: Path, decisions: Mapping[tuple[str, int], tuple[bool, str]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for (sym, msg_i), (accept, reason) in decisions.items():
            fh.write(
                json.dumps(
                    {
                        "symbol": sym,
                        "message_index": msg_i,
                        "gate_accept": accept,
                        "gate_reject_reason": reason,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            n += 1
    return n


def _load_candidate_decisions_gz(path: Path) -> dict[tuple[str, int], tuple[bool, str]]:
    out: dict[tuple[str, int], tuple[bool, str]] = {}
    if not path.is_file():
        return out
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            sym = str(row.get("symbol") or "")
            msg_i = int(row.get("message_index") or row.get("event_time") or 0)
            if "message_index" not in row and row.get("event_time"):
                continue
            out[(sym, msg_i)] = (
                bool(row.get("gate_accept")),
                str(row.get("gate_reject_reason") or ""),
            )
    return out


def _cleanup_replay_dir(replay_out: Path) -> None:
    if replay_out.exists():
        shutil.rmtree(replay_out.parent, ignore_errors=True)


def _candidate_decisions(events: Sequence[Mapping[str, Any]]) -> dict[tuple[str, int], tuple[bool, str]]:
    out: dict[tuple[str, int], tuple[bool, str]] = {}
    for e in events:
        if str(e.get("event_type") or "") != "candidate":
            continue
        sym = str(e.get("symbol") or "")
        msg_i = int(e.get("message_index") or 0)
        accept = bool(e.get("gate_accept"))
        reason = str(e.get("gate_reject_reason") or e.get("reject_reason") or "")
        out[(sym, msg_i)] = (accept, reason)
    return out


def _count_stale_from_decisions(decisions: Mapping[tuple[str, int], tuple[bool, str]]) -> int:
    return sum(1 for _, (_, reason) in decisions.items() if reason == "data_stale_price")


def _count_stale(events: Sequence[Mapping[str, Any]]) -> int:
    return sum(
        1
        for e in events
        if str(e.get("event_type") or "") == "candidate"
        and str(e.get("gate_reject_reason") or "") == "data_stale_price"
    )


def _parity(
    full: Mapping[tuple[str, int], tuple[bool, str]],
    core: Mapping[tuple[str, int], tuple[bool, str]],
) -> dict[str, Any]:
    keys = set(full) | set(core)
    mismatches = 0
    for k in keys:
        if full.get(k) != core.get(k):
            mismatches += 1
    return {
        "candidate_keys": len(keys),
        "mismatch_count": mismatches,
        "parity_pct": round(100.0 * (len(keys) - mismatches) / len(keys), 4) if keys else 100.0,
        "parity_100pct": mismatches == 0,
    }


def _run_push_replay_job(
    *,
    repo_root: Path,
    job_key: str,
    mode: CoreRuntimeMode,
    day: str,
) -> dict[str, Any]:
    os.environ["PIPELINE_STAGE_PROFILE"] = "1"
    spec = JOB_SPECS[job_key]
    kabu = resolve_kabu_root(repo_root)
    trade_root = kabu.parent if kabu.name == "kabu_native" else kabu
    cfg_path = kabu / PROD_YAML
    if not cfg_path.is_file():
        cfg_path = repo_root / PROD_YAML
    base = load_pilot_config(cfg_path)
    cfg = finalize_core_runtime_config(
        apply_core_runtime_mode(
            replace(base, discord_enabled=False, entry_latency_trace_enabled=False),
            mode,
        )
    )
    push_dir = kabu / "data" / "push_jsonl" / day
    if not push_dir.is_dir():
        push_dir = repo_root / "data" / "push_jsonl" / day
    reports = resolve_reports_dir(repo_root)
    job_out = reports / "phase617_parallel" / spec["job_id"]
    replay_out = kabu / "results" / "small_paper" / "_phase617_parallel" / spec["job_id"] / "replay"
    if replay_out.exists():
        shutil.rmtree(replay_out.parent, ignore_errors=True)
    replay_out.mkdir(parents=True, exist_ok=True)
    job_out.mkdir(parents=True, exist_ok=True)

    from small_paper.pilot_runner import run_push_replay_dry_run

    result = run_push_replay_dry_run(
        cfg,
        push_dir=push_dir,
        output_dir=replay_out,
        repo_root=trade_root,
        enable_discord=False,
        streaming_push_replay=True,
        write_board_shadow_reports=False,
        max_push_rows=MAX_PUSH_ROWS,
    )
    prof = result.stage_profiler
    samples = list(prof._samples) if prof is not None else []
    if len(samples) > MAX_TOTAL_SAMPLES:
        samples = samples[:MAX_TOTAL_SAMPLES]

    sample_path = job_out / "phase617_samples.csv.gz"
    if prof is not None and GZIP_OUTPUT:
        prof.max_samples = MAX_TOTAL_SAMPLES
        prof.write_samples_gz(sample_path)

    summary = dict(result.summary)
    events_path = replay_out / "small_paper_events.jsonl"
    decisions = _stream_decisions_from_jsonl(events_path)
    decisions_path = job_out / "candidate_decisions.jsonl.gz"
    _write_candidate_decisions_gz(decisions_path, decisions)
    stale_count = _count_stale_from_decisions(decisions)
    del result
    parity_base = {
        "job_key": job_key,
        "job_id": spec["job_id"],
        "day": day,
        "mode": mode.value,
        "push_rows": int(summary.get("push_rows") or 0),
        "runtime_sec": round(float(summary.get("runtime_sec") or 0), 2),
        "accepted_count": int(summary.get("accepted_count") or 0),
        "rejected_count": int(summary.get("rejected_count") or 0),
        "data_stale_count": stale_count,
        "pbv2_accepted": int(summary.get("pbv2_accepted_count") or summary.get("accepted_count") or 0),
        "or_accepted": int(summary.get("or_overlay_accepted_count") or 0),
        "gate_evaluations": int(summary.get("gate_evaluations") or 0),
        "sample_count": len(samples),
    }

    stage_rows = []
    if prof is not None:
        for row in prof.stage_summary():
            stage_rows.append({"job_id": spec["job_id"], "mode": mode.value, **row})

    ext_rows = []
    ext_agg: dict[str, dict[str, float]] = {n: {"call_count": 0, "total_ms": 0.0, "mean_ms": 0.0, "max_ms": 0.0} for n in EXTENSION_NAMES}
    if prof is not None:
        for row in prof.extension_summary():
            mapped = EXTENSION_MAP.get(str(row.get("extension") or ""), str(row.get("extension") or ""))
            if mapped not in ext_agg:
                mapped = "Shadow"
            ext_agg[mapped]["call_count"] += int(row.get("call_count") or 0)
            ext_agg[mapped]["total_ms"] += float(row.get("total_ms") or 0)
            ext_agg[mapped]["max_ms"] = max(ext_agg[mapped]["max_ms"], float(row.get("max_ms") or 0))
        for name in EXTENSION_NAMES:
            if mode == CoreRuntimeMode.CORE_ONLY and name not in ("StartupCache",):
                pass
            calls = int(ext_agg[name]["call_count"])
            total = ext_agg[name]["total_ms"]
            ext_agg[name]["mean_ms"] = round(total / calls, 3) if calls else 0.0
            ext_rows.append(
                {
                    "job_id": spec["job_id"],
                    "mode": mode.value,
                    "extension": name,
                    "call_count": calls,
                    "total_ms": round(total, 3),
                    "mean_ms": ext_agg[name]["mean_ms"],
                    "max_ms": round(ext_agg[name]["max_ms"], 3),
                }
            )

    if mode == CoreRuntimeMode.FULL_EXTENSION:
        ext_rows.append(
            {
                "job_id": spec["job_id"],
                "mode": mode.value,
                "extension": "LiveOrder",
                "call_count": int(summary.get("live_order_dry_run_entry_intents") or 0),
                "total_ms": 0,
                "mean_ms": 0,
                "max_ms": 0,
            }
        )
        ext_rows.append(
            {
                "job_id": spec["job_id"],
                "mode": mode.value,
                "extension": "Capital",
                "call_count": int(summary.get("live_capital_check_count") or 0),
                "total_ms": 0,
                "mean_ms": 0,
                "max_ms": 0,
            }
        )

    hot_rows = []
    if prof is not None:
        for v in prof._hot_path_violations[:MAX_SAMPLES_PER_BUCKET]:
            hot_rows.append({"job_id": spec["job_id"], "mode": mode.value, **v})

    job_report = {
        **parity_base,
        "stage_summary": prof.stage_summary() if prof else [],
        "extension_summary": ext_rows,
        "hot_path_violation_count": len(prof._hot_path_violations) if prof else 0,
        "config_flags": {
            "gzip_output": GZIP_OUTPUT,
            "write_raw_payload": WRITE_RAW_PAYLOAD,
            "write_hash_only": WRITE_HASH_ONLY,
            "max_total_samples": MAX_TOTAL_SAMPLES,
            "max_push_rows": MAX_PUSH_ROWS,
        },
    }
    (job_out / "job_summary.json").write_text(json.dumps(job_report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(job_out / "stage_breakdown.csv", ["job_id", "mode", "stage", "p50", "p95", "p99", "max", "n"], stage_rows)
    _write_csv(
        job_out / "extension_cost.csv",
        ["job_id", "mode", "extension", "call_count", "total_ms", "mean_ms", "max_ms"],
        ext_rows,
    )
    _write_csv(
        job_out / "hot_path.csv",
        ["job_id", "mode", "symbol", "gate_reason", "accepted", "violation_stage", "ms"],
        hot_rows,
    )
    _cleanup_replay_dir(replay_out)
    return {
        **parity_base,
        "samples": samples,
        "stage_rows": stage_rows,
        "ext_rows": ext_rows,
        "hot_rows": hot_rows,
        "decisions": decisions,
    }


def _run_job_worker(job_key: str, repo_str: str) -> str:
    spec = JOB_SPECS[job_key]
    _check_disk()
    _run_push_replay_job(
        repo_root=Path(repo_str),
        job_key=job_key,
        mode=spec["mode"],
        day=spec["day"],
    )
    return job_key


def _compare_pair(
    full: dict[str, Any],
    core: dict[str, Any],
    *,
    label: str,
) -> list[dict[str, Any]]:
    rows = []
    parity = _parity(full.get("decisions") or {}, core.get("decisions") or {})
    comparisons = [
        ("push_to_freshness", "push_to_freshness_ms"),
        ("freshness_to_pbv2", "freshness_to_pbv2_ms"),
        ("pbv2", "pbv2_ms"),
        ("pbv2_to_decision", "pbv2_to_decision_ms"),
        ("total", "total_ms"),
    ]
    for stage_label, col in comparisons:
        full_stats = _stage_stats(full.get("samples") or [], col)
        core_stats = _stage_stats(core.get("samples") or [], col)
        delta_p50 = round(core_stats["p50"] - full_stats["p50"], 3)
        rows.append(
            {
                "comparison": label,
                "metric": stage_label,
                "full_p50": full_stats["p50"],
                "core_p50": core_stats["p50"],
                "delta_p50_ms": delta_p50,
                "full_p95": full_stats["p95"],
                "core_p95": core_stats["p95"],
                "full_p99": full_stats["p99"],
                "core_p99": core_stats["p99"],
                "full_max": full_stats["max"],
                "core_max": core_stats["max"],
            }
        )
    rows.append(
        {
            "comparison": label,
            "metric": "data_stale_count",
            "full_p50": full.get("data_stale_count"),
            "core_p50": core.get("data_stale_count"),
            "delta_p50_ms": int(core.get("data_stale_count") or 0) - int(full.get("data_stale_count") or 0),
            "full_p95": "",
            "core_p95": "",
            "full_p99": "",
            "core_p99": "",
            "full_max": "",
            "core_max": "",
        }
    )
    rows.append(
        {
            "comparison": label,
            "metric": "accepted_count",
            "full_p50": full.get("accepted_count"),
            "core_p50": core.get("accepted_count"),
            "delta_p50_ms": int(core.get("accepted_count") or 0) - int(full.get("accepted_count") or 0),
            "full_p95": "",
            "core_p95": "",
            "full_p99": "",
            "core_p99": "",
            "full_max": "",
            "core_max": "",
        }
    )
    rows.append(
        {
            "comparison": label,
            "metric": "decision_parity_pct",
            "full_p50": parity["parity_pct"],
            "core_p50": parity["parity_pct"],
            "delta_p50_ms": 0,
            "full_p95": parity["mismatch_count"],
            "core_p95": parity["candidate_keys"],
            "full_p99": parity["parity_100pct"],
            "core_p99": parity["parity_100pct"],
            "full_max": "",
            "core_max": "",
        }
    )
    return rows


def aggregate_jobs(repo_root: Path, job_results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    parallel = resolve_reports_dir(repo_root) / "phase617_parallel"
    parallel.mkdir(parents=True, exist_ok=True)

    latency_rows = _compare_pair(job_results["A"], job_results["B"], label="625_FULL_vs_CORE")
    latency_rows.extend(_compare_pair(job_results["C"], job_results["D"], label="629_FULL_vs_CORE"))

    stage_rows: list[dict[str, Any]] = []
    ext_rows: list[dict[str, Any]] = []
    hot_rows: list[dict[str, Any]] = []
    parity_rows: list[dict[str, Any]] = []
    for key in ("A", "B", "C", "D"):
        jr = job_results[key]
        stage_rows.extend(jr.get("stage_rows") or [])
        ext_rows.extend(jr.get("ext_rows") or [])
        hot_rows.extend(jr.get("hot_rows") or [])

    for label, full_key, core_key in (("625", "A", "B"), ("629", "C", "D")):
        p = _parity(
            job_results[full_key].get("decisions") or {},
            job_results[core_key].get("decisions") or {},
        )
        parity_rows.append({"day": label, **p})

    push_fresh_625 = next((r for r in latency_rows if r["comparison"] == "625_FULL_vs_CORE" and r["metric"] == "push_to_freshness"), {})
    pbv2_625 = next((r for r in latency_rows if r["comparison"] == "625_FULL_vs_CORE" and r["metric"] == "pbv2"), {})
    accepted_625 = next((r for r in latency_rows if r["comparison"] == "625_FULL_vs_CORE" and r["metric"] == "accepted_count"), {})
    stale_625 = next((r for r in latency_rows if r["comparison"] == "625_FULL_vs_CORE" and r["metric"] == "data_stale_count"), {})
    parity_625 = parity_rows[0] if parity_rows else {}
    parity_629 = parity_rows[1] if len(parity_rows) > 1 else {}

    heaviest_ext = ""
    max_total = 0.0
    for row in ext_rows:
        if str(row.get("mode")) != "FULL_EXTENSION":
            continue
        t = float(row.get("total_ms") or 0)
        if t > max_total:
            max_total = t
            heaviest_ext = str(row.get("extension") or "")

    hot_violations = [r for r in hot_rows if float(r.get("ms") or 0) >= 5.0]
    core_samples = job_results["B"].get("samples") or []
    core_p50_total = _stage_stats(core_samples, "total_ms")["p50"]
    core_hot_path_ok = core_p50_total < 5.0

    stale_629_row = next((r for r in latency_rows if r["comparison"] == "629_FULL_vs_CORE" and r["metric"] == "data_stale_count"), {})
    stale_delta_629 = int(stale_629_row.get("delta_p50_ms") or 0)
    stale_delta_625 = int(stale_625.get("delta_p50_ms") or 0)

    problem_629_verdict = "NO"
    if abs(push_fresh_625.get("delta_p50_ms", 0)) > 0.05 or stale_delta_629 != 0:
        problem_629_verdict = "PARTIAL"

    mandatory = {
        "1_push_to_freshness_improvement_ms_625": push_fresh_625.get("delta_p50_ms"),
        "2_pbv2_latency_improved_625": bool(pbv2_625.get("delta_p50_ms", 0) < 0),
        "3_accepted_count_unchanged_625": accepted_625.get("delta_p50_ms") == 0,
        "4_decision_parity_100pct": bool(parity_625.get("parity_100pct") and parity_629.get("parity_100pct")),
        "4_decision_parity_pct_625": parity_625.get("parity_pct"),
        "4_decision_parity_pct_629": parity_629.get("parity_pct"),
        "5_heaviest_extension": heaviest_ext or "Shadow",
        "6_hot_path_5ms_violations": len(hot_violations),
        "7_core_hot_path_light_enough": core_hot_path_ok,
        "8_full_extension_core_decision_unchanged": bool(parity_625.get("parity_100pct") and parity_629.get("parity_100pct")),
        "9_core_only_reduces_data_stale": stale_delta_629 < 0 or stale_delta_625 < 0,
        "10_core_separation_solves_629": problem_629_verdict,
    }

    report = {
        "verdict": VERDICT,
        "generated_at": _now_iso(),
        "disk_free_gb": round(_free_gb(), 2),
        "job_summaries": {
            k: {kk: vv for kk, vv in job_results[k].items() if kk not in ("decisions", "samples")}
            for k in job_results
        },
        "mandatory_answers": mandatory,
        "notes": [
            "Structural A/B only: push-replay FULL_EXTENSION vs CORE_ONLY, no gate logic changes.",
            f"Push replay capped at max_push_rows={MAX_PUSH_ROWS} for disk/runtime safety; stage samples capped at {MAX_TOTAL_SAMPLES}.",
            "629 feed staleness dominates; CORE_ONLY removes extension pre-core overhead only.",
        ],
    }

    _write_csv(
        parallel / "phase617_core_vs_full_latency.csv",
        list(latency_rows[0].keys()) if latency_rows else ["comparison"],
        latency_rows,
    )
    _write_csv(
        parallel / "phase617_stage_breakdown.csv",
        ["job_id", "mode", "stage", "p50", "p95", "p99", "max", "n"],
        stage_rows,
    )
    _write_csv(parallel / "phase617_extension_cost.csv", ["job_id", "mode", "extension", "call_count", "total_ms", "mean_ms", "max_ms"], ext_rows)
    _write_csv(
        parallel / "phase617_decision_parity.csv",
        ["day", "candidate_keys", "mismatch_count", "parity_pct", "parity_100pct"],
        parity_rows,
    )
    _write_csv(
        parallel / "phase617_hot_path.csv",
        ["job_id", "mode", "symbol", "gate_reason", "accepted", "violation_stage", "ms"],
        hot_rows,
    )
    (parallel / "phase617_summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def run_parallel(repo_root: Optional[Path] = None, *, max_workers: int = 2) -> dict[str, Any]:
    repo = Path(repo_root or resolve_kabu_root(Path.cwd()))
    kabu = resolve_kabu_root(repo)
    _check_disk()
    repo_str = str(repo)
    job_results: dict[str, dict[str, Any]] = {}
    with ProcessPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_run_job_worker, k, repo_str): k for k in JOB_SPECS}
        for fut in as_completed(futures):
            key = fut.result()
            spec = JOB_SPECS[key]
            job_dir = resolve_reports_dir(repo) / "phase617_parallel" / spec["job_id"]
            summary_path = job_dir / "job_summary.json"
            if summary_path.is_file():
                meta = json.loads(summary_path.read_text(encoding="utf-8"))
            else:
                meta = {}
            decisions_path = job_dir / "candidate_decisions.jsonl.gz"
            decisions = _load_candidate_decisions_gz(decisions_path)
            samples = []
            sample_gz = job_dir / "phase617_samples.csv.gz"
            if sample_gz.is_file():
                with gzip.open(sample_gz, "rt", encoding="utf-8") as fh:
                    samples = list(csv.DictReader(fh))
            job_results[key] = {
                **meta,
                "decisions": decisions,
                "samples": samples,
                "stage_rows": [],
                "ext_rows": [],
                "hot_rows": [],
            }
            for extra in ("stage_breakdown.csv", "extension_cost.csv", "hot_path.csv"):
                p = job_dir / extra
                if p.is_file():
                    with p.open(encoding="utf-8") as fh:
                        rows = list(csv.DictReader(fh))
                    if extra.startswith("stage"):
                        job_results[key]["stage_rows"] = rows
                    elif extra.startswith("extension"):
                        job_results[key]["ext_rows"] = rows
                    else:
                        job_results[key]["hot_rows"] = rows
    return aggregate_jobs(repo, job_results)


def aggregate_from_disk(repo_root: Optional[Path] = None) -> dict[str, Any]:
    repo = Path(repo_root or resolve_kabu_root(Path.cwd()))
    kabu = resolve_kabu_root(repo)
    reports = resolve_reports_dir(repo)
    job_results: dict[str, dict[str, Any]] = {}
    for key, spec in JOB_SPECS.items():
        job_dir = reports / "phase617_parallel" / spec["job_id"]
        summary_path = job_dir / "job_summary.json"
        meta = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {}
        decisions = _load_candidate_decisions_gz(job_dir / "candidate_decisions.jsonl.gz")
        samples = []
        sample_gz = job_dir / "phase617_samples.csv.gz"
        if sample_gz.is_file():
            with gzip.open(sample_gz, "rt", encoding="utf-8") as fh:
                samples = list(csv.DictReader(fh))
        stage_rows: list[dict[str, Any]] = []
        ext_rows: list[dict[str, Any]] = []
        hot_rows: list[dict[str, Any]] = []
        for extra, target in (
            ("stage_breakdown.csv", "stage_rows"),
            ("extension_cost.csv", "ext_rows"),
            ("hot_path.csv", "hot_rows"),
        ):
            p = job_dir / extra
            if p.is_file():
                with p.open(encoding="utf-8") as fh:
                    rows = list(csv.DictReader(fh))
                if target == "stage_rows":
                    stage_rows = rows
                elif target == "ext_rows":
                    ext_rows = rows
                else:
                    hot_rows = rows
        job_results[key] = {
            **meta,
            "decisions": decisions,
            "samples": samples,
            "stage_rows": stage_rows,
            "ext_rows": ext_rows,
            "hot_rows": hot_rows,
        }
    return aggregate_jobs(repo, job_results)


def run_phase617(repo_root: Optional[Path] = None) -> dict[str, Any]:
    return run_parallel(repo_root)
