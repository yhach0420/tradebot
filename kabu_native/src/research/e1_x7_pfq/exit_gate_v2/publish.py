"""Publish EXIT Gate V2 artifacts (separate dir; does not overwrite V1)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research.e1_x6_provisional.util import sha256_file

from . import PAIRS

SHEETS = [
    "Index", "Precommit", "Identity", "PairEpisodes", "Denominators",
    "RepairableLoss", "ProfitableOpportunityCost", "FailureMechanisms",
    "PairGates", "Verdict", "Tests", "Determinism", "Safety", "ChangeLog",
]


def _kv(d: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for k, v in d.items():
        if isinstance(v, (dict, list)):
            rows.append({"key": k, "value": json.dumps(v, default=str)[:8000]})
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
    sheets_data = report.pop("_sheets", {})

    gate_rows = []
    for pid in PAIRS:
        g = (report.get("pair_gates") or {}).get(pid) or {}
        pr = (report.get("pair_results") or {}).get(pid) or {}
        gate_rows.append({
            "pair_id": pid,
            "pass": g.get("pass"),
            **(g.get("checks") or {}),
            "denominator_n": pr.get("denominator_n"),
            "repairable_in_denominator_n": pr.get("repairable_in_denominator_n"),
            "repairable_fraction": pr.get("repairable_fraction"),
            "repairable_days": pr.get("repairable_days"),
            "top_mechanism": pr.get("top_mechanism"),
            "top_mechanism_fraction": pr.get("top_mechanism_fraction"),
            "profitable_soft_exit_opportunity_cost_n": pr.get("profitable_soft_exit_opportunity_cost_n"),
        })

    sheets = {
        "Index": [
            {"item": "analysis_id", "value": report.get("analysis_id")},
            {"item": "run_id", "value": report.get("run_id")},
            {"item": "verdict", "value": report.get("verdict")},
            {"item": "source_exit_gate_run", "value": report.get("source_exit_gate_run")},
        ],
        "Precommit": _kv(report.get("precommit") or {}),
        "Identity": _kv(report.get("identity") or {}),
        "PairEpisodes": sheets_data.get("PairEpisodes") or [],
        "Denominators": sheets_data.get("Denominators") or [],
        "RepairableLoss": sheets_data.get("RepairableLoss") or [],
        "ProfitableOpportunityCost": sheets_data.get("ProfitableOpportunityCost") or [],
        "FailureMechanisms": sheets_data.get("FailureMechanisms") or [],
        "PairGates": gate_rows,
        "Verdict": _kv(report.get("verdict_detail") or {}),
        "Tests": tests.get("rows") or [{"note": "n/a"}],
        "Determinism": _kv(det),
        "Safety": _kv(report.get("safety") or {}),
        "ChangeLog": [
            {"change": "V2_SUBSET_INVARIANT", "note": "repairable ⊆ denominator; profitable soft excluded from Gate"},
            {"change": "V1_NOT_OVERWRITTEN", "note": report.get("source_exit_gate_run")},
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

    jp = out_dir / "report.json"
    mp = out_dir / "report.md"
    xp = out_dir / "audit.xlsx"
    jp.write_text(json.dumps(public, indent=2, default=str), encoding="utf-8")

    vd = report.get("verdict_detail") or {}
    lines = [
        f"# {report.get('analysis_id')}",
        "",
        f"- run_id: `{report.get('run_id')}`",
        f"- corrects: `{report.get('source_exit_gate_run')}` (not overwritten)",
        f"- verdict: `{report.get('verdict')}`",
        f"- selected_baseline: `{vd.get('selected_baseline_pair')}`",
        f"- exit_revision_implemented: false",
        "",
        "## Pair gates (corrected)",
        "",
    ]
    for pid in PAIRS:
        pr = (report.get("pair_results") or {}).get(pid) or {}
        g = (report.get("pair_gates") or {}).get(pid) or {}
        lines += [
            f"### {pid}",
            f"- denominator: {pr.get('denominator_n')}",
            f"- repairable_in_denominator_n: {pr.get('repairable_in_denominator_n')}",
            f"- repairable_fraction: {pr.get('repairable_fraction')}",
            f"- profitable_soft_exit_opportunity_cost_n: {pr.get('profitable_soft_exit_opportunity_cost_n')}",
            f"- top_mechanism: {pr.get('top_mechanism')} ({pr.get('top_mechanism_fraction')})",
            f"- Gate: {'PASS' if g.get('pass') else 'FAIL'}",
            "",
        ]
    lines += [
        f"- tests: {tests.get('passed')}/{tests.get('total')}",
        f"- A/B: {det.get('ab_match')}",
        f"- submit/cancel/live: 0/0/0",
        "",
    ]
    mp.write_text("\n".join(lines), encoding="utf-8")
    _write_xlsx(xp, sheets)
    shas = {
        "report.json": sha256_file(jp),
        "report.md": sha256_file(mp),
        "audit.xlsx": sha256_file(xp),
    }
    public["published_shas"] = shas
    jp.write_text(json.dumps(public, indent=2, default=str), encoding="utf-8")
    return shas
