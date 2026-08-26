"""Canonical run-classification SoT (operational validity vs strategy eligibility).

Do not infer solely from activation ID string when a manifest/classification
field can be propagated. VALID_SESSION remains operational/runtime validity.
"""
from __future__ import annotations

import os
from typing import Any, Mapping, Optional

NORMAL_PROSPECTIVE_PAPER = "NORMAL_PROSPECTIVE_PAPER"
OPERATIONAL_VALIDATION_ONLY = "OPERATIONAL_VALIDATION_ONLY"
DEGRADED_UNIVERSE = "DEGRADED_UNIVERSE"
CERTIFICATION = "CERTIFICATION"
REPLAY = "REPLAY"

_TRUE = {"1", "true", "yes", "on"}

_STRATEGY_EXCLUDED = frozenset(
    {
        OPERATIONAL_VALIDATION_ONLY,
        DEGRADED_UNIVERSE,
        CERTIFICATION,
        REPLAY,
    }
)


def _flag(name: str, *, environ: Optional[Mapping[str, str]] = None) -> bool:
    env = environ if environ is not None else os.environ
    return str(env.get(name, "") or "").strip().lower() in _TRUE


def _as_bool(value: Any) -> Optional[bool]:
    if value is True:
        return True
    if value is False:
        return False
    if isinstance(value, str) and value.strip().lower() in _TRUE:
        return True
    if isinstance(value, str) and value.strip().lower() in {"0", "false", "no", "off"}:
        return False
    return None


def strategy_evaluation_eligible_for(run_class: str) -> bool:
    return str(run_class or "") == NORMAL_PROSPECTIVE_PAPER


def inclusion_flags(eligible: bool) -> dict[str, bool]:
    return {
        "strategy_evaluation_eligible": bool(eligible),
        "include_in_strategy_metrics": bool(eligible),
        "include_in_cumulative_pnl": bool(eligible),
        "include_in_forward_day_count": bool(eligible),
        "include_in_live_readiness_streak": bool(eligible),
    }


