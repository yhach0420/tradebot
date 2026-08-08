"""Publish E1_X11 Policy Gate V2 artifacts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research.e1_x6_provisional.util import sha256_file

SHEETS = [
    "Index", "Precommit", "SourceIdentity", "SupersededGate", "SymbolDayEvaluability",
    "PolicyEvaluableDays", "AsOfRecurring", "Coverage", "BreadthGate", "CapitalScenarios",
    "CapitalBase", "RequiredCapital", "KioxiaDaily", "KioxiaSummary", "DynamicCore",
    "SpecialQuote", "BlockerMatrix", "Verdict", "Tests", "Determinism", "Safety", "ChangeLog",
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
    # fill ab into blocker matrix copy
    bm = dict(report.get("blocker_matrix") or {})
    bm["ab_determinism_pass"] = det.get("ab_match")
    report["blocker_matrix"] = bm

    sheets = {
        "Index": [
            {"item": "run_id", "value": report.get("run_id")},
            {"item": "verdict", "value": report.get("verdict")},
            {"item": "all_blockers", "value": json.dumps(report.get("all_blockers"))},
        ],
        "Precommit": _kv(report.get("precommit") or {}),
        "SourceIdentity": sh.get("SourceIdentity") or [],
        "SupersededGate": sh.get("SupersededGate") or [],
        "SymbolDayEvaluability": sh.get("SymbolDayEvaluability") or [],
        "PolicyEvaluableDays": sh.get("PolicyEvaluableDays") or [],
        "AsOfRecurring": sh.get("AsOfRecurring") or [],
        "Coverage": sh.get("Coverage") or [],
        "BreadthGate": sh.get("BreadthGate") or [],
        "CapitalScenarios": sh.get("CapitalScenarios") or [],
        "CapitalBase": sh.get("CapitalBase") or [],
        "RequiredCapital": sh.get("RequiredCapital") or [],
        "KioxiaDaily": sh.get("KioxiaDaily") or [],
        "KioxiaSummary": sh.get("KioxiaSummary") or [],
        "DynamicCore": sh.get("DynamicCore") or [],
        "SpecialQuote": sh.get("SpecialQuote") or [],
        "BlockerMatrix": [bm],
        "Verdict": _kv(report.get("verdict_detail") or {}),
        "Tests": tests.get("rows") or [{"test": "n/a", "outcome": "n/a"}],
        "Determinism": _kv(det),
        "Safety": _kv(report.get("safety") or {}),
        "ChangeLog": [
            {"change": "E1_X11_POLICY_GATE_RECONCILIATION_V2", "note": "gate contract only; source not overwritten"},
            {"change": "breadth", "note": "SUPERSEDED_IMPOSSIBLE_ABSOLUTE_BREADTH_GATE → 2×cap / cap"},
            {"change": "warmup", "note": "excluded from daily min eligible"},
        ],
    }
    public = {k: v for k, v in report.items() if not k.startswith("_")}
    public["tests"] = {"exit_code": tests.get("exit_code"), "passed": tests.get("passed"),
                       "failed": tests.get("failed"), "total": tests.get("total")}
    public["determinism"] = det

    jp, mp, xp = out_dir / "report.json", out_dir / "report.md", out_dir / "audit.xlsx"
    jp.write_text(json.dumps(public, indent=2, default=str), encoding="utf-8")
    kx = report.get("kioxia_summary") or {}
    md = [
        f"# {report.get('document_id')}",
        "",
        f"- run_id: `{report.get('run_id')}`",
        f"- **primary verdict: `{report.get('verdict')}`**",
        f"- all_blockers: {report.get('all_blockers')}",
        f"- policy_evaluable_days: {report.get('n_policy_evaluable_days')} · warmup: {report.get('warmup_days')}",
        f"- coverage: {(report.get('coverage') or {}).get('asof_recurring_evaluable_fraction')}",
        f"- 285A notional median/max: {kx.get('one_lot_notional_median')} / {kx.get('one_lot_notional_max')}",
        f"- 285A required capital median/max: {kx.get('required_capital_median')} / {kx.get('required_capital_max')}",
        f"- next: {(report.get('verdict_detail') or {}).get('next')}",
        f"- tests: {tests.get('passed')}/{tests.get('total')} · A/B: {det.get('ab_match')}",
        "- submit/cancel/live: 0/0/0",
        "",
    ]
    mp.write_text("\n".join(md), encoding="utf-8")
    _write_xlsx(xp, sheets)
    shas = {"report.json": sha256_file(jp), "report.md": sha256_file(mp), "audit.xlsx": sha256_file(xp)}
    public["published_shas"] = shas
    jp.write_text(json.dumps(public, indent=2, default=str), encoding="utf-8")
    return shas
