"""Write report.json / report.md / audit.xlsx only under v16_bar_confirmed_be_reloss/."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_exit_family.isolation import V16_OUT
from research.simple_tech_exit_family.v16_spec import ANALYSIS_ID

SHEET_ORDER = (
    "Precommit",
    "Identity",
    "Hold",
    "Taxonomy",
    "G2",
    "P2P3",
    "Recovery",
    "G1Compare",
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
    "G2_EVENT_N_180",
    "G2_EVENT_N_300",
    "P2_EVENT_RATE_180",
    "P2_EVENT_RATE_300",
    "P3_EVENT_RATE_180",
    "P3_EVENT_RATE_300",
    "P2_CAPTURE_RATE_180",
    "P2_CAPTURE_RATE_300",
    "DELTA_VS_HOLD180",
    "DELTA_VS_HOLD300",
    "P2_DELTA",
    "P3_DELTA",
    "NET_G2_CONTRIBUTION_BPS",
    "POST_EVENT_RECOVERY_P2",
    "POST_EVENT_RECOVERY_P3",
    "G2_SUPPORTED_180",
    "G2_SUPPORTED_300",
    "G2_CROSS_HORIZON_SUPPORTED",
    "EXIT_SPEC_FROZEN",
    "TRUE_OOS",
    "ENTRY_CERTIFIED",
    "NON_INTERFERENCE_PASS",
    "VERDICT",
    "NEXT",
)


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    V16_OUT.mkdir(parents=True, exist_ok=True)
    extra = [p for p in V16_OUT.iterdir() if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}]
    for p in extra:
        p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (V16_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (V16_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(V16_OUT / "audit.xlsx")


def flatten_trade(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "date": r.get("date"),
        "symbol": r.get("symbol"),
        "signal_t0": r.get("t0"),
        "fill_t": r.get("fill_t"),
        "fill_price": r.get("fill_price"),
        "hold_180": r.get("hold_180"),
        "hold_300": r.get("hold_300"),
        "class_180": r.get("class_180"),
        "class_300": r.get("class_300"),
        "G2_ARM_T": r.get("G2_ARM_T"),
        "G2_EVENT_T": r.get("G2_EVENT_T"),
        "G2_BAR_CLOSE": r.get("G2_BAR_CLOSE"),
        "G2_EXIT_PNL": r.get("G2_EXIT_PNL"),
        "MAX_AFTER_180": r.get("MAX_AFTER_180"),
        "MAX_AFTER_300": r.get("MAX_AFTER_300"),
    }


def build_markdown(report: dict[str, Any]) -> str:
    req = dict(report.get("required") or {})
    d180 = dict(req.get("DELTA_VS_HOLD180") or {})
    d300 = dict(req.get("DELTA_VS_HOLD300") or {})
    net = dict(req.get("NET_G2_CONTRIBUTION_BPS") or {})
    cmp = dict(req.get("G1_COMPARE") or {})
    p2r = dict(req.get("POST_EVENT_RECOVERY_P2") or {})
    p3r = dict(req.get("POST_EVENT_RECOVERY_P3") or {})
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
            f"G2_EVENT_N 180/300: `{req.get('G2_EVENT_N_180')}` / `{req.get('G2_EVENT_N_300')}`",
            f"P2_EVENT_RATE 180/300: `{req.get('P2_EVENT_RATE_180')}` / `{req.get('P2_EVENT_RATE_300')}`",
            f"P3_EVENT_RATE 180/300: `{req.get('P3_EVENT_RATE_180')}` / `{req.get('P3_EVENT_RATE_300')}`",
            f"P2_CAPTURE_RATE 180/300: `{req.get('P2_CAPTURE_RATE_180')}` / `{req.get('P2_CAPTURE_RATE_300')}`",
            "",
            f"DELTA_VS_HOLD180 mean/median: `{d180.get('mean')}` / `{d180.get('median')}`",
            f"DELTA_VS_HOLD300 mean/median: `{d300.get('mean')}` / `{d300.get('median')}`",
            f"NET_G2_CONTRIBUTION_BPS 180/300: `{net.get(180) or net.get('180')}` / `{net.get(300) or net.get('300')}`",
            "",
            f"POST_EVENT_RECOVERY_P2 median 180/300: `{(p2r.get(180) or p2r.get('180') or {}).get('median')}` / `{(p2r.get(300) or p2r.get('300') or {}).get('median')}`",
            f"POST_EVENT_RECOVERY_P3 median 180/300: `{(p3r.get(180) or p3r.get('180') or {}).get('median')}` / `{(p3r.get(300) or p3r.get('300') or {}).get('median')}`",
            "",
            f"G2 vs G1 event N 180: `{cmp.get('G2_EVENT_N_180')}` < `{cmp.get('G1_EVENT_N_180_V15')}` = `{cmp.get('G2_LT_G1_EVENT_N_180')}`",
            f"P3 false-exit reduced 180/300: `{cmp.get('P3_FALSE_EXIT_REDUCED_180')}` / `{cmp.get('P3_FALSE_EXIT_REDUCED_300')}`",
            "",
            f"G2_SUPPORTED_180: `{req.get('G2_SUPPORTED_180')}`  G2_SUPPORTED_300: `{req.get('G2_SUPPORTED_300')}`",
            f"G2_CROSS_HORIZON_SUPPORTED: `{req.get('G2_CROSS_HORIZON_SUPPORTED')}`",
            f"EXIT_SPEC_FROZEN: `{req.get('EXIT_SPEC_FROZEN')}`",
            f"TRUE_OOS: `{req.get('TRUE_OOS')}`  ENTRY_CERTIFIED: `{req.get('ENTRY_CERTIFIED')}`",
            f"NON_INTERFERENCE_PASS: `{req.get('NON_INTERFERENCE_PASS')}`",
            "",
            f"NEXT: {req.get('NEXT')}",
            "",
        ]
    )
