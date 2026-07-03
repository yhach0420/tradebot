"""
Phase620 disk cleanup: plan, delete, report freed space.
"""

from __future__ import annotations

import csv
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

DISK_FREE_WARN_GB = 50.0
DISK_FREE_ABORT_GB = 30.0

KEEP_PATTERNS = (
    "phase620_summary.json",
    "phase620_freshness_semantics_full_period_backtest.md",
    "small_paper_events.jsonl",
    "small_paper_rejects",
    "small_paper_summary.json",
)


def _free_gb(path: Path) -> float:
    try:
        return shutil.disk_usage(path).free / (1024**3)
    except OSError:
        return 0.0


def _dir_size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def _should_keep(path: Path) -> bool:
    name = path.name.lower()
    if any(k in name for k in KEEP_PATTERNS):
        return True
    if path.suffix == ".md" and "phase620" in name:
        return True
    return False


def _collect_targets(kabu: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    candidates: list[Path] = []

    ckpt = kabu / "results" / "small_paper" / "_phase620_freshness_checkpoints"
    if ckpt.is_dir():
        candidates.append(ckpt)

    v1 = kabu / "results" / "reports" / "phase620_freshness_backtest"
    if v1.is_dir():
        for p in v1.rglob("*"):
            if p.is_file() and not _should_keep(p):
                candidates.append(p)

    sp = kabu / "results" / "small_paper"
    if sp.is_dir():
        for p in sp.rglob("*phase620*"):
            if p.is_dir() and ("_phase620" in p.name or p.name == "_phase620_v2_temp"):
                candidates.append(p)
        for p in sp.rglob("*_replay"):
            if p.is_dir():
                candidates.append(p)

    v2_jobs = kabu / "results" / "reports" / "phase620_freshness_backtest_v2" / "jobs"
    if v2_jobs.is_dir():
        for p in v2_jobs.rglob("*"):
            if p.is_file() and p.name not in (
                "job_summary.json",
                "trades.csv.gz",
                "reject_sample.csv.gz",
                "log.txt",
            ):
                candidates.append(p)

    seen: set[str] = set()
    for p in sorted(set(candidates), key=lambda x: str(x)):
        key = str(p.resolve())
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "path": str(p),
                "kind": "dir" if p.is_dir() else "file",
                "size_mb": round(_dir_size_bytes(p) / (1024**2), 2),
                "action": "delete",
            }
        )
    return rows


def run_disk_cleanup(repo_root: Path) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    reports = resolve_reports_dir(kabu)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    free_before = _free_gb(kabu)

    plan_rows = _collect_targets(kabu)
    plan_path = reports / f"phase620_disk_cleanup_plan_{ts}.csv"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    with plan_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["path", "kind", "size_mb", "action"])
        w.writeheader()
        w.writerows(plan_rows)

    deleted: list[dict[str, Any]] = []
    freed = 0
    for row in plan_rows:
        p = Path(str(row["path"]))
        if not p.exists():
            continue
        sz = _dir_size_bytes(p)
        try:
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink(missing_ok=True)
            deleted.append({**row, "deleted": True})
            freed += sz
        except OSError as exc:
            deleted.append({**row, "deleted": False, "error": str(exc)})

    free_after = _free_gb(kabu)
    result = {
        "timestamp": ts,
        "free_gb_before": round(free_before, 2),
        "free_gb_after": round(free_after, 2),
        "freed_gb": round(freed / (1024**3), 3),
        "deleted_count": sum(1 for d in deleted if d.get("deleted")),
        "plan_path": str(plan_path),
        "can_resume": free_after >= DISK_FREE_WARN_GB,
        "abort_if_below_gb": DISK_FREE_ABORT_GB,
        "warn_if_below_gb": DISK_FREE_WARN_GB,
    }
    result_path = reports / f"phase620_disk_cleanup_result_{ts}.csv"
    with result_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(
            fh,
            fieldnames=[
                "path",
                "kind",
                "size_mb",
                "action",
                "deleted",
                "error",
            ],
        )
        w.writeheader()
        for d in deleted:
            w.writerow(
                {
                    "path": d.get("path"),
                    "kind": d.get("kind"),
                    "size_mb": d.get("size_mb"),
                    "action": d.get("action"),
                    "deleted": d.get("deleted"),
                    "error": d.get("error", ""),
                }
            )
        fh.write(f"\n# summary: freed_gb={result['freed_gb']} free_after={result['free_gb_after']}\n")
    result["result_path"] = str(result_path)
    return result
