"""
Phase 55: Live observer re-trial readiness checks (q070_cap3 + allowed windows).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

from small_paper.allowed_trading_windows import (
    DEFAULT_ALLOWED_WINDOWS,
    parse_allowed_trading_windows,
    windows_summary,
)
from small_paper.config import SmallPaperPilotConfig, load_pilot_config
from small_paper.safety import (
    SafetyCheck,
    check_discord_observer_only,
    check_discord_webhook_env,
    check_kabu_station_connection,
    check_mfe_favorable_trial_config,
    check_no_live_order_paths,
    check_order_disabled,
    check_output_path_writable,
    check_paper_only,
    check_daytrade_suitability_trial_config,
    check_symbol_cooloff_trial_config,
    load_config_and_check,
)

JST = ZoneInfo("Asia/Tokyo")

EXPECTED_POLICY_LABEL = "q070_cap3_trial"
EXPECTED_MFE_FAV_POLICY_LABEL = "q070_cap3_mfe_fav_trial"
EXPECTED_SYMBOL_COOLOFF_POLICY_LABEL = "q070_cap3_mfe_fav_symbol_cooloff_trial"
EXPECTED_VOL_LIQ_POLICY_LABEL = "q070_cap3_mfe_fav_vol_liq_trial"
EXPECTED_PRICE_MOM_EXIT_POLICY_LABEL = "q070_cap3_mfe_fav_price_mom_exit_trial"
SUPPORTED_TRIAL_POLICY_LABELS = frozenset(
    {
        EXPECTED_POLICY_LABEL,
        EXPECTED_MFE_FAV_POLICY_LABEL,
        EXPECTED_SYMBOL_COOLOFF_POLICY_LABEL,
        EXPECTED_VOL_LIQ_POLICY_LABEL,
        EXPECTED_PRICE_MOM_EXIT_POLICY_LABEL,
    }
)
EXPECTED_MIN_QUALITY = 0.70
EXPECTED_ENTRY_SCORE_V2_MIN = 3
EXPECTED_MAX_CONCURRENT = 5


def _q070_entry_gate_ok(config: SmallPaperPilotConfig) -> bool:
    """Phase267: v2>=4 gate replaces quality reject on q070 trial YAMLs."""
    return int(getattr(config, "entry_score_v2_min", 0) or 0) == EXPECTED_ENTRY_SCORE_V2_MIN and (
        not config.reject_below_quality
    )
MIN_PHASE54_PF = 1.20
MIN_PHASE60_STRUCTURAL_PF = 1.20
MIN_PHASE79_OOS_PF = 1.20
MIN_PHASE84_OOS_PF = 1.20
PHASE79_SYMBOL_COOLOFF_REVIEW_FILENAME = "phase79_symbol_cooloff_trial_review.json"
PHASE84_VOL_LIQ_REVIEW_REL = "kabu_native/results/reports/phase84_vol_liq_trial_review.json"
DEFAULT_PHASE54_SESSION_REL = (
    "kabu_native/results/small_paper/20260518/push_replay_220451"
)
DEFAULT_PHASE60_STRUCTURAL_SESSION_REL = (
    "kabu_native/results/small_paper/20260519/live_full_session_081047"
)
EXPECTED_STRUCTURAL_EXIT_POLICY = "combined_structural_exit_v1"
EXPECTED_STRUCTURAL_EXIT_POLICY_V2_PRICE_MOM = "combined_structural_exit_v2_price_mom"
PHASE72_STRUCTURAL_REVIEW_FILENAME = "phase72_price_momentum_exit_trial_review.json"
STRUCTURAL_REVIEW_FILENAME = "structural_observer_review.json"


@dataclass
class ReadinessCheck:
    check_id: str
    passed: bool
    message: str
    details: dict[str, Any] = field(default_factory=dict)


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _expected_windows_match(config: SmallPaperPilotConfig) -> bool:
    expected = parse_allowed_trading_windows(
        [{"start": s, "end": e} for s, e in DEFAULT_ALLOWED_WINDOWS]
    )
    actual = config.allowed_windows()
    if len(actual) != len(expected):
        return False
    for a, e in zip(actual, expected):
        if a.start != e.start or a.end != e.end:
            return False
    return True


def _session_key_from_structural_dir(structural_session_dir: Path, repo_root: Path) -> str:
    base = (repo_root / "kabu_native" / "results" / "small_paper").resolve()
    return str(structural_session_dir.resolve().relative_to(base)).replace("\\", "/")


def check_phase79_symbol_cooloff_config(config: SmallPaperPilotConfig) -> ReadinessCheck:
    ok = (
        config.policy_label == EXPECTED_SYMBOL_COOLOFF_POLICY_LABEL
        and config.policy_trial
        and config.baseline_policy == EXPECTED_MFE_FAV_POLICY_LABEL
        and _q070_entry_gate_ok(config)
        and config.max_concurrent_positions == EXPECTED_MAX_CONCURRENT
        and config.favorable_mode == "mfe_linked"
        and config.favorable_mfe_scale > 0
        and config.use_market_time_window
        and config.structural_exit_policy == EXPECTED_STRUCTURAL_EXIT_POLICY
        and config.symbol_cooloff_enabled
        and config.symbol_cooloff_rule == "prior_avg_pnl_negative_trades_ge_5"
        and config.symbol_cooloff_min_trades == 5
        and config.symbol_cooloff_apply_mode == "reject_entry"
        and not config.order_enabled
        and config.paper_only
    )
    return ReadinessCheck(
        "phase79_symbol_cooloff_trial_config",
        ok,
        "q070_cap3_mfe_fav_symbol_cooloff trial config OK"
        if ok
        else "expected Phase79 symbol cooloff trial config (mfe_fav + rule D)",
        {
            "policy_label": config.policy_label,
            "baseline_policy": config.baseline_policy,
            "symbol_cooloff_enabled": config.symbol_cooloff_enabled,
            "symbol_cooloff_rule": config.symbol_cooloff_rule,
            "symbol_cooloff_min_trades": config.symbol_cooloff_min_trades,
            "order_enabled": config.order_enabled,
            "paper_only": config.paper_only,
        },
    )


def check_symbol_cooloff_trial_readiness(
    config: SmallPaperPilotConfig,
    *,
    repo_root: Path,
    structural_session_dir: Path,
) -> ReadinessCheck:
    sc = check_symbol_cooloff_trial_config(
        config,
        repo_root=repo_root,
        run_session_key=_session_key_from_structural_dir(structural_session_dir, repo_root),
    )
    return ReadinessCheck(sc.check_id, sc.passed, sc.message, sc.details)


def check_symbol_cooloff_prior_only(
    config: SmallPaperPilotConfig,
    *,
    repo_root: Path,
    structural_session_dir: Path,
) -> ReadinessCheck:
    if not config.symbol_cooloff_enabled:
        return ReadinessCheck(
            "symbol_cooloff_prior_only",
            True,
            "symbol_cooloff disabled (skipped)",
            {},
        )
    from small_paper.symbol_cooloff import (
        build_symbol_cooloff_state,
        validate_prior_only_sources,
    )

    run_key = _session_key_from_structural_dir(structural_session_dir, repo_root)
    state = build_symbol_cooloff_state(
        config, repo_root=repo_root, run_session_key=run_key
    )
    if state is None:
        return ReadinessCheck(
            "symbol_cooloff_prior_only",
            False,
            "symbol_cooloff_enabled but state not built",
            {"run_session_key": run_key},
        )
    errs = validate_prior_only_sources(state, run_session_key=run_key)
    ok = not errs
    return ReadinessCheck(
        "symbol_cooloff_prior_only",
        ok,
        "cooloff sources strictly before run session"
        if ok
        else "; ".join(errs),
        {
            "run_session_key": run_key,
            "source_sessions": state.source_sessions,
            "prior_only_errors": errs,
        },
    )


def check_symbol_cooloff_summary_field(
    config: SmallPaperPilotConfig,
    structural_session_dir: Path,
) -> ReadinessCheck:
    """Verify rejected_by_symbol_cooloff is emitted (summary or Phase79 OOS evidence)."""
    if not config.symbol_cooloff_enabled:
        return ReadinessCheck(
            "symbol_cooloff_summary_field",
            True,
            "symbol_cooloff disabled (skipped)",
            {},
        )
    summary_path = structural_session_dir / "small_paper_summary.json"
    summary = _load_json(summary_path)
    if "rejected_by_symbol_cooloff" in summary:
        val = summary.get("rejected_by_symbol_cooloff")
        return ReadinessCheck(
            "symbol_cooloff_summary_field",
            True,
            f"small_paper_summary.json has rejected_by_symbol_cooloff={val}",
            {"summary_path": str(summary_path), "rejected_by_symbol_cooloff": val},
        )
    p79_path = structural_session_dir / PHASE79_SYMBOL_COOLOFF_REVIEW_FILENAME
    p79 = _load_json(p79_path)
    agg = (p79.get("aggregate_oos") or {}).get("symbol_cooloff_trial") or {}
    rejected = int(agg.get("aggregate_rejected_by_symbol_cooloff") or 0)
    ok = bool(p79) and rejected > 0
    return ReadinessCheck(
        "symbol_cooloff_summary_field",
        ok,
        f"Phase79 OOS shows rejections ({rejected}); live summary will emit rejected_by_symbol_cooloff"
        if ok
        else "missing rejected_by_symbol_cooloff in summary and no Phase79 rejection evidence",
        {
            "summary_path": str(summary_path),
            "phase79_review": str(p79_path),
            "aggregate_rejected_by_symbol_cooloff": rejected,
            "pilot_summary_fields": [
                "symbol_cooloff_enabled",
                "symbol_cooloff_list",
                "rejected_by_symbol_cooloff",
            ],
        },
    )


def check_phase79_symbol_cooloff_oos_pf(structural_session_dir: Path) -> ReadinessCheck:
    path = structural_session_dir / PHASE79_SYMBOL_COOLOFF_REVIEW_FILENAME
    data = _load_json(path)
    agg = (data.get("aggregate_oos") or {}).get("symbol_cooloff_trial") or {}
    pf_raw = agg.get("aggregate_structural_pf")
    pf_val = float(pf_raw) if isinstance(pf_raw, (int, float)) else 0.0
    ok = bool(data) and pf_val >= MIN_PHASE79_OOS_PF
    return ReadinessCheck(
        "phase79_symbol_cooloff_oos_pf",
        ok,
        f"Phase79 OOS aggregate PF {pf_val} >= {MIN_PHASE79_OOS_PF}"
        if ok
        else f"missing {PHASE79_SYMBOL_COOLOFF_REVIEW_FILENAME} or PF {pf_raw} below {MIN_PHASE79_OOS_PF}",
        {
            "phase79_review_path": str(path),
            "oos_aggregate_structural_pf": pf_val,
            "min_required": MIN_PHASE79_OOS_PF,
            "decision": data.get("decision"),
            "no_filter_oos_pf": (data.get("aggregate_oos") or {})
            .get("no_filter", {})
            .get("aggregate_structural_pf"),
        },
    )


def phase79_symbol_cooloff_oos_pf(structural_session_dir: Path) -> Optional[float]:
    data = _load_json(structural_session_dir / PHASE79_SYMBOL_COOLOFF_REVIEW_FILENAME)
    agg = (data.get("aggregate_oos") or {}).get("symbol_cooloff_trial") or {}
    pf = agg.get("aggregate_structural_pf")
    return float(pf) if isinstance(pf, (int, float)) else None


def _phase84_review_path(repo_root: Path) -> Path:
    return repo_root / PHASE84_VOL_LIQ_REVIEW_REL


def phase84_vol_liq_oos_pf(repo_root: Path) -> Optional[float]:
    data = _load_json(_phase84_review_path(repo_root))
    agg = (data.get("aggregate_oos") or {}).get("vol_liq_trial") or {}
    pf = agg.get("aggregate_structural_pf")
    return float(pf) if isinstance(pf, (int, float)) else None


def check_phase84_vol_liq_trial_config(config: SmallPaperPilotConfig) -> ReadinessCheck:
    ok = (
        config.policy_label == EXPECTED_VOL_LIQ_POLICY_LABEL
        and config.policy_trial
        and config.baseline_policy == EXPECTED_MFE_FAV_POLICY_LABEL
        and _q070_entry_gate_ok(config)
        and config.max_concurrent_positions == EXPECTED_MAX_CONCURRENT
        and config.favorable_mode == "mfe_linked"
        and config.favorable_mfe_scale > 0
        and config.use_market_time_window
        and config.structural_exit_policy == EXPECTED_STRUCTURAL_EXIT_POLICY
        and config.daytrade_suitability_enabled
        and config.daytrade_suitability_rule == "volatility_liquidity_top50"
        and config.daytrade_suitability_lookback_sessions == "prior_only"
        and config.daytrade_suitability_apply_mode == "reject_entry"
        and not config.order_enabled
        and config.paper_only
    )
    return ReadinessCheck(
        "phase84_vol_liq_trial_config",
        ok,
        "q070_cap3_mfe_fav_vol_liq trial config OK"
        if ok
        else "vol_liq trial config mismatch",
        {
            "policy_label": config.policy_label,
            "baseline_policy": config.baseline_policy,
            "daytrade_suitability_enabled": config.daytrade_suitability_enabled,
            "daytrade_suitability_rule": config.daytrade_suitability_rule,
            "daytrade_suitability_lookback_sessions": config.daytrade_suitability_lookback_sessions,
        },
    )


def check_daytrade_suitability_trial_readiness(
    config: SmallPaperPilotConfig,
    *,
    repo_root: Path,
    structural_session_dir: Path,
) -> ReadinessCheck:
    sc = check_daytrade_suitability_trial_config(
        config,
        repo_root=repo_root,
        run_session_key=_session_key_from_structural_dir(structural_session_dir, repo_root),
    )
    return ReadinessCheck(sc.check_id, sc.passed, sc.message, sc.details)


def check_daytrade_suitability_prior_only(
    config: SmallPaperPilotConfig,
    *,
    repo_root: Path,
    structural_session_dir: Path,
) -> ReadinessCheck:
    if not config.daytrade_suitability_enabled:
        return ReadinessCheck(
            "daytrade_suitability_prior_only",
            True,
            "daytrade_suitability disabled (skipped)",
            {},
        )
    from small_paper.daytrade_suitability_gate import (
        build_vol_liq_threshold,
        validate_prior_only_sources,
    )

    run_key = _session_key_from_structural_dir(structural_session_dir, repo_root)
    state = build_vol_liq_threshold(
        config, repo_root=repo_root, run_session_key=run_key
    )
    if state is None:
        return ReadinessCheck(
            "daytrade_suitability_prior_only",
            False,
            "daytrade_suitability_enabled but state not built",
            {"run_session_key": run_key},
        )
    errs = validate_prior_only_sources(state, run_session_key=run_key)
    ok = not errs and len(state.source_sessions) > 0
    return ReadinessCheck(
        "daytrade_suitability_prior_only",
        ok,
        "suitability threshold from prior sessions only"
        if ok
        else "; ".join(errs) if errs else "no prior source sessions for threshold",
        {
            "run_session_key": run_key,
            "daytrade_suitability_source_sessions": state.source_sessions,
            "daytrade_suitability_threshold": state.vol_liq_threshold,
            "prior_quality_trade_count": state.prior_quality_trade_count,
            "prior_only_errors": errs,
        },
    )


def check_daytrade_suitability_summary_field(
    config: SmallPaperPilotConfig,
    structural_session_dir: Path,
    *,
    repo_root: Path,
) -> ReadinessCheck:
    if not config.daytrade_suitability_enabled:
        return ReadinessCheck(
            "daytrade_suitability_summary_field",
            True,
            "daytrade_suitability disabled (skipped)",
            {},
        )
    summary_path = structural_session_dir / "small_paper_summary.json"
    summary = _load_json(summary_path)
    if "rejected_by_daytrade_suitability" in summary:
        val = summary.get("rejected_by_daytrade_suitability")
        return ReadinessCheck(
            "daytrade_suitability_summary_field",
            True,
            f"small_paper_summary.json has rejected_by_daytrade_suitability={val}",
            {"summary_path": str(summary_path), "rejected_by_daytrade_suitability": val},
        )
    p84 = _load_json(_phase84_review_path(repo_root))
    agg = (p84.get("aggregate_oos") or {}).get("vol_liq_trial") or {}
    rejected = int(agg.get("aggregate_rejected_by_suitability") or 0)
    ok = bool(p84) and rejected > 0
    return ReadinessCheck(
        "daytrade_suitability_summary_field",
        ok,
        f"Phase84 OOS shows suitability rejections ({rejected}); live summary will emit rejected_by_daytrade_suitability"
        if ok
        else "missing rejected_by_daytrade_suitability in summary and no Phase84 rejection evidence",
        {
            "summary_path": str(summary_path),
            "phase84_review": str(_phase84_review_path(repo_root)),
            "aggregate_rejected_by_suitability": rejected,
            "pilot_summary_fields": [
                "daytrade_suitability_enabled",
                "daytrade_suitability_rule",
                "daytrade_suitability_threshold",
                "rejected_by_daytrade_suitability",
                "daytrade_suitability_source_sessions",
            ],
        },
    )


def check_phase84_vol_liq_oos_pf(repo_root: Path) -> ReadinessCheck:
    path = _phase84_review_path(repo_root)
    data = _load_json(path)
    agg = (data.get("aggregate_oos") or {}).get("vol_liq_trial") or {}
    pf_raw = agg.get("aggregate_structural_pf")
    pf_val = float(pf_raw) if isinstance(pf_raw, (int, float)) else 0.0
    ok = bool(data) and pf_val >= MIN_PHASE84_OOS_PF
    return ReadinessCheck(
        "phase84_vol_liq_oos_pf",
        ok,
        f"Phase84 OOS aggregate PF {pf_val} >= {MIN_PHASE84_OOS_PF}"
        if ok
        else f"missing {path.name} or PF {pf_raw} below {MIN_PHASE84_OOS_PF}",
        {
            "phase84_review_path": str(path),
            "oos_aggregate_structural_pf": pf_val,
            "min_required": MIN_PHASE84_OOS_PF,
            "recommendation": data.get("recommendation"),
            "no_filter_oos_pf": (data.get("aggregate_oos") or {})
            .get("no_filter", {})
            .get("aggregate_structural_pf"),
        },
    )


def check_phase67_mfe_fav_config(config: SmallPaperPilotConfig) -> ReadinessCheck:
    ok = (
        config.policy_label == EXPECTED_MFE_FAV_POLICY_LABEL
        and config.policy_trial
        and config.baseline_policy == "q070_cap3_trial"
        and _q070_entry_gate_ok(config)
        and config.max_concurrent_positions == EXPECTED_MAX_CONCURRENT
        and config.favorable_mode == "mfe_linked"
        and config.favorable_mfe_scale > 0
        and config.use_market_time_window
        and config.structural_exit_policy == EXPECTED_STRUCTURAL_EXIT_POLICY
    )
    return ReadinessCheck(
        "phase67_mfe_fav_trial_config",
        ok,
        "q070_cap3_mfe_fav trial config OK"
        if ok
        else "expected q070_cap3_mfe_fav_trial with mfe_linked favorable + market time window",
        {
            "policy_label": config.policy_label,
            "baseline_policy": config.baseline_policy,
            "favorable_mode": config.favorable_mode,
            "favorable_mfe_scale": config.favorable_mfe_scale,
            "use_market_time_window": config.use_market_time_window,
            "structural_exit_policy": config.structural_exit_policy,
        },
    )


def check_phase51_config(config: SmallPaperPilotConfig) -> ReadinessCheck:
    ok = (
        config.policy_label in SUPPORTED_TRIAL_POLICY_LABELS
        and config.policy_trial
        and _q070_entry_gate_ok(config)
        and config.max_concurrent_positions == EXPECTED_MAX_CONCURRENT
    )
    return ReadinessCheck(
        "phase51_q070_cap3_config",
        ok,
        "supported trial policy label OK"
        if ok
        else (
            f"expected one of {sorted(SUPPORTED_TRIAL_POLICY_LABELS)} "
            f"entry_score_v2_min={EXPECTED_ENTRY_SCORE_V2_MIN} cap={EXPECTED_MAX_CONCURRENT}"
        ),
        {
            "policy_label": config.policy_label,
            "policy_trial": config.policy_trial,
            "trial_policy_supported": config.policy_label in SUPPORTED_TRIAL_POLICY_LABELS,
            "min_continuation_quality": config.min_continuation_quality,
            "max_concurrent_positions": config.max_concurrent_positions,
            "supported_trial_policy_labels": sorted(SUPPORTED_TRIAL_POLICY_LABELS),
        },
    )


def check_phase72_price_mom_exit_trial_config(config: SmallPaperPilotConfig) -> ReadinessCheck:
    ratio = float(config.price_momentum_fade_ratio)
    ratio_ok = 0.75 <= ratio <= 0.85
    ok = (
        config.policy_label == EXPECTED_PRICE_MOM_EXIT_POLICY_LABEL
        and config.policy_trial
        and config.baseline_policy == EXPECTED_MFE_FAV_POLICY_LABEL
        and _q070_entry_gate_ok(config)
        and config.max_concurrent_positions == EXPECTED_MAX_CONCURRENT
        and config.favorable_mode == "mfe_linked"
        and config.favorable_mfe_scale > 0
        and config.use_market_time_window
        and config.structural_exit_policy == EXPECTED_STRUCTURAL_EXIT_POLICY_V2_PRICE_MOM
        and ratio_ok
    )
    return ReadinessCheck(
        "phase72_price_mom_exit_trial_config",
        ok,
        "q070_cap3_mfe_fav_price_mom_exit trial config OK"
        if ok
        else "expected Phase72 price-momentum fade trial config",
        {
            "policy_label": config.policy_label,
            "baseline_policy": config.baseline_policy,
            "structural_exit_policy": config.structural_exit_policy,
            "price_momentum_fade_ratio": ratio,
            "favorable_mode": config.favorable_mode,
        },
    )


def check_phase52_allowed_windows(config: SmallPaperPilotConfig) -> ReadinessCheck:
    ok = bool(config.allowed_windows()) and _expected_windows_match(config)
    return ReadinessCheck(
        "phase52_allowed_trading_windows",
        ok,
        "allowed_trading_windows fixed (09:05-11:23, 12:33-15:20)"
        if ok
        else "allowed_trading_windows missing or not Phase52 defaults",
        {"allowed_trading_windows": windows_summary(config.allowed_windows())},
    )


def check_phase53_cap_not_recommended(
    reference_session_dir: Path,
) -> ReadinessCheck:
    path = reference_session_dir / "exposure_cap_whatif.json"
    data = _load_json(path)
    rec = (data.get("recommendation") or {}).get("recommend_cap_candidate")
    guidance = (data.get("recommendation") or {}).get("live_observer_trial_guidance", "")
    ok = bool(data) and rec is None
    return ReadinessCheck(
        "phase53_cap_lift_not_recommended",
        ok if data else False,
        "cap4/5 not recommended; hold cap=3"
        if ok and data
        else "exposure_cap_whatif missing or cap lift still recommended",
        {
            "recommend_cap_candidate": rec,
            "guidance": guidance,
            "reference": str(reference_session_dir),
        },
    )


def check_phase54_baseline_pf(reference_session_dir: Path) -> ReadinessCheck:
    path = reference_session_dir / "runtime_exit_review.json"
    data = _load_json(path)
    whatif = data.get("exit_policy_whatif") or []
    baseline = next((r for r in whatif if r.get("policy") == "baseline_observer_exit"), {})
    pf = baseline.get("profit_factor")
    pf_val = float(pf) if isinstance(pf, (int, float)) else 0.0
    ok = bool(data) and pf_val >= MIN_PHASE54_PF
    return ReadinessCheck(
        "phase54_baseline_observer_pf",
        ok,
        f"baseline_observer PF {pf_val} >= {MIN_PHASE54_PF}"
        if ok
        else f"runtime_exit_review missing or PF {pf} below {MIN_PHASE54_PF}",
        {
            "profit_factor": pf,
            "avg_pnl_pct": baseline.get("avg_pnl_pct"),
            "recommend_runtime_fix": (data.get("recommendation") or {}).get(
                "recommend_runtime_fix"
            ),
            "reference": str(reference_session_dir),
        },
    )


def check_mfe_favorable_trial_readiness(config: SmallPaperPilotConfig) -> ReadinessCheck:
    sc = check_mfe_favorable_trial_config(config)
    return ReadinessCheck(sc.check_id, sc.passed, sc.message, sc.details)


def check_take_observer_only(config: SmallPaperPilotConfig) -> ReadinessCheck:
    sc = check_discord_observer_only(config)
    nlo = check_no_live_order_paths(config)
    ok = sc.passed and nlo.passed and config.discord_observer_only
    return ReadinessCheck(
        "take_is_observer_only",
        ok,
        "TAKE/HOLD/EXIT are observer notifications only; no order path"
        if ok
        else "observer-only or no-order-path check failed",
        {
            "discord_observer_only": config.discord_observer_only,
            "order_enabled": config.order_enabled,
        },
    )


def check_safety_core(config: SmallPaperPilotConfig) -> list[ReadinessCheck]:
    out: list[ReadinessCheck] = []
    for sc in (check_order_disabled(config), check_paper_only(config)):
        out.append(
            ReadinessCheck(
                sc.check_id,
                sc.passed,
                sc.message,
                sc.details,
            )
        )
    return out


def check_discord_ready(config: SmallPaperPilotConfig) -> ReadinessCheck:
    sc = check_discord_webhook_env(config)
    if not config.discord_enabled:
        return ReadinessCheck(
            "discord_observer_ok",
            False,
            "discord_enabled=false — enable for live observer retrial",
            {},
        )
    return ReadinessCheck(
        "discord_observer_ok",
        sc.passed,
        sc.message,
        sc.details,
    )


def check_kabu_connection(repo_root: Path, *, stale_tick_sec: float) -> ReadinessCheck:
    sc = check_kabu_station_connection(repo_root, stale_tick_sec=stale_tick_sec)
    warn = (sc.details or {}).get("is_warning") or "WARNING" in sc.message
    return ReadinessCheck(
        "kabu_connection_ok",
        sc.passed,
        sc.message,
        {**(sc.details or {}), "warning": warn},
    )


def check_output_writable(
    config: SmallPaperPilotConfig,
    *,
    repo_root: Path,
    day_key: str,
) -> ReadinessCheck:
    sc = check_output_path_writable(config, repo_root=repo_root, day_key=day_key)
    return ReadinessCheck(sc.check_id, sc.passed, sc.message, sc.details)


def phase54_reference_pf(reference_session_dir: Path) -> Optional[float]:
    data = _load_json(reference_session_dir / "runtime_exit_review.json")
    for row in data.get("exit_policy_whatif") or []:
        if row.get("policy") == "baseline_observer_exit":
            pf = row.get("profit_factor")
            return float(pf) if isinstance(pf, (int, float)) else None
    perf = _load_json(reference_session_dir / "small_paper_performance_review.json")
    acc = perf.get("accepted_trade_performance") or {}
    pf = acc.get("profit_factor")
    return float(pf) if isinstance(pf, (int, float)) else None


def resolve_structural_review(
    structural_session_dir: Path,
    *,
    prefer_v2: bool = False,
) -> tuple[dict[str, Any], str]:
    """Prefer phase72 trial review JSON when present, else structural_observer_review.json."""
    p72 = structural_session_dir / PHASE72_STRUCTURAL_REVIEW_FILENAME
    p60 = structural_session_dir / STRUCTURAL_REVIEW_FILENAME
    if p72.is_file() and (prefer_v2 or not p60.is_file()):
        return _load_json(p72), PHASE72_STRUCTURAL_REVIEW_FILENAME
    if p60.is_file():
        return _load_json(p60), STRUCTURAL_REVIEW_FILENAME
    if p72.is_file():
        return _load_json(p72), PHASE72_STRUCTURAL_REVIEW_FILENAME
    return {}, ""


def _structural_verdict_and_pf(
    data: Mapping[str, Any],
    *,
    config: SmallPaperPilotConfig,
) -> tuple[str, float, str]:
    """Return (verdict, structural_pf, policy_from_review)."""
    if int(data.get("phase") or 0) == 72 or data.get("v2_official_verdict"):
        verdict = str(data.get("v2_official_verdict") or data.get("official_verdict") or "")
        pf_raw = data.get("structural_pf_v2")
        if pf_raw is None:
            v2m = data.get("v2_metrics") or {}
            pf_raw = v2m.get("structural_pf")
        policy = EXPECTED_STRUCTURAL_EXIT_POLICY_V2_PRICE_MOM
    else:
        verdict = str(data.get("official_verdict") or "")
        pf_raw = data.get("structural_pf")
        if pf_raw is None:
            sm = data.get("structural_metrics") or {}
            pf_raw = sm.get("structural_pf")
        policy = str(data.get("structural_exit_policy") or data.get("policy") or "")
    pf_val = float(pf_raw) if isinstance(pf_raw, (int, float)) else 0.0
    if config.structural_exit_policy == EXPECTED_STRUCTURAL_EXIT_POLICY_V2_PRICE_MOM:
        if data.get("v2_official_verdict"):
            verdict = str(data.get("v2_official_verdict"))
        if data.get("structural_pf_v2") is not None:
            pf_val = float(data["structural_pf_v2"])
        policy = config.structural_exit_policy or policy
    return verdict, pf_val, policy


def check_phase60_combined_structural_pass(
    structural_session_dir: Path,
    *,
    config: SmallPaperPilotConfig,
) -> ReadinessCheck:
    prefer_v2 = config.structural_exit_policy == EXPECTED_STRUCTURAL_EXIT_POLICY_V2_PRICE_MOM
    data, source = resolve_structural_review(structural_session_dir, prefer_v2=prefer_v2)
    verdict, pf_val, policy = _structural_verdict_and_pf(data, config=config)
    allowed_policies = {
        EXPECTED_STRUCTURAL_EXIT_POLICY,
        EXPECTED_STRUCTURAL_EXIT_POLICY_V2_PRICE_MOM,
        "combined_structural_exit_v1_fade_watch_shadow",
        "combined_structural_exit_v1_fade_hybrid_shadow",
        "combined_structural_exit_v1_fade_breakdown_shadow",
        "combined_structural_exit_v1_breakdown_confirmed_shadow",
        "combined_structural_exit_v1_fade_disable_shadow",
    }
    cfg_ok = config.structural_exit_policy in allowed_policies or policy in allowed_policies
    ok = (
        bool(data)
        and bool(source)
        and cfg_ok
        and verdict == "structural_pass"
        and round(pf_val, 2) >= MIN_PHASE60_STRUCTURAL_PF
    )
    return ReadinessCheck(
        "phase60_combined_structural_pass",
        ok,
        f"structural review ({source}) verdict={verdict} PF={pf_val}"
        if ok
        else f"missing/invalid review source={source} verdict={verdict} PF={pf_val}",
        {
            "structural_exit_policy": policy or config.structural_exit_policy,
            "official_verdict": verdict,
            "structural_pf": pf_val,
            "structural_avg_pnl": data.get("structural_avg_pnl")
            or (data.get("v2_metrics") or {}).get("structural_avg_pnl"),
            "structural_review_source": source,
            "reference": str(structural_session_dir),
        },
    )


def live_observer_retrial_summary_fields(
    config: SmallPaperPilotConfig,
    *,
    reference_session_dir: Optional[Path] = None,
    structural_session_dir: Optional[Path] = None,
    repo_root: Optional[Path] = None,
) -> dict[str, Any]:
    pf = phase54_reference_pf(reference_session_dir) if reference_session_dir else None
    struct_data, struct_source = (
        resolve_structural_review(structural_session_dir, prefer_v2=True)
        if structural_session_dir
        else ({}, "")
    )
    _, struct_pf, _ = _structural_verdict_and_pf(struct_data, config=config) if struct_data else ("", 0.0, "")
    p79_pf = (
        phase79_symbol_cooloff_oos_pf(structural_session_dir)
        if structural_session_dir and config.policy_label == EXPECTED_SYMBOL_COOLOFF_POLICY_LABEL
        else None
    )
    out = {
        "live_observer_retrial_phase": 61,
        "runtime_policy": config.policy_label,
        "exit_policy": config.structural_exit_policy or EXPECTED_STRUCTURAL_EXIT_POLICY,
        "structural_exit_policy": config.structural_exit_policy or EXPECTED_STRUCTURAL_EXIT_POLICY,
        "observer_exit_mode": "combined_structural_exit_notification_only",
        "take_is_observer_only": True,
        "allowed_trading_windows": windows_summary(config.allowed_windows()),
        "phase54_reference_pf": pf,
        "phase54_reference_session": str(reference_session_dir) if reference_session_dir else None,
        "phase60_structural_pf": struct_pf or struct_data.get("structural_pf"),
        "phase60_official_verdict": struct_data.get("official_verdict")
        or struct_data.get("v2_official_verdict"),
        "structural_review_source": struct_source,
        "phase60_structural_session": str(structural_session_dir) if structural_session_dir else None,
        "phase54_take_note": "TAKE is observer signal only; combined structural rules may EXIT notify",
        "post_session_review_cmd": (
            "python kabu_native/scripts/review_structural_observer.py "
            "--structural-exit-policy combined_structural_exit_v1"
        ),
    }
    if config.policy_label == EXPECTED_SYMBOL_COOLOFF_POLICY_LABEL:
        out["phase79_symbol_cooloff_oos_pf"] = p79_pf
        out["symbol_cooloff_rule"] = config.symbol_cooloff_rule
        out["symbol_cooloff_note"] = (
            "Rolling OOS chronic-loser cooloff (not 5803-specific); limited session history - trial only"
        )
    if config.policy_label == EXPECTED_VOL_LIQ_POLICY_LABEL and repo_root is not None:
        out["phase84_oos_pf_reference"] = phase84_vol_liq_oos_pf(repo_root)
        out["daytrade_suitability_rule"] = config.daytrade_suitability_rule
        out["daytrade_suitability_note"] = (
            "volatility_liquidity top50 from prior sessions only; not marketcap-tier targeting"
        )
    return out


def run_live_observer_readiness(
    config_path: Path,
    *,
    repo_root: Path,
    day_key: str,
    reference_session_dir: Path,
    structural_session_dir: Optional[Path] = None,
    skip_kabu: bool = False,
    skip_safety_bundle: bool = False,
) -> dict[str, Any]:
    config = load_pilot_config(config_path)
    struct_dir = structural_session_dir or (repo_root / DEFAULT_PHASE60_STRUCTURAL_SESSION_REL)
    if config.policy_label == EXPECTED_PRICE_MOM_EXIT_POLICY_LABEL:
        policy_checks = [
            check_phase51_config(config),
            check_phase72_price_mom_exit_trial_config(config),
            check_mfe_favorable_trial_readiness(config),
        ]
    elif config.policy_label == EXPECTED_SYMBOL_COOLOFF_POLICY_LABEL:
        policy_checks = [
            check_phase51_config(config),
            check_phase79_symbol_cooloff_config(config),
            check_mfe_favorable_trial_readiness(config),
            check_symbol_cooloff_trial_readiness(
                config, repo_root=repo_root, structural_session_dir=struct_dir
            ),
            check_symbol_cooloff_prior_only(
                config, repo_root=repo_root, structural_session_dir=struct_dir
            ),
            check_symbol_cooloff_summary_field(config, struct_dir),
        ]
    elif config.policy_label == EXPECTED_VOL_LIQ_POLICY_LABEL:
        policy_checks = [
            check_phase51_config(config),
            check_phase84_vol_liq_trial_config(config),
            check_mfe_favorable_trial_readiness(config),
            check_daytrade_suitability_trial_readiness(
                config, repo_root=repo_root, structural_session_dir=struct_dir
            ),
            check_daytrade_suitability_prior_only(
                config, repo_root=repo_root, structural_session_dir=struct_dir
            ),
            check_daytrade_suitability_summary_field(
                config, struct_dir, repo_root=repo_root
            ),
        ]
    elif config.policy_label == EXPECTED_MFE_FAV_POLICY_LABEL:
        policy_checks = [
            check_phase51_config(config),
            check_phase67_mfe_fav_config(config),
            check_mfe_favorable_trial_readiness(config),
        ]
    else:
        policy_checks = [check_phase51_config(config)]
    if config.policy_label == EXPECTED_SYMBOL_COOLOFF_POLICY_LABEL:
        structural_gate = check_phase79_symbol_cooloff_oos_pf(struct_dir)
    elif config.policy_label == EXPECTED_VOL_LIQ_POLICY_LABEL:
        structural_gate = check_phase60_combined_structural_pass(struct_dir, config=config)
        checks_extra = [check_phase84_vol_liq_oos_pf(repo_root)]
    else:
        structural_gate = check_phase60_combined_structural_pass(struct_dir, config=config)
        checks_extra = []

    checks: list[ReadinessCheck] = [
        *policy_checks,
        check_phase52_allowed_windows(config),
        check_phase53_cap_not_recommended(reference_session_dir),
        check_phase54_baseline_pf(reference_session_dir),
        structural_gate,
        *checks_extra,
        check_take_observer_only(config),
        *check_safety_core(config),
        check_discord_ready(config),
        check_output_writable(config, repo_root=repo_root, day_key=day_key),
    ]
    if not skip_kabu:
        checks.append(check_kabu_connection(repo_root, stale_tick_sec=config.live_stale_tick_sec))

    safety_failed: list[str] = []
    if not skip_safety_bundle:
        _, safety_checks = load_config_and_check(
            config_path,
            repo_root=repo_root,
            day_key=day_key,
            live_mode=True,
            full_session=True,
            dry_run_flag=True,
        )
        hard_fail = [
            c
            for c in safety_checks
            if not c.passed and c.check_id not in ("legacy_paper_trade_warning",)
        ]
        safety_failed = [c.check_id for c in hard_fail]
        checks.append(
            ReadinessCheck(
                "small_paper_safety_bundle",
                len(hard_fail) == 0,
                "safety bundle pass" if not hard_fail else f"failed: {safety_failed}",
                {"failed_check_ids": safety_failed},
            )
        )

    required = [c for c in checks if c.check_id != "legacy_paper_trade_warning"]
    failed = [c.check_id for c in required if not c.passed]
    warnings = [
        c.check_id
        for c in checks
        if c.passed and (c.details or {}).get("warning")
    ]
    ready = len(failed) == 0
    struct_data, struct_source = resolve_structural_review(
        struct_dir, prefer_v2=config.policy_label == EXPECTED_PRICE_MOM_EXIT_POLICY_LABEL
    )
    cooloff_ids = (
        "phase79_symbol_cooloff_trial_config",
        "symbol_cooloff_trial_config",
        "symbol_cooloff_prior_only",
        "symbol_cooloff_summary_field",
        "phase79_symbol_cooloff_oos_pf",
    )
    symbol_cooloff_check = (
        all(c.passed for c in checks if c.check_id in cooloff_ids)
        if config.policy_label == EXPECTED_SYMBOL_COOLOFF_POLICY_LABEL
        else None
    )
    vol_liq_ids = (
        "phase84_vol_liq_trial_config",
        "daytrade_suitability_trial_config",
        "daytrade_suitability_prior_only",
        "daytrade_suitability_summary_field",
        "phase84_vol_liq_oos_pf",
    )
    daytrade_suitability_check = (
        all(c.passed for c in checks if c.check_id in vol_liq_ids)
        if config.policy_label == EXPECTED_VOL_LIQ_POLICY_LABEL
        else None
    )
    suit_threshold: Optional[float] = None
    suit_sources: list[str] = []
    if config.policy_label == EXPECTED_VOL_LIQ_POLICY_LABEL and config.daytrade_suitability_enabled:
        from small_paper.daytrade_suitability_gate import build_vol_liq_threshold

        run_key = _session_key_from_structural_dir(struct_dir, repo_root)
        st = build_vol_liq_threshold(
            config, repo_root=repo_root, run_session_key=run_key
        )
        if st is not None:
            suit_threshold = st.vol_liq_threshold
            suit_sources = list(st.source_sessions)

    if config.policy_label == EXPECTED_SYMBOL_COOLOFF_POLICY_LABEL:
        report_phase = 80
    elif config.policy_label == EXPECTED_VOL_LIQ_POLICY_LABEL:
        report_phase = 85
    else:
        report_phase = 73

    return {
        "phase": report_phase,
        "component": "live_observer_readiness",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "readiness": ready,
        "ready_for_live_observer_retrial": ready,
        "trial_policy_supported": config.policy_label in SUPPORTED_TRIAL_POLICY_LABELS,
        "symbol_cooloff_check": symbol_cooloff_check,
        "daytrade_suitability_check": daytrade_suitability_check,
        "phase79_oos_pf_reference": phase79_symbol_cooloff_oos_pf(struct_dir)
        if config.policy_label == EXPECTED_SYMBOL_COOLOFF_POLICY_LABEL
        else None,
        "phase84_oos_pf_reference": phase84_vol_liq_oos_pf(repo_root)
        if config.policy_label == EXPECTED_VOL_LIQ_POLICY_LABEL
        else None,
        "daytrade_suitability_threshold": suit_threshold,
        "daytrade_suitability_source_sessions": suit_sources,
        "structural_review_source": struct_source,
        "config_path": str(config_path),
        "reference_session_dir": str(reference_session_dir),
        "structural_session_dir": str(struct_dir),
        "retrial_policy": live_observer_retrial_summary_fields(
            config,
            reference_session_dir=reference_session_dir,
            structural_session_dir=struct_dir,
            repo_root=repo_root,
        ),
        "live_run_command": (
            "python kabu_native/scripts/run_small_paper_pilot.py --dry-run --source live "
            "--full-session --wait-until-session "
            f"--config {config_path} --poll-interval-sec 5"
        ),
        "post_session_review_commands": [
            "python kabu_native/scripts/review_runtime_exit.py --session-dir <live_session_dir> "
            f"--config {config_path}",
            "python kabu_native/scripts/review_runtime_weakness.py --session-dir <live_session_dir>",
            f"python kabu_native/scripts/review_exposure_cap_whatif.py --session-dir <live_session_dir> "
            f"--config {config_path}",
        ],
        "checks": [
            {
                "check_id": c.check_id,
                "passed": c.passed,
                "message": c.message,
                "details": c.details,
            }
            for c in checks
        ],
        "failed_check_ids": failed,
        "warnings": warnings,
        "constraints": {
            "order_enabled": False,
            "paper_only": True,
            "no_new_entry_exit_logic": True,
            "no_time_band_optimization": True,
            "take_is_not_exit": True,
        },
    }


def write_readiness_report(report: Mapping[str, Any], *, repo_root: Path, day_key: str) -> Path:
    out = repo_root / "kabu_native" / "results" / "reports" / f"live_observer_readiness_{day_key}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(dict(report), ensure_ascii=False, indent=2), encoding="utf-8")
    return out
