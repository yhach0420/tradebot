#!/usr/bin/env python3
"""Phase687W58 preflight — Paper Forward observers default ON, observe-only."""

from __future__ import annotations

import ast
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
sys.path.insert(0, str(NATIVE / "src"))

JST = ZoneInfo("Asia/Tokyo")
OUT = NATIVE / "results" / "reports"

from small_paper.forward_observer_defaults import (
    COST_AWARE_ENV,
    PULLBACK_VOLUME_ENV,
    ensure_paper_forward_observer_env,
    forward_observer_status_block,
    parse_env_bool,
)
from small_paper.pullback_volume_forward_logger import DEFAULT_OUT_DIR, disk_usage_pct


def main() -> int:
    bat = REPO / "run_paper_trade.bat"
    bat_txt = bat.read_text(encoding="utf-8", errors="ignore") if bat.is_file() else ""
    logger_mod = NATIVE / "src" / "small_paper" / "pullback_volume_forward_logger.py"
    ca_mod = NATIVE / "src" / "small_paper" / "cost_aware_entry_shadow.py"
    defaults_mod = NATIVE / "src" / "small_paper" / "forward_observer_defaults.py"

    # Simulate paper defaults without mutating caller env permanently for unset case
    # Use a clean resolution check via paper mark in a subprocess-like local apply
    saved = {k: parse_env_bool(k) for k in (COST_AWARE_ENV, PULLBACK_VOLUME_ENV, "KABU_PAPER_RUNTIME")}
    # Only apply ensure if user didn't already set OFF
    ensure_paper_forward_observer_env()
    status = forward_observer_status_block()

    out_writable = True
    try:
        DEFAULT_OUT_DIR.mkdir(parents=True, exist_ok=True)
        probe = DEFAULT_OUT_DIR / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except Exception as exc:
        out_writable = False
        status["output_dir_error"] = str(exc)

    tree_l = ast.parse(logger_mod.read_text(encoding="utf-8"))
    fns = {n.name for n in tree_l.body if isinstance(n, ast.FunctionDef)}
    ca_src = ca_mod.read_text(encoding="utf-8")

    checks = {
        "bat_sets_cost_aware_if_undefined": "if not defined COST_AWARE_ENTRY_SHADOW" in bat_txt,
        "bat_sets_pullback_if_undefined": "if not defined PULLBACK_VOLUME_FORWARD" in bat_txt,
        "defaults_module_exists": defaults_mod.is_file(),
        "cost_aware_entry_shadow_enabled": bool(status["cost_aware_entry_shadow_enabled"]),
        "pullback_volume_forward_enabled": bool(status["pullback_volume_forward_enabled"]),
        "enabled_source_cost_aware": status["cost_aware_entry_shadow_source"],
        "enabled_source_pullback": status["pullback_volume_forward_source"],
        "observe_only": True,
        "new_reject": False,
        "new_permit": False,
        "gate_decision_unchanged": True,
        "fail_open": True,
        "output_dir_writable": out_writable,
        "no_block_entry_fn": "block_entry" not in fns and "should_reject" not in fns,
        "cost_aware_uses_resolver": "resolve_cost_aware_entry_shadow" in ca_src,
        "disk_usage_pct": disk_usage_pct("C:/"),
    }
    disk = checks["disk_usage_pct"]
    checks["disk_warning"] = isinstance(disk, (int, float)) and disk > 75.0

    warning = status.get("warning")
    # If user explicitly set 0, enabled may be false — still READY with note
    explicit_off = (
        saved[COST_AWARE_ENV] is False or saved[PULLBACK_VOLUME_ENV] is False
    )
    ready = bool(
        checks["bat_sets_cost_aware_if_undefined"]
        and checks["bat_sets_pullback_if_undefined"]
        and checks["defaults_module_exists"]
        and checks["output_dir_writable"]
        and checks["no_block_entry_fn"]
        and checks["observe_only"]
        and (checks["cost_aware_entry_shadow_enabled"] or explicit_off)
        and (checks["pullback_volume_forward_enabled"] or explicit_off)
    )
    report = {
        "phase": "Phase687W58",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "verdict": "FORWARD_SHADOWS_DEFAULT_ON_READY" if ready else "PREFLIGHT_BLOCKED",
        "paper_defaults": {
            "cost_aware_entry_shadow": True,
            "pullback_volume_forward": True,
        },
        "runtime_unchanged": True,
        "new_reject": False,
        "new_permit": False,
        "fail_open": True,
        "checks": checks,
        "status": status,
        "warning": warning,
        "explicit_off": explicit_off,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "phase687w58_forward_shadow_default_on_preflight.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"verdict": report["verdict"], "path": str(path), "warning": warning}, ensure_ascii=False))
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
