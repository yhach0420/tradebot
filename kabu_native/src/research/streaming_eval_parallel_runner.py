"""
Phase343-pre: session-level parallel replay evaluation runner.

One session per worker process; workers write temp JSON; parent merges.
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.streaming_eval_session_runners import (
    SessionEvalResult,
    bootstrap_sys_path,
    execute_streaming_eval_worker_job,
    run_streaming_eval_session,
)


@dataclass
class ParallelEvalConfig:
    parallel: bool = False
    max_workers: int = 1
    worker_temp_dir: Optional[Path] = None
    cleanup_temp: bool = True

    def effective_workers(self) -> int:
        if not self.parallel:
            return 1
        return max(1, int(self.max_workers))


@dataclass
class ParallelEvalRunResult:
    session_results: list[SessionEvalResult] = field(default_factory=list)
    wall_runtime_sec: float = 0.0
    peak_memory_mb: float = 0.0
    temp_dir: Optional[Path] = None
    failed_sessions: list[dict[str, Any]] = field(default_factory=list)
    max_workers: int = 1
    parallel: bool = False


def add_parallel_eval_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--parallel",
        action="store_true",
        default=False,
        help="Enable session-level parallel evaluation (default: sequential)",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=1,
        help="Process pool size when --parallel (default: 1)",
    )
    parser.add_argument(
        "--worker-temp-dir",
        type=Path,
        default=None,
        help="Directory for per-worker temp JSON files",
    )
    parser.add_argument(
        "--keep-worker-temp",
        action="store_true",
        default=False,
        help="Do not delete worker temp files after merge",
    )


def parallel_config_from_args(args: argparse.Namespace) -> ParallelEvalConfig:
    return ParallelEvalConfig(
        parallel=bool(getattr(args, "parallel", False)),
        max_workers=int(getattr(args, "max_workers", 1) or 1),
        worker_temp_dir=getattr(args, "worker_temp_dir", None),
        cleanup_temp=not bool(getattr(args, "keep_worker_temp", False)),
    )


def _safe_session_slug(session_meta: Mapping[str, Any], index: int) -> str:
    sid = str(session_meta.get("session_id") or session_meta.get("day_key") or f"session_{index}")
    return sid.replace("/", "_").replace("\\", "_").replace(":", "_")


def _make_temp_dir(base: Optional[Path], mode: str) -> Path:
    if base is not None:
        path = Path(base)
        path.mkdir(parents=True, exist_ok=True)
        return path
    from datetime import datetime
    from zoneinfo import ZoneInfo

    stamp = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y%m%d_%H%M%S")
    path = Path.cwd() / "kabu_native" / "results" / "reports" / f"_parallel_eval_{mode}_{stamp}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _note_parent_memory_mb() -> float:
    from research.phase337_exit_candidate_evaluation import _memory_mb

    return _memory_mb()


def _load_session_result(path: Path) -> SessionEvalResult:
    data = json.loads(path.read_text(encoding="utf-8"))
    return SessionEvalResult.from_dict(data)


def run_parallel_session_evaluation(
    *,
    sessions: Sequence[Mapping[str, Any]],
    mode: str,
    repo_root: Path,
    config_path: Path,
    max_push_rows: Optional[int],
    streaming: bool,
    parallel_config: ParallelEvalConfig,
    extra: Optional[Mapping[str, Any]] = None,
    progress: Optional[Callable[[str], None]] = None,
) -> ParallelEvalRunResult:
    """Evaluate sessions sequentially or in parallel; returns ordered session results."""
    workers = parallel_config.effective_workers()
    temp_dir = _make_temp_dir(parallel_config.worker_temp_dir, mode)
    out = ParallelEvalRunResult(
        temp_dir=temp_dir,
        max_workers=workers,
        parallel=parallel_config.parallel and workers > 1,
    )
    peak_mb = 0.0
    t0 = time.monotonic()

    def _log(msg: str) -> None:
        if progress is not None:
            progress(msg)

    if workers <= 1 or len(sessions) <= 1:
        for i, session_meta in enumerate(sessions):
            sid = session_meta.get("session_id")
            _log(f"[{i + 1}/{len(sessions)}] {sid} ...")
            result = run_streaming_eval_session(
                mode=mode,
                session_meta=session_meta,
                repo_root=repo_root,
                config_path=config_path,
                max_push_rows=max_push_rows,
                streaming=streaming,
                extra=extra,
                session_index=i,
            )
            out.session_results.append(result)
            peak_mb = max(peak_mb, result.peak_memory_mb, _note_parent_memory_mb())
            if result.error:
                out.failed_sessions.append({**dict(session_meta), "error": result.error})
                _log(f"  FAIL: {result.error}")
            else:
                _log(
                    f"  ok trade_rows={len(result.trade_rows)} "
                    f"runtime={result.runtime_sec:.1f}s"
                )
    else:
        jobs: list[dict[str, Any]] = []
        for i, session_meta in enumerate(sessions):
            slug = _safe_session_slug(session_meta, i)
            output_path = temp_dir / f"worker_{i:03d}_{slug}.json"
            jobs.append(
                {
                    "session_index": i,
                    "mode": mode,
                    "session_meta": dict(session_meta),
                    "repo_root": str(repo_root),
                    "config_path": str(config_path),
                    "max_push_rows": max_push_rows,
                    "streaming": streaming,
                    "extra": dict(extra or {}),
                    "output_path": str(output_path),
                }
            )

        completed: dict[int, SessionEvalResult] = {}
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(execute_streaming_eval_worker_job, job): job for job in jobs}
            done_count = 0
            for fut in as_completed(futures):
                job = futures[fut]
                done_count += 1
                sid = job.get("session_meta", {}).get("session_id")
                try:
                    status = fut.result()
                except Exception as exc:
                    idx = int(job.get("session_index") or 0)
                    result = SessionEvalResult(
                        session_meta=dict(job.get("session_meta") or {}),
                        session_index=idx,
                        error=f"pool_exception:{exc}",
                    )
                    completed[idx] = result
                    out.failed_sessions.append(
                        {**dict(job.get("session_meta") or {}), "error": result.error}
                    )
                    _log(f"[{done_count}/{len(sessions)}] {sid} FAIL pool_exception")
                    continue

                peak_mb = max(peak_mb, float(status.get("peak_memory_mb") or 0.0))
                output_path = Path(str(status.get("output_path") or ""))
                if output_path.is_file():
                    result = _load_session_result(output_path)
                else:
                    result = SessionEvalResult(
                        session_meta=dict(job.get("session_meta") or {}),
                        session_index=int(status.get("session_index") or 0),
                        error=str(status.get("error") or "missing_worker_output"),
                    )
                completed[result.session_index] = result
                if result.error:
                    out.failed_sessions.append({**result.session_meta, "error": result.error})
                    _log(f"[{done_count}/{len(sessions)}] {sid} FAIL {result.error}")
                else:
                    _log(
                        f"[{done_count}/{len(sessions)}] {sid} ok "
                        f"trade_rows={len(result.trade_rows)} runtime={result.runtime_sec:.1f}s"
                    )
                peak_mb = max(peak_mb, result.peak_memory_mb, _note_parent_memory_mb())

        out.session_results = [completed[i] for i in range(len(sessions)) if i in completed]

    out.wall_runtime_sec = round(time.monotonic() - t0, 2)
    out.peak_memory_mb = round(peak_mb, 2)

    if parallel_config.cleanup_temp and temp_dir.is_dir():
        shutil.rmtree(temp_dir, ignore_errors=True)
        out.temp_dir = None
    return out


def ingest_session_results_to_aggregator(
    agg: Any,
    run_result: ParallelEvalRunResult,
    *,
    ingest_method: str = "ingest_session",
) -> None:
    """Feed ordered session results into a phase aggregator."""
    import inspect

    ingest = getattr(agg, ingest_method, None)
    if ingest is None:
        raise AttributeError(f"aggregator missing {ingest_method}")
    params = inspect.signature(ingest).parameters

    for result in run_result.session_results:
        kwargs: dict[str, Any] = {
            "session_meta": result.session_meta,
            "trade_rows": result.trade_rows,
            "push_rows": result.push_rows,
            "runtime_sec": result.runtime_sec,
            "error": result.error,
        }
        if "vwap_coverage_pct" in params:
            kwargs["vwap_coverage_pct"] = result.vwap_coverage_pct
        ingest(**kwargs)
        if hasattr(agg, "note_memory"):
            agg.note_memory()


def directory_size_mb(path: Path) -> float:
    if not path.exists():
        return 0.0
    total = 0
    if path.is_file():
        return round(path.stat().st_size / (1024 * 1024), 4)
    for fp in path.rglob("*"):
        if fp.is_file():
            total += fp.stat().st_size
    return round(total / (1024 * 1024), 4)


def output_paths_size_mb(paths: Mapping[str, Any]) -> float:
    total = 0.0
    for p in paths.values():
        total += directory_size_mb(Path(p))
    return round(total, 4)


def write_parallel_eval_benchmark(
    path: Path,
    *,
    sequential_runtime_sec: float,
    parallel_runtime_sec: float,
    max_workers: int,
    sessions_evaluated: int,
    sessions_failed: int,
    peak_memory_mb: float,
    output_size_mb: float,
    extra: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    speedup = (
        round(sequential_runtime_sec / parallel_runtime_sec, 4)
        if parallel_runtime_sec > 0
        else None
    )
    payload = {
        "title": "phase343_pre_parallel_eval_benchmark",
        "sequential_runtime_sec": round(sequential_runtime_sec, 2),
        "parallel_runtime_sec": round(parallel_runtime_sec, 2),
        "speedup_ratio": speedup,
        "max_workers": max_workers,
        "sessions_evaluated": sessions_evaluated,
        "sessions_failed": sessions_failed,
        "peak_memory_mb": round(peak_memory_mb, 2),
        "output_size_mb": round(output_size_mb, 4),
        "windows_stable": True,
        **dict(extra or {}),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload
