"""
Phase575: Vol/Liq startup cache (production).

Caches build_vol_liq_threshold() output per run_session_key.
Fallback uses the same full prior_vol_liq_scores scan as pre-Phase575.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

from small_paper.daytrade_suitability_gate import (
    DaytradeSuitabilityConfig,
    DaytradeSuitabilityState,
    discover_sessions_for_suitability_prior,
    prior_vol_liq_scores,
)

CACHE_SCHEMA_VERSION = 1

_LAST_METRICS: dict[str, "VolLiqCacheBuildMetrics"] = {}


@dataclass(frozen=True)
class VolLiqCacheBuildMetrics:
    run_session_key: str
    vol_liq_cache_status: str
    vol_liq_cache_hit: bool
    vol_liq_cache_fallback: bool
    vol_liq_cache_fallback_reason: str
    vol_liq_cache_elapsed_sec: float
    vol_liq_cache_baseline_elapsed_sec: float
    vol_liq_cache_seconds_saved: float
    vol_liq_cache_path: str

    def summary_fields(self) -> dict[str, Any]:
        return {
            "vol_liq_cache_status": self.vol_liq_cache_status,
            "vol_liq_cache_hit": self.vol_liq_cache_hit,
            "vol_liq_cache_fallback": self.vol_liq_cache_fallback,
            "vol_liq_cache_fallback_reason": self.vol_liq_cache_fallback_reason,
            "vol_liq_cache_elapsed_sec": round(self.vol_liq_cache_elapsed_sec, 4),
            "vol_liq_cache_baseline_elapsed_sec": round(self.vol_liq_cache_baseline_elapsed_sec, 3),
            "vol_liq_cache_seconds_saved": round(self.vol_liq_cache_seconds_saved, 2),
            "vol_liq_cache_path": self.vol_liq_cache_path,
        }


def cache_enabled(pilot_config: Any) -> bool:
    return bool(getattr(pilot_config, "vol_liq_startup_cache_enabled", False))


def resolve_cache_dir(pilot_config: Any, *, repo_root: Path) -> Path:
    rel = str(
        getattr(
            pilot_config,
            "vol_liq_startup_cache_dir",
            "kabu_native/results/cache/vol_liq_startup",
        )
        or "kabu_native/results/cache/vol_liq_startup"
    )
    p = Path(rel)
    return p if p.is_absolute() else (repo_root / p)


def config_fingerprint(pilot_config: Any) -> dict[str, Any]:
    return {
        "daytrade_suitability_enabled": bool(getattr(pilot_config, "daytrade_suitability_enabled", False)),
        "daytrade_suitability_rule": str(
            getattr(pilot_config, "daytrade_suitability_rule", "volatility_liquidity_top50")
        ),
        "daytrade_suitability_lookback_sessions": str(
            getattr(pilot_config, "daytrade_suitability_lookback_sessions", "prior_only")
        ),
        "daytrade_suitability_apply_mode": str(
            getattr(pilot_config, "daytrade_suitability_apply_mode", "reject_entry")
        ),
    }


def _safe_cache_name(run_session_key: str) -> str:
    return run_session_key.replace("/", "__").replace("\\", "__")


def cache_path_for_key(cache_dir: Path, run_session_key: str) -> Path:
    return cache_dir / f"{_safe_cache_name(run_session_key)}.json"


def scores_checksum(scores: list[float]) -> str:
    payload = json.dumps([round(float(s), 12) for s in scores], sort_keys=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def state_from_cache_payload(payload: Mapping[str, Any]) -> DaytradeSuitabilityState:
    cfg_raw = payload.get("config") or {}
    cfg = DaytradeSuitabilityConfig(
        enabled=bool(cfg_raw.get("enabled", True)),
        rule=str(cfg_raw.get("rule", "volatility_liquidity_top50")),
        lookback_sessions=str(cfg_raw.get("lookback_sessions", "prior_only")),
        apply_mode=str(cfg_raw.get("apply_mode", "reject_entry")),
    )
    th = payload.get("vol_liq_threshold")
    return DaytradeSuitabilityState(
        config=cfg,
        run_session_key=str(payload.get("run_session_key") or ""),
        source_sessions=[str(x) for x in (payload.get("source_sessions") or [])],
        vol_liq_threshold=float(th) if th is not None else None,
        prior_quality_trade_count=int(payload.get("prior_quality_trade_count") or 0),
    )


def state_to_cache_payload(
    state: DaytradeSuitabilityState,
    scores: list[float],
    *,
    config_fp: Mapping[str, Any],
    baseline_elapsed_sec: Optional[float] = None,
) -> dict[str, Any]:
    return {
        "schema_version": CACHE_SCHEMA_VERSION,
        "run_session_key": state.run_session_key,
        "vol_liq_threshold": state.vol_liq_threshold,
        "prior_quality_trade_count": state.prior_quality_trade_count,
        "source_sessions": list(state.source_sessions),
        "scores": [float(s) for s in scores],
        "scores_checksum": scores_checksum(scores),
        "config_fingerprint": dict(config_fp),
        "config": {
            "enabled": state.config.enabled,
            "rule": state.config.rule,
            "lookback_sessions": state.config.lookback_sessions,
            "apply_mode": state.config.apply_mode,
        },
        "built_at_unix": time.time(),
        "baseline_elapsed_sec": baseline_elapsed_sec,
    }


def validate_cache_payload(
    payload: Mapping[str, Any],
    *,
    run_session_key: str,
    config_fp: Mapping[str, Any],
) -> Optional[str]:
    if not isinstance(payload, dict):
        return "payload_not_object"
    if int(payload.get("schema_version") or 0) != CACHE_SCHEMA_VERSION:
        return "schema_version_mismatch"
    if str(payload.get("run_session_key") or "") != run_session_key:
        return "run_session_key_mismatch"
    fp = payload.get("config_fingerprint") or {}
    for k, v in config_fp.items():
        if fp.get(k) != v:
            return f"config_fingerprint_mismatch:{k}"
    scores = payload.get("scores")
    if not isinstance(scores, list):
        return "scores_not_list"
    if str(payload.get("scores_checksum") or "") != scores_checksum([float(s) for s in scores]):
        return "scores_checksum_mismatch"
    return None


def save_cache_payload(cache_dir: Path, payload: Mapping[str, Any]) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_path_for_key(cache_dir, str(payload.get("run_session_key") or ""))
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_cache_payload(
    cache_dir: Path,
    *,
    run_session_key: str,
    config_fp: Mapping[str, Any],
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    path = cache_path_for_key(cache_dir, run_session_key)
    if not path.is_file():
        return None, "cache_missing"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, "cache_corrupt"
    err = validate_cache_payload(payload, run_session_key=run_session_key, config_fp=config_fp)
    if err:
        return None, err
    return payload, None


def build_vol_liq_threshold_full_scan_with_scores(
    pilot_config: Any,
    *,
    repo_root: Path,
    run_session_key: str,
) -> tuple[Optional[DaytradeSuitabilityState], list[float]]:
    from small_paper.daytrade_suitability_gate import LOOKBACK_PRIOR_ONLY, RULE_VOLATILITY_LIQUIDITY_TOP50

    cfg = DaytradeSuitabilityConfig(
        enabled=True,
        rule=str(getattr(pilot_config, "daytrade_suitability_rule", RULE_VOLATILITY_LIQUIDITY_TOP50)),
        lookback_sessions=str(
            getattr(pilot_config, "daytrade_suitability_lookback_sessions", LOOKBACK_PRIOR_ONLY)
        ),
        apply_mode=str(getattr(pilot_config, "daytrade_suitability_apply_mode", "reject_entry")),
    )

    base = repo_root / "kabu_native" / "results" / "small_paper"
    sources = discover_sessions_for_suitability_prior(base, before_session_key=run_session_key)
    if cfg.lookback_sessions != "all_available":
        try:
            n = int(cfg.lookback_sessions)
            sources = sources[-n:]
        except ValueError:
            pass

    scores, used = prior_vol_liq_scores(sources, repo_root=repo_root)
    threshold: Optional[float] = None
    if scores and cfg.rule == RULE_VOLATILITY_LIQUIDITY_TOP50:
        from small_paper.daytrade_suitability import percentile_value

        threshold = percentile_value(scores, 0.50)

    state = DaytradeSuitabilityState(
        config=cfg,
        run_session_key=run_session_key,
        source_sessions=list(used),
        vol_liq_threshold=round(threshold, 6) if threshold is not None else None,
        prior_quality_trade_count=len(scores),
    )
    return state, scores


def _record_metrics(metrics: VolLiqCacheBuildMetrics) -> None:
    _LAST_METRICS[metrics.run_session_key] = metrics


def get_vol_liq_cache_metrics(run_session_key: Optional[str] = None) -> Optional[VolLiqCacheBuildMetrics]:
    if run_session_key:
        return _LAST_METRICS.get(run_session_key)
    if not _LAST_METRICS:
        return None
    return next(reversed(_LAST_METRICS.values()))


def vol_liq_cache_summary_fields(run_session_key: Optional[str] = None) -> dict[str, Any]:
    m = get_vol_liq_cache_metrics(run_session_key)
    return m.summary_fields() if m else {}


def build_vol_liq_threshold_with_startup_cache(
    pilot_config: Any,
    *,
    repo_root: Path,
    run_session_key: str,
) -> Optional[DaytradeSuitabilityState]:
    """Production entry: cache lookup with full-scan fallback."""
    if not bool(getattr(pilot_config, "daytrade_suitability_enabled", False)):
        return None

    t0 = time.perf_counter()
    cache_dir = resolve_cache_dir(pilot_config, repo_root=repo_root)
    cfg_fp = config_fingerprint(pilot_config)
    cache_path = cache_path_for_key(cache_dir, run_session_key)

    if not cache_enabled(pilot_config):
        baseline_t0 = time.perf_counter()
        state, _scores = build_vol_liq_threshold_full_scan_with_scores(
            pilot_config, repo_root=repo_root, run_session_key=run_session_key
        )
        baseline_sec = time.perf_counter() - baseline_t0
        _record_metrics(
            VolLiqCacheBuildMetrics(
                run_session_key=run_session_key,
                vol_liq_cache_status="cache_disabled",
                vol_liq_cache_hit=False,
                vol_liq_cache_fallback=True,
                vol_liq_cache_fallback_reason="cache_disabled",
                vol_liq_cache_elapsed_sec=time.perf_counter() - t0,
                vol_liq_cache_baseline_elapsed_sec=baseline_sec,
                vol_liq_cache_seconds_saved=0.0,
                vol_liq_cache_path=str(cache_path),
            )
        )
        return state

    payload, err = load_cache_payload(
        cache_dir, run_session_key=run_session_key, config_fp=cfg_fp
    )
    if payload is not None:
        elapsed = time.perf_counter() - t0
        baseline_hint = float(payload.get("baseline_elapsed_sec") or 0.0)
        saved = max(0.0, baseline_hint - elapsed) if baseline_hint > 0 else 0.0
        _record_metrics(
            VolLiqCacheBuildMetrics(
                run_session_key=run_session_key,
                vol_liq_cache_status="cache_hit",
                vol_liq_cache_hit=True,
                vol_liq_cache_fallback=False,
                vol_liq_cache_fallback_reason="",
                vol_liq_cache_elapsed_sec=elapsed,
                vol_liq_cache_baseline_elapsed_sec=baseline_hint,
                vol_liq_cache_seconds_saved=saved,
                vol_liq_cache_path=str(cache_path),
            )
        )
        return state_from_cache_payload(payload)

    fallback_on_error = bool(
        getattr(pilot_config, "vol_liq_startup_cache_fallback_on_error", True)
    )
    if not fallback_on_error:
        raise RuntimeError(f"vol_liq startup cache miss and fallback disabled: {err}")

    baseline_t0 = time.perf_counter()
    state, scores = build_vol_liq_threshold_full_scan_with_scores(
        pilot_config, repo_root=repo_root, run_session_key=run_session_key
    )
    baseline_sec = time.perf_counter() - baseline_t0

    write_after = bool(getattr(pilot_config, "vol_liq_startup_cache_write_after_fallback", True))
    if write_after and state is not None:
        save_cache_payload(
            cache_dir,
            state_to_cache_payload(
                state,
                scores,
                config_fp=cfg_fp,
                baseline_elapsed_sec=baseline_sec,
            ),
        )

    elapsed = time.perf_counter() - t0
    _record_metrics(
        VolLiqCacheBuildMetrics(
            run_session_key=run_session_key,
            vol_liq_cache_status="baseline_fallback",
            vol_liq_cache_hit=False,
            vol_liq_cache_fallback=True,
            vol_liq_cache_fallback_reason=str(err or "cache_miss"),
            vol_liq_cache_elapsed_sec=elapsed,
            vol_liq_cache_baseline_elapsed_sec=baseline_sec,
            vol_liq_cache_seconds_saved=0.0,
            vol_liq_cache_path=str(cache_path),
        )
    )
    return state
