#!/usr/bin/env python3
"""Phase687W70 STEP1-2 — Emergency snapshot + retention baseline (read-only sources)."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NATIVE / "src"))
sys.path.insert(0, str(NATIVE))

SMALL_PAPER = NATIVE / "results" / "small_paper"
REPORTS = NATIVE / "results" / "reports"
RETENTION = NATIVE / "results" / "retention"
PUSH_JSONL = NATIVE / "data" / "push_jsonl"
if not PUSH_JSONL.is_dir():
    PUSH_JSONL = NATIVE.parent / "data" / "push_jsonl"

MIN_DAY = "20260615"

BACKUP_NAMES = {
    "small_paper_events.csv",
    "small_paper_events.jsonl",
    "small_paper_positions.csv",
    "small_paper_rejects.csv",
    "small_paper_summary.json",
    "structural_trades.csv",
    "structural_events.csv",
    "quality_top_debug.csv",
    "quality_top_debug.json",
    "live_session_config.json",
    "live_session_safety_report.json",
    "errors.jsonl",
    "heartbeat.jsonl",
    "BACKUP_COMPLETE.json",
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _count_event_types(events: Path) -> tuple[Optional[int], Optional[int]]:
    if not events.is_file():
        return None, None
    acc = ex = 0
    try:
        if events.suffix == ".jsonl":
            with events.open(encoding="utf-8", errors="replace") as f:
                for line in f:
                    if '"event_type": "accepted"' in line or '"event_type":"accepted"' in line:
                        acc += 1
                    elif '"event_type": "observer_exit"' in line or '"event_type":"observer_exit"' in line:
                        ex += 1
        else:
            import csv as _csv

            with events.open(encoding="utf-8", newline="", errors="replace") as f:
                for row in _csv.DictReader(f):
                    et = row.get("event_type")
                    if et == "accepted":
                        acc += 1
                    elif et == "observer_exit":
                        ex += 1
    except OSError:
        return None, None
    return acc, ex


def iter_backup_files(session_dir: Path) -> Iterable[Path]:
    for name in sorted(BACKUP_NAMES):
        p = session_dir / name
        if p.is_file():
            yield p
    # also copy nested safety reports if present
    for p in session_dir.rglob("live_session_safety_report.json"):
        if p.is_file():
            yield p


def copy_verified(src: Path, dst: Path) -> dict[str, Any]:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        return {
            "source_path": str(src),
            "backup_path": str(dst),
            "size_bytes": src.stat().st_size,
            "sha256": "",
            "creation_time": datetime.fromtimestamp(src.stat().st_ctime).isoformat(),
            "last_write_time": datetime.fromtimestamp(src.stat().st_mtime).isoformat(),
            "copied_at": datetime.now(JST).isoformat(),
            "verification_status": "SKIP_EXISTS_NO_OVERWRITE",
        }
    shutil.copy2(src, dst)
    src_hash = sha256_file(src)
    dst_hash = sha256_file(dst)
    src_sz = src.stat().st_size
    dst_sz = dst.stat().st_size
    ok = src_hash == dst_hash and src_sz == dst_sz
    return {
        "source_path": str(src),
        "backup_path": str(dst),
        "size_bytes": src_sz,
        "sha256": src_hash,
        "creation_time": datetime.fromtimestamp(src.stat().st_ctime).isoformat(),
        "last_write_time": datetime.fromtimestamp(src.stat().st_mtime).isoformat(),
        "copied_at": datetime.now(JST).isoformat(),
        "verification_status": "OK" if ok else "SHA256_OR_SIZE_MISMATCH",
        "backup_sha256": dst_hash,
        "backup_size_bytes": dst_sz,
    }


def build_baseline() -> dict[str, Any]:
    days = sorted(
        p for p in SMALL_PAPER.iterdir() if p.is_dir() and len(p.name) == 8 and p.name.isdigit() and p.name >= MIN_DAY
    )
    sessions: list[dict[str, Any]] = []
    files_rows: list[dict[str, Any]] = []
    total_bytes = 0
    file_count = 0
    for day in days:
        for sess in sorted(day.glob("live_session_*")):
            if not sess.is_dir():
                continue
            sess_files = []
            sess_bytes = 0
            for f in sess.rglob("*"):
                if not f.is_file():
                    continue
                file_count += 1
                sz = f.stat().st_size
                sess_bytes += sz
                total_bytes += sz
                digest = sha256_file(f) if f.name in BACKUP_NAMES or f.suffix in {".json", ".csv", ".jsonl"} else ""
                # hash only critical / smallish files for baseline speed; events always hashed
                if f.name.startswith("small_paper_events") or f.stat().st_size <= 5_000_000:
                    if not digest:
                        digest = sha256_file(f)
                row = {
                    "day": day.name,
                    "session": sess.name,
                    "rel_path": str(f.relative_to(sess)).replace("\\", "/"),
                    "size_bytes": sz,
                    "sha256": digest,
                    "mtime": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                }
                files_rows.append(row)
                sess_files.append(row)
            ev = sess / "small_paper_events.csv"
            if not ev.is_file():
                ev = sess / "small_paper_events.jsonl"
            acc, ex = _count_event_types(ev)
            summary_trades = None
            sp = sess / "small_paper_summary.json"
            if sp.is_file():
                try:
                    sj = json.loads(sp.read_text(encoding="utf-8"))
                    summary_trades = sj.get("trade_count") or sj.get("accepted_count") or sj.get("n_trades")
                except (OSError, json.JSONDecodeError):
                    pass
            sessions.append(
                {
                    "day": day.name,
                    "session": sess.name,
                    "path": str(sess),
                    "file_count": len(sess_files),
                    "total_bytes": sess_bytes,
                    "accepted_count": acc,
                    "observer_exit_count": ex,
                    "summary_trade_count": summary_trades,
                    "has_events": ev.is_file(),
                }
            )
    day_names = [d.name for d in days]
    baseline = {
        "phase": "Phase687W70",
        "generated_at": datetime.now(JST).isoformat(),
        "min_day": MIN_DAY,
        "day_dirs": day_names,
        "day_count": len(day_names),
        "session_count": len(sessions),
        "file_count": file_count,
        "total_bytes": total_bytes,
        "oldest_session": sessions[0] if sessions else None,
        "newest_session": sessions[-1] if sessions else None,
        "sessions": sessions,
        "note": "Baseline for retention guard; do not auto-delete small_paper",
    }
    return baseline, files_rows


def run_backup(*, include_reports: bool = True) -> dict[str, Any]:
    stamp = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    snap = NATIVE / "results" / "archive" / f"emergency_snapshot_{stamp}"
    snap.mkdir(parents=True, exist_ok=False)

    manifest: list[dict[str, Any]] = []
    sessions_copied = 0
    failed = 0

    days = sorted(
        p for p in SMALL_PAPER.iterdir() if p.is_dir() and len(p.name) == 8 and p.name.isdigit() and p.name >= MIN_DAY
    )
    for day in days:
        for sess in sorted(day.glob("live_session_*")):
            if not sess.is_dir():
                continue
            sessions_copied += 1
            for src in iter_backup_files(sess):
                rel = src.relative_to(SMALL_PAPER)
                dst = snap / "small_paper" / rel
                row = copy_verified(src, dst)
                manifest.append(row)
                if row["verification_status"] not in ("OK", "SKIP_EXISTS_NO_OVERWRITE"):
                    failed += 1

    # push_jsonl
    push_copied = 0
    if PUSH_JSONL.is_dir():
        for src in PUSH_JSONL.rglob("*"):
            if not src.is_file() or src.name == ".gitkeep":
                continue
            rel = src.relative_to(PUSH_JSONL)
            dst = snap / "push_jsonl" / rel
            row = copy_verified(src, dst)
            manifest.append(row)
            push_copied += 1
            if row["verification_status"] not in ("OK", "SKIP_EXISTS_NO_OVERWRITE"):
                failed += 1

    # related reports (W66-W69 + daily_runner for window)
    if include_reports and REPORTS.is_dir():
        patterns = [
            "phase687w66*",
            "phase687w67*",
            "phase687w69*",
            "daily_runner_summary_2026*",
            "phase148_am_pm_daily_runner_2026*",
            "phase265_structural_trades_backfill_by_session.csv",
            "phase300_board_live_payload_availability_report.json",
        ]
        for pat in patterns:
            for src in REPORTS.glob(pat):
                if not src.is_file():
                    continue
                dst = snap / "reports" / src.name
                row = copy_verified(src, dst)
                manifest.append(row)
                if row["verification_status"] not in ("OK", "SKIP_EXISTS_NO_OVERWRITE"):
                    failed += 1

    baseline, files_rows = build_baseline()
    RETENTION.mkdir(parents=True, exist_ok=True)
    base_json = RETENTION / "small_paper_retention_baseline.json"
    base_csv = RETENTION / "small_paper_retention_baseline.csv"
    base_json.write_text(json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")
    with base_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["day", "session", "rel_path", "size_bytes", "sha256", "mtime"],
        )
        w.writeheader()
        for r in files_rows:
            w.writerow(r)

    # copy baseline into snapshot
    for src in (base_json, base_csv):
        row = copy_verified(src, snap / "retention" / src.name)
        manifest.append(row)
        if row["verification_status"] not in ("OK", "SKIP_EXISTS_NO_OVERWRITE"):
            failed += 1

    man_path = snap / "manifest.json"
    summary = {
        "phase": "Phase687W70",
        "snapshot_dir": str(snap),
        "created_at": datetime.now(JST).isoformat(),
        "sessions_copied": sessions_copied,
        "files_in_manifest": len(manifest),
        "failed_verifications": failed,
        "total_bytes_copied": sum(int(m.get("size_bytes") or 0) for m in manifest if m.get("verification_status") == "OK"),
        "push_jsonl_files_copied": push_copied,
        "protection_complete": failed == 0,
        "min_day": MIN_DAY,
    }
    man_path.write_text(
        json.dumps({"summary": summary, "files": manifest}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # reports
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "phase687w70_immediate_backup_report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md = [
        "# Phase687W70 Immediate Backup Report",
        "",
        f"- snapshot: `{snap}`",
        f"- sessions_copied: {sessions_copied}",
        f"- files: {len(manifest)}",
        f"- failed_verifications: {failed}",
        f"- total_bytes_ok: {summary['total_bytes_copied']}",
        f"- protection_complete: {summary['protection_complete']}",
        f"- push_jsonl_files: {push_copied}",
        "",
    ]
    (REPORTS / "phase687w70_immediate_backup_report.md").write_text("\n".join(md), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-reports", action="store_true")
    args = ap.parse_args()
    summary = run_backup(include_reports=not args.skip_reports)
    return 0 if summary.get("protection_complete") else 2


if __name__ == "__main__":
    raise SystemExit(main())
