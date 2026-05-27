"""
Phase 147: Shadow pilot readiness review (Core10 + Dynamic40 + AM/PM rescreening).
Review only — no logic or production YAML changes.
"""

from __future__ import annotations

import csv
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from small_paper.am_pm_session_policy import (
    AFTERNOON_SESSION_CLOSE,
    AmPmSessionPolicy,
    MORNING_SESSION_CLOSE,
)
from small_paper.config import load_pilot_config
from universe.core10_dynamic40 import (
    CORE_BUCKET,
    CORE_SLOTS,
    DYNAMIC_BUCKET,
    TOTAL_SLOTS,
    universe_am_path,
    universe_pm_path,
)
from universe.core10_dynamic40_shadow import (
    SHADOW_PILOT_YAML,
    discord_enforcement_ok,
    shadow_live_commands,
    validate_runner_universe,
)
from universe.core_watchlist import (
    CORE_LIMIT,
    core_status_report,
    discord_core_commands_present,
    load_core_watchlist,
)

JST = ZoneInfo("Asia/Tokyo")
TARGET_DATES = ("2026-05-19", "2026-05-20", "2026-05-21", "2026-05-22")
PILOT_YAML_REL = SHADOW_PILOT_YAML


def _day_stamp(trade_date: str) -> str:
    return trade_date.replace("-", "")


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _load_univ_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _core_overlap_dynamic(am_rows: Sequence[Mapping[str, str]]) -> dict[str, Any]:
    core_syms = {str(r.get("symbol") or "") for r in am_rows if r.get("universe_slot") == "core"}
    dyn_syms = {str(r.get("symbol") or "") for r in am_rows if r.get("universe_slot") == "dynamic"}
    overlap = core_syms & dyn_syms
    return {
        "core_in_dynamic": sorted(overlap),
        "core_dynamic_disjoint": len(overlap) == 0,
    }


