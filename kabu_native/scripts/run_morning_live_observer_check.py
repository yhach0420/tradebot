#!/usr/bin/env python3
"""
Phase 92: Morning pre-flight check before live observer (q070_cap3_mfe_fav_vol_liq_trial).

Example::
    python kabu_native/scripts/run_morning_live_observer_check.py
    python kabu_native/scripts/run_morning_live_observer_check.py --skip-kabu --skip-safety
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
EXPECTED_POLICY_LABEL = "q070_cap3_mfe_fav_vol_liq_trial"
DEFAULT_CONFIG_REL = "kabu_native/configs/small_paper_pilot_q070_cap3_mfe_fav_vol_liq.yaml"
REPORTS_REL = "kabu_native/results/reports"
SMALL_PAPER_REL = "kabu_native/results/small_paper"
DAILY_SUMMARY_NAME = "daily_live_observer_summary.csv"

LIVE_COMMAND = (
    "python kabu_native/scripts/run_small_paper_pilot.py --dry-run --source live "
    "--full-session --wait-until-session "
    "--config kabu_native/configs/small_paper_pilot_q070_cap3_mfe_fav_vol_liq.yaml "
    "--poll-interval-sec 5"
)


@dataclass
class MorningCheck:
    check_id: str
    passed: bool
    message: str
    details: dict[str, Any] = field(default_factory=dict)


def _bootstrap() -> Path:
    script = Path(__file__).resolve()
    repo_root = script.parents[2]
    native = script.parents[1]
    for p in (native / "src", repo_root):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    return repo_root


def _check_to_dict(c: MorningCheck) -> dict[str, Any]:
    return {
        "check_id": c.check_id,
        "passed": c.passed,
        "message": c.message,
        "details": c.details,
    }


def _latest_summary_verdict(repo_root: Path) -> dict[str, Any]:
    path = repo_root / REPORTS_REL / DAILY_SUMMARY_NAME
    if not path.is_file():
        return {}
    import csv

    rows: list[dict[str, str]] = []
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return {}

    vol_liq_rows = [r for r in rows if r.get("policy_label") == EXPECTED_POLICY_LABEL]
    pool = vol_liq_rows if vol_liq_rows else rows
    # Latest trading day (session_id YYYYMMDD/...), not latest review generation time.
    last = max(pool, key=lambda r: str(r.get("session_id") or ""))
    return {
        "source": "daily_live_observer_summary.csv",
        "session_id": last.get("session_id"),
        "daily_verdict": last.get("daily_verdict"),
        "reviewed_at": last.get("reviewed_at"),
    }


def _find_latest_live_session_review(repo_root: Path) -> dict[str, Any]:
    """Latest daily review under live_full_session_* only (excludes ad-hoc replays)."""
    base = repo_root / SMALL_PAPER_REL
    candidates: list[tuple[str, Path, dict[str, Any]]] = []
    if not base.is_dir():
        return {}
    for path in base.glob("**/live_full_session_*/daily_live_observer_review.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        sid = str(data.get("session_id") or path.parent.name)
        candidates.append((sid, path, data))
    if not candidates:
        return {}
    candidates.sort(key=lambda x: x[0], reverse=True)
    _, path, data = candidates[0]
    return {
        "source": "live_full_session_daily_review",
        "path": str(path.relative_to(repo_root)).replace("\\", "/"),
        "session_id": data.get("session_id"),
        "daily_verdict": data.get("daily_verdict"),
        "generated_at": data.get("generated_at"),
        "continue_main_config": data.get("continue_main_config"),
    }


def _resolve_previous_daily_verdict(repo_root: Path) -> dict[str, Any]:
    """Prefer cumulative summary CSV; fallback to latest live session review file."""
    summary = _latest_summary_verdict(repo_root)
    live_review = _find_latest_live_session_review(repo_root)
    verdict = summary.get("daily_verdict") or live_review.get("daily_verdict")
    return {
        "daily_verdict": verdict,
        "authoritative_source": summary.get("source") or live_review.get("source"),
        "summary": summary,
        "live_session_review": live_review,
    }


def run_morning_check(
    *,
    repo_root: Path,
    config_path: Path,
    day_key: str,
    skip_kabu: bool,
    skip_safety: bool,
    reference_session_dir: Optional[Path],
    structural_session_dir: Optional[Path],
) -> dict[str, Any]:
    from small_paper.config import load_pilot_config
    from small_paper.live_observer_readiness import (
        DEFAULT_PHASE54_SESSION_REL,
        DEFAULT_PHASE60_STRUCTURAL_SESSION_REL,
        EXPECTED_VOL_LIQ_POLICY_LABEL,
        run_live_observer_readiness,
    )
    from small_paper.pilot_env import load_pilot_environment
    from small_paper.safety import check_discord_webhook_env, check_output_path_writable

    config = load_pilot_config(config_path)
    env = load_pilot_environment(
        repo_root=repo_root,
        discord_webhook_env=config.discord_webhook_env,
    )

    ref_dir = reference_session_dir or (repo_root / DEFAULT_PHASE54_SESSION_REL)
    struct_dir = structural_session_dir or (repo_root / DEFAULT_PHASE60_STRUCTURAL_SESSION_REL)
    if not ref_dir.is_absolute():
        ref_dir = repo_root / ref_dir
    if not struct_dir.is_absolute():
        struct_dir = repo_root / struct_dir

    checks: list[MorningCheck] = []

    # 1) Full readiness bundle
    readiness_report = run_live_observer_readiness(
        config_path,
        repo_root=repo_root,
        day_key=day_key,
        reference_session_dir=ref_dir,
        structural_session_dir=struct_dir,
        skip_kabu=skip_kabu,
        skip_safety_bundle=skip_safety,
    )
    readiness_ok = bool(readiness_report.get("readiness"))
    checks.append(
        MorningCheck(
            "readiness_true",
            readiness_ok,
            "readiness=true" if readiness_ok else f"readiness=false failed={readiness_report.get('failed_checks')}",
            {
                "failed_checks": readiness_report.get("failed_checks"),
                "warnings": readiness_report.get("warnings"),
            },
        )
    )

    # 2) Policy label
    policy_ok = config.policy_label == EXPECTED_POLICY_LABEL
    checks.append(
        MorningCheck(
            "policy_label",
            policy_ok,
            f"policy_label={config.policy_label!r}"
            + ("" if policy_ok else f" expected {EXPECTED_POLICY_LABEL!r}"),
            {"expected": EXPECTED_POLICY_LABEL, "actual": config.policy_label},
        )
    )

    # 3) order_enabled / paper_only
    order_ok = not config.order_enabled
    checks.append(
        MorningCheck(
            "order_enabled_false",
            order_ok,
            f"order_enabled={config.order_enabled}",
            {"order_enabled": config.order_enabled},
        )
    )
    paper_ok = bool(config.paper_only)
    checks.append(
        MorningCheck(
            "paper_only_true",
            paper_ok,
            f"paper_only={config.paper_only}",
            {"paper_only": config.paper_only},
        )
    )

    # 4) Discord webhook
    discord_sc = check_discord_webhook_env(config)
    checks.append(
        MorningCheck(
            "discord_webhook_set",
            discord_sc.passed,
            discord_sc.message,
            {
                **discord_sc.details,
                "env_var": env.discord_webhook_env,
                "env_set": env.discord_webhook_set,
            },
        )
    )

    # 5) Output writable
    out_sc = check_output_path_writable(config, repo_root=repo_root, day_key=day_key)
    checks.append(
        MorningCheck(
            "output_path_writable",
            out_sc.passed,
            out_sc.message,
            dict(out_sc.details),
        )
    )

    # 6) Previous daily review
    prev = _resolve_previous_daily_verdict(repo_root)
    prev_verdict = prev.get("daily_verdict")
    prev_stop = prev_verdict == "stop_and_review"
    prev_ok = not prev_stop
    sid = (prev.get("summary") or {}).get("session_id") or (prev.get("live_session_review") or {}).get(
        "session_id"
    )
    checks.append(
        MorningCheck(
            "previous_daily_review_not_stop",
            prev_ok,
            "no prior stop_and_review daily review"
            if prev_ok
            else f"latest daily_verdict=stop_and_review ({sid})",
            prev,
        )
    )

    # 7) Kabu connection (optional)
    if skip_kabu:
        checks.append(
            MorningCheck(
                "kabu_station_connection",
                True,
                "skipped (--skip-kabu)",
                {"skipped": True},
            )
        )
    else:
        from small_paper.live_observer_readiness import check_kabu_connection

        kabu_rc = check_kabu_connection(repo_root, stale_tick_sec=config.live_stale_tick_sec)
        checks.append(
            MorningCheck(
                "kabu_station_connection",
                kabu_rc.passed,
                kabu_rc.message,
                dict(kabu_rc.details),
            )
        )

    blocked_ids = frozenset(
        {
            "readiness_true",
            "policy_label",
            "order_enabled_false",
            "paper_only_true",
            "previous_daily_review_not_stop",
        }
    )
    blocked = [c.check_id for c in checks if c.check_id in blocked_ids and not c.passed]
    caution_ids: list[str] = []
    if not skip_kabu and not any(c.check_id == "kabu_station_connection" and c.passed for c in checks):
        if "kabu_station_connection" not in blocked_ids:
            caution_ids.append("kabu_station_connection")
    if readiness_report.get("warnings"):
        caution_ids.append("readiness_warnings")
    if prev_verdict == "caution":
        caution_ids.append("previous_daily_review_caution")
    if not env.dotenv_exists:
        caution_ids.append("dotenv_missing")

    if blocked:
        verdict = "blocked"
        rationale = f"Blocked: {', '.join(blocked)}"
        show_live = False
    elif caution_ids or not readiness_ok:
        verdict = "caution"
        rationale = f"Caution: {', '.join(caution_ids)}" if caution_ids else "readiness warnings"
        show_live = False
    else:
        failed_other = [c.check_id for c in checks if not c.passed]
        if failed_other:
            verdict = "caution"
            rationale = f"Non-blocking failures: {', '.join(failed_other)}"
            show_live = False
        else:
            verdict = "ready"
            rationale = "All morning checks passed; safe to start live observer dry-run"
            show_live = True

    return {
        "phase": 92,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "day_key": day_key,
        "main_config": EXPECTED_POLICY_LABEL,
        "config_path": str(config_path.relative_to(repo_root)).replace("\\", "/"),
        "expected_policy_label": EXPECTED_POLICY_LABEL,
        "skip_kabu": skip_kabu,
        "skip_safety": skip_safety,
        "checks": [_check_to_dict(c) for c in checks],
        "readiness_summary": {
            "readiness": readiness_ok,
            "failed_checks": readiness_report.get("failed_checks"),
            "warnings": readiness_report.get("warnings"),
            "policy_label": readiness_report.get("policy_label"),
        },
        "previous_daily_review": prev,
        "pilot_env": {
            "dotenv_exists": env.dotenv_exists,
            "discord_webhook_set": env.discord_webhook_set,
            "kabu_api_password_set": env.kabu_api_password_set,
        },
        "morning_verdict": verdict,
        "verdict_rationale": rationale,
        "blocked_checks": blocked,
        "caution_notes": caution_ids,
        "live_command": LIVE_COMMAND if show_live else None,
        "conclusion": (
            f"Start live observer with {EXPECTED_POLICY_LABEL}."
            if show_live
            else f"Do not start live until resolved ({verdict})."
        ),
        "note": "Pre-flight only; no trading logic or YAML changes.",
    }


def main() -> int:
    repo_root = _bootstrap()
    from small_paper.live_observer_readiness import (
        DEFAULT_PHASE54_SESSION_REL,
        DEFAULT_PHASE60_STRUCTURAL_SESSION_REL,
    )

    parser = argparse.ArgumentParser(description="Phase92 morning live observer pre-flight")
    parser.add_argument("--config", type=Path, default=repo_root / DEFAULT_CONFIG_REL)
    parser.add_argument("--report-date", default=None, help="YYYYMMDD (default: today JST)")
    parser.add_argument("--skip-kabu", action="store_true")
    parser.add_argument("--skip-safety", action="store_true")
    parser.add_argument(
        "--reference-session-dir",
        type=Path,
        default=repo_root / DEFAULT_PHASE54_SESSION_REL,
    )
    parser.add_argument(
        "--structural-session-dir",
        type=Path,
        default=repo_root / DEFAULT_PHASE60_STRUCTURAL_SESSION_REL,
    )
    args = parser.parse_args()

    cfg_path = args.config if args.config.is_absolute() else repo_root / args.config
    if not cfg_path.is_file():
        print(f"config not found: {cfg_path}", file=sys.stderr)
        return 2

    day_key = args.report_date or datetime.now(JST).strftime("%Y%m%d")
    report = run_morning_check(
        repo_root=repo_root,
        config_path=cfg_path,
        day_key=day_key,
        skip_kabu=args.skip_kabu,
        skip_safety=args.skip_safety,
        reference_session_dir=args.reference_session_dir,
        structural_session_dir=args.structural_session_dir,
    )

    out_dir = repo_root / REPORTS_REL
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"morning_live_observer_check_{day_key}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "morning_verdict": report["morning_verdict"],
                "conclusion": report["conclusion"],
                "live_command": report.get("live_command"),
                "output": str(out_path),
            },
            ensure_ascii=True,
        )
    )
    print(f"Wrote {out_path}", file=sys.stderr)

    if report["morning_verdict"] == "blocked":
        return 2
    if report["morning_verdict"] == "caution":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
