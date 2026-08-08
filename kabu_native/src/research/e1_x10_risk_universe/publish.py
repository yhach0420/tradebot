"""Publish E1_X10 Risk Universe artifacts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research.e1_x6_provisional.util import sha256_file

SHEETS = [
    "Index", "Precommit", "Identity", "CurrentRiskConfig", "ReferencePrices",
    "OneLotNotional", "TickRisk", "SpreadRisk", "DepthRisk", "Freshness",
    "BidJumps", "ExecutableLoss", "SymbolRiskSummary", "CapitalConcentration",
    "StaticEligibility", "DynamicGateFeasibility", "NotionalBands", "KioxiaProfile",
    "PnLIndependence", "Verdict", "Tests", "Determinism", "Safety", "ChangeLog",
]


def _kv(d: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for k, v in d.items():
        if isinstance(v, (dict, list)):
            rows.append({"key": k, "value": json.dumps(v, default=str)[:12000]})
        else:
            rows.append({"key": k, "value": v})
    return rows


def _cell(v: Any) -> Any:
    if v is None:
        return ""
    if isinstance(v, (dict, list, tuple)):
        return json.dumps(v, default=str)[:32000]
    if isinstance(v, bool):
        return str(v)
    return v


def _write_xlsx(path: Path, sheets: dict[str, list[dict[str, Any]]]) -> None:
    from openpyxl import Workbook
    wb = Workbook()
    wb.remove(wb.active)
    for name in SHEETS:
        ws = wb.create_sheet(name[:31])
        rows = sheets.get(name) or [{"note": "empty"}]
        keys: list[str] = []
        for r in rows:
            for k in r:
                if k not in keys:
                    keys.append(k)
        ws.append(keys)
        for r in rows:
            ws.append([_cell(r.get(k)) for k in keys])
    wb.save(path)


def publish(report: dict[str, Any], tests: dict[str, Any], det: dict[str, Any], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    sh = report.pop("_sheets", {})
    cfg = report.get("current_risk_config") or {}
    sheets = {
        "Index": [
            {"item": "analysis_id", "value": report.get("analysis_id")},
            {"item": "run_id", "value": report.get("run_id")},
            {"item": "verdict", "value": report.get("verdict")},
            {"item": "purpose", "value": report.get("purpose")},
        ],
        "Precommit": [
            {"key": "source_closure", "value": report.get("source_closure")},
            {"key": "period", "value": json.dumps(report.get("period"))},
            {"key": "diagnostic_only", "value": True},
            {"key": "no_alpha_selection", "value": True},
        ],
        "Identity": _kv({
            "source_closure": report.get("source_closure"),
            "lot": report.get("lot"),
            "qty_unit": report.get("qty_unit_contract"),
        }),
        "CurrentRiskConfig": sh.get("CurrentRiskConfig") or [],
        "ReferencePrices": sh.get("ReferencePrices") or [],
        "OneLotNotional": sh.get("OneLotNotional") or [],
        "TickRisk": sh.get("TickRisk") or [],
        "SpreadRisk": sh.get("SpreadRisk") or [],
        "DepthRisk": sh.get("DepthRisk") or [],
        "Freshness": sh.get("Freshness") or [],
        "BidJumps": sh.get("BidJumps") or [],
        "ExecutableLoss": sh.get("ExecutableLoss") or [],
        "SymbolRiskSummary": sh.get("SymbolRiskSummary") or [],
        "CapitalConcentration": sh.get("CapitalConcentration") or [],
        "StaticEligibility": sh.get("StaticEligibility") or [],
        "DynamicGateFeasibility": sh.get("DynamicGateFeasibility") or [],
        "NotionalBands": sh.get("NotionalBands") or [],
        "KioxiaProfile": sh.get("KioxiaProfile") or [],
        "PnLIndependence": sh.get("PnLIndependence") or [],
        "Verdict": _kv(report.get("verdict_detail") or {}),
        "Tests": tests.get("rows") or [{"test": "n/a", "outcome": "n/a"}],
        "Determinism": _kv(det),
        "Safety": _kv(report.get("safety") or {}),
        "ChangeLog": [
            {"change": "E1_X10_RISK_UNIVERSE_AUDIT", "note": "diagnostic only; no runtime"},
            {"change": "risk_budget", "note": cfg.get("status")},
            {"change": "position_cap", "note": f"max_concurrent_positions={cfg.get('max_concurrent_positions')}"},
        ],
    }
    public = {k: v for k, v in report.items() if not k.startswith("_")}
    # trim bulky required_risk_budgets duplication in json if symbol_risk_summary present
    public["tests"] = {
        "exit_code": tests.get("exit_code"),
        "passed": tests.get("passed"),
        "failed": tests.get("failed"),
        "total": tests.get("total"),
    }
    public["determinism"] = det

    jp, mp, xp = out_dir / "report.json", out_dir / "report.md", out_dir / "audit.xlsx"
    jp.write_text(json.dumps(public, indent=2, default=str), encoding="utf-8")

    cov = report.get("coverage") or {}
    nd = report.get("notional_distribution") or {}
    cap = report.get("capital_concentration") or {}
    kx = report.get("kioxia_profile") or {}
    md = [
        f"# {report.get('document_id')}",
        "",
        f"- run_id: `{report.get('run_id')}`",
        f"- **verdict: `{report.get('verdict')}`**",
        f"- purpose: AUTOMATION_RISK_ELIGIBILITY (not alpha)",
        f"- risk_budget_status: `{cfg.get('status')}`",
        f"- max_concurrent_positions: `{cfg.get('max_concurrent_positions')}`",
        f"- per_trade_risk_limit_yen: `{cfg.get('per_trade_risk_limit_yen')}`",
        f"- available_trading_capital_yen: `{cfg.get('available_trading_capital_yen')}`",
        "",
        "## Coverage",
        "",
        f"- symbol quote coverage: {cov.get('symbol_quote_coverage')}",
        f"- reference price coverage: {cov.get('reference_price_symbol_day_coverage')}",
        f"- spread ok coverage: {cov.get('spread_ok_symbol_coverage')}",
        "",
        "## One-lot notional",
        "",
        f"- median/p90/max: {nd.get('median')} / {nd.get('p90')} / {nd.get('max')}",
        "",
        f"- top3 required capital: {cap.get('required_capital_for_top3')} ({cap.get('top3_symbols')})",
        f"- position_cap required capital: {cap.get('required_capital_for_position_cap')}",
        "",
        "## 285A",
        "",
        f"- notional median: {kx.get('one_lot_notional_median')}",
        f"- tick risk: {kx.get('one_tick_risk_yen_100_median')}",
        f"- spread p95: {kx.get('spread_cost_p95')}",
        f"- exec 5s p95: {kx.get('exec_loss_5s_p95')}",
        f"- est execution risk: {kx.get('estimated_execution_risk_yen')}",
        "",
        f"- PnL independence: {(report.get('pnl_independence') or {}).get('status')}",
        f"- next: {(report.get('verdict_detail') or {}).get('next')}",
        f"- tests: {tests.get('passed')}/{tests.get('total')}",
        f"- A/B: {det.get('ab_match')}",
        "- submit/cancel/live: 0/0/0",
        "",
    ]
    mp.write_text("\n".join(md), encoding="utf-8")
    _write_xlsx(xp, sheets)
    shas = {"report.json": sha256_file(jp), "report.md": sha256_file(mp), "audit.xlsx": sha256_file(xp)}
    public["published_shas"] = shas
    jp.write_text(json.dumps(public, indent=2, default=str), encoding="utf-8")
    return shas
