"""
Phase 44: Small paper pilot safety checks (no live orders).
"""

from __future__ import annotations

import ast
import json
import sys
from dataclasses import dataclass, field
from glob import glob
from pathlib import Path
from typing import Any, Optional, Sequence

from small_paper.config import (
    FROZEN_ENTRY_PROFILE,
    FROZEN_EXIT_PROFILE,
    SmallPaperPilotConfig,
    load_pilot_config,
    resolve_output_dir,
)


@dataclass
class SafetyCheck:
    check_id: str
    passed: bool
    message: str
    details: dict[str, Any] = field(default_factory=dict)


_FORBIDDEN_IMPORT_MARKERS = (
    "paper_trade",
    "yahoo_kabu_watch",
    "kabu_signal_shadow",
    "order_manager",
    "order_client",
    "broker_execution",
)
_FORBIDDEN_CALL_NAMES = frozenset(
    {
        "place_order",
        "send_order",
        "sendorder",
        "placeorder",
        "execute_order",
        "submit_order",
    }
)
_YAHOO_DISCORD_WEBHOOK_ENV = "DISCORD_WEBHOOK_URL"


def _import_module_names(node: ast.Import | ast.ImportFrom) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.module:
        return [node.module]
    return []


def _forbidden_import_hit(module_name: str) -> Optional[str]:
    low = module_name.lower()
    for marker in _FORBIDDEN_IMPORT_MARKERS:
        if low == marker or low.startswith(f"{marker}.") or f".{marker}." in low:
            return f"import:{module_name}"
    return None


