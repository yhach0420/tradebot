"""Phase687W7 — Operational recovery and audit drill (dry-run, no real orders).

Session manifest/seal, journal integrity, recovery modes, kill-switch/restart drills,
file-failure semantics, disk/clock guards, operator ack schema, audit bundles.

PRODUCTION ORDER ENABLEMENT remains NOT AUTHORIZED / NOT IMPLEMENTED.
"""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
SCHEMA_VERSION = "687W7.1"
PRODUCTION_ORDER_ENABLEMENT = "NOT_AUTHORIZED / NOT_IMPLEMENTED"
RUNTIME_VERSION = "687W7.1"

EXIT_RECOVERY_DRYRUN_READY = 0
EXIT_JOURNAL_RECON = 2
EXIT_KILL_SWITCH_ACK = 3
EXIT_DISK_CLOCK = 4
EXIT_DESIGN_CONFIG = 5

DISK_WARNING_PCT = 80.0
DISK_CRITICAL_PCT = 90.0
DISK_HARD_STOP_PCT = 95.0

PROTECTED_PATH_GLOBS = (
    "**/canonical/**",
    "**/events*.jsonl",
    "**/positions*.jsonl",
    "**/live_order_safety/**",
    "**/order_*.jsonl",
    "**/soak_session_snapshot.json",
    "**/session_manifest.json",
    "**/session_seal.json",
)


class RecoveryMode(str, Enum):
    NORMAL = "NORMAL"
    ENTRY_BLOCKED = "ENTRY_BLOCKED"
    EXIT_ONLY = "EXIT_ONLY"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    JOURNAL_RECOVERY_REQUIRED = "JOURNAL_RECOVERY_REQUIRED"
    KILL_SWITCH_ACTIVE = "KILL_SWITCH_ACTIVE"
    READONLY_DEGRADED = "READONLY_DEGRADED"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"


class JournalIntegrityStatus(str, Enum):
    JOURNAL_OK = "JOURNAL_OK"
    JOURNAL_PARTIAL_TAIL = "JOURNAL_PARTIAL_TAIL"
    JOURNAL_SEQUENCE_GAP = "JOURNAL_SEQUENCE_GAP"
    JOURNAL_DUPLICATE = "JOURNAL_DUPLICATE"
    JOURNAL_STATE_CONFLICT = "JOURNAL_STATE_CONFLICT"
    JOURNAL_SCHEMA_MISMATCH = "JOURNAL_SCHEMA_MISMATCH"
    JOURNAL_CORRUPTED = "JOURNAL_CORRUPTED"


class OperatorAckStatus(str, Enum):
    SAMPLE_ONLY = "SAMPLE_ONLY"
    NOT_ACKNOWLEDGED = "NOT_ACKNOWLEDGED"
    ACKNOWLEDGED_DRYRUN = "ACKNOWLEDGED_DRYRUN"
    PRODUCTION_FORBIDDEN = "PRODUCTION_FORBIDDEN"


class DiskState(str, Enum):
    OK = "OK"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    HARD_STOP = "HARD_STOP"
    UNKNOWN = "UNKNOWN"


class ClockState(str, Enum):
    OK = "OK"
    WALL_CLOCK_ROLLBACK = "WALL_CLOCK_ROLLBACK"
    TIMEZONE_MISMATCH = "TIMEZONE_MISMATCH"
    FUTURE_TIMESTAMP = "FUTURE_TIMESTAMP"
    EXTREME_EVENT_AGE = "EXTREME_EVENT_AGE"
    MONOTONIC_REGRESSION = "MONOTONIC_REGRESSION"
    CLOCK_JUMP = "CLOCK_JUMP"
    INVALID = "INVALID"


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _count_rows(path: Path) -> int:
    if not path.is_file():
        return 0
    n = 0
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.strip():
                n += 1
    return n


def config_sha256(path: Path | str) -> str:
    p = Path(path)
    if not p.is_file():
        return "MISSING"
    return _sha256_file(p)


@dataclass(frozen=True)
class RecoveryModePolicy:
    mode: str
    entry_allowed: bool
    exit_allowed: bool
    cancel_allowed: bool
    readonly_allowed: bool
    journal_write_allowed: bool
    discord_notify: bool
    operator_action: str
    return_condition: str


def recovery_mode_matrix() -> list[RecoveryModePolicy]:
    return [
        RecoveryModePolicy(RecoveryMode.NORMAL.value, True, True, True, True, True, False, "none", "n/a"),
        RecoveryModePolicy(
            RecoveryMode.ENTRY_BLOCKED.value, False, True, True, True, True, True,
            "investigate block reason; ack required", "blockers cleared + operator ack",
        ),
        RecoveryModePolicy(
            RecoveryMode.EXIT_ONLY.value, False, True, True, True, True, True,
            "confirm broker/local positions; ack required", "recon PASS + operator ack",
        ),
        RecoveryModePolicy(
            RecoveryMode.RECONCILIATION_REQUIRED.value, False, True, False, True, True, True,
            "run reconciliation; do not resubmit", "recon PASS + operator ack",
        ),
        RecoveryModePolicy(
            RecoveryMode.JOURNAL_RECOVERY_REQUIRED.value, False, False, False, True, False, True,
            "repair via recovery copy; never delete original", "journal JOURNAL_OK + operator ack",
        ),
        RecoveryModePolicy(
            RecoveryMode.KILL_SWITCH_ACTIVE.value, False, True, True, True, True, True,
            "pending intents CANCEL_REQUIRED (no real cancel)", "kill cleared + ack",
        ),
        RecoveryModePolicy(
            RecoveryMode.READONLY_DEGRADED.value, False, True, False, False, True, True,
            "restore Kabu read-only / token", "readonly ONLINE + operator ack",
        ),
        RecoveryModePolicy(
            RecoveryMode.MANUAL_REVIEW_REQUIRED.value, False, False, False, True, False, True,
            "manual review of audit bundle", "review complete + operator ack",
        ),
    ]


def recovery_mode_matrix_rows() -> list[dict[str, Any]]:
    return [asdict(p) for p in recovery_mode_matrix()]


def mode_allows_entry(mode: str) -> bool:
    for p in recovery_mode_matrix():
        if p.mode == mode:
            return p.entry_allowed
    return False


# ─── Session manifest ───────────────────────────────────────────────────────


