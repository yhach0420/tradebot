"""Phase687W8 — One-command Paper Trade checked orchestrator.

Runs prechecks → existing run_paper_trade.bat (once) → post W4S evaluation.
Does not mutate strategy flags, YAML, or run_paper_trade.bat internals.
"""

from __future__ import annotations

import argparse
import atexit
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = NATIVE_ROOT.parent
DEFAULT_PAPER_BAT = REPO_ROOT / "run_paper_trade.bat"
DEFAULT_CONFIG = (
    NATIVE_ROOT
    / "configs"
    / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
)
LOG_DIR = NATIVE_ROOT / "results" / "reports" / "paper_trade_checked_runner"

SECRET_PATTERNS = (
    re.compile(r"(?i)(password|api[_-]?password|token|apikey|api_key|secret)\s*[:=]\s*\S+"),
    re.compile(r"(?i)\"(password|token|api_key|apikey|secret)\"\s*:\s*\"[^\"]+\""),
)

VERDICT_READY = "ONE_COMMAND_PAPER_RUNNER_READY"
VERDICT_PRECHECK = "PRECHECK_ORCHESTRATION_FAILED"
VERDICT_SAFETY = "SAFETY_FLAG_VALIDATION_FAILED"
VERDICT_POST = "POST_SESSION_EVALUATION_FAILED"
VERDICT_BAT_MOD = "EXISTING_RUNNER_MODIFIED"
VERDICT_CAPTURE_REQUIRED = "CAPTURE_REQUIRED_NOT_READY"

SEAL_PROPAGATION_OK = "SEAL_PROPAGATION_OK"
LIVE_PAPER_PROVENANCE = "LIVE_PAPER_RUNTIME"
PROVENANCE_TEST_FIXTURE = "TEST_FIXTURE"
PROVENANCE_SYNTHETIC = "SYNTHETIC"

# Path markers that must never enter Forward soak counts
_FORWARD_PATH_EXCLUDE_MARKERS = (
    "_test_",
    "_w8_test_",
    "/tests/",
    "\\tests\\",
    "/fixture",
    "\\fixture",
    "fixture/",
    "fixture\\",
    "/synthetic",
    "\\synthetic",
    "synthetic/",
    "synthetic\\",
    "/tmp/",
    "\\tmp\\",
    "/temp/",
    "\\temp\\",
    "temporarydirectory",
    "appdata\\local\\temp",
    "appdata/local/temp",
    "/results/reports/",
    "\\results\\reports\\",
    "phase687w",  # phase report sample trees under reports already covered; keep soft
)


def is_excluded_forward_path(path: Path | str) -> tuple[bool, str]:
    """Return (excluded, reason) for Forward soak / sessions_collected."""
    raw = str(path).replace("\\", "/").lower()
    name = Path(path).name.lower()
    if "_w8_test_" in raw or raw.rstrip("/").endswith("_w8_test_qualified") or "/_w8_test_" in raw:
        return True, "path:_w8_test_*"
    if "_test_" in raw:
        return True, "path:_test_"
    if "/tests/" in raw or raw.endswith("/tests"):
        return True, "path:tests"
    if "fixture" in raw:
        return True, "path:fixture"
    if "synthetic" in raw:
        return True, "path:synthetic"
    if "/results/reports/" in raw or raw.startswith("results/reports/") or "\\results\\reports\\" in str(path).lower():
        return True, "path:results/reports"
    if "temporarydirectory" in raw or "/tmp/" in raw or "appdata/local/temp" in raw:
        return True, "path:temp"
    if "pytest-" in name or "pytest-" in raw:
        return True, "path:pytest"
    return False, ""


