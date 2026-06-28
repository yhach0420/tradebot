"""
Research-only vol/liq startup cache (Phase574 shadow).

Does NOT modify Runtime. Provides cache read/write and fallback helpers
that mirror build_vol_liq_threshold() output for equivalence validation.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Literal, Mapping, Optional, Sequence

from small_paper.daytrade_suitability_gate import (
    DaytradeSuitabilityConfig,
    DaytradeSuitabilityState,
    build_vol_liq_threshold,
    discover_sessions_for_suitability_prior,
    prior_vol_liq_scores,
)

CACHE_SCHEMA_VERSION = 1
LoadResult = Literal["cache_hit", "baseline_fallback", "baseline_refresh"]


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


def build_vol_liq_threshold_with_scores(
    pilot_config: Any,
    *,
    repo_root: Path,
    run_session_key: str,
) -> tuple[Optional[DaytradeSuitabilityState], list[float]]:
    """Baseline path returning state + raw prior scores (research only)."""
    enabled = bool(getattr(pilot_config, "daytrade_suitability_enabled", False))
    if not enabled:
        return None, []

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


def state_to_cache_payload(
    state: DaytradeSuitabilityState,
    scores: list[float],
    *,
    config_fp: Mapping[str, Any],
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
        "baseline_elapsed_sec": None,
    }


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
    key = str(payload.get("run_session_key") or "")
    path = cache_path_for_key(cache_dir, key)
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
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None, "cache_corrupt"
    err = validate_cache_payload(payload, run_session_key=run_session_key, config_fp=config_fp)
    if err:
        return None, err
    return payload, None


def build_vol_liq_threshold_cached(
    pilot_config: Any,
    *,
    repo_root: Path,
    run_session_key: str,
    cache_dir: Path,
    allow_refresh: bool = False,
    score_store_dir: Optional[Path] = None,
) -> tuple[Optional[DaytradeSuitabilityState], list[float], LoadResult, float]:
    """
    Cache-aware build. Returns (state, scores, load_result, elapsed_sec).
    On miss/corrupt/invalid: falls back to incremental baseline scan and writes cache.
    """
    t0 = time.perf_counter()
    cfg_fp = config_fingerprint(pilot_config)
    if not cfg_fp["daytrade_suitability_enabled"]:
        return None, [], "baseline_fallback", time.perf_counter() - t0

    payload, err = load_cache_payload(cache_dir, run_session_key=run_session_key, config_fp=cfg_fp)
    if payload is not None and not allow_refresh:
        state = state_from_cache_payload(payload)
        scores = [float(s) for s in payload.get("scores") or []]
        return state, scores, "cache_hit", time.perf_counter() - t0

    store = score_store_dir or (cache_dir.parent / "vol_liq_source_session_scores")
    state, scores = build_vol_liq_threshold_with_scores_incremental(
        pilot_config,
        repo_root=repo_root,
        run_session_key=run_session_key,
        score_store_dir=store,
    )
    if state is not None:
        save_cache_payload(
            cache_dir,
            state_to_cache_payload(state, scores, config_fp=cfg_fp),
        )
    result: LoadResult = "baseline_refresh" if err is None and allow_refresh else "baseline_fallback"
    return state, scores, result, time.perf_counter() - t0


def states_equivalent(
    baseline: Optional[DaytradeSuitabilityState],
    cached: Optional[DaytradeSuitabilityState],
    *,
    scores_baseline: list[float],
    scores_cached: list[float],
) -> dict[str, bool]:
    if baseline is None and cached is None:
        return {
            "threshold_match": True,
            "source_sessions_match": True,
            "prior_count_match": True,
            "scores_match": True,
        }
    if baseline is None or cached is None:
        return {
            "threshold_match": False,
            "source_sessions_match": False,
            "prior_count_match": False,
            "scores_match": False,
        }
    sb = [round(float(x), 12) for x in scores_baseline]
    sc = [round(float(x), 12) for x in scores_cached]
    sb.sort()
    sc.sort()
    return {
        "threshold_match": baseline.vol_liq_threshold == cached.vol_liq_threshold,
        "source_sessions_match": list(baseline.source_sessions) == list(cached.source_sessions),
        "prior_count_match": baseline.prior_quality_trade_count == cached.prior_quality_trade_count,
        "scores_match": sb == sc,
    }


# Re-export baseline for timing comparisons
def prior_vol_liq_scores_incremental(
    source_sessions: Sequence[tuple[str, Path]],
    *,
    repo_root: Path,
    score_store_dir: Path,
) -> tuple[list[float], list[str]]:
    """
    Scan each source session at most once (shared score store).
    Reduces Phase574 full-period validation from O(n^2) to O(n).
    """
    from small_paper.daytrade_suitability_gate import (
        load_session_trades_csv_or_replay,
        push_dir_for_session_key,
    )
    from small_paper.daytrade_suitability import QUALITY_GATE, volatility_liquidity_score
    from small_paper.accepted_liquidity_metrics import load_push_tick_series, lookup_metrics_at_entry

    score_store_dir.mkdir(parents=True, exist_ok=True)
    scores: list[float] = []
    used: list[str] = []

    for session_id, session_dir in source_sessions:
        cache_file = score_store_dir / f"{session_id.replace('/', '__')}.json"
        if cache_file.is_file():
            try:
                payload = json.loads(cache_file.read_text(encoding="utf-8"))
                session_scores = [float(x) for x in payload.get("scores") or []]
            except (json.JSONDecodeError, OSError):
                session_scores = []
        else:
            session_scores = []
            push_dir = push_dir_for_session_key(session_id, repo_root)
            if push_dir is not None and push_dir.is_dir():
                trades = load_session_trades_csv_or_replay(session_dir, repo_root=repo_root)
                symbols = {str(t.get("symbol") or "") for t in trades}
                series = load_push_tick_series(push_dir, symbols)
                for t in trades:
                    q = _float_or_none(t.get("continuation_quality_score")) or 0.0
                    if q < QUALITY_GATE:
                        continue
                    sym = str(t.get("symbol") or "")
                    ent_ts = _parse_ts_iso(str(t.get("entry_time") or ""))
                    m = lookup_metrics_at_entry(series.get(sym, []), ent_ts)
                    vol = volatility_liquidity_score(
                        _float_or_none(m.get("atr_pct")),
                        _float_or_none(m.get("trading_value_jpy")),
                    )
                    if vol is not None:
                        session_scores.append(float(vol))
            cache_file.write_text(
                json.dumps({"session_id": session_id, "scores": session_scores}, ensure_ascii=False),
                encoding="utf-8",
            )
        if session_scores:
            scores.extend(session_scores)
            used.append(session_id)
    return scores, used


def _float_or_none(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _parse_ts_iso(iso: str) -> float:
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def build_vol_liq_threshold_with_scores_incremental(
    pilot_config: Any,
    *,
    repo_root: Path,
    run_session_key: str,
    score_store_dir: Path,
) -> tuple[Optional[DaytradeSuitabilityState], list[float]]:
    """Baseline using shared per-source-session score store."""
    enabled = bool(getattr(pilot_config, "daytrade_suitability_enabled", False))
    if not enabled:
        return None, []

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

    scores, used = prior_vol_liq_scores_incremental(
        sources, repo_root=repo_root, score_store_dir=score_store_dir
    )
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


def build_vol_liq_threshold_baseline(
    pilot_config: Any,
    *,
    repo_root: Path,
    run_session_key: str,
    score_store_dir: Optional[Path] = None,
) -> tuple[Optional[DaytradeSuitabilityState], list[float], float]:
    t0 = time.perf_counter()
    if score_store_dir is not None:
        state, scores = build_vol_liq_threshold_with_scores_incremental(
            pilot_config,
            repo_root=repo_root,
            run_session_key=run_session_key,
            score_store_dir=score_store_dir,
        )
    else:
        state, scores = build_vol_liq_threshold_with_scores(
            pilot_config, repo_root=repo_root, run_session_key=run_session_key
        )
    return state, scores, time.perf_counter() - t0


def build_vol_liq_threshold_runtime_wrapper(
    pilot_config: Any,
    *,
    repo_root: Path,
    run_session_key: str,
) -> Optional[DaytradeSuitabilityState]:
    """Thin wrapper around production entry point (for cross-check)."""
    return build_vol_liq_threshold(
        pilot_config, repo_root=repo_root, run_session_key=run_session_key
    )
