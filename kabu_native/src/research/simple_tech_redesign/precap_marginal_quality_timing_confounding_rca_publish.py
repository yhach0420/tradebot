"""Publish 3 artifacts for pre-CAP timing confounding RCA."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_redesign.isolation import PRECAP_TIMING_RCA_OUT
from research.simple_tech_redesign.precap_marginal_quality_timing_confounding_rca_spec import ANALYSIS_ID

SHEET_ORDER = (
    "summary",
    "identity",
    "raw",
    "arrival",
    "days",
    "match_primary",
    "match_secondary",
    "thirds",
    "first5",
    "occupancy_cells",
    "horizon",
    "roles",
    "t3",
    "fwd_ex_285A",
    "concentration",
    "candidates",
    "decision",
    "leak",
)


def _cell(v: Any) -> Any:
    if isinstance(v, (dict, list, tuple)):
        return json.dumps(json_sanitize(v), ensure_ascii=False, default=str)
    return v


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    PRECAP_TIMING_RCA_OUT.mkdir(parents=True, exist_ok=True)
    for p in PRECAP_TIMING_RCA_OUT.iterdir():
        if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}:
            p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (PRECAP_TIMING_RCA_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (PRECAP_TIMING_RCA_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(PRECAP_TIMING_RCA_OUT / "audit.xlsx")


def _slim_cohort(pack: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in pack.items() if k not in {"candidates", "_match_primary"}}


def build_sheets(report: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    dec = dict(report.get("decision") or {})
    dev = dict(report.get("development") or {})
    fwd = dict(report.get("forward") or {})
    fex = dict(report.get("forward_ex_285A") or {})
    sheets: dict[str, list[dict[str, Any]]] = {}
    sheets["summary"] = kv_rows(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "VERDICT": dec.get("VERDICT"),
            "CASE": dec.get("CASE"),
            "PRIMARY_MECHANISM": dec.get("PRIMARY_MECHANISM"),
        }
    )
    sheets["identity"] = kv_rows({"DEV": dev.get("identity"), "FWD": fwd.get("identity")})
    sheets["raw"] = kv_rows({"DEV": dev.get("raw"), "FWD": fwd.get("raw")})
    sheets["arrival"] = kv_rows({"DEV": dev.get("arrival"), "FWD": fwd.get("arrival")})
    sheets["days"] = list(dev.get("day_table") or []) + [{"cohort": "FORWARD_BURNED", **r} for r in list(fwd.get("day_table") or [])]
    if sheets["days"] and "cohort" not in sheets["days"][0]:
        sheets["days"] = [{"cohort": "DEVELOPMENT", **r} for r in list(dev.get("day_table") or [])] + [
            {"cohort": "FORWARD_BURNED", **r} for r in list(fwd.get("day_table") or [])
        ]
    sheets["match_primary"] = kv_rows({"DEV": dev.get("match_primary_same_day_same_role"), "FWD": fwd.get("match_primary_same_day_same_role")})
    sheets["match_secondary"] = kv_rows({"DEV": dev.get("match_secondary_same_day"), "FWD": fwd.get("match_secondary_same_day")})
    sheets["thirds"] = kv_rows({"DEV": dev.get("thirds"), "FWD": fwd.get("thirds")})
    sheets["first5"] = kv_rows(
        {
            "DEV_first5": dev.get("first5"),
            "DEV_later": dev.get("later"),
            "FWD_first5": fwd.get("first5"),
            "FWD_later": fwd.get("later"),
        }
    )
    sheets["occupancy_cells"] = kv_rows({"DEV": dev.get("occupancy_cells"), "FWD": fwd.get("occupancy_cells")})
    sheets["horizon"] = kv_rows({"DEV": dev.get("horizon"), "FWD": fwd.get("horizon")})
    sheets["roles"] = kv_rows({"DEV": dev.get("roles"), "FWD": fwd.get("roles")})
    sheets["t3"] = kv_rows({"DEV": dev.get("t3_inventory"), "FWD": fwd.get("t3_inventory")})
    sheets["fwd_ex_285A"] = kv_rows(
        {
            "raw": fex.get("raw"),
            "match": fex.get("match_primary_same_day_same_role"),
            "thirds": fex.get("thirds"),
            "arrival": fex.get("arrival"),
        }
    )
    sheets["concentration"] = kv_rows(
        {
            "DEV_day": dev.get("day_concentration"),
            "FWD_day": fwd.get("day_concentration"),
            "DEV_symbol": dev.get("symbol_concentration"),
            "FWD_symbol": fwd.get("symbol_concentration"),
        }
    )
    sheets["candidates"] = [{"cohort": "DEVELOPMENT", **r} for r in list(dev.get("candidates") or [])] + [
        {"cohort": "FORWARD_BURNED", **r} for r in list(fwd.get("candidates") or [])
    ]
    sheets["decision"] = kv_rows({k: dec.get(k) for k in sorted(dec)})
    sheets["leak"] = kv_rows(dict(report.get("leak") or {}))
    if not sheets["candidates"]:
        sheets["candidates"] = [{"empty": True}]
    if not sheets["days"]:
        sheets["days"] = [{"empty": True}]
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
        f"win={_n(p.get('win_rate'))} mean={_n(p.get('mean_pnl'))} median={_n(p.get('median_pnl'))}"
    )


def _clock(pack: Any) -> str:
    p = dict(pack or {})
    return (
        f"n={p.get('n')} rank_med={_n(p.get('arrival_rank_median'))} rank_iqr={_n(p.get('arrival_rank_iqr'))} "
        f"from_open_med={_n(p.get('from_open_median'))} to_close_med={_n(p.get('to_close_median'))}"
    )


def _third(pack: Any) -> str:
    parts = []
    for name in ("FIRST", "MIDDLE", "LAST"):
        v = dict((pack or {}).get(name) or {})
        b = dict(v.get("blocked") or {})
        a = dict(v.get("admitted") or {})
        parts.append(
            f"{name} admitted_n={v.get('admitted_n')} blocked_n={v.get('blocked_hyp_n')} "
            f"blocked_mean={_n(b.get('mean_pnl'))} blocked_PF={b.get('PF')} "
            f"admitted_mean={_n(a.get('mean_pnl'))}"
        )
    return "; ".join(parts)


def _cells(pack: Any) -> str:
    parts = []
    for name in ("EARLY_AND_ADMITTED", "EARLY_NOT_ADMITTED", "LATE_BUT_ADMITTED", "LATE_AND_BLOCKED"):
        v = dict((pack or {}).get(name) or {})
        parts.append(f"{name} group_n={v.get('group_n')} {_q(v)}")
    return "; ".join(parts)


def _conc_brief(pack: Any) -> str:
    parts = []
    for name, v in dict(pack or {}).items():
        if not isinstance(v, dict):
            continue
        parts.append(
            f"{name} top={v.get('top')} top_pnl={_n(v.get('top_pnl'))} "
            f"share_abs={_n(v.get('share_of_abs'))} share_net={_n(v.get('share_of_net'))} "
            f"warn50={v.get('warning_gt_50pct')}"
        )
    return "; ".join(parts)


def _t3_brief(pack: Any) -> str:
    skip = {"missing_not_computed"}
    parts = []
    for name, v in dict(pack or {}).items():
        if name in skip or not isinstance(v, dict):
            continue
        if v.get("matched_paired_n"):
            parts.append(
                f"{name} adm_med={_n((v.get('admitted') or {}).get('median'))} "
                f"blk_med={_n((v.get('blocked') or {}).get('median'))} "
                f"paired_med={_n(v.get('matched_paired_diff_median'))} n={v.get('matched_paired_n')}"
            )
    missing = dict((pack or {}).get("missing_not_computed") or {})
    if missing:
        parts.append(f"MISSING={missing}")
    return "; ".join(parts)


def build_markdown(report: dict[str, Any]) -> str:
    dec = dict(report.get("decision") or {})
    q = dict(dec.get("questions") or {})
    dev = dict(report.get("development") or {})
    fwd = dict(report.get("forward") or {})
    fex = dict(report.get("forward_ex_285A") or {})
    di = dict(dev.get("identity") or {})
    fi = dict(fwd.get("identity") or {})
    dm = dict(dev.get("match_primary_same_day_same_role") or {})
    fm = dict(fwd.get("match_primary_same_day_same_role") or {})
    dsh = dict((dec.get("shrinkage") or {}).get("DEV") or {})
    fsh = dict((dec.get("shrinkage") or {}).get("FWD") or {})
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"**VERDICT:** `{dec.get('VERDICT')}` (CASE {dec.get('CASE')})",
        f"**PRIMARY_MECHANISM:** `{dec.get('PRIMARY_MECHANISM')}`",
        f"**OBSERVED_ECONOMIC_BOTTLENECK (frozen, not intrinsic):** `{dec.get('OBSERVED_ECONOMIC_BOTTLENECK')}`",
        "**first_eligible_prospective_date:** `null` · **prospective_armed:** `false`",
        "",
        "## Required answers",
        f"1. Identity DEV ok={di.get('ok')} fill={di.get('fill_n')} hyp={di.get('hypothetical_fill_n')} cap_only={di.get('cap_only_blocked_n')} clock={di.get('clock_identity_ok')}. FWD ok={fi.get('ok')} fill={fi.get('fill_n')} hyp={fi.get('hypothetical_fill_n')} cap_only={fi.get('cap_only_blocked_n')} clock={fi.get('clock_identity_ok')}.",
        f"2. Raw admitted vs blocked. DEV admitted {_q((dev.get('raw') or {}).get('admitted'))} vs blocked {_q((dev.get('raw') or {}).get('blocked'))} weaker={(dev.get('raw') or {}).get('comparison')}. FWD admitted {_q((fwd.get('raw') or {}).get('admitted'))} vs blocked {_q((fwd.get('raw') or {}).get('blocked'))} weaker={(fwd.get('raw') or {}).get('comparison')}.",
        f"3. Arrival-rank. DEV admitted {_clock((dev.get('arrival') or {}).get('admitted'))}; CAP-only blocked {_clock((dev.get('arrival') or {}).get('cap_only_blocked'))}; hyp fills {_clock((dev.get('arrival') or {}).get('blocked_hyp'))}. FWD admitted {_clock((fwd.get('arrival') or {}).get('admitted'))}; CAP-only {_clock((fwd.get('arrival') or {}).get('cap_only_blocked'))}; hyp {_clock((fwd.get('arrival') or {}).get('blocked_hyp'))}.",
        f"4. Time distribution is in item 3 (from_open / to_close medians).",
        f"5. Day-level comparison in audit days sheet. DEV days={len(dev.get('day_table') or [])} FWD days={len(fwd.get('day_table') or [])}.",
        f"6. Matched N DEV={dm.get('matched_blocked_n')} unmatched={dm.get('unmatched_n')}; FWD={fm.get('matched_blocked_n')} unmatched={fm.get('unmatched_n')}. PRIMARY=same-day+same-role.",
        f"7. Median timestamp gap DEV={_n(dm.get('median_timestamp_gap_sec'))}s FWD={_n(fm.get('median_timestamp_gap_sec'))}s.",
        f"8. Time-matched DEV blocked {_q(dm.get('blocked'))} vs matched admitted {_q(dm.get('matched_admitted'))} paired_mean={_n(dm.get('mean_paired_difference'))} weaker={dm.get('comparison')} timing_explained={_n(dsh.get('fraction_explained_by_timing'))}.",
        f"9. Time-matched FWD blocked {_q(fm.get('blocked'))} vs matched admitted {_q(fm.get('matched_admitted'))} paired_mean={_n(fm.get('mean_paired_difference'))} weaker={fm.get('comparison')} timing_explained={_n(fsh.get('fraction_explained_by_timing'))}.",
        f"10. Arrival thirds DEV={_third(dev.get('thirds'))} FWD={_third(fwd.get('thirds'))}.",
        f"11. FIRST5 vs later DEV first5 {_q(dev.get('first5'))} later {_q(dev.get('later'))}; FWD first5 {_q(fwd.get('first5'))} later {_q(fwd.get('later'))}. No FIRST5 filter created.",
        f"12. early/late x admitted/blocked DEV={_cells(dev.get('occupancy_cells'))} FWD={_cells(fwd.get('occupancy_cells'))}.",
        f"13. Remaining-horizon Spearman(seconds_to_close, pnl) DEV={dev.get('horizon')} FWD={fwd.get('horizon')}.",
        f"14. Existing T3 inventory from frozen v22 state_t0.tf1 + P2 setup_low/high. rci9_prev and EMA21 slope input are MISSING_NOT_IN_FROZEN_CACHE (not computed).",
        f"15. Matched T3-state differences DEV={_t3_brief(dev.get('t3_inventory'))}. FWD v22 join is DEV-only cache (n={fwd.get('v22_join_n')}).",
        f"16. CORE/ADDED DEV CORE admitted {_q(((dev.get('roles') or {}).get('CORE') or {}).get('admitted'))} blocked {_q(((dev.get('roles') or {}).get('CORE') or {}).get('blocked'))}; ADDED admitted {_q(((dev.get('roles') or {}).get('ADDED') or {}).get('admitted'))} blocked {_q(((dev.get('roles') or {}).get('ADDED') or {}).get('blocked'))}. FWD CORE admitted {_q(((fwd.get('roles') or {}).get('CORE') or {}).get('admitted'))} blocked {_q(((fwd.get('roles') or {}).get('CORE') or {}).get('blocked'))}; ADDED admitted {_q(((fwd.get('roles') or {}).get('ADDED') or {}).get('admitted'))} blocked {_q(((fwd.get('roles') or {}).get('ADDED') or {}).get('blocked'))}. No role filter.",
        f"17. FWD FULL raw={(fwd.get('raw') or {}).get('comparison')} matched={fm.get('comparison')}.",
        f"18. FWD ex-285A diagnostic raw={(fex.get('raw') or {}).get('comparison')} matched={(fex.get('match_primary_same_day_same_role') or {}).get('comparison')} thirds={_third(fex.get('thirds'))}. Exclusion is diagnostic only.",
        f"19. Day concentration DEV={_conc_brief(dev.get('day_concentration'))} FWD={_conc_brief(fwd.get('day_concentration'))}.",
        f"20. Symbol concentration DEV={_conc_brief(dev.get('symbol_concentration'))} FWD={_conc_brief(fwd.get('symbol_concentration'))}. 285A included. No exclusion rule.",
        f"21. Q1={q.get('Q1_raw_blocked_weak')} Q2={q.get('Q2_matched_disadvantage_remains')} Q3={q.get('Q3_late_arrival_strata_weak')} Q4={q.get('Q4_FIRST5_vs_later_quality_gap')} Q5={q.get('Q5_late_admitted_also_weak_time_effect')} Q6={q.get('Q6_matched_blocked_weaker_intrinsic')} Q7={q.get('Q7_remaining_horizon_alone')} Q8={q.get('Q8_DEV_FWD_same_direction')} Q9={q.get('Q9_FWD_ex_285A_same_architecture')}",
        f"22. PRIMARY_MECHANISM={dec.get('PRIMARY_MECHANISM')}",
        f"23. SECONDARY_DRIVERS={dec.get('SECONDARY_DRIVERS')}",
        f"24. verdict=`{dec.get('VERDICT')}` CASE={dec.get('CASE')}",
        "25. new ENTRY filter?=false",
        "26. CAP changed?=false",
        "27. new EXIT?=false",
        "28. TRUE_OOS=false",
        "29. CERTIFIED=false",
        f"30. next={dec.get('NEXT')}",
        "",
        "STOP.",
    ]
    return "\n".join(lines) + "\n"
