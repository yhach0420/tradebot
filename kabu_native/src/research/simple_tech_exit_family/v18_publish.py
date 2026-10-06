"""Write report.json / report.md / audit.xlsx only under v18_fixed180_exit/."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_exit_family.isolation import V18_OUT
from research.simple_tech_exit_family.v18_spec import ANALYSIS_ID

SHEET_ORDER = (
    "Precommit",
    "Identity",
    "HoldRef",
    "Policy",
    "Latency",
    "Days",
    "VsHold",
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
    "EXIT_POLICY",
    "EXIT_LATENCY",
    "TRADE_N",
    "WIN_N",
    "LOSS_N",
    "WIN_RATE",
    "MEAN_BPS",
    "MEDIAN_BPS",
    "TOTAL_PNL_YEN_100",
    "PROFIT_FACTOR_YEN_100",
    "MAX_DRAWDOWN_YEN_100",
    "POSITIVE_DAY_N",
    "NEGATIVE_DAY_N",
    "EX_BEST_DAY",
    "EX_TOP3_DAY",
    "DROP_TOP_SYMBOL",
    "DROP_TOP3_SYMBOL",
    "DELTA_VS_V14_HOLD180",
    "FIXED180_DEVELOPMENT_SUPPORTED",
    "TRUE_OOS",
    "EXIT_SIGNAL_SPEC_FROZEN_DEVELOPMENT",
    "EXIT_CERTIFIED",
    "ENTRY_CERTIFIED",
    "NON_INTERFERENCE_PASS",
    "VERDICT",
    "NEXT",
)


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    V18_OUT.mkdir(parents=True, exist_ok=True)
    extra = [p for p in V18_OUT.iterdir() if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}]
    for p in extra:
        p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (V18_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (V18_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(V18_OUT / "audit.xlsx")


def flatten_trade(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "date": r.get("date"),
        "symbol": r.get("symbol"),
        "fill_t": r.get("fill_t"),
        "fill_price": r.get("fill_price"),
        "scheduled_exit_time": r.get("scheduled_exit_time"),
        "actual_exit_quote_time": r.get("actual_exit_quote_time"),
        "exit_latency_sec": r.get("exit_latency_sec"),
        "exit_bid": r.get("exit_bid"),
        "exit_bps": r.get("exit_bps"),
        "pnl_yen_100": r.get("pnl_yen_100"),
        "session_clamped": r.get("session_clamped"),
        "V14_HOLD180": r.get("V14_HOLD180"),
        "delta_vs_v14_hold180": r.get("delta_vs_v14_hold180"),
        "EXIT_MISS": r.get("EXIT_MISS"),
    }


def build_markdown(report: dict[str, Any]) -> str:
    req = dict(report.get("required") or {})
    lat = dict(req.get("EXIT_LATENCY") or {})
    dlt = dict(req.get("DELTA_VS_V14_HOLD180") or {})
    return "\n".join(
        [
            f"# {ANALYSIS_ID}",
            "",
            f"VERDICT: **{req.get('VERDICT')}**",
            f"CASE: `{req.get('CASE')}`",
            f"EXIT_POLICY: `{req.get('EXIT_POLICY')}`",
            "",
            f"ENTRY_STACK_PARITY: `{req.get('ENTRY_STACK_PARITY')}`",
            f"E4_FILL_SET_HASH_PARITY: `{req.get('E4_FILL_SET_HASH_PARITY')}`  FILLED_N=`{req.get('FILLED_N')}`",
            "",
            f"TRADE_N: `{req.get('TRADE_N')}`  WIN_N: `{req.get('WIN_N')}`  LOSS_N: `{req.get('LOSS_N')}`  WIN_RATE: `{req.get('WIN_RATE')}`",
            f"MEAN_BPS / MEDIAN_BPS: `{req.get('MEAN_BPS')}` / `{req.get('MEDIAN_BPS')}`",
            f"TOTAL_PNL_YEN_100: `{req.get('TOTAL_PNL_YEN_100')}`  PF: `{req.get('PROFIT_FACTOR_YEN_100')}`  DD: `{req.get('MAX_DRAWDOWN_YEN_100')}`",
            "",
            f"POSITIVE_DAY_N / NEGATIVE_DAY_N: `{req.get('POSITIVE_DAY_N')}` / `{req.get('NEGATIVE_DAY_N')}`",
            f"EX_BEST_DAY: `{req.get('EX_BEST_DAY')}`  EX_TOP3_DAY: `{req.get('EX_TOP3_DAY')}`",
            f"DROP_TOP_SYMBOL: `{req.get('DROP_TOP_SYMBOL')}`  DROP_TOP3_SYMBOL: `{req.get('DROP_TOP3_SYMBOL')}`",
            "",
            f"EXIT_LATENCY mean/median/p75/max: `{lat.get('mean')}` / `{lat.get('median')}` / `{lat.get('p75')}` / `{lat.get('max')}`",
            f"DELTA_VS_V14_HOLD180 mean/median/worst: `{dlt.get('mean')}` / `{dlt.get('median')}` / `{dlt.get('worst')}`",
            "",
            f"FIXED180_DEVELOPMENT_SUPPORTED: `{req.get('FIXED180_DEVELOPMENT_SUPPORTED')}`",
            f"EXIT_SIGNAL_SPEC_FROZEN_DEVELOPMENT: `{req.get('EXIT_SIGNAL_SPEC_FROZEN_DEVELOPMENT')}`",
            f"TRUE_OOS: `{req.get('TRUE_OOS')}`  EXIT_CERTIFIED: `{req.get('EXIT_CERTIFIED')}`",
            f"NON_INTERFERENCE_PASS: `{req.get('NON_INTERFERENCE_PASS')}`",
            "",
            f"NEXT: {req.get('NEXT')}",
            "",
        ]
    )