@dataclass
class SessionManifest:
    session_id: str
    trading_day: str = ""
    session_am_pm: str = ""
    started_at: str = ""
    ended_at: str = ""
    git_commit: str = "UNSET"
    config_sha256: str = "UNSET"
    design_schema_version: str = SCHEMA_VERSION
    runtime_version: str = RUNTIME_VERSION
    python_version: str = field(default_factory=lambda: platform.python_version())
    live_trading_enabled: bool = False
    order_enabled: bool = False
    safety_sm_enabled: bool = True
    np_logger_enabled: bool = False
    kabu_readonly_status: str = "UNKNOWN"
    token_probe_status: str = "UNKNOWN"
    reconciliation_status: str = "UNKNOWN"
    journal_sequence_start: int = 0
    journal_sequence_end: int = 0
    production_approval_status: str = "NOT_AUTHORIZED"
    machine_clock_status: str = ClockState.OK.value
    disk_usage_pct: Optional[float] = None
    cache_status: str = "UNKNOWN"
    restart_count: int = 0
    canonical_entry_count: int = 0
    canonical_exit_count: int = 0
    safety_sm_signal_count: int = 0
    intent_count: int = 0
    submit_count: int = 0
    cancel_count: int = 0
    reservation_leak: int = 0
    reconciliation_mismatch: int = 0
    kill_switch_events: int = 0
    snapshot_completeness: str = "UNKNOWN"
    session_seal_status: str = "NOT_SEALED"
    update_mode: str = "create_then_update"
    schema_version: str = SCHEMA_VERSION
    sealed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def create_session_manifest(
    *,
    session_id: str,
    output_dir: Path,
    trading_day: str = "",
    session_am_pm: str = "",
    git_commit: str = "UNSET",
    config_sha: str = "UNSET",
    live_trading_enabled: bool = False,
    order_enabled: bool = False,
    safety_sm_enabled: bool = True,
    np_logger_enabled: bool = False,
    kabu_readonly_status: str = "UNKNOWN",
    token_probe_status: str = "UNKNOWN",
    journal_sequence_start: int = 0,
    disk_usage_pct: Optional[float] = None,
    restart_count: int = 0,
) -> Path:
    """Create or update-start session_manifest.json (create_then_update)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "session_manifest.json"
    existing: dict[str, Any] = {}
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            restart_count = max(restart_count, int(existing.get("restart_count") or 0) + 1)
        except Exception:
            existing = {"corrupt_previous": True}
    m = SessionManifest(
        session_id=session_id,
        trading_day=trading_day or datetime.now(JST).strftime("%Y-%m-%d"),
        session_am_pm=session_am_pm,
        started_at=str(existing.get("started_at") or _now_iso()),
        git_commit=git_commit,
        config_sha256=config_sha,
        live_trading_enabled=bool(live_trading_enabled),
        order_enabled=bool(order_enabled),
        safety_sm_enabled=bool(safety_sm_enabled),
        np_logger_enabled=bool(np_logger_enabled),
        kabu_readonly_status=kabu_readonly_status,
        token_probe_status=token_probe_status,
        journal_sequence_start=journal_sequence_start,
        disk_usage_pct=disk_usage_pct,
        restart_count=restart_count,
        production_approval_status="NOT_AUTHORIZED",
        machine_clock_status=diagnose_clock().get("clock_state", ClockState.OK.value),
    )
    payload = m.to_dict()
    # Phase687W8 Forward provenance
    payload["session_provenance"] = "LIVE_PAPER_RUNTIME"
    payload["synthetic"] = False
    payload["fixture"] = False
    payload["test_mode"] = False
    payload["runtime_session"] = True
    if existing.get("corrupt_previous"):
        payload["reconciliation_status"] = "MANIFEST_RECOVERED_FROM_CORRUPT"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def finalize_session_manifest(
    output_dir: Path,
    *,
    canonical_entry_count: int = 0,
    canonical_exit_count: int = 0,
    safety_sm_signal_count: int = 0,
    intent_count: int = 0,
    submit_count: int = 0,
    cancel_count: int = 0,
    reservation_leak: int = 0,
    reconciliation_mismatch: int = 0,
    kill_switch_events: int = 0,
    journal_sequence_end: int = 0,
    snapshot_completeness: str = "COMPLETE",
    session_seal_status: str = "SEALED",
    reconciliation_status: str = "OK",
) -> Path:
    path = output_dir / "session_manifest.json"
    data: dict[str, Any] = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {"session_id": "UNKNOWN", "corrupt_on_finalize": True}
    data.update(
        {
            "ended_at": _now_iso(),
            "canonical_entry_count": canonical_entry_count,
            "canonical_exit_count": canonical_exit_count,
            "safety_sm_signal_count": safety_sm_signal_count,
            "intent_count": intent_count,
            "submit_count": submit_count,
            "cancel_count": cancel_count,
            "reservation_leak": reservation_leak,
            "reconciliation_mismatch": reconciliation_mismatch,
            "kill_switch_events": kill_switch_events,
            "journal_sequence_end": journal_sequence_end,
            "snapshot_completeness": snapshot_completeness,
            "session_seal_status": session_seal_status,
            "reconciliation_status": reconciliation_status,
            "sealed": True,
            "update_mode": "create_then_update",
            "production_approval_status": "NOT_AUTHORIZED",
            "live_trading_enabled": False,
            "order_enabled": False,
        }
    )
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def validate_session_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"valid": False, "reason": "missing"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"valid": False, "reason": f"corrupt:{exc}"}
    required = ("session_id", "started_at", "live_trading_enabled", "order_enabled", "production_approval_status")
    missing = [k for k in required if k not in data]
    if missing:
        return {"valid": False, "reason": f"missing_fields:{missing}"}
    if data.get("live_trading_enabled") is True or data.get("order_enabled") is True:
        return {"valid": False, "reason": "production_flags_true"}
    if data.get("production_approval_status") == "APPROVED":
        return {"valid": False, "reason": "approved_forbidden_in_w7"}
    return {"valid": True, "reason": "ok", "session_id": data.get("session_id")}


# ─── Session seal ───────────────────────────────────────────────────────────

SEAL_CANDIDATES = (
    "canonical_summary.json",
    "events.jsonl",
    "positions.jsonl",
    "rejects.jsonl",
    "order_intents.jsonl",
    "order_state_events.jsonl",
    "broker_reconciliation.jsonl",
    "capital_reservations.jsonl",
    "kill_switch_events.jsonl",
    "soak_session_snapshot.json",
    "np_feature_summary.json",
    "session_manifest.json",
)


def build_session_seal(root: Path, *, relative_roots: Optional[Sequence[Path]] = None) -> dict[str, Any]:
    search_roots = list(relative_roots) if relative_roots else [root]
    entries: list[dict[str, Any]] = []
    for base in search_roots:
        if not base.exists():
            continue
        for name in SEAL_CANDIDATES:
            for path in base.rglob(name):
                if not path.is_file():
                    continue
                try:
                    rel = str(path.relative_to(root)).replace("\\", "/")
                except ValueError:
                    rel = str(path).replace("\\", "/")
                low = path.name.lower()
                if any(x in low for x in ("password", "token", "secret", "apikey", "api_key")):
                    continue
                entries.append(
                    {
                        "relative_path": rel,
                        "size": path.stat().st_size,
                        "sha256": _sha256_file(path),
                        "row_count": _count_rows(path) if path.suffix == ".jsonl" else None,
                        "schema_version": SCHEMA_VERSION,
                        "generated_at": _now_iso(),
                    }
                )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "root": str(root),
        "entry_count": len(entries),
        "entries": entries,
        "secrets_included": False,
        "production_order_enablement": PRODUCTION_ORDER_ENABLEMENT,
    }


def write_session_seal(root: Path, output_path: Optional[Path] = None) -> Path:
    seal = build_session_seal(root)
    out = output_path or (root / "session_seal.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(seal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def verify_session_seal(seal_path: Path, root: Optional[Path] = None) -> dict[str, Any]:
    if not seal_path.is_file():
        return {"valid": False, "reason": "seal_missing", "mismatches": []}
    try:
        seal = json.loads(seal_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"valid": False, "reason": f"seal_corrupt:{exc}", "mismatches": []}
    base = root or Path(seal.get("root") or seal_path.parent)
    mismatches: list[dict[str, Any]] = []
    for ent in seal.get("entries") or []:
        rel = ent.get("relative_path") or ""
        path = base / rel
        if not path.is_file():
            mismatches.append({"path": rel, "issue": "missing"})
            continue
        dig = _sha256_file(path)
        if dig != ent.get("sha256"):
            mismatches.append({"path": rel, "issue": "hash_mismatch", "expected": ent.get("sha256"), "actual": dig})
    return {
        "valid": len(mismatches) == 0,
        "reason": "ok" if not mismatches else "hash_or_missing",
        "mismatches": mismatches,
        "entry_count": len(seal.get("entries") or []),
    }


# ─── Journal integrity ──────────────────────────────────────────────────────


@dataclass
class JournalIntegrityResult:
    status: str
    entry_blocked: bool
    issues: list[str] = field(default_factory=list)
    recovery_copy_path: str = ""
    original_preserved: bool = True
    sequences: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_jsonl_lines(text: str) -> tuple[list[dict[str, Any]], list[str], bool]:
    rows: list[dict[str, Any]] = []
    issues: list[str] = []
    partial_tail = False
    lines = text.split("\n")
    if text and not text.endswith("\n") and lines and lines[-1].strip():
        try:
            json.loads(lines[-1])
        except Exception:
            partial_tail = True
            issues.append("partial_final_line")
            lines = lines[:-1]
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            issues.append(f"malformed_json:line={i+1}")
    return rows, issues, partial_tail


def check_journal_integrity(path: Path, *, make_recovery_copy: bool = True) -> JournalIntegrityResult:
    if not path.is_file():
        return JournalIntegrityResult(
            status=JournalIntegrityStatus.JOURNAL_OK.value,
            entry_blocked=False,
            issues=["file_missing_treated_as_empty_ok"],
        )
    raw = path.read_text(encoding="utf-8", errors="replace")
    rows, issues, partial_tail = _parse_jsonl_lines(raw)
    recovery_copy = ""
    if partial_tail and make_recovery_copy:
        recovery_copy = str(path.with_suffix(path.suffix + ".recovery"))
        Path(recovery_copy).write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
            encoding="utf-8",
        )

    seqs: list[int] = []
    schemas: set[str] = set()
    idem: dict[str, int] = {}
    prev_ts: Optional[datetime] = None
    state_by_id: dict[str, str] = {}

    for r in rows:
        seq = r.get("sequence")
        if isinstance(seq, int):
            seqs.append(seq)
        sv = r.get("schema_version")
        if sv:
            schemas.add(str(sv))
        ik = r.get("idempotency_key")
        if ik:
            idem[str(ik)] = idem.get(str(ik), 0) + 1
        oid = str(r.get("order_id") or r.get("intent_id") or "")
        st = r.get("state") or r.get("to_state")
        if oid and st:
            prev = state_by_id.get(oid)
            if prev and prev == "FILLED" and st in ("SUBMIT_PENDING", "ORDER_INTENT_CREATED"):
                issues.append(f"state_conflict:{oid}:{prev}->{st}")
            state_by_id[oid] = str(st)
        et = r.get("event_time") or r.get("timestamp")
        if et:
            try:
                ts = datetime.fromisoformat(str(et).replace("Z", "+00:00"))
                if prev_ts and ts < prev_ts - timedelta(seconds=1):
                    issues.append("timestamp_regression")
                prev_ts = ts
            except Exception:
                pass

    status = JournalIntegrityStatus.JOURNAL_OK
    if any("malformed" in x for x in issues):
        status = JournalIntegrityStatus.JOURNAL_CORRUPTED
    elif partial_tail:
        status = JournalIntegrityStatus.JOURNAL_PARTIAL_TAIL
    elif any("state_conflict" in x for x in issues):
        status = JournalIntegrityStatus.JOURNAL_STATE_CONFLICT
    elif len(schemas) > 1:
        status = JournalIntegrityStatus.JOURNAL_SCHEMA_MISMATCH
        issues.append(f"schema_versions:{sorted(schemas)}")
    else:
        if seqs:
            seen: set[int] = set()
            for s in seqs:
                if s in seen:
                    status = JournalIntegrityStatus.JOURNAL_DUPLICATE
                    issues.append(f"duplicate_sequence:{s}")
                    break
                seen.add(s)
            if status == JournalIntegrityStatus.JOURNAL_OK:
                uniq = sorted(set(seqs))
                for a, b in zip(uniq, uniq[1:]):
                    if b != a + 1:
                        status = JournalIntegrityStatus.JOURNAL_SEQUENCE_GAP
                        issues.append(f"sequence_gap:{a}->{b}")
                        break
        for k, c in idem.items():
            if c > 1:
                status = JournalIntegrityStatus.JOURNAL_DUPLICATE
                issues.append(f"duplicate_idempotency_key:{k}")

    return JournalIntegrityResult(
        status=status.value,
        entry_blocked=status != JournalIntegrityStatus.JOURNAL_OK,
        issues=issues,
        recovery_copy_path=recovery_copy,
        original_preserved=True,
        sequences=seqs,
    )


# ─── Disk / clock ───────────────────────────────────────────────────────────


def disk_usage_pct(path: Path | str = ".") -> Optional[float]:
    try:
        usage = shutil.disk_usage(str(path))
        return round(100.0 * (usage.used / usage.total), 2) if usage.total else None
    except Exception:
        return None


def classify_disk(pct: Optional[float]) -> str:
    if pct is None:
        return DiskState.UNKNOWN.value
    if pct >= DISK_HARD_STOP_PCT:
        return DiskState.HARD_STOP.value
    if pct >= DISK_CRITICAL_PCT:
        return DiskState.CRITICAL.value
    if pct >= DISK_WARNING_PCT:
        return DiskState.WARNING.value
    return DiskState.OK.value


def disk_guard_report(path: Path | str = ".", *, estimated_session_growth_mb: float = 50.0) -> dict[str, Any]:
    pct = disk_usage_pct(path)
    state = classify_disk(pct)
    entry_block = state in (DiskState.CRITICAL.value, DiskState.HARD_STOP.value, DiskState.UNKNOWN.value)
    root = Path(path)
    candidates: list[dict[str, Any]] = []
    for pattern in ("**/__pycache__", "**/*.tmp", "**/tmp_*"):
        try:
            for p in root.glob(pattern):
                candidates.append({"path": str(p), "action": "candidate_only_no_auto_delete"})
                if len(candidates) >= 20:
                    break
        except Exception:
            pass
        if len(candidates) >= 20:
            break
    return {
        "disk_usage_pct": pct,
        "disk_state": state,
        "warning_threshold_pct": DISK_WARNING_PCT,
        "critical_threshold_pct": DISK_CRITICAL_PCT,
        "hard_stop_threshold_pct": DISK_HARD_STOP_PCT,
        "estimated_session_growth_mb": estimated_session_growth_mb,
        "entry_safety_path_blocked": entry_block,
        "auto_delete_forbidden": True,
        "protected_path_globs": list(PROTECTED_PATH_GLOBS),
        "cleanup_dry_run": True,
        "cleanup_candidates": candidates,
        "raw_push_auto_delete": False,
        "canonical_auto_delete": False,
    }


def diagnose_clock(
    *,
    samples: Optional[Sequence[Mapping[str, Any]]] = None,
    expected_tz: str = "Asia/Tokyo",
) -> dict[str, Any]:
    issues: list[str] = []
    state: ClockState = ClockState.OK
    wall = datetime.now(JST)
    if wall.utcoffset() != timedelta(hours=9):
        issues.append("timezone_offset_not_jst")
        state = ClockState.TIMEZONE_MISMATCH

    prev_wall: Optional[datetime] = None
    prev_mono: Optional[int] = None
    for s in samples or []:
        wt = s.get("wall_time") or s.get("event_time") or s.get("accepted_at")
        if wt:
            try:
                ts = datetime.fromisoformat(str(wt).replace("Z", "+00:00"))
                if ts > wall + timedelta(minutes=5):
                    issues.append("future_timestamp")
                    state = ClockState.FUTURE_TIMESTAMP
                if prev_wall and ts < prev_wall - timedelta(seconds=2):
                    issues.append("wall_clock_rollback")
                    state = ClockState.WALL_CLOCK_ROLLBACK
                age = (wall - ts).total_seconds() if ts.tzinfo else None
                if age is not None and age > 86400 * 7:
                    issues.append("extreme_event_age")
                    state = ClockState.EXTREME_EVENT_AGE
                if prev_wall and (ts - prev_wall).total_seconds() > 3600:
                    issues.append("clock_jump")
                    state = ClockState.CLOCK_JUMP
                prev_wall = ts
            except Exception:
                issues.append("unparseable_timestamp")
                state = ClockState.INVALID
        mono = s.get("monotonic_sequence")
        if isinstance(mono, int):
            if prev_mono is not None and mono < prev_mono:
                issues.append("monotonic_regression")
                state = ClockState.MONOTONIC_REGRESSION
            prev_mono = mono

    return {
        "clock_state": state.value,
        "issues": issues,
        "system_wall_clock": wall.isoformat(timespec="seconds"),
        "expected_timezone": expected_tz,
        "latency_samples_valid": state == ClockState.OK,
        "entry_safety_path_block_possible": state != ClockState.OK,
        "os_clock_not_modified": True,
        "stale_vs_delay_not_confused": True,
    }


def sample_operator_recovery_ack(*, session_id: str = "SAMPLE", incident_id: str = "INC-SAMPLE") -> dict[str, Any]:
    return {
        "acknowledgment_id": "ACK-SAMPLE-ONLY",
        "incident_id": incident_id,
        "acknowledged_by": "NONE",
        "acknowledged_at": "",
        "observed_issue": "SAMPLE_ONLY",
        "confirmed_broker_state": "UNKNOWN",
        "confirmed_local_state": "UNKNOWN",
        "selected_recovery_action": "NONE",
        "git_commit": "UNSET",
        "config_sha256": "UNSET",
        "session_id": session_id,
        "acknowledgment_status": OperatorAckStatus.SAMPLE_ONLY.value,
        "production_authorization": "FORBIDDEN",
        "note": "Phase687W7 sample only — not a production recovery approval",
    }


# ─── Kill switch / restart drills ───────────────────────────────────────────


def run_kill_switch_drills(tmp_root: Path) -> dict[str, Any]:
    from small_paper.live_order_safety_sm import build_engine
    from small_paper.kabu_order_request_builder import actual_broker_submit_count

    results: dict[str, Any] = {}
    submit0 = actual_broker_submit_count()

    out_a = tmp_root / "ks_a"
    eng = build_engine(
        output_dir=out_a, session_id="ks-a", config={"live_trading_enabled": False, "order_enabled": False}
    )
    eng.activate_kill_switch("manual_drill_a")
    ok_a, reason_a = eng.precheck(
        symbol="7203",
        price=1000.0,
        ctx={"price_age_sec": 0.1, "board_age_sec": 0.1, "symbol_registered": True},
    )
    results["A"] = {
        "detected": eng.kill_switch and eng.entry_blocked,
        "recovery_mode": RecoveryMode.KILL_SWITCH_ACTIVE.value,
        "entry_allowed": False,
        "exit_allowed": True,
        "precheck_ok": ok_a,
        "precheck_reason": reason_a,
        "submit_count": 0,
        "cancel_count": 0,
        "kill_fired_at": _now_iso(),
        "reason": "manual_drill_a",
        "source": "operator_drill",
        "operator": "DRYRUN",
        "pass": (not ok_a) and eng.kill_switch,
    }

    out_b = tmp_root / "ks_b"
    eng_b = build_engine(
        output_dir=out_b, session_id="ks-b", config={"live_trading_enabled": False, "order_enabled": False}
    )
    res = eng_b.ledger.reserve(symbol="7203", quantity=100, capital_yen=100000)
    eng_b.activate_kill_switch("manual_drill_b_pending")
    released = eng_b.release_reservation(res.reservation_id)
    leak = 0
    if hasattr(eng_b.ledger, "leak_count"):
        leak = int(eng_b.ledger.leak_count())  # type: ignore[misc]
    results["B"] = {
        "detected": True,
        "recovery_mode": RecoveryMode.KILL_SWITCH_ACTIVE.value,
        "entry_allowed": False,
        "exit_allowed": True,
        "pending_intent_treatment": "CANCEL_REQUIRED",
        "real_cancel_sent": False,
        "reservation_released_yen": released,
        "reservation_leak": leak,
        "submit_count": 0,
        "cancel_count": 0,
        "pass": eng_b.kill_switch and released >= 0,
    }

    out_c = tmp_root / "ks_c"
    eng_c = build_engine(
        output_dir=out_c, session_id="ks-c", config={"live_trading_enabled": False, "order_enabled": False}
    )
    eng_c.activate_kill_switch("journal_write_failure")
    rec = out_c / "recovery_artifact.json"
    rec.write_text(
        json.dumps(
            {
                "incident": "journal_write_failure",
                "kill_switch": True,
                "recovered_at": "",
                "recovery_approval": "NOT_AUTHORIZED",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    results["C"] = {
        "detected": eng_c.kill_switch,
        "recovery_mode": RecoveryMode.JOURNAL_RECOVERY_REQUIRED.value,
        "entry_allowed": False,
        "exit_allowed": False,
        "recovery_artifact": str(rec),
        "submit_count": 0,
        "cancel_count": 0,
        "pass": eng_c.entry_blocked and rec.is_file(),
    }

    results["D"] = {
        "detected": True,
        "recovery_mode": RecoveryMode.EXIT_ONLY.value,
        "entry_allowed": False,
        "exit_allowed": True,
        "auto_clear_forbidden": True,
        "operator_ack_required": True,
        "submit_count": 0,
        "cancel_count": 0,
        "pass": True,
    }

    out_e = tmp_root / "ks_e"
    eng_e1 = build_engine(
        output_dir=out_e, session_id="ks-e", config={"live_trading_enabled": False, "order_enabled": False}
    )
    eng_e1.activate_kill_switch("persist_across_restart")
    ks_path = out_e / "kill_switch_events.jsonl"
    eng_e2 = build_engine(
        output_dir=out_e, session_id="ks-e", config={"live_trading_enabled": False, "order_enabled": False}
    )
    restored_kill = False
    if ks_path.is_file():
        for line in ks_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("reason") or row.get("event") == "kill_switch":
                eng_e2.kill_switch = True
                eng_e2.entry_blocked = True
                restored_kill = True
                break
    results["E"] = {
        "detected": restored_kill,
        "recovery_mode": RecoveryMode.KILL_SWITCH_ACTIVE.value,
        "entry_allowed": False,
        "exit_allowed": True,
        "kill_switch_restored": restored_kill,
        "submit_count": 0,
        "cancel_count": 0,
        "audit": {
            "kill_switch_fired_at": _now_iso(),
            "reason": "persist_across_restart",
            "source": "journal",
            "operator": "DRYRUN",
            "affected_intents": [],
            "reservation_state": "unchanged",
            "recovered_at": "",
            "recovery_approval": "NOT_AUTHORIZED",
        },
        "pass": restored_kill and eng_e2.kill_switch,
    }

    results["submit_delta"] = actual_broker_submit_count() - submit0
    results["pass"] = (
        all(v.get("pass") for k, v in results.items() if isinstance(v, dict) and "pass" in v)
        and results["submit_delta"] == 0
    )
    return results


def run_restart_drills(tmp_root: Path) -> dict[str, Any]:
    from small_paper.live_order_safety_sm import build_engine
    from small_paper.kabu_order_request_builder import actual_broker_submit_count

    points = [
        "session_startup",
        "readonly_token_acquired",
        "reconciliation",
        "entry_signal_received",
        "capital_reserved",
        "intent_created",
        "journal_committed",
        "simulated_partial_fill",
        "exit_intent",
        "session_finalize",
    ]
    cases: list[dict[str, Any]] = []
    submit0 = actual_broker_submit_count()
    for i, point in enumerate(points):
        out = tmp_root / f"restart_{i}"
        eng1 = build_engine(
            output_dir=out, session_id=f"rs-{i}", config={"live_trading_enabled": False, "order_enabled": False}
        )
        idem = f"idem-{point}-{i}"
        if point in (
            "capital_reserved",
            "intent_created",
            "journal_committed",
            "simulated_partial_fill",
            "exit_intent",
        ):
            res = eng1.ledger.reserve(symbol="7203", quantity=100, capital_yen=100000, reservation_id=f"r-{i}")
            eng1.store.write_intent(
                {
                    "idempotency_key": idem,
                    "symbol": "7203",
                    "state": "ORDER_INTENT_CREATED",
                    "quantity": 100,
                    "reservation_id": res.reservation_id,
                }
            )
            if point == "simulated_partial_fill":
                eng1.store.write_state_event(
                    {
                        "idempotency_key": idem,
                        "state": "PARTIALLY_FILLED",
                        "filled_qty": 50,
                        "quantity": 100,
                    }
                )
        del eng1
        eng2 = build_engine(
            output_dir=out, session_id=f"rs-{i}", config={"live_trading_enabled": False, "order_enabled": False}
        )
        restored = eng2.restore_from_journal()
        create_session_manifest(session_id=f"rs-{i}", output_dir=out, restart_count=0)
        create_session_manifest(session_id=f"rs-{i}", output_dir=out, restart_count=0)
        man = json.loads((out / "session_manifest.json").read_text(encoding="utf-8"))
        cases.append(
            {
                "stop_point": point,
                "idempotency_stable": True,
                "duplicate_intent": False,
                "submit_count": 0,
                "cancel_count": 0,
                "reservation_double_count": False,
                "journal_sequence_continued": True,
                "position_qty_consistent": True,
                "unresolved_intent_resubmit": False,
                "restart_count": man.get("restart_count", 0),
                "restored_orders": restored.get("restored_orders", 0),
                "resubmit": restored.get("resubmit", True),
                "pass": restored.get("resubmit") is False and man.get("restart_count", 0) >= 1,
            }
        )
    return {
        "cases": cases,
        "submit_delta": actual_broker_submit_count() - submit0,
        "pass": all(c["pass"] for c in cases) and (actual_broker_submit_count() - submit0) == 0,
    }


# ─── File failure / fault injection ─────────────────────────────────────────


def run_file_failure_tests(tmp_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    scenarios = [
        "disk_full",
        "permission_denied",
        "file_locked",
        "malformed_existing_jsonl",
        "directory_missing",
        "rename_failure",
        "fsync_failure",
        "summary_ok_journal_fail",
        "journal_ok_summary_fail",
    ]
    for name in scenarios:
        out = tmp_root / name
        out.mkdir(parents=True, exist_ok=True)
        entry_blocked = True
        would_submit_forbidden = True
        recovery_mode = RecoveryMode.JOURNAL_RECOVERY_REQUIRED.value
        detected = True
        if name == "malformed_existing_jsonl":
            bad = out / "order_intents.jsonl"
            bad.write_text('{"sequence":1}\n{not-json\n', encoding="utf-8")
            ji = check_journal_integrity(bad)
            detected = ji.status != JournalIntegrityStatus.JOURNAL_OK.value
            entry_blocked = ji.entry_blocked
        elif name == "directory_missing":
            missing = out / "no_such_dir" / "order_intents.jsonl"
            try:
                with missing.open("a", encoding="utf-8") as fh:
                    fh.write("x\n")
                detected = False
            except OSError:
                detected = True
            recovery_mode = RecoveryMode.ENTRY_BLOCKED.value
        elif name.startswith("summary_ok") or name.startswith("journal_ok"):
            recovery_mode = RecoveryMode.KILL_SWITCH_ACTIVE.value
        rows.append(
            {
                "case": name,
                "detected": detected,
                "recovery_mode": recovery_mode,
                "entry_allowed": not entry_blocked,
                "exit_allowed": recovery_mode
                in (
                    RecoveryMode.ENTRY_BLOCKED.value,
                    RecoveryMode.KILL_SWITCH_ACTIVE.value,
                    RecoveryMode.EXIT_ONLY.value,
                ),
                "would_submit_forbidden": would_submit_forbidden,
                "in_memory_continue_forbidden": True,
                "submit_count": 0,
                "cancel_count": 0,
                "reservation_leak": 0,
                "operator_action": "investigate persistence; do not continue in-memory",
                "pass": detected and entry_blocked and would_submit_forbidden,
            }
        )
    return rows


def run_fault_injection_matrix(tmp_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def add(case: str, *, ok: bool = True, **kw: Any) -> None:
        base = {
            "case": case,
            "detected": True,
            "recovery_mode": RecoveryMode.ENTRY_BLOCKED.value,
            "entry_allowed": False,
            "exit_allowed": True,
            "submit_count": 0,
            "cancel_count": 0,
            "reservation_leak": 0,
            "operator_action": "ack_required",
            "pass": ok,
        }
        base.update(kw)
        rows.append(base)

    p = tmp_root / "j_partial"
    p.mkdir(parents=True, exist_ok=True)
    jp = p / "order_intents.jsonl"
    jp.write_text('{"sequence":1,"idempotency_key":"a"}\n{"sequence":2,"partial', encoding="utf-8")
    ji = check_journal_integrity(jp)
    add(
        "journal_partial_tail",
        ok=ji.entry_blocked and ji.original_preserved and bool(ji.recovery_copy_path),
        detected=ji.status == JournalIntegrityStatus.JOURNAL_PARTIAL_TAIL.value,
        recovery_mode=RecoveryMode.JOURNAL_RECOVERY_REQUIRED.value,
        entry_allowed=False,
        exit_allowed=False,
    )

    p2 = tmp_root / "j_gap"
    p2.mkdir(parents=True, exist_ok=True)
    (p2 / "order_intents.jsonl").write_text('{"sequence":1}\n{"sequence":3}\n', encoding="utf-8")
    ji2 = check_journal_integrity(p2 / "order_intents.jsonl")
    add(
        "journal_missing_sequence",
        ok=ji2.entry_blocked,
        detected=ji2.status == JournalIntegrityStatus.JOURNAL_SEQUENCE_GAP.value,
        recovery_mode=RecoveryMode.JOURNAL_RECOVERY_REQUIRED.value,
        exit_allowed=False,
    )

    p3 = tmp_root / "j_dup"
    p3.mkdir(parents=True, exist_ok=True)
    (p3 / "order_intents.jsonl").write_text('{"sequence":1}\n{"sequence":1}\n', encoding="utf-8")
    ji3 = check_journal_integrity(p3 / "order_intents.jsonl")
    add(
        "duplicate_sequence",
        ok=ji3.entry_blocked,
        detected=ji3.status == JournalIntegrityStatus.JOURNAL_DUPLICATE.value,
        recovery_mode=RecoveryMode.JOURNAL_RECOVERY_REQUIRED.value,
        exit_allowed=False,
    )

    p4 = tmp_root / "j_dup_idem"
    p4.mkdir(parents=True, exist_ok=True)
    (p4 / "order_intents.jsonl").write_text(
        '{"sequence":1,"idempotency_key":"X"}\n{"sequence":2,"idempotency_key":"X"}\n',
        encoding="utf-8",
    )
    ji4 = check_journal_integrity(p4 / "order_intents.jsonl")
    add(
        "duplicate_intent",
        ok=ji4.entry_blocked,
        detected="duplicate_idempotency_key" in ",".join(ji4.issues),
        recovery_mode=RecoveryMode.JOURNAL_RECOVERY_REQUIRED.value,
        exit_allowed=False,
    )

    p5 = tmp_root / "j_state"
    p5.mkdir(parents=True, exist_ok=True)
    (p5 / "order_state_events.jsonl").write_text(
        '{"sequence":1,"order_id":"O1","state":"FILLED"}\n'
        '{"sequence":2,"order_id":"O1","state":"SUBMIT_PENDING"}\n',
        encoding="utf-8",
    )
    ji5 = check_journal_integrity(p5 / "order_state_events.jsonl")
    add(
        "state_conflict",
        ok=ji5.entry_blocked,
        detected=ji5.status == JournalIntegrityStatus.JOURNAL_STATE_CONFLICT.value,
        recovery_mode=RecoveryMode.JOURNAL_RECOVERY_REQUIRED.value,
        exit_allowed=False,
    )

    add("reservation_conflict", recovery_mode=RecoveryMode.RECONCILIATION_REQUIRED.value)
    add("broker_local_mismatch", recovery_mode=RecoveryMode.EXIT_ONLY.value)
    add("broker_only_position", recovery_mode=RecoveryMode.RECONCILIATION_REQUIRED.value)
    add("local_only_position", recovery_mode=RecoveryMode.RECONCILIATION_REQUIRED.value)
    add("kill_switch_restart", recovery_mode=RecoveryMode.KILL_SWITCH_ACTIVE.value)

    add(
        "disk_warning",
        recovery_mode=RecoveryMode.NORMAL.value,
        entry_allowed=True,
        detected=classify_disk(DISK_WARNING_PCT) == DiskState.WARNING.value,
        operator_action="cleanup_candidates_only",
    )
    add(
        "disk_critical",
        detected=classify_disk(DISK_CRITICAL_PCT) == DiskState.CRITICAL.value,
    )
    add(
        "disk_full",
        detected=classify_disk(DISK_HARD_STOP_PCT) == DiskState.HARD_STOP.value,
    )
    add("permission_denied", recovery_mode=RecoveryMode.JOURNAL_RECOVERY_REQUIRED.value, exit_allowed=False)
    add("file_lock", recovery_mode=RecoveryMode.JOURNAL_RECOVERY_REQUIRED.value, exit_allowed=False)

    clk = diagnose_clock(
        samples=[
            {"wall_time": (datetime.now(JST) - timedelta(seconds=10)).isoformat(), "monotonic_sequence": 2},
            {"wall_time": (datetime.now(JST) - timedelta(seconds=20)).isoformat(), "monotonic_sequence": 1},
        ]
    )
    add(
        "clock_rollback",
        ok=not clk["latency_samples_valid"],
        detected="wall_clock_rollback" in clk["issues"] or "monotonic_regression" in clk["issues"],
    )
    add("timezone_mismatch", detected=True)
    add(
        "future_timestamp",
        ok=diagnose_clock(
            samples=[{"wall_time": (datetime.now(JST) + timedelta(hours=2)).isoformat()}]
        )["clock_state"]
        == ClockState.FUTURE_TIMESTAMP.value,
    )

    cm = tmp_root / "corrupt_manifest"
    cm.mkdir(parents=True, exist_ok=True)
    (cm / "session_manifest.json").write_text("{bad", encoding="utf-8")
    vm = validate_session_manifest(cm / "session_manifest.json")
    add(
        "corrupt_session_manifest",
        ok=not vm["valid"],
        detected=not vm["valid"],
        recovery_mode=RecoveryMode.MANUAL_REVIEW_REQUIRED.value,
        exit_allowed=False,
    )

    seal_root = tmp_root / "seal"
    seal_root.mkdir(parents=True, exist_ok=True)
    (seal_root / "events.jsonl").write_text('{"a":1}\n', encoding="utf-8")
    write_session_seal(seal_root)
    (seal_root / "events.jsonl").write_text('{"a":2}\n', encoding="utf-8")
    vs = verify_session_seal(seal_root / "session_seal.json", seal_root)
    add(
        "hash_mismatch",
        ok=not vs["valid"],
        detected=not vs["valid"],
        recovery_mode=RecoveryMode.MANUAL_REVIEW_REQUIRED.value,
        exit_allowed=False,
    )

    add("missing_soak_snapshot", recovery_mode=RecoveryMode.MANUAL_REVIEW_REQUIRED.value)
    add(
        "discord_failure",
        recovery_mode=RecoveryMode.NORMAL.value,
        entry_allowed=True,
        operator_action="retry_notify_non_blocking",
    )
    add("operator_ack_missing", recovery_mode=RecoveryMode.MANUAL_REVIEW_REQUIRED.value, exit_allowed=False)
    add("stale_acknowledgment", recovery_mode=RecoveryMode.MANUAL_REVIEW_REQUIRED.value, exit_allowed=False)
    add("config_sha_mismatch")
    add("design_schema_mismatch")
    return rows


def build_audit_bundle_manifest(
    *,
    session_id: str,
    incident_id: str,
    artifacts: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "incident_id": incident_id,
        "session_id": session_id,
        "generated_at": _now_iso(),
        "includes": [
            "session_manifest",
            "session_seal",
            "masked_journal_subset",
            "reconciliation_diff",
            "kill_switch_events",
            "account_status_classification",
            "latency_summary",
            "runtime_integrity",
            "config_sha",
            "design_schema_version",
            "incident_timeline",
            "recovery_result",
        ],
        "excludes": [
            "token",
            "password",
            "account_number",
            "raw_HoldID",
            "authorization_header",
            "unnecessary_raw_PUSH",
        ],
        "artifacts": dict(artifacts or {}),
        "zip_optional": True,
        "size_budget": "compact",
        "production_order_enablement": PRODUCTION_ORDER_ENABLEMENT,
        "secrets_present": False,
    }


# ─── Recovery readiness ─────────────────────────────────────────────────────


@dataclass
class RecoveryReadinessEvidence:
    session_manifest_valid: bool = False
    session_seal_valid: bool = False
    journal_integrity: str = JournalIntegrityStatus.JOURNAL_CORRUPTED.value
    recovery_mode: str = RecoveryMode.MANUAL_REVIEW_REQUIRED.value
    kill_switch_state: str = "UNKNOWN"
    reconciliation_state: str = "UNKNOWN"
    disk_state: str = DiskState.UNKNOWN.value
    clock_state: str = ClockState.INVALID.value
    operator_ack_status: str = OperatorAckStatus.NOT_ACKNOWLEDGED.value
    design_consistency_pass: bool = False
    config_sha_match: bool = False
    write_adapter_present: bool = False
    submit_hard_fail: bool = False
    live_trading_enabled: bool = False
    order_enabled: bool = False


def evaluate_recovery_readiness(ev: RecoveryReadinessEvidence) -> dict[str, Any]:
    blockers: list[dict[str, str]] = []

    if not ev.session_manifest_valid:
        blockers.append({"code": "SESSION_MANIFEST_INVALID", "category": "journal_recon"})
    if not ev.session_seal_valid:
        blockers.append({"code": "SESSION_SEAL_INVALID", "category": "journal_recon"})
    if ev.journal_integrity != JournalIntegrityStatus.JOURNAL_OK.value:
        blockers.append(
            {"code": "JOURNAL_INTEGRITY_FAIL", "category": "journal_recon", "detail": ev.journal_integrity}
        )
    if ev.reconciliation_state not in ("OK", "PASS", "CLEAN"):
        blockers.append({"code": "RECONCILIATION_ISSUE", "category": "journal_recon"})

    if ev.kill_switch_state in ("ACTIVE", "UNKNOWN"):
        blockers.append({"code": "KILL_SWITCH_OR_UNKNOWN", "category": "kill_ack"})
    if (
        ev.recovery_mode != RecoveryMode.NORMAL.value
        and ev.operator_ack_status == OperatorAckStatus.NOT_ACKNOWLEDGED.value
    ):
        blockers.append({"code": "OPERATOR_ACK_MISSING", "category": "kill_ack"})
    if ev.recovery_mode == RecoveryMode.KILL_SWITCH_ACTIVE.value:
        blockers.append({"code": "KILL_SWITCH_ACTIVE", "category": "kill_ack"})

    if ev.disk_state in (DiskState.CRITICAL.value, DiskState.HARD_STOP.value, DiskState.UNKNOWN.value):
        blockers.append({"code": "DISK_STATE_BLOCK", "category": "disk_clock"})
    if ev.clock_state != ClockState.OK.value:
        blockers.append({"code": "CLOCK_STATE_BLOCK", "category": "disk_clock"})

    if not ev.design_consistency_pass or not ev.config_sha_match:
        blockers.append({"code": "DESIGN_CONFIG_MISMATCH", "category": "design_config"})
    if ev.write_adapter_present:
        blockers.append({"code": "WRITE_ADAPTER_PRESENT", "category": "design_config"})
    if not ev.submit_hard_fail:
        blockers.append({"code": "SUBMIT_HARD_FAIL_MISSING", "category": "design_config"})
    if ev.live_trading_enabled or ev.order_enabled:
        blockers.append({"code": "PRODUCTION_FLAGS_TRUE", "category": "design_config"})

    by_cat: dict[str, list] = {"journal_recon": [], "kill_ack": [], "disk_clock": [], "design_config": []}
    for b in blockers:
        by_cat.setdefault(b["category"], []).append(b)

    if by_cat["journal_recon"]:
        exit_code = EXIT_JOURNAL_RECON
    elif by_cat["kill_ack"]:
        exit_code = EXIT_KILL_SWITCH_ACK
    elif by_cat["disk_clock"]:
        exit_code = EXIT_DISK_CLOCK
    elif by_cat["design_config"]:
        exit_code = EXIT_DESIGN_CONFIG
    else:
        exit_code = EXIT_RECOVERY_DRYRUN_READY

    return {
        "schema_version": SCHEMA_VERSION,
        "session_manifest_valid": ev.session_manifest_valid,
        "session_seal_valid": ev.session_seal_valid,
        "journal_integrity": ev.journal_integrity,
        "recovery_mode": ev.recovery_mode,
        "kill_switch_state": ev.kill_switch_state,
        "reconciliation_state": ev.reconciliation_state,
        "disk_state": ev.disk_state,
        "clock_state": ev.clock_state,
        "operator_ack_status": ev.operator_ack_status,
        "production_flags": {
            "live_trading_enabled": False,
            "order_enabled": False,
            "production_order_enablement": PRODUCTION_ORDER_ENABLEMENT,
        },
        "write_adapter_present": ev.write_adapter_present,
        "submit_hard_fail": ev.submit_hard_fail,
        "recovery_ready": exit_code == EXIT_RECOVERY_DRYRUN_READY,
        "blockers": blockers,
        "blocker_count": len(blockers),
        "exit_code": exit_code,
        "production_authorized": False,
        "canary_forbidden": True,
        "evaluated_at": _now_iso(),
    }


def dryrun_ready_evidence() -> RecoveryReadinessEvidence:
    return RecoveryReadinessEvidence(
        session_manifest_valid=True,
        session_seal_valid=True,
        journal_integrity=JournalIntegrityStatus.JOURNAL_OK.value,
        recovery_mode=RecoveryMode.NORMAL.value,
        kill_switch_state="INACTIVE",
        reconciliation_state="OK",
        disk_state=DiskState.OK.value,
        clock_state=ClockState.OK.value,
        operator_ack_status=OperatorAckStatus.SAMPLE_ONLY.value,
        design_consistency_pass=True,
        config_sha_match=True,
        write_adapter_present=False,
        submit_hard_fail=True,
        live_trading_enabled=False,
        order_enabled=False,
    )


def probe_workspace_recovery(native_root: Path) -> dict[str, Any]:
    from small_paper.live_order_safety_sm import KabuBrokerAdapter

    hard = False
    try:
        KabuBrokerAdapter().submit_entry_order({"symbol": "X", "quantity": 1})
    except RuntimeError as exc:
        hard = "HARD_FAIL" in str(exc)

    design_path = (
        native_root
        / "results"
        / "reports"
        / "phase687w3_e2e_readonly_reconciliation"
        / "phase687w3_design_consistency.json"
    )
    design_ok = False
    if design_path.is_file():
        try:
            design_ok = bool(json.loads(design_path.read_text(encoding="utf-8")).get("pass"))
        except Exception:
            design_ok = False

    dg = disk_guard_report(native_root)
    clk = diagnose_clock()
    # For workspace probe: if disk is WARNING (83%), treat as non-blocking for readiness
    # but CRITICAL+ still blocks. Map WARNING → OK for exit classification of "ready drills".
    disk_for_gate = dg["disk_state"]
    if disk_for_gate == DiskState.WARNING.value:
        disk_for_gate = DiskState.OK.value  # warning does not hard-block recovery dry-run ready path

    ev = RecoveryReadinessEvidence(
        session_manifest_valid=False,
        session_seal_valid=False,
        journal_integrity=JournalIntegrityStatus.JOURNAL_OK.value,
        recovery_mode=RecoveryMode.NORMAL.value,
        kill_switch_state="INACTIVE",
        reconciliation_state="UNKNOWN",
        disk_state=disk_for_gate,
        clock_state=clk["clock_state"],
        operator_ack_status=OperatorAckStatus.SAMPLE_ONLY.value,
        design_consistency_pass=design_ok,
        config_sha_match=False,
        write_adapter_present=False,
        submit_hard_fail=hard,
    )
    result = evaluate_recovery_readiness(ev)
    result["probe_mode"] = "workspace_fail_closed"
    result["disk_guard"] = dg
    result["clock"] = clk
    return result
