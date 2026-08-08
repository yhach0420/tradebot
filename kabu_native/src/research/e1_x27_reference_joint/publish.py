"""Publish E1_X27 three artifacts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research.e1_x6_provisional.util import sha256_file

SHEETS = [
    "Index", "SourceIdentity", "ManifestIntegrity", "RouteRegistry", "AliasRegistry",
    "ControlMapping", "ReferenceContract", "LedgerCoverage", "RouteMetrics",
    "DiscoveryResults", "EvaluationResults", "Stress20260803", "Consumed20260804",
    "EntrySelection", "ExitAdaptation", "PairwiseCommonEpisode", "JointClassification",
    "CommonControlEntryEvidence", "FamilyResults", "ProtectVsRoom", "ExitReasonResults",
    "DailyResults", "SymbolResults", "LODO", "LOSO", "DependencyTags", "StressTags",
    "Bootstrap", "FDR", "Views", "X28Handoff", "Tests", "Determinism", "Safety", "ChangeLog",
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
    sheets = {n: sh.get(n) or [{"note": "empty"}] for n in SHEETS}
    sheets["Index"] = [
        {"item": "run_id", "value": report.get("run_id")},
        {"item": "verdict", "value": report.get("verdict")},
        {"item": "manifest_v2_sha", "value": report.get("manifest_v2_sha")},
        {"item": "semantic_routes", "value": report.get("semantic_routes")},
        {"item": "joint_positive", "value": (report.get("evaluation_class_counts") or {}).get("REFERENCE_JOINT_EDGE_POSITIVE")},
    ]
    sheets["Tests"] = tests.get("rows") or []
    sheets["Determinism"] = _kv(det)
    sheets["Safety"] = _kv(report.get("safety") or {})
    public = {k: v for k, v in report.items() if not k.startswith("_")}
    public["tests"] = {
        "exit_code": tests.get("exit_code"), "passed": tests.get("passed"),
        "failed": tests.get("failed"), "total": tests.get("total"),
    }
    public["determinism"] = det
    jp, mp, xp = out_dir / "report.json", out_dir / "report.md", out_dir / "audit.xlsx"
    jp.write_text(json.dumps(public, indent=2, default=str), encoding="utf-8")
    cc = report.get("evaluation_class_counts") or {}
    md = [
        f"# {report.get('document_id')}",
        "",
        f"- run_id: `{report.get('run_id')}`",
        f"- verdict: `{report.get('verdict')}`",
        f"- manifest V2 sha: `{report.get('manifest_v2_sha')}`",
        f"- semantic routes: `{report.get('semantic_routes')}`",
        f"- Evaluation joint-positive: `{cc.get('REFERENCE_JOINT_EDGE_POSITIVE')}`",
        f"- X28 handoff routes: `{report.get('x28_handoff_route_count')}`",
        f"- tests: {tests.get('passed')}/{tests.get('total')} · A/B: {det.get('ab_match')}",
        "- submit/cancel/live: 0/0/0",
        "- reference CurrentPrice only; ask/bid deferred to X28",
        "",
    ]
    mp.write_text("\n".join(md), encoding="utf-8")
    _write_xlsx(xp, sheets)
    shas = {"report.json": sha256_file(jp), "report.md": sha256_file(mp), "audit.xlsx": sha256_file(xp)}
    public["published_shas"] = shas
    jp.write_text(json.dumps(public, indent=2, default=str), encoding="utf-8")
    return shas