def resolve_run_classification(
    summary: Optional[Mapping[str, Any]] = None,
    *,
    environ: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    """Resolve canonical run class from manifest/summary/env — not ID-string matching first."""
    s = dict(summary or {})
    env = dict(environ) if environ is not None else dict(os.environ)
    source = "default_normal_prospective_paper"
    run_class = NORMAL_PROSPECTIVE_PAPER

    explicit = str(
        s.get("run_classification")
        or s.get("paper_mode")
        or s.get("canonical_run_classification")
        or ""
    ).strip()
    if explicit in _STRATEGY_EXCLUDED or explicit == NORMAL_PROSPECTIVE_PAPER:
        run_class = explicit
        source = "summary.run_classification_or_paper_mode"

    if source == "default_normal_prospective_paper":
        allowed = _as_bool(s.get("strategy_evaluation_allowed"))
        invalid_eval = _as_bool(s.get("INVALID_FOR_STRATEGY_EVALUATION"))
        if allowed is False or invalid_eval is True:
            run_class = OPERATIONAL_VALIDATION_ONLY
            source = "summary.strategy_evaluation_allowed_or_INVALID_FOR_STRATEGY_EVALUATION"

    if source == "default_normal_prospective_paper":
        degraded_hint = bool(str(s.get("degraded_universe") or "").strip()) or int(
            s.get("watch_symbols_count") or 0
        ) == 49
        try:
            from small_paper.operational_validation import (
                ENV_OPVAL_MODE,
                operational_validation_mode,
                opval_degraded_universe_mode,
            )

            if opval_degraded_universe_mode(environ=env) or (
                operational_validation_mode(environ=env) and degraded_hint
            ):
                run_class = DEGRADED_UNIVERSE
                source = "env_or_summary_degraded_universe"
            elif operational_validation_mode(environ=env):
                run_class = OPERATIONAL_VALIDATION_ONLY
                source = f"env.{ENV_OPVAL_MODE}"
        except Exception:
            if _flag("TRADEBOT_OPVAL_DEGRADED_UNIVERSE_ONLY", environ=env) or (
                _flag("TRADEBOT_OPERATIONAL_VALIDATION_MODE", environ=env) and degraded_hint
            ):
                run_class = DEGRADED_UNIVERSE
                source = "env_or_summary_degraded_universe"
            elif _flag("TRADEBOT_OPERATIONAL_VALIDATION_MODE", environ=env):
                run_class = OPERATIONAL_VALIDATION_ONLY
                source = "env.TRADEBOT_OPERATIONAL_VALIDATION_MODE"

    if source.startswith("env") or source.startswith("summary"):
        pass
    elif run_class == NORMAL_PROSPECTIVE_PAPER:
        try:
            from small_paper.runtime_clock import (
                MARKET_INPUT_REPLAY,
                MARKET_INPUT_SYNTHETIC,
                certification_mode,
                market_input_mode,
                session_clock_enabled,
            )

            mode = str(s.get("market_input_mode") or market_input_mode(environ=env) or "").strip().upper()
            if certification_mode(environ=env) or session_clock_enabled(environ=env):
                run_class = CERTIFICATION
                source = "certification_or_session_clock"
            elif mode in {MARKET_INPUT_REPLAY, MARKET_INPUT_SYNTHETIC, "REPLAY", "SYNTHETIC"}:
                run_class = REPLAY
                source = "market_input_mode_replay_or_synthetic"
        except Exception:
            mode = str(s.get("market_input_mode") or env.get("MARKET_INPUT_MODE") or "").strip().upper()
            if _flag("TRADEBOT_CERTIFICATION_MODE", environ=env) or _flag(
                "TRADEBOT_SESSION_CLOCK", environ=env
            ):
                run_class = CERTIFICATION
                source = "certification_or_session_clock"
            elif mode in {"REPLAY", "SYNTHETIC"}:
                run_class = REPLAY
                source = "market_input_mode_replay_or_synthetic"

    # Last-resort: OPVAL activation identity (only if no proper field already won).
    if run_class == NORMAL_PROSPECTIVE_PAPER:
        aid = str(s.get("activation_id") or env.get("TRADEBOT_ACTIVATION_ID") or "").strip()
        if "OPVAL" in aid.upper() or aid.endswith("_OPVAL_CURRENT_TRADING_DAY"):
            run_class = OPERATIONAL_VALIDATION_ONLY
            source = "fallback_activation_id_opval"

    if int(s.get("watch_symbols_count") or 0) == 49 and run_class == OPERATIONAL_VALIDATION_ONLY:
        run_class = DEGRADED_UNIVERSE
        source = f"{source}+watch_symbols_count_49"

    if str(s.get("degraded_universe") or "").strip() and run_class in {
        OPERATIONAL_VALIDATION_ONLY,
        NORMAL_PROSPECTIVE_PAPER,
    }:
        run_class = DEGRADED_UNIVERSE
        source = "summary.degraded_universe"

    eligible = strategy_evaluation_eligible_for(run_class)
    flags = inclusion_flags(eligible)
    return {
        "run_classification": run_class,
        "run_classification_source": source,
        **flags,
    }


def stamp_run_classification(
    summary: dict[str, Any],
    *,
    environ: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    body = resolve_run_classification(summary, environ=environ)
    summary["run_classification"] = body["run_classification"]
    summary["run_classification_source"] = body["run_classification_source"]
    summary["strategy_evaluation_eligible"] = body["strategy_evaluation_eligible"]
    summary.setdefault("paper_mode", body["run_classification"])
    if body["run_classification"] in _STRATEGY_EXCLUDED:
        summary["INVALID_FOR_STRATEGY_EVALUATION"] = True
        summary["strategy_evaluation_allowed"] = False
        if body["run_classification"] == OPERATIONAL_VALIDATION_ONLY:
            summary["NOT_PROSPECTIVE_DAY1"] = True
    return body
