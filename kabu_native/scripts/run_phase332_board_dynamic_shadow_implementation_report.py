#!/usr/bin/env python3
"""
Phase332: Board-dynamic trailing shadow implementation verification.

Output: kabu_native/results/reports/phase332_board_dynamic_shadow_implementation_report.json
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

OUT = Path("kabu_native/results/reports/phase332_board_dynamic_shadow_implementation_report.json")
JST = ZoneInfo("Asia/Tokyo")

MODULE_PATHS = (
    "kabu_native/src/small_paper/board_dynamic_trailing_shadow.py",
    "kabu_native/src/small_paper/observer_position_tracker.py",
    "kabu_native/src/small_paper/pilot_runner.py",
)

TEST_MODULE = "kabu_native/tests/test_phase332_board_dynamic_trailing_shadow.py"


def _run(cmd: list[str], *, cwd: Path, pythonpath: Path | None = None) -> dict[str, Any]:
    env = os.environ.copy()
    if pythonpath is not None:
        prev = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(pythonpath) + (os.pathsep + prev if prev else "")
    p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, env=env)
    return {
        "command": " ".join(cmd),
        "exit_code": p.returncode,
        "ok": p.returncode == 0,
        "stderr": p.stderr[-4000:],
        "stdout": p.stdout[-4000:],
    }


def _bootstrap() -> Path:
    script = Path(__file__).resolve()
    repo = script.parents[2]
    native = script.parents[1]
    for p in (native / "src", repo):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    return repo


def _py_compile_checks(repo: Path) -> dict[str, Any]:
    results = []
    all_ok = True
    for rel in MODULE_PATHS:
        path = repo / rel
        r = _run([sys.executable, "-m", "py_compile", str(path)], cwd=repo)
        results.append({"path": rel, **r})
        all_ok = all_ok and r["ok"]
    return {"ok": all_ok, "modules": results}


def _unit_test(repo: Path, native_src: Path) -> dict[str, Any]:
    r = _run(
        [sys.executable, "-m", "unittest", TEST_MODULE, "-v"],
        cwd=repo,
        pythonpath=native_src,
    )
    return r


def _synthetic_shadow_demo() -> dict[str, Any]:
    from small_paper.board_dynamic_trailing_shadow import (
        BOARD_SPLIT_PERCENTILE,
        SHADOW_FIELD_KEYS,
        enrich_exit_board_dynamic_shadow_fields,
        trailing_params_for_board_tier,
    )

    ticks_high = [
        {"ts_epoch": 1000.0, "price": 1000.0, "pnl_pct": 0.0},
        {"ts_epoch": 1010.0, "price": 1012.0, "pnl_pct": 1.2},
        {"ts_epoch": 1020.0, "price": 1006.0, "pnl_pct": 0.6},
    ]
    ticks_low = [
        {"ts_epoch": 1000.0, "price": 1000.0, "pnl_pct": 0.0},
        {"ts_epoch": 1010.0, "price": 1007.0, "pnl_pct": 0.7},
        {"ts_epoch": 1020.0, "price": 1002.0, "pnl_pct": 0.2},
    ]
    high = enrich_exit_board_dynamic_shadow_fields(
        {"entry_imbalance_percentile": 60.0},
        rich_ticks=ticks_high,
        entry_price=1000.0,
        entry_ts=1000.0,
        hard_stop_pct=1.2,
        actual_exit_time=1030.0,
        actual_exit_price=1004.0,
        actual_pnl_pct=0.4,
    )
    low = enrich_exit_board_dynamic_shadow_fields(
        {"entry_imbalance_percentile": 30.0},
        rich_ticks=ticks_low,
        entry_price=1000.0,
        entry_ts=1000.0,
        hard_stop_pct=1.2,
        actual_exit_time=1030.0,
        actual_exit_price=1005.0,
        actual_pnl_pct=0.5,
    )
    return {
        "board_split_percentile": BOARD_SPLIT_PERCENTILE,
        "board_high_params": trailing_params_for_board_tier(60.0),
        "board_low_params": trailing_params_for_board_tier(30.0),
        "sample_board_high": {k: high.get(k) for k in SHADOW_FIELD_KEYS},
        "sample_board_low": {k: low.get(k) for k in SHADOW_FIELD_KEYS},
    }


def _source_integrity_checks(repo: Path) -> dict[str, Any]:
    obs = (repo / "kabu_native/src/small_paper/observer_position_tracker.py").read_text(
        encoding="utf-8"
    )
    pilot = (repo / "kabu_native/src/small_paper/pilot_runner.py").read_text(encoding="utf-8")
    policies = (repo / "kabu_native/src/research/structural_exit_policies.py").read_text(
        encoding="utf-8"
    )
    discord = (repo / "kabu_native/src/small_paper/discord_message_builder.py").read_text(
        encoding="utf-8"
    )
    return {
        "observer_has_shadow_enrich": "enrich_exit_board_dynamic_shadow_fields" in obs,
        "pilot_has_shadow_fields": all(
            f'"{k}"' in pilot
            for k in (
                "shadow_exit_reason",
                "shadow_pnl_yen_100",
                "actual_vs_shadow_delta_yen",
            )
        ),
        "actual_exit_unchanged_trailing_mfe": (
            "TRAILING_MFE_ACTIVATE_PCT = 0.80" in policies
            and "TRAILING_MFE_GIVEBACK_FRAC = 0.50" in policies
        ),
        "discord_unchanged": "shadow_exit_reason" not in discord,
        "production_exit_not_modified": "board_dynamic" not in policies,
    }


def main() -> int:
    repo = _bootstrap()
    native_src = repo / "kabu_native" / "src"

    py_compile = _py_compile_checks(repo)
    unit_test = _unit_test(repo, native_src)
    synthetic = _synthetic_shadow_demo()
    integrity = _source_integrity_checks(repo)

    all_ok = (
        py_compile["ok"]
        and unit_test["ok"]
        and integrity["observer_has_shadow_enrich"]
        and integrity["pilot_has_shadow_fields"]
        and integrity["actual_exit_unchanged_trailing_mfe"]
        and integrity["discord_unchanged"]
        and integrity["production_exit_not_modified"]
        and synthetic["sample_board_high"]["shadow_exit_reason"] == "trailing_mfe_exit"
        and synthetic["sample_board_low"]["shadow_exit_reason"] == "trailing_mfe_exit"
    )

    report = {
        "phase": 332,
        "title": "board_dynamic_shadow_implementation_report",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "constraint": "shadow only; actual EXIT unchanged",
        "shadow_policy": {
            "board_high": {
                "entry_imbalance_percentile_gte": 47.62,
                "activate_pct": 1.0,
                "giveback_frac": 0.6,
            },
            "board_low": {
                "entry_imbalance_percentile_lt": 47.62,
                "activate_pct": 0.6,
                "giveback_frac": 0.4,
            },
        },
        "actual_exit_preserved": {
            "hard_stop_pct": 1.2,
            "trailing_mfe_activate_pct": 0.8,
            "trailing_mfe_giveback_frac": 0.5,
        },
        "verification": {
            "py_compile": py_compile,
            "unit_test": unit_test,
            "synthetic_shadow_demo": synthetic,
            "source_integrity": integrity,
        },
        "verdict": {
            "implementation_ok": all_ok,
            "shadow_logging_ready": all_ok,
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT}")
    print(f"implementation_ok={all_ok}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
