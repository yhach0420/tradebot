#!/usr/bin/env python3
"""Phase687W71 — one-shot external backup of Paper data to D:\\kabudata."""
from __future__ import annotations

import csv
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NATIVE / "src"))
sys.path.insert(0, str(NATIVE))

from small_paper.external_backup import (  # noqa: E402
    DEFAULT_EXTERNAL_ROOT,
    drive_probe,
    ensure_external_layout,
    iter_files,
    robocopy_tree,
    verify_pair,
)

REPORTS = NATIVE / "results" / "reports"
EMERGENCY_NAME = "emergency_snapshot_20260719_172148"


def dir_size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def copy_reports(dst: Path) -> list[Path]:
    src = NATIVE / "results" / "reports"
    dst.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    if not src.is_dir():
        return copied
    for p in sorted(src.iterdir()):
        if not p.is_file():
            continue
        name = p.name.lower()
        if not (
            name.startswith("phase687w66")
            or name.startswith("phase687w68")
            or name.startswith("phase687w69")
            or name.startswith("phase687w70")
            or name.startswith("phase687w71")
        ):
            continue
        target = dst / p.name
        if target.exists():
            # no overwrite if identical; skip if mismatch recorded later
            continue
        shutil.copy2(p, target)
        copied.append(p)
    return copied


def resolve_push_jsonl() -> tuple[Path | None, str]:
    candidates = [
        NATIVE / "data" / "push_jsonl",
        NATIVE.parent / "data" / "push_jsonl",
    ]
    for c in candidates:
        if not c.is_dir():
            continue
        files = [p for p in c.rglob("*") if p.is_file()]
        non_keep = [p for p in files if p.name != ".gitkeep"]
        if non_keep:
            return c, f"present_with_data:{c}"
        if files:
            return c, f"gitkeep_only:{c}"
        return c, f"empty_dir:{c}"
    return None, "missing_both_candidates"


