"""Publish exactly 3 artifacts for the floor-break candidate run."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_redesign.entry_anchored_floor_break_candidate_spec import (
    ANALYSIS_ID,
    CANDIDATE_ID,
    FIRST_PROSPECTIVE_DAY,
    STATE_MACHINE,
)
from research.simple_tech_redesign.isolation import ENTRY_ANCHORED_FLOOR_BREAK_CANDIDATE_OUT

SHEET_ORDER = (
    "summary",
    "identity",
    "state_machine",
    "boundary",
    "direct",
    "classes",
    "portfolio",
    "incremental",
    "winner_harm",
    "days",
    "concentration",
    "core_added",
    "fwd",
    "triggers",
    "incremental_fills",
    "decision",
    "leak",
)


def _cell(v: Any) -> Any:
    if isinstance(v, (dict, list, tuple)):
        return json.dumps(json_sanitize(v), ensure_ascii=False, default=str)
    return v


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    ENTRY_ANCHORED_FLOOR_BREAK_CANDIDATE_OUT.mkdir(parents=True, exist_ok=True)
    for p in ENTRY_ANCHORED_FLOOR_BREAK_CANDIDATE_OUT.iterdir():
        if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}:
            p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (ENTRY_ANCHORED_FLOOR_BREAK_CANDIDATE_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (ENTRY_ANCHORED_FLOOR_BREAK_CANDIDATE_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(ENTRY_ANCHORED_FLOOR_BREAK_CANDIDATE_OUT / "audit.xlsx")


def build_sheets(report: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    dec = dict(report.get("decision") or {})
    req = dict(report.get("required") or {})
    dev = dict(report.get("development") or {})
    fwd = dict(report.get("forward") or {})
    sheets: dict[str, list[dict[str, Any]]] = {}
    sheets["summary"] = kv_rows(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "CANDIDATE_ID": CANDIDATE_ID,
            "VERDICT": dec.get("VERDICT"),
            "CASE": dec.get("CASE"),
            "CANDIDATE_FROZEN": dec.get("CANDIDATE_FROZEN"),
            "TRUE_OOS": False,
            "CERTIFIED": False,
            "first_eligible_prospective_date": dec.get("first_eligible_prospective_date"),
        }
    )
    sheets["identity"] = kv_rows({"DEV": dev.get("identity"), "FWD": fwd.get("identity")})
    sheets["state_machine"] = kv_rows(dict(STATE_MACHINE))
    bdev = dict(dev.get("boundary") or {})
    bfwd = dict(fwd.get("boundary") or {})
    sheets["boundary"] = (
        [{"cohort": "DEVELOPMENT", **{k: v for k, v in bdev.items() if not str(k).endswith("_trades")}}]
        + [{"cohort": "FORWARD_BURNED", **{k: v for k, v in bfwd.items() if not str(k).endswith("_trades")}}]
        + [{"cohort": "DEVELOPMENT", "bucket": "A", **r} for r in list(bdev.get("A_trades") or [])]
        + [{"cohort": "DEVELOPMENT", "bucket": "B", **r} for r in list(bdev.get("B_trades") or [])]
        + [{"cohort": "DEVELOPMENT", "bucket": "C", **r} for r in list(bdev.get("C_trades") or [])]
        + [{"cohort": "FORWARD_BURNED", "bucket": "A", **r} for r in list(bfwd.get("A_trades") or [])]
        + [{"cohort": "FORWARD_BURNED", "bucket": "B", **r} for r in list(bfwd.get("B_trades") or [])]
        + [{"cohort": "FORWARD_BURNED", "bucket": "C", **r} for r in list(bfwd.get("C_trades") or [])]
    )
    sheets["direct"] = kv_rows(
        {
            "DEV_trigger_n": dev.get("trigger_n"),
            "DEV_DIRECT_EXIT_DELTA": dev.get("DIRECT_EXIT_DELTA"),
            "DEV_winner_harm_delta": dev.get("winner_harm_delta"),
            "DEV_failure_saved_delta": dev.get("failure_saved_delta"),
            "DEV_SLOT": dev.get("SLOT_RELEASE_DOWNSTREAM_DELTA"),
            "DEV_TOTAL": dev.get("TOTAL_CAUSAL_DELTA"),
            "DEV_decomp_ok": dev.get("decomp_ok"),
        }
    )
    class_rows = []
    for cohort, pack in (("DEVELOPMENT", dev), ("FORWARD_BURNED", fwd)):
        for name, d in dict(pack.get("class_diag") or {}).items():
            class_rows.append({"cohort": cohort, "class": name, **dict(d)})
    sheets["classes"] = class_rows
    port_rows = []
    for cohort, pack in (("DEVELOPMENT", dev), ("FORWARD_BURNED", fwd)):
        for arm in ("control", "treatment"):
            port_rows.append({"cohort": cohort, "arm": arm, **dict(pack.get(arm) or {})})
    sheets["portfolio"] = port_rows
    sheets["incremental"] = kv_rows(
        {
            "DEV": {k: v for k, v in dict(dev.get("incremental") or {}).items() if k != "trades"},
            "FWD": {k: v for k, v in dict(fwd.get("incremental") or {}).items() if k != "trades"},
        }
    )
    harm_rows = []
    for cohort, pack in (("DEVELOPMENT", dev), ("FORWARD_BURNED", fwd)):
        for lab, body in dict(pack.get("winner_harm") or {}).items():
            harm_rows.append({"cohort": cohort, "class": lab, **{k: v for k, v in dict(body).items() if k != "trades"}})
            for t in list(body.get("trades") or []):
                harm_rows.append({"cohort": cohort, "class": lab, **dict(t)})
    sheets["winner_harm"] = harm_rows
    sheets["days"] = [{"cohort": "DEVELOPMENT", **r} for r in list(dev.get("days") or [])] + [
        {"cohort": "FORWARD_BURNED", **r} for r in list(fwd.get("days") or [])
    ]
    sheets["concentration"] = kv_rows({"DEV": dev.get("concentration"), "FWD": fwd.get("concentration")})
    sheets["core_added"] = kv_rows({"DEV": dev.get("core_added"), "FWD": fwd.get("core_added")})
    sheets["fwd"] = kv_rows(
        {
            "DIRECT_EXIT_DELTA": fwd.get("DIRECT_EXIT_DELTA"),
            "SLOT_RELEASE_DOWNSTREAM_DELTA": fwd.get("SLOT_RELEASE_DOWNSTREAM_DELTA"),
            "TOTAL_CAUSAL_DELTA": fwd.get("TOTAL_CAUSAL_DELTA"),
            "control": fwd.get("control"),
            "treatment": fwd.get("treatment"),
            "winner_harm_delta": fwd.get("winner_harm_delta"),
        }
    )
    sheets["triggers"] = list(dev.get("shared_trigger_trades") or []) + [
        {"cohort": "FORWARD_BURNED", **r} for r in list(fwd.get("shared_trigger_trades") or [])
    ]
    sheets["incremental_fills"] = list((dev.get("incremental") or {}).get("trades") or []) + [
        {"cohort": "FORWARD_BURNED", **r} for r in list((fwd.get("incremental") or {}).get("trades") or [])
    ]
    sheets["decision"] = kv_rows({k: dec.get(k) for k in sorted(dec)})
    sheets["leak"] = kv_rows(dict(report.get("leak") or {}))
    if not sheets["boundary"]:
        sheets["boundary"] = [{"empty": True}]
    if not sheets["classes"]:
        sheets["classes"] = [{"empty": True}]
    if not sheets["portfolio"]:
        sheets["portfolio"] = [{"empty": True}]
    if not sheets["winner_harm"]:
        sheets["winner_harm"] = [{"empty": True}]
    if not sheets["days"]:
        sheets["days"] = [{"empty": True}]
    if not sheets["triggers"]:
        sheets["triggers"] = [{"empty": True}]
    if not sheets["incremental_fills"]:
        sheets["incremental_fills"] = [{"empty": True}]
    _ = req
    return sheets


def _n(v: Any) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.4g}" if abs(v) < 1 else f"{v:.2f}"
    return str(v)


def build_markdown(report: dict[str, Any]) -> str:
    dec = dict(report.get("decision") or {})
    req = dict(report.get("required") or {})
    dev = dict(report.get("development") or {})
    fwd = dict(report.get("forward") or {})
    ident_d = dict(dev.get("identity") or {})
    ident_f = dict(fwd.get("identity") or {})
    b = dict(dev.get("boundary") or {})
    u = dict((dev.get("class_diag") or {}).get("U") or {})
    pe = dict((dev.get("class_diag") or {}).get("P_EARLY") or {})
    ptf = dict((dev.get("class_diag") or {}).get("PTF") or {})
    dip = dict((dev.get("class_diag") or {}).get("DIP") or {})
    good = dict((dev.get("class_diag") or {}).get("GOOD") or {})
    incr = dict(dev.get("incremental") or {})
    ds = dict(dev.get("day_summary") or {})
    ca = dict(dev.get("core_added") or {})
    conc = dict(dev.get("concentration") or {})
    harm = dict(dev.get("winner_harm") or {})
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"**Candidate:** `{CANDIDATE_ID}`",
        f"**VERDICT:** `{dec.get('VERDICT')}` (CASE {dec.get('CASE')})",
        f"**CANDIDATE_FROZEN:** `{dec.get('CANDIDATE_FROZEN')}`",
        "",
        "## Required answers",
        f"1. CONTROL identity DEV: fill_n={ident_d.get('fill_n')} CORE/ADDED={ident_d.get('core_n')}/{ident_d.get('added_n')} PnL={ident_d.get('pnl')} ok={ident_d.get('ok')}",
        f"2. CONTROL identity FWD: fill_n={ident_f.get('fill_n')} CORE/ADDED={ident_f.get('core_n')}/{ident_f.get('added_n')} PnL={ident_f.get('pnl')} ok={ident_f.get('ok')}",
        "3. State machine: start STRUCTURAL_EXIT_ARMED; completed 1m Close only; Close>SETUP_HIGH → UPSIDE_BREAK_LOCKED; Close<SETUP_LOW while ARMED → PRE_UPSIDE_PULLBACK_FLOOR_BREAK; EXIT first causal Bid after fill/trigger finalize; session-close operational.",
        f"4. signal→fill boundary: A_prefill_upside={b.get('A_prefill_upside_n')} B_prefill_floor={b.get('B_prefill_floor_n')} C_postfill_vs_rca={b.get('C_postfill_vs_rca_n')}",
        f"5. DEV trigger N (shared occupancy)={dev.get('trigger_n')} occupancy-control trigger N={dev.get('occupancy_control_trigger_n')}",
        f"6. U: trigger_n={u.get('trigger_n')} direct_delta={_n(u.get('direct_delta'))} median={_n(u.get('median_delta'))} +n={u.get('positive_delta_n')} -n={u.get('negative_delta_n')}",
        f"7. P_EARLY: trigger_n={pe.get('trigger_n')} direct_delta={_n(pe.get('direct_delta'))} median={_n(pe.get('median_delta'))} +n={pe.get('positive_delta_n')} -n={pe.get('negative_delta_n')}",
        f"8. PTF: trigger_n={ptf.get('trigger_n')} direct_delta={_n(ptf.get('direct_delta'))} median={_n(ptf.get('median_delta'))} +n={ptf.get('positive_delta_n')} -n={ptf.get('negative_delta_n')}",
        f"9. DIP: trigger_n={dip.get('trigger_n')} direct_delta={_n(dip.get('direct_delta'))} candidate_exit_pnl={_n((harm.get('DIP') or {}).get('candidate_exit_pnl'))} control_close_pnl={_n((harm.get('DIP') or {}).get('control_close_pnl'))}",
        f"10. GOOD: trigger_n={good.get('trigger_n')} direct_delta={_n(good.get('direct_delta'))} candidate_exit_pnl={_n((harm.get('GOOD') or {}).get('candidate_exit_pnl'))} control_close_pnl={_n((harm.get('GOOD') or {}).get('control_close_pnl'))}",
        f"11. DIRECT_EXIT_DELTA={_n(dev.get('DIRECT_EXIT_DELTA'))}",
        f"12. treatment incremental fill N={incr.get('incremental_fill_n')}",
        f"13. incremental fill PnL={_n(incr.get('incremental_pnl'))} GP={_n(incr.get('gross_profit'))} GL={_n(incr.get('gross_loss'))} PF={incr.get('PF')}",
        f"14. SLOT_RELEASE_DOWNSTREAM_DELTA={_n(dev.get('SLOT_RELEASE_DOWNSTREAM_DELTA'))} (incremental={_n(dev.get('SLOT_RELEASE_INCREMENTAL_PNL'))} displaced={_n(dev.get('DISPLACED_TRADE_DELTA'))})",
        f"15. TOTAL_CAUSAL_DELTA={_n(dev.get('TOTAL_CAUSAL_DELTA'))} decomp_ok={dev.get('decomp_ok')}",
        f"16. Control/Treatment total PnL={_n((dev.get('control') or {}).get('total_pnl'))} / {_n((dev.get('treatment') or {}).get('total_pnl'))}",
        f"17. Control/Treatment PF={(dev.get('control') or {}).get('PF')} / {(dev.get('treatment') or {}).get('PF')}",
        f"18. Control/Treatment max DD={_n((dev.get('control') or {}).get('max_drawdown'))} / {_n((dev.get('treatment') or {}).get('max_drawdown'))}",
        f"19. day improve/worsen/flat={ds.get('improve_day_n')}/{ds.get('worsen_day_n')}/{ds.get('flat_day_n')} top+={ds.get('top_positive_day')} top-={ds.get('top_negative_day')}",
        f"20. CORE/ADDED direct delta={_n((ca.get('CORE') or {}).get('direct_delta'))} / {_n((ca.get('ADDED') or {}).get('direct_delta'))}",
        f"21. concentration: top+day={conc.get('largest_positive_delta_day')} share={_n(conc.get('largest_positive_day_share'))} LOO_day={_n(conc.get('leave_one_largest_positive_delta_day'))} top+sym={conc.get('largest_positive_delta_symbol')} share={_n(conc.get('largest_positive_symbol_share'))} warn={conc.get('concentration_warning_gt_50pct')} collapse={conc.get('support_collapse')}",
        f"22. FWD_BURNED direct delta={_n(fwd.get('DIRECT_EXIT_DELTA'))}",
        f"23. FWD_BURNED slot delta={_n(fwd.get('SLOT_RELEASE_DOWNSTREAM_DELTA'))}",
        f"24. FWD_BURNED total delta={_n(fwd.get('TOTAL_CAUSAL_DELTA'))}",
        f"25. winner harm DEV={_n(dev.get('winner_harm_delta'))} failure_saved={_n(dev.get('failure_saved_delta'))} FWD_harm={_n(fwd.get('winner_harm_delta'))}",
        f"26. verdict=`{dec.get('VERDICT')}` CASE={dec.get('CASE')}",
        f"27. CANDIDATE_FROZEN={dec.get('CANDIDATE_FROZEN')}",
        f"28. candidate identity/hash spec={req.get('CANDIDATE_SPEC_SHA256')} source={req.get('SOURCE_SHA256')} identity={req.get('CANDIDATE_IDENTITY_HASH')}",
        "29. TRUE_OOS=false",
        "30. CERTIFIED=false",
        f"31. first eligible prospective date={dec.get('first_eligible_prospective_date') or FIRST_PROSPECTIVE_DAY}",
        f"32. next={dec.get('NEXT')}",
        "",
        f"**NEXT:** {dec.get('NEXT')}",
        "",
        "STOP.",
    ]
    return "\n".join(lines) + "\n"
