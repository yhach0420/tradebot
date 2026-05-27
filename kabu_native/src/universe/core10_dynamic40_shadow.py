"""
Phase 118: Wire Core10 + Dynamic40 universes to AM/PM shadow live runner.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from universe.core10_dynamic40 import (
    CORE_BUCKET,
    CORE_SLOTS,
    DYNAMIC_BUCKET,
    DYNAMIC_SLOTS,
    TOTAL_SLOTS,
    build_am_universe,
    build_pm_universe,
    universe_am_path,
    universe_pm_path,
    validate_universe,
    write_universe_csv,
)
from universe.core_watchlist import (
    CORE_LIMIT,
    REJECT_CORE_LIMIT_EXCEEDED,
    load_core_watchlist,
    normalize_watch_symbol,
    resolve_core_symbol_source_path,
    validate_watch_symbol,
)
from universe.daily_features import load_features_csv

DISCORD_BOT_REL = "discord_issue_bot/discord_issue_bot.py"
SHADOW_PILOT_YAML = "kabu_native/configs/small_paper_pilot_q070_cap3_mfe_fav_vol_liq.yaml"


def discord_enforcement_ok(repo_root: Path) -> bool:
    from universe.core_watchlist import discord_enforcement_ok as _ok

    return _ok(repo_root)


def diagnose_core_watchlist(
    repo_root: Path,
    core_symbols: list[str],
    *,
    trade_date: Optional[date] = None,
) -> dict[str, Any]:
    from universe.core_watchlist import core_status_report

    report = core_status_report(repo_root, trade_date=trade_date)
    report["core_limit_enforced"] = discord_enforcement_ok(repo_root)
    report["reject_reason_overflow"] = REJECT_CORE_LIMIT_EXCEEDED
    return report


def validate_runner_universe(
    path: Path,
    *,
    expected_session: str,
) -> dict[str, Any]:
    base = validate_universe(path, expected_session=expected_session)
    checks = list(base.get("checks") or [])
    ok = bool(base.get("passed"))

    def add(cid: str, passed: bool, detail: str) -> None:
        nonlocal ok
        if not passed:
            ok = False
        checks.append({"check_id": cid, "passed": passed, "detail": detail})

    if not path.is_file():
        base["passed"] = False
        base["checks"] = checks
        return base

    buckets: set[str] = set()
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            buckets.add(str(row.get("source_bucket") or ""))

    core_n = int(base.get("core_count") or 0)
    dyn_n = int(base.get("dynamic_count") or 0)
    total = int(base.get("total_count") or 0)

    add("source_bucket_core", CORE_BUCKET in buckets, f"buckets={buckets}")
    add("source_bucket_dynamic", DYNAMIC_BUCKET in buckets, f"buckets={buckets}")
    add(
        "dynamic_slot_policy",
        dyn_n >= DYNAMIC_SLOTS or (total == TOTAL_SLOTS and dyn_n == total - core_n),
        f"core={core_n} dynamic={dyn_n} total={total}",
    )

    base["passed"] = ok
    base["checks"] = checks
    base["source_buckets"] = sorted(buckets)
    return base


def build_core10_dynamic40_universes(
    *,
    repo_root: Path,
    reports_dir: Path,
    day_stamp: str,
    trade_date: date,
    symbol_meta: Mapping[str, Mapping[str, Any]],
    feature_rows: list[dict[str, str]],
    push_day_dir: Path,
) -> dict[str, Any]:
    core_symbols, _ = load_core_watchlist(repo_root)
    core_diag = diagnose_core_watchlist(repo_root, core_symbols, trade_date=trade_date)

    am_csv = universe_am_path(reports_dir, day_stamp)
    pm_csv = universe_pm_path(reports_dir, day_stamp)

    am_rows: list[dict[str, Any]] = []
    pm_rows: list[dict[str, Any]] = []
    if feature_rows:
        am_rows = build_am_universe(
            core_symbols=core_symbols,
            feature_rows=feature_rows,
            symbol_meta=symbol_meta,
        )
        pm_rows = build_pm_universe(
            core_symbols=core_symbols,
            feature_rows=feature_rows,
            symbol_meta=symbol_meta,
            push_day_dir=push_day_dir,
        )
        write_universe_csv(am_csv, am_rows)
        write_universe_csv(pm_csv, pm_rows)

    return {
        "core_diagnosis": core_diag,
        "am_output": str(am_csv),
        "pm_output": str(pm_csv),
        "am_row_count": len(am_rows),
        "pm_row_count": len(pm_rows),
        "features_used": len(feature_rows),
    }


def shadow_live_commands(*, am_csv_rel: str, pm_csv_rel: str, pilot_yaml: str = SHADOW_PILOT_YAML) -> dict[str, Any]:
    return {
        "session_range_supported": True,
        "universe_mode": "core10-dynamic40",
        "am_pm_session_times": {
            "am": {"session_start": "09:03", "session_end": "11:25", "entry_stop": "11:20", "force_close": "11:25"},
            "pm": {"session_start": "12:33", "session_end": "15:23", "entry_stop": "15:18", "force_close": "15:23"},
        },
        "morning_shadow_live": [
            "python kabu_native/scripts/check_small_paper_safety.py",
            (
                "python kabu_native/scripts/run_small_paper_pilot.py --dry-run --source live "
                f"--universe-csv {am_csv_rel} "
                f"--config {pilot_yaml} "
                "--am-pm-session am --wait-until-session --poll-interval-sec 5"
            ),
        ],
        "afternoon_shadow_live": [
            "python kabu_native/scripts/check_small_paper_safety.py",
            (
                "python kabu_native/scripts/run_small_paper_pilot.py --dry-run --source live "
                f"--universe-csv {pm_csv_rel} "
                f"--config {pilot_yaml} "
                "--am-pm-session pm --wait-until-session --poll-interval-sec 5"
            ),
        ],
        "do_not_use_full_session": "Separate AM/PM runs with --am-pm-session",
        "core10_note": "Core symbols from discord_issue_bot/watchlist.json only (max 10)",
    }


def build_pipeline_report(
    *,
    repo_root: Path,
    reports_dir: Path,
    day_stamp: str,
    trade_date: date,
    feature_rows: list[dict[str, str]],
    push_day_dir: Path,
    symbol_meta: Mapping[str, Mapping[str, Any]],
    am_runner_fn: Callable[[Path], dict[str, Any]],
    pm_runner_fn: Callable[[Path], dict[str, Any]],
) -> dict[str, Any]:
    """Full Phase118 report dict (shared by phase118 script and phase115 mode)."""
    from universe.core10_dynamic40 import universe_am_path, universe_pm_path

    build = build_core10_dynamic40_universes(
        repo_root=repo_root,
        reports_dir=reports_dir,
        day_stamp=day_stamp,
        trade_date=trade_date,
        symbol_meta=symbol_meta,
        feature_rows=feature_rows,
        push_day_dir=push_day_dir,
    )
    core_diag = build["core_diagnosis"]
    am_csv = universe_am_path(reports_dir, day_stamp)
    pm_csv = universe_pm_path(reports_dir, day_stamp)
    am_val = validate_runner_universe(am_csv, expected_session="am")
    pm_val = validate_runner_universe(pm_csv, expected_session="pm")
    am_runner = am_runner_fn(am_csv)
    pm_runner = pm_runner_fn(pm_csv)
    verdict, verdict_notes = determine_verdict(
        core_diag=core_diag,
        am_val=am_val,
        pm_val=pm_val,
        am_runner_ok=bool(am_runner.get("passed")),
        pm_runner_ok=bool(pm_runner.get("passed")),
        build=build,
    )

    def _rel(p: Path) -> str:
        try:
            return str(p.relative_to(repo_root))
        except ValueError:
            return str(p)

    return {
        "phase": 118,
        "day_stamp": day_stamp,
        "verdict": verdict,
        "verdict_notes": verdict_notes,
        "verdict_options": {
            "A": "core10_dynamic40_pipeline_ready",
            "B": "core_symbol_source_missing",
            "C": "core_limit_not_enforced",
            "D": "runner_load_failed",
        },
        "core10_diagnosis": core_diag,
        "build": build,
        "universe_validation": {"am": am_val, "pm": pm_val},
        "runner_check": {"am": am_runner, "pm": pm_runner},
        "shadow_live_commands": shadow_live_commands(
            am_csv_rel=_rel(am_csv),
            pm_csv_rel=_rel(pm_csv),
        ),
        "outputs": {
            "universe_am_csv": _rel(am_csv),
            "universe_pm_csv": _rel(pm_csv),
        },
    }


def determine_verdict(
    *,
    core_diag: Mapping[str, Any],
    am_val: Mapping[str, Any],
    pm_val: Mapping[str, Any],
    am_runner_ok: bool,
    pm_runner_ok: bool,
    build: Mapping[str, Any],
) -> tuple[str, list[str]]:
    notes: list[str] = []

    if not core_diag.get("readable_exists"):
        return "core_symbol_source_missing", ["Discord watchlist.json not readable"]

    if len(core_diag.get("core_symbols") or []) > CORE_SLOTS:
        return "core_limit_not_enforced", [f"core_count={core_diag.get('core_count')} > {CORE_SLOTS}"]

    if not core_diag.get("core_limit_enforced"):
        return "core_limit_not_enforced", ["discord_issue_bot missing Core10 limit guard"]

    if not core_diag.get("core_freshness_check_present", True):
        return "core_freshness_check_missing", ["core_last_updated_date / stale warning not in pipeline"]

    if core_diag.get("invalid_core_symbols"):
        notes.append(f"invalid_core={core_diag.get('invalid_core_symbols')}")

    if build.get("am_row_count", 0) < TOTAL_SLOTS or build.get("pm_row_count", 0) < TOTAL_SLOTS:
        notes.append(f"am={build.get('am_row_count')} pm={build.get('pm_row_count')}")
        return "core_symbol_source_missing", notes + ["features missing or insufficient for 50-symbol universe"]

    if not am_val.get("passed") or not pm_val.get("passed"):
        return "runner_load_failed", ["universe CSV validation failed"]

    if not am_runner_ok or not pm_runner_ok:
        return "runner_load_failed", ["load_symbols count != 50 for AM or PM"]

    notes.append("Core10+Dynamic40 AM/PM runner CSVs ready for shadow live")
    return "core10_dynamic40_pipeline_ready", notes
