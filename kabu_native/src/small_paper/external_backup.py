"""Phase687W71 — external Paper backup to D:\\kabudata (copy-only, no source delete).

Runtime ENTRY/EXIT/Shadow/thresholds/capital are never modified here.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

DEFAULT_EXTERNAL_ROOT = Path(os.environ.get("PAPER_EXTERNAL_BACKUP_ROOT", r"D:\kabudata"))
PENDING_NAME = "external_backup_pending.json"
EXTERNAL_BACKUP_ENV_DISABLE = "PAPER_EXTERNAL_BACKUP_DISABLE"


def native_root() -> Path:
    return Path(__file__).resolve().parents[2]


def archive_small_paper_root(root: Optional[Path] = None) -> Path:
    return (root or native_root()) / "results" / "archive" / "small_paper"


def retention_dir(root: Optional[Path] = None) -> Path:
    return (root or native_root()) / "results" / "retention"


def pending_path(root: Optional[Path] = None) -> Path:
    return retention_dir(root) / PENDING_NAME


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def drive_probe(root: Path = DEFAULT_EXTERNAL_ROOT) -> dict[str, Any]:
    """Return external root availability + free space.

    Write probe uses the backup root (not drive letter root) so C:\\ / D:\\
    ACL restrictions do not false-negative writable checks.
    """
    root = Path(root)
    drive = Path(str(root)[:2] + "\\") if len(str(root)) >= 2 and str(root)[1] == ":" else root
    exists = drive.exists()
    writable = False
    free = 0
    total = 0
    filesystem = ""
    label = ""
    if exists:
        try:
            usage = shutil.disk_usage(str(drive))
            free = int(usage.free)
            total = int(usage.total)
        except OSError:
            pass
        try:
            root.mkdir(parents=True, exist_ok=True)
            probe = root / f"_w71_write_probe_{os.getpid()}.tmp"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            writable = True
        except OSError:
            writable = False
        filesystem = ""
        label = ""
    return {
        "drive_exists": exists,
        "writable": writable,
        "free_space_bytes": free,
        "total_space_bytes": total,
        "filesystem": filesystem,
        "volume_label": label,
        "root": str(root),
        "connected": bool(exists and writable),
    }


def ensure_external_layout(root: Path = DEFAULT_EXTERNAL_ROOT) -> None:
    for name in (
        "emergency_snapshots",
        "small_paper_archive",
        "current_small_paper",
        "retention",
        "push_jsonl",
        "reports",
        "manifests",
    ):
        (root / name).mkdir(parents=True, exist_ok=True)


def robocopy_tree(src: Path, dst: Path) -> dict[str, Any]:
    """Copy tree with robocopy. Never use /MIR /PURGE /MOVE /MOV."""
    dst.mkdir(parents=True, exist_ok=True)
    if not src.exists():
        return {"ok": False, "exit_code": -1, "error": "source_missing", "src": str(src), "dst": str(dst)}
    cmd = [
        "robocopy",
        str(src),
        str(dst),
        "/E",
        "/COPY:DAT",
        "/DCOPY:DAT",
        "/R:2",
        "/W:2",
        "/XJ",
        "/MT:8",
        "/NFL",
        "/NDL",
        "/NJH",
        "/NJS",
    ]
    # Forbid destructive flags explicitly (defense in depth).
    forbidden = {"/MIR", "/PURGE", "/MOVE", "/MOV"}
    if any(a.upper() in forbidden for a in cmd):
        raise RuntimeError("destructive robocopy flags forbidden")
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    # robocopy: 0-7 = success / partial success; >=8 = failure
    ok = proc.returncode < 8
    return {
        "ok": ok,
        "exit_code": proc.returncode,
        "src": str(src),
        "dst": str(dst),
        "stdout": (proc.stdout or "")[-2000:],
        "stderr": (proc.stderr or "")[-2000:],
    }


def verify_pair(src: Path, dst: Path) -> dict[str, Any]:
    row = {
        "source_relative_path": str(src),
        "destination_relative_path": str(dst),
        "source_size": None,
        "destination_size": None,
        "source_sha256": "",
        "destination_sha256": "",
        "verification_status": "MISSING_DEST",
        "copy_error": "",
    }
    if not src.is_file():
        row["verification_status"] = "MISSING_SOURCE"
        row["copy_error"] = "source_missing"
        return row
    row["source_size"] = src.stat().st_size
    if not dst.is_file():
        row["verification_status"] = "MISSING_DEST"
        row["copy_error"] = "destination_missing"
        return row
    row["destination_size"] = dst.stat().st_size
    try:
        row["source_sha256"] = sha256_file(src)
        row["destination_sha256"] = sha256_file(dst)
    except OSError as e:
        row["verification_status"] = "HASH_ERROR"
        row["copy_error"] = str(e)
        return row
    if row["source_size"] == row["destination_size"] and row["source_sha256"] == row["destination_sha256"]:
        row["verification_status"] = "VERIFIED"
    else:
        row["verification_status"] = "MISMATCH"
    return row


def iter_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*") if p.is_file())


def load_pending(root: Optional[Path] = None) -> dict[str, Any]:
    path = pending_path(root)
    if not path.is_file():
        return {"pending": [], "updated_at": None}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"pending": [], "updated_at": None, "error": "unreadable"}


def save_pending(data: dict[str, Any], root: Optional[Path] = None) -> Path:
    path = pending_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = datetime.now(JST).isoformat()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def add_pending_session(day: str, session: str, *, root: Optional[Path] = None, reason: str = "") -> None:
    data = load_pending(root)
    pending = data.setdefault("pending", [])
    key = f"{day}/{session}"
    if not any(p.get("key") == key for p in pending):
        pending.append(
            {
                "key": key,
                "day": day,
                "session": session,
                "reason": reason or "EXTERNAL_BACKUP_PENDING",
                "queued_at": datetime.now(JST).isoformat(),
            }
        )
    data["status"] = "EXTERNAL_BACKUP_PENDING"
    save_pending(data, root)


def remove_pending_session(day: str, session: str, *, root: Optional[Path] = None) -> None:
    data = load_pending(root)
    key = f"{day}/{session}"
    data["pending"] = [p for p in data.get("pending", []) if p.get("key") != key]
    if not data["pending"]:
        data["status"] = "OK"
    save_pending(data, root)


def external_session_archive_path(
    day: str, session: str, *, external_root: Path = DEFAULT_EXTERNAL_ROOT
) -> Path:
    return external_root / "small_paper_archive" / day / session


def copy_session_to_external(
    session_dir: Path,
    *,
    native: Optional[Path] = None,
    external_root: Path = DEFAULT_EXTERNAL_ROOT,
) -> dict[str, Any]:
    """Copy one archived/live session to D:\\kabudata\\small_paper_archive\\{day}\\{session}\\."""
    native = native or native_root()
    session_dir = Path(session_dir)
    day = session_dir.parent.name
    sess = session_dir.name
    started = datetime.now(JST).isoformat()

    if os.environ.get(EXTERNAL_BACKUP_ENV_DISABLE) == "1":
        return {"ok": True, "skipped": True, "reason": "disabled", "session": sess}

    probe = drive_probe(external_root)
    if not probe["connected"]:
        add_pending_session(day, sess, root=native, reason="D_DRIVE_NOT_CONNECTED")
        print(
            f"[EXTERNAL_BACKUP] EXTERNAL_BACKUP_PENDING day={day} session={sess} "
            f"(D not connected)",
            flush=True,
        )
        return {
            "ok": False,
            "pending": True,
            "code": "EXTERNAL_BACKUP_PENDING",
            "session": sess,
            "day": day,
            "probe": probe,
        }

    ensure_external_layout(external_root)
    dest = external_session_archive_path(day, sess, external_root=external_root)
    dest.mkdir(parents=True, exist_ok=True)

    # Prefer C archive if present; else copy from live session dir.
    c_arch = archive_small_paper_root(native) / day / sess
    src = c_arch if c_arch.is_dir() else session_dir

    verified = 0
    failed = 0
    total_bytes = 0
    rows: list[dict[str, Any]] = []
    for src_file in iter_files(src):
        rel = src_file.relative_to(src)
        dst_file = dest / rel
        if dst_file.exists():
            row = verify_pair(src_file, dst_file)
            if row["verification_status"] != "VERIFIED":
                failed += 1
                row["copy_error"] = row.get("copy_error") or "exists_no_overwrite_mismatch"
            else:
                verified += 1
                total_bytes += int(row["destination_size"] or 0)
            rows.append(row)
            continue
        try:
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dst_file)
            row = verify_pair(src_file, dst_file)
            if row["verification_status"] == "VERIFIED":
                verified += 1
                total_bytes += int(row["destination_size"] or 0)
            else:
                failed += 1
            rows.append(row)
        except OSError as e:
            failed += 1
            rows.append(
                {
                    "source_relative_path": str(src_file),
                    "destination_relative_path": str(dst_file),
                    "verification_status": "COPY_ERROR",
                    "copy_error": str(e),
                }
            )

    flag = {
        "session": sess,
        "day": day,
        "source_path": str(src),
        "archive_path": str(dest),
        "file_count": len(rows),
        "total_bytes": total_bytes,
        "verified_count": verified,
        "failed_count": failed,
        "completed_at": datetime.now(JST).isoformat(),
        "started_at": started,
        "ok": failed == 0,
        "status": "VERIFIED" if failed == 0 else "FAILED",
    }
    if failed == 0:
        (dest / "BACKUP_COMPLETE.json").write_text(
            json.dumps(flag, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        remove_pending_session(day, sess, root=native)
    else:
        (dest / "BACKUP_FAILED.json").write_text(
            json.dumps({**flag, "rows": rows[:50]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        add_pending_session(day, sess, root=native, reason="VERIFY_FAILED")
        print(f"[EXTERNAL_BACKUP] FAILED day={day} session={sess} failed={failed}", flush=True)
    return flag


def sync_pending_external(
    *,
    native: Optional[Path] = None,
    external_root: Path = DEFAULT_EXTERNAL_ROOT,
) -> dict[str, Any]:
    """Attempt to sync all pending sessions if D is connected. Never blocks Runtime on disconnect."""
    native = native or native_root()
    probe = drive_probe(external_root)
    data = load_pending(native)
    pending = list(data.get("pending") or [])
    if not probe["connected"]:
        if pending:
            print(
                f"[EXTERNAL_BACKUP] D disconnected; {len(pending)} session(s) still EXTERNAL_BACKUP_PENDING",
                flush=True,
            )
        return {
            "ok": True,
            "warn": True,
            "code": "EXTERNAL_BACKUP_PENDING" if pending else "OK",
            "pending_count": len(pending),
            "synced": [],
            "failed": [],
            "probe": probe,
            "blocks_start": False,
        }

    synced: list[str] = []
    failed: list[str] = []
    for item in pending:
        day = item.get("day", "")
        sess = item.get("session", "")
        # Prefer C archive, else live
        c_arch = archive_small_paper_root(native) / day / sess
        live = native / "results" / "small_paper" / day / sess
        src = c_arch if c_arch.is_dir() else live
        if not src.is_dir():
            failed.append(f"{day}/{sess}:source_missing")
            continue
        result = copy_session_to_external(src, native=native, external_root=external_root)
        if result.get("ok"):
            synced.append(f"{day}/{sess}")
        else:
            failed.append(f"{day}/{sess}")

    return {
        "ok": True,
        "code": "OK" if not failed and not load_pending(native).get("pending") else "PARTIAL",
        "pending_count": len(load_pending(native).get("pending") or []),
        "synced": synced,
        "failed": failed,
        "probe": probe,
        "blocks_start": False,
    }


@dataclass
class ExternalSyncStatus:
    c_archived: list[str] = field(default_factory=list)
    d_archived: list[str] = field(default_factory=list)
    unsynced: list[str] = field(default_factory=list)
    sha_mismatches: list[str] = field(default_factory=list)
    d_connected: bool = False
    blocks_start: bool = False
    code: str = "OK"


def check_external_sync_status(
    *,
    native: Optional[Path] = None,
    external_root: Path = DEFAULT_EXTERNAL_ROOT,
    sync_if_connected: bool = True,
) -> ExternalSyncStatus:
    """Startup check: compare C archive vs D archive. D missing → warn only."""
    native = native or native_root()
    probe = drive_probe(external_root)
    status = ExternalSyncStatus(d_connected=probe["connected"])

    c_root = archive_small_paper_root(native)
    c_sessions: list[str] = []
    if c_root.is_dir():
        for day_dir in sorted(p for p in c_root.iterdir() if p.is_dir() and p.name.isdigit()):
            for sess_dir in sorted(p for p in day_dir.iterdir() if p.is_dir()):
                c_sessions.append(f"{day_dir.name}/{sess_dir.name}")
    status.c_archived = c_sessions

    if not probe["connected"]:
        pending = load_pending(native).get("pending") or []
        keys = [p.get("key", "") for p in pending if p.get("key")]
        for s in c_sessions:
            if s not in keys:
                keys.append(s)
        status.unsynced = keys
        status.code = "EXTERNAL_BACKUP_PENDING" if keys else "OK"
        status.blocks_start = False
        print(
            f"[EXTERNAL_BACKUP] warn: D not connected; unsynced_count={len(status.unsynced)} "
            f"(Paper start NOT blocked)",
            flush=True,
        )
        if keys:
            existing = {p.get("key"): p for p in pending if p.get("key")}
            merged = []
            for k in keys:
                if k in existing:
                    merged.append(existing[k])
                else:
                    day, _, sess = k.partition("/")
                    merged.append(
                        {
                            "key": k,
                            "day": day,
                            "session": sess,
                            "reason": "D_DRIVE_NOT_CONNECTED",
                            "queued_at": datetime.now(JST).isoformat(),
                        }
                    )
            save_pending({"status": "EXTERNAL_BACKUP_PENDING", "pending": merged}, native)
        return status

    ensure_external_layout(external_root)
    d_root = external_root / "small_paper_archive"
    d_sessions: list[str] = []
    if d_root.is_dir():
        for day_dir in sorted(p for p in d_root.iterdir() if p.is_dir() and p.name.isdigit()):
            for sess_dir in sorted(p for p in day_dir.iterdir() if p.is_dir()):
                key = f"{day_dir.name}/{sess_dir.name}"
                d_sessions.append(key)
                # sample BACKUP_COMPLETE presence
                if not (sess_dir / "BACKUP_COMPLETE.json").is_file():
                    status.unsynced.append(key)
    status.d_archived = d_sessions
    status.unsynced = sorted(set(status.unsynced) | (set(c_sessions) - set(d_sessions)))

    if sync_if_connected and status.unsynced:
        print(f"[EXTERNAL_BACKUP] syncing {len(status.unsynced)} unsynced session(s) before start", flush=True)
        for key in list(status.unsynced):
            day, sess = key.split("/", 1)
            c_arch = c_root / day / sess
            live = native / "results" / "small_paper" / day / sess
            src = c_arch if c_arch.is_dir() else live
            if src.is_dir():
                copy_session_to_external(src, native=native, external_root=external_root)
        # refresh
        return check_external_sync_status(
            native=native, external_root=external_root, sync_if_connected=False
        )

    status.code = "OK" if not status.unsynced and not status.sha_mismatches else "EXTERNAL_BACKUP_PENDING"
    status.blocks_start = False
    return status


def after_session_archive(
    session_dir: Path,
    *,
    native: Optional[Path] = None,
    external_root: Path = DEFAULT_EXTERNAL_ROOT,
) -> dict[str, Any]:
    """Call after C-side archive_session_copy completes."""
    return copy_session_to_external(session_dir, native=native, external_root=external_root)


def status_as_dict(status: ExternalSyncStatus) -> dict[str, Any]:
    return asdict(status)
