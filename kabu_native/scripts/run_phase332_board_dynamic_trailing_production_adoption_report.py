#!/usr/bin/env python3
"""
Phase332: Board-dynamic trailing production adoption verification.

Output: phase332_board_dynamic_trailing_production_adoption_report.json
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

OUT = Path(
    "kabu_native/results/reports/phase332_board_dynamic_trailing_production_adoption_report.json"
)
CONFIG = Path(
    "kabu_native/configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
)
JST = ZoneInfo("Asia/Tokyo")

MODULE_PATHS = (
    "kabu_native/src/research/structural_exit_policies.py",
    "kabu_native/src/small_paper/board_dynamic_trailing_shadow.py",
    "kabu_native/src/small_paper/observer_position_tracker.py",
    "kabu_native/src/small_paper/discord_message_builder.py",
    "kabu_native/src/small_paper/pilot_runner.py",
)

TEST_MODULES = (
    "kabu_native/tests/test_phase332_board_dynamic_trailing_shadow.py",
    "kabu_native/tests/test_phase332_board_dynamic_trailing_production.py",
)


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
        "stderr": p.stderr[-5000:],
        "stdout": p.stdout[-3000:],
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


def _unit_tests(repo: Path, native_src: Path) -> dict[str, Any]:
    results = []
    all_ok = True
    for mod in TEST_MODULES:
        r = _run([sys.executable, "-m", "unittest", mod, "-v"], cwd=repo, pythonpath=native_src)
        results.append({"module": mod, **r})
        all_ok = all_ok and r["ok"]
    return {"ok": all_ok, "modules": results}


def _config_reflection(repo: Path) -> dict[str, Any]:
    text = (repo / CONFIG).read_text(encoding="utf-8")
    return {
        "config_path": str(CONFIG),
        "structural_exit_policy": "combined_structural_exit_v1_trailing_mfe_shadow"
        in text,
        "phase332_note": "Phase332" in text or "board-dynamic" in text or "board_high" in text,
        "board_high_params": "activate 1.0%" in text or "activate 1.0" in text,
        "board_low_params": "activate 0.6%" in text or "activate 0.6" in text,
        "legacy_shadow_note": "legacy fixed" in text or "0.8%/50%" in text,
    }


def _production_logic_checks(repo: Path) -> dict[str, Any]:
    from research.structural_exit_policies import (
        trailing_mfe_exit_triggered,
        trailing_mfe_params,
    )

    policies = (repo / "kabu_native/src/research/structural_exit_policies.py").read_text(
        encoding="utf-8"
    )
    observer = (repo / "kabu_native/src/small_paper/observer_position_tracker.py").read_text(
        encoding="utf-8"
    )
    discord = (repo / "kabu_native/src/small_paper/discord_message_builder.py").read_text(
        encoding="utf-8"
    )
    pilot = (repo / "kabu_native/src/small_paper/pilot_runner.py").read_text(encoding="utf-8")

    high_act, high_gb, high_tier = trailing_mfe_params(60.0)
    low_act, low_gb, low_tier = trailing_mfe_params(30.0)

    return {
        "structural_exit_uses_board_dynamic": "trailing_mfe_exit_triggered" in policies,
        "observer_passes_imbalance_percentile": "entry_imbalance_percentile=imb_pct" in observer,
        "observer_logs_board_tier": "board_dynamic_trailing_tier" in observer,
        "discord_shows_board_tier": "board_dynamic_trailing_tier" in discord,
        "pilot_logs_production_fields": '"board_dynamic_trailing_tier"' in pilot,
        "board_high_params": {
            "tier": high_tier,
            "activate_pct": high_act,
            "giveback_frac": high_gb,
        },
        "board_low_params": {
            "tier": low_tier,
            "activate_pct": low_act,
            "giveback_frac": low_gb,
        },
        "board_high_trailing_example": trailing_mfe_exit_triggered(
            peak_pnl=1.1, pnl=0.6, entry_imbalance_percentile=60.0
        ),
        "board_low_trailing_example": trailing_mfe_exit_triggered(
            peak_pnl=0.7, pnl=0.25, entry_imbalance_percentile=30.0
        ),
    }


def main() -> int:
    repo = _bootstrap()
    native_src = repo / "kabu_native" / "src"

    py_compile = _py_compile_checks(repo)
    unit_test = _unit_tests(repo, native_src)
    config = _config_reflection(repo)
    logic = _production_logic_checks(repo)

    all_ok = (
        py_compile["ok"]
        and unit_test["ok"]
        and config["phase332_note"]
        and logic["structural_exit_uses_board_dynamic"]
        and logic["observer_passes_imbalance_percentile"]
        and logic["board_high_trailing_example"]
        and logic["board_low_trailing_example"]
    )

    report = {
        "phase": 332,
        "title": "board_dynamic_trailing_production_adoption_report",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "adoption": {
            "production_exit": "board_dynamic_trailing",
            "paper_trade": True,
            "replay": True,
            "board_split_percentile": 47.62,
            "board_high": {"activate_pct": 1.0, "giveback_frac": 0.6},
            "board_low": {"activate_pct": 0.6, "giveback_frac": 0.4},
            "hard_stop_pct": 1.2,
            "shadow_counterfactual": "legacy_fixed_0.8pct_50pct",
        },
        "verification": {
            "py_compile": py_compile,
            "unit_test": unit_test,
            "config_reflection": config,
            "production_logic": logic,
        },
        "verdict": {
            "production_adoption_ok": all_ok,
            "applies_to": ["production_exit", "paper_trade", "replay"],
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT}")
    print(f"production_adoption_ok={all_ok}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
