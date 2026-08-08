"""Publish E1_X7–X9 closure artifacts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research.e1_x6_provisional.util import sha256_file

SHEETS = [
    "Index", "SourceRuns", "SourceIdentity", "SupersededRuns",
    "PFQEntryFindings", "PFQExitFindings", "RevisionFailure",
    "SymbolLeverage", "KioxiaDependence", "UniverseRegime",
    "MetadataLimitations", "FinalStatuses", "RejectedPaths",
    "FutureDesignPrinciples", "OpenItems", "Verdict", "Tests",
    "Determinism", "Safety", "ChangeLog",
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
            {"item": "document_id", "value": report.get("document_id")},
            {"item": "run_id", "value": report.get("run_id")},
            {"item": "verdict", "value": report.get("verdict")},
            {"item": "pfq_closed", "value": True},
            {"item": "robust_strategy", "value": False},
        ],
        "SourceRuns": sh.get("SourceRuns") or [],
        "SourceIdentity": sh.get("SourceIdentity") or [],
        "SupersededRuns": sh.get("SupersededRuns") or [],
        "PFQEntryFindings": sh.get("PFQEntryFindings") or [],
        "PFQExitFindings": sh.get("PFQExitFindings") or [],
        "RevisionFailure": sh.get("RevisionFailure") or [],
        "SymbolLeverage": sh.get("SymbolLeverage") or [],
        "KioxiaDependence": sh.get("KioxiaDependence") or [],
        "UniverseRegime": sh.get("UniverseRegime") or [],
        "MetadataLimitations": sh.get("MetadataLimitations") or [],
        "FinalStatuses": sh.get("FinalStatuses") or [],
        "RejectedPaths": sh.get("RejectedPaths") or [],
        "FutureDesignPrinciples": sh.get("FutureDesignPrinciples") or [],
        "OpenItems": sh.get("OpenItems") or [],
        "Verdict": _kv(report.get("verdict_detail") or {}),
        "Tests": tests.get("rows") or [{"test": "n/a", "outcome": "n/a"}],
        "Determinism": _kv(det),
        "Safety": _kv(report.get("safety") or {}),
        "ChangeLog": [
            {"change": "E1_X7_X9_PROGRAM_CLOSURE", "note": "assembly only; no new computation"},
            {"change": "exit_gate_v1", "note": "SUPERSEDED_BY_EXIT_GATE_RECONCILIATION_V2"},
            {"change": "pfq_line", "note": "PFQ_CURRENT_LINE_CLOSED_REJECTED"},
            {"change": "no_auto_next_study", "note": "stop after closure"},
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

    srcs = report.get("sources") or {}
    md = [
        f"# {report.get('document_id')}",
        "",
        f"- run_id: `{report.get('run_id')}`",
        f"- **verdict: `{report.get('verdict')}`**",
        f"- pfq_closed: true · robust_strategy: false",
        f"- prospective_allowed: false · shadow_allowed: false · runtime_impact: false",
        "",
        "## Canonical sources",
        "",
    ]
    for k, v in srcs.items():
        md.append(f"- `{k}`: `{v.get('run_id')}` → `{v.get('verdict')}`")
    md += [
        "",
        "## Superseded",
        "",
        "- `e1x7_pfq_exit_gate_20260804_235025_A` → `SUPERSEDED_BY_EXIT_GATE_RECONCILIATION_V2`",
        "",
        "## PFQ final",
        "",
        "- ENTRY path (UPDATE_Q70): supported on fixed-grid first-touch",
        "- ENTRY+EXIT economics: all rejected",
        "- EXIT revision mechanism failed → `PFQ_CURRENT_LINE_CLOSED_REJECTED`",
        "",
        "## 285A",
        "",
        "- Strong threshold / concentration / economic distortion",
        "- ENTRY path signal survives ex-285A and LOSO 65/65",
        "",
        "## Universe",
        "",
        "- No stable low-participation proxy separation; direct ownership not evaluable",
        "",
        "## Next",
        "",
        "- Allowed: new independent ENTRY family hypothesis selection only",
        "- Forbidden: auto-implement, candidate gen, Prospective/Shadow, runtime change",
        "",
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
