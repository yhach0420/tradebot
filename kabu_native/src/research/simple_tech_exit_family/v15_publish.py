"""Write report.json / report.md / audit.xlsx only under v15_be_reloss_mechanism/."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_exit_family.isolation import V15_OUT
from research.simple_tech_exit_family.v15_spec import ANALYSIS_ID

SHEET_ORDER = (
    "Precommit",
    "Identity",
    "Hold",
    "Taxonomy",
    "G1",
    "Timing",
    "WinnerHarm",
    "Recovery",
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
    "ARMED_N_180",
    "ARMED_N_300",
    "G1_EVENT_N_180",
    "G1_EVENT_N_300",
    "P2_CAPTURE_RATE_180",
    "P2_CAPTURE_RATE_300",
    "DELTA_VS_HOLD180",
    "DELTA_VS_HOLD300",
    "P3_G1_EVENT_N",
    "WINNER_HARM_180",
    "WINNER_HARM_300",
    "POST_EVENT_RECOVERY",
    "G1_SUPPORTED_180",
    "G1_SUPPORTED_300",
    "G1_CROSS_HORIZON_SUPPORTED",
    "EXIT_SPEC_FROZEN",
    "TRUE_OOS",
    "ENTRY_CERTIFIED",
    "NON_INTERFERENCE_PASS",
    "VERDICT",
    "NEXT",
)


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    V15_OUT.mkdir(parents=True, exist_ok=True)
    extra = [p for p in V15_OUT.iterdir() if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}]
    for p in extra:
        p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (V15_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (V15_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(V15_OUT / "audit.xlsx")


def flatten_trade(r: dict[str, Any]) -> dict[str, Any]:
    return {
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
        "FIRST_POSITIVE_TIME": r.get("FIRST_POSITIVE_TIME"),
        "G1_ARM_T": r.get("G1_ARM_T"),
        "G1_EVENT_T": r.get("G1_EVENT_T"),
        "G1_EXIT_PNL": r.get("G1_EXIT_PNL"),
        "BE_LOSS_TIME": r.get("BE_LOSS_TIME"),
        "MAX_AFTER_180": r.get("MAX_AFTER_180"),
        "MAX_AFTER_300": r.get("MAX_AFTER_300"),
        "MFE_BID_BPS": r.get("MFE_BID_BPS"),
        "MAE_BID_BPS": r.get("MAE_BID_BPS"),
    }


def build_markdown(report: dict[str, Any]) -> str:
    req = dict(report.get("required") or {})
    d180 = dict(req.get("DELTA_VS_HOLD180") or {})
    d300 = dict(req.get("DELTA_VS_HOLD300") or {})
    w180 = dict(req.get("WINNER_HARM_180") or {})
    w300 = dict(req.get("WINNER_HARM_300") or {})
    rec = dict(req.get("POST_EVENT_RECOVERY") or {})
    rec180 = dict(rec.get(180) or rec.get("180") or {})
    rec300 = dict(rec.get(300) or rec.get("300") or {})
    p3 = dict(req.get("P3_G1_EVENT_N") or {})
    return "\n".join(
        [
            f"# {ANALYSIS_ID}",
            "",
            f"VERDICT: **{req.get('VERDICT')}**",
            f"CASE: `{req.get('CASE')}`",
            "",
            f"ENTRY_STACK_PARITY: `{req.get('ENTRY_STACK_PARITY')}`",
            f"E4_FILL_SET_HASH_PARITY: `{req.get('E4_FILL_SET_HASH_PARITY')}`  FILLED_N=`{req.get('FILLED_N')}`",
            "",
            f"ARMED_N 180/300: `{req.get('ARMED_N_180')}` / `{req.get('ARMED_N_300')}`",
            f"G1_EVENT_N 180/300: `{req.get('G1_EVENT_N_180')}` / `{req.get('G1_EVENT_N_300')}`",
            f"P2_CAPTURE_RATE 180/300: `{req.get('P2_CAPTURE_RATE_180')}` / `{req.get('P2_CAPTURE_RATE_300')}`",
            "",
            f"DELTA_VS_HOLD180 mean/median: `{d180.get('mean')}` / `{d180.get('median')}`",
            f"DELTA_VS_HOLD300 mean/median: `{d300.get('mean')}` / `{d300.get('median')}`",
            "",
            f"P3_G1_EVENT_N 180/300: `{p3.get(180) or p3.get('180')}` / `{p3.get(300) or p3.get('300')}`",
            f"WINNER_HARM_180 mean/median/n: `{w180.get('WINNER_HARM_MEAN')}` / `{w180.get('WINNER_HARM_MEDIAN')}` / `{w180.get('WINNER_EVENT_N')}`",
            f"WINNER_HARM_300 mean/median/n: `{w300.get('WINNER_HARM_MEAN')}` / `{w300.get('WINNER_HARM_MEDIAN')}` / `{w300.get('WINNER_EVENT_N')}`",
            f"POST_EVENT_RECOVERY median 180/300: `{rec180.get('median')}` / `{rec300.get('median')}`",
            "",
            f"G1_SUPPORTED_180: `{req.get('G1_SUPPORTED_180')}`  G1_SUPPORTED_300: `{req.get('G1_SUPPORTED_300')}`",
            f"G1_CROSS_HORIZON_SUPPORTED: `{req.get('G1_CROSS_HORIZON_SUPPORTED')}`",
            f"EXIT_SPEC_FROZEN: `{req.get('EXIT_SPEC_FROZEN')}`",
            f"TRUE_OOS: `{req.get('TRUE_OOS')}`  ENTRY_CERTIFIED: `{req.get('ENTRY_CERTIFIED')}`",
            f"NON_INTERFERENCE_PASS: `{req.get('NON_INTERFERENCE_PASS')}`",
            "",
            f"NEXT: {req.get('NEXT')}",
            "",
        ]
    )
