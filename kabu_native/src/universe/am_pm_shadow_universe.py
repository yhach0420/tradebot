"""
Phase 115: Wire Phase114 AM/PM universes to shadow live runner CSVs.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

PUSH_LIMIT = 50

RUNNER_UNIVERSE_FIELDS = (
    "symbol",
    "symbol_key",
    "exchange",
    "passed",
    "source_bucket",
    "selected_reason",
    "rank",
    "volatility_liquidity_score",
    "am_pm_session",
)

AM_SOURCE_BUCKET = "am_dynamic50"
PM_SOURCE_BUCKET = "pm_dynamic50"
AM_SESSION = "am"
PM_SESSION = "pm"


def phase114_am_path(reports_dir: Path, day_stamp: str) -> Path:
    return reports_dir / f"phase114_am_universe_dynamic50_{day_stamp}.csv"


def phase114_pm_path(reports_dir: Path, day_stamp: str) -> Path:
    return reports_dir / f"phase114_pm_universe_dynamic50_{day_stamp}.csv"


def universe_am_path(reports_dir: Path, day_stamp: str) -> Path:
    return reports_dir / f"universe_am_dynamic50_{day_stamp}.csv"


def universe_pm_path(reports_dir: Path, day_stamp: str) -> Path:
    return reports_dir / f"universe_pm_dynamic50_{day_stamp}.csv"


def limit_diagnostics_path(reports_dir: Path, day_stamp: str) -> Path:
    return reports_dir / f"phase114_limit_status_diagnostics_{day_stamp}.csv"


def _norm(code: str) -> str:
    c = str(code).strip().upper().split("@")[0]
    return c if c.endswith(".T") else f"{c}.T"


def load_phase114_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    rows: list[dict[str, str]] = []
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            sym = _norm(str(row.get("symbol") or ""))
            if sym:
                rows.append({k: str(v or "").strip() for k, v in row.items()} | {"symbol": sym})
    return rows


def convert_to_runner_row(
    row: Mapping[str, str],
    *,
    session: str,
    source_bucket: str,
    selected_reason: Optional[str] = None,
) -> dict[str, Any]:
    sym = _norm(row["symbol"])
    ex = int(row.get("exchange") or 1)
    reason = selected_reason or str(row.get("selected_reason") or "")
    vl = row.get("volatility_liquidity_score") or row.get("previous_day_vol_liq_score") or row.get("pm_composite_score") or ""
    return {
        "symbol": sym,
        "symbol_key": str(row.get("symbol_key") or f"{sym.replace('.T', '')}@{ex}"),
        "exchange": ex,
        "passed": "True",
        "source_bucket": source_bucket,
        "selected_reason": reason,
        "rank": str(row.get("rank") or ""),
        "volatility_liquidity_score": vl,
        "am_pm_session": session,
    }


def write_runner_universe(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(RUNNER_UNIVERSE_FIELDS), extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in RUNNER_UNIVERSE_FIELDS})


def validate_runner_universe(
    path: Path,
    *,
    expected_bucket: str,
    expected_session: str,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    ok = True

    def add(cid: str, passed: bool, detail: str) -> None:
        nonlocal ok
        if not passed:
            ok = False
        checks.append({"check_id": cid, "passed": passed, "detail": detail})

    if not path.is_file():
        add("file_exists", False, "missing")
        return {"passed": False, "checks": checks, "total_count": 0, "symbol_count": 0}

    rows: list[dict[str, str]] = []
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for col in RUNNER_UNIVERSE_FIELDS:
            add(f"column_{col}", col in (reader.fieldnames or []), col)
        for row in reader:
            rows.append({k: str(v or "") for k, v in row.items()})

    syms = [r.get("symbol", "") for r in rows]
    dup = len(syms) - len(set(syms))
    buckets = {r.get("source_bucket") for r in rows}
    sessions = {r.get("am_pm_session") for r in rows}
    passed_n = sum(1 for r in rows if str(r.get("passed", "")).lower() in ("true", "1", "yes"))

    add("symbol_count_50", len(rows) == PUSH_LIMIT, f"count={len(rows)}")
    add("no_duplicate_symbols", dup == 0, f"duplicates={dup}")
    add("source_bucket", buckets == {expected_bucket}, f"buckets={buckets}")
    add("am_pm_session", sessions == {expected_session}, f"sessions={sessions}")
    add("all_passed_true", passed_n == len(rows), f"passed={passed_n}/{len(rows)}")
    add("total_count_le_50", len(rows) <= PUSH_LIMIT, f"total={len(rows)}")

    return {
        "passed": ok,
        "checks": checks,
        "total_count": len(rows),
        "symbol_count": len(rows),
        "duplicate_count": dup,
        "source_buckets": sorted(buckets),
        "am_pm_sessions": sorted(sessions),
    }


def load_limit_warnings(limit_path: Path, symbols: set[str]) -> list[dict[str, Any]]:
    if not limit_path.is_file():
        return []
    warnings: list[dict[str, Any]] = []
    with limit_path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            sym = _norm(str(row.get("symbol") or ""))
            if sym not in symbols:
                continue
            warn = str(row.get("shadow_warn") or "").strip()
            if warn or row.get("shadow_exclude_candidate") in ("True", "true", "1"):
                warnings.append(
                    {
                        "symbol": sym,
                        "shadow_warn": warn,
                        "is_limit_up": row.get("is_limit_up"),
                        "is_limit_down": row.get("is_limit_down"),
                        "near_limit_up": row.get("near_limit_up"),
                        "near_limit_down": row.get("near_limit_down"),
                        "shadow_exclude_candidate": row.get("shadow_exclude_candidate"),
                        "note": "warning_only_not_excluded_in_phase115",
                    }
                )
    return warnings


def build_from_phase114(
    reports_dir: Path,
    day_stamp: str,
) -> dict[str, Any]:
    am_in = phase114_am_path(reports_dir, day_stamp)
    pm_in = phase114_pm_path(reports_dir, day_stamp)
    am_out = universe_am_path(reports_dir, day_stamp)
    pm_out = universe_pm_path(reports_dir, day_stamp)

    am_src = load_phase114_rows(am_in)
    pm_src = load_phase114_rows(pm_in)

    am_rows = [
        convert_to_runner_row(
            r,
            session=AM_SESSION,
            source_bucket=AM_SOURCE_BUCKET,
            selected_reason=r.get("selected_reason") or "previous_day_vol_liq_top50",
        )
        for r in sorted(am_src, key=lambda x: int(x.get("rank") or 999))[:PUSH_LIMIT]
    ]
    pm_rows = [
        convert_to_runner_row(
            r,
            session=PM_SESSION,
            source_bucket=PM_SOURCE_BUCKET,
            selected_reason=r.get("selected_reason") or "pm_rescreen_vol_liq_morning_liquidity",
        )
        for r in sorted(pm_src, key=lambda x: int(x.get("rank") or 999))[:PUSH_LIMIT]
    ]

    if len(am_rows) == PUSH_LIMIT:
        write_runner_universe(am_out, am_rows)
    if len(pm_rows) == PUSH_LIMIT:
        write_runner_universe(pm_out, pm_rows)

    return {
        "am_input_exists": am_in.is_file(),
        "pm_input_exists": pm_in.is_file(),
        "am_output": str(am_out),
        "pm_output": str(pm_out),
        "am_row_count": len(am_rows),
        "pm_row_count": len(pm_rows),
    }


def shadow_live_commands(
    day_stamp: str,
    *,
    am_csv_rel: str,
    pm_csv_rel: str,
    pilot_yaml: str,
) -> dict[str, Any]:
    return {
        "session_range_supported": True,
        "session_range_note": (
            "run_small_paper_pilot.py: --session-start/--session-end and "
            "--am-pm-session am|pm (Phase116 runtime overlay, no YAML change)"
        ),
        "am_pm_session_times": {
            "am": {"session_start": "09:03", "session_end": "11:25", "entry_stop": "11:20", "force_close": "11:25"},
            "pm": {"session_start": "12:33", "session_end": "15:23", "entry_stop": "15:18", "force_close": "15:23"},
        },
        "universe_screening_windows": {
            "am": "09:00-09:03",
            "pm": "12:25-12:32",
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
        "morning_shadow_live_explicit": [
            "python kabu_native/scripts/check_small_paper_safety.py",
            (
                "python kabu_native/scripts/run_small_paper_pilot.py --dry-run --source live "
                f"--universe-csv {am_csv_rel} "
                f"--config {pilot_yaml} "
                "--session-start 09:03 --session-end 11:25 --wait-until-session --poll-interval-sec 5"
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
        "afternoon_shadow_live_explicit": [
            "python kabu_native/scripts/check_small_paper_safety.py",
            (
                "python kabu_native/scripts/run_small_paper_pilot.py --dry-run --source live "
                f"--universe-csv {pm_csv_rel} "
                f"--config {pilot_yaml} "
                "--session-start 12:33 --session-end 15:23 --wait-until-session --poll-interval-sec 5"
            ),
        ],
        "do_not_use_full_session": "Omit --full-session; AM and PM are separate runs",
        "position_policy_design": {
            "am_positions_close_before_lunch": True,
            "am_new_entry_stop": "11:20",
            "am_force_close": "11:25",
            "pm_new_entry_stop": "15:18",
            "pm_force_close": "15:23",
            "pm_positions_close_before_market_close": True,
            "no_carry_am_to_pm": True,
            "exit_reasons": ["morning_session_close", "afternoon_session_close"],
            "phase116_status": "shadow_runtime_force_close_enabled",
        },
    }


def determine_verdict(
    *,
    build: Mapping[str, Any],
    am_val: dict[str, Any],
    pm_val: dict[str, Any],
    am_runner_ok: bool,
    pm_runner_ok: bool,
) -> tuple[str, list[str]]:
    notes: list[str] = []
    if not build.get("am_input_exists") or not build.get("pm_input_exists"):
        return "am_pm_universe_incomplete", ["phase114 AM/PM source CSV missing"]
    if am_val.get("total_count", 0) < PUSH_LIMIT or pm_val.get("total_count", 0) < PUSH_LIMIT:
        notes.append(f"am={am_val.get('total_count')} pm={pm_val.get('total_count')}")
        return "am_pm_universe_incomplete", notes
    if am_val.get("duplicate_count", 0) > 0 or pm_val.get("duplicate_count", 0) > 0:
        return "am_pm_universe_incomplete", ["duplicate symbols in AM or PM universe"]
    if not am_val.get("passed") or not pm_val.get("passed"):
        return "am_pm_universe_incomplete", ["universe validation failed"]
    if not am_runner_ok or not pm_runner_ok:
        return "runner_load_failed", ["load_symbols failed for AM or PM CSV"]
    notes.append("AM/PM runner CSVs OK; session-start/end commands ready")
    return "am_pm_shadow_pipeline_ready", notes
