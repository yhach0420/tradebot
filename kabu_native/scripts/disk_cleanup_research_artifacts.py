#!/usr/bin/env python3
"""P0 disk cleanup — plan, delete research checkpoints, write result CSV."""

from __future__ import annotations

import csv
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "results" / "reports"
JST = ZoneInfo("Asia/Tokyo")

KEEP_REPORT_SUFFIXES = {".json", ".md"}

PHASE_INTERMEDIATE_PREFIXES = (
    "phase600_",
    "phase603_",
    "phase607_",
    "phase607b_",
    "phase608_",
    "phase609_",
    "phase610_",
    "phase611_",
    "phase613_",
)

PHASE_KEEP_BASENAMES = {
    "phase600_report.json",
    "phase603_report.json",
    "phase607b_report.json",
    "phase608_report.json",
    "phase609_report.json",
    "phase610_report.json",
    "phase611_report.json",
    "phase613_report.json",
}

PHASE_KEEP_DIR_SUFFIXES = (
    "_parallel",
)


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    if path.is_file():
        return path.stat().st_size
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def _free_bytes(drive: str = "C:") -> int:
    try:
        import shutil as sh

        usage = sh.disk_usage(drive)
        return int(usage.free)
    except Exception:
        return 0


def _ts_tag() -> str:
    return datetime.now(JST).strftime("%Y%m%d_%H%M")


def collect_candidates(repo: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    sp = repo / "results" / "small_paper"

    for p in sorted(sp.glob("_phase*")):
        rows.append(
            {
                "path": str(p),
                "kind": "checkpoint_dir",
                "size_bytes": _dir_size(p),
                "action": "delete",
                "reason": "phase backtest/replay checkpoint",
            }
        )

    reports = repo / "results" / "reports"
    for p in sorted(reports.iterdir()):
        if p.is_dir():
            if any(p.name.endswith(suf) for suf in PHASE_KEEP_DIR_SUFFIXES):
                continue
            if p.name.startswith("phase611_") and p.name != "phase611_parallel":
                rows.append(
                    {
                        "path": str(p),
                        "kind": "phase_huge_dir",
                        "size_bytes": _dir_size(p),
                        "action": "delete",
                        "reason": "phase full dump replaced by disk-safe parallel",
                    }
                )
            continue
        if not p.is_file():
            continue
        name = p.name
        if name in PHASE_KEEP_BASENAMES:
            continue
        if not any(name.startswith(pref) for pref in PHASE_INTERMEDIATE_PREFIXES):
            continue
        if p.suffix in KEEP_REPORT_SUFFIXES:
            continue
        if name.endswith(".csv.gz") or name.endswith(".gz"):
            continue
        rows.append(
            {
                "path": str(p),
                "kind": "phase_intermediate_csv",
                "size_bytes": _dir_size(p),
                "action": "delete",
                "reason": "research intermediate; final json/md/gz kept",
            }
        )

    # replay temp under small_paper
    for p in sorted(sp.glob("*replay*temp*")) + sorted(sp.glob("_*replay*")):
        if p.name.startswith("_phase"):
            continue
        rows.append(
            {
                "path": str(p),
                "kind": "replay_temp",
                "size_bytes": _dir_size(p),
                "action": "delete",
                "reason": "replay temp output",
            }
        )

    return rows


def write_plan(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["path", "kind", "size_bytes", "size_gb", "action", "reason"]
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(
                {
                    **{k: r[k] for k in fields if k != "size_gb"},
                    "size_gb": round(r["size_bytes"] / (1024**3), 4),
                }
            )


def execute_deletes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Phase687W70: never delete live session / push_jsonl / archive without approval.
    sys.path.insert(0, str(ROOT / "src"))
    from small_paper.data_retention_guard import ProtectedDataDeleteError, forbid_protected_delete

    results: list[dict[str, Any]] = []
    for r in rows:
        p = Path(r["path"])
        ok = False
        err = ""
        try:
            forbid_protected_delete(p, root=ROOT, reason="disk_cleanup_research_artifacts")
            if p.is_dir():
                shutil.rmtree(p)
                ok = not p.exists()
            elif p.is_file():
                p.unlink()
                ok = not p.exists()
            else:
                ok = True
                err = "already_missing"
        except ProtectedDataDeleteError as exc:
            err = str(exc)
        except Exception as exc:
            err = str(exc)
        results.append({**r, "deleted_ok": ok, "error": err})
    return results


def main() -> int:
    tag = _ts_tag()
    free_before = _free_bytes()
    rows = collect_candidates(ROOT)
    plan_path = REPORTS / f"disk_cleanup_plan_{tag}.csv"
    write_plan(rows, plan_path)
    total_planned = sum(r["size_bytes"] for r in rows)
    print(f"plan: {plan_path} ({len(rows)} items, {total_planned/(1024**3):.2f} GB)")

    results = execute_deletes(rows)
    free_after = _free_bytes()
    result_path = REPORTS / f"disk_cleanup_result_{tag}.csv"
    fields = [
        "path",
        "kind",
        "size_bytes",
        "size_gb",
        "action",
        "reason",
        "deleted_ok",
        "error",
    ]
    with result_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in results:
            w.writerow(
                {
                    **{k: r.get(k, "") for k in fields if k not in ("size_gb",)},
                    "size_gb": round(r["size_bytes"] / (1024**3), 4),
                }
            )

    freed = free_after - free_before
    print(f"result: {result_path}")
    print(f"free_before_gb: {free_before/(1024**3):.2f}")
    print(f"free_after_gb: {free_after/(1024**3):.2f}")
    print(f"freed_gb: {freed/(1024**3):.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
