"""Write report.json / report.md / audit.xlsx only under pre_cap_hard_reject_failure_rca/."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_redesign.isolation import PRE_CAP_HARD_REJECT_RCA_OUT
from research.simple_tech_redesign.pre_cap_hard_reject_rca_spec import ANALYSIS_ID

SHEET_ORDER = (
    "summary",
    "integrity",
    "identity",
    "hard_reject",
    "control_only",
    "incremental",
    "pairs",
    "order",
    "relative",
    "decision",
)


def _cell(v: Any) -> Any:
    if isinstance(v, (dict, list, tuple)):
        return json.dumps(json_sanitize(v), ensure_ascii=False, default=str)
    return v


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    PRE_CAP_HARD_REJECT_RCA_OUT.mkdir(parents=True, exist_ok=True)
    extra = [p for p in PRE_CAP_HARD_REJECT_RCA_OUT.iterdir() if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}]
    for p in extra:
        p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (PRE_CAP_HARD_REJECT_RCA_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (PRE_CAP_HARD_REJECT_RCA_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(PRE_CAP_HARD_REJECT_RCA_OUT / "audit.xlsx")


def _fmt(v: Any) -> str:
    if v is None:
        return "None"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        if abs(v) >= 1000:
            return f"{v:,.2f}"
        return f"{v:.4f}"
    if isinstance(v, dict):
        return json.dumps(json_sanitize(v), ensure_ascii=False, default=str)
    if isinstance(v, list):
        return ", ".join(str(x) for x in v) if v else "none"
    return str(v)


def _trade_row(t: dict[str, Any], *, cohort: str, identity: str) -> dict[str, Any]:
    return {
        "cohort": cohort,
        "identity": identity,
        "date": t.get("date"),
        "symbol": t.get("symbol"),
        "t0": t.get("t0"),
        "fill_time": t.get("fill_time"),
        "fill_role": t.get("fill_role"),
        "execution_route": t.get("execution_route"),
        "path_type": t.get("path_type"),
        "never_break_even": t.get("never_break_even"),
        "pnl_yen_100": t.get("pnl_yen_100"),
        "minutes_from_session_open": t.get("minutes_from_session_open"),
        "signal_order_in_day": t.get("signal_order_in_day"),
        "pre_cap_reject": t.get("pre_cap_reject"),
        "removal_class": t.get("removal_class"),
        "remove_diagnostic": t.get("remove_diagnostic"),
        "adverse_component_count": t.get("adverse_component_count"),
        "BID_DEPLETION_5S": t.get("BID_DEPLETION_5S"),
        "ASK_ADD_5S": t.get("ASK_ADD_5S"),
        "SPREAD_EXPANSION_5S": t.get("SPREAD_EXPANSION_5S"),
        "bid_depletion_event_n": t.get("bid_depletion_event_n"),
        "ask_add_event_n": t.get("ask_add_event_n"),
        "spread_expand_event_n": t.get("spread_expand_event_n"),
        "spread_bps": t.get("spread_bps"),
        "bid1_qty": t.get("bid1_qty"),
        "ask1_qty": t.get("ask1_qty"),
        "candidate_density_60s": t.get("candidate_density_60s"),
    }


def _pair_row(p: dict[str, Any], *, cohort: str) -> dict[str, Any]:
    rem = dict(p.get("removed") or {})
    inc = dict(p.get("replacement") or {})
    return {
        "cohort": cohort,
        "date": p.get("date"),
        "match_mode": p.get("match_mode"),
        "removed_symbol": rem.get("symbol"),
        "removed_t0": rem.get("t0"),
        "removed_role": rem.get("fill_role"),
        "removed_path": rem.get("path_type"),
        "removed_pnl": p.get("removed_trade_pnl"),
        "removed_adverse": p.get("removed_adverse_component_count"),
        "removed_never_be": rem.get("never_break_even"),
        "replacement_symbol": inc.get("symbol"),
        "replacement_t0": inc.get("t0"),
        "replacement_role": inc.get("fill_role"),
        "replacement_path": inc.get("path_type"),
        "replacement_pnl": p.get("replacement_trade_pnl"),
        "replacement_adverse": p.get("replacement_adverse_component_count"),
        "replacement_never_be": inc.get("never_break_even"),
        "replacement_minus_removed": p.get("replacement_minus_removed"),
        "elapsed_sec_from_removed": p.get("elapsed_sec_from_removed"),
        "control_slot_count_at_replacement_t0": p.get("control_slot_count_at_replacement_t0"),
        "preceding_precap_reject_n": p.get("preceding_precap_reject_n"),
        "enabling_reject_seq": p.get("enabling_reject_seq"),
        "candidate_density_60s": p.get("candidate_density_60s"),
        "removed_trade_id": p.get("removed_trade_id"),
        "replacement_trade_id": p.get("replacement_trade_id"),
    }


def build_sheets(report: dict[str, Any], leak: dict[str, Any], reporting: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    req = dict(report.get("required") or {})
    decision = dict(report.get("decision") or {})
    ident_rows = []
    hard_rows = []
    co_rows = []
    inc_rows = []
    pair_rows = []
    order_rows = []
    rel_rows = []
    for cohort_key, label in (("development", "DEVELOPMENT"), ("forward", "FORWARD_BURNED")):
        body = dict(report.get(cohort_key) or {})
        ident_rows.append({"cohort": label, **dict(body.get("identity") or {}), **dict(body.get("attribution") or {})})
        hard_rows.append({"cohort": label, "slice": "direct_precap", **dict((body.get("HARD_REJECT_SEMANTIC") or {}).get("DIRECT_PRECAP_REMOVED") or {})})
        hard_rows.append({"cohort": label, "slice": "direct_precap_CORE", **dict(((body.get("HARD_REJECT_SEMANTIC") or {}).get("DIRECT_PRECAP_REMOVED_BY_ROLE") or {}).get("CORE") or {})})
        hard_rows.append({"cohort": label, "slice": "direct_precap_ADDED", **dict(((body.get("HARD_REJECT_SEMANTIC") or {}).get("DIRECT_PRECAP_REMOVED_BY_ROLE") or {}).get("ADDED") or {})})
        for t in list(body.get("control_only") or []):
            co_rows.append(_trade_row(t, cohort=label, identity="CONTROL_ONLY"))
        for t in list(body.get("incremental") or []):
            inc_rows.append(_trade_row(t, cohort=label, identity="INCREMENTAL_TREATMENT"))
        for p in list(body.get("pairs") or []):
            pair_rows.append(_pair_row(p, cohort=label))
        order_rows.append({"cohort": label, **dict(body.get("ORDER") or {})})
        rel_rows.append({"cohort": label, **{k: v for k, v in dict(body.get("RELATIVE_BOARD") or {}).items() if k != "by_component"}, "by_component": (body.get("RELATIVE_BOARD") or {}).get("by_component")})
    return {
        "summary": kv_rows(req),
        "integrity": kv_rows({**dict(leak), **dict(reporting), "NON_INTERFERENCE_PASS": req.get("NON_INTERFERENCE_PASS")}),
        "identity": ident_rows or [{"empty": True}],
        "hard_reject": hard_rows or [{"empty": True}],
        "control_only": co_rows or [{"empty": True}],
        "incremental": inc_rows or [{"empty": True}],
        "pairs": pair_rows or [{"empty": True}],
        "order": order_rows or [{"empty": True}],
        "relative": rel_rows or [{"empty": True}],
        "decision": kv_rows(decision),
    }


def _sem_line(label: str, pack: dict[str, Any]) -> str:
    return (
        f"- {label}: n=`{pack.get('n')}` CORE/ADDED=`{pack.get('CORE_n')}/{pack.get('ADDED_n')}` "
        f"GOOD=`{pack.get('GOOD_n')}` DIP=`{pack.get('DIP_n')}` EARLY=`{pack.get('EARLY_n')}` "
        f"NEVER_BE=`{_fmt(pack.get('NEVER_BE_rate'))}` pos=`{_fmt(pack.get('pos_pnl_rate'))}` "
        f"median PnL=`{_fmt(pack.get('median_pnl'))}` "
        f"LOOKED_WRONG=`{pack.get('LOOKED_WRONG_TO_REMOVE_n')}` LOOKED_RIGHT=`{pack.get('LOOKED_RIGHT_TO_REMOVE_n')}`"
    )


def build_markdown(report: dict[str, Any]) -> str:
    req = dict(report.get("required") or {})
    dec = dict(report.get("decision") or {})
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"VERDICT: **{req.get('VERDICT')}** CASE=`{dec.get('CASE')}`",
        f"TRUE_OOS=`{req.get('TRUE_OOS')}` CERTIFIED=`{req.get('CERTIFIED')}` RANKING_RULE_CREATED=`{dec.get('RANKING_RULE_CREATED')}`",
        f"NEXT: {req.get('NEXT')}",
        "",
        "Diagnostic RCA only. No new ENTRY rule. 2-of-3 not retuned. Window 5s fixed. board_ok unused. "
        "Branch U unused. CAP occupancy-only. 20260903 unused.",
        "",
        "## Identity reproduction",
        "",
    ]
    for cohort_key, label in (("development", "DEVELOPMENT"), ("forward", "FORWARD_BURNED")):
        body = dict(report.get(cohort_key) or {})
        ident = dict(body.get("identity") or {})
        attr = dict(body.get("attribution") or {})
        lines.append(
            f"- {label}: COMMON=`{ident.get('COMMON_N')}` CONTROL_ONLY=`{ident.get('CONTROL_ONLY_N')}` "
            f"INCREMENTAL=`{ident.get('INCREMENTAL_TREATMENT_N')}` "
            f"DIRECT_REMOVAL=`{_fmt(attr.get('DIRECT_REMOVAL_EFFECT'))}` "
            f"DOWNSTREAM=`{_fmt(attr.get('DOWNSTREAM_OCCUPANCY_EFFECT'))}`"
        )
    lines.extend(["", "## A. HARD_REJECT_SEMANTIC_FAILURE", ""])
    for cohort_key, label in (("development", "DEVELOPMENT"), ("forward", "FORWARD_BURNED")):
        sem = dict((report.get(cohort_key) or {}).get("HARD_REJECT_SEMANTIC") or {})
        lines.append(f"### {label}")
        lines.append(_sem_line("unconstrained flagged", dict(sem.get("UNCONSTRAINED_FLAGGED") or {})))
        roles = dict(sem.get("UNCONSTRAINED_FLAGGED_BY_ROLE") or {})
        lines.append(_sem_line("flagged CORE", dict(roles.get("CORE") or {})))
        lines.append(_sem_line("flagged ADDED", dict(roles.get("ADDED") or {})))
        lines.append(_sem_line("direct Pre-CAP removed occupancy fills", dict(sem.get("DIRECT_PRECAP_REMOVED") or {})))
        droles = dict(sem.get("DIRECT_PRECAP_REMOVED_BY_ROLE") or {})
        lines.append(_sem_line("direct CORE", dict(droles.get("CORE") or {})))
        lines.append(_sem_line("direct ADDED", dict(droles.get("ADDED") or {})))
        lines.append(_sem_line("occupancy cascade removed", dict(sem.get("OCCUPANCY_CASCADE_REMOVED") or {})))
        lines.append("")
    lines.extend(["## B. Replacement pairs", ""])
    for cohort_key, label in (("development", "DEVELOPMENT"), ("forward", "FORWARD_BURNED")):
        body = dict(report.get(cohort_key) or {})
        pe = dict(body.get("PAIR_ECONOMICS") or {})
        od = dict(body.get("ORDER") or {})
        lines.append(
            f"- {label}: pair_n=`{pe.get('pair_n')}` better=`{pe.get('replacement_better_n')}` "
            f"worse=`{pe.get('replacement_worse_n')}` median delta=`{_fmt(pe.get('median_pair_delta'))}` "
            f"total pair delta=`{_fmt(pe.get('total_pair_delta'))}`"
        )
        lines.append(
            f"  elapsed median s=`{_fmt(od.get('median_elapsed_sec_removed_to_replacement'))}` "
            f"removed min from open=`{_fmt(od.get('median_removed_minutes_from_open'))}` "
            f"replacement min from open=`{_fmt(od.get('median_replacement_minutes_from_open'))}` "
            f"removed CORE/ADDED=`{od.get('CONTROL_ONLY_CORE_n')}/{od.get('CONTROL_ONLY_ADDED_n')}` "
            f"incr CORE/ADDED=`{od.get('INCREMENTAL_CORE_n')}/{od.get('INCREMENTAL_ADDED_n')}`"
        )
    lines.extend(["", "## C. Relative board vs outcome", ""])
    for cohort_key, label in (("development", "DEVELOPMENT"), ("forward", "FORWARD_BURNED")):
        rel = dict((report.get(cohort_key) or {}).get("RELATIVE_BOARD") or {})
        lines.append(
            f"- {label}: usable=`{rel.get('usable_preference_n')}` agree=`{rel.get('preference_agree_n')}` "
            f"agree_rate=`{_fmt(rel.get('preference_agree_rate'))}` "
            f"median Δadverse=`{_fmt(rel.get('median_replacement_minus_removed_adverse'))}` "
            f"median Δpnl=`{_fmt(rel.get('median_replacement_minus_removed_pnl'))}` "
            f"aligned=`{rel.get('lower_adverse_aligns_with_higher_pnl')}`"
        )
    lines.extend(
        [
            "",
            "## Decision",
            "",
            f"HARD_REJECT_SEMANTIC_FAILURE=`{dec.get('HARD_REJECT_SEMANTIC_FAILURE')}`",
            f"RELATIVE_ALIGNED_DEV/FWD=`{dec.get('RELATIVE_ALIGNED_DEV')}` / `{dec.get('RELATIVE_ALIGNED_FWD')}`",
            f"ORDER_MAIN=`{dec.get('ORDER_MAIN')}`",
            f"IDENTITY_OK=`{dec.get('IDENTITY_OK')}`",
            "",
            "STOP. No new rule implemented.",
            "",
        ]
    )
    return "\n".join(lines)