def read_session_provenance(
    snap: Mapping[str, Any] | None = None,
    manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge provenance from snapshot then manifest."""
    snap_d = dict(snap or {})
    man_d = dict(manifest or {})

    def pick(key: str, default: Any = None) -> Any:
        if key in snap_d and snap_d[key] is not None:
            return snap_d[key]
        if key in man_d and man_d[key] is not None:
            return man_d[key]
        return default

    return {
        "session_provenance": str(pick("session_provenance", "") or ""),
        "synthetic": bool(pick("synthetic", False)),
        "fixture": bool(pick("fixture", False)),
        "test_mode": bool(pick("test_mode", False)),
        "runtime_session": bool(pick("runtime_session", False)),
    }


def classify_session_bucket(
    path: Path,
    *,
    snap: Mapping[str, Any] | None = None,
    manifest: Mapping[str, Any] | None = None,
) -> tuple[str, str]:
    """Return (bucket, reason) where bucket in forward|test|synthetic|fixture|excluded."""
    excluded, reason = is_excluded_forward_path(path)
    prov = read_session_provenance(snap, manifest)
    raw = str(path).replace("\\", "/").lower()

    if prov["synthetic"] or prov["session_provenance"] == PROVENANCE_SYNTHETIC or "synthetic" in raw:
        return "synthetic", reason or "provenance:synthetic"
    if prov["fixture"] or prov["session_provenance"] == PROVENANCE_TEST_FIXTURE or "fixture" in raw:
        return "fixture", reason or "provenance:fixture"
    if (
        prov["test_mode"]
        or "_test_" in raw
        or "_w8_test_" in raw
        or "/tests/" in raw
    ):
        return "test", reason or "provenance:test_mode"

    if excluded:
        return "excluded", reason

    if (
        prov["session_provenance"] == LIVE_PAPER_PROVENANCE
        and not prov["synthetic"]
        and not prov["fixture"]
        and not prov["test_mode"]
        and prov["runtime_session"] is True
    ):
        return "forward", "LIVE_PAPER_RUNTIME"
    if not prov["session_provenance"]:
        return "excluded", "provenance_missing"
    return "excluded", f"provenance:{prov['session_provenance'] or 'unknown'}"


def live_paper_provenance_fields() -> dict[str, Any]:
    return {
        "session_provenance": LIVE_PAPER_PROVENANCE,
        "synthetic": False,
        "fixture": False,
        "test_mode": False,
        "runtime_session": True,
    }


def test_fixture_provenance_fields() -> dict[str, Any]:
    return {
        "session_provenance": PROVENANCE_TEST_FIXTURE,
        "synthetic": False,
        "fixture": True,
        "test_mode": True,
        "runtime_session": False,
    }


def synthetic_provenance_fields() -> dict[str, Any]:
    return {
        "session_provenance": PROVENANCE_SYNTHETIC,
        "synthetic": True,
        "fixture": False,
        "test_mode": False,
        "runtime_session": False,
    }


def trading_date_jst(now: Optional[datetime] = None) -> str:
    """Always runtime JST YYYYMMDD — never a fixed date constant for trading."""
    dt = now or datetime.now(JST)
    return dt.strftime("%Y%m%d")


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def redact_secrets(text: str) -> str:
    out = text or ""
    for pat in SECRET_PATTERNS:
        out = pat.sub(lambda m: m.group(0).split("=")[0].split(":")[0] + "=[REDACTED]", out)
    return out


def default_pythonpath() -> str:
    return f"{NATIVE_ROOT / 'src'};{REPO_ROOT}"


def resolve_session_artifact_paths(snapshot_path: Path) -> dict[str, Optional[Path]]:
    """Locate manifest/seal near a soak_session_snapshot.json."""
    snap = Path(snapshot_path)
    candidates_manifest: list[Path] = []
    candidates_seal: list[Path] = []
    # typical: <session>/live_order_safety/soak_session_snapshot.json
    safety = snap.parent
    session_root = safety.parent if safety.name == "live_order_safety" else safety
    for base in (safety, session_root, snap.parent):
        candidates_manifest.append(base / "session_manifest.json")
        candidates_seal.append(base / "session_seal.json")
    candidates_seal.append(session_root / "session_seal.json")
    man = next((p for p in candidates_manifest if p.is_file()), None)
    seal = next((p for p in candidates_seal if p.is_file()), None)
    return {"snapshot": snap if snap.is_file() else None, "manifest": man, "seal": seal, "session_root": session_root}


def qualify_session_artifacts(
    *,
    snapshot_path: Optional[Path] = None,
    snap: Optional[Mapping[str, Any]] = None,
    seal: Optional[Mapping[str, Any]] = None,
    manifest: Optional[Mapping[str, Any]] = None,
    manifest_exists: bool = False,
    seal_exists: bool = False,
    snapshot_exists: bool = False,
    submit_count: int = 0,
    cancel_count: int = 0,
    paper_exit_code: Optional[int] = None,
    require_forward_provenance: bool = False,
) -> dict[str, Any]:
    """Seal AND qualification. Forward count also requires provenance + path isolation.

    Phase687W11A: session_seal.json is SoT. Snapshot copy fields alone cannot pass.
    """
    failures: list[str] = []
    fields: dict[str, Any] = {}

    if paper_exit_code is not None and int(paper_exit_code) != 0:
        failures.append("paper_exit_code!=0")

    if not snapshot_exists:
        failures.append("snapshot_missing")
    if not manifest_exists:
        failures.append("manifest_missing")
    if not seal_exists:
        failures.append("seal_missing")

    snap_d = dict(snap or {})
    seal_d = dict(seal or {})
    man_d = dict(manifest or {})
    prov = read_session_provenance(snap_d, man_d)

    # --- SoT: seal fields (never overwrite seal from snapshot) ---
    from small_paper.w4s_seal_propagation import (
        compare_seal_snapshot,
        w4s_seal_success_ok,
    )

    status = str(seal_d.get("session_seal_status") or ("MISSING" if not seal_exists else "UNKNOWN"))
    entry = int(seal_d.get("entry_count") or 0)
    required = int(seal_d.get("required_count") or 0)
    missing = int(seal_d.get("required_artifact_missing_count") or 0)
    seal_verified = bool(
        seal_d.get("session_seal_verified")
        if seal_d.get("session_seal_verified") is not None
        else (status == "SEALED_VALID" and missing == 0 and entry > 0)
    )
    mutation = bool(
        seal_d.get("post_seal_mutation_detected")
        if "post_seal_mutation_detected" in seal_d
        else snap_d.get("post_seal_mutation_detected", False)
    )
    prop = str(
        snap_d.get("seal_propagation_status")
        or seal_d.get("seal_propagation_status")
        or ""
    )
    assertion_fail = int(snap_d.get("recovery_assertion_failure_count") or 0)
    unexpected = int(snap_d.get("recovery_unexpected_object_count") or 0)

    # Cross-check snapshot vs seal SoT
    cross = {"pass": False, "mismatch_count": 0, "mismatches": [], "reason": "seal_missing"}
    if seal_exists and seal_d:
        # Align session_id / trading_date / provenance before numeric compare
        for key_pair in (
            ("session_id", "session_id"),
            ("trading_date", "trading_date"),
            ("session_provenance", "session_provenance"),
        ):
            sk, lk = key_pair
            sv = str(snap_d.get(sk) or snap_d.get(lk) or "")
            lv = str(seal_d.get(sk) or seal_d.get(lk) or "")
            if sv and lv and sv != lv:
                failures.append(f"SNAPSHOT_SEAL_MISMATCH:{sk}")
        # Build snap view that must match seal SoT (use snap values as "actual")
        # compare_seal_snapshot expects snap to carry seal_* fields
        cmp_snap = dict(snap_d)
        # If snap omits fields, do not invent seal values into snap for a false pass —
        # missing snap fields will mismatch against seal SoT.
        cross = compare_seal_snapshot(
            cmp_snap,
            seal_d,
            verified=seal_verified,
            post_mutation=mutation,
        )
        if not cross.get("pass"):
            failures.append("SNAPSHOT_SEAL_MISMATCH")
            for m in cross.get("mismatches") or []:
                failures.append(f"SNAPSHOT_SEAL_MISMATCH:{m.get('field')}")
        if not w4s_seal_success_ok(cmp_snap, seal_d):
            if "SNAPSHOT_SEAL_MISMATCH" not in failures:
                # seal incomplete / disagree
                if status != "SEALED_VALID" or entry <= 0:
                    pass
                else:
                    failures.append("w4s_seal_success_ok=false")

    fields.update(
        {
            "session_seal_status": status,
            "session_seal_entry_count": entry,
            "session_seal_required_count": required,
            "required_artifact_missing_count": missing,
            "session_seal_verified": seal_verified,
            "post_seal_mutation_detected": mutation,
            "seal_propagation_status": prop,
            "recovery_assertion_failure_count": assertion_fail,
            "recovery_unexpected_object_count": unexpected,
            "actual_submit": int(submit_count),
            "actual_cancel": int(cancel_count),
            "snapshot_seal_crosscheck_pass": bool(cross.get("pass")),
            "snapshot_seal_mismatch_count": int(cross.get("mismatch_count") or 0),
            **prov,
        }
    )

    if status != "SEALED_VALID":
        failures.append(f"session_seal_status={status}")
    if not seal_verified:
        failures.append("session_seal_verified!=true")
    if entry <= 0:
        failures.append("session_seal_entry_count<=0")
    if required <= 0:
        failures.append("session_seal_required_count<=0")
    if entry != required:
        failures.append(f"entry_required_mismatch:{entry}/{required}")
    if missing != 0:
        failures.append(f"required_artifact_missing_count={missing}")
    if mutation:
        failures.append("post_seal_mutation_detected")
    if prop != SEAL_PROPAGATION_OK:
        failures.append(f"seal_propagation_status={prop or 'MISSING'}")
    if assertion_fail != 0:
        failures.append(f"recovery_assertion_failure_count={assertion_fail}")
    if unexpected != 0:
        failures.append(f"recovery_unexpected_object_count={unexpected}")
    if int(submit_count) != 0:
        failures.append(f"actual_submit={submit_count}")
    if int(cancel_count) != 0:
        failures.append(f"actual_cancel={cancel_count}")

    # Deduplicate failure tags while preserving order
    seen: set[str] = set()
    uniq_failures: list[str] = []
    for f in failures:
        if f not in seen:
            seen.add(f)
            uniq_failures.append(f)
    failures = uniq_failures

    seal_ok = len(failures) == 0
    path_obj = Path(snapshot_path) if snapshot_path else Path(".")
    # Phase687W30: invalid Paper sessions (register_failed etc.) never count as normal/forward days
    try:
        session_root = path_obj
        if path_obj.name == "soak_session_snapshot.json" and path_obj.parent.name == "live_order_safety":
            session_root = path_obj.parent.parent
        summary_path = session_root / "small_paper_summary.json"
        if summary_path.is_file():
            from small_paper.session_validity import classify_session_validity

            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            validity = classify_session_validity(summary)
            if not validity.get("include_in_strategy_metrics", True):
                failures = list(failures) + [f"INVALID_SESSION:{validity.get('session_validity')}"]
                seal_ok = False
    except Exception:
        pass
    bucket, bucket_reason = classify_session_bucket(path_obj, snap=snap_d, manifest=man_d)
    forward_failures = list(failures)
    if bucket != "forward":
        forward_failures.append(f"not_forward:{bucket_reason}")
    if require_forward_provenance and bucket != "forward":
        pass

    return {
        "qualified": seal_ok,
        "seal_qualified": seal_ok,
        "forward_qualified": seal_ok and bucket == "forward",
        "counted_as_normal_session": seal_ok,
        "counted_as_forward_session": seal_ok and bucket == "forward",
        "qualification_failure": (
            "SNAPSHOT_SEAL_MISMATCH"
            if any(str(x).startswith("SNAPSHOT_SEAL_MISMATCH") for x in failures)
            else (failures[0] if failures else "")
        ),
        "bucket": bucket,
        "bucket_reason": bucket_reason,
        "failures": failures,
        "forward_failures": forward_failures,
        "fields": fields,
        "snapshot_path": str(snapshot_path) if snapshot_path else "",
        "crosscheck": cross,
    }


def qualify_snapshot_path(
    snapshot_path: Path,
    *,
    submit_count: int = 0,
    cancel_count: int = 0,
    paper_exit_code: Optional[int] = None,
) -> dict[str, Any]:
    paths = resolve_session_artifact_paths(snapshot_path)
    snap_data: dict[str, Any] = {}
    seal_data: dict[str, Any] = {}
    man_data: dict[str, Any] = {}
    if paths["snapshot"] and paths["snapshot"].is_file():
        try:
            snap_data = json.loads(paths["snapshot"].read_text(encoding="utf-8"))
        except Exception:
            snap_data = {}
    if paths["seal"] and paths["seal"].is_file():
        try:
            seal_data = json.loads(paths["seal"].read_text(encoding="utf-8"))
        except Exception:
            seal_data = {}
    if paths["manifest"] and paths["manifest"].is_file():
        try:
            man_data = json.loads(paths["manifest"].read_text(encoding="utf-8"))
        except Exception:
            man_data = {}
    return qualify_session_artifacts(
        snapshot_path=paths["snapshot"],
        snap=snap_data,
        seal=seal_data,
        manifest=man_data,
        manifest_exists=bool(paths["manifest"] and paths["manifest"].is_file()),
        seal_exists=bool(paths["seal"] and paths["seal"].is_file()),
        snapshot_exists=bool(paths["snapshot"] and paths["snapshot"].is_file()),
        submit_count=submit_count,
        cancel_count=cancel_count,
        paper_exit_code=paper_exit_code,
    )


def write_qualified_session_fixture(
    root: Path,
    *,
    session_id: str = "W8-QUAL",
    provenance: Optional[Mapping[str, Any]] = None,
) -> Path:
    """Create a complete seal 14/14 session. Default provenance = TEST_FIXTURE (not Forward)."""
    safety = root / "live_order_safety"
    safety.mkdir(parents=True, exist_ok=True)
    prov = dict(provenance or test_fixture_provenance_fields())
    (safety / "session_manifest.json").write_text(
        json.dumps(
            {
                "session_id": session_id,
                "sealed": True,
                "git_commit": "fixture",
                "config_sha256": "abc",
                "live_trading_enabled": False,
                "order_enabled": False,
                "production_approval_status": "NOT_AUTHORIZED",
                **prov,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    seal = {
        "schema_version": "687W7A2.1",
        "session_id": session_id,
        "generated_at": _now_iso(),
        "entry_count": 14,
        "required_count": 14,
        "required_artifact_missing_count": 0,
        "missing_required": [],
        "session_seal_status": "SEALED_VALID",
        "session_seal_manifest_sha256": "a" * 64,
        "entries": [{"relative_path": f"f{i}.json", "exists": True, "sha256": "b" * 64} for i in range(14)],
        "finalize_locked": True,
        "seal_metadata_overlay_applied": True,
    }
    (root / "session_seal.json").write_text(json.dumps(seal, indent=2) + "\n", encoding="utf-8")
    snap = {
        "schema_version": "687W7A2.1",
        "phase": "687W4S",
        "session_id": session_id,
        "session_seal_status": "SEALED_VALID",
        "session_seal_entry_count": 14,
        "session_seal_required_count": 14,
        "required_artifact_missing_count": 0,
        "session_seal_verified": True,
        "session_seal_generated_at": seal["generated_at"],
        "session_seal_schema_version": "687W7A2.1",
        "session_seal_manifest_sha256": "a" * 64,
        "post_seal_mutation_detected": False,
        "seal_propagation_status": SEAL_PROPAGATION_OK,
        "recovery_assertion_failure_count": 0,
        "recovery_unexpected_object_count": 0,
        "recovery_expected_actual_match": True,
        "flags": {"live_trading_enabled": False, "order_enabled": False},
        "safety": {"actual_broker_submit_count": 0, "actual_broker_cancel_count": 0, "reservation_leak": 0},
        "readonly": {"account_status": "ONLINE_VALID"},
        "mapping": {"missing_intent_count": 0, "orphan_intent_count": 0, "duplicate_intent_created_count": 0},
        **prov,
    }
    snap_path = safety / "soak_session_snapshot.json"
    snap_path.write_text(json.dumps(snap, indent=2) + "\n", encoding="utf-8")
    return snap_path


def write_live_forward_session_fixture(root: Path, *, session_id: str = "LIVE-FWD") -> Path:
    """Seal-complete fixture stamped as LIVE_PAPER_RUNTIME (for isolation tests only)."""
    return write_qualified_session_fixture(root, session_id=session_id, provenance=live_paper_provenance_fields())


def write_synthetic_session_fixture(root: Path, *, session_id: str = "SYN") -> Path:
    return write_qualified_session_fixture(root, session_id=session_id, provenance=synthetic_provenance_fields())


@dataclass
class StepResult:
    name: str
    step_index: int
    command: list[str] | str
    started_at: str = ""
    ended_at: str = ""
    duration_sec: float = 0.0
    exit_code: int = 0
    result: str = "PASS"
    blocked_reason: str = ""
    stdout_tail: str = ""
    stderr_tail: str = ""
    info_only: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


CommandFn = Callable[[Sequence[str] | str, Mapping[str, str], Path], tuple[int, str, str]]


def _default_run(
    cmd: Sequence[str] | str,
    env: Mapping[str, str],
    cwd: Path,
) -> tuple[int, str, str]:
    run_kw: dict[str, Any] = {
        "cwd": str(cwd),
        "env": dict(env),
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if isinstance(cmd, str):
        proc = subprocess.run(cmd, shell=True, **run_kw)
    else:
        proc = subprocess.run(list(cmd), **run_kw)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def paper_bat_command(bat: Path) -> list[str]:
    """cmd /c call <bat> — list form, no %ERRORLEVEL% expansion."""
    return ["cmd.exe", "/d", "/c", "call", str(bat)]


def run_paper_bat_preserving_exitcode(
    bat: Path,
    env: Mapping[str, str],
    cwd: Path,
    *,
    feed_pause_newline: bool = True,
) -> tuple[int, str, str]:
    """Return the bat's actual ERRORLEVEL as subprocess.returncode.

    Must not embed the cmd percent-ERRORLEVEL percent token in a command string
    (parse-time expansion would replace it with 0 before the bat runs).
    """
    cmd = paper_bat_command(bat)
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=dict(env),
        input="\n" if feed_pause_newline else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return int(proc.returncode), proc.stdout or "", proc.stderr or ""


class PaperTradeCheckedRunner:
    """Fail-closed orchestrator. Paper bat is invoked at most once."""

    def __init__(
        self,
        *,
        repo_root: Path = REPO_ROOT,
        native_root: Path = NATIVE_ROOT,
        paper_bat: Path = DEFAULT_PAPER_BAT,
        config_path: Path = DEFAULT_CONFIG,
        python_exe: str = sys.executable,
        run_command: Optional[CommandFn] = None,
        skip_paper: bool = False,
        skip_w4s: bool = False,
        no_pause: bool = True,
        allow_paper_without_capture: bool = False,
        capture_synthetic: bool = False,
        skip_capture_wait: bool = False,
        demo_push_e2e: bool = False,
        comm_fault_e2e: bool = False,
        reuse_capture: bool = False,
        reuse_capture_pid: Optional[int] = None,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.native_root = Path(native_root)
        self.paper_bat = Path(paper_bat)
        self.config_path = Path(config_path)
        self.python_exe = python_exe
        self.run_command = run_command or _default_run
        self.skip_paper = skip_paper
        self.skip_w4s = skip_w4s
        self.no_pause = no_pause
        self.allow_paper_without_capture = allow_paper_without_capture
        self.reuse_capture = bool(reuse_capture)
        self.reuse_capture_pid = int(reuse_capture_pid) if reuse_capture_pid else None
        self.demo_push_e2e = bool(demo_push_e2e)
        self.comm_fault_e2e = bool(comm_fault_e2e)
        if self.demo_push_e2e:
            os.environ["TRADEBOT_DEMO_PUSH_E2E"] = "1"
            # Demo: no live Capture wait / no Kabu register write path
            skip_capture_wait = True
            skip_w4s = True
            self.skip_w4s = True
        if self.comm_fault_e2e:
            os.environ["TRADEBOT_COMM_FAULT_E2E"] = "1"
            skip_capture_wait = True
            skip_w4s = True
            self.skip_w4s = True
        # Test harness: skip_paper implies synthetic capture + operator-stop finalize
        self.capture_synthetic = bool(
            capture_synthetic or skip_paper or self.demo_push_e2e or self.comm_fault_e2e
        )
        self.skip_capture_wait = bool(
            skip_capture_wait or skip_paper or self.demo_push_e2e or self.comm_fault_e2e
        )
        self.steps: list[StepResult] = []
        self.paper_call_count = 0
        self.w4s_call_count = 0
        self.trading_date = trading_date_jst()
        self.blocked: Optional[dict[str, Any]] = None
        self.paper_exit_code: Optional[int] = None
        self.paper_elapsed_sec: Optional[float] = None
        self.post_session: dict[str, Any] = {}
        self.verdict = VERDICT_PRECHECK
        self.capture: dict[str, Any] = {
            "started": False,
            "pid": None,
            "status": None,
            "output": None,
            "event_count": 0,
            "override_used": False,
        }
        self.paper_blocked_capture_continues = False
        self.kabu_readonly_status = "UNKNOWN"
        self.universe_prebuild: dict[str, Any] = {}
        self._step_total = 17
        # Phase687W16 — owned Capture child only (never foreign/orphan-by-name)
        self._owned_capture = None  # OwnedCaptureProcess | None
        self._cleanup_done = False
        self._cleanup_result: Optional[dict[str, Any]] = None
        self._shutdown_reason = "normal_exit"
        self._signal_handlers_installed = False
        self.demo_push_summary: dict[str, Any] = {}
        self.comm_fault_summary: dict[str, Any] = {}

    def _env(self) -> dict[str, str]:
        try:
            from small_paper.env_loader import ensure_repo_dotenv

            ensure_repo_dotenv(repo_root=self.repo_root)
        except Exception:
            pass
        env = os.environ.copy()
        env["PYTHONPATH"] = default_pythonpath()
        env["PYTHONIOENCODING"] = "utf-8"
        return env

    def _py(self, *args: str) -> list[str]:
        return [self.python_exe, *args]

    def _record(
        self,
        name: str,
        step_index: int,
        command: list[str] | str,
        *,
        exit_code: int,
        started: float,
        stdout: str = "",
        stderr: str = "",
        result: Optional[str] = None,
        blocked_reason: str = "",
        info_only: bool = False,
    ) -> StepResult:
        ended = time.time()
        res = result or ("PASS" if exit_code == 0 else "FAIL")
        step = StepResult(
            name=name,
            step_index=step_index,
            command=command if isinstance(command, str) else list(command),
            started_at=datetime.fromtimestamp(started, tz=JST).isoformat(timespec="seconds"),
            ended_at=_now_iso(),
            duration_sec=round(ended - started, 3),
            exit_code=exit_code,
            result=res,
            blocked_reason=blocked_reason,
            stdout_tail=redact_secrets((stdout or "")[-2000:]),
            stderr_tail=redact_secrets((stderr or "")[-800:]),
            info_only=info_only,
        )
        self.steps.append(step)
        return step

    def _block(self, step: str, exit_code: int, reason: str, next_action: str) -> None:
        self.blocked = {
            "failed_step": step,
            "exit_code": exit_code,
            "reason": reason,
            "next_action": next_action,
        }
        self.verdict = VERDICT_PRECHECK if "safety" not in step.lower() else VERDICT_SAFETY
        if "safety" in step.lower() or "flag" in step.lower():
            self.verdict = VERDICT_SAFETY

    def _print_banner(self) -> None:
        print("========================================")
        print("TradeBot Paper Trade Checked Runner")
        print(f"Trading date: {self.trading_date}")
        print("Real orders: DISABLED")
        print("========================================")

    def _print_step(self, idx: int, total: int, label: str, status: str) -> None:
        dots = "." * max(1, 20 - len(label))
        print(f"[{idx}/{total}] {label}{dots}{status}")

    # ── Prechecks ───────────────────────────────────────────────────────────

    def step_disk_guard(self) -> bool:
        started = time.time()
        cmd = self._py("-c", "from small_paper.operational_recovery import disk_guard_report; import json; print(json.dumps(disk_guard_report(r'%s')))" % str(self.native_root))
        code, out, err = self.run_command(cmd, self._env(), self.native_root)
        reason = ""
        ok = code == 0
        state = "UNKNOWN"
        if ok:
            try:
                data = json.loads(out.strip().splitlines()[-1])
                state = str(data.get("disk_state") or "UNKNOWN")
                if state in ("CRITICAL", "HARD_STOP"):
                    ok = False
                    reason = f"disk_state={state}"
            except Exception as exc:
                ok = False
                reason = f"disk_guard_parse_error:{type(exc).__name__}"
        else:
            reason = "disk_guard_command_failed"
        step = self._record(
            "disk_guard",
            1,
            cmd,
            exit_code=0 if ok else (code or 1),
            started=started,
            stdout=out,
            stderr=err,
            result="PASS" if ok else "FAIL",
            blocked_reason=reason,
        )
        self._print_step(1, self._step_total, "Disk guard", step.result)
        if not ok:
            self._block("disk_guard", step.exit_code, reason or "disk critical", "Free disk space above critical threshold, then retry.")
        return ok

    def step_cache_prebuild(self) -> bool:
        started = time.time()
        cmd = self._py("-m", "small_paper.prebuild_vol_liq_startup_cache", "--date", self.trading_date)
        code, out, err = self.run_command(cmd, self._env(), self.native_root)
        ok = code == 0
        step = self._record(
            "cache_prebuild",
            2,
            cmd,
            exit_code=code,
            started=started,
            stdout=out,
            stderr=err,
            blocked_reason="" if ok else "cache prebuild failed",
        )
        self._print_step(2, 8, "Cache prebuild", step.result)
        if not ok:
            self._block("cache_prebuild", code, "vol_liq startup cache prebuild failed", "Fix cache inputs / network, then retry.")
        return ok

    def step_kabu_readonly(self) -> bool:
        started = time.time()
        cmd = self._py("-m", "small_paper.check_kabu_readonly_readiness")
        code, out, err = self.run_command(cmd, self._env(), self.native_root)
        ok = code == 0
        reason = "kabu readonly readiness failed"
        # Surface failure category without secrets
        try:
            data = json.loads(out) if out.strip().startswith("{") else {}
            cat = (
                data.get("failure_reason")
                or data.get("token_probe_status")
                or data.get("readiness_status")
                or data.get("status")
            )
            if cat:
                reason = str(cat)
        except Exception:
            # try last JSON object in output
            for line in reversed(out.splitlines()):
                if line.strip().startswith("{"):
                    try:
                        data = json.loads(line)
                        reason = str(
                            data.get("failure_reason")
                            or data.get("token_probe_status")
                            or data.get("readiness_status")
                            or reason
                        )
                        break
                    except Exception:
                        pass
        self.kabu_readonly_status = "ONLINE" if ok else "OFFLINE"
        if ok:
            self.kabu_readonly_status = "ONLINE"
        step = self._record(
            "kabu_readonly",
            3,
            cmd,
            exit_code=code,
            started=started,
            stdout=out,
            stderr=err,
            blocked_reason="" if ok else reason,
        )
        self._print_step(3, self._step_total, "Kabu readonly", step.result)
        if not ok:
            self._block(
                "kabu_readonly",
                code,
                reason,
                "Start Kabu Station manually if needed; do not auto-start. Fix auth/port, then retry.",
            )
        return ok

    def step_legacy_register_preclear(self) -> bool:
        """Unregister/all only before Ingress spawn. Forbidden after Ingress owns Kabu."""
        started = time.time()
        if self.capture_synthetic or self.demo_push_e2e or self.comm_fault_e2e or self.reuse_capture:
            self._record(
                "legacy_register_preclear",
                3,
                "SKIPPED",
                exit_code=0,
                started=started,
                result="PASS",
                info_only=True,
            )
            return True
        from small_paper.kabu_registration_authority import (
            forbid_post_ingress_unregister_all,
            ingress_owns_kabu_registration,
        )

        owned = ingress_owns_kabu_registration(self.native_root, self.trading_date)
        if owned.get("owned"):
            gate = forbid_post_ingress_unregister_all(
                self.native_root, self.trading_date, caller="checked_runner.preclear"
            )
            step = self._record(
                "legacy_register_preclear",
                3,
                "blocked_ingress_owns",
                exit_code=0,
                started=started,
                stdout=json.dumps(gate, ensure_ascii=False, default=str),
                result="PASS",
            )
            self._print_step(3, self._step_total, "Legacy unregister preclear", "SKIP(owned)")
            return True
        from api.kabu_register import clear_register_before_session

        out = clear_register_before_session(self.repo_root)
        ok = bool(out.get("ok") or out.get("skipped"))
        step = self._record(
            "legacy_register_preclear",
            3,
            "clear_register_before_session",
            exit_code=0 if ok else 1,
            started=started,
            stdout=json.dumps(
                {k: out.get(k) for k in ("ok", "cleared", "skipped", "reason")},
                ensure_ascii=False,
                default=str,
            ),
            result="PASS" if ok else "FAIL",
            blocked_reason="" if ok else str(out.get("error") or "preclear_failed"),
        )
        self._print_step(3, self._step_total, "Legacy unregister preclear", step.result)
        if not ok:
            self._block(
                "legacy_register_preclear",
                1,
                step.blocked_reason,
                "Legacy unregister/all must succeed before Ingress spawn.",
            )
        return ok

    def _verify_actual_kabu_exact50(self) -> bool:
        if self.capture_synthetic or self.demo_push_e2e or self.comm_fault_e2e:
            return True
        from small_paper.kabu_registration_authority import verify_exact50_membership

        started = time.time()
        last: dict = {}
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            last = verify_exact50_membership(
                self.native_root,
                self.trading_date,
                require_actual_kabu=True,
                allow_self_record_only=False,
            )
            if last.get("ok"):
                break
            time.sleep(0.5)
        ok = bool(last.get("ok"))
        self.capture["actual_kabu_membership"] = last
        if not ok:
            self._record(
                "actual_kabu_membership",
                7,
                "verify_exact50_membership",
                exit_code=1,
                started=started,
                stdout=json.dumps(last, ensure_ascii=False, default=str),
                result="FAIL",
                blocked_reason=str(last.get("reason") or "actual_kabu_mismatch"),
            )
            self._block(
                "actual_kabu_membership",
                1,
                str(last.get("reason") or "actual_kabu_mismatch"),
                "Ingress self-record 50 is not READY if actual Kabu RegistList is empty or drifted.",
            )
        return ok

    def step_preflight(self) -> bool:
        started = time.time()
        # Phase687W70: retention integrity before any live Paper spawn (no auto-delete).
        try:
            from small_paper.data_retention_guard import check_retention_integrity, format_retention_console

            ret = check_retention_integrity(root=self.native_root)
            print(format_retention_console(ret), flush=True)
            if not ret.ok:
                step = self._record(
                    "preflight",
                    4,
                    ["retention_guard"],
                    exit_code=2,
                    started=started,
                    stdout=format_retention_console(ret),
                    stderr=ret.code,
                    blocked_reason=ret.code,
                )
                self._print_step(4, 8, "Preflight", step.result)
                self._block(
                    "preflight",
                    2,
                    ret.code,
                    "Restore missing sessions from archive or rebuild retention baseline after approved recovery.",
                )
                return False
            # Phase687W71: external D sync — never block solely because D is missing.
            try:
                from small_paper.external_backup import check_external_sync_status, status_as_dict

                ext_status = check_external_sync_status(native=self.native_root, sync_if_connected=True)
                print(
                    f"[EXTERNAL_BACKUP] code={ext_status.code} d_connected={ext_status.d_connected} "
                    f"unsynced={len(ext_status.unsynced)} blocks_start={ext_status.blocks_start}",
                    flush=True,
                )
                _ = status_as_dict(ext_status)  # structured status available for future logging
            except Exception as ext_exc:
                print(f"[EXTERNAL_BACKUP] warn: sync check failed: {ext_exc}", flush=True)
        except Exception as exc:
            step = self._record(
                "preflight",
                4,
                ["retention_guard"],
                exit_code=2,
                started=started,
                stdout="",
                stderr=str(exc),
                blocked_reason="retention_guard_exception",
            )
            self._print_step(4, 8, "Preflight", step.result)
            self._block("preflight", 2, "retention_guard_exception", str(exc))
            return False

        script = self.native_root / "scripts" / "check_live_pipeline_preflight.py"
        cmd = self._py(str(script))
        code, out, err = self.run_command(cmd, self._env(), self.native_root)
        ok = code == 0
        step = self._record(
            "preflight",
            4,
            cmd,
            exit_code=code,
            started=started,
            stdout=out,
            stderr=err,
            blocked_reason="" if ok else "live pipeline preflight failed",
        )
        self._print_step(4, 8, "Preflight", step.result)
        if not ok:
            self._block("preflight", code, "live pipeline preflight failed", "Inspect preflight output and fix blockers.")
        return ok

    def step_smoke(self) -> bool:
        started = time.time()
        script = self.native_root / "scripts" / "run_production_startup_smoke_test.py"
        cmd = self._py(str(script))
        code, out, err = self.run_command(cmd, self._env(), self.native_root)
        ok = code == 0
        step = self._record(
            "smoke",
            5,
            cmd,
            exit_code=code,
            started=started,
            stdout=out,
            stderr=err,
            blocked_reason="" if ok else "production startup smoke failed",
        )
        self._print_step(5, 8, "Smoke", step.result)
        if not ok:
            self._block("smoke", code, "production startup smoke failed", "Inspect smoke report and fix blockers.")
        return ok

    def step_recovery(self) -> bool:
        started = time.time()
        cmd = self._py("-m", "small_paper.check_live_order_recovery_readiness")
        code, out, err = self.run_command(cmd, self._env(), self.native_root)
        ok = code == 0
        step = self._record(
            "recovery",
            6,
            cmd,
            exit_code=code,
            started=started,
            stdout=out,
            stderr=err,
            blocked_reason="" if ok else "recovery readiness failed",
        )
        self._print_step(6, 8, "Recovery", step.result)
        if not ok:
            self._block("recovery", code, "recovery readiness failed", "Inspect recovery blockers, then retry.")
        return ok

    def step_design_consistency(self) -> bool:
        started = time.time()
        script = self.native_root / "scripts" / "check_live_order_design_consistency.py"
        cmd = self._py(str(script))
        code, out, err = self.run_command(cmd, self._env(), self.native_root)
        ok = code == 0
        if ok:
            # also require JSON pass flag when present
            design_path = (
                self.native_root
                / "results"
                / "reports"
                / "phase687w3_e2e_readonly_reconciliation"
                / "phase687w3_design_consistency.json"
            )
            if design_path.is_file():
                try:
                    payload = json.loads(design_path.read_text(encoding="utf-8"))
                    if payload.get("pass") is False:
                        ok = False
                except Exception:
                    ok = False
        step = self._record(
            "design_consistency",
            0,
            cmd,
            exit_code=0 if ok else (code or 1),
            started=started,
            stdout=out,
            stderr=err,
            result="PASS" if ok else "FAIL",
            blocked_reason="" if ok else "design consistency mismatch",
        )
        if not ok:
            self._block(
                "design_consistency",
                step.exit_code,
                "design consistency mismatch",
                "Align design schema and code, then retry.",
            )
        return ok

    def step_safety_flags(self) -> bool:
        """Fail-closed safety: flags false, HARD_FAIL, no write adapter, NOT_AUTHORIZED.

        production enablement NOT_AUTHORIZED alone must NOT block.
        """
        started = time.time()
        issues: list[str] = []
        details: dict[str, Any] = {}

        # Config flags + SHA presence
        try:
            sys.path.insert(0, str(self.native_root / "src"))
            sys.path.insert(0, str(self.repo_root))
            from small_paper.config import load_pilot_config

            if not self.config_path.is_file():
                issues.append("config_missing")
            else:
                cfg = load_pilot_config(self.config_path)
                details["live_trading_enabled"] = bool(cfg.live_trading_enabled)
                details["order_enabled"] = bool(cfg.order_enabled)
                if cfg.live_trading_enabled:
                    issues.append("live_trading_enabled=true")
                if cfg.order_enabled:
                    issues.append("order_enabled=true")
                sha = hashlib.sha256(self.config_path.read_bytes()).hexdigest()
                details["config_sha256"] = sha
                if not sha or sha == "0" * 64:
                    issues.append("config_sha_invalid")
        except Exception as exc:
            issues.append(f"config_load_error:{type(exc).__name__}")

        # Submit HARD_FAIL
        submit_hard_fail = False
        try:
            from small_paper.live_order_safety_sm import KabuBrokerAdapter

            try:
                KabuBrokerAdapter().submit_entry_order({"symbol": "X", "quantity": 1})
            except RuntimeError as exc:
                submit_hard_fail = "HARD_FAIL" in str(exc)
            except Exception as exc:
                submit_hard_fail = "HARD_FAIL" in str(exc)
        except Exception as exc:
            issues.append(f"hard_fail_probe_error:{type(exc).__name__}")
        details["submit_hard_fail"] = submit_hard_fail
        if not submit_hard_fail:
            issues.append("submit_hard_fail=false")

        # Production enablement probe (info) + safety extraction
        enablement_exit = 0
        enablement_out = ""
        try:
            from small_paper.production_enablement_gate import probe_current_workspace

            probe = probe_current_workspace(native_root=self.native_root)
            details["write_adapter_present"] = bool(probe.get("write_adapter_present"))
            details["production_order_enablement"] = probe.get("production_order_enablement")
            details["approval_status"] = probe.get("approval_status")
            details["production_ready"] = probe.get("production_ready")
            if probe.get("write_adapter_present"):
                issues.append("write_adapter_present")
            # authorization must remain absent / NOT_AUTHORIZED
            appr = str(probe.get("approval_status") or "")
            if appr.upper() in ("APPROVED", "AUTHORIZED"):
                issues.append(f"production_authorization={appr}")
            if probe.get("production_ready") is True:
                issues.append("production_ready=true")
            # NOT_AUTHORIZED is expected — do not add as issue
            enablement_out = json.dumps(
                {
                    "approval_status": probe.get("approval_status"),
                    "production_ready": False,
                    "production_order_enablement": "NOT_AUTHORIZED / NOT_IMPLEMENTED",
                    "blocker_count": probe.get("blocker_count"),
                    "info": "NOT_AUTHORIZED is expected; does not block Paper by itself",
                },
                ensure_ascii=False,
            )
        except Exception as exc:
            issues.append(f"enablement_probe_error:{type(exc).__name__}")
            enablement_exit = 1

        ok = len(issues) == 0
        step = self._record(
            "safety_flags",
            7,
            "validate_safety_flags+production_enablement_info",
            exit_code=0 if ok else 1,
            started=started,
            stdout=enablement_out + "\n" + json.dumps(details, ensure_ascii=False),
            stderr="",
            result="PASS" if ok else "FAIL",
            blocked_reason="" if ok else ";".join(issues),
            info_only=False,
        )
        # Always show enablement as informational line
        print(f"      (info) production enablement: NOT_AUTHORIZED (expected; not a Paper blocker)")
        self._print_step(7, 8, "Safety flags", step.result)
        if not ok:
            self._block(
                "safety_flags",
                1,
                ";".join(issues),
                "Restore live_trading_enabled=false, order_enabled=false, HARD_FAIL, no write adapter.",
            )
            self.verdict = VERDICT_SAFETY
        return ok

    def step_production_enablement_info(self) -> None:
        """Explicit info-only CLI call — never blocks solely for NOT_AUTHORIZED."""
        started = time.time()
        cmd = self._py("-m", "small_paper.check_production_enablement_readiness")
        code, out, err = self.run_command(cmd, self._env(), self.native_root)
        # Info only: record but do not block on non-zero (expected when soak incomplete)
        self._record(
            "production_enablement_info",
            0,
            cmd,
            exit_code=code,
            started=started,
            stdout=out,
            stderr=err,
            result="INFO",
            info_only=True,
        )

    # ── Paper ───────────────────────────────────────────────────────────────

    def step_start_demo_push_e2e(self) -> int:
        """Phase687W20: demo PUSH certification via formal push-replay ingest (no live orders)."""
        started = time.time()
        os.environ["TRADEBOT_DEMO_PUSH_E2E"] = "1"
        cmd = self._py(
            "-m",
            "small_paper.demo_push_runtime_path",
            "--demo-push-e2e",
            "--repo-root",
            str(self.repo_root),
            "--native-root",
            str(self.native_root),
        )
        self.paper_call_count += 1
        code, out, err = self.run_command(cmd, self._env(), self.native_root)
        self.paper_exit_code = int(code)
        self.demo_push_summary = {"stdout_tail": (out or "")[-2000:], "stderr_tail": (err or "")[-1000:]}
        try:
            from small_paper.demo_push_runtime_path import report_dir

            summary_path = report_dir(self.native_root) / "final_summary.json"
            if summary_path.is_file():
                self.demo_push_summary.update(json.loads(summary_path.read_text(encoding="utf-8")))
        except Exception as exc:
            self.demo_push_summary["summary_load_error"] = f"{type(exc).__name__}:{exc}"
        self._record(
            "paper_trade",
            8,
            cmd,
            exit_code=code,
            started=started,
            stdout=out,
            stderr=err,
            result="PASS" if code == 0 else "FAIL",
            blocked_reason="" if code == 0 else "demo_push_e2e non-zero",
        )
        return code

    def step_start_comm_fault_e2e(self) -> int:
        """Phase687W21: Kabu communication fault injection & recovery certification."""
        started = time.time()
        os.environ["TRADEBOT_COMM_FAULT_E2E"] = "1"
        cmd = self._py(
            "-m",
            "small_paper.comm_fault_runtime_path",
            "--comm-fault-e2e",
            "--repo-root",
            str(self.repo_root),
            "--native-root",
            str(self.native_root),
        )
        self.paper_call_count += 1
        code, out, err = self.run_command(cmd, self._env(), self.native_root)
        self.paper_exit_code = int(code)
        self.comm_fault_summary = {
            "stdout_tail": (out or "")[-2000:],
            "stderr_tail": (err or "")[-1000:],
        }
        try:
            from small_paper.comm_fault_runtime_path import report_dir

            report_path = report_dir(self.native_root) / "phase687w21_report.json"
            if report_path.is_file():
                self.comm_fault_summary.update(json.loads(report_path.read_text(encoding="utf-8")))
        except Exception as exc:
            self.comm_fault_summary["summary_load_error"] = f"{type(exc).__name__}:{exc}"
        self._record(
            "paper_trade",
            8,
            cmd,
            exit_code=code,
            started=started,
            stdout=out,
            stderr=err,
            result="PASS" if code == 0 else "FAIL",
            blocked_reason="" if code == 0 else "comm_fault_e2e non-zero",
        )
        return code

    def step_start_paper(self) -> int:
        self._print_step(8, 8, "Starting Paper", "")
        if self.comm_fault_e2e:
            return self.step_start_comm_fault_e2e()
        if self.demo_push_e2e:
            return self.step_start_demo_push_e2e()
        if self.skip_paper:
            started = time.time()
            self._record(
                "paper_trade",
                8,
                "SKIPPED",
                exit_code=0,
                started=started,
                result="SKIPPED",
            )
            self.paper_exit_code = 0
            return 0
        if not self.paper_bat.is_file():
            self._block("paper_trade", 1, f"missing {self.paper_bat}", "Restore run_paper_trade.bat")
            self.paper_exit_code = 1
            return 1
        started = time.time()
        bat = Path(self.paper_bat)
        cmd = paper_bat_command(bat)
        self.paper_call_count += 1
        if self.run_command is _default_run:
            code, out, err = run_paper_bat_preserving_exitcode(bat, self._env(), self.repo_root)
        else:
            code, out, err = self.run_command(cmd, self._env(), self.repo_root)
        elapsed = time.time() - started
        self.paper_exit_code = int(code)
        self.paper_elapsed_sec = float(elapsed)
        from small_paper.kabu_registration_authority import (
            PREMATURE_PRE_WARMUP_EXIT,
            classify_pre_warmup_process_exit,
        )

        classified = classify_pre_warmup_process_exit(int(code))
        premature = bool(
            not self.demo_push_e2e
            and not self.comm_fault_e2e
            and not self.skip_paper
            and classified.get("fail")
            and str(classified.get("reason") or "") == PREMATURE_PRE_WARMUP_EXIT
        )
        if premature:
            self.paper_exit_code = int(classified.get("exit_code") or 4)
            code = int(self.paper_exit_code)
            err = (err or "") + (
                f"\n{PREMATURE_PRE_WARMUP_EXIT}: paper bat returned 0 before warmup/session start"
            )
        self._record(
            "paper_trade",
            8,
            cmd,
            exit_code=code,
            started=started,
            stdout=out,
            stderr=err,
            result="FAIL" if premature or int(code) != 0 else "PASS",
            blocked_reason=(
                PREMATURE_PRE_WARMUP_EXIT
                if premature
                else ("" if int(code) == 0 else "paper bat non-zero exit")
            ),
        )
        return int(code)

    # ── Post ────────────────────────────────────────────────────────────────

    def step_post_session(self, *, paper_ok: bool) -> dict[str, Any]:
        """Post-check with Forward/test/synthetic isolation. sessions_collected = forward only."""
        started = time.time()
        w4s: dict[str, Any] = {}
        w4s_skipped = bool(self.skip_w4s)
        if not self.skip_w4s:
            cmd = self._py("-m", "research.phase687w4s_runtime_readonly_forward_soak")
            self.w4s_call_count += 1
            code, out, err = self.run_command(cmd, self._env(), self.native_root)
            try:
                for line in reversed(out.splitlines()):
                    line = line.strip()
                    if line.startswith("{") and "verdict" in line:
                        w4s = json.loads(line)
                        break
                if not w4s and out.strip().startswith("{"):
                    w4s = json.loads(out)
            except Exception:
                w4s = {"verdict": "PARSE_ERROR", "raw_tail": redact_secrets(out[-500:])}
            w4s["exit_code"] = code
            self._record(
                "w4s_forward_soak",
                9,
                cmd,
                exit_code=code,
                started=started,
                stdout=out,
                stderr=err,
                result="PASS" if code == 0 else "FAIL",
            )
        else:
            self._record("w4s_forward_soak", 9, "SKIPPED", exit_code=0, started=started, result="SKIPPED")

        results_root = self.native_root / "results"
        snaps = (
            sorted(results_root.rglob("soak_session_snapshot.json"), key=lambda p: p.stat().st_mtime)
            if results_root.is_dir()
            else []
        )

        from small_paper.kabu_order_request_builder import actual_broker_submit_count

        submit_n = int(actual_broker_submit_count() or 0)
        agg = w4s.get("aggregate") or {}
        cancel_n = int(agg.get("cancel_total") or 0) if isinstance(agg, dict) else 0

        qualifications: list[dict[str, Any]] = []
        sessions_list: list[dict[str, Any]] = []
        excluded_sessions: list[dict[str, Any]] = []
        forward_q = 0
        test_q = 0
        synthetic_q = 0
        fixture_q = 0
        excluded_n = 0

        for pth in snaps:
            q = qualify_snapshot_path(
                pth,
                submit_count=submit_n,
                cancel_count=cancel_n,
                paper_exit_code=self.paper_exit_code,
            )
            qualifications.append(q)
            bucket = str(q.get("bucket") or "excluded")
            seal_ok = bool(q.get("seal_qualified") or q.get("qualified"))
            rel = str(pth)
            try:
                rel = str(pth.relative_to(self.native_root)).replace("\\", "/")
            except Exception:
                pass
            entry = {
                "path": rel,
                "session_id": None,
                "bucket": bucket,
                "bucket_reason": q.get("bucket_reason"),
                "seal_qualified": seal_ok,
                "forward_qualified": bool(q.get("forward_qualified")),
                "session_seal_entry_count": (q.get("fields") or {}).get("session_seal_entry_count"),
                "session_seal_status": (q.get("fields") or {}).get("session_seal_status"),
                "failures": q.get("failures"),
            }
            try:
                entry["session_id"] = json.loads(pth.read_text(encoding="utf-8")).get("session_id")
            except Exception:
                entry["session_id"] = None
            sessions_list.append(entry)

            if seal_ok and bucket == "forward":
                forward_q += 1
            elif seal_ok and bucket == "test":
                test_q += 1
            elif seal_ok and bucket == "synthetic":
                synthetic_q += 1
            elif seal_ok and bucket == "fixture":
                fixture_q += 1
                test_q += 1  # fixture counts toward test_qualified
            else:
                excluded_n += 1
                excluded_sessions.append(
                    {
                        "path": rel,
                        "reason": q.get("bucket_reason") or ";".join(q.get("forward_failures") or q.get("failures") or []),
                    }
                )

        # W4S not run / unknown → do not advance Forward counts
        w4s_verdict_raw = str(w4s.get("verdict") or w4s.get("status") or "")
        if w4s_skipped or self.w4s_call_count == 0:
            w4s_verdict = "NOT_RUN"
            sessions_collected = 0
            readonly_success = 0
            forward_for_display = 0
        elif w4s_verdict_raw in ("", "UNKNOWN", "PARSE_ERROR"):
            w4s_verdict = w4s_verdict_raw or "UNKNOWN"
            sessions_collected = 0
            readonly_success = 0
            forward_for_display = 0
        else:
            w4s_verdict = w4s_verdict_raw
            sessions_collected = forward_q
            readonly_success = int(agg.get("readonly_success_sessions") or 0) if isinstance(agg, dict) else 0
            # readonly_success must not exceed forward qualified
            if readonly_success > sessions_collected:
                readonly_success = sessions_collected
            forward_for_display = forward_q

        # Seal display from newest forward-qualified, else newest seal-qualified
        seal_status = "MISSING"
        seal_entry = 0
        seal_required = 0
        seal_verified = False
        prop_status = ""
        missing_n = 0
        mutation = False
        pick = next((q for q in reversed(qualifications) if q.get("forward_qualified")), None)
        if pick is None:
            pick = next((q for q in reversed(qualifications) if q.get("seal_qualified")), None)
        if pick is not None:
            f = pick["fields"]
            seal_status = str(f.get("session_seal_status"))
            seal_entry = int(f.get("session_seal_entry_count") or 0)
            seal_required = int(f.get("session_seal_required_count") or 0)
            seal_verified = bool(f.get("session_seal_verified"))
            prop_status = str(f.get("seal_propagation_status") or "")
            missing_n = int(f.get("required_artifact_missing_count") or 0)
            mutation = bool(f.get("post_seal_mutation_detected"))

        paper_exit = int(self.paper_exit_code if self.paper_exit_code is not None else -1)
        counted_forward = (
            (not w4s_skipped)
            and self.w4s_call_count == 1
            and w4s_verdict not in ("NOT_RUN", "UNKNOWN", "PARSE_ERROR", "")
            and paper_exit == 0
            and forward_for_display >= 1
            and submit_n == 0
            and cancel_n == 0
        )

        if w4s_skipped:
            # Test mode may skip W4S; production must not
            result = "W4S_NOT_RUN"
        elif paper_exit != 0:
            result = "ABNORMAL_PAPER"
        elif counted_forward:
            result = "OK"
        else:
            result = "SESSION_ARTIFACT_INCOMPLETE"

        post = {
            "paper_exit_code": self.paper_exit_code,
            "paper_normal_exit": bool(paper_ok),
            "counted_as_normal_session": counted_forward,
            "counted_as_forward_session": counted_forward,
            "w4s_verdict": w4s_verdict,
            "w4s_reported_session_count": int(agg.get("session_count") or 0) if isinstance(agg, dict) else 0,
            "sessions_collected": sessions_collected,
            "forward_qualified_session_count": forward_q if not w4s_skipped else 0,
            "test_qualified_session_count": test_q,
            "synthetic_qualified_session_count": synthetic_q,
            "fixture_qualified_session_count": fixture_q,
            "excluded_session_count": excluded_n,
            "excluded_sessions": excluded_sessions[:50],
            "exclusion_reasons": sorted({e.get("reason") or "" for e in excluded_sessions if e.get("reason")}),
            "readonly_success_sessions": readonly_success,
            "seal_status": seal_status,
            "seal_entry_count": seal_entry,
            "seal_required_count": seal_required,
            "session_seal_verified": seal_verified,
            "required_artifact_missing_count": missing_n,
            "post_seal_mutation_detected": mutation,
            "seal_propagation_status": prop_status,
            "mapping_loss": int(agg.get("mapping_loss_total") or 0) if isinstance(agg, dict) else 0,
            "duplicate_intent": int(agg.get("duplicate_intent_total") or 0) if isinstance(agg, dict) else 0,
            "reservation_leak": int(agg.get("reservation_leak_total") or 0) if isinstance(agg, dict) else 0,
            "actual_submit": submit_n,
            "actual_cancel": cancel_n,
            "latency_p95": agg.get("accept_to_would_submit_p95_across") if isinstance(agg, dict) else None,
            "am_pm_sessions": sessions_list,
            "w4s_call_count": self.w4s_call_count,
            "paper_call_count": self.paper_call_count,
            "result": result,
        }
        self.post_session = post
        return post

    def _print_blocked(self) -> None:
        b = self.blocked or {}
        if self.paper_blocked_capture_continues and self.capture.get("started"):
            print()
            print("[PAPER BLOCKED - CAPTURE CONTINUES]")
            print(f"failed_step: {b.get('failed_step')}")
            print(f"capture_status: {self.capture.get('status')}")
            print(f"capture_pid: {self.capture.get('pid')}")
            print(f"captured_event_count: {self.capture.get('event_count')}")
            print(f"capture_output_path: {self.capture.get('output')}")
            print(f"scheduled_end: {self.trading_date} 15:35 JST")
            return
        print()
        print("[BLOCKED]")
        print(f"failed_step: {b.get('failed_step')}")
        print(f"exit_code: {b.get('exit_code')}")
        print(f"reason: {b.get('reason')}")
        print(f"next_action: {b.get('next_action')}")

    def _print_capture_banner(self) -> None:
        c = self.capture
        print()
        print("[CAPTURE]")
        print(f"status: {c.get('status') or 'UNKNOWN'}")
        print(f"pid: {c.get('pid')}")
        print(f"symbols: {c.get('symbols_label', '?/?')}")
        print(f"topology: {c.get('topology', 'SINGLE_INGRESS_LOCAL_FANOUT')}")
        print(f"output: {c.get('output')}")
        print("Paper dependency: NONE")

    def _print_capture_finish(self) -> None:
        c = self.capture
        print()
        print("[MARKET CAPTURE]")
        print(f"status: {c.get('final_status') or c.get('status')}")
        print(f"events: {c.get('event_count')}")
        print(f"symbols: {c.get('symbols_seen')}")
        print(f"disconnects: {c.get('disconnect_count')}")
        print(f"drops: {c.get('dropped_event_count')}")
        print(f"seal: {c.get('seal_pass')}")
        print(f"capture_complete: {c.get('capture_complete')}")

    def step_universe_prebuild(self) -> bool:
        """Phase687W15B: ensure same-day AM universe SoT exists (no previous-day fallback)."""
        started = time.time()
        from small_paper.universe_prebuild import run_universe_prebuild, write_prebuild_artifact

        result = run_universe_prebuild(
            repo_root=self.repo_root,
            native_root=self.native_root,
            trading_date=self.trading_date,
            allow_synthetic=bool(self.capture_synthetic),
        )
        self.universe_prebuild = dict(result)
        try:
            write_prebuild_artifact(self.native_root, self.trading_date, result)
        except OSError:
            pass
        ok = bool(result.get("ok"))
        reason = str(result.get("error_reason") or result.get("verdict") or "")
        if not ok:
            if reason == "universe_validation_failed":
                next_action = (
                    f"expected_symbols=50 actual_symbols={result.get('symbol_count')}; "
                    "Check generator output and validation_result."
                )
            elif reason == "non_trading_day":
                next_action = "Trading date is weekend; formal Paper start is blocked."
            else:
                next_action = "Check feature source and generator log."
            step = self._record(
                "universe_prebuild",
                4,
                "run_universe_prebuild",
                exit_code=1,
                started=started,
                stdout=json.dumps(
                    {
                        k: result.get(k)
                        for k in (
                            "verdict",
                            "existing_or_generated",
                            "output_path",
                            "symbol_count",
                            "core_count",
                            "dynamic_count",
                            "error_reason",
                            "generator_exit_code",
                        )
                    },
                    ensure_ascii=False,
                ),
                result="FAIL",
                blocked_reason=reason or "universe_generation_failed",
            )
            self._print_step(4, self._step_total, "Universe prebuild", step.result)
            self._block("universe_prebuild", 1, step.blocked_reason, next_action)
            return False

        step = self._record(
            "universe_prebuild",
            4,
            "run_universe_prebuild",
            exit_code=0,
            started=started,
            stdout=json.dumps(
                {
                    k: result.get(k)
                    for k in (
                        "verdict",
                        "existing_or_generated",
                        "output_path",
                        "symbol_count",
                        "core_count",
                        "dynamic_count",
                        "duration_sec",
                    )
                },
                ensure_ascii=False,
            ),
            result="PASS",
        )
        self._print_step(4, self._step_total, "Universe prebuild", step.result)
        self.capture["universe_prebuild"] = result
        return True

    def step_universe_resolve(self) -> bool:
        started = time.time()
        from small_paper.day_fixed_am_registration import load_am_canonical_50

        resolved = load_am_canonical_50(self.native_root, self.trading_date)
        ok = bool(resolved.get("ok")) and int(resolved.get("symbol_count") or 0) == 50
        # Weekend / missing CSV: synthetic tests inject symbols via coordination
        if not ok and self.capture_synthetic:
            ok = True
            resolved = {
                **resolved,
                "ok": True,
                "symbols": [str(7200 + i) for i in range(50)],
                "symbol_count": 50,
                "reason": "synthetic_universe",
                "universe_path": resolved.get("universe_path"),
                "universe_sha256": "",
            }
        step = self._record(
            "universe_resolve",
            5,
            "load_am_canonical_50",
            exit_code=0 if ok else 1,
            started=started,
            stdout=json.dumps(
                {k: resolved.get(k) for k in ("ok", "symbol_count", "universe_path", "reason")},
                ensure_ascii=False,
            ),
            result="PASS" if ok else "FAIL",
            blocked_reason="" if ok else str(resolved.get("reason") or "universe_resolve_failed"),
        )
        self._print_step(5, self._step_total, "Universe resolve", step.result)
        self.capture["universe"] = resolved
        if not ok:
            self._block("universe_resolve", 1, step.blocked_reason, "Provide today's AM universe CSV SoT (exactly 50 symbols).")
        return ok

    def step_registration_coordination(self) -> bool:
        started = time.time()
        from small_paper.day_fixed_am_registration import (
            bind_same_day_am_desired_universe,
            canonical_membership_sha,
        )
        from small_paper.market_capture_registration import coordinate_registration

        uni = self.capture.get("universe") or {}
        symbols = list(uni.get("symbols") or [])
        bind = bind_same_day_am_desired_universe(
            self.native_root,
            self.trading_date,
            symbols=symbols,
            source_path=str(uni.get("universe_path") or ""),
            source_sha256=str(uni.get("universe_sha256") or uni.get("universe_manifest_sha256") or ""),
        )
        if not bind.get("ok"):
            step = self._record(
                "registration_coordination",
                6,
                "bind_same_day_am_desired_universe",
                exit_code=1,
                started=started,
                result="FAIL",
                blocked_reason=str(bind.get("reason") or "desired_universe_bind_failed"),
            )
            self._print_step(6, self._step_total, "Registration plan", step.result)
            self._block(
                "registration_coordination",
                1,
                step.blocked_reason,
                "Bind same-day AM 50 to Ingress desired universe before spawn.",
            )
            return False
        coord = coordinate_registration(
            self.native_root,
            self.trading_date,
            expected_symbols=symbols,
            apply_register=False,  # live PUT remains Ingress-owned after same-day bind
            universe_path=uni.get("universe_path"),
            universe_sha256=str(uni.get("universe_sha256") or uni.get("universe_manifest_sha256") or ""),
            test_mode=self.capture_synthetic,
            extra={
                "source_trading_date": self.trading_date,
                "source_path": str(uni.get("universe_path") or ""),
                "source_sha256": str(uni.get("universe_sha256") or uni.get("universe_manifest_sha256") or ""),
                "desired_count": len(symbols),
                "canonical_membership_sha": canonical_membership_sha(symbols),
            },
        )
        ok = bool(coord.get("ok")) and int(coord.get("expected_count") or 0) <= 50
        step = self._record(
            "registration_coordination",
            6,
            "coordinate_registration",
            exit_code=0 if ok else 1,
            started=started,
            stdout=json.dumps(
                {
                    "status": coord.get("status"),
                    "expected_count": coord.get("expected_count"),
                    "registration_match": coord.get("registration_match"),
                    "unregister_all_used": coord.get("unregister_all_used"),
                },
                ensure_ascii=False,
            ),
            result="PASS" if ok else "FAIL",
            blocked_reason="" if ok else str(coord.get("reason") or "registration_coordination_failed"),
        )
        self._print_step(6, self._step_total, "Registration plan", step.result)
        # Explicit: coordination only — runtime PUT is Paper-owned (Phase687W31)
        if ok:
            print(
                f"  [{6}/{self._step_total}] REGISTRATION_COORDINATION_READY "
                f"(Runtime register......PENDING)",
                flush=True,
            )
        self.capture["registration"] = {
            **coord,
            "coordination_only": True,
            "runtime_register": "PENDING",
            "desired_bound": True,
            "display_status": "REGISTRATION_COORDINATION_READY" if ok else "FAIL",
        }
        self.capture["symbols_label"] = f"{coord.get('expected_count', 0)}/50"
        if not ok:
            self._block("registration_coordination", 1, step.blocked_reason, "Fix registration coordination (≤50, lock, no race).")
        return ok

    def step_start_capture(self) -> bool:
        started = time.time()
        from small_paper.market_capture_sidecar import (
            capture_day_dir,
            spawn_sidecar_process,
            wait_capture_online,
        )
        from small_paper.capture_child_cleanup import prepare_day_dir_operator_stop_for_spawn
        from small_paper.market_ingress_protocol import market_ingress_v2_enabled
        from datetime import datetime
        from zoneinfo import ZoneInfo

        # Phase687W18: archive stale operator_stop.flag before owned spawn
        day_dir = capture_day_dir(self.native_root, self.trading_date)
        spawn_started_at = datetime.now(ZoneInfo("Asia/Tokyo"))
        stop_prep = prepare_day_dir_operator_stop_for_spawn(
            day_dir,
            spawn_started_at=spawn_started_at,
        )
        self.capture["operator_stop_prep"] = stop_prep

        if self.reuse_capture:
            if not market_ingress_v2_enabled():
                self.verdict = VERDICT_CAPTURE_REQUIRED
                self._block(
                    "capture_sidecar_start",
                    1,
                    "reuse_capture_requires_MARKET_INGRESS_V2",
                    "Enable MARKET_INGRESS_V2=1 for --reuse-capture.",
                )
                self._record(
                    "capture_sidecar_start",
                    7,
                    "reuse_existing_ingress",
                    exit_code=1,
                    started=started,
                    result="FAIL",
                    blocked_reason="reuse_capture_requires_MARKET_INGRESS_V2",
                )
                self._print_step(7, self._step_total, "Capture reuse", "FAIL")
                return False
            return self._step_reuse_capture(started=started, day_dir=day_dir)

        if market_ingress_v2_enabled():
            from small_paper.market_ingress_spawn import spawn_ingress_process, wait_ingress_online

            # Cutover: Independent Ingress owns WS + Raw; legacy fanout sidecar OFF.
            spawn = spawn_ingress_process(
                native_root=self.native_root,
                trading_date=self.trading_date,
                python_exe=self.python_exe,
                synthetic=bool(self.capture_synthetic),
            )
            if spawn.get("rejected"):
                self.verdict = "V1R_PBV2_DUPLICATE_RUNTIME_CONTAMINATION"
                self._block(
                    "capture_sidecar_start",
                    1,
                    "V1R_PBV2_DUPLICATE_RUNTIME_CONTAMINATION",
                    "Live Market Ingress already running for this trading-date; refuse second spawn. "
                    "Stop orphan ingress or use --reuse-capture.",
                )
                self._record(
                    "capture_sidecar_start",
                    7,
                    "ingress_spawn_rejected_duplicate",
                    exit_code=1,
                    started=started,
                    result="FAIL",
                    blocked_reason="V1R_PBV2_DUPLICATE_RUNTIME_CONTAMINATION",
                )
                self._print_step(7, self._step_total, "Capture Ingress", "FAIL (duplicate)")
                return False
            if int(spawn.get("pid") or 0) > 0:
                from small_paper.capture_child_cleanup import record_owned_from_spawn

                self._owned_capture = record_owned_from_spawn(spawn, native_root=self.native_root)
            wait = wait_ingress_online(
                self.native_root,
                self.trading_date,
                timeout_sec=20.0 if self.capture_synthetic else 45.0,
                require_registered_count=50 if int((self.capture.get("registration") or {}).get("expected_count") or 0) == 50 else 0,
            )
            ok = bool(wait.get("ok"))
            self.capture.update(
                {
                    "started": ok,
                    "pid": wait.get("pid") or spawn.get("pid"),
                    "status": wait.get("status") or ("INGRESS_ONLINE" if ok else "INGRESS_START_FAILED"),
                    "output": str(day_dir),
                    "topology": "INDEPENDENT_MARKET_INGRESS",
                    "websocket_owner": "MARKET_INGRESS_SERVICE",
                    "capture_source": "INGRESS_RAW_WRITER",
                    "legacy_paper_websocket": "DISABLED",
                    "legacy_capture_fanout": "DISABLED",
                    "spawn": spawn,
                    "wait": wait,
                    "reused": False,
                }
            )
        else:
            spawn = spawn_sidecar_process(
                native_root=self.native_root,
                trading_date=self.trading_date,
                synthetic=self.capture_synthetic,
                synthetic_events=80 if self.capture_synthetic else 0,
                python_exe=self.python_exe,
            )
            # Track owned PID immediately (even if wait fails) so finally can stop our child.
            if int(spawn.get("pid") or 0) > 0:
                from small_paper.capture_child_cleanup import record_owned_from_spawn

                self._owned_capture = record_owned_from_spawn(spawn, native_root=self.native_root)
            wait = wait_capture_online(
                self.native_root,
                self.trading_date,
                timeout_sec=20.0 if self.capture_synthetic else 45.0,
            )
            ok = bool(wait.get("ok"))
            self.capture.update(
                {
                    "started": ok,
                    "pid": wait.get("pid") or spawn.get("pid"),
                    "status": wait.get("status") or ("CAPTURE_ONLINE" if ok else "CAPTURE_START_FAILED"),
                    "output": wait.get("output") or spawn.get("output"),
                    "topology": "SINGLE_INGRESS_LOCAL_FANOUT",
                    "spawn": spawn,
                    "wait": wait,
                }
            )
        step = self._record(
            "capture_sidecar_start",
            7,
            spawn.get("cmd") or "spawn_sidecar",
            exit_code=0 if ok else 1,
            started=started,
            stdout=json.dumps({"spawn": spawn, "wait": wait}, ensure_ascii=False),
            result="PASS" if ok else "FAIL",
            blocked_reason="" if ok else str(wait.get("reason") or "capture_sidecar_start_failed"),
        )
        self._print_step(7, self._step_total, "Capture sidecar", step.result)
        if ok:
            self._print_capture_banner()
            if not self._verify_actual_kabu_exact50():
                return False
        else:
            if self.allow_paper_without_capture:
                self.capture["override_used"] = True
                print()
                print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
                print("WARNING: --allow-paper-without-capture")
                print("Market capture UNAVAILABLE — Paper may continue")
                print("Real orders remain DISABLED")
                print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
                # log unavailable
                log_dir = self.native_root / "results" / "reports" / "paper_trade_checked_runner"
                log_dir.mkdir(parents=True, exist_ok=True)
                (log_dir / f"capture_unavailable_{self.trading_date}.json").write_text(
                    json.dumps({"at": _now_iso(), "reason": wait, "override": True}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                return True
            self.verdict = VERDICT_CAPTURE_REQUIRED
            self._block(
                "capture_sidecar_start",
                1,
                "CAPTURE_REQUIRED_NOT_READY",
                "Start capture sidecar successfully, or pass --allow-paper-without-capture (not default).",
            )
        return ok or self.allow_paper_without_capture

    def _step_reuse_capture(self, *, started: float, day_dir: Path) -> bool:
        """Attach to an already-running Independent Market Ingress (explicit --reuse-capture).

        Fail-closed. Never calls spawn_ingress_process / Popen. Does not claim ownership
        (so runner cleanup will not stop the reused Capture).
        """
        from small_paper.market_ingress_reuse import attach_existing_ingress

        reg = self.capture.get("registration") or {}
        uni = self.capture.get("universe") or {}
        expected = int(
            reg.get("expected_count")
            or uni.get("symbol_count")
            or (self.universe_prebuild or {}).get("symbol_count")
            or 0
        )
        attach = attach_existing_ingress(
            native_root=self.native_root,
            trading_date=self.trading_date,
            expected_symbol_count=expected,
            expected_pid=self.reuse_capture_pid,
        )
        ok = bool(attach.get("ok"))
        # Critical: do NOT set self._owned_capture — reused Capture must survive runner exit.
        self._owned_capture = None
        self.capture.update(
            {
                "started": ok,
                "pid": attach.get("pid"),
                "status": attach.get("status") or ("REUSE_OK" if ok else "REUSE_FAIL"),
                "output": attach.get("output") or str(day_dir),
                "topology": "INDEPENDENT_MARKET_INGRESS",
                "websocket_owner": "MARKET_INGRESS_SERVICE",
                "capture_source": "INGRESS_RAW_WRITER",
                "legacy_paper_websocket": "DISABLED",
                "legacy_capture_fanout": "DISABLED",
                "spawn": {
                    "mode": "reuse_capture",
                    "spawned": False,
                    "pid": attach.get("pid"),
                    "cmd": None,
                },
                "wait": attach,
                "reused": True,
                "owned_by_runner": False,
                "continuing_until": True,
                "runtime_register_pending": bool(attach.get("runtime_register_pending")),
            }
        )
        step = self._record(
            "capture_sidecar_start",
            7,
            "reuse_existing_ingress",
            exit_code=0 if ok else 1,
            started=started,
            stdout=json.dumps({"reuse": attach}, ensure_ascii=False, default=str),
            result="PASS" if ok else "FAIL",
            blocked_reason="" if ok else str(attach.get("reason") or "capture_reuse_failed"),
        )
        self._print_step(7, self._step_total, "Capture reuse", step.result)
        if ok:
            print(
                f"  [{7}/{self._step_total}] REUSE_CAPTURE pid={attach.get('pid')} "
                f"state={attach.get('status')} owned_by_runner=false",
                flush=True,
            )
            self._print_capture_banner()
            if not self._verify_actual_kabu_exact50():
                return False
            return True
        self.verdict = VERDICT_CAPTURE_REQUIRED
        self._block(
            "capture_sidecar_start",
            1,
            str(attach.get("reason") or "CAPTURE_REUSE_FAILED"),
            "Fix Capture reuse preconditions, or start without --reuse-capture.",
        )
        return False

    def _refresh_capture_stats(self) -> None:
        out = self.capture.get("output")
        if not out:
            return
        status_path = Path(out) / "capture_status.json"
        summary_path = Path(out) / "capture_summary.json"
        seal_path = Path(out) / "capture_seal.json"
        if status_path.is_file():
            try:
                st = json.loads(status_path.read_text(encoding="utf-8"))
                self.capture["status"] = st.get("capture_status")
                self.capture["event_count"] = st.get("event_count")
                self.capture["pid"] = st.get("pid")
            except Exception:
                pass
        if summary_path.is_file():
            try:
                sm = json.loads(summary_path.read_text(encoding="utf-8"))
                self.capture["final_status"] = sm.get("capture_status")
                self.capture["event_count"] = sm.get("total_events")
                self.capture["symbols_seen"] = sm.get("symbols_seen_count")
                self.capture["disconnect_count"] = sm.get("disconnect_count")
                self.capture["dropped_event_count"] = sm.get("dropped_event_count")
                self.capture["capture_complete"] = sm.get("capture_complete")
            except Exception:
                pass
        if seal_path.is_file():
            try:
                seal = json.loads(seal_path.read_text(encoding="utf-8"))
                self.capture["seal_pass"] = bool(seal.get("seal_pass"))
            except Exception:
                pass

    def step_capture_finalize_verify(self) -> dict[str, Any]:
        """Verify capture seal, or accept CAPTURE_CONTINUING until 15:35 on live runs.

        Sidecar finalizes independently at 15:35; parent runner must not kill it.
        Test harness uses skip_capture_wait + operator_stop to force seal.
        """
        started = time.time()
        out = Path(self.capture.get("output") or "")
        if out.is_dir() and self.skip_capture_wait:
            from small_paper.capture_child_cleanup import request_graceful_stop

            pid = int(self.capture.get("pid") or 0)
            request_graceful_stop(
                out,
                session_id=str(self.capture.get("session_id") or ""),
                pid=pid,
                reason="skip_capture_wait",
            )
            deadline = time.time() + 15
            while time.time() < deadline:
                if (out / "capture_seal.json").is_file():
                    break
                time.sleep(0.2)
        self._refresh_capture_stats()
        seal_ok = bool(self.capture.get("seal_pass"))
        override = bool(self.capture.get("override_used"))
        continuing = False
        if not seal_ok and not override and self.capture.get("started") and not self.skip_capture_wait:
            # Live: sidecar still running until 15:35 — not a failure
            continuing = True
            self.capture["final_status"] = self.capture.get("status") or "CAPTURE_ONLINE"
            self.capture["capture_complete"] = False
            self.capture["seal_pass"] = None
            self.capture["continuing_until"] = f"{self.trading_date} 15:35 JST"
        ok = seal_ok or override or continuing
        step = self._record(
            "capture_finalize_verify",
            17,
            "capture_seal_verify",
            exit_code=0 if ok else 1,
            started=started,
            stdout=json.dumps(
                {
                    k: self.capture.get(k)
                    for k in ("status", "event_count", "seal_pass", "capture_complete", "continuing_until")
                },
                ensure_ascii=False,
            ),
            result="PASS" if ok else "FAIL",
            blocked_reason="" if ok else "capture_seal_missing",
        )
        self._print_step(17, self._step_total, "Capture finalize", step.result)
        if continuing:
            print()
            print("[MARKET CAPTURE]")
            print(f"status: CONTINUING_UNTIL_1535")
            print(f"events: {self.capture.get('event_count')}")
            print(f"pid: {self.capture.get('pid')}")
            print(f"output: {self.capture.get('output')}")
            print(f"scheduled_end: {self.trading_date} 15:35 JST")
            print("note: Sidecar finalizes independently; Paper runner does not wait")
        else:
            self._print_capture_finish()
        return {"ok": ok, "capture": dict(self.capture), "continuing": continuing}

    def _paper_block_but_capture_continues(self, step_name: str) -> None:
        self.paper_blocked_capture_continues = True
        self._refresh_capture_stats()
        self._print_blocked()
        # Phase687W10: OPERATIONS ownership = Checked Runner (fail-open)
        try:
            from notify.discord_notification_router import get_router

            b = self.blocked or {}
            get_router(self.native_root).publish_paper_blocked(
                failed_step=str(b.get("failed_step") or step_name),
                reason=str(b.get("reason") or ""),
                next_action=str(b.get("next_action") or ""),
                capture_status=str(self.capture.get("status") or ""),
                capture_pid=self.capture.get("pid"),
                capture_output=str(self.capture.get("output") or ""),
                capture_continues=True,
                trading_date=self.trading_date,
            )
        except Exception:
            pass

    def _print_post(self) -> None:
        p = self.post_session
        print()
        print("[POST SESSION]")
        for k in (
            "paper_exit_code",
            "w4s_verdict",
            "sessions_collected",
            "readonly_success_sessions",
            "seal_status",
            "seal_entry_count",
            "seal_required_count",
            "mapping_loss",
            "duplicate_intent",
            "reservation_leak",
            "actual_submit",
            "actual_cancel",
            "latency_p95",
            "result",
        ):
            print(f"{k}: {p.get(k)}")
        if p.get("am_pm_sessions"):
            print("am_pm_snapshots:")
            for s in p["am_pm_sessions"]:
                print(f"  - {s.get('session_id')} {s.get('path')} seal={s.get('session_seal_status')}")

    def _print_finish(self) -> None:
        p = self.post_session
        print()
        print("========================================")
        print("Paper Trade Finished")
        print(f"Paper result: {self.paper_exit_code}")
        print(f"Forward soak: {p.get('w4s_verdict')}")
        print(f"Real submit/cancel: {p.get('actual_submit', 0)} / {p.get('actual_cancel', 0)}")
        print("========================================")

    def write_logs(self) -> tuple[Path, Path]:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
        log_path = LOG_DIR / f"checked_runner_{stamp}.log"
        json_path = LOG_DIR / f"checked_runner_{stamp}.json"
        payload = {
            "trading_date": self.trading_date,
            "trading_date_source": "JST_runtime",
            "fixed_date_forbidden": True,
            "pythonpath": default_pythonpath(),
            "paper_bat": str(self.paper_bat),
            "paper_call_count": self.paper_call_count,
            "w4s_call_count": self.w4s_call_count,
            "paper_exit_code": self.paper_exit_code,
            "blocked": self.blocked,
            "blocked_reason": (self.blocked or {}).get("reason"),
            "steps": [s.to_dict() for s in self.steps],
            "post_session": self.post_session,
            "verdict": self.verdict,
            "real_orders": "DISABLED",
            "secrets_logged": False,
            "generated_at": _now_iso(),
        }
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        lines = [
            f"Trading date: {self.trading_date}",
            f"Verdict: {self.verdict}",
            f"Paper exit: {self.paper_exit_code}",
            f"W4S: {self.post_session.get('w4s_verdict')}",
            "",
        ]
        for s in self.steps:
            lines.append(
                f"{s.name}: result={s.result} exit={s.exit_code} duration={s.duration_sec}s reason={s.blocked_reason}"
            )
            if s.stdout_tail:
                lines.append(redact_secrets(s.stdout_tail[:500]))
        if self.blocked:
            lines.append(f"BLOCKED: {json.dumps(self.blocked, ensure_ascii=False)}")
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return log_path, json_path

    def _install_signal_handlers(self) -> None:
        if self._signal_handlers_installed:
            return
        self._signal_handlers_installed = True
        try:
            atexit.register(self._atexit_cleanup)
        except Exception:
            pass
        # Pytest owns SIGINT; finally/atexit still cover synthetic cleanup.
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return

        def _on_signal(signum, frame):  # noqa: ANN001
            self._shutdown_reason = "signal"
            try:
                self.cleanup_owned_capture(reason="signal")
            except Exception:
                pass
            if signum == getattr(signal, "SIGINT", None):
                raise KeyboardInterrupt()
            raise SystemExit(128 + int(signum or 0))

        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            sig = getattr(signal, name, None)
            if sig is None:
                continue
            try:
                signal.signal(sig, _on_signal)
            except (ValueError, OSError):
                pass

    def _atexit_cleanup(self) -> None:
        if self._cleanup_done:
            return
        try:
            self.cleanup_owned_capture(reason="atexit_orphan")
        except Exception:
            pass

    def cleanup_owned_capture(self, reason: Optional[str] = None) -> dict[str, Any]:
        """Idempotent owned-Capture stop. Never kills foreign processes."""
        from small_paper.capture_child_cleanup import (
            cleanup_owned_capture,
            write_cleanup_artifact,
        )

        # Reused Capture is never owned — never stop PID from a prior session.
        if self.reuse_capture or bool(self.capture.get("reused")):
            out = {
                "skipped": True,
                "skip_reason": "reused_capture_not_owned",
                "pid": self.capture.get("pid"),
                "reason": str(reason or self._shutdown_reason or ""),
            }
            self._cleanup_done = True
            self._cleanup_result = out
            self.capture["child_cleanup"] = dict(out)
            return out

        continue_skips = {
            "paper_blocked_capture_continues",
            "capture_continuing_until_scheduled_end",
        }
        if self._cleanup_done and self._cleanup_result is not None:
            prev = dict(self._cleanup_result)
            # Honor intentional continue: atexit / duplicate must not kill deferred Capture.
            if prev.get("skipped") and str(prev.get("skip_reason") or "") in continue_skips:
                prev["duplicate_cleanup"] = True
                return prev
            dup = cleanup_owned_capture(
                self._owned_capture,
                reason=str(reason or self._shutdown_reason or "duplicate_cleanup"),
                paper_blocked_capture_continues=bool(self.paper_blocked_capture_continues),
                continuing_until_scheduled_end=bool(self.capture.get("continuing_until")),
                seal_pass=bool(self.capture.get("seal_pass")),
                skip_capture_wait=bool(self.skip_capture_wait),
                graceful_timeout_sec=2.0,
                terminate_timeout_sec=1.0,
            )
            out = dup.to_dict()
            out["duplicate_cleanup"] = True
            self._cleanup_result = out
            return out

        why = str(reason or self._shutdown_reason or "normal_exit")
        self._shutdown_reason = why
        result = cleanup_owned_capture(
            self._owned_capture,
            reason=why,
            paper_blocked_capture_continues=bool(self.paper_blocked_capture_continues),
            continuing_until_scheduled_end=bool(self.capture.get("continuing_until")),
            seal_pass=bool(self.capture.get("seal_pass")),
            skip_capture_wait=bool(self.skip_capture_wait),
        )
        self._cleanup_done = True
        self._cleanup_result = result.to_dict()
        self.capture["child_cleanup"] = dict(self._cleanup_result)
        try:
            write_cleanup_artifact(self.native_root, self.trading_date, result)
        except OSError:
            pass
        return dict(self._cleanup_result)

    def run(self) -> int:
        self._shutdown_reason = "normal_exit"
        self._install_signal_handlers()
        exit_code = 1
        try:
            try:
                from small_paper.env_loader import ensure_repo_dotenv, log_webhook_configured

                st = ensure_repo_dotenv(repo_root=self.repo_root)
                log_webhook_configured(st)
            except Exception:
                pass
            self.trading_date = trading_date_jst()
            self._print_banner()

            # Phase687W9/W15B order:
            # 1 JST date (done) → 2 disk → 3 kabu readonly → 4 universe prebuild → 5 universe resolve
            # → 6 registration → 7 capture start → … → paper path → capture finalize
            if not self.step_disk_guard():
                self._print_blocked()
                self.write_logs()
                exit_code = int((self.blocked or {}).get("exit_code") or 1)
                return exit_code
            if not self.step_kabu_readonly():
                self._print_blocked()
                self.write_logs()
                exit_code = int((self.blocked or {}).get("exit_code") or 1)
                return exit_code
            if not self.step_legacy_register_preclear():
                self._print_blocked()
                self.write_logs()
                exit_code = int((self.blocked or {}).get("exit_code") or 1)
                return exit_code
            if not self.step_universe_prebuild():
                self._print_blocked()
                self.write_logs()
                exit_code = int((self.blocked or {}).get("exit_code") or 1)
                return exit_code
            if not self.step_universe_resolve():
                self._print_blocked()
                self.write_logs()
                exit_code = int((self.blocked or {}).get("exit_code") or 1)
                return exit_code
            # Demo / Comm-fault E2E: skip Kabu register write (submit/register forbidden)
            if self.demo_push_e2e or self.comm_fault_e2e:
                started = time.time()
                skip_label = "SKIPPED_COMM_FAULT_E2E" if self.comm_fault_e2e else "SKIPPED_DEMO_PUSH_E2E"
                self._record(
                    "registration",
                    6,
                    skip_label,
                    exit_code=0,
                    started=started,
                    result="PASS",
                    info_only=True,
                )
                self._print_step(
                    6,
                    self._step_total,
                    "Registration",
                    "SKIP(comm-fault)" if self.comm_fault_e2e else "SKIP(demo)",
                )
            elif not self.step_registration_coordination():
                self._print_blocked()
                self.write_logs()
                exit_code = int((self.blocked or {}).get("exit_code") or 1)
                return exit_code
            if self.demo_push_e2e or self.comm_fault_e2e:
                # Capture ingest happens inside certification harness (temp workspace)
                started = time.time()
                cap_label = (
                    "COMM_FAULT_CAPTURE_DEFERRED" if self.comm_fault_e2e else "DEMO_CAPTURE_INGEST_DEFERRED"
                )
                self._record(
                    "capture_sidecar",
                    7,
                    cap_label,
                    exit_code=0,
                    started=started,
                    result="PASS",
                    info_only=True,
                )
                self.capture["started"] = True
                self.capture["status"] = "COMM_FAULT_DEFERRED" if self.comm_fault_e2e else "DEMO_DEFERRED"
                self._print_step(
                    7,
                    self._step_total,
                    "Capture sidecar",
                    "COMM_FAULT" if self.comm_fault_e2e else "DEMO",
                )
            elif not self.step_start_capture():
                self._print_blocked()
                self.write_logs()
                exit_code = int((self.blocked or {}).get("exit_code") or 1)
                return exit_code

            # Paper path — failures must NOT stop capture (live continue-to-15:35 policy)
            paper_path_ok = True
            if not self.step_cache_prebuild():
                paper_path_ok = False
                self._paper_block_but_capture_continues("cache_prebuild")
            elif not self.step_preflight():
                paper_path_ok = False
                self._paper_block_but_capture_continues("preflight")
            elif not self.step_smoke():
                paper_path_ok = False
                self._paper_block_but_capture_continues("smoke")
            elif not self.step_recovery():
                paper_path_ok = False
                self._paper_block_but_capture_continues("recovery")
            elif not self.step_design_consistency():
                paper_path_ok = False
                self._paper_block_but_capture_continues("design_consistency")
            else:
                self.step_production_enablement_info()
                if not self.step_safety_flags():
                    paper_path_ok = False
                    self._paper_block_but_capture_continues("safety_flags")

            paper_code = 1
            paper_ok = False
            if paper_path_ok:
                # V1R Primary fail-closed gate BEFORE classic paper bat.
                # If assertion fails: NO PAPER PRIMARY (do not fall back to PBv2).
                try:
                    from small_paper.v1r_exit_v2_activation_gate import (
                        ASSERTION_FAIL,
                        assert_exit_v2_primary_roles,
                    )
                    _v1r_assert = assert_exit_v2_primary_roles()
                    print(_v1r_assert.startup_block, flush=True)
                    self._record(
                        "v1r_exit_v2_primary_role_assertion",
                        8,
                        "assert_exit_v2_primary_roles",
                        exit_code=0 if _v1r_assert.ok else 2,
                        started=time.time(),
                        result="PASS" if _v1r_assert.ok else "FAIL",
                        blocked_reason="" if _v1r_assert.ok else (_v1r_assert.reason or ASSERTION_FAIL),
                    )
                    if not _v1r_assert.ok:
                        paper_path_ok = False
                        self.blocked = {
                            "step": "v1r_exit_v2_primary_role_assertion",
                            "reason": _v1r_assert.reason or ASSERTION_FAIL,
                            "exit_code": 2,
                            "no_pbv2_primary_fallback": True,
                            "no_fixed600_primary_fallback": True,
                        }
                        self._paper_block_but_capture_continues("v1r_exit_v2_primary_role_assertion")
                except Exception as exc:
                    paper_path_ok = False
                    self.blocked = {
                        "step": "v1r_exit_v2_primary_role_assertion",
                        "reason": f"V1R_EXIT_V2_PRIMARY_ROLE_ASSERTION_FAILED:exception:{exc}",
                        "exit_code": 2,
                        "no_pbv2_primary_fallback": True,
                        "no_fixed600_primary_fallback": True,
                    }
                    self._paper_block_but_capture_continues("v1r_exit_v2_primary_role_assertion")

            if paper_path_ok:
                paper_code = self.step_start_paper()
                paper_ok = paper_code == 0
                if self.comm_fault_e2e:
                    self.post_session = {
                        "result": "COMM_FAULT_E2E_OK" if paper_ok else "COMM_FAULT_E2E_FAIL",
                        "w4s_verdict": "NOT_RUN",
                        "sessions_collected": 0,
                        "counted_as_forward_session": False,
                        "actual_submit": 0,
                        "actual_cancel": 0,
                        "comm_fault_e2e": True,
                        "comm_fault_verdict": (self.comm_fault_summary or {}).get("verdict"),
                    }
                    self._print_post()
                    self._print_finish()
                elif self.demo_push_e2e:
                    self.post_session = {
                        "result": "DEMO_PUSH_E2E_OK" if paper_ok else "DEMO_PUSH_E2E_FAIL",
                        "w4s_verdict": "NOT_RUN",
                        "sessions_collected": 0,
                        "counted_as_forward_session": False,
                        "actual_submit": 0,
                        "actual_cancel": 0,
                        "demo_push_e2e": True,
                        "demo_verdict": (self.demo_push_summary or {}).get("verdict"),
                    }
                    self._print_post()
                    self._print_finish()
                else:
                    self.step_post_session(paper_ok=paper_ok)
                    self._print_post()
                    self._print_finish()
            else:
                self.paper_exit_code = int((self.blocked or {}).get("exit_code") or 1)
                self.post_session = {
                    "result": "PAPER_BLOCKED",
                    "w4s_verdict": "NOT_RUN",
                    "sessions_collected": 0,
                    "counted_as_forward_session": False,
                    "actual_submit": 0,
                    "actual_cancel": 0,
                    "paper_blocked_capture_continues": True,
                }

            # Capture continues to scheduled end (tests: operator_stop via skip_capture_wait)
            if self.demo_push_e2e or self.comm_fault_e2e:
                started = time.time()
                fin_label = (
                    "COMM_FAULT_NO_LIVE_CAPTURE_FINALIZE"
                    if self.comm_fault_e2e
                    else "DEMO_NO_LIVE_CAPTURE_FINALIZE"
                )
                self._record(
                    "capture_finalize",
                    17,
                    fin_label,
                    exit_code=0,
                    started=started,
                    result="PASS",
                    info_only=True,
                )
                self._print_step(
                    17,
                    self._step_total,
                    "Capture finalize",
                    "COMM_FAULT" if self.comm_fault_e2e else "DEMO",
                )
            else:
                self.step_capture_finalize_verify()

            if self.paper_call_count > 1 or self.w4s_call_count > 1:
                self.verdict = VERDICT_POST
            elif (self.demo_push_e2e or self.comm_fault_e2e) and paper_ok:
                self.verdict = VERDICT_READY
            elif not paper_path_ok:
                if self.capture.get("started") and not self.capture.get("override_used"):
                    self.verdict = VERDICT_PRECHECK
                else:
                    self.verdict = VERDICT_PRECHECK
            elif self.skip_w4s:
                self.verdict = VERDICT_POST
                self.post_session["counted_as_forward_session"] = False
                self.post_session["counted_as_normal_session"] = False
                if self.post_session.get("w4s_verdict") != "NOT_RUN":
                    self.post_session["w4s_verdict"] = "NOT_RUN"
                self.post_session["sessions_collected"] = 0
            elif not paper_ok:
                self.verdict = VERDICT_POST
            elif not self.post_session.get("counted_as_forward_session"):
                self.verdict = VERDICT_POST
            elif self.post_session.get("result") != "OK":
                self.verdict = VERDICT_POST
            elif self.post_session.get("actual_submit", 0) or self.post_session.get("actual_cancel", 0):
                self.verdict = VERDICT_POST
            else:
                self.verdict = VERDICT_READY

            payload_extra = {
                "capture": self.capture,
                "universe_prebuild": self.universe_prebuild,
                "paper_blocked_capture_continues": self.paper_blocked_capture_continues,
            }
            self.write_logs()
            try:
                logs = sorted((LOG_DIR).glob("checked_runner_*.json"), key=lambda p: p.stat().st_mtime)
                if logs:
                    last = logs[-1]
                    data = json.loads(last.read_text(encoding="utf-8"))
                    data.update(payload_extra)
                    last.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            except Exception:
                pass

            if self.verdict == VERDICT_CAPTURE_REQUIRED:
                exit_code = 1
            elif self.verdict == VERDICT_READY and paper_ok:
                exit_code = 0
            elif paper_ok and self.verdict == VERDICT_POST:
                exit_code = 2
            elif not paper_path_ok:
                exit_code = int((self.blocked or {}).get("exit_code") or 1)
            else:
                exit_code = paper_code or 1
            return exit_code
        except KeyboardInterrupt:
            self._shutdown_reason = "keyboard_interrupt"
            print("\n[CHECKED RUNNER] KeyboardInterrupt — stopping owned Capture sidecar")
            exit_code = 130
            return exit_code
        except Exception as exc:
            self._shutdown_reason = "exception"
            print(f"\n[CHECKED RUNNER] Exception — stopping owned Capture sidecar: {type(exc).__name__}: {exc}")
            raise
        finally:
            try:
                self.cleanup_owned_capture(reason=self._shutdown_reason)
            except Exception as cleanup_exc:
                print(f"[CHECKED RUNNER] cleanup error: {type(cleanup_exc).__name__}: {cleanup_exc}")


def existing_paper_bat_sha256(path: Path = DEFAULT_PAPER_BAT) -> str:
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


# Known baseline hash captured when Phase687W8 wrapper was introduced (must not change bat).
EXISTING_PAPER_BAT_SHA256_BASELINE = "521ca914de07398bda624b715a5e9cbd678c0685008535cfa0fa02d28f4281c6"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="One-command Paper Trade checked runner (Phase687W8/W9)")
    parser.add_argument("--no-pause", action="store_true", help="Do not pause at end (default for python entry)")
    parser.add_argument("--skip-paper", action="store_true", help="Skip calling run_paper_trade.bat (tests)")
    parser.add_argument("--skip-w4s", action="store_true", help="Skip W4S evaluator (tests)")
    parser.add_argument(
        "--allow-paper-without-capture",
        action="store_true",
        help="Override: allow Paper when capture sidecar fails (NOT default; real orders stay disabled)",
    )
    parser.add_argument("--capture-synthetic", action="store_true", help="Test-only synthetic capture sidecar")
    parser.add_argument("--skip-capture-wait", action="store_true", help="Test-only: operator-stop capture instead of 15:35 wait")
    parser.add_argument(
        "--reuse-capture",
        action="store_true",
        help="Attach to an already-running Independent Market Ingress (fail-closed; no new spawn)",
    )
    parser.add_argument(
        "--reuse-capture-pid",
        type=int,
        default=0,
        help="Optional expected Capture PID when using --reuse-capture (fail-closed if mismatch)",
    )
    parser.add_argument(
        "--demo-push-e2e",
        action="store_true",
        help="Phase687W20: demo PUSH full runtime path (fail-closed; also TRADEBOT_DEMO_PUSH_E2E=1)",
    )
    parser.add_argument(
        "--comm-fault-e2e",
        action="store_true",
        help="Phase687W21: Kabu comm fault injection & recovery (fail-closed; also TRADEBOT_COMM_FAULT_E2E=1)",
    )
    parser.add_argument("--repo-root", type=str, default=str(REPO_ROOT))
    parser.add_argument("--native-root", type=str, default=str(NATIVE_ROOT))
    parser.add_argument("--paper-bat", type=str, default=str(DEFAULT_PAPER_BAT))
    args = parser.parse_args(list(argv) if argv is not None else None)

    demo = bool(args.demo_push_e2e) or str(os.environ.get("TRADEBOT_DEMO_PUSH_E2E", "")).strip() in (
        "1",
        "true",
        "yes",
        "on",
    )
    comm_fault = bool(args.comm_fault_e2e) or str(
        os.environ.get("TRADEBOT_COMM_FAULT_E2E", "")
    ).strip() in (
        "1",
        "true",
        "yes",
        "on",
    )
    harness = demo or comm_fault
    if bool(args.reuse_capture) and harness:
        print("[CHECKED RUNNER] --reuse-capture incompatible with demo/comm-fault harness", flush=True)
        return 2
    runner = PaperTradeCheckedRunner(
        repo_root=Path(args.repo_root),
        native_root=Path(args.native_root),
        paper_bat=Path(args.paper_bat),
        skip_paper=bool(args.skip_paper),
        skip_w4s=bool(args.skip_w4s) or harness,
        no_pause=bool(args.no_pause),
        allow_paper_without_capture=bool(args.allow_paper_without_capture),
        capture_synthetic=bool(args.capture_synthetic),
        skip_capture_wait=bool(args.skip_capture_wait) or harness,
        demo_push_e2e=demo,
        comm_fault_e2e=comm_fault,
        reuse_capture=bool(args.reuse_capture),
        reuse_capture_pid=int(args.reuse_capture_pid or 0) or None,
    )
    code = runner.run()
    if not args.no_pause:
        try:
            input("Press Enter to exit...")
        except EOFError:
            pass
    return code


if __name__ == "__main__":
    raise SystemExit(main())
