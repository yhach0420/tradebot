"""Write report.json / report.md / audit.xlsx only under v14_exit_state_path_rca/."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_exit_family.isolation import V14_OUT
from research.simple_tech_exit_family.v14_spec import ANALYSIS_ID, EVENT_IDS

SHEET_ORDER = (
    "Precommit",
    "Identity",
    "Hold",
    "Taxonomy",
    "D1",
    "D2",
    "D3",
    "Trades",
    "Reporting",
    "Integrity",
    "Non_Interference",
)

REQUIRED_KEYS = (
    "ANALYSIS_ID",
    "ENTRY_STACK_PARITY",
    "E4_FILL_SET_HASH_PARITY",
    "FILLED_N",
    "HOLD_60",
    "HOLD_180",
    "HOLD_300",
    "P1_180",
    "P2_180",
    "P3_180",
    "P4_180",
    "P1_300",
    "P2_300",
    "P3_300",
    "P4_300",
    "PRIMARY_EXIT_PROBLEM_180",
    "PRIMARY_EXIT_PROBLEM_300",
    "PRIMARY_EXIT_PROBLEM",
    "EXIT_MECHANISM_SUPPORTED_LIST",
    "EXIT_SPEC_FROZEN",
    "TRUE_OOS",
    "ENTRY_CERTIFIED",
    "NON_INTERFERENCE_PASS",
    "VERDICT",
    "NEXT",
)


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    V14_OUT.mkdir(parents=True, exist_ok=True)
    extra = [p for p in V14_OUT.iterdir() if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}]
    for p in extra:
        p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (V14_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (V14_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
    wb = Workbook()
    first = True
    for name in SHEET_ORDER:
        rows = sheets.get(name) or [{"empty": True}]
        if first:
            ws = wb.active
            ws.title = name[:31]
            first = False
        else:
            ws = wb.create_sheet(name[:31])
        _sheet(ws, rows)
    wb.save(V14_OUT / "audit.xlsx")


def flatten_trade(r: dict[str, Any]) -> dict[str, Any]:
    rec = {
        "date": r.get("date"),
        "symbol": r.get("symbol"),
        "signal_t0": r.get("t0"),
        "fill_t": r.get("fill_t"),
        "fill_price": r.get("fill_price"),
        "hold_60": r.get("hold_60"),
        "hold_180": r.get("hold_180"),
        "hold_300": r.get("hold_300"),
        "class_180": r.get("class_180"),
        "class_300": r.get("class_300"),
        "MFE_BID_BPS": r.get("MFE_BID_BPS"),
        "MAE_BID_BPS": r.get("MAE_BID_BPS"),
        "TIME_TO_MFE": r.get("TIME_TO_MFE"),
        "TIME_TO_MAE": r.get("TIME_TO_MAE"),
        "FIRST_POSITIVE_TIME": r.get("FIRST_POSITIVE_TIME"),
        "FIRST_NEGATIVE_TIME": r.get("FIRST_NEGATIVE_TIME"),
        "BE_LOSS_TIME": r.get("BE_LOSS_TIME"),
        "MAX_BEFORE_180": r.get("MAX_BEFORE_180"),
        "MIN_BEFORE_180": r.get("MIN_BEFORE_180"),
        "MAX_BEFORE_300": r.get("MAX_BEFORE_300"),
        "MIN_BEFORE_300": r.get("MIN_BEFORE_300"),
    }
    for eid in EVENT_IDS:
        ev = (r.get("events") or {}).get(eid) or {}
        rec[f"{eid}_event_t"] = ev.get("event_t")
        rec[f"{eid}_exit_t"] = ev.get("exit_t")
        rec[f"{eid}_exit_pnl"] = ev.get("exit_pnl")
        rec[f"{eid}_internal"] = ev.get("internal")
    return rec


def build_markdown(report: dict[str, Any]) -> str:
    req = dict(report.get("required") or {})
    return "\n".join(
        [
            f"# {ANALYSIS_ID}",
            "",
            f"VERDICT: **{req.get('VERDICT')}**",
            f"PRIMARY_EXIT_PROBLEM: `{req.get('PRIMARY_EXIT_PROBLEM')}`",
            f"180: `{req.get('PRIMARY_EXIT_PROBLEM_180')}`  300: `{req.get('PRIMARY_EXIT_PROBLEM_300')}`",
            "",
            f"ENTRY_STACK_PARITY: `{req.get('ENTRY_STACK_PARITY')}`",
            f"E4_FILL_SET_HASH_PARITY: `{req.get('E4_FILL_SET_HASH_PARITY')}`  FILLED_N=`{req.get('FILLED_N')}`",
            "",
            f"P1/P2/P3/P4 180: {req.get('P1_180')}/{req.get('P2_180')}/{req.get('P3_180')}/{req.get('P4_180')}",
            f"P1/P2/P3/P4 300: {req.get('P1_300')}/{req.get('P2_300')}/{req.get('P3_300')}/{req.get('P4_300')}",
            "",
            f"EXIT_MECHANISM_SUPPORTED_LIST: `{req.get('EXIT_MECHANISM_SUPPORTED_LIST')}`",
            f"EXIT_SPEC_FROZEN: `{req.get('EXIT_SPEC_FROZEN')}`",
            f"TRUE_OOS: `{req.get('TRUE_OOS')}`  ENTRY_CERTIFIED: `{req.get('ENTRY_CERTIFIED')}`",
            f"NON_INTERFERENCE_PASS: `{req.get('NON_INTERFERENCE_PASS')}`",
            "",
            f"NEXT: {req.get('NEXT')}",
            "",
        ]
    )
