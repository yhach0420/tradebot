"""Phase575: production vol/liq startup cache tests."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from small_paper.config import SmallPaperPilotConfig
from small_paper.daytrade_suitability_gate import (
    DaytradeSuitabilityConfig,
    DaytradeSuitabilityState,
    build_vol_liq_threshold,
)
from small_paper.vol_liq_startup_cache import (
    build_vol_liq_threshold_full_scan_with_scores,
    build_vol_liq_threshold_with_startup_cache,
    cache_path_for_key,
    get_vol_liq_cache_metrics,
    resolve_cache_dir,
    save_cache_payload,
    scores_checksum,
    state_to_cache_payload,
    config_fingerprint,
)


def _cfg(**kw) -> SmallPaperPilotConfig:
    base = SmallPaperPilotConfig(
        daytrade_suitability_enabled=True,
        vol_liq_startup_cache_enabled=True,
        vol_liq_startup_cache_dir="kabu_native/results/cache/vol_liq_startup",
    )
    return replace(base, **kw)


def _state(run_key: str = "20260625/live_session_080340") -> DaytradeSuitabilityState:
    return DaytradeSuitabilityState(
        config=DaytradeSuitabilityConfig(enabled=True),
        run_session_key=run_key,
        source_sessions=["20260624/live_session_080340"],
        vol_liq_threshold=54.695739,
        prior_quality_trade_count=3,
    )


def test_cache_hit_returns_same_threshold(tmp_path: Path) -> None:
    repo = tmp_path
    run_key = "20260625/live_session_080340"
    cfg = _cfg()
    cache_dir = resolve_cache_dir(cfg, repo_root=repo)
    scores = [10.0, 20.0, 54.695739]
    state = _state(run_key)
    save_cache_payload(
        cache_dir,
        state_to_cache_payload(state, scores, config_fp=config_fingerprint(cfg), baseline_elapsed_sec=900.0),
    )

    out = build_vol_liq_threshold_with_startup_cache(cfg, repo_root=repo, run_session_key=run_key)
    assert out is not None
    assert out.vol_liq_threshold == 54.695739
    m = get_vol_liq_cache_metrics(run_key)
    assert m is not None
    assert m.vol_liq_cache_hit is True
    assert m.vol_liq_cache_fallback is False


def test_cache_missing_falls_back_and_writes(tmp_path: Path) -> None:
    repo = tmp_path
    run_key = "20260625/live_session_080340"
    cfg = _cfg()
    state = _state(run_key)
    scores = [1.0, 2.0, 3.0]

    with patch(
        "small_paper.vol_liq_startup_cache.build_vol_liq_threshold_full_scan_with_scores",
        return_value=(state, scores),
    ) as mock_scan:
        out = build_vol_liq_threshold_with_startup_cache(cfg, repo_root=repo, run_session_key=run_key)
        mock_scan.assert_called_once()

    assert out is not None
    assert out.vol_liq_threshold == 54.695739
    cache_dir = resolve_cache_dir(cfg, repo_root=repo)
    assert cache_path_for_key(cache_dir, run_key).is_file()
    m = get_vol_liq_cache_metrics(run_key)
    assert m is not None
    assert m.vol_liq_cache_fallback is True
    assert m.vol_liq_cache_fallback_reason == "cache_missing"


def test_cache_corrupt_falls_back(tmp_path: Path) -> None:
    repo = tmp_path
    run_key = "20260625/live_session_080340"
    cfg = _cfg()
    cache_dir = resolve_cache_dir(cfg, repo_root=repo)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path_for_key(cache_dir, run_key).write_text("{bad", encoding="utf-8")
    state = _state(run_key)

    with patch(
        "small_paper.vol_liq_startup_cache.build_vol_liq_threshold_full_scan_with_scores",
        return_value=(state, [1.0]),
    ):
        out = build_vol_liq_threshold_with_startup_cache(cfg, repo_root=repo, run_session_key=run_key)

    assert out is not None
    m = get_vol_liq_cache_metrics(run_key)
    assert m is not None
    assert m.vol_liq_cache_fallback_reason == "cache_corrupt"


def test_cache_wrong_run_key_falls_back(tmp_path: Path) -> None:
    repo = tmp_path
    run_key = "20260625/live_session_080340"
    cfg = _cfg()
    cache_dir = resolve_cache_dir(cfg, repo_root=repo)
    scores = [1.0, 2.0]
    payload = state_to_cache_payload(_state("20991231/live_session_000000"), scores, config_fp=config_fingerprint(cfg))
    payload["run_session_key"] = "20991231/live_session_000000"
    path = cache_path_for_key(cache_dir, run_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")

    with patch(
        "small_paper.vol_liq_startup_cache.build_vol_liq_threshold_full_scan_with_scores",
        return_value=(_state(run_key), scores),
    ):
        out = build_vol_liq_threshold_with_startup_cache(cfg, repo_root=repo, run_session_key=run_key)

    assert out is not None
    m = get_vol_liq_cache_metrics(run_key)
    assert m is not None
    assert m.vol_liq_cache_fallback_reason == "run_session_key_mismatch"


def test_cache_disabled_uses_full_scan(tmp_path: Path) -> None:
    repo = tmp_path
    run_key = "20260625/live_session_080341"
    cfg = _cfg(vol_liq_startup_cache_enabled=False)
    state = _state(run_key)

    with patch(
        "small_paper.vol_liq_startup_cache.prior_vol_liq_scores",
        return_value=([54.0, 55.0], ["20260624/live_session_080340"]),
    ):
        with patch(
            "small_paper.vol_liq_startup_cache.discover_sessions_for_suitability_prior",
            return_value=[("20260624/live_session_080340", tmp_path)],
        ):
            out = build_vol_liq_threshold(cfg, repo_root=repo, run_session_key=run_key)

    assert out is not None
    m = get_vol_liq_cache_metrics(run_key)
    assert m is not None
    assert m.vol_liq_cache_status == "cache_disabled"


def test_build_vol_liq_threshold_routes_to_cache_when_enabled(tmp_path: Path) -> None:
    cfg = _cfg()
    state = _state()
    with patch(
        "small_paper.vol_liq_startup_cache.build_vol_liq_threshold_with_startup_cache",
        return_value=state,
    ) as mock_cached:
        out = build_vol_liq_threshold(cfg, repo_root=tmp_path, run_session_key=state.run_session_key)
    mock_cached.assert_called_once()
    assert out is state


def test_checksum_detects_tampering() -> None:
    scores = [1.1, 2.2]
    payload = scores_checksum(scores)
    assert payload != scores_checksum([1.1, 2.3])