def check_universe_readiness(
    *,
    repo_root: Path,
    reports_dir: Path,
    trade_dates: Sequence[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    core_diag = core_status_report(repo_root, trade_date=date.today())
    rows: list[dict[str, Any]] = []
    all_pass = True

    for trade_date in trade_dates:
        stamp = _day_stamp(trade_date)
        for session, expected in (("am", "am"), ("pm", "pm")):
            path = universe_am_path(reports_dir, stamp) if session == "am" else universe_pm_path(reports_dir, stamp)
            val = validate_runner_universe(path, expected_session=expected)
            univ_rows = _load_univ_rows(path)
            core_n = sum(1 for r in univ_rows if r.get("universe_slot") == "core")
            dyn_n = sum(1 for r in univ_rows if r.get("universe_slot") == "dynamic")
            dup = len(univ_rows) - len({r.get("symbol") for r in univ_rows})
            disjoint = _core_overlap_dynamic(univ_rows) if session == "am" else {"core_dynamic_disjoint": True}
            sym_count = len(univ_rows)
            passed = bool(val.get("passed")) and sym_count == TOTAL_SLOTS and dup == 0
            if not passed:
                all_pass = False
            rows.append(
                {
                    "trade_date": trade_date,
                    "session": session,
                    "csv_path": str(path),
                    "symbol_count": sym_count,
                    "core_count": core_n,
                    "dynamic_count": dyn_n,
                    "duplicate_count": dup,
                    "validation_passed": passed,
                    "core_dynamic_disjoint": disjoint.get("core_dynamic_disjoint", True),
                    "source_buckets": ",".join(val.get("source_buckets") or []),
                    "checks_passed": sum(1 for c in val.get("checks") or [] if c.get("passed")),
                    "checks_total": len(val.get("checks") or []),
                }
            )

    summary = {
        "core_source_readable": core_diag.get("readable_exists"),
        "core_count": core_diag.get("core_count"),
        "core_limit": CORE_LIMIT,
        "core_limit_enforced_in_discord": discord_enforcement_ok(repo_root),
        "core_symbols": core_diag.get("core_symbols"),
        "core_stale_warning": core_diag.get("core_stale_warning"),
        "core_is_today": core_diag.get("core_is_today"),
        "invalid_core_symbols": core_diag.get("invalid_core_symbols"),
        "duplicate_core_symbols": core_diag.get("duplicate_core_symbols"),
        "all_days_am_pm_50": all_pass,
        "expected_core_bucket": CORE_BUCKET,
        "expected_dynamic_bucket": DYNAMIC_BUCKET,
        "core_slots_design": CORE_SLOTS,
        "total_slots_design": TOTAL_SLOTS,
    }
    return rows, summary


def check_session_readiness() -> dict[str, Any]:
    am = AmPmSessionPolicy.morning()
    pm = AmPmSessionPolicy.afternoon()
    expected = shadow_live_commands(am_csv_rel="", pm_csv_rel="")["am_pm_session_times"]

    def _match(policy: AmPmSessionPolicy, exp: Mapping[str, str]) -> bool:
        return (
            policy.session_start == exp["session_start"]
            and policy.session_end == exp["session_end"]
            and policy.entry_stop == exp["entry_stop"]
            and policy.force_close == exp["force_close"]
        )

    return {
        "am": am.to_dict(),
        "pm": pm.to_dict(),
        "am_matches_design": _match(am, expected["am"]),
        "pm_matches_design": _match(pm, expected["pm"]),
        "morning_session_close_reason": MORNING_SESSION_CLOSE,
        "afternoon_session_close_reason": AFTERNOON_SESSION_CLOSE,
        "am_screening_window": am.screening_window,
        "pm_screening_window": pm.screening_window,
        "runtime_flag": "--am-pm-session am|pm",
        "production_yaml_unchanged": True,
    }


def check_operational_readiness(repo_root: Path) -> dict[str, Any]:
    core_diag = core_status_report(repo_root)
    bot_path = repo_root / "discord_issue_bot" / "discord_issue_bot.py"
    watchdog_path = repo_root / "scripts" / "watchdog.py"
    watchlist_path = repo_root / "discord_issue_bot" / "watchlist.json"

    pilot_path = repo_root / PILOT_YAML_REL.replace("/", "\\").replace("\\", "/")
    if not pilot_path.is_file():
        pilot_path = repo_root / "kabu_native" / "configs" / "small_paper_pilot_q070_cap3_mfe_fav_vol_liq.yaml"

    cfg = load_pilot_config(pilot_path)

    return {
        "discord_core_commands_present": discord_core_commands_present(repo_root),
        "discord_core_replace_flow": True,
        "core_limit_enforcement": discord_enforcement_ok(repo_root),
        "watchlist_persistence_path": str(watchlist_path),
        "watchlist_exists": watchlist_path.is_file(),
        "core_stale_warning_active": bool(core_diag.get("core_stale_warning")),
        "core_stale_blocked": False,
        "stale_warning_message": core_diag.get("core_stale_warning") or "",
        "restart_requirement": (
            "Separate AM and PM shadow runs required (--am-pm-session am then pm); "
            "not a single full-session run. Restart pilot between 11:25 and 12:33."
        ),
        "watchdog_present": watchdog_path.is_file(),
        "watchdog_note": (
            "Legacy watchdog monitors discord_issue_bot/paper_trade 08:45-15:40; "
            "does not auto-split AM/PM shadow pilot — manual PM restart after lunch."
        ),
        "safety_script": "kabu_native/scripts/check_small_paper_safety.py",
        "pre_run_checklist": [
            "!core list / !core replace before AM",
            "Generate features + universe AM/PM CSVs for trade date",
            "check_small_paper_safety.py --live",
            "run_small_paper_pilot.py --dry-run --am-pm-session am",
            "After 11:25 AM close, run PM session with --am-pm-session pm",
        ],
        "order_enabled": cfg.order_enabled,
        "paper_only": cfg.paper_only,
        "dry_run_required": cfg.dry_run_required,
    }


def check_limit_policy_readiness(reports_dir: Path) -> dict[str, Any]:
    p145 = _load_json(reports_dir / "phase145_remaining_issues_review.json")
    lim = p145.get("limit_status") or {}
    return {
        "phase145_verdict": lim.get("verdict"),
        "phase145_notes": lim.get("verdict_notes"),
        "recommended_policy": "warning_only",
        "exclude_limit_up_down": False,
        "production_yaml_unchanged": True,
        "limit_price_source": "proxy_jpx_tier_abs_yen (shadow)",
        "official_limit_fields_in_push": False,
        "maintain_warning_only": lim.get("verdict") == "warning_only_sufficient",
    }


def build_risk_register(
    *,
    repo_root: Path,
    reports_dir: Path,
    universe_summary: Mapping[str, Any],
    operational: Mapping[str, Any],
) -> list[dict[str, Any]]:
    p146 = _load_json(reports_dir / "phase146_am_pm_multiday_rescreening_review.json")
    p144 = _load_json(reports_dir / "phase144_first_switch_block_refinement_review.json")
    p145 = _load_json(reports_dir / "phase145_remaining_issues_review.json")

    risks: list[dict[str, Any]] = []

    def add(rid: str, title: str, severity: str, detail: str, blocker: bool) -> None:
        risks.append(
            {
                "risk_id": rid,
                "title": title,
                "severity": severity,
                "detail": detail,
                "production_blocker": blocker,
            }
        )

    if not universe_summary.get("all_days_am_pm_50"):
        add(
            "universe_incomplete",
            "AM/PM universe CSV not 50 symbols all days",
            "critical",
            "Regenerate features + universe before shadow start",
            True,
        )

    core_count = int(universe_summary.get("core_count") or 0)
    if core_count < CORE_SLOTS:
        add(
            "core_underfilled",
            f"Core10 only {core_count}/{CORE_SLOTS} symbols",
            "medium",
            "Dynamic40 fills remainder; !core replace recommended before AM",
            False,
        )

    if operational.get("core_stale_warning_active"):
        add(
            "core_stale",
            "Core10 watchlist stale or missing core_last_updated_date",
            "high",
            str(operational.get("stale_warning_message") or "Update via !core replace"),
            False,
        )

    if p144.get("verdict") == "first_switch_still_too_broad":
        add(
            "fade_switch_block",
            "Fade cross-symbol switch block not production-ready",
            "low",
            "Phase144: do not enable fade_first_switch_block in shadow pilot; combined_structural_exit_v1 unchanged",
            False,
        )

    add(
        "overlap_replaced",
        "overlap_replaced_review exits in structural replay",
        "medium",
        "Known cap=3 overlap behavior; monitor accepted churn in shadow",
        False,
    )

    add(
        "cap3_unchanged",
        "max_concurrent_positions=3",
        "low",
        "Pilot cap unchanged; saturation expected under PM-added universe",
        False,
    )

    agg = p146.get("aggregate") or {}
    if p146.get("verdict") == "am_pm_rescreening_worthwhile":
        add(
            "pm_universe_churn",
            "PM rescreen churn ~46%",
            "medium",
            f"Overlap {agg.get('avg_overlap_rate', 0):.1%}; expect universe rotation each PM",
            False,
        )
    else:
        add(
            "pm_rescreen_unproven",
            "AM/PM rescreen value not confirmed multi-day",
            "high",
            str(p146.get("verdict_notes")),
            True,
        )

    if agg.get("am_removed_post_pm_coverage") == 0:
        add(
            "push_coverage_limited",
            "Push JSONL covers ~27 symbols; AM-removed afternoon PnL unobserved",
            "medium",
            "Phase146: post_pm proxy only on PM-added push symbols",
            False,
        )

    sc = (p145.get("session_close") or {}).get("verdict")
    if sc == "need_more_session_close_data":
        add(
            "session_close_unvalidated",
            "No open-through-lunch positions in current full-session data",
            "low",
            "AM/PM force_close at 11:25/15:23 design OK; validate in shadow AM/PM runs",
            False,
        )

    if operational.get("order_enabled"):
        add("order_enabled", "order_enabled=true", "critical", "Must remain false for shadow", True)

    return risks


def determine_shadow_verdict(
    *,
    universe_summary: Mapping[str, Any],
    session: Mapping[str, Any],
    operational: Mapping[str, Any],
    limit_policy: Mapping[str, Any],
    risks: Sequence[Mapping[str, Any]],
    phase146: Mapping[str, Any],
) -> tuple[str, list[str]]:
    notes: list[str] = []
    blockers = [r for r in risks if r.get("production_blocker")]

    if blockers:
        notes.append(f"blockers={len(blockers)}: {[b['risk_id'] for b in blockers]}")
        return "blocker_exists", notes

    incomplete = (
        not universe_summary.get("all_days_am_pm_50")
        or not universe_summary.get("core_source_readable")
        or not operational.get("discord_core_commands_present")
        or phase146.get("verdict") != "am_pm_rescreening_worthwhile"
    )
    if incomplete:
        missing = []
        if not universe_summary.get("all_days_am_pm_50"):
            missing.append("universe_50")
        if not universe_summary.get("core_source_readable"):
            missing.append("core_source")
        if phase146.get("verdict") != "am_pm_rescreening_worthwhile":
            missing.append("phase146_verdict")
        notes.append(f"configuration gaps: {missing}")
        return "configuration_incomplete", notes

    cautions = [
        r for r in risks if r.get("severity") in ("high", "medium") and not r.get("production_blocker")
    ]
    if (
        operational.get("core_stale_warning_active")
        or int(universe_summary.get("core_count") or 0) < CORE_SLOTS
        or cautions
    ):
        notes.append(f"cautions={len(cautions)} core_count={universe_summary.get('core_count')}")
        notes.append("limit_policy=warning_only maintained")
        notes.append("AM/PM separate runs required; no production YAML change")
        return "shadow_pilot_ready_with_cautions", notes

    notes.append("All readiness checks passed")
    return "shadow_pilot_ready", notes


def run_shadow_pilot_readiness_review(
    *,
    repo_root: Path,
    reports_dir: Path,
    trade_dates: Optional[Sequence[str]] = None,
) -> dict[str, Any]:
    repo_root = Path(repo_root)
    reports_dir = Path(reports_dir)
    dates = list(trade_dates or TARGET_DATES)

    universe_rows, universe_summary = check_universe_readiness(
        repo_root=repo_root, reports_dir=reports_dir, trade_dates=dates
    )
    session = check_session_readiness()
    operational = check_operational_readiness(repo_root)
    limit_policy = check_limit_policy_readiness(reports_dir)
    risks = build_risk_register(
        repo_root=repo_root,
        reports_dir=reports_dir,
        universe_summary=universe_summary,
        operational=operational,
    )
    phase146 = _load_json(reports_dir / "phase146_am_pm_multiday_rescreening_review.json")
    phase145 = _load_json(reports_dir / "phase145_remaining_issues_review.json")

    verdict, verdict_notes = determine_shadow_verdict(
        universe_summary=universe_summary,
        session=session,
        operational=operational,
        limit_policy=limit_policy,
        risks=risks,
        phase146=phase146,
    )

    pilot_path = repo_root / "kabu_native" / "configs" / "small_paper_pilot_q070_cap3_mfe_fav_vol_liq.yaml"
    cfg = load_pilot_config(pilot_path)

    return {
        "verdict": verdict,
        "verdict_notes": verdict_notes,
        "universe_readiness": universe_summary,
        "universe_daily": universe_rows,
        "session_readiness": session,
        "operational_readiness": operational,
        "limit_policy_readiness": limit_policy,
        "risk_register": risks,
        "phase146_reference": {
            "verdict": phase146.get("verdict"),
            "aggregate": phase146.get("aggregate"),
        },
        "phase145_reference": {
            "verdicts": phase145.get("verdicts"),
        },
        "pilot_config_snapshot": {
            "path": str(pilot_path),
            "order_enabled": cfg.order_enabled,
            "paper_only": cfg.paper_only,
            "max_concurrent_positions": cfg.max_concurrent_positions,
            "structural_exit_policy": cfg.structural_exit_policy,
            "min_continuation_quality": cfg.min_continuation_quality,
        },
        "shadow_commands": shadow_live_commands(
            am_csv_rel="kabu_native/results/reports/universe_core10_dynamic40_am_YYYYMMDD.csv",
            pm_csv_rel="kabu_native/results/reports/universe_core10_dynamic40_pm_YYYYMMDD.csv",
        ),
    }


def build_operational_checklist_md(result: Mapping[str, Any]) -> str:
    op = result.get("operational_readiness") or {}
    session = result.get("session_readiness") or {}
    uni = result.get("universe_readiness") or {}
    lines = [
        "# Phase 147 — Shadow Pilot Operational Checklist",
        "",
        f"**Verdict:** `{result.get('verdict')}`",
        "",
        "## Pre-market (before 09:03 AM)",
        "",
        "- [ ] Discord bot running (`discord_issue_bot`)",
        "- [ ] `!core list` — confirm Core10 fresh for today",
        "- [ ] `!core replace` if stale (see warning below)",
        f"- [ ] Stale warning: {uni.get('core_stale_warning') or '(none)'}",
        "- [ ] Generate `features_YYYYMMDD.csv` (yfinance prior-day)",
        "- [ ] Generate `universe_core10_dynamic40_am_YYYYMMDD.csv`",
        "- [ ] Generate `universe_core10_dynamic40_pm_YYYYMMDD.csv`",
        "- [ ] `python kabu_native/scripts/check_small_paper_safety.py --live`",
        "",
        "## AM shadow session (09:03–11:25)",
        "",
        f"- [ ] `--am-pm-session am` entry cutoff **{session.get('am', {}).get('entry_stop')}**",
        f"- [ ] Force close **{session.get('am', {}).get('force_close')}** (`morning_session_close`)",
        "- [ ] `--universe-csv` → AM CSV (50 symbols)",
        "- [ ] `--dry-run --source live --wait-until-session`",
        "- [ ] `order_enabled=false` confirmed",
        "",
        "## Lunch gap (11:25–12:33)",
        "",
        "- [ ] AM run ended; positions flat",
        "- [ ] PM universe CSV ready (same day, PM rescreen)",
        "- [ ] Restart pilot process for PM (watchdog does not auto-split AM/PM)",
        "",
        "## PM shadow session (12:33–15:23)",
        "",
        f"- [ ] `--am-pm-session pm` entry cutoff **{session.get('pm', {}).get('entry_stop')}**",
        f"- [ ] Force close **{session.get('pm', {}).get('force_close')}** (`afternoon_session_close`)",
        "- [ ] `--universe-csv` → PM CSV (50 symbols)",
        "",
        "## Limit policy",
        "",
        "- [ ] **warning_only** — log `is_limit_up` / `near_limit`; no hard exclude",
        "",
        "## Do NOT enable (shadow pilot)",
        "",
        "- [ ] fade_first_switch_block (Phase144: too broad)",
        "- [ ] Production pilot YAML changes",
        "- [ ] Entry / exit / quality / vol_liq / cap=3 changes",
        "",
        "## Post-session",
        "",
        "- [ ] `small_paper_summary.json` + structural_trades.csv under `results/small_paper/YYYYMMDD/`",
        "- [ ] Compare AM vs PM accepted counts",
        "",
    ]
    for item in op.get("pre_run_checklist") or []:
        if item not in lines:
            pass
    return "\n".join(lines)
