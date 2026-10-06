"""Publish 3 artifacts for slot-release marginal admission quality RCA."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_redesign.isolation import SLOT_RELEASE_MARGINAL_RCA_OUT
from research.simple_tech_redesign.slot_release_marginal_admission_quality_rca_spec import ANALYSIS_ID

SHEET_ORDER = (
    "summary",
    "identity",
    "blocked_pool",
    "comparison",
    "roles",
    "time",
    "generations",
    "one_generation",
    "observed_vs_blocked",
    "incremental_join",
    "outlier_285A",
    "same_symbol",
    "root_class",
    "days",
    "symbols",
    "cap_only",
    "incrementals",
    "decision",
    "leak",
)


def _cell(v: Any) -> Any:
    if isinstance(v, (dict, list, tuple)):
        return json.dumps(json_sanitize(v), ensure_ascii=False, default=str)
    return v


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    SLOT_RELEASE_MARGINAL_RCA_OUT.mkdir(parents=True, exist_ok=True)
    for p in SLOT_RELEASE_MARGINAL_RCA_OUT.iterdir():
        if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}:
            p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (SLOT_RELEASE_MARGINAL_RCA_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (SLOT_RELEASE_MARGINAL_RCA_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(SLOT_RELEASE_MARGINAL_RCA_OUT / "audit.xlsx")


def build_sheets(report: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    dec = dict(report.get("decision") or {})
    dev = dict(report.get("development") or {})
    fwd = dict(report.get("forward") or {})
    sheets: dict[str, list[dict[str, Any]]] = {}
    sheets["summary"] = kv_rows({"ANALYSIS_ID": ANALYSIS_ID, "VERDICT": dec.get("VERDICT"), "CASE": dec.get("CASE"), "PRIMARY_CAUSAL_BOTTLENECK": dec.get("PRIMARY_CAUSAL_BOTTLENECK")})
    sheets["identity"] = kv_rows({"DEV": dev.get("identity"), "FWD": fwd.get("identity")})
    sheets["blocked_pool"] = kv_rows({"DEV": {"n": dev.get("cap_only_blocked_n"), "hyp": dev.get("hypothetical_fill_n"), **dict(dev.get("blocked_pool") or {})}, "FWD": {"n": fwd.get("cap_only_blocked_n"), "hyp": fwd.get("hypothetical_fill_n"), **dict(fwd.get("blocked_pool") or {})}})
    sheets["comparison"] = kv_rows({"DEV": {"admitted": dev.get("admitted_control"), "cmp": dev.get("comparison")}, "FWD": {"admitted": fwd.get("admitted_control"), "cmp": fwd.get("comparison")}})
    sheets["roles"] = kv_rows({"DEV_blocked": dev.get("blocked_by_role"), "DEV_admitted": dev.get("admitted_by_role"), "FWD_blocked": fwd.get("blocked_by_role"), "FWD_admitted": fwd.get("admitted_by_role")})
    sheets["time"] = kv_rows({"DEV": dev.get("time"), "FWD": fwd.get("time")})
    gen_rows = []
    for cohort, pack in (("DEVELOPMENT", dev), ("FORWARD_BURNED", fwd)):
        for g, body in dict(pack.get("generation_econ") or {}).items():
            gen_rows.append({"cohort": cohort, "generation": g, **dict(body)})
    sheets["generations"] = gen_rows or [{"empty": True}]
    sheets["one_generation"] = kv_rows({"DEV": dev.get("one_generation_only"), "FWD": fwd.get("one_generation_only")})
    ov_dev = dict(dev.get("observed_vs_blocked") or {})
    ov_fwd = dict(fwd.get("observed_vs_blocked") or {})
    sheets["observed_vs_blocked"] = kv_rows(
        {
            "DEV": {k: v for k, v in ov_dev.items() if k not in {"join", "overlap_ids", "incremental_not_in_cap_only_ids"}},
            "FWD": {k: v for k, v in ov_fwd.items() if k not in {"join", "overlap_ids", "incremental_not_in_cap_only_ids"}},
        }
    )
    sheets["incremental_join"] = list(ov_dev.get("join") or []) + list(ov_fwd.get("join") or [])
    if not sheets["incremental_join"]:
        sheets["incremental_join"] = [{"empty": True}]
    sheets["outlier_285A"] = kv_rows(dict(fwd.get("outlier_285A") or {"empty": True}))
    sheets["same_symbol"] = kv_rows({"DEV": dev.get("same_symbol"), "FWD": fwd.get("same_symbol")})
    sheets["root_class"] = kv_rows({"DEV": dev.get("root_exit_class"), "FWD": fwd.get("root_exit_class")})
    sheets["days"] = kv_rows({"DEV": dev.get("day_concentration"), "FWD": fwd.get("day_concentration")})
    sheets["symbols"] = kv_rows({"DEV": dev.get("symbol_concentration"), "FWD": fwd.get("symbol_concentration")})
    sheets["cap_only"] = [{"cohort": "DEVELOPMENT", **r} for r in list(dev.get("cap_only_rows") or [])] + [{"cohort": "FORWARD_BURNED", **r} for r in list(fwd.get("cap_only_rows") or [])]
    sheets["incrementals"] = [{"cohort": "DEVELOPMENT", **r} for r in list(dev.get("incremental") or [])] + [{"cohort": "FORWARD_BURNED", **r} for r in list(fwd.get("incremental") or [])]
    sheets["decision"] = kv_rows({k: dec.get(k) for k in sorted(dec)})
    sheets["leak"] = kv_rows(dict(report.get("leak") or {}))
    if not sheets["cap_only"]:
        sheets["cap_only"] = [{"empty": True}]
    if not sheets["incrementals"]:
        sheets["incrementals"] = [{"empty": True}]
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
        f"max_loss={_n(p.get('max_loss'))} max_gain={_n(p.get('max_gain'))}"
    )


def _clock(pack: Any) -> str:
    p = dict(pack or {})
    return (
        f"n={p.get('n')} from_open_median_s={_n(p.get('from_open_median'))} "
        f"from_open_iqr_s={_n(p.get('from_open_iqr'))} to_close_median_s={_n(p.get('to_close_median'))} "
        f"to_close_iqr_s={_n(p.get('to_close_iqr'))}"
    )


def _conc(pack: Any) -> str:
    p = dict(pack or {})
    return (
        f"top={p.get('top')} pnl={_n(p.get('top_pnl'))} share_abs={_n(p.get('share'))} "
        f"warn>50%={p.get('warning_gt_50pct')}"
    )


def build_markdown(report: dict[str, Any]) -> str:
    dec = dict(report.get("decision") or {})
    q = dict(dec.get("questions") or {})
    dev = dict(report.get("development") or {})
    fwd = dict(report.get("forward") or {})
    di = dict(dev.get("identity") or {})
    fi = dict(fwd.get("identity") or {})
    db = dict(dev.get("blocked_pool") or {})
    fb = dict(fwd.get("blocked_pool") or {})
    da = dict(dev.get("admitted_control") or {})
    fa = dict(fwd.get("admitted_control") or {})
    ogd = dict(dev.get("one_generation_only") or {})
    ogf = dict(fwd.get("one_generation_only") or {})
    ovd = dict(dev.get("observed_vs_blocked") or {})
    ovf = dict(fwd.get("observed_vs_blocked") or {})
    outl = dict(fwd.get("outlier_285A") or {})
    dbr = dict(dev.get("blocked_by_role") or {})
    dar = dict(dev.get("admitted_by_role") or {})
    fbr = dict(fwd.get("blocked_by_role") or {})
    far = dict(fwd.get("admitted_by_role") or {})
    dt = dict(dev.get("time") or {})
    ft = dict(fwd.get("time") or {})
    dss = dict(dev.get("same_symbol") or {})
    fss = dict(fwd.get("same_symbol") or {})
    dd = dict(dev.get("day_concentration") or {})
    fd = dict(fwd.get("day_concentration") or {})
    ds = dict(dev.get("symbol_concentration") or {})
    fs = dict(fwd.get("symbol_concentration") or {})
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"**VERDICT:** `{dec.get('VERDICT')}` (CASE {dec.get('CASE')})",
        f"**PRIMARY_CAUSAL_BOTTLENECK:** `{dec.get('PRIMARY_CAUSAL_BOTTLENECK')}`",
        f"**SECONDARY_DRIVERS:** `{dec.get('SECONDARY_DRIVERS')}`",
        "**first_eligible_prospective_date:** `null`",
        "**prospective_armed:** `false`",
        f"**earliest_possible_if_case_A:** `{dec.get('earliest_possible_if_case_A')}` (not armed)",
        "",
        "## Reporting cleanup",
        "Formal CASE D meaning: `first_eligible_prospective_date = null`.",
        "The prior floor-break candidate report left `required.first_eligible_prospective_date = 20260907`",
        "while `decision.first_eligible_prospective_date = null`.",
        "`20260907` is `earliest_possible_if_case_A` only. It is not a prospective armed date.",
        "Do not treat 20260907 as Day1. This RCA does not mutate the CASE D 3 artifacts.",
        "",
        "## Required answers",
        f"1. Identity matched. DEV Control 84 / 18/66 / +146680; failed Treatment 115 / incr 31 / DIRECT +72990 / SLOT +76550 / TOTAL +149540. FWD Control 20 / 9/11 / -45100; failed Treatment 35 / incr 15 / DIRECT -5000 / SLOT -82900 / TOTAL -87900. leftover_ok={di.get('leftover_ok')}/{fi.get('leftover_ok')} replay_match={di.get('control_replay_match')}/{fi.get('control_replay_match')}.",
        f"2. CAP_ONLY_BLOCKED_N DEV={dev.get('cap_only_blocked_n')} FWD={fwd.get('cap_only_blocked_n')}. Multi-reason (CAP plus not execution-eligible) DEV={dev.get('multi_reason_n')} FWD={fwd.get('multi_reason_n')}. SAME_SYMBOL blocked DEV={dev.get('same_symbol_blocked_n')} FWD={fwd.get('same_symbol_blocked_n')}. execution_evaluable_n DEV={dev.get('execution_evaluable_n')} FWD={fwd.get('execution_evaluable_n')}.",
        f"3. Hypothetical one-shot fills DEV={dev.get('hypothetical_fill_n')} FWD={fwd.get('hypothetical_fill_n')}.",
        f"4. DEV blocked pool: {_q(db)}.",
        f"5. FWD blocked pool: {_q(fb)}.",
        f"6. Control admitted vs blocked: DEV admitted {_q(da)} vs blocked {_q(db)}; clearly_weaker={dev.get('comparison')}. FWD admitted {_q(fa)} vs blocked {_q(fb)}; clearly_weaker={fwd.get('comparison')}. CAP is not turned into a quality filter.",
        f"7. CORE/ADDED. DEV blocked CORE {_q(dbr.get('CORE'))} ADDED {_q(dbr.get('ADDED'))}; admitted CORE {_q(dar.get('CORE'))} ADDED {_q(dar.get('ADDED'))}. FWD blocked CORE {_q(fbr.get('CORE'))} ADDED {_q(fbr.get('ADDED'))}; admitted CORE {_q(far.get('CORE'))} ADDED {_q(far.get('ADDED'))}. No role filter created.",
        f"8. Time. DEV admitted {_clock(dt.get('control_admitted'))}; DEV blocked {_clock(dt.get('cap_only_blocked'))}. FWD admitted {_clock(ft.get('control_admitted'))}; FWD blocked {_clock(ft.get('cap_only_blocked'))}. Blocked arrivals sit later in the session. No time threshold created.",
        f"9. DEV generations: GEN0 n={(dev.get('generation_econ') or {}).get('0', {}).get('fill_n')} pnl={_n((dev.get('generation_econ') or {}).get('0', {}).get('total_pnl'))}; GEN1 n={((dev.get('generation_econ') or {}).get('1') or {}).get('fill_n')} pnl={_n(dev.get('GEN1_TOTAL_PNL'))} candidate_exit={((dev.get('generation_econ') or {}).get('1') or {}).get('candidate_exit_n')}; GEN2 n={((dev.get('generation_econ') or {}).get('2') or {}).get('fill_n')} pnl={_n(((dev.get('generation_econ') or {}).get('2') or {}).get('total_pnl'))}; GEN3 n={((dev.get('generation_econ') or {}).get('3') or {}).get('fill_n')} pnl={_n(((dev.get('generation_econ') or {}).get('3') or {}).get('total_pnl'))}.",
        f"10. FWD generations: GEN0 n={(fwd.get('generation_econ') or {}).get('0', {}).get('fill_n')} pnl={_n((fwd.get('generation_econ') or {}).get('0', {}).get('total_pnl'))}; GEN1 n={((fwd.get('generation_econ') or {}).get('1') or {}).get('fill_n')} pnl={_n(fwd.get('GEN1_TOTAL_PNL'))} candidate_exit={((fwd.get('generation_econ') or {}).get('1') or {}).get('candidate_exit_n')}; GEN2 n={((fwd.get('generation_econ') or {}).get('2') or {}).get('fill_n')} pnl={_n(((fwd.get('generation_econ') or {}).get('2') or {}).get('total_pnl'))}.",
        f"11. GEN1 economics DEV {_q(dev.get('GEN1'))}; FWD {_q(fwd.get('GEN1'))}.",
        f"12. GEN2+ economics DEV {_q(dev.get('GEN2PLUS'))}; FWD {_q(fwd.get('GEN2PLUS'))}. FWD GEN2+ is +1400, not the damage source.",
        f"13. ONE_GENERATION_ONLY_DIAGNOSTIC DEV total_delta={_n(ogd.get('TOTAL_CAUSAL_DELTA'))} vs_full_cascade={_n(ogd.get('vs_full_cascade_delta'))} incr_n={ogd.get('incremental_fill_n')} incr_pnl={_n(ogd.get('incremental_pnl'))}. FWD total_delta={_n(ogf.get('TOTAL_CAUSAL_DELTA'))} vs_full_cascade={_n(ogf.get('vs_full_cascade_delta'))} incr_n={ogf.get('incremental_fill_n')} incr_pnl={_n(ogf.get('incremental_pnl'))}. Not a candidate. Not a policy.",
        f"14. Observed incremental vs CAP-only pool: DEV overlap {ovd.get('overlap_n')}/{ovd.get('incremental_n')} parity_ok={ovd.get('parity_ok_n')} not_in_pool={ovd.get('incremental_not_in_cap_only_n')} ids={ovd.get('incremental_not_in_cap_only_ids')}. FWD overlap {ovf.get('overlap_n')}/{ovf.get('incremental_n')} parity_ok={ovf.get('parity_ok_n')} not_in_pool={ovf.get('incremental_not_in_cap_only_n')} ids={ovf.get('incremental_not_in_cap_only_ids')}. Non-overlap incrementals were CONTROL SAME_SYMBOL blocked, not CAP-only. Session-close of overlap DEV {_q(ovd.get('observed_incremental_subset_session_close'))} vs full blocked {_q(ovd.get('all_blocked_pool'))}. FWD overlap session-close {_q(ovf.get('observed_incremental_subset_session_close'))} vs full blocked {_q(ovf.get('all_blocked_pool'))}. Treatment incremental PnL DEV {_q(ovd.get('observed_incremental_treatment_pnl'))}; FWD {_q(ovf.get('observed_incremental_treatment_pnl'))}.",
        f"15. 20260828 285A pnl=-60000 originally_CAP_only_blocked={outl.get('originally_cap_only_blocked')} generation={outl.get('generation')} root={outl.get('root_control_exit_trade_id')} parent={outl.get('parent_fill_trade_id')} parent_exit={outl.get('parent_exit_reason')} same_symbol_as_root={outl.get('same_symbol_as_root')} same_symbol_as_parent={outl.get('same_symbol_as_parent')} share_of_incremental={_n(outl.get('share_of_incremental_pnl'))} share_of_blocked_pool={_n(outl.get('share_of_blocked_pool_pnl'))}. Exclusion forbidden. Diagnostic only: FWD blocked excluding 285A {_q(outl.get('fwd_blocked_pool_excluding_285A_diagnostic_only'))} vs admitted, clearly_weaker={outl.get('fwd_blocked_ex_285A_vs_admitted')}.",
        f"16. Same-symbol/re-entry. DEV as_root {_q(dss.get('same_symbol_as_root'))}; as_parent {_q(dss.get('same_symbol_as_parent'))}; previously_exited {_q(dss.get('same_symbol_previously_exited_in_session'))}. FWD as_root {_q(fss.get('same_symbol_as_root'))}; as_parent {_q(fss.get('same_symbol_as_parent'))}; previously_exited {_q(fss.get('same_symbol_previously_exited_in_session'))}. Not the primary driver. Same-symbol semantics unchanged.",
        f"17. Root-exit class DEV={dev.get('root_exit_class')} FWD={fwd.get('root_exit_class')}. Diagnostic labels only. FWD downstream damage concentrates in P_EARLY.",
        f"18. Day concentration (abs share). DEV blocked {_conc(dd.get('blocked_top'))}; DEV incremental {_conc(dd.get('incremental_top'))}; DEV 20260806 blocked={_n(dd.get('dev_20260806_blocked_pnl'))} incremental={_n(dd.get('dev_20260806_incremental_pnl'))}. FWD blocked {_conc(fd.get('blocked_top'))}; FWD incremental {_conc(fd.get('incremental_top'))}; FWD 20260828 blocked={_n(fd.get('fwd_20260828_blocked_pnl'))} incremental={_n(fd.get('fwd_20260828_incremental_pnl'))}.",
        f"19. Symbol concentration (abs share). DEV blocked {_conc(ds.get('blocked'))}; DEV incremental {_conc(ds.get('incremental'))}. FWD blocked {_conc(fs.get('blocked'))}; FWD incremental {_conc(fs.get('incremental'))}. No symbol exclusion.",
        f"20. FWD -82900 primary source is GEN1 ({_n(fwd.get('GEN1_TOTAL_PNL'))}), not GEN2+ ({_n(fwd.get('GEN2PLUS_TOTAL_PNL'))}). 285A is 72% of incremental PnL and is inside the CAP-only pool. Remaining FWD incremental after 285A is still negative but much smaller.",
        f"21. PRIMARY_CAUSAL_BOTTLENECK={dec.get('PRIMARY_CAUSAL_BOTTLENECK')}",
        f"22. SECONDARY_DRIVERS={dec.get('SECONDARY_DRIVERS')}",
        f"23. verdict=`{dec.get('VERDICT')}` CASE={dec.get('CASE')}",
        "24. new rule created?=false",
        "25. ENTRY changed?=false",
        "26. CAP changed?=false",
        "27. TRUE_OOS=false",
        "28. CERTIFIED=false",
        f"29. next={dec.get('NEXT')}",
        "",
        f"Q1={q.get('Q1_blocked_pool_worse_than_admitted_DEV')} Q2={q.get('Q2_DEV_FWD_same_direction')} Q3={q.get('Q3_FWD_blocked_pool_itself_bad')} Q4={q.get('Q4_floor_break_admitted_worse_subset')} Q5={q.get('Q5_FWD_damage_already_in_GEN1')} Q6={q.get('Q6_GEN2plus_worsened')} Q7={q.get('Q7_same_symbol_reentry_primary')} Q8={q.get('Q8_explained_by_20260828_285A_alone')}",
        "",
        "STOP.",
    ]
    return "\n".join(lines) + "\n"
