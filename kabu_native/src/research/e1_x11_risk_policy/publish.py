"""Publish E1_X11 artifacts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research.e1_x6_provisional.util import sha256_file

SHEETS = [
    "Index", "Precommit", "SourceIdentity", "ConfigDrift", "WalletFields", "CapitalBase",
    "HistoryInventory", "RollingSupport", "RecurringUniverse", "PolicyDefinition",
    "SymbolDayRisk", "CapitalScenarios", "StaticEligibility", "DailyEligibleCounts",
    "DynamicGate", "PriceFreshnessContract", "SpecialQuote", "KioxiaProfile",
    "PolicyAdequacy", "Verdict", "Tests", "Determinism", "Safety", "ChangeLog",
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
    sheets = {
        "Index": [
            {"item": "analysis_id", "value": report.get("analysis_id")},
            {"item": "run_id", "value": report.get("run_id")},
            {"item": "verdict", "value": report.get("verdict")},
            {"item": "policy_id", "value": (report.get("policy_definition") or {}).get("POLICY_ID")},
        ],
        "Precommit": [
            {"key": "source_x10", "value": report.get("source_x10")},
            {"key": "policy_id", "value": "FIXED100_CONSERVATIVE_V1"},
            {"key": "fractions", "value": "0.25%/1.0%/15%/75%/25%"},
            {"key": "pnl_forbidden", "value": True},
        ],
        "SourceIdentity": sh.get("SourceIdentity") or [],
        "ConfigDrift": sh.get("ConfigDrift") or [],
        "WalletFields": sh.get("WalletFields") or [],
        "CapitalBase": sh.get("CapitalBase") or [],
        "HistoryInventory": sh.get("HistoryInventory") or [],
        "RollingSupport": sh.get("RollingSupport") or [],
        "RecurringUniverse": sh.get("RecurringUniverse") or [],
        "PolicyDefinition": sh.get("PolicyDefinition") or [],
        "SymbolDayRisk": sh.get("SymbolDayRisk") or [],
        "CapitalScenarios": sh.get("CapitalScenarios") or [],
        "StaticEligibility": sh.get("StaticEligibility") or [],
        "DailyEligibleCounts": sh.get("DailyEligibleCounts") or [],
        "DynamicGate": sh.get("DynamicGate") or [],
        "PriceFreshnessContract": sh.get("PriceFreshnessContract") or [],
        "SpecialQuote": sh.get("SpecialQuote") or [],
        "KioxiaProfile": sh.get("KioxiaProfile") or [],
        "PolicyAdequacy": sh.get("PolicyAdequacy") or [],
        "Verdict": _kv(report.get("verdict_detail") or {}),
        "Tests": tests.get("rows") or [{"test": "n/a", "outcome": "n/a"}],
        "Determinism": _kv(det),
        "Safety": _kv(report.get("safety") or {}),
        "ChangeLog": [
            {"change": "E1_X11_RISK_POLICY_CALIBRATION", "note": "no runtime; no YAML change"},
            {"change": "capital_base", "note": "UNRESOLVED"},
            {"change": "config_drift", "note": "CONFIG_FILENAME_CAP3_CANONICAL_CAP5_DRIFT"},
        ],
    }
    public = {k: v for k, v in report.items() if not k.startswith("_")}
    public["tests"] = {
        "exit_code": tests.get("exit_code"),
        "passed": tests.get("passed"),
        "failed": tests.get("failed"),
        "total": tests.get("total"),
    }
    public["determinism"] = det
    jp, mp, xp = out_dir / "report.json", out_dir / "report.md", out_dir / "audit.xlsx"
    jp.write_text(json.dumps(public, indent=2, default=str), encoding="utf-8")

    cb = report.get("capital_base") or {}
    ru = report.get("recurring_universe") or {}
    kx = report.get("kioxia_profile") or {}
    md = [
        f"# {report.get('document_id')}",
        "",
        f"- run_id: `{report.get('run_id')}`",
        f"- **verdict: `{report.get('verdict')}`**",
        f"- policy: `FIXED100_CONSERVATIVE_V1` (0.25 / 1.0 / 15 / 75 / 25)",
        f"- max_concurrent_positions: 5 (filename cap3 drift recorded)",
        f"- capital_base: `{cb.get('status')}`",
        f"- recurring coverage: {ru.get('recurring_risk_metric_coverage')} (n={ru.get('n_recurring')})",
        f"- 285A required capital: {kx.get('required_capital')}",
        f"- special_quote: {(report.get('special_quote') or {}).get('dynamic_guard')}",
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
