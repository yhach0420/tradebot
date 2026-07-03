"""
Phase552: Production startup smoke test — same path as run_paper_trade.bat → AM runner → pilot.

Exercises production YAML, tradebotfile/ repo_root cwd, make_exposure_gate(), and guard builds
without Kabu connection.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

from small_paper.config import load_pilot_config
from small_paper.entry_cluster_classifier import resolve_entry_cluster_guard_model_path
from small_paper.entry_cluster_guard import (
    config_from_pilot,
    validate_entry_cluster_guard_model,
)

JST = ZoneInfo("Asia/Tokyo")

PHASE552_SMOKE_VERDICT = "phase552_production_startup_smoke_test_and_model_path_fix_done"
DEFAULT_PRODUCTION_CONFIG_REL = (
    "kabu_native/configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
)

_REQUIRED_MODEL_KEYS = frozenset(
    {
        "cluster_features",
        "global_feature_medians",
        "cluster_centroids",
        "reject_clusters",
        "reject_csubs",
    }
)

_SAMPLE_INFERENCE_TRADE: dict[str, Any] = {
    "symbol": "5074.T",
    "board_update_frequency": 0.01,
    "update_count_before_entry": 3,
    "minutes_from_open": 45.0,
    "relative_volume": 2.0,
    "volume_ratio": 2.0,
    "entry_vwap_dev_pct": 0.5,
    "entry_near_day_high_pct": 2.0,
    "entry_momentum_score": 0.2,
    "entry_expectancy_score_v2": 5,
    "entry_order_book_imbalance": 0.55,
}


@dataclass
class SmokeTestReport:
    ready: bool = False
    verdict: str = ""
    config_path: str = ""
    config_rel: str = ""
    repo_root: str = ""
    cwd: str = ""
    model_path: Optional[str] = None
    errors: list[str] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "ready": self.ready,
            "config_path": self.config_path,
            "config_rel": self.config_rel,
            "repo_root": self.repo_root,
            "cwd": self.cwd,
            "model_path": self.model_path,
            "checks": self.checks,
            "errors": self.errors,
        }


def resolve_production_config_path(*, repo_root: Path, config_rel: str) -> Path:
    root = repo_root.resolve()
    rel = config_rel.replace("\\", "/")
    return root / rel


# Earliest sortable key → no prior sessions scanned (fast preflight; same make_exposure_gate API).
SMOKE_TEST_RUN_SESSION_KEY = "00010101/live_full_session_smoke_test"


def production_run_session_key(*, day_stamp: Optional[str] = None) -> str:
    """Same shape as pilot_runner session_key_from_output_dir (day/live_full_session_*)."""
    if day_stamp == "smoke_fast":
        return SMOKE_TEST_RUN_SESSION_KEY
    day = day_stamp or datetime.now(JST).strftime("%Y%m%d")
    return f"{day}/live_full_session_smoke_test"


def run_production_startup_smoke_test(
    *,
    repo_root: Path,
    config_rel: str = DEFAULT_PRODUCTION_CONFIG_REL,
    run_session_key: Optional[str] = None,
) -> SmokeTestReport:
    """Mirror run_small_paper_pilot → run_live_dry_run gate initialization."""
    root = repo_root.resolve()
    session_key = run_session_key or production_run_session_key(day_stamp="smoke_fast")
    report = SmokeTestReport(
        repo_root=str(root),
        cwd=str(Path.cwd().resolve()),
        config_rel=config_rel.replace("\\", "/"),
    )
    errors: list[str] = []
    checks: dict[str, bool] = {}

    cfg_path = resolve_production_config_path(repo_root=root, config_rel=config_rel)
    report.config_path = str(cfg_path)
    if not cfg_path.is_file():
        errors.append(f"production YAML missing: {cfg_path}")
        report.errors = errors
        report.verdict = "production_startup_smoke_test_failed"
        return report

    try:
        config = load_pilot_config(cfg_path)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"production YAML load failed: {exc}")
        report.errors = errors
        report.verdict = "production_startup_smoke_test_failed"
        return report
    checks["production_yaml_load"] = True

    if getattr(config, "entry_cluster_guard_enabled", False):
        guard_cfg = config_from_pilot(config, repo_root=root)
        try:
            model_path = resolve_entry_cluster_guard_model_path(
                repo_root=root,
                yaml_path=guard_cfg.model_path,
            )
            report.model_path = str(model_path)
        except FileNotFoundError as exc:
            errors.append(str(exc))
        else:
            checks["model_path_resolved"] = True
            try:
                raw = json.loads(model_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                errors.append(f"model JSON parse error ({model_path}): {exc}")
            else:
                missing = sorted(_REQUIRED_MODEL_KEYS - set(raw.keys()))
                if missing:
                    errors.append(f"model missing required keys: {missing}")
                else:
                    checks["model_json_valid"] = True

        cg_state, cg_errors = validate_entry_cluster_guard_model(config, repo_root=root)
        errors.extend(cg_errors)
        if cg_state is not None and not cg_errors:
            checks["entry_cluster_guard_build"] = True
            try:
                cls = cg_state.model.classify(_SAMPLE_INFERENCE_TRADE)
                if "cluster_id" not in cls:
                    errors.append("classifier inference missing cluster_id")
                else:
                    checks["classifier_inference"] = True
            except Exception as exc:  # noqa: BLE001
                errors.append(f"classifier inference failed: {exc}")
            summary = cg_state.summary_fields()
            missing_summary = [
                key
                for key in (
                    "cluster_guard_reject_count",
                    "cluster_guard_exception_count",
                    "cluster_guard_exception_pnl",
                )
                if key not in summary
            ]
            if missing_summary:
                errors.append(f"cluster guard summary missing: {missing_summary}")
            else:
                checks["cluster_guard_summary"] = True

    try:
        gate = config.make_exposure_gate(repo_root=root, run_session_key=session_key)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"make_exposure_gate failed: {exc}")
        gate = None
    else:
        checks["make_exposure_gate"] = True

    if gate is not None:
        if getattr(config, "entry_cluster_guard_enabled", False):
            if getattr(gate, "entry_cluster_guard", None) is None:
                errors.append("ExposureGate.entry_cluster_guard is None")
            else:
                checks["gate_entry_cluster_guard"] = True
        if getattr(config, "reentry_rsi_guard_enabled", False):
            if getattr(gate, "reentry_rsi_guard", None) is None:
                errors.append("ExposureGate.reentry_rsi_guard is None")
            else:
                checks["reentry_rsi_guard"] = True
        if getattr(config, "entry_quality_guard_enabled", False):
            if getattr(gate, "entry_quality_guard", None) is None:
                errors.append("ExposureGate.entry_quality_guard is None")
            else:
                checks["entry_quality_guard"] = True
        if getattr(config, "stop_low_mfe_guard_enabled", False):
            if getattr(gate, "stop_low_mfe_guard", None) is None:
                errors.append("ExposureGate.stop_low_mfe_guard is None")
            else:
                checks["stop_low_mfe_guard"] = True
                slm = gate.stop_low_mfe_guard
                summary = slm.summary_fields()
                missing_summary = [
                    key
                    for key in (
                        "stop_low_mfe_guard_reject_count",
                        "stop_low_mfe_guard_missing_count",
                        "stop_low_mfe_guard_net_shadow",
                    )
                    if key not in summary
                ]
                if missing_summary:
                    errors.append(f"stop_low_mfe guard summary missing: {missing_summary}")
                else:
                    checks["stop_low_mfe_guard_summary"] = True

        if getattr(config, "exit_shadow_monitor_enabled", False):
            from small_paper.exit_shadow_monitor import SUMMARY_FIELD_KEYS
            from small_paper.exit_shadow_monitor import (
                config_from_pilot as exit_shadow_config_from_pilot,
            )
            from small_paper.exit_shadow_monitor import finalize_session_exit_shadow_monitor_safe

            sample = finalize_session_exit_shadow_monitor_safe(
                [], monitor=exit_shadow_config_from_pilot(config)
            )
            missing = [k for k in SUMMARY_FIELD_KEYS if k not in sample]
            if missing:
                errors.append(f"exit_shadow_monitor summary missing: {missing}")
            else:
                checks["exit_shadow_monitor_summary"] = True

    if getattr(config, "or_overlay_enabled", False):
        try:
            from small_paper.or_overlay_entry import build_or_overlay_state

            or_state = build_or_overlay_state(config)
            if or_state is None:
                errors.append("build_or_overlay_state returned None while or_overlay_enabled")
            else:
                checks["or_overlay_build"] = True
        except Exception as exc:  # noqa: BLE001
            errors.append(f"or overlay build failed: {exc}")

    from small_paper.phase627_preflight import phase627_preflight_checks

    p627_errors = phase627_preflight_checks(config, repo_root=root)
    if p627_errors:
        errors.extend(p627_errors)
    else:
        checks["phase627_cluster_guard_safety"] = True

    try:
        from small_paper.discord_message_builder import build_entry_detail

        detail = build_entry_detail(
            symbol="5074.T",
            entry_price=1000.0,
            stop_price=950.0,
            slot_usage="1/5",
            entry_score_v2=5,
            data={
                "entry_type": "PBV2",
                "cluster_guard_status": "PASSED",
                "profile": config.profile,
            },
        )
        if not detail:
            errors.append("discord build_entry_detail returned empty")
        else:
            checks["discord_formatter"] = True
    except Exception as exc:  # noqa: BLE001
        errors.append(f"discord formatter failed: {exc}")

    report.checks = checks
    report.errors = errors
    report.ready = len(errors) == 0
    report.verdict = PHASE552_SMOKE_VERDICT if report.ready else "production_startup_smoke_test_failed"
    return report
