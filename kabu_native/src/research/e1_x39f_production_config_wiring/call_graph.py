"""Production call graph from current repo SoT (not prior docs)."""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any

NATIVE = Path(__file__).resolve().parents[3]
REPO = NATIVE.parent


def build_call_graph() -> dict[str, Any]:
    bat_checked = REPO / "run_paper_trade_checked.bat"
    ps1 = NATIVE / "scripts" / "run_paper_trade_checked.ps1"
    runner_mod = "small_paper.paper_trade_checked_runner"
    bat_paper = REPO / "run_paper_trade.bat"
    daily_script = NATIVE / "scripts" / "run_core10_dynamic40_am_pm_daily_runner.py"
    daily_mod = NATIVE / "src" / "runner" / "am_pm_daily_runner.py"
    pilot = NATIVE / "scripts" / "run_small_paper_pilot.py"
    yaml_path = (
        NATIVE / "configs"
        / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
    )
    pin = NATIVE / "configs" / "production_config_sha256.pin"

    # Read constants from live modules
    from small_paper.paper_trade_checked_runner import DEFAULT_CONFIG, DEFAULT_PAPER_BAT, NATIVE_ROOT, REPO_ROOT
    from runner.am_pm_daily_runner import TRAILING_MFE_SHADOW_YAML

    stages = [
        {
            "stage": "run_paper_trade_checked.bat",
            "path": str(bat_checked.resolve()),
            "exists": bat_checked.exists(),
            "config_path": None,
            "cli_args": ["--no-pause", "--demo-push-e2e", "--comm-fault-e2e", "--reuse-capture"],
            "env_overrides": ["TRADEBOT_DEMO_PUSH_E2E", "TRADEBOT_COMM_FAULT_E2E"],
            "cwd": str(REPO),
            "PYTHONPATH": None,
            "subprocess_env": "inherits OS; launches powershell -File ps1",
            "defaults": {},
        },
        {
            "stage": "run_paper_trade_checked.ps1",
            "path": str(ps1.resolve()),
            "exists": ps1.exists(),
            "config_path": None,
            "cli_args": ["-m small_paper.paper_trade_checked_runner", "--repo-root", "--native-root", "--paper-bat"],
            "env_overrides": ["PYTHONPATH", "PYTHONIOENCODING", "MARKET_INGRESS_V2"],
            "cwd": str(NATIVE),
            "PYTHONPATH": f"{NATIVE / 'src'};{REPO}",
            "subprocess_env": "PowerShell $env: inherited by python child",
            "defaults": {"MARKET_INGRESS_V2": "1 if unset"},
        },
        {
            "stage": "small_paper.paper_trade_checked_runner",
            "path": str((NATIVE / "src/small_paper/paper_trade_checked_runner.py").resolve()),
            "exists": True,
            "config_path": str(Path(DEFAULT_CONFIG).resolve()),
            "cli_args": ["--repo-root", "--native-root", "--paper-bat", "--skip-paper", "--demo-push-e2e"],
            "env_overrides": ["ensure_repo_dotenv → os.environ.copy + PYTHONPATH + PYTHONIOENCODING"],
            "cwd": "native for module; paper bat cwd=repo_root",
            "PYTHONPATH": "default_pythonpath() = native/src;repo",
            "subprocess_env": "_env() full copy after dotenv; Capture/Ingress inherit",
            "defaults": {
                "DEFAULT_CONFIG": str(DEFAULT_CONFIG),
                "DEFAULT_PAPER_BAT": str(DEFAULT_PAPER_BAT),
                "REPO_ROOT": str(REPO_ROOT),
                "NATIVE_ROOT": str(NATIVE_ROOT),
            },
        },
        {
            "stage": "run_paper_trade.bat",
            "path": str(bat_paper.resolve()),
            "exists": bat_paper.exists(),
            "config_path": None,
            "cli_args": [],
            "env_overrides": [
                "KABU_PAPER_RUNTIME=1", "MARKET_INGRESS_V2=1",
                "COST_AWARE_ENTRY_SHADOW=0", "COST_AWARE_ENTRY_V2_SHADOW=0",
                "PULLBACK_VOLUME_FORWARD=1",
            ],
            "cwd": str(REPO),
            "PYTHONPATH": "kabu_native\\src",
            "subprocess_env": "bat set if unset; child python inherits",
            "defaults": {"hardcoded_REPO": "C:\\Users\\yhach\\Documents\\tradebotfile"},
        },
        {
            "stage": "run_core10_dynamic40_am_pm_daily_runner.py",
            "path": str(daily_script.resolve()),
            "exists": daily_script.exists(),
            "config_path": str(Path(TRAILING_MFE_SHADOW_YAML).resolve()) if Path(str(TRAILING_MFE_SHADOW_YAML)).exists() else str(TRAILING_MFE_SHADOW_YAML),
            "cli_args": [
                "--universe-mode core10-dynamic40-price-risk-filter-shadow",
                "--enable-intraday-refresh",
                "--exit-policy-shadow trailing-mfe",
            ],
            "env_overrides": ["ensure_paper_forward_observer_env"],
            "cwd": str(REPO),
            "PYTHONPATH": "inherited from bat",
            "subprocess_env": "inherits parent (no env= override on pilot spawn)",
            "defaults": {"TRAILING_MFE_SHADOW_YAML": str(TRAILING_MFE_SHADOW_YAML)},
        },
        {
            "stage": "am_pm_daily_runner → run_small_paper_pilot.py",
            "path": str(pilot.resolve()),
            "exists": pilot.exists(),
            "config_path": str(yaml_path.resolve()),
            "cli_args": [
                "--dry-run", "--source live", "--config <YAML>",
                "--am-pm-session am|pm", "--wait-until-session",
            ],
            "env_overrides": [],
            "cwd": str(REPO),
            "PYTHONPATH": "inherited",
            "subprocess_env": "subprocess.run without env= → full inherit",
            "defaults": {},
            "config_reload": "load_pilot_config once per pilot process",
        },
        {
            "stage": "v1r_primary_runtime.resolve (isolation)",
            "path": str((NATIVE / "src/small_paper/v1r_primary_runtime.py").resolve()),
            "exists": True,
            "config_path": str(yaml_path.resolve()),
            "cli_args": ["audit / dry-load only — no live observer"],
            "env_overrides": [],
            "cwd": "n/a",
            "PYTHONPATH": "same",
            "subprocess_env": "n/a",
            "defaults": {
                "V1R_SoT": "activation+freeze binds",
                "YAML": "PBV2 shadow + infra only",
            },
        },
    ]

    return {
        "entrypoint": str(bat_checked.resolve()),
        "production_yaml": str(yaml_path.resolve()),
        "production_pin": str(pin.resolve()),
        "stages": stages,
        "yaml_exists": yaml_path.exists(),
        "pin_exists": pin.exists(),
    }
