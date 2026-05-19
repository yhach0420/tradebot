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
    load_config_and_check,
)

JST = ZoneInfo("Asia/Tokyo")

EXPECTED_POLICY_LABEL = "q070_cap3_trial"
EXPECTED_MFE_FAV_POLICY_LABEL = "q070_cap3_mfe_fav_trial"
EXPECTED_MIN_QUALITY = 0.70
EXPECTED_MAX_CONCURRENT = 3
MIN_PHASE54_PF = 1.20
MIN_PHASE60_STRUCTURAL_PF = 1.20
DEFAULT_PHASE54_SESSION_REL = (
    "kabu_native/results/small_paper/20260518/push_replay_220451"
)
DEFAULT_PHASE60_STRUCTURAL_SESSION_REL = (
    "kabu_native/results/small_paper/20260519/live_full_session_081047"
)
EXPECTED_STRUCTURAL_EXIT_POLICY = "combined_structural_exit_v1"


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


def check_phase67_mfe_fav_config(config: SmallPaperPilotConfig) -> ReadinessCheck:
    ok = (
        config.policy_label == EXPECTED_MFE_FAV_POLICY_LABEL
        and config.policy_trial
        and config.baseline_policy == "q070_cap3_trial"
        and abs(config.min_continuation_quality - EXPECTED_MIN_QUALITY) < 1e-6
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
        config.policy_label == EXPECTED_POLICY_LABEL
        and config.policy_trial
        and abs(config.min_continuation_quality - EXPECTED_MIN_QUALITY) < 1e-6
        and config.max_concurrent_positions == EXPECTED_MAX_CONCURRENT
    )
    return ReadinessCheck(
        "phase51_q070_cap3_config",
        ok,
        "q070_cap3 trial config OK"
        if ok
        else f"expected {EXPECTED_POLICY_LABEL} q={EXPECTED_MIN_QUALITY} cap={EXPECTED_MAX_CONCURRENT}",
        {
            "policy_label": config.policy_label,
            "policy_trial": config.policy_trial,
            "min_continuation_quality": config.min_continuation_quality,
            "max_concurrent_positions": config.max_concurrent_positions,
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


def check_phase60_combined_structural_pass(
    structural_session_dir: Path,
    *,
    config: SmallPaperPilotConfig,
) -> ReadinessCheck:
    path = structural_session_dir / "structural_observer_review.json"
    data = _load_json(path)
    policy = str(data.get("structural_exit_policy") or data.get("policy") or "")
    verdict = str(data.get("official_verdict") or "")
    pf = data.get("structural_pf")
    pf_val = float(pf) if isinstance(pf, (int, float)) else 0.0
    cfg_ok = (
        config.structural_exit_policy == EXPECTED_STRUCTURAL_EXIT_POLICY
        or policy == EXPECTED_STRUCTURAL_EXIT_POLICY
    )
    ok = (
        bool(data)
        and cfg_ok
        and verdict == "structural_pass"
        and round(pf_val, 2) >= MIN_PHASE60_STRUCTURAL_PF
    )
    return ReadinessCheck(
        "phase60_combined_structural_pass",
        ok,
        f"combined_structural_exit_v1 official_verdict={verdict} PF={pf_val}"
        if ok
        else f"missing review or verdict={verdict} PF={pf} policy={policy}",
        {
            "structural_exit_policy": policy or config.structural_exit_policy,
            "official_verdict": verdict,
            "structural_pf": pf,
            "structural_avg_pnl": data.get("structural_avg_pnl"),
            "reference": str(structural_session_dir),
        },
    )


def live_observer_retrial_summary_fields(
    config: SmallPaperPilotConfig,
    *,
    reference_session_dir: Optional[Path] = None,
    structural_session_dir: Optional[Path] = None,
) -> dict[str, Any]:
    pf = phase54_reference_pf(reference_session_dir) if reference_session_dir else None
    struct_data = (
        _load_json(structural_session_dir / "structural_observer_review.json")
        if structural_session_dir
        else {}
    )
    return {
        "live_observer_retrial_phase": 61,
        "runtime_policy": config.policy_label,
        "exit_policy": config.structural_exit_policy or EXPECTED_STRUCTURAL_EXIT_POLICY,
        "structural_exit_policy": config.structural_exit_policy or EXPECTED_STRUCTURAL_EXIT_POLICY,
        "observer_exit_mode": "combined_structural_exit_notification_only",
        "take_is_observer_only": True,
        "allowed_trading_windows": windows_summary(config.allowed_windows()),
        "phase54_reference_pf": pf,
        "phase54_reference_session": str(reference_session_dir) if reference_session_dir else None,
        "phase60_structural_pf": struct_data.get("structural_pf"),
        "phase60_official_verdict": struct_data.get("official_verdict"),
        "phase60_structural_session": str(structural_session_dir) if structural_session_dir else None,
        "phase54_take_note": "TAKE is observer signal only; combined structural rules may EXIT notify",
        "post_session_review_cmd": (
            "python kabu_native/scripts/review_structural_observer.py "
            "--structural-exit-policy combined_structural_exit_v1"
        ),
    }


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
    policy_checks: list[ReadinessCheck] = (
        [check_phase67_mfe_fav_config(config), check_mfe_favorable_trial_readiness(config)]
        if config.policy_label == EXPECTED_MFE_FAV_POLICY_LABEL
        else [check_phase51_config(config)]
    )
    checks: list[ReadinessCheck] = [
        *policy_checks,
        check_phase52_allowed_windows(config),
        check_phase53_cap_not_recommended(reference_session_dir),
        check_phase54_baseline_pf(reference_session_dir),
        check_phase60_combined_structural_pass(struct_dir, config=config),
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

    return {
        "phase": 61,
        "component": "live_observer_readiness",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "readiness": ready,
        "ready_for_live_observer_retrial": ready,
        "config_path": str(config_path),
        "reference_session_dir": str(reference_session_dir),
        "structural_session_dir": str(struct_dir),
        "retrial_policy": live_observer_retrial_summary_fields(
            config,
            reference_session_dir=reference_session_dir,
            structural_session_dir=struct_dir,
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
