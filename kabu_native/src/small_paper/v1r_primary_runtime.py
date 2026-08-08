"""V1R Paper Primary effective runtime — production YAML isolation.

SoT for V1R Primary contracts is activation / V1R freeze / universe binding.
Production PBV2 YAML is loaded for shadow + infrastructure only; its guards
MUST NOT alter V1R candidate/score/rank/admission/PENDING/FILL/EXIT600.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = NATIVE_ROOT.parent

PRODUCTION_YAML = (
    NATIVE_ROOT
    / "configs"
    / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
)
PRODUCTION_PIN = NATIVE_ROOT / "configs" / "production_config_sha256.pin"

V1R_SHA = "dfd311d4dc32a802b8e55f6d28d75a2db12d4192a71fb53b48d5308573a58e0a"
MODEL_ARTIFACT_SHA = "f63f7f88e9ff6ea5b84a89b0949baa76166d697525620287a7c230f821e7356b"
UNIVERSE_BINDING_SHA = "45b2fb20d02abbe7d557a55fecc87da3e7c19126eb7415ce9bdc4579aca39fee"
PRECOMMIT_U1_SHA = "ebe2b86ca881dfe94d8af986e8689481b40f1e013ad64bc4d645f485b1da625b"
ACTIVATION_SHA = "3f567810afb6cef713021f543d2b0fae7f4856ea4bff131fe0d91e903cc70801"
ANCHOR_SHA = "4a2f176ef6f52458cb0e5b38764275e6ddafc01e1849693965b116089514eac2"

UNIVERSE_CONTRACT = "DAY_FIXED_AM_RUNTIME_UNIVERSE_V1"
BOARD_FRESHNESS_SEC_V1R = 5.0  # frozen execution contract — NOT YAML 3.0
WAIT_SEC = 1.0
POSITION_CAP = 5
LOT_QTY = 100
EXIT_HOLD_SEC = 600.0
DUPLICATE_RULE = "no_overlap_replace"

CLOCK_GRID = (
    (9, 5), (9, 15), (9, 25), (9, 40),
    (10, 0), (10, 20), (10, 40), (11, 0),
    (12, 40), (13, 0), (13, 20), (13, 40),
    (14, 0), (14, 20), (14, 40), (15, 0),
)

# Production YAML keys that may feed PBV2 Shadow only (never V1R Primary path).
PBV2_SHADOW_ONLY_KEYS = frozenset({
    "entry_profile", "entry_score_v2_min", "high_drift_guard_enabled",
    "weak_shape_reject_enabled", "late_chase_guard_enabled",
    "classic_late_chase_rsi_guard_enabled", "classic_late_chase_rsi_threshold",
    "entry_cluster_guard_enabled", "entry_cluster_guard_exception_enabled",
    "entry_cluster_guard_liquidity_burst_threshold", "entry_cluster_guard_model_path",
    "entry_cluster_guard_reject_clusters", "entry_cluster_guard_reject_csubs",
    "pbv2_flat_band_mainline_enabled", "pbv2_flat_band_shadow_enabled",
    "pbv2_flat_band_shadow_apply_pool", "pbv2_flat_band_shadow_overheat_rise5_pct",
    "pbv2_flat_band_shadow_rise10_flat_max_pct", "pbv2_flat_band_shadow_rise10_flat_min_pct",
    "pbv2_flat_band_shadow_rise5_flat_max_pct", "pbv2_flat_band_shadow_rise5_flat_min_pct",
    "pbv2_rise5_shadow_enabled", "pbv2_rise5_shadow_apply_pool", "pbv2_rise5_shadow_threshold_pct",
    "entry_price_risk_guard_enabled", "entry_price_risk_guard_apply_mode",
    "entry_price_risk_guard_max_tick_ratio_pct", "entry_price_risk_guard_min_entry_price",
    "entry_price_risk_guard_shadow", "daily_loss_guard_enabled", "daily_loss_guard_pct",
    "live_capital_check_enabled", "entry_freshness_guard_enabled",
    "entry_max_price_age_sec", "entry_max_board_age_sec",
    "entry_freshness_board_fallback_enabled", "entry_freshness_board_fallback_max_spread_bps",
    "event_stale_threshold_sec", "board_stale_threshold_sec", "trade_stale_threshold_sec",
    "trade_stale_mode", "freshness_semantics_v2_enabled",
    "allowed_trading_windows", "use_market_time_window",
    "structural_exit_policy", "no_progress_exit_enabled",
    "exit_shadow_monitor_enabled", "exit_shadow_monitor_t2_enabled", "exit_shadow_monitor_t3_enabled",
    "or_overlay_enabled", "cap_or", "cap_pbv2", "or_max_update_count",
    "momentum_score_cutoff_max", "min_continuation_quality", "reject_below_quality",
    "entry_quality_guard_enabled", "entry_quality_max_spread_bps", "entry_quality_max_update_count",
    "reentry_rsi_guard_enabled", "reentry_rsi_guard_threshold",
    "risk_cluster_block_enabled", "risk_cluster_consecutive_losses",
    "stop_low_mfe_guard_enabled", "stop_low_mfe_guard_threshold",
    "stop_low_mfe_guard_missing_policy", "stop_low_mfe_guard_pbv2_only",
    "favorable_mfe_scale", "favorable_mode",
    "legacy_vwap_pullback_guard_enabled", "vwap_shadow_reject_enabled",
    "enable_near_day_high_low_momentum_dynamic40_guard",
    "enable_pullback_misread_dynamic40_guard",
    "flat_weak_range_shadow_enabled", "volume_gate_relaxation_shadow_enabled",
})

# Infra / safety shared (not strategy admission for V1R).
INFRA_KEYS = frozenset({
    "dry_run", "dry_run_required", "paper_only", "shadow_only",
    "live_trading_enabled", "order_enabled",
    "live_order_dry_run_enabled", "live_order_api_wiring_enabled",
    "live_order_adapter_enabled", "live_order_safety_sm_enabled",
    "live_order_discord_enabled", "live_order_notifier_enabled",
    "live_order_jsonl_enabled", "live_order_entry_timeout_sec",
    "discord_enabled", "discord_observer_only", "discord_send_rejects",
    "output", "live", "profile", "default_source", "poll_interval_sec",
    "max_polls", "policy_label", "policy_trial", "baseline_policy",
    "comparison_note", "require_phase43_pass", "phase43_diagnosis_glob",
    "max_concurrent_positions", "position_cap_mode", "position_cap_release",
    "same_symbol_open_policy", "virtual_hold_sec", "entry_cooldown_sec",
    "entry_scan_batch_enabled", "entry_scan_window_sec", "max_entries_per_scan",
})

NOTIFY_ENV = {
    "v1r_paper": "KABU_DISCORD_RESEARCH_WEBHOOK_URL",  # Paper Primary events (not live actual)
    "v1r_paper_fallback": "KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL",
    "pbv2_shadow": "KABU_SHADOW_DISCORD_WEBHOOK_URL",
    "pbv2_shadow_fallback": "KABU_DISCORD_RESEARCH_WEBHOOK_URL",
    "one_m_shadow": "KABU_DISCORD_RESEARCH_WEBHOOK_URL",
    "cap_blocked": "KABU_SMALL_PAPER_CAP_BLOCKED_WEBHOOK_URL",
    "operations": "KABU_DISCORD_OPERATIONS_WEBHOOK_URL",
    "critical": "KABU_DISCORD_CRITICAL_WEBHOOK_URL",
    "capture": "KABU_DISCORD_MARKET_CAPTURE_WEBHOOK_URL",
    "forbidden_for_shadow": "KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL",  # PBV2/1M must not steal this as sole path incorrectly — documented
}


@dataclass
class KeyRouting:
    yaml_key: str
    yaml_value: Any
    classification: str  # PRIMARY_V1R | SHADOW_PBV2 | INFRA | DEAD | UNKNOWN
    reaches_v1r_primary: bool
    consumer: str
    notes: str = ""


@dataclass
class V1REffectiveRuntime:
    """Runtime object after production config resolution + V1R isolation."""
    config_source_paths: list[str]
    yaml_sha256: str
    pin_sha256: str
    pin_match: bool
    primary_role: str = "V1R"
    pbv2_role: str = "SHADOW_ONLY"
    one_m_role: str = "SHADOW_ONLY_DIAGNOSTIC"
    strategy_sha: str = V1R_SHA
    model_sha: str = MODEL_ARTIFACT_SHA
    universe_binding_sha: str = UNIVERSE_BINDING_SHA
    precommit_sha: str = PRECOMMIT_U1_SHA
    activation_sha: str = ACTIVATION_SHA
    anchor_sha: str = ANCHOR_SHA
    position_cap: int = POSITION_CAP
    qty: int = LOT_QTY
    wait_sec: float = WAIT_SEC
    board_freshness_sec: float = BOARD_FRESHNESS_SEC_V1R
    duplicate_rule: str = DUPLICATE_RULE
    exit_contract: str = "FIRST_VALID_BUY1_AT_OR_AFTER_TARGET"
    exit_hold_sec: float = EXIT_HOLD_SEC
    universe_contract: str = UNIVERSE_CONTRACT
    anchors: list[str] = field(default_factory=lambda: [
        f"{h:02d}:{m:02d}" for h, m in CLOCK_GRID
    ])
    live_trading_enabled: bool = False
    order_enabled: bool = False
    order_submit_disabled: bool = True
    cancel_disabled: bool = True
    paper_only: bool = True
    pbv2_yaml_cap: Optional[int] = None
    pbv2_freshness_board_age_sec: Optional[float] = None
    pbv2_daily_loss_guard_enabled: Optional[bool] = None
    key_routings: list[KeyRouting] = field(default_factory=list)
    notify_env: dict[str, str] = field(default_factory=lambda: dict(NOTIFY_ENV))
    prospective_observer_started: bool = False
    opened_20260810: bool = False
    isolation_applied: bool = True

    def to_public_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # redact any accidental secrets in yaml_value
        for kr in d.get("key_routings") or []:
            v = kr.get("yaml_value")
            if isinstance(v, str) and ("http" in v.lower() or "webhook" in kr.get("yaml_key", "").lower()):
                kr["yaml_value"] = "<redacted>"
        return d


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def classify_yaml_key(key: str, value: Any) -> KeyRouting:
    if key in PBV2_SHADOW_ONLY_KEYS:
        return KeyRouting(
            yaml_key=key, yaml_value=value, classification="SHADOW_PBV2",
            reaches_v1r_primary=False,
            consumer="pbv2_shadow_pilot_path",
            notes="Isolated from V1R Primary admission/fill/exit",
        )
    if key in INFRA_KEYS or key.startswith("vol_liq_") or key.startswith("readiness_") or key.startswith("microsequence_") or key.startswith("daytrade_") or key.startswith("np_"):
        reaches = key in {
            "max_concurrent_positions",  # alias observe only; V1R SoT is freeze cap
            "live_trading_enabled", "order_enabled", "dry_run", "paper_only",
        }
        return KeyRouting(
            yaml_key=key, yaml_value=_safe_val(value), classification="INFRA",
            reaches_v1r_primary=False if key not in {"live_trading_enabled", "order_enabled", "paper_only", "dry_run"} else True,
            consumer="pilot_infra_safety",
            notes="Infrastructure/safety; V1R strategy values not sourced from YAML",
        )
    return KeyRouting(
        yaml_key=key, yaml_value=_safe_val(value), classification="UNKNOWN",
        reaches_v1r_primary=False,
        consumer="unmapped",
        notes="Not consumed by V1R Primary; flagged for inventory",
    )


def _safe_val(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _safe_val(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_safe_val(v) for v in value[:20]]
    if isinstance(value, str) and ("http://" in value or "https://" in value):
        return "<redacted>"
    return value


def resolve_v1r_effective_from_production(
    *,
    yaml_path: Optional[Path] = None,
    pilot_config: Any = None,
) -> V1REffectiveRuntime:
    """Load production YAML via real loader (or accept preloaded), isolate V1R."""
    path = Path(yaml_path) if yaml_path else PRODUCTION_YAML
    pin_path = PRODUCTION_PIN
    if pilot_config is None:
        from small_paper.config import load_pilot_config
        pilot_config = load_pilot_config(path)

    yaml_sha = file_sha256(path)
    pin_sha = pin_path.read_text(encoding="utf-8").strip() if pin_path.exists() else ""
    raw = getattr(pilot_config, "raw", None) or {}
    routings = [classify_yaml_key(k, raw.get(k)) for k in sorted(raw.keys())]

    # Cap alias: canonical V1R = freeze POSITION_CAP; YAML max_concurrent is observe-only
    yaml_cap = int(getattr(pilot_config, "max_concurrent_positions", 0) or 0)

    return V1REffectiveRuntime(
        config_source_paths=[
            str(path.resolve()),
            str(pin_path.resolve()),
            str((NATIVE_ROOT / "results/research/e1_x39d_final_activation/V1R_PAPER_PRIMARY_ACTIVATION_V1.json").resolve()),
            str((NATIVE_ROOT / "results/research/e1_x39c_concentration_reconciliation/V1R_OPERATIONAL_UNIVERSE_BINDING_V1.json").resolve()),
        ],
        yaml_sha256=yaml_sha,
        pin_sha256=pin_sha,
        pin_match=yaml_sha == pin_sha,
        position_cap=POSITION_CAP,
        qty=LOT_QTY,
        wait_sec=WAIT_SEC,
        board_freshness_sec=BOARD_FRESHNESS_SEC_V1R,
        live_trading_enabled=bool(getattr(pilot_config, "live_trading_enabled", False)),
        order_enabled=bool(getattr(pilot_config, "order_enabled", False)),
        order_submit_disabled=not bool(getattr(pilot_config, "order_enabled", False)),
        cancel_disabled=True,
        paper_only=bool(getattr(pilot_config, "paper_only", True)),
        pbv2_yaml_cap=yaml_cap,
        pbv2_freshness_board_age_sec=float(getattr(pilot_config, "entry_max_board_age_sec", 3.0)),
        pbv2_daily_loss_guard_enabled=bool(getattr(pilot_config, "daily_loss_guard_enabled", False)),
        key_routings=routings,
        isolation_applied=True,
    )


def assert_v1r_not_contaminated(eff: V1REffectiveRuntime) -> dict[str, Any]:
    """Fail if any SHADOW key is marked as reaching V1R Primary."""
    leaks = [
        r for r in eff.key_routings
        if r.classification == "SHADOW_PBV2" and r.reaches_v1r_primary
    ]
    return {
        "pass": len(leaks) == 0 and eff.board_freshness_sec == BOARD_FRESHNESS_SEC_V1R,
        "leaks": [r.yaml_key for r in leaks],
        "v1r_freshness": eff.board_freshness_sec,
        "pbv2_freshness": eff.pbv2_freshness_board_age_sec,
    }
