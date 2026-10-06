"""Write report.json / report.md / audit.xlsx only under v20_frozen_portfolio_economics/."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_exit_family.v18_publish import flatten_trade
from research.simple_tech_strategy.isolation import V20_OUT
from research.simple_tech_strategy.v20_spec import ANALYSIS_ID

SHEET_ORDER = (
    "Precommit",
    "Identity",
    "Construction",
    "Frequency",
    "PnL",
    "Drawdown",
    "Efficiency",
    "Days",
    "Symbols",
    "LODO",
    "Execution",
    "Trades",
    "Reporting",
    "Integrity",
    "Non_Interference",
)

REQUIRED_KEYS = (
    "ANALYSIS_ID",
    "STRATEGY_STACK_PARITY",
    "TRADE_N",
    "TRADES_PER_ELIGIBLE_DAY",
    "ACTIVE_DAY_N",
    "NO_TRADE_DAY_N",
    "MAX_CONCURRENT_POSITIONS",
    "SAME_SYMBOL_OVERLAP_N",
    "PEAK_GROSS_NOTIONAL_YEN",
    "TOTAL_PNL_YEN_100",
    "PF",
    "WIN_RATE",
    "REALIZED_MAX_DD",
    "MARK_TO_MARKET_MAX_DD",
    "AVG_PNL_PER_ELIGIBLE_DAY",
    "NET_TO_MAX_DD",
    "POSITIVE_DAY_N",
    "NEGATIVE_DAY_N",
    "EX_BEST_DAY_TOTAL_PNL",
    "EX_TOP3_DAY_TOTAL_PNL",
    "TOP_SYMBOL_PNL_SHARE",
    "TOP3_SYMBOL_PNL_SHARE",
    "TOP_DAY_PNL_SHARE",
    "TOP3_DAY_PNL_SHARE",
    "DEVELOPMENT_PORTFOLIO_ECONOMICS_PASS",
    "FORWARD_OOS_ELIGIBLE",
    "TRUE_OOS",
    "STRATEGY_CERTIFIED",
    "VERDICT",
    "NEXT",
)


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    V20_OUT.mkdir(parents=True, exist_ok=True)
    extra = [p for p in V20_OUT.iterdir() if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}]
    for p in extra:
        p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (V20_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (V20_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(V20_OUT / "audit.xlsx")


def flatten_v20_trade(r: dict[str, Any]) -> dict[str, Any]:
    base = flatten_trade(r)
    base.update(
        {
            "t0": r.get("t0"),
            "fill_ask_qty": r.get("fill_ask_qty"),
            "fill_bid_qty": r.get("fill_bid_qty"),
            "exit_bid_qty": r.get("exit_bid_qty"),
            "entry_quote_missing": r.get("entry_quote_missing"),
            "exit_quote_missing": r.get("exit_quote_missing"),
            "entry_qty_missing": r.get("entry_qty_missing"),
            "exit_qty_missing": r.get("exit_qty_missing"),
            "timestamp_inversion": r.get("timestamp_inversion"),
            "future_use": r.get("future_use"),
            "mtm_mae_yen": r.get("mtm_mae_yen"),
            "mtm_mfe_yen": r.get("mtm_mfe_yen"),
        }
    )
    return base


def build_markdown(report: dict[str, Any]) -> str:
    req = dict(report.get("required") or {})
    return "\n".join(
        [
            f"# {ANALYSIS_ID}",
            "",
            f"VERDICT: **{req.get('VERDICT')}**",
            f"CASE: `{req.get('CASE')}`",
            "",
            f"STRATEGY_STACK_PARITY: `{req.get('STRATEGY_STACK_PARITY')}`",
            f"TRADE_N: `{req.get('TRADE_N')}`",
            f"TRADES_PER_ELIGIBLE_DAY: `{req.get('TRADES_PER_ELIGIBLE_DAY')}`",
            f"ACTIVE_DAY_N: `{req.get('ACTIVE_DAY_N')}`  NO_TRADE_DAY_N: `{req.get('NO_TRADE_DAY_N')}`",
            "",
            f"MAX_CONCURRENT_POSITIONS: `{req.get('MAX_CONCURRENT_POSITIONS')}`",
            f"SAME_SYMBOL_OVERLAP_N: `{req.get('SAME_SYMBOL_OVERLAP_N')}`",
            f"PEAK_GROSS_NOTIONAL_YEN: `{req.get('PEAK_GROSS_NOTIONAL_YEN')}`",
            "",
            f"TOTAL_PNL_YEN_100: `{req.get('TOTAL_PNL_YEN_100')}`  PF: `{req.get('PF')}`  WIN_RATE: `{req.get('WIN_RATE')}`",
            f"REALIZED_MAX_DD: `{req.get('REALIZED_MAX_DD')}`  MARK_TO_MARKET_MAX_DD: `{req.get('MARK_TO_MARKET_MAX_DD')}`",
            f"AVG_PNL_PER_ELIGIBLE_DAY: `{req.get('AVG_PNL_PER_ELIGIBLE_DAY')}`  NET_TO_MAX_DD: `{req.get('NET_TO_MAX_DD')}`",
            "",
            f"POSITIVE_DAY_N: `{req.get('POSITIVE_DAY_N')}`  NEGATIVE_DAY_N: `{req.get('NEGATIVE_DAY_N')}`",
            f"EX_BEST_DAY_TOTAL_PNL: `{req.get('EX_BEST_DAY_TOTAL_PNL')}`  EX_TOP3_DAY_TOTAL_PNL: `{req.get('EX_TOP3_DAY_TOTAL_PNL')}`",
            "",
            f"TOP_SYMBOL_PNL_SHARE: `{req.get('TOP_SYMBOL_PNL_SHARE')}`  TOP3_SYMBOL_PNL_SHARE: `{req.get('TOP3_SYMBOL_PNL_SHARE')}`",
            f"TOP_DAY_PNL_SHARE: `{req.get('TOP_DAY_PNL_SHARE')}`  TOP3_DAY_PNL_SHARE: `{req.get('TOP3_DAY_PNL_SHARE')}`",
            "",
            f"DEVELOPMENT_PORTFOLIO_ECONOMICS_PASS: `{req.get('DEVELOPMENT_PORTFOLIO_ECONOMICS_PASS')}`",
            f"FORWARD_OOS_ELIGIBLE: `{req.get('FORWARD_OOS_ELIGIBLE')}`",
            f"TRUE_OOS: `{req.get('TRUE_OOS')}`  STRATEGY_CERTIFIED: `{req.get('STRATEGY_CERTIFIED')}`",
            f"PRE_FEE_PNL: `{req.get('PRE_FEE_PNL')}`  PORTFOLIO_CONSTRUCTION_UNRESOLVED: `{req.get('PORTFOLIO_CONSTRUCTION_UNRESOLVED')}`",
            f"NON_INTERFERENCE_PASS: `{req.get('NON_INTERFERENCE_PASS')}`",
            "",
            f"NEXT: {req.get('NEXT')}",
            "",
        ]
    )