def _call_leaf_name(func: ast.expr) -> Optional[str]:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _scan_python_executable_order_refs(path: Path) -> list[str]:
    """AST scan: imports, calls, getenv — excludes comments and docstrings."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as e:
        return [f"syntax_error:{e}"]

    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for mod in _import_module_names(node):
                hit = _forbidden_import_hit(mod)
                if hit:
                    hits.append(hit)
        elif isinstance(node, ast.Call):
            name = _call_leaf_name(node.func)
            if name and name.lower() in _FORBIDDEN_CALL_NAMES:
                hits.append(f"call:{name}")
            if name == "getenv" or (
                isinstance(node.func, ast.Attribute) and node.func.attr == "getenv"
            ):
                if node.args and isinstance(node.args[0], ast.Constant):
                    env = str(node.args[0].value)
                    if env == _YAHOO_DISCORD_WEBHOOK_ENV:
                        hits.append(f"getenv:{env}")
    return hits


def check_order_disabled(config: SmallPaperPilotConfig) -> SafetyCheck:
    ok = not config.order_enabled
    return SafetyCheck(
        "order_enabled_false",
        ok,
        "order_enabled is false" if ok else "order_enabled must be false for pilot",
        {"order_enabled": config.order_enabled},
    )


def check_paper_only(config: SmallPaperPilotConfig) -> SafetyCheck:
    ok = config.paper_only
    return SafetyCheck(
        "paper_only_true",
        ok,
        "paper_only is true" if ok else "paper_only must be true",
        {"paper_only": config.paper_only},
    )


def check_quality_threshold(config: SmallPaperPilotConfig) -> SafetyCheck:
    ok = config.min_continuation_quality >= 0.55
    return SafetyCheck(
        "min_continuation_quality",
        ok,
        f"min_continuation_quality={config.min_continuation_quality}",
        {"min": config.min_continuation_quality, "required_min": 0.55},
    )


def check_max_concurrent(config: SmallPaperPilotConfig) -> SafetyCheck:
    ok = config.max_concurrent_positions <= 3
    return SafetyCheck(
        "max_concurrent_positions",
        ok,
        f"max_concurrent_positions={config.max_concurrent_positions}",
        {"max": config.max_concurrent_positions, "required_max": 3},
    )


def check_trial_policy_label(config: SmallPaperPilotConfig) -> SafetyCheck:
    label = (config.policy_label or "").strip()
    if config.policy_trial:
        ok = bool(label) and label.endswith("_trial")
        return SafetyCheck(
            "trial_policy_label",
            ok,
            f"trial policy_label={label!r}"
            if ok
            else "policy_trial=true requires policy_label ending with _trial",
            {
                "policy_label": label,
                "policy_trial": True,
                "min_continuation_quality": config.min_continuation_quality,
            },
        )
    return SafetyCheck(
        "trial_policy_label",
        True,
        f"production policy_label={label or 'q055_cap3'}",
        {"policy_label": label, "policy_trial": False},
    )


def check_profile_frozen(config: SmallPaperPilotConfig) -> SafetyCheck:
    ok = (
        config.profile == FROZEN_EXIT_PROFILE
        and config.entry_profile == FROZEN_ENTRY_PROFILE
    )
    return SafetyCheck(
        "profile_frozen",
        ok,
        "v13 combined + v2 entry locked" if ok else "profile/entry_profile mismatch",
        {
            "profile": config.profile,
            "entry_profile": config.entry_profile,
            "expected_profile": FROZEN_EXIT_PROFILE,
            "expected_entry": FROZEN_ENTRY_PROFILE,
        },
    )


def check_exposure_guards(config: SmallPaperPilotConfig) -> SafetyCheck:
    ok = config.risk_cluster_block_enabled and config.daily_loss_guard_enabled
    return SafetyCheck(
        "exposure_guards_enabled",
        ok,
        "risk_cluster and daily_loss guards enabled" if ok else "exposure guards disabled",
        {
            "risk_cluster_block_enabled": config.risk_cluster_block_enabled,
            "daily_loss_guard_enabled": config.daily_loss_guard_enabled,
        },
    )


def check_phase43_pass(
    config: SmallPaperPilotConfig,
    *,
    repo_root: Path,
) -> SafetyCheck:
    if not config.require_phase43_pass:
        return SafetyCheck("phase43_pass", True, "require_phase43_pass=false (skipped)", {})
    pattern = config.phase43_diagnosis_glob
    paths = sorted(glob(str(repo_root / pattern)))
    if not paths:
        return SafetyCheck(
            "phase43_pass",
            False,
            "no phase43 diagnosis file found",
            {"glob": pattern},
        )
    latest = Path(paths[-1])
    data = json.loads(latest.read_text(encoding="utf-8"))
    revised = (data.get("revised_candidate_evaluation") or {}).get(
        "move_to_small_paper_candidate"
    )
    reported = data.get("move_to_small_paper_candidate_reported")
    ok = bool(revised)
    return SafetyCheck(
        "phase43_pass",
        ok,
        "revised_candidate=true" if ok else "phase43 revised_candidate not true",
        {
            "diagnosis_path": str(latest),
            "revised_candidate": revised,
            "reported_candidate": reported,
            "recommended_decision": data.get("recommended_decision"),
        },
    )


def check_no_live_order_paths(config: SmallPaperPilotConfig) -> SafetyCheck:
    raw = config.raw
    forbidden_true = []
    for key in ("place_orders", "orders_enabled", "live_trading", "enable_orders"):
        if bool(raw.get(key)):
            forbidden_true.append(key)
    safety = raw.get("safety") or {}
    if isinstance(safety, dict):
        for key in ("place_orders", "order_enabled", "orders_enabled"):
            if bool(safety.get(key)):
                forbidden_true.append(f"safety.{key}")
    ok = not forbidden_true
    return SafetyCheck(
        "no_live_order_modules",
        ok,
        "no order placement flags in config" if ok else f"forbidden flags true: {forbidden_true}",
        {"forbidden_true": forbidden_true},
    )


def check_output_path_writable(
    config: SmallPaperPilotConfig,
    *,
    repo_root: Path,
    day_key: str,
) -> SafetyCheck:
    out = resolve_output_dir(config, repo_root=repo_root, day_key=day_key)
    try:
        out.mkdir(parents=True, exist_ok=True)
        probe = out / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        ok = True
        msg = f"output writable: {out}"
    except OSError as e:
        ok = False
        msg = f"output not writable: {e}"
    return SafetyCheck("output_path_writable", ok, msg, {"output_dir": str(out)})


def check_dry_run_required(config: SmallPaperPilotConfig, *, dry_run_flag: bool) -> SafetyCheck:
    ok = config.dry_run_required and dry_run_flag
    return SafetyCheck(
        "dry_run_required",
        ok,
        "--dry-run flag set" if ok else "dry_run_required in config but --dry-run not passed",
        {"dry_run_required": config.dry_run_required, "dry_run_flag": dry_run_flag},
    )


def check_kabu_password_set() -> SafetyCheck:
    import os

    ok = bool(os.environ.get("KABU_API_PASSWORD", "").strip())
    return SafetyCheck(
        "kabu_api_password",
        ok,
        "KABU_API_PASSWORD is set"
        if ok
        else (
            "KABU_API_PASSWORD missing — load repo-root .env via "
            "load_pilot_environment(repo_root) before safety"
        ),
        {"root_cause": None if ok else "env_not_loaded_or_missing_key"},
    )


def check_no_order_client_import() -> SafetyCheck:
    """Ensure pilot code path does not import legacy order / paper_trade bridges."""
    import small_paper.pilot_runner as pr

    hits = _scan_python_executable_order_refs(Path(pr.__file__))
    ok = not hits
    return SafetyCheck(
        "no_order_client_import",
        ok,
        "pilot_runner has no order client imports" if ok else f"executable order refs: {hits}",
        {"executable_refs": hits},
    )


def check_discord_observer_only(config: SmallPaperPilotConfig) -> SafetyCheck:
    if not config.discord_enabled:
        return SafetyCheck(
            "discord_observer_only",
            True,
            "discord disabled",
            {"discord_enabled": False},
        )
    ok = (
        not config.order_enabled
        and config.paper_only
        and config.discord_observer_only
    )
    return SafetyCheck(
        "discord_observer_only",
        ok,
        "discord observer-only with order_enabled=false"
        if ok
        else "discord requires order_enabled=false, paper_only=true, discord_observer_only=true",
        {
            "order_enabled": config.order_enabled,
            "paper_only": config.paper_only,
            "discord_observer_only": config.discord_observer_only,
        },
    )


def check_discord_notifier_no_orders() -> SafetyCheck:
    import small_paper.discord_notifier as dn

    hits = _scan_python_executable_order_refs(Path(dn.__file__))
    ok = not hits
    return SafetyCheck(
        "discord_notifier_no_orders",
        ok,
        "discord_notifier has no order client imports"
        if ok
        else f"executable order refs in discord_notifier: {hits}",
        {"executable_refs": hits},
    )


_SMALL_PAPER_DISCORD_WEBHOOK_ENV = "KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL"
_SMALL_PAPER_NOTIFY_WEBHOOK_ENV = "KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL"


def check_discord_webhook_env(config: SmallPaperPilotConfig) -> SafetyCheck:
    import os

    if not config.discord_enabled:
        return SafetyCheck("discord_webhook_env", True, "discord disabled", {})
    env_name = (config.discord_webhook_env or _SMALL_PAPER_DISCORD_WEBHOOK_ENV).strip()
    notify_env = (
        getattr(config, "discord_trade_notify_webhook_env", None)
        or _SMALL_PAPER_NOTIFY_WEBHOOK_ENV
    ).strip()
    cfg_ok = env_name == _SMALL_PAPER_DISCORD_WEBHOOK_ENV
    legacy_url = (os.environ.get(env_name) or "").strip()
    notify_url = (os.environ.get(notify_env) or "").strip()
    ok = cfg_ok and bool(legacy_url or notify_url)
    if not cfg_ok:
        msg = (
            f"discord_webhook_env must be {_SMALL_PAPER_DISCORD_WEBHOOK_ENV} "
            f"(got {env_name!r}; do not share KABU_SHADOW / Yahoo webhooks)"
        )
    elif not legacy_url and not notify_url:
        msg = (
            f"{_SMALL_PAPER_DISCORD_WEBHOOK_ENV} or {notify_env} required — "
            "load repo-root .env via load_pilot_environment(repo_root) before safety"
        )
    elif legacy_url and notify_url:
        msg = f"{_SMALL_PAPER_DISCORD_WEBHOOK_ENV} and {notify_env} are set (split channels)"
    elif notify_url:
        msg = (
            f"{notify_env} is set; {env_name} missing — trade notify OK, "
            "HEARTBEAT/HOLD use legacy env or fallback"
        )
    else:
        msg = f"{_SMALL_PAPER_DISCORD_WEBHOOK_ENV} is set (trade notify falls back to same URL)"
    return SafetyCheck(
        "discord_webhook_env",
        ok,
        msg,
        {
            "webhook_env": env_name,
            "trade_notify_webhook_env": notify_env,
            "legacy_set": bool(legacy_url),
            "notify_set": bool(notify_url),
            "expected_env": _SMALL_PAPER_DISCORD_WEBHOOK_ENV,
        },
    )


def check_config_hash_recorded(config_path: Path) -> SafetyCheck:
    from small_paper.config import config_file_sha256

    digest = config_file_sha256(config_path)
    return SafetyCheck(
        "config_hash_available",
        True,
        f"config sha256={digest[:16]}...",
        {"config_path": str(config_path), "config_sha256": digest},
    )


def check_live_output_writable(
    config: SmallPaperPilotConfig,
    *,
    repo_root: Path,
    day_key: str,
    session_stamp: str,
    full_session: bool = False,
) -> SafetyCheck:
    from small_paper.config import resolve_live_full_session_dir, resolve_live_session_dir

    if full_session:
        out = resolve_live_full_session_dir(
            config, repo_root=repo_root, day_key=day_key, session_stamp=session_stamp
        )
        check_id = "live_full_session_output_writable"
    else:
        out = resolve_live_session_dir(
            config, repo_root=repo_root, day_key=day_key, session_stamp=session_stamp
        )
        check_id = "live_output_path_writable"
    try:
        out.mkdir(parents=True, exist_ok=True)
        probe = out / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        ok = True
        msg = f"live session output writable: {out}"
    except OSError as e:
        ok = False
        msg = f"live output not writable: {e}"
    return SafetyCheck(check_id, ok, msg, {"output_dir": str(out)})


def check_kabu_station_connection(repo_root: Path, *, stale_tick_sec: float = 120.0) -> SafetyCheck:
    import os
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from small_paper.pilot_runner import verify_kabu_connection
    from storage.intraday_recorder import parse_kabu_time

    if not os.environ.get("KABU_API_PASSWORD", "").strip():
        return SafetyCheck(
            "kabu_station_connection",
            False,
            (
                "kabu connection not attempted: KABU_API_PASSWORD unset "
                "(likely .env not loaded — use load_pilot_environment before safety)"
            ),
            {"root_cause": "kabu_api_password_missing"},
        )

    jst = ZoneInfo("Asia/Tokyo")
    try:
        conn = verify_kabu_connection(repo_root)
        tick_raw = conn.get("current_price_time")
        stale = False
        age_sec: Optional[float] = None
        if tick_raw:
            tick = parse_kabu_time(tick_raw, fallback=datetime.now(jst))
            age_sec = max(0.0, (datetime.now(jst) - tick).total_seconds())
            stale = age_sec > stale_tick_sec
        msg = "kabu station connection OK"
        if stale:
            msg = f"WARNING: board tick stale ({age_sec:.0f}s > {stale_tick_sec}s)"
        return SafetyCheck(
            "kabu_station_connection",
            True,
            msg,
            {"connection": conn, "stale": stale, "tick_age_sec": age_sec},
        )
    except Exception as e:
        err = str(e)
        root = "kabu_api_password" if "KABU_API_PASSWORD" in err else "kabu_station_unreachable"
        return SafetyCheck(
            "kabu_station_connection",
            False,
            f"kabu connection failed: {e}",
            {"root_cause": root, "error": err},
        )


def check_stale_data_probe(repo_root: Path, *, stale_tick_sec: float = 120.0) -> SafetyCheck:
    chk = check_kabu_station_connection(repo_root, stale_tick_sec=stale_tick_sec)
    stale = bool((chk.details or {}).get("stale"))
    return SafetyCheck(
        "stale_data_probe",
        True,
        chk.message,
        {"stale": stale, **(chk.details or {})},
    )


def check_legacy_paper_trade_warning(repo_root: Path) -> SafetyCheck:
    warnings: list[str] = []
    for base in (repo_root / "results" / "paper_trade",):
        if not base.is_dir():
            continue
        for state_path in base.glob("*/paper_trade_runtime_state.json"):
            try:
                data = json.loads(state_path.read_text(encoding="utf-8"))
                if data.get("running") is True or str(data.get("status", "")).lower() == "running":
                    warnings.append(str(state_path))
            except (OSError, json.JSONDecodeError):
                continue
    try:
        import subprocess

        proc = subprocess.run(
            ["tasklist"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        blob = (proc.stdout or "").lower()
        if "paper_trade" in blob or "yahoo_kabu_watch" in blob:
            warnings.append("tasklist_match")
    except (OSError, subprocess.SubprocessError):
        pass
    warn = bool(warnings)
    return SafetyCheck(
        "legacy_paper_trade_warning",
        True,
        "WARNING: legacy paper_trade may be running" if warn else "no legacy paper_trade detected",
        {"warnings": warnings, "is_warning": warn},
    )


def check_fade_hybrid_shadow_trial_config(config: SmallPaperPilotConfig) -> SafetyCheck:
    from research.fade_hybrid_shadow import POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_HYBRID_SHADOW

    policy = (config.structural_exit_policy or "").strip()
    if policy != POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_HYBRID_SHADOW:
        return SafetyCheck(
            "fade_hybrid_shadow_trial_config",
            True,
            f"structural_exit_policy={policy or 'default'} (fade hybrid checks skipped)",
            {"structural_exit_policy": policy},
        )
    trial_ok = config.policy_trial and (config.policy_label or "").endswith("_trial")
    cap_ok = int(config.max_concurrent_positions) == 3
    order_ok = not config.order_enabled
    paper_ok = config.paper_only
    shadow_ok = bool(getattr(config, "shadow_only", False))
    guard_ok = bool(getattr(config, "entry_price_risk_guard_enabled", False))
    ok = trial_ok and cap_ok and order_ok and paper_ok
    msgs: list[str] = []
    if not trial_ok:
        msgs.append("policy_trial=true and policy_label *_trial required")
    if not cap_ok:
        msgs.append("max_concurrent_positions must be 3")
    if not order_ok:
        msgs.append("order_enabled must be false")
    if not paper_ok:
        msgs.append("paper_only must be true")
    if not shadow_ok:
        msgs.append("shadow_only=true recommended")
    if not guard_ok:
        msgs.append("entry_price_risk_guard_enabled recommended")
    return SafetyCheck(
        "fade_hybrid_shadow_trial_config",
        ok,
        "fade hybrid shadow trial config OK" if ok else "; ".join(msgs),
        {
            "structural_exit_policy": policy,
            "policy_label": config.policy_label,
            "policy_trial": config.policy_trial,
            "shadow_only": shadow_ok,
            "max_concurrent_positions": config.max_concurrent_positions,
            "order_enabled": config.order_enabled,
            "paper_only": config.paper_only,
            "entry_price_risk_guard_enabled": guard_ok,
        },
    )


def check_price_momentum_exit_trial_config(config: SmallPaperPilotConfig) -> SafetyCheck:
    from research.structural_exit_policies import POLICY_COMBINED_STRUCTURAL_EXIT_V2_PRICE_MOM

    policy = (config.structural_exit_policy or "").strip()
    if policy != POLICY_COMBINED_STRUCTURAL_EXIT_V2_PRICE_MOM:
        return SafetyCheck(
            "price_momentum_exit_trial_config",
            True,
            f"structural_exit_policy={policy or 'default'} (v2 price-mom checks skipped)",
            {"structural_exit_policy": policy},
        )
    ratio = float(config.price_momentum_fade_ratio)
    ratio_ok = 0.75 <= ratio <= 0.85
    trial_ok = config.policy_trial and (config.policy_label or "").endswith("_trial")
    order_ok = not config.order_enabled
    paper_ok = config.paper_only
    ok = ratio_ok and trial_ok and order_ok and paper_ok
    msgs = []
    if not trial_ok:
        msgs.append("policy_trial=true and policy_label *_trial required")
    if not ratio_ok:
        msgs.append("price_momentum_fade_ratio must be in [0.75, 0.85]")
    if not order_ok:
        msgs.append("order_enabled must be false")
    if not paper_ok:
        msgs.append("paper_only must be true")
    return SafetyCheck(
        "price_momentum_exit_trial_config",
        ok,
        "price momentum fade trial config OK" if ok else "; ".join(msgs),
        {
            "structural_exit_policy": policy,
            "price_momentum_fade_ratio": ratio,
            "policy_label": config.policy_label,
            "policy_trial": config.policy_trial,
            "order_enabled": config.order_enabled,
            "paper_only": config.paper_only,
        },
    )


def check_symbol_cooloff_trial_config(
    config: SmallPaperPilotConfig,
    *,
    repo_root: Path,
    run_session_key: Optional[str] = None,
) -> SafetyCheck:
    if not config.symbol_cooloff_enabled:
        return SafetyCheck(
            "symbol_cooloff_trial_config",
            True,
            "symbol_cooloff disabled (checks skipped)",
            {"symbol_cooloff_enabled": False},
        )
    msgs: list[str] = []
    if not config.policy_trial or not (config.policy_label or "").endswith("_trial"):
        msgs.append("policy_trial=true and policy_label *_trial required")
    if config.order_enabled:
        msgs.append("order_enabled must be false")
    if not config.paper_only:
        msgs.append("paper_only must be true")
    if config.symbol_cooloff_apply_mode != "reject_entry":
        msgs.append("symbol_cooloff_apply_mode must be reject_entry")
    prior_errors: list[str] = []
    if run_session_key:
        from small_paper.symbol_cooloff import (
            build_symbol_cooloff_state,
            validate_prior_only_sources,
        )

        state = build_symbol_cooloff_state(
            config,
            repo_root=repo_root,
            run_session_key=run_session_key,
        )
        if state is not None:
            prior_errors = validate_prior_only_sources(state, run_session_key=run_session_key)
            if prior_errors:
                msgs.extend(prior_errors)
    ok = not msgs
    return SafetyCheck(
        "symbol_cooloff_trial_config",
        ok,
        "symbol cooloff trial config OK" if ok else "; ".join(msgs),
        {
            "symbol_cooloff_enabled": config.symbol_cooloff_enabled,
            "symbol_cooloff_rule": config.symbol_cooloff_rule,
            "policy_label": config.policy_label,
            "run_session_key": run_session_key,
            "prior_only_errors": prior_errors,
        },
    )


def check_daytrade_suitability_trial_config(
    config: SmallPaperPilotConfig,
    *,
    repo_root: Path,
    run_session_key: Optional[str] = None,
) -> SafetyCheck:
    if not config.daytrade_suitability_enabled:
        return SafetyCheck(
            "daytrade_suitability_trial_config",
            True,
            "daytrade_suitability disabled (checks skipped)",
            {"daytrade_suitability_enabled": False},
        )
    msgs: list[str] = []
    if not config.policy_trial or not (config.policy_label or "").endswith("_trial"):
        msgs.append("policy_trial=true and policy_label *_trial required")
    if config.order_enabled:
        msgs.append("order_enabled must be false")
    if not config.paper_only:
        msgs.append("paper_only must be true")
    if config.daytrade_suitability_apply_mode != "reject_entry":
        msgs.append("daytrade_suitability_apply_mode must be reject_entry")
    if config.daytrade_suitability_rule != "volatility_liquidity_top50":
        msgs.append("daytrade_suitability_rule must be volatility_liquidity_top50")
    prior_errors: list[str] = []
    if run_session_key:
        from small_paper.daytrade_suitability_gate import (
            build_vol_liq_threshold,
            validate_prior_only_sources,
        )

        state = build_vol_liq_threshold(
            config,
            repo_root=repo_root,
            run_session_key=run_session_key,
        )
        if state is not None:
            prior_errors = validate_prior_only_sources(state, run_session_key=run_session_key)
            if prior_errors:
                msgs.extend(prior_errors)
    ok = not msgs
    return SafetyCheck(
        "daytrade_suitability_trial_config",
        ok,
        "vol_liq suitability trial config OK" if ok else "; ".join(msgs),
        {
            "daytrade_suitability_enabled": config.daytrade_suitability_enabled,
            "daytrade_suitability_rule": config.daytrade_suitability_rule,
            "policy_label": config.policy_label,
            "run_session_key": run_session_key,
            "prior_only_errors": prior_errors,
        },
    )


def check_mfe_favorable_trial_config(config: SmallPaperPilotConfig) -> SafetyCheck:
    mode = (config.favorable_mode or "").strip()
    if mode != "mfe_linked":
        return SafetyCheck(
            "mfe_favorable_trial_config",
            True,
            f"favorable_mode={mode or 'default'} (mfe_linked checks skipped)",
            {"favorable_mode": mode},
        )
    scale_ok = config.favorable_mfe_scale > 0
    market_ok = bool(config.use_market_time_window)
    trial_ok = config.policy_trial and (config.policy_label or "").endswith("_trial")
    ok = scale_ok and market_ok and trial_ok
    msgs = []
    if not trial_ok:
        msgs.append("policy_trial=true and policy_label *_trial required")
    if not scale_ok:
        msgs.append("favorable_mfe_scale must be > 0")
    if not market_ok:
        msgs.append("use_market_time_window must be true for mfe_linked trial")
    return SafetyCheck(
        "mfe_favorable_trial_config",
        ok,
        "mfe_linked trial bridge settings OK" if ok else "; ".join(msgs),
        {
            "favorable_mode": mode,
            "favorable_mfe_scale": config.favorable_mfe_scale,
            "use_market_time_window": config.use_market_time_window,
            "policy_label": config.policy_label,
            "policy_trial": config.policy_trial,
        },
    )


def check_kabu_register_capacity(
    *,
    universe_symbol_count: int = 50,
    repo_root: Optional[Path] = None,
    pre_clear: bool = False,
) -> SafetyCheck:
    from api.kabu_register import (
        KABU_PUSH_REGISTER_LIMIT,
        assess_register_capacity,
        clear_register_before_session,
    )

    cap = assess_register_capacity(universe_symbol_count=universe_symbol_count)
    clear_result: dict[str, Any] = {}
    if pre_clear and repo_root is not None:
        clear_result = clear_register_before_session(repo_root)

    within = bool(cap.get("within_limit"))
    msgs: list[str] = []
    if not within:
        msgs.append(
            f"universe {universe_symbol_count} > kabu register limit {KABU_PUSH_REGISTER_LIMIT}"
        )
    if pre_clear and repo_root is not None and not clear_result.get("ok"):
        msgs.append(f"unregister/all pre-clear failed: {clear_result.get('error')}")

    ok = within and (not pre_clear or clear_result.get("ok", True))
    detail = {
        **cap,
        "pre_clear_requested": pre_clear,
        "pre_clear_result": clear_result or None,
    }
    if ok:
        msg = (
            f"register capacity OK ({universe_symbol_count}/{KABU_PUSH_REGISTER_LIMIT})"
            + ("; unregister/all pre-cleared" if clear_result.get("cleared") else "")
        )
    else:
        msg = "; ".join(msgs) if msgs else "register capacity check failed"
    return SafetyCheck("kabu_register_capacity", ok, msg, detail)


def check_no_legacy_yahoo_paper() -> SafetyCheck:
    legacy = "kabu_signal_shadow" in sys.modules or "yahoo_kabu_watch" in sys.modules
    return SafetyCheck(
        "no_legacy_yahoo_paper",
        not legacy,
        "legacy paper modules not loaded" if not legacy else "legacy module already imported",
        {},
    )


def run_all_safety_checks(
    config: SmallPaperPilotConfig,
    *,
    repo_root: Path,
    day_key: str,
    live_mode: bool = False,
    full_session: bool = False,
    dry_run_flag: bool = False,
    config_path: Optional[Path] = None,
    session_stamp: Optional[str] = None,
) -> list[SafetyCheck]:
    checks = [
        check_order_disabled(config),
        check_paper_only(config),
        check_quality_threshold(config),
        check_max_concurrent(config),
        check_trial_policy_label(config),
        check_profile_frozen(config),
        check_exposure_guards(config),
        check_phase43_pass(config, repo_root=repo_root),
        check_no_live_order_paths(config),
        check_dry_run_required(config, dry_run_flag=dry_run_flag),
        check_no_order_client_import(),
        check_discord_observer_only(config),
        check_discord_notifier_no_orders(),
        check_discord_webhook_env(config),
        check_mfe_favorable_trial_config(config),
        check_price_momentum_exit_trial_config(config),
        check_fade_hybrid_shadow_trial_config(config),
        check_symbol_cooloff_trial_config(
            config,
            repo_root=repo_root,
            run_session_key=(
                f"{day_key}/{session_stamp}" if session_stamp else None
            ),
        ),
        check_daytrade_suitability_trial_config(
            config,
            repo_root=repo_root,
            run_session_key=(
                f"{day_key}/{session_stamp}" if session_stamp else None
            ),
        ),
        check_output_path_writable(config, repo_root=repo_root, day_key=day_key),
    ]
    if live_mode or full_session:
        checks.extend(
            [
                check_kabu_password_set(),
                check_kabu_register_capacity(
                    universe_symbol_count=50,
                    repo_root=repo_root,
                    pre_clear=live_mode,
                ),
                check_no_legacy_yahoo_paper(),
                check_kabu_station_connection(
                    repo_root, stale_tick_sec=config.live_stale_tick_sec
                ),
                check_stale_data_probe(repo_root, stale_tick_sec=config.live_stale_tick_sec),
                check_legacy_paper_trade_warning(repo_root),
            ]
        )
        if config_path:
            checks.append(check_config_hash_recorded(config_path))
        if session_stamp:
            checks.append(
                check_live_output_writable(
                    config,
                    repo_root=repo_root,
                    day_key=day_key,
                    session_stamp=session_stamp,
                    full_session=full_session,
                )
            )
    return checks


def load_config_and_check(
    config_path: Path,
    *,
    repo_root: Path,
    day_key: str,
    live_mode: bool = False,
    full_session: bool = False,
    dry_run_flag: bool = False,
    session_stamp: Optional[str] = None,
) -> tuple[SmallPaperPilotConfig, list[SafetyCheck]]:
    cfg = load_pilot_config(config_path)
    checks = run_all_safety_checks(
        cfg,
        repo_root=repo_root,
        day_key=day_key,
        live_mode=live_mode or full_session,
        full_session=full_session,
        dry_run_flag=dry_run_flag,
        config_path=config_path,
        session_stamp=session_stamp,
    )
    return cfg, checks
