"""
Phase 153d: Shadow pipeline helpers for price-risk filtered Core10+Dynamic40 universes.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Mapping

from universe.core10_dynamic40 import TOTAL_SLOTS, validate_universe
from universe.core10_dynamic40_price_risk import (
    build_price_risk_universes,
    universe_am_price_risk_path,
    universe_pm_price_risk_path,
)
from universe.price_risk_filter import UNIVERSE_MODE

SHADOW_PILOT_YAML = "kabu_native/configs/small_paper_pilot_q070_cap3_mfe_fav_vol_liq.yaml"
ENTRY_GUARD_SHADOW_YAML = (
    "kabu_native/configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_shadow.yaml"
)


def shadow_live_commands(
    *,
    am_csv_rel: str,
    pm_csv_rel: str,
    pilot_yaml: str = SHADOW_PILOT_YAML,
    entry_guard_yaml: str = ENTRY_GUARD_SHADOW_YAML,
) -> dict[str, Any]:
    return {
        "universe_mode": UNIVERSE_MODE,
        "session_range_supported": True,
        "am_pm_session_times": {
            "am": {"session_start": "09:03", "session_end": "11:25", "entry_stop": "11:20", "force_close": "11:25"},
            "pm": {"session_start": "12:33", "session_end": "15:23", "entry_stop": "15:18", "force_close": "15:23"},
        },
        "morning_shadow_live": [
            "python kabu_native/scripts/check_small_paper_safety.py",
            (
                "python kabu_native/scripts/run_small_paper_pilot.py --dry-run --source live "
                f"--universe-csv {am_csv_rel} "
                f"--config {entry_guard_yaml} "
                "--am-pm-session am --wait-until-session --poll-interval-sec 5"
            ),
        ],
        "afternoon_shadow_live": [
            "python kabu_native/scripts/check_small_paper_safety.py",
            (
                "python kabu_native/scripts/run_small_paper_pilot.py --dry-run --source live "
                f"--universe-csv {pm_csv_rel} "
                f"--config {entry_guard_yaml} "
                "--am-pm-session pm --wait-until-session --poll-interval-sec 5"
            ),
        ],
        "dual_defense_note": (
            "Use price-risk universe CSV with entry_price_risk_guard_shadow pilot YAML "
            "(153b); universe drops 5856-type names, entry gate rejects any residual."
        ),
        "production_pilot_yaml_unchanged": SHADOW_PILOT_YAML,
        "daily_runner_universe_mode": UNIVERSE_MODE,
    }


def build_shadow_universe_report(
    *,
    repo_root: Path,
    reports_dir: Path,
    day_stamp: str,
    trade_date: date,
    feature_rows: list[dict[str, str]],
    symbol_meta: Mapping[str, Mapping[str, Any]],
    push_day_dir: Path,
    core_symbols: list[str],
) -> dict[str, Any]:
    build = build_price_risk_universes(
        reports_dir=reports_dir,
        day_stamp=day_stamp,
        core_symbols=core_symbols,
        feature_rows=feature_rows,
        symbol_meta=symbol_meta,
        push_day_dir=push_day_dir,
    )
    am_csv = universe_am_price_risk_path(reports_dir, day_stamp)
    pm_csv = universe_pm_price_risk_path(reports_dir, day_stamp)
    am_val = validate_universe(am_csv, expected_session="am") if am_csv.is_file() else {"passed": False}
    pm_val = validate_universe(pm_csv, expected_session="pm") if pm_csv.is_file() else {"passed": False}

    def _rel(p: Path) -> str:
        try:
            return str(p.relative_to(repo_root))
        except ValueError:
            return str(p)

    return {
        "phase": 153,
        "phase_id": "153d",
        "day_stamp": day_stamp,
        "universe_mode": UNIVERSE_MODE,
        "build": build,
        "universe_validation": {"am": am_val, "pm": pm_val},
        "shadow_live_commands": shadow_live_commands(
            am_csv_rel=_rel(am_csv),
            pm_csv_rel=_rel(pm_csv),
        ),
        "outputs": {
            "universe_am_csv": _rel(am_csv),
            "universe_pm_csv": _rel(pm_csv),
        },
        "constraints": [
            "no_production_pilot_yaml_change",
            "no_production_universe_overwrite",
            "no_entry_exit_cap_change",
            "no_daily_runner_wire",
            "shadow_review_only",
        ],
    }