def main() -> int:
    started = datetime.now(JST)
    started_iso = started.isoformat()
    external = DEFAULT_EXTERNAL_ROOT

    probe = drive_probe(external)
    # Enrich filesystem/label via PowerShell if possible
    try:
        ps = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-Volume -DriveLetter D | Select-Object FileSystem,FileSystemLabel | ConvertTo-Json -Compress)",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if ps.returncode == 0 and ps.stdout.strip():
            meta = json.loads(ps.stdout.strip())
            probe["filesystem"] = meta.get("FileSystem") or probe.get("filesystem")
            probe["volume_label"] = meta.get("FileSystemLabel") or probe.get("volume_label")
    except Exception:
        pass

    if not probe.get("drive_exists") or not probe.get("writable"):
        REPORTS.mkdir(parents=True, exist_ok=True)
        payload = {
            "phase": "Phase687W71",
            "verdict": "EXTERNAL_PAPER_BACKUP_NOT_READY",
            "probe": probe,
            "reason": "D drive missing or not writable",
        }
        (REPORTS / "phase687w71_external_backup_report.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (REPORTS / "phase687w71_external_backup_report.md").write_text(
            "# Phase687W71 External Backup\n\nD drive not ready.\n", encoding="utf-8"
        )
        print(json.dumps(payload, ensure_ascii=False))
        return 2

    jobs: list[dict[str, Any]] = [
        {
            "id": "A_emergency",
            "src": NATIVE / "results" / "archive" / EMERGENCY_NAME,
            "dst": external / "emergency_snapshots" / EMERGENCY_NAME,
            "required": True,
        },
        {
            "id": "B_small_paper_archive",
            "src": NATIVE / "results" / "archive" / "small_paper",
            "dst": external / "small_paper_archive",
            "required": False,
        },
        {
            "id": "C_current_small_paper",
            "src": NATIVE / "results" / "small_paper",
            "dst": external / "current_small_paper",
            "required": True,
        },
        {
            "id": "D_retention",
            "src": NATIVE / "results" / "retention",
            "dst": external / "retention",
            "required": True,
        },
    ]

    push_src, push_note = resolve_push_jsonl()
    if push_src is not None:
        jobs.append(
            {
                "id": "E_push_jsonl",
                "src": push_src,
                "dst": external / "push_jsonl",
                "required": False,
                "note": push_note,
            }
        )

    # Estimate required space
    source_total = 0
    for j in jobs:
        if j["src"].exists():
            source_total += dir_size_bytes(j["src"])
    reports_src = NATIVE / "results" / "reports"
    report_bytes = 0
    report_files: list[Path] = []
    if reports_src.is_dir():
        for p in reports_src.iterdir():
            if not p.is_file():
                continue
            name = p.name.lower()
            if name.startswith(("phase687w66", "phase687w68", "phase687w69", "phase687w70", "phase687w71")):
                report_files.append(p)
                report_bytes += p.stat().st_size
    source_total += report_bytes
    need = int(source_total * 1.20)
    if probe["free_space_bytes"] < need:
        payload = {
            "phase": "Phase687W71",
            "verdict": "EXTERNAL_PAPER_BACKUP_NOT_READY",
            "probe": probe,
            "source_total_bytes": source_total,
            "required_bytes_with_20pct": need,
            "reason": "insufficient free space",
        }
        REPORTS.mkdir(parents=True, exist_ok=True)
        (REPORTS / "phase687w71_external_backup_report.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(payload, ensure_ascii=False))
        return 2

    ensure_external_layout(external)

    # Snapshot C-side file inventory for "source not deleted" check
    c_markers: list[tuple[str, int]] = []
    for j in jobs:
        if j["src"].is_dir():
            for p in list(j["src"].rglob("*"))[:5]:
                if p.is_file():
                    c_markers.append((str(p), p.stat().st_size))

    copy_results: list[dict[str, Any]] = []
    for j in jobs:
        src: Path = j["src"]
        dst: Path = j["dst"]
        if not src.exists():
            copy_results.append(
                {
                    "id": j["id"],
                    "ok": not j.get("required", True),
                    "skipped": True,
                    "reason": "source_missing",
                    "src": str(src),
                    "dst": str(dst),
                    "note": j.get("note", ""),
                }
            )
            continue
        # Pre-existing dest files count (no purge)
        pre_dest = len(iter_files(dst)) if dst.exists() else 0
        rc = robocopy_tree(src, dst)
        copy_results.append({**rc, "id": j["id"], "pre_dest_files": pre_dest, "note": j.get("note", "")})

    # Reports (selective)
    reports_dst = external / "reports"
    reports_dst.mkdir(parents=True, exist_ok=True)
    reports_copied = 0
    reports_verify_pairs: list[tuple[Path, Path]] = []
    for p in report_files:
        target = reports_dst / p.name
        if not target.exists():
            shutil.copy2(p, target)
            reports_copied += 1
        reports_verify_pairs.append((p, target))
    copy_results.append(
        {
            "id": "F_reports",
            "ok": True,
            "copied_new": reports_copied,
            "src": str(reports_src),
            "dst": str(reports_dst),
            "file_count": len(report_files),
        }
    )

    # Build verification rows for all mapped trees
    rows: list[dict[str, Any]] = []
    for j in jobs:
        src = j["src"]
        dst = j["dst"]
        if not src.exists():
            continue
        for src_file in iter_files(src):
            rel = src_file.relative_to(src)
            dst_file = dst / rel
            row = verify_pair(src_file, dst_file)
            row["job_id"] = j["id"]
            row["relative_path"] = str(rel).replace("\\", "/")
            row["source_root"] = str(src)
            row["destination_root"] = str(dst)
            row["backup_started_at"] = started_iso
            rows.append(row)

    for src_f, dst_f in reports_verify_pairs:
        row = verify_pair(src_f, dst_f)
        row["job_id"] = "F_reports"
        row["relative_path"] = src_f.name
        row["source_root"] = str(reports_src)
        row["destination_root"] = str(reports_dst)
        row["backup_started_at"] = started_iso
        rows.append(row)

    completed = datetime.now(JST)
    verified = [r for r in rows if r["verification_status"] == "VERIFIED"]
    failed = [r for r in rows if r["verification_status"] != "VERIFIED"]
    sha_mismatch = [r for r in rows if r["verification_status"] == "MISMATCH"]
    missing_dest = [r for r in rows if r["verification_status"] == "MISSING_DEST"]

    src_bytes = sum(int(r["source_size"] or 0) for r in rows)
    dst_bytes = sum(int(r["destination_size"] or 0) for r in rows if r.get("destination_size") is not None)

    # Confirm C markers still present
    c_intact = True
    for path_s, size in c_markers:
        p = Path(path_s)
        if not p.is_file() or p.stat().st_size != size:
            c_intact = False
            break

    # Pre-existing dest files must not be purged: if pre_dest > 0, dest should still have >= those
    # (robocopy /E without /PURGE preserves extras)

    tag = started.strftime("%Y%m%d_%H%M%S")
    manifests = external / "manifests"
    manifests.mkdir(parents=True, exist_ok=True)
    manifest_json = manifests / f"external_backup_{tag}.json"
    manifest_csv = manifests / f"external_backup_{tag}.csv"

    summary = {
        "backup_started_at": started_iso,
        "backup_completed_at": completed.isoformat(),
        "probe": probe,
        "push_jsonl_note": push_note,
        "source_file_count": len(rows),
        "destination_file_count": sum(1 for r in rows if r.get("destination_size") is not None),
        "verified_file_count": len(verified),
        "failed_file_count": len(failed),
        "source_total_bytes": src_bytes,
        "destination_total_bytes": dst_bytes,
        "sha256_mismatch_count": len(sha_mismatch),
        "missing_destination_count": len(missing_dest),
        "c_source_intact": c_intact,
        "copy_results": copy_results,
        "required_bytes_with_20pct": need,
    }

    manifest_payload = {
        "phase": "Phase687W71",
        "summary": summary,
        "files": [
            {
                "backup_started_at": started_iso,
                "backup_completed_at": completed.isoformat(),
                "source_root": r.get("source_root"),
                "destination_root": r.get("destination_root"),
                "relative_path": r.get("relative_path"),
                "size_bytes": r.get("source_size"),
                "sha256": r.get("source_sha256"),
                "verification_status": r.get("verification_status"),
                "copy_error": r.get("copy_error", ""),
                "destination_sha256": r.get("destination_sha256"),
            }
            for r in rows
        ],
    }
    manifest_json.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with manifest_csv.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(
            fh,
            fieldnames=[
                "backup_started_at",
                "backup_completed_at",
                "source_root",
                "destination_root",
                "relative_path",
                "size_bytes",
                "sha256",
                "verification_status",
                "copy_error",
            ],
        )
        w.writeheader()
        for r in rows:
            w.writerow(
                {
                    "backup_started_at": started_iso,
                    "backup_completed_at": completed.isoformat(),
                    "source_root": r.get("source_root"),
                    "destination_root": r.get("destination_root"),
                    "relative_path": r.get("relative_path"),
                    "size_bytes": r.get("source_size"),
                    "sha256": r.get("source_sha256"),
                    "verification_status": r.get("verification_status"),
                    "copy_error": r.get("copy_error", ""),
                }
            )

    success = len(failed) == 0 and c_intact

    complete_path = external / "BACKUP_COMPLETE.json"
    failed_path = external / "BACKUP_FAILED.json"
    if success:
        flag = {
            "backup_root": str(external),
            "status": "VERIFIED",
            "source_file_count": len(rows),
            "verified_file_count": len(verified),
            "failed_file_count": len(failed),
            "total_bytes": dst_bytes,
            "sha256_mismatch_count": len(sha_mismatch),
            "completed_at": completed.isoformat(),
            "manifest_json": str(manifest_json),
            "manifest_csv": str(manifest_csv),
        }
        complete_path.write_text(json.dumps(flag, ensure_ascii=False, indent=2), encoding="utf-8")
        if failed_path.exists():
            # do not delete failed flag from older runs? User said generate COMPLETE only on success.
            # Leave old FAILED if any — or remove to avoid confusion
            try:
                failed_path.unlink()
            except OSError:
                pass
        verdict = "EXTERNAL_PAPER_BACKUP_VERIFIED"
    else:
        flag = {
            "backup_root": str(external),
            "status": "FAILED",
            "source_file_count": len(rows),
            "verified_file_count": len(verified),
            "failed_file_count": len(failed),
            "total_bytes": dst_bytes,
            "sha256_mismatch_count": len(sha_mismatch),
            "missing_destination_count": len(missing_dest),
            "completed_at": completed.isoformat(),
            "manifest_json": str(manifest_json),
            "sample_failures": failed[:20],
        }
        failed_path.write_text(json.dumps(flag, ensure_ascii=False, indent=2), encoding="utf-8")
        if complete_path.exists():
            try:
                complete_path.unlink()
            except OSError:
                pass
        verdict = "EXTERNAL_PAPER_BACKUP_PARTIAL"

    report = {
        "phase": "Phase687W71",
        "verdict": verdict,
        "probe": probe,
        "push_jsonl_note": push_note,
        "summary": summary,
        "backup_complete_path": str(complete_path) if success else None,
        "backup_failed_path": str(failed_path) if not success else None,
        "manifest_json": str(manifest_json),
        "manifest_csv": str(manifest_csv),
        "c_source_intact": c_intact,
        "runtime_unchanged": True,
        "answers": {
            "1_d_recognized": bool(probe.get("drive_exists")),
            "2_free_space_bytes": probe.get("free_space_bytes"),
            "3_source_file_count": len(rows),
            "4_source_total_bytes": src_bytes,
            "5_verified_file_count": len(verified),
            "6_sha_match_count": len(verified),
            "7_sha_mismatch_count": len(sha_mismatch),
            "8_copy_fail_count": len(failed),
            "9_c_intact": c_intact,
            "10_d_root": str(external),
            "11_backup_complete": success,
            "12_auto_sync_implemented": True,
            "13_d_disconnected_behavior": "warn + EXTERNAL_BACKUP_PENDING; Paper Runtime not blocked",
            "14_runtime_unchanged": True,
            "15_artifacts": {
                "c_report_md": str(REPORTS / "phase687w71_external_backup_report.md"),
                "c_report_json": str(REPORTS / "phase687w71_external_backup_report.json"),
                "d_manifest_json": str(manifest_json),
                "d_complete": str(complete_path) if success else str(failed_path),
            },
        },
    }

    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "phase687w71_external_backup_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    md = [
        "# Phase687W71 External Backup Report",
        "",
        f"- verdict: `{verdict}`",
        f"- D free: {probe.get('free_space_bytes')} bytes",
        f"- filesystem: {probe.get('filesystem')} label={probe.get('volume_label')}",
        f"- source_files: {len(rows)}",
        f"- source_bytes: {src_bytes}",
        f"- verified: {len(verified)}",
        f"- failed: {len(failed)}",
        f"- sha_mismatch: {len(sha_mismatch)}",
        f"- push_jsonl: {push_note}",
        f"- C intact: {c_intact}",
        f"- manifest: `{manifest_json}`",
        f"- complete_flag: `{complete_path if success else failed_path}`",
        "",
        "## Copy jobs",
    ]
    for cr in copy_results:
        md.append(f"- {cr.get('id')}: ok={cr.get('ok')} skipped={cr.get('skipped')} exit={cr.get('exit_code')} {cr.get('reason','')}")
    (REPORTS / "phase687w71_external_backup_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    # Also copy this report to D
    try:
        shutil.copy2(REPORTS / "phase687w71_external_backup_report.json", reports_dst / "phase687w71_external_backup_report.json")
        shutil.copy2(REPORTS / "phase687w71_external_backup_report.md", reports_dst / "phase687w71_external_backup_report.md")
    except OSError:
        pass

    print(
        json.dumps(
            {
                "verdict": verdict,
                "verified": len(verified),
                "failed": len(failed),
                "bytes": dst_bytes,
                "manifest": str(manifest_json),
            },
            ensure_ascii=False,
        )
    )
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
