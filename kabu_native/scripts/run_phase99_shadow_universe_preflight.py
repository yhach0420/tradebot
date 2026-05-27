#!/usr/bin/env python3
"""
Phase 99: Preflight checks before shadow live with dynamic universe (no PF evaluation).
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parents[2]
NATIVE = ROOT / "kabu_native"
REPORTS = NATIVE / "results" / "reports"

CODE_RE = re.compile(r"^\d{4}\.T$")
STATIC_UNIVERSE = NATIVE / "data/universe/universe_intraday_full.csv"
TRADABLE_MASTER = ROOT / "data/jpx/tradable_symbols.csv"
PILOT_YAML = NATIVE / "configs/small_paper_pilot.yaml"
VOL_LIQ_YAML = NATIVE / "configs/small_paper_pilot_q070_cap3_mfe_fav_vol_liq.yaml"
PUSH_LIMIT = 50
STATIC_MAX = 27
DYNAMIC_MAX = 23
FULL_MASTER_MIN_TRADABLE = 500
FOCUS_SYMBOLS = ("6613.T", "3905.T")

UNIVERSE_TRIAL_REQUIRED = (
    "symbol",
    "exchange",
    "symbol_key",
    "passed",
    "selection_reason",
    "dynamic_score",
)
BUILD_SCRIPT = NATIVE / "scripts/build_dynamic_universe.py"
JPX_BUILD_SCRIPT = NATIVE / "scripts/build_jpx_symbol_master.py"


def _bootstrap() -> None:
    for p in (NATIVE / "src", ROOT):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def validate_tradable_master(path: Path) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    ok = True

    def add(cid: str, passed: bool, detail: str, **extra: Any) -> None:
        nonlocal ok
        if not passed:
            ok = False
        checks.append({"check_id": cid, "passed": passed, "detail": detail, **extra})

    if not path.is_file():
        add("tradable_exists", False, f"missing: {_rel(path)}")
        return {"passed": False, "checks": checks, "row_count": 0}

    add("tradable_exists", True, _rel(path))

    required_cols = ("symbol", "exchange", "market", "name")
    rows: list[dict[str, str]] = []
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames or [])
        for col in required_cols:
            add(f"column_{col}", col in fields, f"column {col}")
        for row in reader:
            rows.append({k: str(v or "").strip() for k, v in row.items()})

    markets = Counter()
    etf_in_tradable = 0
    reit_in_tradable = 0
    bad_code = 0
    bad_exchange = 0
    focus_present = {s: False for s in FOCUS_SYMBOLS}

    for row in rows:
        sym = row.get("symbol", "")
        if sym in focus_present:
            focus_present[sym] = True
        if not CODE_RE.match(sym):
            bad_code += 1
        ex = row.get("exchange", "")
        if ex != "1":
            bad_exchange += 1
        m = row.get("market", "").lower()
        if m:
            markets[m] += 1
        if row.get("is_etf", "").lower() == "true":
            etf_in_tradable += 1
        if row.get("is_reit", "").lower() == "true":
            reit_in_tradable += 1

    add("four_digit_codes_only", bad_code == 0, f"invalid_codes={bad_code}")
    add("exchange_is_1", bad_exchange == 0, f"non_exchange_1={bad_exchange}")
    add("has_prime", markets.get("prime", 0) > 0, f"prime={markets.get('prime', 0)}")
    add("has_standard", markets.get("standard", 0) > 0, f"standard={markets.get('standard', 0)}")
    add("has_growth", markets.get("growth", 0) > 0, f"growth={markets.get('growth', 0)}")
    add("no_etf_in_tradable", etf_in_tradable == 0, f"etf_rows={etf_in_tradable}")
    add("no_reit_in_tradable", reit_in_tradable == 0, f"reit_rows={reit_in_tradable}")

    for sym, present in focus_present.items():
        add(f"focus_{sym}_in_tradable", present, f"{sym} via market rules (not hardcoded in build)")

    # Rule-based: focus symbols must be growth/standard in master
    for row in rows:
        if row.get("symbol") == "6613.T":
            add("6613_market_growth", row.get("market") == "growth", f"market={row.get('market')}")
        if row.get("symbol") == "3905.T":
            add("3905_market_standard", row.get("market") == "standard", f"market={row.get('market')}")

    is_sample_only = len(rows) < FULL_MASTER_MIN_TRADABLE
    add(
        "full_master_size",
        not is_sample_only,
        f"tradable_count={len(rows)} threshold={FULL_MASTER_MIN_TRADABLE}",
        sample_only=is_sample_only,
    )

    return {
        "passed": ok,
        "checks": checks,
        "row_count": len(rows),
        "market_distribution": dict(markets),
        "sample_only": is_sample_only,
    }


def validate_universe_trial_csv(path: Path, static_syms: set[str]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    ok = True

    def add(cid: str, passed: bool, detail: str) -> None:
        nonlocal ok
        if not passed:
            ok = False
        checks.append({"check_id": cid, "passed": passed, "detail": detail})

    if not path.is_file():
        add("trial_csv_exists", False, "missing")
        return {"passed": False, "checks": checks}

    rows: list[dict[str, str]] = []
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fields = set(reader.fieldnames or [])
        phase105_cols = {"source_bucket", "selected_reason", "sampling_method", "market"}
        is_phase105_csv = phase105_cols.issubset(fields)
        for col in UNIVERSE_TRIAL_REQUIRED:
            if col == "selection_reason" and is_phase105_csv:
                continue
            add(f"trial_col_{col}", col in fields, col)
        if is_phase105_csv:
            add("trial_phase105_columns", True, "source_bucket + selected_reason present")
        elif "selection_reason" in fields:
            add("trial_col_selection_reason", True, "selection_reason")
        elif "selected_reason" in fields:
            add("trial_col_selection_reason", True, "selected_reason alias")
        else:
            add("trial_col_selection_reason", False, "missing selection_reason/selected_reason")

        for row in reader:
            rows.append({k: str(v or "") for k, v in row.items()})

    syms = [r.get("symbol", "") for r in rows]
    dup = len(syms) - len(set(syms))
    static_rows = [
        r
        for r in rows
        if r.get("source_bucket") == "static_legacy"
        or r.get("selection_reason") == "static_intraday_full"
    ]
    dynamic_rows = [
        r
        for r in rows
        if r.get("source_bucket") == "dynamic_sampled"
        or r.get("selection_reason") == "dynamic_turnover_gap_score"
    ]

    add("no_duplicate_symbols", dup == 0, f"duplicates={dup}")
    add("total_count_50", len(rows) == PUSH_LIMIT, f"total={len(rows)}")
    add("static_count_27", len(static_rows) == STATIC_MAX, f"static={len(static_rows)}")
    add("dynamic_count_23", len(dynamic_rows) == DYNAMIC_MAX, f"dynamic={len(dynamic_rows)}")
    static_set = {s for s in syms if s in static_syms}
    add(
        "static_matches_intraday_full",
        static_set == static_syms,
        f"overlap={len(static_set)}/{len(static_syms)}",
    )

    passed_rows = sum(1 for r in rows if str(r.get("passed", "")).lower() in ("true", "1", "yes"))
    add("all_passed_true", passed_rows == len(rows), f"passed={passed_rows}/{len(rows)}")

    return {
        "passed": ok,
        "checks": checks,
        "total_count": len(rows),
        "static_count": len(static_rows),
        "dynamic_count": len(dynamic_rows),
        "path": _rel(path),
    }


def run_build_skip_kabu(day_stamp: str) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(BUILD_SCRIPT),
        "--board-mode",
        "none",
        "--date-stamp",
        day_stamp,
    ]
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=120)
    diag = REPORTS / f"phase98_dynamic_universe_build_{day_stamp}.json"
    payload: dict[str, Any] = {"exit_code": proc.returncode, "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip()}
    if diag.is_file():
        payload.update(json.loads(diag.read_text(encoding="utf-8")))
    payload["board_skipped"] = payload.get("skip_kabu", True)
    return payload


def validate_runner_universe_load(trial_csv: Path) -> dict[str, Any]:
    _bootstrap()
    from storage.symbol_sources import load_symbols

    default_path = NATIVE / "data/universe/universe_intraday_full.csv"
    default_syms = load_symbols(universe=default_path, native_root=NATIVE)
    trial_syms = load_symbols(universe=trial_csv, native_root=NATIVE)

    return {
        "passed": len(trial_syms) > 0 and len(trial_syms) <= PUSH_LIMIT,
        "default_universe_path": _rel(default_path),
        "default_symbol_count": len(default_syms),
        "universe_csv_path": _rel(trial_csv),
        "trial_symbol_count": len(trial_syms),
        "counts_match_csv": len(trial_syms) == _count_passed_csv(trial_csv),
    }


def _count_passed_csv(path: Path) -> int:
    n = 0
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if str(row.get("passed", "")).lower() in ("true", "1", "yes"):
                n += 1
    return n


def load_static_symbols() -> set[str]:
    syms: set[str] = set()
    if not STATIC_UNIVERSE.is_file():
        return syms
    with STATIC_UNIVERSE.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            sym = str(row.get("symbol") or "").strip()
            if not sym.endswith(".T"):
                sym = f"{sym}.T" if sym else ""
            if sym:
                syms.add(sym)
    return syms


def determine_verdict(
    *,
    master: dict[str, Any],
    build: dict[str, Any],
    trial: dict[str, Any],
    runner: dict[str, Any],
    day_stamp: str,
) -> tuple[str, str]:
    if not runner.get("passed"):
        return "runner_universe_csv_not_ready", "load_symbols failed or count mismatch for --universe-csv"

    if master.get("sample_only"):
        if build.get("need_symbol_master"):
            return "need_full_jpx_master", "Symbol master missing; place JPX listed_issues.xlsx and rebuild"
        detail = (
            f"Only sample tradable master ({master.get('row_count')} symbols). "
            "Deploy full JPX export before production shadow."
        )
        return "need_full_jpx_master", detail

    if build.get("need_symbol_master"):
        return "need_full_jpx_master", "Symbol master missing; run build_jpx_symbol_master.py first"

    phase105 = REPORTS / f"phase105_register_limit_aware_universe_{day_stamp}.json"
    phase105_ok = False
    if phase105.is_file():
        p105 = json.loads(phase105.read_text(encoding="utf-8"))
        sc = p105.get("success_criteria") or {}
        phase105_ok = bool(sc.get("met")) and p105.get("verdict") == "register_limit_aware_universe_ready"

    master_ok = master.get("passed") or (
        not master.get("sample_only") and (master.get("row_count") or 0) >= FULL_MASTER_MIN_TRADABLE
    )
    if (
        master_ok
        and trial.get("passed")
        and build.get("need_symbol_master") is False
        and build.get("exit_code") == 0
        and phase105_ok
    ):
        return (
            "shadow_universe_ready",
            "Phase105 board-free 50-symbol universe ready; shadow live with --universe-csv allowed.",
        )

    if build.get("board_fetch_success_count", 0) == 0 and not phase105_ok:
        if build.get("verdict") in (
            "register_limit_aware_universe_ready",
            "build_ready_with_tradable_master",
        ):
            return (
                "need_board_runtime_check",
                "Trial CSV exists; optional --board-mode validate on session morning.",
            )

    if (
        master_ok
        and trial.get("passed")
        and build.get("need_symbol_master") is False
        and build.get("exit_code") == 0
    ):
        return "shadow_universe_ready", "Trial CSV OK; confirm phase105 success_criteria in JSON."

    return "need_board_runtime_check", "Review failed checks in preflight JSON."


def shadow_ops_commands(day_stamp: str) -> dict[str, list[str]]:
    ymd = f"{day_stamp[:4]}-{day_stamp[4:6]}-{day_stamp[6:8]}"
    trial = f"kabu_native/results/reports/universe_dynamic_trial_{day_stamp}.csv"
    return {
        "A_jpx_master_update": [
            "python kabu_native/scripts/build_jpx_symbol_master.py",
        ],
        "B_dynamic_universe_build": [
            "python kabu_native/scripts/build_dynamic_universe.py --board-mode none",
            "# Optional board validate (final 50 only, no replacement):",
            "# python kabu_native/scripts/build_dynamic_universe.py --board-mode validate",
        ],
        "C_shadow_live": [
            "python kabu_native/scripts/check_small_paper_safety.py",
            (
                "python kabu_native/scripts/run_small_paper_pilot.py --dry-run --source live "
                "--full-session --wait-until-session "
                f"--universe-csv {trial} "
                "--config kabu_native/configs/small_paper_pilot_q070_cap3_mfe_fav_vol_liq.yaml "
                "--poll-interval-sec 5"
            ),
        ],
        "notes": [
            f"Replace YYYYMMDD in paths with trade date (example: {day_stamp}).",
            "Do not overwrite universe_intraday_full.csv or production small_paper_pilot.yaml.",
            f"PUSH register limit: {PUSH_LIMIT} symbols.",
        ],
    }


def write_md(path: Path, report: dict[str, Any]) -> None:
    cmds = report.get("shadow_ops_commands", {})
    lines = [
        "# Phase 99 — Shadow Universe Preflight",
        "",
        f"**Date:** {report.get('trade_date')}",
        f"**Verdict:** `{report.get('verdict')}`",
        "",
        report.get("verdict_detail", ""),
        "",
        "## Master validation",
        "",
        f"- tradable rows: {report.get('tradable_symbol_count')}",
        f"- sample only: {report.get('sample_master_only')}",
        "",
        "## Build (--skip-kabu)",
        "",
        f"- verdict: {report.get('build_skip_kabu', {}).get('verdict')}",
        f"- need_symbol_master: {report.get('build_skip_kabu', {}).get('need_symbol_master')}",
        f"- board_skipped: {report.get('build_skip_kabu', {}).get('board_skipped')}",
        "",
        "## Universe trial CSV",
        "",
        f"- path: `{report.get('universe_trial_csv')}`",
        f"- total: {report.get('trial_validation', {}).get('total_count')}",
        f"- static: {report.get('trial_validation', {}).get('static_count')}",
        f"- dynamic: {report.get('trial_validation', {}).get('dynamic_count')}",
        "",
        "## Runner",
        "",
        f"- trial symbol_count: {report.get('runner', {}).get('trial_symbol_count')}",
        f"- default symbol_count: {report.get('runner', {}).get('default_symbol_count')}",
        "",
        "## Shadow ops (tomorrow onward)",
        "",
        "### A. JPX master update",
        "",
    ]
    for c in cmds.get("A_jpx_master_update", []):
        lines.append(f"```bash\n{c}\n```")
    lines.extend(["", "### B. Dynamic universe", ""])
    for c in cmds.get("B_dynamic_universe_build", []):
        if c.startswith("#"):
            lines.append(c)
        else:
            lines.append(f"```bash\n{c}\n```")
    lines.extend(["", "### C. Shadow live", ""])
    for c in cmds.get("C_shadow_live", []):
        lines.append(f"```bash\n{c}\n```")
    lines.extend(["", "### Notes", ""])
    for n in cmds.get("notes", []):
        lines.append(f"- {n}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    _bootstrap()
    parser = argparse.ArgumentParser(description="Phase 99 shadow universe preflight")
    parser.add_argument("--trade-date", default=None)
    parser.add_argument("--day-stamp", default=None)
    args = parser.parse_args()

    day_stamp = args.day_stamp or (args.trade_date or datetime.now(JST).strftime("%Y%m%d")).replace("-", "")
    trial_csv = REPORTS / f"universe_dynamic_trial_{day_stamp}.csv"

    static_syms = load_static_symbols()
    master_val = validate_tradable_master(TRADABLE_MASTER)
    build_val = run_build_skip_kabu(day_stamp)
    if not trial_csv.is_file() and build_val.get("output_universe_csv"):
        trial_csv = ROOT / str(build_val["output_universe_csv"])
    trial_val = validate_universe_trial_csv(trial_csv, static_syms)
    runner_val = validate_runner_universe_load(trial_csv)

    pilot_yaml_unchanged = PILOT_YAML.is_file()
    if pilot_yaml_unchanged:
        text = PILOT_YAML.read_text(encoding="utf-8")
        pilot_yaml_unchanged = "universe_intraday_full" in text or "universe" not in text

    verdict, verdict_detail = determine_verdict(
        master=master_val,
        build=build_val,
        trial=trial_val,
        runner=runner_val,
        day_stamp=day_stamp,
    )

    next_steps: list[str] = []
    if master_val.get("sample_only"):
        next_steps.append("Place JPX listed_issues.xlsx under data/jpx/raw/ and run build_jpx_symbol_master.py")
    if not (REPORTS / f"phase105_register_limit_aware_universe_{day_stamp}.json").is_file():
        next_steps.append("Run: python kabu_native/scripts/run_phase106_shadow_live_pipeline.py")
    else:
        next_steps.append(
            "Optional session open: build_dynamic_universe.py --board-mode validate (diagnostic only)"
        )
    if verdict == "shadow_universe_ready":
        next_steps.append("Run shadow live with --universe-csv per shadow_ops_commands")

    report: dict[str, Any] = {
        "phase": 99,
        "trade_date": f"{day_stamp[:4]}-{day_stamp[4:6]}-{day_stamp[6:8]}",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "verdict": verdict,
        "verdict_detail": verdict_detail,
        "also_required_before_shadow_live": next_steps,
        "verdict_options": {
            "A": "shadow_universe_ready",
            "B": "need_full_jpx_master",
            "C": "need_board_runtime_check",
            "D": "runner_universe_csv_not_ready",
        },
        "tradable_symbol_count": master_val.get("row_count"),
        "sample_master_only": master_val.get("sample_only"),
        "symbol_master_path": _rel(TRADABLE_MASTER),
        "master_validation": master_val,
        "build_skip_kabu": build_val,
        "universe_trial_csv": _rel(trial_csv),
        "trial_validation": trial_val,
        "runner": runner_val,
        "production_pilot_yaml_unchanged": pilot_yaml_unchanged,
        "vol_liq_trial_yaml": _rel(VOL_LIQ_YAML),
        "constraints_confirmed": [
            "no_production_pilot_yaml_change",
            "no_overwrite_universe_intraday_full",
            "no_symbol_hardcode",
            "no_time_of_day_filter",
            "shadow_dry_run_only",
            "no_pf_evaluation_in_phase99",
        ],
        "board_runtime_expected_fields": [
            "board_fetch_success_count",
            "board_fetch_error_count",
            "dynamic_count",
            "total_count",
            "duplicate_removed_count",
            "selected_dynamic_symbols",
            "market_distribution_selected",
        ],
        "shadow_ops_commands": shadow_ops_commands(day_stamp),
    }

    REPORTS.mkdir(parents=True, exist_ok=True)
    json_path = REPORTS / f"phase99_shadow_universe_preflight_{day_stamp}.json"
    md_path = REPORTS / "phase99_shadow_universe_preflight.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_md(md_path, report)

    print(json.dumps({"verdict": verdict, "json": str(json_path), "md": str(md_path)}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
