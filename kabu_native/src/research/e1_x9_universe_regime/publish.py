"""Publish E1_X9 artifacts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research.e1_x6_provisional.util import sha256_file

SHEETS = [
    "Index", "Precommit", "Identity", "MetadataSources", "AsOfValidation", "Coverage",
    "SymbolMetadata", "DirectOwnership", "Microstructure", "RegimeDefinitions",
    "RegimeAssignments", "RegimeSupport", "FirstTouchByRegime", "UpdateSignalByRegime",
    "WithinSymbolReference", "Interactions", "EconomicReference", "KioxiaProfile",
    "Verdict", "Tests", "Determinism", "Safety", "ChangeLog",
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
        ],
        "Precommit": _kv(report.get("precommit") or {}),
        "Identity": _kv(report.get("identity") or {}),
        "MetadataSources": sh.get("MetadataSources") or [],
        "AsOfValidation": sh.get("AsOfValidation") or [],
        "Coverage": sh.get("Coverage") or [],
        "SymbolMetadata": sh.get("SymbolMetadata") or [],
        "DirectOwnership": sh.get("DirectOwnership") or [],
        "Microstructure": sh.get("Microstructure") or [],
        "RegimeDefinitions": sh.get("RegimeDefinitions") or [],
        "RegimeAssignments": sh.get("RegimeAssignments") or [],
        "RegimeSupport": sh.get("RegimeSupport") or [],
        "FirstTouchByRegime": sh.get("FirstTouchByRegime") or [],
        "UpdateSignalByRegime": sh.get("UpdateSignalByRegime") or [],
        "WithinSymbolReference": sh.get("WithinSymbolReference") or [],
        "Interactions": sh.get("Interactions") or [],
        "EconomicReference": sh.get("EconomicReference") or [],
        "KioxiaProfile": sh.get("KioxiaProfile") or [],
        "Verdict": _kv(report.get("verdict_detail") or {}),
        "Tests": tests.get("rows") or [{"test": "n/a", "outcome": "n/a"}],
        "Determinism": _kv(det),
        "Safety": _kv(report.get("safety") or {}),
        "ChangeLog": [
            {"change": "E1_X9_UNIVERSE_REGIME_AUDIT", "note": "descriptive; no PFQ revival; no new family"},
            {"change": "direct_ownership", "note": "NOT_EVALUABLE"},
            {"change": "market_cap_asof", "note": "unavailable before 20260721"},
        ],
    }
    public = {k: v for k, v in report.items() if not k.startswith("_")}
    public["tests"] = {"exit_code": tests.get("exit_code"), "passed": tests.get("passed"),
                       "failed": tests.get("failed"), "total": tests.get("total")}
    public["determinism"] = det
    jp, mp, xp = out_dir / "report.json", out_dir / "report.md", out_dir / "audit.xlsx"
    jp.write_text(json.dumps(public, indent=2, default=str), encoding="utf-8")
    kx = report.get("kioxia_profile") or {}
    cov = report.get("coverage") or {}
    vd = report.get("verdict_detail") or {}
    md = [
        f"# {report.get('document_id')}",
        "",
        f"- run_id: `{report.get('run_id')}`",
        f"- **verdict: `{report.get('verdict')}`**",
        f"- pfq_revive: false",
        "",
        "## Coverage",
        "",
        f"- market symbol/episode: {cov.get('market_symbol')} / {cov.get('market_episode')}",
        f"- turnover symbol/episode: {cov.get('turnover_symbol')} / {cov.get('turnover_episode')}",
        f"- market_cap: 0 (not as-of)",
        f"- direct ownership: NOT_EVALUABLE",
        "",
        "## 285A",
        "",
        f"- segment/index/turnover: {kx.get('market_segment')} / {kx.get('index_status')} / {kx.get('turnover_tercile')}",
        f"- similar regime symbols: {kx.get('similar_n')} — {kx.get('similar_regime_symbols')}",
        "",
        f"- update_heavy_vs_light: {report.get('update_heavy_vs_light')}",
        f"- next: {vd.get('next')}",
        f"- tests: {tests.get('passed')}/{tests.get('total')}",
        f"- A/B: {det.get('ab_match')}",
        f"- submit/cancel/live: 0/0/0",
        "",
    ]
    mp.write_text("\n".join(md), encoding="utf-8")
    _write_xlsx(xp, sheets)
    shas = {"report.json": sha256_file(jp), "report.md": sha256_file(mp), "audit.xlsx": sha256_file(xp)}
    public["published_shas"] = shas
    jp.write_text(json.dumps(public, indent=2, default=str), encoding="utf-8")
    return shas
