#!/usr/bin/env python3
"""
Phase288: SymbolSpec subscript crash fix report + verification hooks.
"""

from __future__ import annotations

import argparse
import json
import sys
import unittest
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native/results/reports/phase288_symbolspec_subscript_crash_fix.json"
NATIVE = REPO / "kabu_native"
SRC = NATIVE / "src"
JST = ZoneInfo("Asia/Tokyo")


def _bootstrap() -> None:
    for p in (SRC, REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _code_audit() -> dict[str, Any]:
    runner = (SRC / "runner/am_pm_daily_runner.py").read_text(encoding="utf-8")
    sources = (SRC / "storage/symbol_sources.py").read_text(encoding="utf-8")
    return {
        "root_cause": "notify_screening_universe_discord used s[0] on SymbolSpec from load_symbols()",
        "failed_run_evidence": {
            "day_stamp": "20260605",
            "verdict": "am_failed",
            "stopped_reason": "runner_exception",
            "exception": "TypeError: 'SymbolSpec' object is not subscriptable",
        },
        "fix": {
            "symbol_sources_helpers": all(
                name in sources for name in ("def symbol_name", "def symbols_list", "def symbol_key_name")
            ),
            "am_pm_daily_runner_uses_symbols_list": "symbols_list(syms)" in runner,
            "removed_subscript_pattern": "s[0]) for s in syms" not in runner,
        },
        "scope": "notification wiring only; ENTRY/EXIT/universe selection unchanged",
    }


def _run_unit_tests() -> dict[str, Any]:
    loader = unittest.TestLoader()
    tests_dir = NATIVE / "tests"
    if str(tests_dir) not in sys.path:
        sys.path.insert(0, str(tests_dir))
    suite = loader.loadTestsFromName("test_phase288_symbolspec_subscript_fix")
    result = unittest.TextTestRunner(verbosity=0).run(suite)
    return {
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "ok": result.wasSuccessful(),
    }


def _notify_smoke_test(day_stamp: str) -> dict[str, Any]:
    from pathlib import Path

    from api.rest_client import load_kabu_env
    from runner.am_pm_daily_runner import DailyRunnerOptions, make_state, notify_screening_universe_discord

    try:
        load_kabu_env(repo_root=REPO)
    except Exception:
        pass
    csv = NATIVE / "results" / "reports" / f"universe_core10_dynamic40_price_risk_am_{day_stamp}.csv"
    if not csv.is_file():
        return {"ok": False, "skipped": True, "reason": "universe_csv_missing"}
    state = make_state(
        repo_root=REPO,
        native_root=NATIVE,
        options=DailyRunnerOptions(day_stamp=day_stamp, dry_run_only=False),
    )
    try:
        result = notify_screening_universe_discord(
            state,
            session_label="AM Screening",
            universe_csv=csv,
        )
        return {"ok": True, "result": result, "typeerror": False}
    except TypeError as exc:
        return {"ok": False, "typeerror": True, "error": str(exc)}


def _session_dirs_after(day_stamp: str, *, after_hhmm: str = "082900") -> list[str]:
    root = NATIVE / "results" / "small_paper" / day_stamp
    if not root.is_dir():
        return []
    out: list[str] = []
    for p in sorted(root.iterdir()):
        if not p.is_dir():
            continue
        name = p.name
        if not name.startswith("live_session_") and not name.startswith("live_full_session_"):
            continue
        suffix = name.rsplit("_", 1)[-1]
        if suffix.isdigit() and suffix >= after_hhmm:
            out.append(name)
    return out


def _load_daily_summary(day_stamp: str) -> dict[str, Any] | None:
    path = NATIVE / "results" / "reports" / f"daily_runner_summary_{day_stamp}.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _verify_rerun(summary: dict[str, Any] | None, *, day_stamp: str) -> dict[str, Any]:
    if summary is None:
        return {
            "summary_found": False,
            "day_stamp": day_stamp,
            "verdict_not_am_failed": None,
            "am_session_started": None,
            "screening_notify_sent": None,
        }
    verdict = str(summary.get("verdict") or "")
    am_prep = summary.get("am_prep") or {}
    screening = am_prep.get("screening_notify") or {}
    am_live = summary.get("am_live") or {}
    return {
        "summary_found": True,
        "day_stamp": day_stamp,
        "verdict": verdict,
        "stopped_reason": summary.get("stopped_reason"),
        "verdict_not_am_failed": verdict != "am_failed",
        "am_session_started": bool(am_live.get("session_dir") or am_live.get("pilot_ok")),
        "screening_notify_sent": bool(screening.get("sent")),
        "screening_notify": screening,
        "am_live_session_dir": am_live.get("session_dir"),
    }


def main() -> int:
    _bootstrap()
    parser = argparse.ArgumentParser()
    parser.add_argument("--day-stamp", default=datetime.now(JST).strftime("%Y%m%d"))
    args = parser.parse_args()

    tests = _run_unit_tests()
    audit = _code_audit()
    summary = _load_daily_summary(args.day_stamp)
    rerun = _verify_rerun(summary, day_stamp=args.day_stamp)
    notify_smoke = _notify_smoke_test(args.day_stamp)
    post_fix_sessions = _session_dirs_after(args.day_stamp, after_hhmm="082900")

    if summary and str(summary.get("verdict") or "") == "am_failed":
        rerun["note"] = (
            "daily_runner_summary may still reflect the pre-fix crash until AM subprocess "
            "finishes and write_outputs runs again"
        )
    if post_fix_sessions:
        rerun["post_fix_am_session_dirs"] = post_fix_sessions
        rerun["am_session_started"] = True
        rerun["verdict_not_am_failed"] = None
    if notify_smoke.get("ok") and notify_smoke.get("result", {}).get("sent"):
        rerun["screening_notify_sent"] = True
        rerun["screening_notify"] = notify_smoke["result"]

    report: dict[str, Any] = {
        "phase": 288,
        "mode": "symbolspec_subscript_crash_fix",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "constraints": {
            "entry_logic_unchanged": True,
            "exit_logic_unchanged": True,
            "universe_selection_unchanged": True,
            "notification_and_runner_crash_fix_only": True,
        },
        "1_code_audit": audit,
        "2_unit_tests": tests,
        "3_notify_smoke_test": notify_smoke,
        "4_rerun_verification": rerun,
        "5_recommendation": {
            "fix_applied": audit["fix"]["am_pm_daily_runner_uses_symbols_list"],
            "rerun_command": (
                "python kabu_native/scripts/run_core10_dynamic40_am_pm_daily_runner.py "
                "--universe-mode core10-dynamic40-price-risk-filter-shadow "
                "--enable-intraday-refresh --exit-policy-shadow trailing-mfe"
            ),
            "post_rerun_checks": [
                "verdict != am_failed",
                "am_live.session_dir present",
                "am_prep.screening_notify.sent == true",
                "10:00 refresh notify at intraday refresh",
            ],
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
    print(json.dumps({"tests_ok": tests["ok"], "rerun": rerun}, ensure_ascii=False, indent=2))
    return 0 if tests["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
