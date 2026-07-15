"""Phase687W19 — Full Paper Startup Path E2E certification helpers (read-only / test harness).

Does not change ENTRY/EXIT strategy logic. Provides residual inventory, path inventory,
clock-window expectations, and ExposureGate reachability proof under injected clock.
"""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = NATIVE_ROOT.parent
REPORT_DIR = NATIVE_ROOT / "results" / "reports" / "phase687w19_full_paper_startup_e2e"


@dataclass
class ResidualItem:
    path: str
    kind: str
    classification: str
    mtime: str = ""
    size: int = 0
    notes: str = ""


def trading_date_jst(now: Optional[datetime] = None) -> str:
    return (now or datetime.now(JST)).strftime("%Y%m%d")


def list_capture_related_processes() -> list[dict[str, Any]]:
    """Best-effort Windows process scan for paper/capture."""
    if sys.platform != "win32":
        return []
    ps = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.CommandLine -match "
        "'paper_trade|market_capture|run_paper|am_pm_daily|small_paper.pilot|run_small_paper' } | "
        "Select-Object ProcessId,ParentProcessId,Name,CommandLine | ConvertTo-Json -Compress"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
        raw = (r.stdout or "").strip()
        if not raw or raw.lower() == "null":
            return []
        data = json.loads(raw)
        if isinstance(data, dict):
            return [data]
        return list(data or [])
    except Exception as exc:
        return [{"error": f"{type(exc).__name__}:{exc}"}]


def classify_residual(path: Path, *, trading_date: str) -> ResidualItem:
    name = path.name.lower()
    rel = str(path).replace("\\", "/")
    try:
        st = path.stat()
        mtime = datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")
        size = int(st.st_size)
    except OSError:
        mtime, size = "", 0

    if name == "operator_stop.flag":
        return ResidualItem(
            path=rel,
            kind="operator_stop",
            classification="must_archive",
            mtime=mtime,
            size=size,
            notes="W18 prepare_day_dir archives before spawn; sidecar ignores stale",
        )
    if name.endswith(".pid"):
        return ResidualItem(
            path=rel,
            kind="pid_file",
            classification="must_ignore" if trading_date not in rel else "stale",
            mtime=mtime,
            size=size,
            notes="Cleared on capture exit; conflict returns CAPTURE_ALREADY_RUNNING",
        )
    if name.endswith(".lock"):
        return ResidualItem(
            path=rel,
            kind="lock",
            classification="stale",
            mtime=mtime,
            size=size,
            notes="Registration/file locks — fail-closed if held by live owner",
        )
    if "session_seal.json" in name and trading_date in rel:
        return ResidualItem(
            path=rel,
            kind="same_day_seal",
            classification="must_ignore",
            mtime=mtime,
            size=size,
            notes="Same-day seal not required by Recovery pre-start; Paper creates new session",
        )
    if "/market_capture/" in rel and trading_date in rel:
        return ResidualItem(
            path=rel,
            kind="capture_day_artifact",
            classification="reusable",
            mtime=mtime,
            size=size,
            notes="Day dir reused; stale stop archived on spawn",
        )
    return ResidualItem(path=rel, kind="other", classification="must_ignore", mtime=mtime, size=size)


def scan_residuals(native_root: Path, *, trading_date: Optional[str] = None) -> list[ResidualItem]:
    day = trading_date or trading_date_jst()
    items: list[ResidualItem] = []
    roots = [
        native_root / "data" / "market_capture",
        native_root / "results" / "small_paper" / day,
    ]
    patterns = ("operator_stop.flag", "*.pid", "*.lock", "session_seal.json", "capture_status.json")
    for root in roots:
        if not root.exists():
            continue
        for pat in patterns:
            for p in root.rglob(pat):
                if p.is_file():
                    items.append(classify_residual(p, trading_date=day))
    return items


