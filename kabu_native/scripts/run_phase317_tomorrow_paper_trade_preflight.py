#!/usr/bin/env python3
"""Phase317: Tomorrow paper-trade preflight (ENTRY/EXIT/Discord/Kabu/refresh)."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
REPO = Path(__file__).resolve().parents[2]
NATIVE = REPO / "kabu_native"
SRC = NATIVE / "src"
REPORTS = NATIVE / "results" / "reports"
DEFAULT_DAY = "20260608"
DEFAULT_CONFIG_REL = (
    "kabu_native/configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
)
OUT_NAME = "phase317_tomorrow_paper_trade_preflight.json"


@dataclass
class PreflightCheck:
    check_id: str
    passed: bool
    message: str
    details: dict[str, Any] = field(default_factory=dict)


def _bootstrap() -> None:
    for p in (SRC, REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _to_dict(c: PreflightCheck) -> dict[str, Any]:
    return {
        "check_id": c.check_id,
        "passed": c.passed,
        "message": c.message,
        "details": c.details,
    }


def _py_compile() -> PreflightCheck:
    targets = [
        SRC / "small_paper/entry_expectancy_score_shadow.py",
        SRC / "research/exposure_gate.py",
        SRC / "small_paper/discord_message_builder.py",
        SRC / "notify/discord.py",
        SRC / "replay/pnl_yen.py",
        SRC / "runner/am_pm_daily_runner.py",
    ]
    errors: list[str] = []
    for t in targets:
        r = subprocess.run(
            [sys.executable, "-m", "py_compile", str(t)],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            errors.append(f"{t}: {r.stderr.strip()}")
    ok = not errors
    return PreflightCheck(
        "py_compile",
        ok,
        "py_compile OK" if ok else f"py_compile failed: {len(errors)}",
        {"errors": errors},
    )


def _check_phase314_entry() -> PreflightCheck:
    from research.exposure_gate import (
        REJECT_ENTRY_SCORE_V2_BELOW,
        REJECT_MOMENTUM_LOW_REQUIRED,
        ExposureGate,
        ExposureGateConfig,
    )
    from small_paper.entry_expectancy_score_shadow import (
        ENTRY_SCORE_V2_GATE_MIN,
        REQUIRED_V2_TOKENS,
        SCORE_POINTS_V2,
        active_score_tokens_v2,
        compute_entry_expectancy_score_fields,
    )

    full_trade = {
        "momentum_continuation_score": 0.20,
        "entry_order_book_imbalance": 0.50,
    }
    momentum_only = {
        "momentum_continuation_score": 0.20,
        "entry_order_book_imbalance": 0.40,
    }
    no_momentum = {
        "momentum_continuation_score": 0.35,
        "entry_order_book_imbalance": 0.50,
    }
    fields_full = compute_entry_expectancy_score_fields(trade=full_trade)
    active = active_score_tokens_v2(full_trade)

    gate = ExposureGate(ExposureGateConfig(entry_score_v2_min=ENTRY_SCORE_V2_GATE_MIN))
    base = {
        "profile": "momentum_volume_v13_combined",
        "symbol": "9984.T",
        "entry_time": "2026-06-08T09:30:00+09:00",
        "exit_time": "2026-06-08T10:00:00+09:00",
        "trade_date": "2026-06-08",
        "continuation_quality_score": 0.45,
    }
    d_pass = gate.evaluate_entry({**base, **full_trade})
    d_mom_only = gate.evaluate_entry({**base, **momentum_only})
    d_no_mom = gate.evaluate_entry({**base, **no_momentum})

    score_ok = set(SCORE_POINTS_V2.keys()) == {"Momentum:low", "Board:mid"}
    removed_absent = not any(
        t.startswith(("Duration:", "Price:", "TV:", "HBRecent:")) for t in active
    )
    matrix_ok = (
        d_pass.accept
        and d_mom_only.reason == REJECT_ENTRY_SCORE_V2_BELOW
        and d_no_mom.reason == REJECT_MOMENTUM_LOW_REQUIRED
    )
    ok = (
        score_ok
        and sum(SCORE_POINTS_V2.values()) == 3
        and ENTRY_SCORE_V2_GATE_MIN == 3
        and REQUIRED_V2_TOKENS == frozenset({"Momentum:low"})
        and removed_absent
        and matrix_ok
        and fields_full["entry_expectancy_score_v2"] == 3
    )
    return PreflightCheck(
        "phase314_final_entry_conditions",
        ok,
        "Phase314 Momentum+Board min=3 reflected"
        if ok
        else "Phase314 entry score check failed",
        {
            "SCORE_POINTS_V2": dict(SCORE_POINTS_V2),
            "REQUIRED_V2_TOKENS": sorted(REQUIRED_V2_TOKENS),
            "ENTRY_SCORE_V2_GATE_MIN": ENTRY_SCORE_V2_GATE_MIN,
            "active_score_tokens_sample": active,
            "gate_pass": d_pass.accept,
            "gate_momentum_only_reason": d_mom_only.reason,
            "gate_no_momentum_reason": d_no_mom.reason,
        },
    )


def _check_phase316_exit_discord() -> PreflightCheck:
    from replay.pnl_yen import format_exit_pnl_line
    from small_paper.discord_message_builder import build_exit_detail

    detail = build_exit_detail(
        symbol="3905.T",
        entry_price=2857.0,
        exit_price=2869.0,
        pnl_pct=0.42,
        mfe_pct=0.8,
        mae_pct=-0.2,
        hold_minutes=12.0,
        exit_reason="trailing_mfe_exit",
        pnl_yen_100=1200.0,
    )
    example = format_exit_pnl_line(0.42, 1200.0)
    ok = "損益: +0.42% / +1,200円(100株)" in detail and example == "損益: +0.42% / +1,200円(100株)"
    return PreflightCheck(
        "phase316_exit_discord_yen_display",
        ok,
        "Phase316 EXIT yen display reflected" if ok else "Phase316 EXIT display missing yen",
        {"example_line": example, "detail_excerpt": detail.splitlines()[3] if detail else ""},
    )


def _check_config_entry_min(config_path: Path) -> PreflightCheck:
    from small_paper.config import load_pilot_config
    from small_paper.live_observer_readiness import EXPECTED_ENTRY_SCORE_V2_MIN

    cfg = load_pilot_config(config_path)
    yaml_text = config_path.read_text(encoding="utf-8")
    all_configs_ok = True
    bad_configs: list[str] = []
    for p in sorted((NATIVE / "configs").glob("small_paper_pilot_q070*.yaml")):
        text = p.read_text(encoding="utf-8")
        if "entry_score_v2_min: 3" not in text:
            all_configs_ok = False
            bad_configs.append(p.name)

    ok = (
        cfg.entry_score_v2_min == 3
        and EXPECTED_ENTRY_SCORE_V2_MIN == 3
        and "entry_score_v2_min: 3" in yaml_text
        and all_configs_ok
    )
    return PreflightCheck(
        "config_entry_score_v2_min_3",
        ok,
        f"entry_score_v2_min=3 on {config_path.name}"
        if ok
        else "entry_score_v2_min mismatch",
        {
            "config_path": str(config_path.relative_to(REPO)).replace("\\", "/"),
            "config_entry_score_v2_min": cfg.entry_score_v2_min,
            "EXPECTED_ENTRY_SCORE_V2_MIN": EXPECTED_ENTRY_SCORE_V2_MIN,
            "all_q070_yaml_min_3": all_configs_ok,
            "bad_configs": bad_configs,
        },
    )


def _check_momentum_required() -> PreflightCheck:
    from research.exposure_gate import REJECT_MOMENTUM_LOW_REQUIRED, ExposureGate, ExposureGateConfig
    from small_paper.entry_expectancy_score_shadow import (
        REQUIRED_V2_TOKENS,
        momentum_low_required_for_v2,
    )

    trade_no_mom = {"momentum_continuation_score": 0.40, "entry_order_book_imbalance": 0.55}
    gate = ExposureGate(ExposureGateConfig(entry_score_v2_min=3))
    decision = gate.evaluate_entry(
        {
            "profile": "momentum_volume_v13_combined",
            "symbol": "7203.T",
            "continuation_quality_score": 0.80,
            **trade_no_mom,
        }
    )
    ok = (
        REQUIRED_V2_TOKENS == frozenset({"Momentum:low"})
        and not momentum_low_required_for_v2(trade_no_mom)
        and not decision.accept
        and decision.reason == REJECT_MOMENTUM_LOW_REQUIRED
    )
    return PreflightCheck(
        "momentum_low_required",
        ok,
        "Momentum:low required at gate" if ok else "Momentum required check failed",
        {
            "REQUIRED_V2_TOKENS": sorted(REQUIRED_V2_TOKENS),
            "no_momentum_gate_reason": decision.reason,
        },
    )


def _check_discord_connectivity(
    config_path: Path,
    *,
    skip_ping: bool,
) -> PreflightCheck:
    import os

    import requests

    from small_paper.config import load_pilot_config
    from small_paper.pilot_env import load_pilot_environment
    from small_paper.safety import check_discord_webhook_env

    config = load_pilot_config(config_path)
    env = load_pilot_environment(repo_root=REPO, discord_webhook_env=config.discord_webhook_env)
    sc = check_discord_webhook_env(config)
    notify_env = (
        getattr(config, "discord_trade_notify_webhook_env", None)
        or "KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL"
    ).strip()
    legacy_env = (config.discord_webhook_env or "KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL").strip()
    webhook_url = (os.environ.get(notify_env) or os.environ.get(legacy_env) or "").strip()

    ping_ok = False
    ping_detail = "skipped"
    if not skip_ping and webhook_url:
        payload = {
            "content": "[KABU_PAPER] Phase317 Preflight",
            "embeds": [
                {
                    "title": "[KABU_PAPER] Phase317 Preflight",
                    "description": "Discord connectivity test (no orders).",
                    "color": 0x4A5568,
                    "footer": {"text": "phase317 preflight"},
                }
            ],
        }
        try:
            resp = requests.post(webhook_url, json=payload, timeout=15)
            ping_ok = resp.status_code < 400
            ping_detail = f"HTTP {resp.status_code}"
        except Exception as e:
            ping_detail = str(e)
    elif skip_ping:
        ping_ok = True
        ping_detail = "ping skipped by flag"

    ok = sc.passed and (ping_ok if webhook_url else sc.passed)
    return PreflightCheck(
        "discord_notification_connectivity",
        ok,
        sc.message if ok else f"Discord check failed: {sc.message}; ping={ping_detail}",
        {
            "webhook_env": legacy_env,
            "trade_notify_webhook_env": notify_env,
            "env_loaded": env.discord_webhook_set,
            "webhook_configured": bool(webhook_url),
            "ping_attempted": bool(webhook_url) and not skip_ping,
            "ping_ok": ping_ok,
            "ping_detail": ping_detail,
            **sc.details,
        },
    )


def _check_kabu_station(*, skip_kabu: bool, timeout_sec: float = 25.0) -> PreflightCheck:
    if skip_kabu:
        return PreflightCheck(
            "kabu_station_connection",
            True,
            "kabu check skipped",
            {"skipped": True},
        )

    from small_paper.pilot_env import load_pilot_environment
    from small_paper.safety import check_kabu_station_connection

    load_pilot_environment(repo_root=REPO)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(check_kabu_station_connection, REPO)
            sc = fut.result(timeout=timeout_sec)
    except concurrent.futures.TimeoutError:
        return PreflightCheck(
            "kabu_station_connection",
            False,
            f"kabu connection timed out after {timeout_sec:.0f}s",
            {"timeout_sec": timeout_sec},
        )
    stale = bool((sc.details or {}).get("stale"))
    ok = sc.passed
    msg = sc.message
    if ok and stale:
        msg = f"{sc.message} (non-blocking stale tick)"
    return PreflightCheck(
        "kabu_station_connection",
        ok,
        msg,
        {**sc.details, "stale_non_blocking": stale, "timeout_sec": timeout_sec},
    )


def _check_am_pm_refresh(config_path: Path, day_stamp: str) -> PreflightCheck:
    from runner.am_pm_daily_runner import (
        AM_REFRESH_HHMM,
        DailyRunnerOptions,
        PM_REFRESH_HHMM,
        _intraday_refresh_preflight,
        core10_status,
        make_state,
        pilot_command_argv,
        universe_am_rel,
        universe_pm_rel,
        verify_config_safety,
    )
    from small_paper.config import load_pilot_config
    from universe.intraday_refresh import AM_REFRESH_TIME, PM_REFRESH_TIME, check_intraday_refresh_policy

    cfg = load_pilot_config(config_path)
    pol = check_intraday_refresh_policy(
        refresh_enabled=True,
        max_concurrent_positions=int(cfg.max_concurrent_positions),
        register_count=50,
        open_symbols_count=0,
        price_risk_mode=True,
        entry_guard_enabled=bool(getattr(cfg, "entry_price_risk_guard_enabled", False)),
    )

    state = make_state(
        repo_root=REPO,
        native_root=NATIVE,
        options=DailyRunnerOptions(
            day_stamp=day_stamp,
            dry_run_only=True,
            config_rel=str(config_path.relative_to(REPO)).replace("\\", "/"),
            universe_mode="core10-dynamic40-price-risk-filter-shadow",
            enable_intraday_refresh=True,
        ),
    )
    cfg_check = verify_config_safety(state)
    refresh_issue = _intraday_refresh_preflight(state, cfg_check)
    core = core10_status(state)
    am_argv = pilot_command_argv(state, session="am", universe_rel=universe_am_rel(day_stamp))
    pm_argv = pilot_command_argv(state, session="pm", universe_rel=universe_pm_rel(day_stamp))

    features_path = REPORTS / f"features_{day_stamp}.csv"
    am_refresh_path = REPORTS / f"universe_core10_dynamic40_price_risk_am_refresh1000_{day_stamp}.csv"
    pm_refresh_path = REPORTS / f"universe_core10_dynamic40_price_risk_pm_refresh1430_{day_stamp}.csv"

    ok = (
        bool(pol.get("ok"))
        and cfg_check.get("ok")
        and refresh_issue is None
        and bool(core.get("readable_exists"))
        and "--enable-intraday-refresh" in am_argv
        and "--enable-intraday-refresh" in pm_argv
        and AM_REFRESH_TIME == AM_REFRESH_HHMM == "10:00"
        and PM_REFRESH_TIME == PM_REFRESH_HHMM == "14:30"
    )

    return PreflightCheck(
        "am_pm_intraday_refresh_will_not_block",
        ok,
        "AM/PM refresh policy and runner argv OK"
        if ok
        else "AM/PM refresh may block runner",
        {
            "refresh_policy": pol,
            "config_safety_ok": cfg_check.get("ok"),
            "config_safety_issues": cfg_check.get("issues"),
            "intraday_refresh_issue": refresh_issue,
            "core10_readable": core.get("readable_exists"),
            "core10_stale_caution": core.get("stale_caution"),
            "am_refresh_time": AM_REFRESH_TIME,
            "pm_refresh_time": PM_REFRESH_TIME,
            "am_argv_has_intraday_refresh": "--enable-intraday-refresh" in am_argv,
            "pm_argv_has_intraday_refresh": "--enable-intraday-refresh" in pm_argv,
            "features_csv_exists": features_path.is_file(),
            "features_csv": str(features_path.relative_to(REPO)).replace("\\", "/"),
            "am_refresh_csv_exists": am_refresh_path.is_file(),
            "pm_refresh_csv_exists": pm_refresh_path.is_file(),
            "note": "refresh CSVs are built at AM prep when features exist; policy must pass",
        },
    )


def run_preflight(
    *,
    day_stamp: str,
    config_rel: str,
    skip_kabu: bool,
    skip_safety: bool,
    skip_discord_ping: bool,
) -> dict[str, Any]:
    config_path = REPO / config_rel.replace("/", "\\") if "\\" in config_rel else REPO / config_rel
    checks: list[PreflightCheck] = [
        _py_compile(),
        _check_phase314_entry(),
        _check_phase316_exit_discord(),
        _check_config_entry_min(config_path),
        _check_momentum_required(),
        _check_discord_connectivity(config_path, skip_ping=skip_discord_ping),
        _check_kabu_station(skip_kabu=skip_kabu),
        _check_am_pm_refresh(config_path, day_stamp),
    ]

    failed = [c.check_id for c in checks if not c.passed]
    overall = not failed

    return {
        "phase": 317,
        "title": "tomorrow_paper_trade_preflight",
        "target_trade_date": day_stamp,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "config": str(config_path.relative_to(REPO)).replace("\\", "/"),
        "checks": [_to_dict(c) for c in checks],
        "failed_checks": failed,
        "preflight_ok": overall,
        "verdict": "ready_for_paper_trade" if overall else "fix_before_trade",
        "notes": [
            "Phase314: Momentum:low + Board:mid, min=3",
            "Phase316: EXIT Discord shows pnl_yen_100",
            "features CSV for target day is generated by phase113 on trade morning if missing",
        ],
    }


def main() -> int:
    _bootstrap()
    parser = argparse.ArgumentParser(description="Phase317 tomorrow paper-trade preflight")
    parser.add_argument("--day-stamp", default=DEFAULT_DAY)
    parser.add_argument("--config", default=DEFAULT_CONFIG_REL)
    parser.add_argument("--skip-kabu", action="store_true")
    parser.add_argument("--skip-safety", action="store_true")
    parser.add_argument(
        "--skip-discord-ping",
        action="store_true",
        help="Skip live webhook POST (env check only)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPORTS / OUT_NAME,
    )
    args = parser.parse_args()

    report = run_preflight(
        day_stamp=args.day_stamp,
        config_rel=args.config,
        skip_kabu=args.skip_kabu,
        skip_safety=args.skip_safety,
        skip_discord_ping=args.skip_discord_ping,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {args.output}")
    print(f"preflight_ok={report['preflight_ok']} failed={report['failed_checks']}")
    return 0 if report["preflight_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
