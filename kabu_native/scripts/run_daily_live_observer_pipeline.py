#!/usr/bin/env python3
"""
Phase 93: Daily live observer pipeline (morning check -> live dry-run -> daily review).

Example::
    python kabu_native/scripts/run_daily_live_observer_pipeline.py --skip-kabu --skip-safety
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
EXPECTED_POLICY_LABEL = "q070_cap3_mfe_fav_vol_liq_trial"
CONFIG_REL = "kabu_native/configs/small_paper_pilot_q070_cap3_mfe_fav_vol_liq.yaml"
REPORTS_REL = "kabu_native/results/reports"
SMALL_PAPER_REL = "kabu_native/results/small_paper"
POLL_INTERVAL_SEC = 5

LIVE_COMMAND = (
    "python kabu_native/scripts/run_small_paper_pilot.py --dry-run --source live "
    "--full-session --wait-until-session "
    f"--config {CONFIG_REL} --poll-interval-sec {POLL_INTERVAL_SEC}"
)


def _bootstrap() -> tuple[Path, Path]:
    script = Path(__file__).resolve()
    repo_root = script.parents[2]
    native = script.parents[1]
    for p in (native / "src", repo_root):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    return repo_root, native


def _find_latest_live_session_dir(repo_root: Path, day_key: str) -> Optional[Path]:
    base = repo_root / SMALL_PAPER_REL
    if not base.is_dir():
        return None

    def _pool_under(parent: Path) -> list[Path]:
        if not parent.is_dir():
            return []
        found = [
            p
            for p in parent.iterdir()
            if p.is_dir() and p.name.startswith("live_full_session_")
        ]
        with_summary = [p for p in found if (p / "small_paper_summary.json").is_file()]
        return with_summary if with_summary else found

    candidates = _pool_under(base / day_key)
    if not candidates:
        for day_dir in sorted(base.iterdir(), reverse=True):
            if day_dir.is_dir() and len(day_dir.name) == 8:
                candidates = _pool_under(day_dir)
                if candidates:
                    break
    if not candidates:
        candidates = [
            p
            for p in base.glob("*/live_full_session_*")
            if p.is_dir() and (p / "small_paper_summary.json").is_file()
        ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: (p.parent.name, p.name))


def _verify_config_safety(repo_root: Path, config_path: Path) -> tuple[bool, str]:
    from small_paper.config import load_pilot_config

    cfg = load_pilot_config(config_path)
    if cfg.policy_label != EXPECTED_POLICY_LABEL:
        return False, f"policy_label={cfg.policy_label!r}"
    if cfg.order_enabled:
        return False, "order_enabled=true"
    if not cfg.paper_only:
        return False, "paper_only=false"
    return True, "config safety OK"


def main() -> int:
    repo_root, native = _bootstrap()
    scripts = native / "scripts"
    day_key = datetime.now(JST).strftime("%Y%m%d")
    session_stamp = datetime.now(JST).strftime("%H%M%S")

    parser = argparse.ArgumentParser(description="Phase93 daily live observer pipeline")
    parser.add_argument("--config", type=Path, default=repo_root / CONFIG_REL)
    parser.add_argument("--report-date", default=None, help="YYYYMMDD (default: today JST)")
    parser.add_argument("--skip-kabu", action="store_true")
    parser.add_argument("--skip-safety", action="store_true")
    parser.add_argument(
        "--skip-live",
        action="store_true",
        help="Run morning check (+ daily review on latest session) only; for testing",
    )
    args = parser.parse_args()

    day_key = args.report_date or day_key
    cfg_path = args.config if args.config.is_absolute() else repo_root / args.config
    if not cfg_path.is_file():
        print(f"config not found: {cfg_path}", file=sys.stderr)
        return 2

    pipeline: dict[str, Any] = {
        "phase": 93,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "day_key": day_key,
        "config_path": str(cfg_path.relative_to(repo_root)).replace("\\", "/"),
        "live_command": LIVE_COMMAND,
        "skip_kabu": args.skip_kabu,
        "skip_safety": args.skip_safety,
        "pipeline_status": "started",
    }

    # A) Morning check
    morning_script = scripts / "run_morning_live_observer_check.py"
    morning_cmd = [
        sys.executable,
        str(morning_script),
        "--config",
        str(cfg_path),
        "--report-date",
        day_key,
    ]
    if args.skip_kabu:
        morning_cmd.append("--skip-kabu")
    if args.skip_safety:
        morning_cmd.append("--skip-safety")

    print("[pipeline] morning check...", file=sys.stderr)
    morning_proc = subprocess.run(morning_cmd, cwd=str(repo_root), capture_output=True, text=True)
    morning_json_path = repo_root / REPORTS_REL / f"morning_live_observer_check_{day_key}.json"
    morning_report: dict[str, Any] = {}
    if morning_json_path.is_file():
        morning_report = json.loads(morning_json_path.read_text(encoding="utf-8"))
    elif morning_proc.stdout.strip():
        try:
            morning_report = json.loads(morning_proc.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            pass

    morning_verdict = morning_report.get("morning_verdict", "unknown")
    pipeline["morning_verdict"] = morning_verdict
    pipeline["morning_report_path"] = str(morning_json_path.relative_to(repo_root)).replace("\\", "/")
    pipeline["morning_exit_code"] = morning_proc.returncode

    if morning_verdict != "ready":
        pipeline["pipeline_status"] = "stopped_at_morning"
        pipeline["final_summary"] = {
            "morning_verdict": morning_verdict,
            "live_session_dir": None,
            "daily_verdict": None,
            "structural_pf": None,
            "avg_pnl": None,
            "trade_count": None,
            "continue_main_config": None,
        }
        _write_pipeline(repo_root, day_key, pipeline)
        print(json.dumps(pipeline["final_summary"], ensure_ascii=True, indent=2))
        return 2 if morning_verdict == "blocked" else 1

    if args.skip_live:
        pipeline["pipeline_status"] = "stopped_skip_live_flag"
        live_dir = _find_latest_live_session_dir(repo_root, day_key)
        _finish_daily_review(pipeline, repo_root, scripts, live_dir, day_key)
        _write_pipeline(repo_root, day_key, pipeline)
        print(json.dumps(pipeline["final_summary"], ensure_ascii=True, indent=2))
        return 0

    # Safety re-check before live
    safe, safe_msg = _verify_config_safety(repo_root, cfg_path)
    if not safe:
        pipeline["pipeline_status"] = "stopped_at_config_safety"
        pipeline["config_safety_error"] = safe_msg
        pipeline["final_summary"] = {
            "morning_verdict": morning_verdict,
            "live_session_dir": None,
            "daily_verdict": None,
            "structural_pf": None,
            "avg_pnl": None,
            "trade_count": None,
            "continue_main_config": False,
        }
        _write_pipeline(repo_root, day_key, pipeline)
        print(f"Config safety failed: {safe_msg}", file=sys.stderr)
        return 2

    # B) Live observer
    from small_paper.config import load_pilot_config, resolve_live_full_session_dir

    config = load_pilot_config(cfg_path)
    expected_dir = resolve_live_full_session_dir(
        config, repo_root=repo_root, day_key=day_key, session_stamp=session_stamp
    )
    pipeline["expected_live_session_dir"] = str(expected_dir.relative_to(repo_root)).replace("\\", "/")

    live_cmd = [
        sys.executable,
        str(scripts / "run_small_paper_pilot.py"),
        "--dry-run",
        "--source",
        "live",
        "--full-session",
        "--wait-until-session",
        "--config",
        str(cfg_path),
        "--poll-interval-sec",
        str(POLL_INTERVAL_SEC),
        "--output-date",
        day_key,
    ]
    if args.skip_safety:
        live_cmd.append("--skip-safety")
    if args.skip_kabu:
        pass  # pilot has no --skip-kabu; morning already checked

    print("[pipeline] live observer (full session)...", file=sys.stderr)
    print(f"[pipeline] command: {' '.join(live_cmd)}", file=sys.stderr)
    live_proc = subprocess.run(live_cmd, cwd=str(repo_root))
    pipeline["live_exit_code"] = live_proc.returncode
    pipeline["live_command_argv"] = live_cmd

    if live_proc.returncode != 0:
        pipeline["pipeline_status"] = "stopped_at_live"
        live_dir = _find_latest_live_session_dir(repo_root, day_key)
        if live_dir:
            pipeline["live_session_dir"] = str(live_dir.relative_to(repo_root)).replace("\\", "/")
        pipeline["final_summary"] = _empty_final_summary(morning_verdict)
        _write_pipeline(repo_root, day_key, pipeline)
        return live_proc.returncode

    # C) Resolve session dir
    live_dir = expected_dir if (expected_dir / "small_paper_summary.json").is_file() else None
    if live_dir is None:
        live_dir = _find_latest_live_session_dir(repo_root, day_key)
    if live_dir is None or not live_dir.is_dir():
        pipeline["pipeline_status"] = "stopped_no_session_dir"
        pipeline["final_summary"] = _empty_final_summary(morning_verdict)
        _write_pipeline(repo_root, day_key, pipeline)
        print("Live session dir not found", file=sys.stderr)
        return 2

    pipeline["live_session_dir"] = str(live_dir.relative_to(repo_root)).replace("\\", "/")

    # D) Daily review
    _finish_daily_review(pipeline, repo_root, scripts, live_dir, day_key)
    pipeline["pipeline_status"] = "completed"
    _write_pipeline(repo_root, day_key, pipeline)

    print(json.dumps(pipeline["final_summary"], ensure_ascii=True, indent=2))
    fs = pipeline["final_summary"]
    print(
        f"\n[pipeline] morning={fs.get('morning_verdict')} "
        f"daily={fs.get('daily_verdict')} PF={fs.get('structural_pf')} "
        f"trades={fs.get('trade_count')}",
        file=sys.stderr,
    )
    daily_v = fs.get("daily_verdict")
    if daily_v == "stop_and_review":
        return 1
    return 0


def _empty_final_summary(morning_verdict: str) -> dict[str, Any]:
    return {
        "morning_verdict": morning_verdict,
        "live_session_dir": None,
        "daily_verdict": None,
        "structural_pf": None,
        "avg_pnl": None,
        "trade_count": None,
        "continue_main_config": None,
    }


def _finish_daily_review(
    pipeline: dict[str, Any],
    repo_root: Path,
    scripts: Path,
    live_dir: Optional[Path],
    day_key: str,
) -> None:
    if live_dir is None:
        pipeline["final_summary"] = _empty_final_summary(pipeline.get("morning_verdict", "unknown"))
        return

    review_script = scripts / "run_daily_live_observer_review.py"
    review_cmd = [
        sys.executable,
        str(review_script),
        "--session-dir",
        str(live_dir),
    ]
    print("[pipeline] daily review...", file=sys.stderr)
    subprocess.run(review_cmd, cwd=str(repo_root), check=False)

    review_path = live_dir / "daily_live_observer_review.json"
    daily_report: dict[str, Any] = {}
    if review_path.is_file():
        daily_report = json.loads(review_path.read_text(encoding="utf-8"))

    m = daily_report.get("metrics") or {}
    pipeline["daily_verdict"] = daily_report.get("daily_verdict")
    pipeline["daily_review_path"] = str(review_path.relative_to(repo_root)).replace("\\", "/")
    pipeline["final_summary"] = {
        "morning_verdict": pipeline.get("morning_verdict"),
        "live_session_dir": str(live_dir.relative_to(repo_root)).replace("\\", "/"),
        "daily_verdict": daily_report.get("daily_verdict"),
        "structural_pf": m.get("structural_pf"),
        "avg_pnl": m.get("structural_avg_pnl"),
        "trade_count": m.get("structural_trade_count"),
        "continue_main_config": daily_report.get("continue_main_config"),
    }


def _write_pipeline(repo_root: Path, day_key: str, pipeline: dict[str, Any]) -> None:
    out_dir = repo_root / REPORTS_REL
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"daily_live_observer_pipeline_{day_key}.json"
    path.write_text(json.dumps(pipeline, ensure_ascii=False, indent=2), encoding="utf-8")
    pipeline["pipeline_report_path"] = str(path.relative_to(repo_root)).replace("\\", "/")
    print(f"Wrote {path}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