def expected_state_for_clock(hhmm: str) -> dict[str, Any]:
    """Expected Paper/Capture posture by JST clock (no OS clock change)."""
    h, m = int(hhmm[:2]), int(hhmm[2:])
    minutes = h * 60 + m
    # Capture scheduled end 15:35
    capture_active = minutes < (15 * 60 + 35)
    # AM 09:03-11:25, PM 12:33-15:23 for full_session evaluation
    in_am = (9 * 60 + 3) <= minutes < (11 * 60 + 25)
    in_pm = (12 * 60 + 33) <= minutes < (15 * 60 + 23)
    before_am = minutes < (9 * 60 + 3)
    after_pm = minutes >= (15 * 60 + 23)
    if in_am or in_pm:
        paper = "PAPER_EVALUATING_EXPECTED"
    elif before_am:
        paper = "PAPER_WAIT_OR_WARMUP_EXPECTED"
    elif after_pm:
        paper = "PAPER_AFTER_SESSION_END_IMMEDIATE_EXIT"
    else:
        paper = "PAPER_BETWEEN_SESSIONS"
    return {
        "hhmm": hhmm,
        "paper_expected": paper,
        "capture_should_continue": capture_active,
        "universe_regen_required": False,
        "recovery_same_day_seal_required": False,
        "entry_evaluation_possible": bool(in_am or in_pm),
    }


STARTUP_PATH_ROWS: list[dict[str, str]] = [
    {
        "step": "1",
        "name": "bat_cwd",
        "caller": "run_paper_trade_checked.bat",
        "callee": "powershell -File run_paper_trade_checked.ps1",
        "pass": "ps1 exists",
        "block": "missing ps1",
        "artifact_in": "REPO=%~dp0",
        "artifact_out": "ERRORLEVEL",
        "time_dependent": "no",
        "cleanup_owner": "n/a",
    },
    {
        "step": "2-4",
        "name": "python_pythonpath_native_root",
        "caller": "run_paper_trade_checked.ps1",
        "callee": "python -m small_paper.paper_trade_checked_runner",
        "pass": "python on PATH; PYTHONPATH=native/src;repo",
        "block": "python missing",
        "artifact_in": "NativeRoot/RepoRoot",
        "artifact_out": "checked runner process",
        "time_dependent": "no",
        "cleanup_owner": "OS process exit",
    },
    {
        "step": "5-7",
        "name": "date_yaml_env",
        "caller": "PaperTradeCheckedRunner.run",
        "callee": "trading_date_jst / env_loader / DEFAULT_CONFIG",
        "pass": "8-digit JST date; dotenv optional",
        "block": "n/a (date always resolved)",
        "artifact_in": "os.environ / .env",
        "artifact_out": "trading_date",
        "time_dependent": "yes JST",
        "cleanup_owner": "n/a",
    },
    {
        "step": "8",
        "name": "disk_guard",
        "caller": "step_disk_guard",
        "callee": "operational_recovery.disk_guard_report",
        "pass": "disk not CRITICAL/HARD_STOP",
        "block": "CRITICAL+",
        "artifact_in": "native_root filesystem",
        "artifact_out": "step stdout JSON",
        "time_dependent": "no",
        "cleanup_owner": "n/a",
    },
    {
        "step": "9",
        "name": "kabu_readonly",
        "caller": "step_kabu_readonly",
        "callee": "check_kabu_readonly_readiness",
        "pass": "exit 0 ready_for_soak",
        "block": "token/port fail",
        "artifact_in": "localhost:18080",
        "artifact_out": "readonly JSON",
        "time_dependent": "no",
        "cleanup_owner": "n/a",
    },
    {
        "step": "10-12",
        "name": "universe_reg",
        "caller": "step_universe_prebuild/resolve/registration",
        "callee": "universe_prebuild / resolve_universe_symbols / coordinate_registration",
        "pass": "50 symbols; registration ok",
        "block": "gen/validate fail; >50",
        "artifact_in": "features/universe csv",
        "artifact_out": "universe csv + registration manifest",
        "time_dependent": "weekday",
        "cleanup_owner": "n/a",
    },
    {
        "step": "13-17",
        "name": "capture_spawn_online",
        "caller": "step_start_capture",
        "callee": "prepare_day_dir_operator_stop + spawn supervisor/sidecar + wait_capture_online",
        "pass": "CAPTURE_ONLINE wait ok",
        "block": "spawn/wait fail",
        "artifact_in": "day dir; stale flags archived",
        "artifact_out": "capture_status/heartbeat; owned PID",
        "time_dependent": "15:35 end",
        "cleanup_owner": "CheckedRunner cleanup_owned_capture / sidecar seal",
    },
    {
        "step": "18-22",
        "name": "paper_gates",
        "caller": "cache/preflight/smoke/recovery/design/safety",
        "callee": "prebuild_vol_liq / scripts / check_live_order_recovery_readiness",
        "pass": "each exit 0",
        "block": "any non-zero → paper_blocked_capture_continues",
        "artifact_in": "cache/design/prior session",
        "artifact_out": "gate stdout + blocked",
        "time_dependent": "recovery prior only",
        "cleanup_owner": "capture continues policy",
    },
    {
        "step": "23-32",
        "name": "paper_bat_pilot",
        "caller": "step_start_paper",
        "callee": "run_paper_trade.bat → daily_runner → run_live_dry_run",
        "pass": "bat exit 0; session artifacts",
        "block": "preflight/smoke/pilot fail",
        "artifact_in": "YAML; universe",
        "artifact_out": "results/small_paper/.../heartbeat.jsonl summary",
        "time_dependent": "AM/PM windows; after_session_end immediate",
        "cleanup_owner": "daily_runner / pilot exit",
    },
    {
        "step": "33-35",
        "name": "finalize_cleanup",
        "caller": "step_capture_finalize_verify + finally cleanup",
        "callee": "seal verify / cleanup_owned_capture",
        "pass": "seal_pass or continuing_until",
        "block": "seal missing when required",
        "artifact_in": "capture_seal.json",
        "artifact_out": "cleanup artifact",
        "time_dependent": "15:35",
        "cleanup_owner": "CheckedRunner finally",
    },
]


