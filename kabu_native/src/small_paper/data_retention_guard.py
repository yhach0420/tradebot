"""Paper data retention guard — prevent silent loss of small_paper / push_jsonl / archive.

Does NOT change ENTRY/EXIT/Shadow/thresholds. Fail-closed on missing baseline integrity.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

# Absolute protection — never auto-delete these trees
PROTECTED_ROOT_SUFFIXES = (
    ("results", "small_paper"),
    ("data", "push_jsonl"),
    ("results", "archive"),
)

DISK_WARN_PCT = 75.0
DISK_BLOCK_PCT = 92.0  # warn at 75%; hard-block only at critical free-space
RETENTION_ENV_DISABLE = "PAPER_RETENTION_GUARD_DISABLE"


def native_root() -> Path:
    return Path(__file__).resolve().parents[2]


def small_paper_root(root: Optional[Path] = None) -> Path:
    return (root or native_root()) / "results" / "small_paper"


def archive_root(root: Optional[Path] = None) -> Path:
    return (root or native_root()) / "results" / "archive"


def retention_dir(root: Optional[Path] = None) -> Path:
    return (root or native_root()) / "results" / "retention"


def baseline_path(root: Optional[Path] = None) -> Path:
    return retention_dir(root) / "small_paper_retention_baseline.json"


def disk_usage_pct(path: str | Path = "C:/") -> float:
    try:
        t, u, _f = shutil.disk_usage(str(path))
        return round(100.0 * u / t, 2) if t else 0.0
    except OSError:
        return 0.0


def is_protected_path(path: Path, *, root: Optional[Path] = None) -> bool:
    """True if path is under protected small_paper / push_jsonl / archive trees."""
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    base = (root or native_root()).resolve()
    for parts in PROTECTED_ROOT_SUFFIXES:
        prot = base.joinpath(*parts)
        try:
            resolved.relative_to(prot)
            return True
        except ValueError:
            continue
        except OSError:
            continue
    # also protect when path string contains these segments (cross-drive copies)
    s = str(resolved).replace("/", "\\").lower()
    return any(
        x in s
        for x in (
            "\\results\\small_paper\\",
            "\\data\\push_jsonl\\",
            "\\results\\archive\\",
        )
    )


class ProtectedDataDeleteError(RuntimeError):
    """Raised when code attempts to delete protected Paper data without approval."""


def forbid_protected_delete(path: Path, *, root: Optional[Path] = None, reason: str = "") -> None:
    if is_protected_path(path, root=root):
        # Allow deleting only scratch dirs *inside* protected trees (not OS %TEMP%).
        name = path.name.lower()
        s = str(path).replace("/", "\\").lower()
        # Match only segments under results\small_paper|archive or data\push_jsonl
        under = ""
        for marker in ("\\results\\small_paper\\", "\\data\\push_jsonl\\", "\\results\\archive\\"):
            i = s.find(marker)
            if i >= 0:
                under = s[i + len(marker) :]
                break
        allowed_scratch = (
            "_phase",
            "demo_",
            "demo_push",
            "_w8_test",
            "fixture",
            "synthetic",
            ".write_probe",
            "_tmp",
            "_quarantine",
        )
        rel_parts = [p for p in under.split("\\") if p]
        if any(p.startswith(a) or p == a for p in rel_parts for a in allowed_scratch):
            return
        if name.endswith(".tmp") or name.endswith(".partial"):
            return
        raise ProtectedDataDeleteError(
            f"DATA_RETENTION_DELETE_FORBIDDEN: refusing delete of protected path {path} ({reason})"
        )


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class RetentionFinding:
    kind: str
    day: str = ""
    session: str = ""
    path: str = ""
    detail: str = ""


@dataclass
class RetentionCheckResult:
    ok: bool
    code: str
    findings: list[RetentionFinding] = field(default_factory=list)
    disk_usage_pct: float = 0.0
    disk_blocks_start: bool = False
    baseline_path: str = ""
    checked_at: str = ""
    current_day_count: int = 0
    baseline_day_count: int = 0
    current_session_count: int = 0
    baseline_session_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "code": self.code,
            "findings": [f.__dict__ for f in self.findings],
            "disk_usage_pct": self.disk_usage_pct,
            "disk_blocks_start": self.disk_blocks_start,
            "baseline_path": self.baseline_path,
            "checked_at": self.checked_at,
            "current_day_count": self.current_day_count,
            "baseline_day_count": self.baseline_day_count,
            "current_session_count": self.current_session_count,
            "baseline_session_count": self.baseline_session_count,
        }


def _list_live_sessions(sp: Path) -> list[tuple[str, str, Path]]:
    out: list[tuple[str, str, Path]] = []
    if not sp.is_dir():
        return out
    for day in sorted(p for p in sp.iterdir() if p.is_dir() and len(p.name) == 8 and p.name.isdigit()):
        for sess in sorted(day.glob("live_session_*")):
            if sess.is_dir():
                out.append((day.name, sess.name, sess))
    return out


def check_retention_integrity(
    *,
    root: Optional[Path] = None,
    require_archive_emergency: bool = True,
    warn_on_disk_pct: float = DISK_WARN_PCT,
    block_on_disk_pct: float = DISK_BLOCK_PCT,
) -> RetentionCheckResult:
    """Compare current small_paper live sessions vs baseline. Growth OK; shrinkage FAIL."""
    checked_at = datetime.now(JST).isoformat()
    root = root or native_root()
    if os.environ.get(RETENTION_ENV_DISABLE) == "1":
        return RetentionCheckResult(
            ok=True,
            code="RETENTION_GUARD_DISABLED",
            checked_at=checked_at,
            baseline_path=str(baseline_path(root)),
        )

    disk = disk_usage_pct("C:/")
    findings: list[RetentionFinding] = []
    bp = baseline_path(root)
    if not bp.is_file():
        return RetentionCheckResult(
            ok=False,
            code="DATA_RETENTION_INTEGRITY_ERROR",
            findings=[RetentionFinding(kind="missing_baseline", path=str(bp), detail="baseline missing")],
            disk_usage_pct=disk,
            disk_blocks_start=disk >= block_on_disk_pct,
            baseline_path=str(bp),
            checked_at=checked_at,
        )

    try:
        baseline = json.loads(bp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return RetentionCheckResult(
            ok=False,
            code="DATA_RETENTION_INTEGRITY_ERROR",
            findings=[RetentionFinding(kind="baseline_unreadable", detail=str(e))],
            disk_usage_pct=disk,
            baseline_path=str(bp),
            checked_at=checked_at,
        )

    base_sessions = {
        (str(s.get("day")), str(s.get("session"))): s for s in (baseline.get("sessions") or [])
    }
    current = _list_live_sessions(small_paper_root(root))
    cur_keys = {(d, s) for d, s, _ in current}

    for key, meta in base_sessions.items():
        if key not in cur_keys:
            findings.append(
                RetentionFinding(
                    kind="missing_session",
                    day=key[0],
                    session=key[1],
                    path=str(meta.get("path") or ""),
                    detail="session directory missing vs baseline",
                )
            )
            continue
        # find path
        sess_path = next(p for d, s, p in current if (d, s) == key)
        if meta.get("has_events"):
            ev = sess_path / "small_paper_events.csv"
            if not ev.is_file():
                ev = sess_path / "small_paper_events.jsonl"
            if not ev.is_file():
                findings.append(
                    RetentionFinding(
                        kind="missing_events",
                        day=key[0],
                        session=key[1],
                        path=str(sess_path),
                        detail="events file missing",
                    )
                )

    # empty day dirs that had sessions in baseline already covered; empty new dirs OK
    if require_archive_emergency:
        arch = archive_root(root)
        snaps = list(arch.glob("emergency_snapshot_*")) if arch.is_dir() else []
        if not snaps:
            findings.append(
                RetentionFinding(
                    kind="missing_emergency_snapshot",
                    path=str(arch),
                    detail="no emergency_snapshot_* under results/archive",
                )
            )

    disk_block = disk >= block_on_disk_pct
    if disk >= warn_on_disk_pct:
        findings.append(
            RetentionFinding(
                kind="disk_usage_high",
                detail=(
                    f"disk_usage_pct={disk} >= {warn_on_disk_pct}; auto-delete forbidden; "
                    f"{'START BLOCKED' if disk_block else 'warn only (block at ' + str(block_on_disk_pct) + '%)'}"
                ),
            )
        )

    ok = not any(
        f.kind in ("missing_session", "missing_events", "missing_baseline", "baseline_unreadable")
        for f in findings
    )
    # critical disk free-space blocks start; 75% is warn-only (never auto-delete)
    if disk_block:
        ok = False

    code = "OK" if ok else "DATA_RETENTION_INTEGRITY_ERROR"
    return RetentionCheckResult(
        ok=ok,
        code=code,
        findings=findings,
        disk_usage_pct=disk,
        disk_blocks_start=disk_block,
        baseline_path=str(bp),
        checked_at=checked_at,
        current_day_count=len({d for d, _, _ in current}),
        baseline_day_count=len({k[0] for k in base_sessions}),
        current_session_count=len(current),
        baseline_session_count=len(base_sessions),
    )


def format_retention_console(result: RetentionCheckResult) -> str:
    lines = [
        f"[RETENTION] code={result.code} ok={result.ok} disk={result.disk_usage_pct}%",
        f"[RETENTION] baseline={result.baseline_path}",
        f"[RETENTION] sessions baseline={result.baseline_session_count} current={result.current_session_count}",
        f"[RETENTION] checked_at={result.checked_at}",
    ]
    for f in result.findings:
        lines.append(
            f"[RETENTION] FINDING {f.kind} day={f.day} session={f.session} path={f.path} {f.detail}"
        )
    return "\n".join(lines)


def archive_session_copy(
    session_dir: Path,
    *,
    root: Optional[Path] = None,
    critical_names: Optional[Sequence[str]] = None,
) -> dict[str, Any]:
    """Copy session artifacts to results/archive/small_paper/{day}/{session}/ — no overwrite, no source delete."""
    root = root or native_root()
    names = set(
        critical_names
        or (
            "small_paper_events.csv",
            "small_paper_events.jsonl",
            "small_paper_positions.csv",
            "small_paper_rejects.csv",
            "small_paper_summary.json",
            "structural_trades.csv",
            "quality_top_debug.csv",
            "live_session_config.json",
            "errors.jsonl",
            "heartbeat.jsonl",
        )
    )
    session_dir = Path(session_dir)
    day = session_dir.parent.name
    sess = session_dir.name
    dest = archive_root(root) / "small_paper" / day / sess
    dest.mkdir(parents=True, exist_ok=True)
    verified = 0
    failed = 0
    total = 0
    copied = 0
    errors: list[str] = []
    for name in sorted(names):
        src = session_dir / name
        if not src.is_file():
            continue
        total += 1
        dst = dest / name
        if dst.exists():
            # no overwrite — verify existing
            try:
                if sha256_file(src) == sha256_file(dst) and src.stat().st_size == dst.stat().st_size:
                    verified += 1
                else:
                    failed += 1
                    errors.append(f"exists_mismatch:{name}")
            except OSError as e:
                failed += 1
                errors.append(f"verify_error:{name}:{e}")
            continue
        try:
            shutil.copy2(src, dst)
            copied += 1
            if sha256_file(src) == sha256_file(dst):
                verified += 1
            else:
                failed += 1
                errors.append(f"sha_mismatch:{name}")
        except OSError as e:
            failed += 1
            errors.append(f"copy_error:{name}:{e}")

    flag = {
        "session": sess,
        "source_path": str(session_dir),
        "archive_path": str(dest),
        "file_count": total,
        "copied_new": copied,
        "total_bytes": sum((dest / n).stat().st_size for n in names if (dest / n).is_file()),
        "verified_count": verified,
        "failed_count": failed,
        "errors": errors,
        "completed_at": datetime.now(JST).isoformat(),
        "ok": failed == 0,
    }
    (dest / "BACKUP_COMPLETE.json").write_text(json.dumps(flag, ensure_ascii=False, indent=2), encoding="utf-8")
    return flag


def preflight_or_raise(*, root: Optional[Path] = None) -> RetentionCheckResult:
    result = check_retention_integrity(root=root)
    print(format_retention_console(result), flush=True)
    if not result.ok:
        raise SystemExit(f"{result.code}: Paper start blocked by retention guard")
    return result
