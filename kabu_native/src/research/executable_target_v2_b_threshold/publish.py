"""Write report.json / report.md / audit.xlsx only. No mass CSV."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from research.executable_target_v2_b_threshold import ANALYSIS_ID, NEW_FORWARD_N, PRIMARY_TARGET

NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "executable_target_v2_b_threshold"
HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
HEADER_FONT = Font(color="FFFFFF", bold=True)

SHEET_ORDER = (
    "Summary",
    "Target_Contract",
    "Target_Coverage",
    "Endpoint_Audit",
    "MFE_Audit",
    "OOF_Contract",
    "B0",
    "Score_Distribution",
    "Threshold_Search",
    "Threshold_OOF",
    "B0_vs_B1",
    "Concentration",
    "Daily",
    "Session",
    "Decision",
    "Safety",
)


def json_sanitize(obj: Any) -> Any:
    if isinstance(obj, float):
        if obj == float("inf"):
            return "Infinity"
        if obj == float("-inf"):
            return "-Infinity"
        if obj != obj:
            return None
        return obj
    if isinstance(obj, dict):
        return {str(k): json_sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_sanitize(v) for v in obj]
    return obj


def kv_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    flat: dict[str, Any] = {}

    def _flat(prefix: str, obj: Any) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                _flat(f"{prefix}.{k}" if prefix else str(k), v)
        else:
            flat[prefix] = obj

    _flat("", data)
    return [{"key": k, "value": v} for k, v in flat.items()]


def _sheet(ws, rows: list[dict[str, Any]]) -> None:
    if not rows:
        ws.append(["empty"])
        return
    keys: list[str] = []
    for r in rows:
        for k in r.keys():
            if k not in keys:
                keys.append(k)
    ws.append(keys)
    for cell in ws[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    for r in rows:
        vals = []
        for k in keys:
            v = r.get(k)
            if isinstance(v, (dict, list)):
                v = json.dumps(v, ensure_ascii=False, default=str)
            if isinstance(v, float) and v != v:
                v = None
            vals.append(v)
        ws.append(vals)
    for i, _k in enumerate(keys, start=1):
        ws.column_dimensions[get_column_letter(i)].width = min(36, max(12, len(str(_k)) + 2))


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(OUT / "audit.xlsx")


def build_markdown(report: dict[str, Any]) -> str:
    t = report.get("target") or {}
    b0 = report.get("B0") or {}
    b1 = report.get("B1") or {}
    occ = report.get("occupancy_parity") or {}
    occ_b0 = report.get("occupancy_B0") or {}
    lines = [
        "# EXECUTABLE TARGET CONTRACT V2 + UNIFORM10 CURRENT ENTRY THRESHOLD",
        "",
        f"ANALYSIS_ID: {ANALYSIS_ID}",
        f"PRIMARY_TARGET: {PRIMARY_TARGET}",
        "OFFLINE RESEARCH ONLY. Runtime / C14 / CLOCK / ENTRY / EXIT unchanged.",
        "SAFETY: submit/cancel/live=0/0/0. Paper=0. OPVAL=0.",
        "OPENS_WITHIN_1S is not used in model eligibility.",
        "",
        "## TARGET",
        "",
        f"TARGET_CONTRACT_V2_VALID: {t.get('TARGET_CONTRACT_V2_VALID')}",
        f"PRIMARY_ROWS: {t.get('PRIMARY_ROWS')}",
        f"ITAYOSE_BASE_ROW_N: {t.get('ITAYOSE_BASE_ROW_N')}",
        f"NON_EXECUTABLE_T0_ROW_N: {t.get('NON_EXECUTABLE_T0_ROW_N')}",
        f"15_20_PRIMARY_TARGET_STATUS: {t.get('15_20_PRIMARY_TARGET_STATUS')}",
        f"LODO_OOF_CONTRACT_READY: {t.get('LODO_OOF_CONTRACT_READY')}",
        f"TARGET_VERDICT: {t.get('TARGET_VERDICT')}",
        "",
        "Canonical continuous close is AM 11:30 / PM 15:00. 15:20+600s=15:30 is outside",
        "continuous session, so 15:20 is EXCLUDED_NONCONTINUOUS_ENDPOINT. SESSION_CLOSE",
        "return is diagnostic-only and is not mixed into PRIMARY_TARGET.",
        "",
        "## B0 (UNIFORM10 + CURRENT ENTRY + CORRECTED FILL, Dual Lane cache)",
        "",
        f"B0: {b0.get('trades')} / {b0.get('PnL')} / {b0.get('PF')} / {b0.get('maxDD')}",
        "",
        "## Occupancy engine vs Dual B0",
        "",
        f"fill Jaccard: {occ.get('jaccard')} cache_n={occ.get('cache_n')} occ_n={occ.get('occ_n')}",
        f"occupancy_B0: {occ_b0.get('trades')} / {occ_b0.get('PnL')} / {occ_b0.get('PF')} / {occ_b0.get('maxDD')}",
        "Gap is Dual-Lane Arch E exits vs post-hoc hypo Arch E, plus residual fill-set",
        "difference. Nested B1 is scored against occupancy NO_THRESHOLD, not Dual PnL.",
        "Absolute score CV across days is high, so the gate is CS percentile not absolute T.",
        "",
        "## B1 OOF",
        "",
        f"BEST_B1_THRESHOLD: {b1.get('BEST_B1_THRESHOLD')}",
        f"B1_OOF: {b1.get('trades')} / {b1.get('PnL')} / {b1.get('PF')} / {b1.get('maxDD')}",
        f"B1_positive_day_rate: {b1.get('positive_day_rate')}",
        f"B1_median_daily_pnl: {b1.get('median_daily_pnl')}",
        f"B1_PnL_ex_top3_trades: {b1.get('PnL_ex_top3_trades')}",
        f"B1_PnL_ex_top3_days: {b1.get('PnL_ex_top3_days')}",
        f"B1_PnL_ex_top_symbol: {b1.get('PnL_ex_top_symbol')}",
        f"B_VERDICT: {report.get('B_VERDICT')}",
        "",
        "Every outer fold selected NO_THRESHOLD: no CS percentile beat inner B0 on",
        "PF AND maxDD-not-worse AND positive-day-rate AND median-daily-PnL together.",
        "Per-feature hard gates were not started (MAX_HARD_GATES=0 this run).",
        "",
        f"C_REBUILD_V2_STATUS: {report.get('C_REBUILD_V2_STATUS')}",
        "C_REBUILD_V1: C_REBUILD_V1_INVALID_TARGET_CONTAMINATED",
        f"NEW_FORWARD_N: {NEW_FORWARD_N}",
        "",
        "STOP. B1 not written to Runtime. No new Strategy candidate. Paper not started.",
        "",
    ]
    return "\n".join(lines)
