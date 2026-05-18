"""
Phase 44: Small paper pilot configuration loader.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

from research.entry_v2 import MOMENTUM_V13_COMBINED_REFERENCE, MOMENTUM_V2_REFERENCE
from research.exposure_gate import ExposureGateConfig
from small_paper.allowed_trading_windows import (
    TradingWindow,
    parse_allowed_trading_windows,
    windows_summary,
)

FROZEN_EXIT_PROFILE = MOMENTUM_V13_COMBINED_REFERENCE
FROZEN_ENTRY_PROFILE = MOMENTUM_V2_REFERENCE


@dataclass
class SmallPaperPilotConfig:
    profile: str = FROZEN_EXIT_PROFILE
    entry_profile: str = FROZEN_ENTRY_PROFILE
    min_continuation_quality: float = 0.55
    max_concurrent_positions: int = 3
    reject_below_quality: bool = True
    order_enabled: bool = False
    paper_only: bool = True
    discord_enabled: bool = False
    discord_observer_only: bool = True
    discord_send_rejects: bool = False
    discord_heartbeat_min: float = 30.0
    discord_webhook_env: str = "KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL"
    discord_cooldown_sec: float = 60.0
    discord_hard_stop_pct: float = 1.20
    discord_hold_min: float = 15.0
    discord_hold_quality_delta: float = 0.03
    discord_take_quality_drop: float = 0.08
    require_phase43_pass: bool = True
    daily_loss_guard_pct: float = -2.5
    risk_cluster_consecutive_losses: int = 5
    risk_cluster_block_enabled: bool = True
    daily_loss_guard_enabled: bool = True
    dry_run_required: bool = True
    default_source: str = "replay"
    max_polls: int = 1
    poll_interval_sec: float = 30.0
    output_base_dir: str = "kabu_native/results/small_paper"
    phase43_diagnosis_glob: str = "kabu_native/results/reports/small_paper_gate_diagnosis_*.json"
    reference_trades_csv: str = ""
    live_duration_sec: float = 1800.0
    live_poll_interval_sec: float = 5.0
    live_record_push_jsonl: bool = True
    live_session_start: str = "09:00"
    live_session_end: str = "15:30"
    live_heartbeat_sec: float = 300.0
    live_stale_tick_sec: float = 120.0
    live_max_consecutive_api_errors: int = 10
    policy_label: str = "q055_cap3"
    policy_trial: bool = False
    baseline_policy: str = ""
    comparison_note: str = ""
    allowed_trading_windows: list[TradingWindow] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def policy_summary_fields(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "policy_label": self.policy_label,
            "min_continuation_quality": self.min_continuation_quality,
            "max_concurrent_positions": self.max_concurrent_positions,
            "policy_trial": self.policy_trial,
        }
        if self.baseline_policy:
            out["baseline_policy"] = self.baseline_policy
        if self.comparison_note:
            out["comparison_note"] = self.comparison_note.strip()
        if self.allowed_trading_windows:
            out["allowed_trading_windows"] = windows_summary(self.allowed_trading_windows)
        return out

    def exposure_gate_config(self) -> ExposureGateConfig:
        return ExposureGateConfig(
            profile=self.profile,
            min_continuation_quality=self.min_continuation_quality,
            max_concurrent_positions=self.max_concurrent_positions,
            reject_below_quality=self.reject_below_quality,
            order_enabled=False,
            discord_enabled=self.discord_enabled,
            daily_loss_guard_pct=self.daily_loss_guard_pct,
            risk_cluster_consecutive_losses=self.risk_cluster_consecutive_losses,
        )

    def allowed_windows(self) -> list[TradingWindow]:
        return list(self.allowed_trading_windows)

    def make_exposure_gate(self) -> ExposureGate:
        from research.exposure_gate import ExposureGate

        return ExposureGate(
            self.exposure_gate_config(),
            allowed_windows=self.allowed_windows(),
        )


def load_pilot_config(path: Path) -> SmallPaperPilotConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out = raw.get("output") or {}
    ref = raw.get("reference") or {}
    return SmallPaperPilotConfig(
        profile=str(raw.get("profile", FROZEN_EXIT_PROFILE)),
        entry_profile=str(raw.get("entry_profile", FROZEN_ENTRY_PROFILE)),
        min_continuation_quality=float(raw.get("min_continuation_quality", 0.55)),
        max_concurrent_positions=int(raw.get("max_concurrent_positions", 3)),
        reject_below_quality=bool(raw.get("reject_below_quality", True)),
        order_enabled=bool(raw.get("order_enabled", False)),
        paper_only=bool(raw.get("paper_only", True)),
        discord_enabled=bool(raw.get("discord_enabled", False)),
        discord_observer_only=bool(raw.get("discord_observer_only", True)),
        discord_send_rejects=bool(raw.get("discord_send_rejects", False)),
        discord_heartbeat_min=float(raw.get("discord_heartbeat_min", 30.0)),
        discord_webhook_env=str(raw.get("discord_webhook_env", "KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL")),
        discord_cooldown_sec=float(raw.get("discord_cooldown_sec", 60.0)),
        discord_hard_stop_pct=float(raw.get("discord_hard_stop_pct", 1.20)),
        discord_hold_min=float(raw.get("discord_hold_min", 15.0)),
        discord_hold_quality_delta=float(raw.get("discord_hold_quality_delta", 0.03)),
        discord_take_quality_drop=float(raw.get("discord_take_quality_drop", 0.08)),
        require_phase43_pass=bool(raw.get("require_phase43_pass", True)),
        daily_loss_guard_pct=float(raw.get("daily_loss_guard_pct", -2.5)),
        risk_cluster_consecutive_losses=int(raw.get("risk_cluster_consecutive_losses", 5)),
        risk_cluster_block_enabled=bool(raw.get("risk_cluster_block_enabled", True)),
        daily_loss_guard_enabled=bool(raw.get("daily_loss_guard_enabled", True)),
        dry_run_required=bool(raw.get("dry_run_required", True)),
        default_source=str(raw.get("default_source", "replay")),
        max_polls=int(raw.get("max_polls", 1)),
        poll_interval_sec=float(raw.get("poll_interval_sec", 30.0)),
        output_base_dir=str(out.get("base_dir", "kabu_native/results/small_paper")),
        phase43_diagnosis_glob=str(
            raw.get("phase43_diagnosis_glob", "kabu_native/results/reports/small_paper_gate_diagnosis_*.json")
        ),
        reference_trades_csv=str(ref.get("trades_csv", "")),
        live_duration_sec=float((raw.get("live") or {}).get("duration_sec", 1800)),
        live_poll_interval_sec=float((raw.get("live") or {}).get("poll_interval_sec", 5.0)),
        live_record_push_jsonl=bool((raw.get("live") or {}).get("record_push_jsonl", True)),
        live_session_start=str((raw.get("live") or {}).get("session_start", "09:00")),
        live_session_end=str((raw.get("live") or {}).get("session_end", "15:30")),
        live_heartbeat_sec=float((raw.get("live") or {}).get("heartbeat_sec", 300)),
        live_stale_tick_sec=float((raw.get("live") or {}).get("stale_tick_sec", 120)),
        live_max_consecutive_api_errors=int(
            (raw.get("live") or {}).get("max_consecutive_api_errors", 10)
        ),
        policy_label=str(raw.get("policy_label", "q055_cap3")),
        policy_trial=bool(raw.get("policy_trial", False)),
        baseline_policy=str(raw.get("baseline_policy", "")),
        comparison_note=str(raw.get("comparison_note", "")),
        allowed_trading_windows=parse_allowed_trading_windows(
            raw.get("allowed_trading_windows")
        ),
        raw=raw,
    )


def config_file_sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve_output_dir(config: SmallPaperPilotConfig, *, repo_root: Path, day_key: str) -> Path:
    base = Path(config.output_base_dir)
    if not base.is_absolute():
        base = repo_root / base
    return base / day_key


def resolve_live_session_dir(
    config: SmallPaperPilotConfig,
    *,
    repo_root: Path,
    day_key: str,
    session_stamp: str,
) -> Path:
    return resolve_output_dir(config, repo_root=repo_root, day_key=day_key) / f"live_session_{session_stamp}"


def resolve_live_full_session_dir(
    config: SmallPaperPilotConfig,
    *,
    repo_root: Path,
    day_key: str,
    session_stamp: str,
) -> Path:
    return (
        resolve_output_dir(config, repo_root=repo_root, day_key=day_key)
        / f"live_full_session_{session_stamp}"
    )


def resolve_push_replay_dir(
    config: SmallPaperPilotConfig,
    *,
    repo_root: Path,
    day_key: str,
    session_stamp: str,
) -> Path:
    return (
        resolve_output_dir(config, repo_root=repo_root, day_key=day_key)
        / f"push_replay_{session_stamp}"
    )
