"""Write report.json / report.md / audit.xlsx only under v21_sizing_attribution_rca/."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_strategy.isolation import V21_OUT
from research.simple_tech_strategy.v21_spec import ANALYSIS_ID

SHEET_ORDER = (
    "Precommit",
    "Identity",
    "Notional",
    "Corr",
    "Quartiles",
    "Days",
    "BestWorstDays",
    "Symbols",
    "TopSymbols",
    "BpsRobustness",
    "Fixed100",
    "Normalized1M",
    "Attribution",
    "Trades",
    "Reporting",
    "Integrity",
    "Non_Interference",
)

REQUIRED_KEYS = (
    "ANALYSIS_ID",
    "STRATEGY_STACK_PARITY",
    "TRADE_N",
    "NOTIONAL_DISTRIBUTION",
    "CORR_NOTIONAL_PNL_YEN",
    "CORR_NOTIONAL_ABS_PNL",
    "CORR_NOTIONAL_BPS",
    "NOTIONAL_QUARTILES",
    "BEST_DAY_ATTRIBUTION",
    "TOP_SYMBOL_ATTRIBUTION",
    "FIXED100",
    "NORMALIZED_1M_DIAGNOSTIC",
    "PRIMARY_FRAGILITY_SOURCE",
    "TRUE_OOS",
    "FORWARD_OOS_ELIGIBLE",
    "VERDICT",
    "NEXT",
)


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    V21_OUT.mkdir(parents=True, exist_ok=True)
    extra = [p for p in V21_OUT.iterdir() if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}]
    for p in extra:
        p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (V21_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (V21_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(V21_OUT / "audit.xlsx")


def flatten_v21_trade(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "date": r.get("date"),
        "symbol": r.get("symbol"),
        "entry_price": r.get("entry_price"),
        "exit_price": r.get("exit_price"),
        "shares": r.get("shares"),
        "entry_notional_yen": r.get("entry_notional_yen"),
        "pnl_bps": r.get("pnl_bps"),
        "pnl_yen_100": r.get("pnl_yen_100"),
        "absolute_pnl_yen": r.get("absolute_pnl_yen"),
        "NORMALIZED_PNL_1M": r.get("NORMALIZED_PNL_1M"),
        "fill_t": r.get("fill_t"),
        "actual_exit_quote_time": r.get("actual_exit_quote_time"),
        "scheduled_exit_time": r.get("scheduled_exit_time"),
        "normalized_is_policy": False,
    }


def build_markdown(report: dict[str, Any]) -> str:
    req = dict(report.get("required") or {})
    dist = dict(req.get("NOTIONAL_DISTRIBUTION") or {})
    fx = dict(req.get("FIXED100") or {})
    nm = dict(req.get("NORMALIZED_1M_DIAGNOSTIC") or {})
    return "\n".join(
        [
            f"# {ANALYSIS_ID}",
            "",
            f"VERDICT: **{req.get('VERDICT')}**",
            f"CASE: `{req.get('CASE')}`",
            f"PRIMARY_FRAGILITY_SOURCE: `{req.get('PRIMARY_FRAGILITY_SOURCE')}`",
            "",
            f"STRATEGY_STACK_PARITY: `{req.get('STRATEGY_STACK_PARITY')}`  TRADE_N: `{req.get('TRADE_N')}`",
            "",
            "NOTIONAL_DISTRIBUTION:",
            f"- min/median/max: `{dist.get('min')}` / `{dist.get('median')}` / `{dist.get('max')}`",
            f"- CV: `{dist.get('CV')}`  max/median: `{dist.get('max_median_ratio')}`",
            f"- TOP1/3/5 share: `{dist.get('TOP1_NOTIONAL_SHARE')}` / `{dist.get('TOP3_NOTIONAL_SHARE')}` / `{dist.get('TOP5_NOTIONAL_SHARE')}`",
            "",
            f"CORR_NOTIONAL_PNL_YEN: `{req.get('CORR_NOTIONAL_PNL_YEN')}`",
            f"CORR_NOTIONAL_ABS_PNL: `{req.get('CORR_NOTIONAL_ABS_PNL')}`",
            f"CORR_NOTIONAL_BPS: `{req.get('CORR_NOTIONAL_BPS')}`",
            "",
            f"BEST_DAY_ATTRIBUTION: `{req.get('BEST_DAY_ATTRIBUTION')}`",
            f"TOP_SYMBOL_ATTRIBUTION: `{req.get('TOP_SYMBOL_ATTRIBUTION')}`",
            "",
            "FIXED100:",
            f"- TOTAL `{fx.get('TOTAL')}` PF `{fx.get('PF')}` EX_BEST `{fx.get('EX_BEST')}` EX_TOP3 `{fx.get('EX_TOP3')}` DD `{fx.get('DD')}`",
            "",
            "NORMALIZED_1M_DIAGNOSTIC (not a strategy):",
            f"- TOTAL `{nm.get('TOTAL')}` PF `{nm.get('PF')}` EX_BEST `{nm.get('EX_BEST')}` EX_TOP3 `{nm.get('EX_TOP3')}` DD `{nm.get('DD')}` DROP_TOP_SYMBOL `{nm.get('DROP_TOP_SYMBOL')}`",
            "",
            f"TRUE_OOS: `{req.get('TRUE_OOS')}`  FORWARD_OOS_ELIGIBLE: `{req.get('FORWARD_OOS_ELIGIBLE')}`",
            f"V20_OFFICIAL_UNCHANGED: `{req.get('V20_OFFICIAL_UNCHANGED')}`",
            "",
            f"NEXT: {req.get('NEXT')}",
            "",
        ]
    )
