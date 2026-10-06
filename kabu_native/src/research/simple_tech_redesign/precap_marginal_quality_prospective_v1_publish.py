"""Publish 3 artifacts for prospective marginal-quality observation V1."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_redesign.isolation import PRECAP_PROSPECTIVE_V1_OUT
from research.simple_tech_redesign.precap_marginal_quality_prospective_v1_spec import ANALYSIS_ID, WINDOW_DAYS

SHEET_ORDER = (
    "summary",
    "discovery",
    "identity",
    "raw",
    "days",
    "roles",
    "arrival",
    "thirds",
    "occupancy_cells",
    "t3",
    "match_secondary",
    "concentration",
    "outlier_285A",
    "candidates",
    "decision",
    "leak",
)


def _cell(v: Any) -> Any:
    if isinstance(v, (dict, list, tuple)):
        return json.dumps(json_sanitize(v), ensure_ascii=False, default=str)
    return v


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    PRECAP_PROSPECTIVE_V1_OUT.mkdir(parents=True, exist_ok=True)
    for p in PRECAP_PROSPECTIVE_V1_OUT.iterdir():
        if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}:
            p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (PRECAP_PROSPECTIVE_V1_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (PRECAP_PROSPECTIVE_V1_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
    wb = Workbook()
    first = True
    for name in SHEET_ORDER:
        rows = sheets.get(name) or [{"empty": True}]
        clean = [{k: _cell(v) for k, v in dict(r).items()} for r in rows]
        if first:
            ws = wb.active
            ws.title = name[:31]
            first = False
        else:
            ws = wb.create_sheet(name[:31])
        _sheet(ws, clean)
    wb.save(PRECAP_PROSPECTIVE_V1_OUT / "audit.xlsx")


def build_sheets(report: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    dec = dict(report.get("decision") or {})
    ev = dict(report.get("evaluation") or {})
    disc = dict(report.get("discovery") or {})
    sheets: dict[str, list[dict[str, Any]]] = {}
    sheets["summary"] = kv_rows(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "CASE": dec.get("CASE"),
            "VERDICT": dec.get("VERDICT"),
            "spec_sha256": report.get("spec_sha256"),
            "source_sha256": report.get("source_sha256"),
            "completed_days": ev.get("completed_days") or disc.get("completed_days"),
            "admitted_n": ev.get("admitted_n"),
            "blocked_n": ev.get("blocked_n"),
            "NEW_ENTRY_FILTER": False,
            "CAP_CHANGED": False,
            "NEW_EXIT_RULE": False,
            "INTRINSIC_MECHANISM_CONFIRMED": False,
            "CANDIDATE_FROZEN": False,
            "PROTOCOL_FROZEN": True,
        }
    )
    sheets["discovery"] = list(disc.get("planned") or [{"empty": True}])
    sheets["identity"] = kv_rows({"identity_ok": ev.get("identity_ok"), "blockers": ev.get("blockers")})
    sheets["raw"] = kv_rows(dict(ev.get("raw") or {}))
    sheets["days"] = list(ev.get("day_table") or [])
    sheets["roles"] = kv_rows(dict(ev.get("roles") or {}))
    sheets["arrival"] = kv_rows(dict(ev.get("arrival") or {}))
    sheets["thirds"] = kv_rows(dict(ev.get("thirds") or {}))
    sheets["occupancy_cells"] = kv_rows(dict(ev.get("occupancy_cells") or {}))
    sheets["t3"] = kv_rows(dict(ev.get("t3_inventory") or {}))
    sheets["match_secondary"] = kv_rows(dict(ev.get("match_secondary_same_day_same_role") or {}))
    sheets["concentration"] = kv_rows(
        {"day": ev.get("day_concentration"), "symbol": ev.get("symbol_concentration")}
    )
    sheets["outlier_285A"] = kv_rows(dict(ev.get("outlier_285A") or {}))
    sheets["candidates"] = list(ev.get("candidates") or [])
    sheets["decision"] = kv_rows(dec)
    sheets["leak"] = kv_rows(dict(report.get("leak") or {}))
    if not sheets["days"]:
        sheets["days"] = [{"empty": True}]
    if not sheets["candidates"]:
        sheets["candidates"] = [{"empty": True}]
    return sheets


def _n(v: Any) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        if abs(v) >= 100:
            return f"{v:.0f}"
        return f"{v:.4f}"
    return str(v)


def _q(pack: Any) -> str:
    p = dict(pack or {})
    return (
        f"n={p.get('n')} total={_n(p.get('total_pnl'))} PF={p.get('PF')} "
        f"win={_n(p.get('win_rate'))} mean={_n(p.get('mean_pnl'))} median={_n(p.get('median_pnl'))} "
        f"gross_loss/fill={_n(p.get('gross_loss_per_fill'))}"
    )


def _clock(pack: Any) -> str:
    p = dict(pack or {})
    return (
        f"n={p.get('n')} rank_med={_n(p.get('arrival_rank_median'))} rank_iqr={_n(p.get('arrival_rank_iqr'))} "
        f"from_open_med={_n(p.get('from_open_median'))} to_close_med={_n(p.get('to_close_median'))}"
    )


def _conc(pack: Any) -> str:
    p = dict(pack or {})
    return (
        f"top={p.get('top')} top_pnl={_n(p.get('top_pnl'))} share_abs={_n(p.get('share_of_abs'))} "
        f"share_net={_n(p.get('share_of_net'))} warn50={p.get('warning_gt_50pct')}"
    )


def build_markdown(report: dict[str, Any]) -> str:
    dec = dict(report.get("decision") or {})
    q = dict(dec.get("questions") or {})
    ev = dict(report.get("evaluation") or {})
    disc = dict(report.get("discovery") or {})
    raw = dict(ev.get("raw") or {})
    roles = dict(ev.get("roles") or {})
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"**VERDICT:** `{dec.get('VERDICT')}` (CASE {dec.get('CASE')})",
        f"**protocol hash:** `{report.get('spec_sha256')}`",
        f"**source hash:** `{report.get('source_sha256')}`",
        "**PROSPECTIVE_OBSERVATION_PROTOCOL_FROZEN:** `true` · **CANDIDATE_FROZEN:** `false`",
        "**INTRINSIC_MECHANISM_CONFIRMED:** `false`",
        "**first_eligible_prospective_date:** `20260907` (MARGINAL QUALITY OBSERVATION DAY1, not floor-break Day1)",
        "",
        "## Required answers",
        f"1. protocol hash spec={report.get('spec_sha256')} source={report.get('source_sha256')}",
        f"2. eligible dates={list(WINDOW_DAYS)}; forbidden=20260903,20260904; today excluded from cohort.",
        f"3. completed prospective days={disc.get('completed_days') or ev.get('completed_days') or []}",
        f"4. admitted N={ev.get('admitted_n')}",
        f"5. blocked hypothetical N={ev.get('blocked_n')} (cap_only={ev.get('cap_only_n')})",
        f"6. admitted economics {_q(raw.get('admitted'))}",
        f"7. blocked economics {_q(raw.get('blocked'))} weaker={raw.get('comparison')}",
        f"8. day-by-day={ev.get('day_table')}",
        f"9. CORE admitted {_q(((roles.get('CORE') or {}).get('admitted')))} blocked {_q(((roles.get('CORE') or {}).get('blocked')))}; "
        f"ADDED admitted {_q(((roles.get('ADDED') or {}).get('admitted')))} blocked {_q(((roles.get('ADDED') or {}).get('blocked')))}. No role filter.",
        f"10. arrival admitted {_clock((ev.get('arrival') or {}).get('admitted'))}; blocked_hyp {_clock((ev.get('arrival') or {}).get('blocked_hyp'))}. No time/rank filter.",
        f"11. top-day concentration {_conc(ev.get('day_concentration'))}",
        f"12. top-symbol concentration {_conc(ev.get('symbol_concentration'))}",
        f"13. 285A contribution {ev.get('outlier_285A')}. Included. Exclusion forbidden.",
        f"14. Q1={q.get('Q1_future_blocked_weaker')} Q2={q.get('Q2_weakness_on_multiple_days')} Q3={q.get('Q3_single_day_explains')} "
        f"Q4={q.get('Q4_single_symbol_explains')} Q5={q.get('Q5_one_role_only')} Q6={q.get('Q6_late_arrival_only')} "
        f"Q7={q.get('Q7_285A_type_outlier_reproduced')}",
        f"15. verdict=`{dec.get('VERDICT')}` CASE={dec.get('CASE')}",
        "16. INTRINSIC_MECHANISM_CONFIRMED=false",
        "17. new ENTRY filter?=false",
        "18. CAP changed?=false",
        "19. new EXIT?=false",
        "20. TRUE_OOS=false",
        "21. CERTIFIED=false",
        f"22. next={dec.get('NEXT')}",
        "",
        "Matching is secondary diagnostic only and is not used in the primary verdict.",
        "T3 state is captured, not searched.",
        "",
        "STOP.",
    ]
    return "\n".join(lines) + "\n"
