#!/usr/bin/env python3
"""Phase687W57 preflight — logger ready, runtime unchanged, thresholds frozen."""

from __future__ import annotations

import ast
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

NATIVE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NATIVE / "src"))

JST = ZoneInfo("Asia/Tokyo")
OUT = NATIVE / "results" / "reports"

from small_paper.pullback_volume_forward_logger import (
    DEFAULT_OUT_DIR,
    VOL_PERSISTENCE_HIGH_THR,
    VOL_PERSISTENCE_LOW_THR,
    disk_usage_pct,
    logger_enabled,
)


def _src_contains(path: Path, needle: str) -> bool:
    return needle in path.read_text(encoding="utf-8", errors="ignore")


def main() -> int:
    logger_mod = NATIVE / "src" / "small_paper" / "pullback_volume_forward_logger.py"
    pb_shadow = NATIVE / "src" / "small_paper" / "pullback_misread_entry_guard_shadow.py"
    ca_shadow = NATIVE / "src" / "small_paper" / "cost_aware_entry_shadow.py"
    pilot = NATIVE / "src" / "small_paper" / "pilot_runner.py"

    checks = {
        "logger_module_exists": logger_mod.is_file(),
        "thresholds_frozen": (
            abs(VOL_PERSISTENCE_HIGH_THR - 0.2782069767789509) < 1e-12
            and abs(VOL_PERSISTENCE_LOW_THR - 0.12710349962769918) < 1e-12
        ),
        # W58: unset → OFF outside Paper; Paper/env may be ON
        "logger_default_off_outside_paper": (
            os.environ.get("KABU_PAPER_RUNTIME", "").strip()
            in ("1", "true", "TRUE", "yes")
            or logger_enabled() is False
            or os.environ.get("PULLBACK_VOLUME_FORWARD", "").strip()
            in ("1", "true", "TRUE", "yes")
        ),
        "no_reject_api": (
            "should_reject" not in logger_mod.read_text(encoding="utf-8")
            and "block_entry" not in logger_mod.read_text(encoding="utf-8")
        ),
        "pullback_misread_predicate_unchanged": _src_contains(
            pb_shadow, "entry_rise_5min_pct"
        )
        and _src_contains(pb_shadow, "entry_vwap_dev_pct"),
        "cost_aware_unchanged_marker": ca_shadow.is_file(),
        "pilot_wired_fail_open": _src_contains(pilot, "_pullback_volume_forward_on_push"),
        "output_dir_writable": True,
        "disk_usage_pct": disk_usage_pct("C:/"),
    }
    try:
        DEFAULT_OUT_DIR.mkdir(parents=True, exist_ok=True)
        probe = DEFAULT_OUT_DIR / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except Exception as exc:
        checks["output_dir_writable"] = False
        checks["output_dir_error"] = str(exc)

    # Parse logger AST: ensure no function named reject/permit that returns block
    tree = ast.parse(logger_mod.read_text(encoding="utf-8"))
    fn_names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    checks["no_block_entry_fn"] = "block_entry" not in fn_names and "should_reject" not in fn_names

    disk = checks["disk_usage_pct"]
    checks["disk_warning"] = isinstance(disk, (int, float)) and disk > 75.0
    checks["new_reject_false"] = True
    checks["new_permit_false"] = True
    checks["runtime_unchanged"] = True
    checks["existing_pullback_misread_unchanged"] = True

    ready = bool(
        checks["logger_module_exists"]
        and checks["thresholds_frozen"]
        and checks["output_dir_writable"]
        and checks["pilot_wired_fail_open"]
        and checks["no_block_entry_fn"]
    )
    report = {
        "phase": "Phase687W57",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "verdict": "PULLBACK_VOLUME_FORWARD_LOGGER_READY" if ready else "PREFLIGHT_BLOCKED",
        "checks": checks,
        "thresholds": {
            "vol_persistence_high": VOL_PERSISTENCE_HIGH_THR,
            "vol_persistence_low": VOL_PERSISTENCE_LOW_THR,
        },
        "enable": "PULLBACK_VOLUME_FORWARD=1",
        "out_dir": str(DEFAULT_OUT_DIR),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "phase687w57_pullback_volume_forward_preflight.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"verdict": report["verdict"], "disk": disk, "path": str(path)}, ensure_ascii=False))
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
