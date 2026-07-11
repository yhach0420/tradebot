"""Phase687W8 — One-command Paper Trade orchestrator audit."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = NATIVE_ROOT.parent
REPORT_DIR = NATIVE_ROOT / "results" / "reports" / "phase687w8_paper_trade_checked_runner"
DOCS = NATIVE_ROOT / "docs" / "live_trading"
JST = ZoneInfo("Asia/Tokyo")

VERDICT_READY = "ONE_COMMAND_PAPER_RUNNER_READY"
VERDICT_PRECHECK = "PRECHECK_ORCHESTRATION_FAILED"
VERDICT_SAFETY = "SAFETY_FLAG_VALIDATION_FAILED"
VERDICT_BAT = "EXISTING_RUNNER_MODIFIED"


def _run(cmd: list[str]) -> dict[str, Any]:
    import os

    env = os.environ.copy()
    env["PYTHONPATH"] = f"{NATIVE_ROOT / 'src'};{REPO_ROOT}"
    proc = subprocess.run(cmd, cwd=str(NATIVE_ROOT), env=env, capture_output=True, text=True)
    return {
        "cmd": cmd,
        "returncode": proc.returncode,
        "ok": proc.returncode == 0,
        "stdout_tail": (proc.stdout or "")[-1500:],
        "stderr_tail": (proc.stderr or "")[-400:],
    }


def _wj(name: str, obj: Any) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    sys.path.insert(0, str(NATIVE_ROOT / "src"))
    sys.path.insert(0, str(REPO_ROOT))
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    from small_paper.paper_trade_checked_runner import (
        EXISTING_PAPER_BAT_SHA256_BASELINE,
        default_pythonpath,
        existing_paper_bat_sha256,
        trading_date_jst,
    )

    smoke = _run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_phase687w8_paper_trade_checked_runner.py",
            "-q",
            "--tb=line",
        ]
    )
    _wj("phase687w8_smoke_result.json", smoke)

    bat = REPO_ROOT / "run_paper_trade.bat"
    bat_sha = existing_paper_bat_sha256(bat).lower()
    bat_ok = bat.is_file() and bat_sha == EXISTING_PAPER_BAT_SHA256_BASELINE.lower()
    _wj(
        "phase687w8_existing_bat_integrity.json",
        {
            "path": str(bat),
            "sha256": bat_sha,
            "baseline": EXISTING_PAPER_BAT_SHA256_BASELINE.lower(),
            "unchanged": bat_ok,
            "pass": bat_ok,
        },
    )

    wrappers = {
        "bat": (REPO_ROOT / "run_paper_trade_checked.bat").is_file(),
        "ps1": (NATIVE_ROOT / "scripts" / "run_paper_trade_checked.ps1").is_file(),
        "python_module": (NATIVE_ROOT / "src" / "small_paper" / "paper_trade_checked_runner.py").is_file(),
        "pythonpath_auto": "src" in default_pythonpath() and str(REPO_ROOT) in default_pythonpath(),
        "jst_date": len(trading_date_jst()) == 8,
        "user_command": r"cd C:\Users\yhach\Documents\tradebotfile; .\run_paper_trade_checked.bat",
    }
    wrappers["pass"] = all(
        [
            wrappers["bat"],
            wrappers["ps1"],
            wrappers["python_module"],
            wrappers["pythonpath_auto"],
            wrappers["jst_date"],
        ]
    )
    _wj("phase687w8_wrapper_presence.json", wrappers)

    preflight = {
        "live_trading_enabled": False,
        "order_enabled": False,
        "real_orders": "DISABLED",
        "pass": True,
        "checked_at": datetime.now(JST).isoformat(timespec="seconds"),
    }
    try:
        from small_paper.config import load_pilot_config

        cfg = load_pilot_config(
            NATIVE_ROOT
            / "configs"
            / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
        )
        preflight["live_trading_enabled"] = bool(cfg.live_trading_enabled)
        preflight["order_enabled"] = bool(cfg.order_enabled)
        preflight["pass"] = (not cfg.live_trading_enabled) and (not cfg.order_enabled)
    except Exception as exc:
        preflight["pass"] = False
        preflight["error"] = type(exc).__name__
    _wj("phase687w8_preflight_result.json", preflight)

    adr = DOCS / "adr" / "ADR-687W8-one-command-paper-runner.md"
    doc_rev = {
        "adr_present": adr.is_file(),
        "operations": "687W8" in (DOCS / "live_order_operations.md").read_text(encoding="utf-8")
        if (DOCS / "live_order_operations.md").is_file()
        else False,
        "system_design": "Phase687W8" in (DOCS / "live_order_system_design.md").read_text(encoding="utf-8")
        if (DOCS / "live_order_system_design.md").is_file()
        else False,
        "pass": False,
    }
    doc_rev["pass"] = all([doc_rev["adr_present"], doc_rev["operations"], doc_rev["system_design"]])
    _wj("phase687w8_documentation_review.json", doc_rev)

    checks = {
        "smoke": smoke.get("ok", False),
        "existing_bat_unchanged": bat_ok,
        "wrappers": wrappers.get("pass", False),
        "preflight": preflight.get("pass", False),
        "docs": doc_rev.get("pass", False),
    }
    if not bat_ok:
        verdict = VERDICT_BAT
    elif not checks["smoke"] or not checks["wrappers"]:
        verdict = VERDICT_PRECHECK
    elif not checks["preflight"]:
        verdict = VERDICT_SAFETY
    elif not checks["docs"]:
        verdict = VERDICT_PRECHECK
    else:
        verdict = VERDICT_READY

    report = {
        "phase": "687W8",
        "verdict": verdict,
        "checks": checks,
        "user_command": wrappers["user_command"],
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
    }
    _wj("phase687w8_report.json", report)
    (REPORT_DIR / "phase687w8_decision.md").write_text(
        f"""# Phase687W8 Decision

**Verdict:** `{verdict}`

## User command (only one)

```bat
cd C:\\Users\\yhach\\Documents\\tradebotfile
.\\run_paper_trade_checked.bat
```

Optional: `.\\run_paper_trade_checked.bat --no-pause`

## Behavior
- Prechecks fail-closed before Paper
- Calls existing `run_paper_trade.bat` once (unchanged)
- Post: W4S forward soak evaluator once
- production enablement NOT_AUTHORIZED is informational only
- Real orders remain DISABLED

## Absolute gates
- live_trading_enabled=false / order_enabled=false
- submit/cancel=0 / HARD_FAIL
- production NOT AUTHORIZED / NOT IMPLEMENTED
""",
        encoding="utf-8",
    )
    print(json.dumps({"verdict": verdict, "checks": checks}, indent=2))
    return 0 if verdict == VERDICT_READY else 1


if __name__ == "__main__":
    raise SystemExit(main())
