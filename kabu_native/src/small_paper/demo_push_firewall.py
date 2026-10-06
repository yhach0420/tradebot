"""Demo-push isolation firewall.

Production recovery, universe, capture, and cache paths stay unchanged unless
BOTH the checked-runner CLI flag and TRADEBOT_DEMO_PUSH_E2E are present.
Either signal alone fail-closes to the production path.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

_TRUE = {"1", "true", "yes", "on"}
_ENV = "TRADEBOT_DEMO_PUSH_E2E"
_CLI = "--demo-push-e2e"
_ISOLATED_ENV = "TRADEBOT_DEMO_PUSH_ISOLATED_ROOT"
_PARENT_CACHE: dict[str, bool] = {}


def env_demo_enabled(environ: Optional[dict[str, str]] = None) -> bool:
    src = os.environ if environ is None else environ
    return str(src.get(_ENV, "")).strip().lower() in _TRUE


def cli_demo_enabled(argv: Optional[Sequence[str]] = None) -> bool:
    args = list(sys.argv if argv is None else argv)
    return _CLI in args


def _windows_command_line(pid: int) -> str:
    if pid <= 0:
        return ""
    script = (
        "(Get-CimInstance Win32_Process -Filter "
        f"\"ProcessId={int(pid)}\").CommandLine"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout or ""


def parent_has_demo_cli() -> bool:
    cached = _PARENT_CACHE.get("hit")
    if cached is not None:
        return bool(cached)
    hit = False
    try:
        parent = os.getppid()
    except OSError:
        parent = 0
    if parent:
        hit = _CLI in _windows_command_line(parent)
    _PARENT_CACHE["hit"] = hit
    return hit


def demo_fully_armed(
    *,
    cli_flag: Optional[bool] = None,
    env_flag: Optional[bool] = None,
    consult_parent: bool = True,
) -> bool:
    """True only when the env and the CLI flag are both present."""
    env_on = env_demo_enabled() if env_flag is None else bool(env_flag)
    if not env_on:
        return False
    if cli_flag is not None:
        return bool(cli_flag)
    if cli_demo_enabled():
        return True
    if consult_parent:
        return parent_has_demo_cli()
    return False


def isolated_native_root() -> Path:
    override = str(os.environ.get(_ISOLATED_ENV, "")).strip()
    if override:
        return Path(override)
    here = Path(__file__).resolve()
    native = here.parents[2]
    return native / "results" / "small_paper" / "demo_push_e2e" / "isolated_native"


def isolated_recovery_result() -> dict[str, Any]:
    """Synthetic clean recovery authority. Does not read production session history."""
    from small_paper.operational_recovery import (
        dryrun_ready_evidence,
        evaluate_recovery_readiness,
    )

    result = evaluate_recovery_readiness(dryrun_ready_evidence())
    result["probe_mode"] = "demo_isolated_synthetic_clean"
    result["artifact_trace"] = {
        "gate": "demo_isolated_recovery",
        "production_history_consulted": False,
        "reference_session": None,
        "prior_sessions_found": 0,
        "prior_eval": {
            "status": "not_applicable_demo_isolated",
            "reason": "synthetic_clean_authority",
        },
        "design": {"pass": True, "recomputed": False, "status": "demo_isolated_not_consulted"},
        "config_sha": {"match": True, "status": "demo_isolated_not_consulted"},
    }
    result["production_history_consulted"] = False
    return result


def install_inprocess_isolation() -> bool:
    """Redirect later in-process production writers to the isolated demo root.

    No-op unless both demo signals are present. Safe to call more than once.
    """
    if not demo_fully_armed():
        return False
    if getattr(install_inprocess_isolation, "_installed", False):
        return True

    root = isolated_native_root()
    (root / "data" / "market_capture").mkdir(parents=True, exist_ok=True)
    (root / "runtime").mkdir(parents=True, exist_ok=True)
    os.environ["KABU_TOKEN_AUTHORITY_DIR"] = str(root / "data" / "market_capture" / "demo_authority")
    os.environ["KABU_STATION_AUTHORITY_DIR"] = str(root / "runtime" / "kabu_station_authority")

    import small_paper.capture_child_cleanup as cleanup
    import small_paper.day_fixed_am_registration as registration
    import small_paper.derived_artifact_contract as derived
    import small_paper.runtime_lifecycle as lifecycle

    orig_freeze = registration.freeze_same_day_am_universe
    orig_finish = lifecycle.finish_teardown
    orig_cleanup = cleanup.write_cleanup_artifact
    orig_design = derived.evaluate_or_recompute_design_consistency

    def _freeze(native_root: Path, trading_date: str, **kwargs: Any) -> dict[str, Any]:
        root = isolated_native_root() if demo_fully_armed() else native_root
        return orig_freeze(root, trading_date, **kwargs)

    def _finish(*, native_root: Path, trading_date: str, owned_pid: int = 0) -> dict[str, Any]:
        root = isolated_native_root() if demo_fully_armed() else native_root
        return orig_finish(
            native_root=root,
            trading_date=str(trading_date),
            owned_pid=int(owned_pid or 0),
        )

    def _cleanup(native_root: Path, trading_date: str, result: Any) -> Path:
        root = isolated_native_root() if demo_fully_armed() else native_root
        return orig_cleanup(root, trading_date, result)

    def _design(native_root: Path, **kwargs: Any) -> dict[str, Any]:
        if demo_fully_armed():
            kwargs = dict(kwargs)
            kwargs["write"] = False
            kwargs["path"] = isolated_native_root() / "results" / "reports" / "design_consistency.json"
        return orig_design(native_root, **kwargs)

    registration.freeze_same_day_am_universe = _freeze
    lifecycle.finish_teardown = _finish
    cleanup.write_cleanup_artifact = _cleanup
    derived.evaluate_or_recompute_design_consistency = _design
    setattr(install_inprocess_isolation, "_installed", True)
    return True