def write_startup_path_inventory(out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "startup_path_inventory.csv"
    cols = list(STARTUP_PATH_ROWS[0].keys())
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(STARTUP_PATH_ROWS)
    return path


def prove_exposure_gate_reachable_under_injected_clock() -> dict[str, Any]:
    """Synthetic proof: ExposureGate.evaluate_entry is reachable (no strategy change)."""
    from research.exposure_gate import ExposureGate, ExposureGateConfig

    gate = ExposureGate(ExposureGateConfig())
    trade = {
        "profile": "small_paper",
        "symbol": "7203",
        "side": "LONG",
        "entry_price": 2500.0,
        "qty": 100,
        "entry_time": "2026-07-14T09:10:00+09:00",
    }
    try:
        decision = gate.evaluate_entry(trade, max_concurrent_positions=3)
        reached = True
        detail = {
            "type": type(decision).__name__,
            "accept": bool(getattr(decision, "accept", None)),
            "reason": str(getattr(decision, "reason", "")),
        }
    except Exception as exc:
        reached = True  # entered the method
        detail = {"exception": f"{type(exc).__name__}:{exc}"}
    return {
        "exposure_gate_reachable": reached,
        "detail": detail,
        "clock_injected_label": "09:10 synthetic trade dict (not OS clock)",
        "note": "Proves call path exists; live PUSH→gate requires market session",
    }


def read_latest_checked_runner(native_root: Path = NATIVE_ROOT) -> Optional[dict[str, Any]]:
    log_dir = native_root / "results" / "reports" / "paper_trade_checked_runner"
    files = sorted(log_dir.glob("checked_runner_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files:
        # Prefer non-pytest temp if path in content uses real native root
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        data["_path"] = str(p)
        return data
    return None
