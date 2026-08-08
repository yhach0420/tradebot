"""Publish E1_X8 artifacts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research.e1_x6_provisional.util import sha256_file

SHEETS = [
    "Index", "Precommit", "Identity", "QuantileContract", "FullThresholds",
    "SymbolProfiles", "FrozenMembership", "LOSOThresholds", "MembershipFlips",
    "RandomDeletion", "InfluenceRanking", "BridgeIdentity", "Ex285ASignal",
    "LOSOSignal", "RederivedSignal", "SymbolGroups", "EconomicReference",
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
    sheets_data = report.pop("_sheets", {})
    groups = report.get("symbol_groups") or {}
    econ = report.get("economic_reference") or {}

    sheets = {
        "Index": [
            {"item": "analysis_id", "value": report.get("analysis_id")},
            {"item": "run_id", "value": report.get("run_id")},
            {"item": "verdict", "value": report.get("verdict")},
        ],
        "Precommit": _kv(report.get("precommit") or {}),
        "Identity": _kv(report.get("identity") or {}),
        "QuantileContract": _kv(report.get("quantile_contract") or {}),
        "FullThresholds": _kv(report.get("full_thresholds") or {}),
        "SymbolProfiles": sheets_data.get("SymbolProfiles") or [],
        "FrozenMembership": sheets_data.get("FrozenMembership") or [],
        "LOSOThresholds": sheets_data.get("LOSOThresholds") or [],
        "MembershipFlips": sheets_data.get("MembershipFlips") or [],
        "RandomDeletion": sheets_data.get("RandomDeletion") or [],
        "InfluenceRanking": sheets_data.get("InfluenceRanking") or [],
        "BridgeIdentity": _kv((report.get("signal_full") or {}).get("bridge_reproduction") or {}),
        "Ex285ASignal": _kv(report.get("signal_ex_285A") or {}),
        "LOSOSignal": sheets_data.get("LOSOSignal") or [],
        "RederivedSignal": sheets_data.get("RederivedSignal") or [],
        "SymbolGroups": _kv({
            "UPDATE_HEAVY": groups.get("UPDATE_HEAVY"),
            "LOW_UPTICK_FLOW": groups.get("LOW_UPTICK_FLOW"),
            "PFQ_LIKE": groups.get("PFQ_LIKE"),
            "definition": groups.get("definition"),
        }),
        "EconomicReference": _kv(econ),
        "Verdict": _kv(report.get("verdict_detail") or {}),
        "Tests": tests.get("rows") or [{"test": "n/a", "outcome": "n/a"}],
        "Determinism": _kv(det),
        "Safety": _kv(report.get("safety") or {}),
        "ChangeLog": [
            {"change": "THRESHOLD_SYMBOL_LEVERAGE_AUDIT", "note": "post-PFQ descriptive; no revival"},
            {"change": "PFQ", "note": "CURRENT_LINE_CLOSED_REJECTED unchanged"},
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

    inf = (report.get("influence") or {}).get("285A") or {}
    ft = report.get("full_thresholds") or {}
    sf = report.get("signal_full") or {}
    se = report.get("signal_ex_285A") or {}
    ls = report.get("loso_signal_summary") or {}
    vd = report.get("verdict_detail") or {}
    lines = [
        f"# {report.get('document_id')} — {report.get('analysis_id')}",
        "",
        f"- run_id: `{report.get('run_id')}`",
        f"- **verdict: `{report.get('verdict')}`**",
        f"- PFQ revive: false",
        "",
        "## Thresholds",
        "",
        f"- full q70: {ft.get('update_q70_full')}",
        f"- full q30: {ft.get('flow_q30_full')}",
        f"- ex-285A q70: {inf.get('update_threshold_without')}",
        f"- ex-285A q30: {inf.get('flow_threshold_without')}",
        "",
        "## 285A influence",
        "",
        f"- update_rank: {inf.get('update_rank')} flow_rank: {inf.get('flow_rank')}",
        f"- size-matched update pct: {inf.get('size_matched_update_percentile')}",
        f"- size-matched flow pct: {inf.get('size_matched_flow_percentile')}",
        f"- max membership flip: {inf.get('max_flip_rate')}",
        f"- kioxia_threshold_leverage: {inf.get('kioxia_threshold_leverage')}",
        "",
        "## UPDATE signal",
        "",
        f"- full support: {sf.get('supported')}",
        f"- ex-285A support: {se.get('supported')}",
        f"- LOSO support preserved rate: {ls.get('support_preserved_rate')}",
        "",
        f"- UPDATE_HEAVY symbols: {(groups.get('UPDATE_HEAVY') or {}).get('n_symbols')}",
        f"- PFQ_LIKE symbols: {(groups.get('PFQ_LIKE') or {}).get('n_symbols')}",
        "",
        f"- next: {vd.get('next')}",
        f"- tests: {tests.get('passed')}/{tests.get('total')}",
        f"- A/B: {det.get('ab_match')}",
        f"- submit/cancel/live: 0/0/0",
        "",
    ]
    mp.write_text("\n".join(lines), encoding="utf-8")
    _write_xlsx(xp, sheets)
    shas = {"report.json": sha256_file(jp), "report.md": sha256_file(mp), "audit.xlsx": sha256_file(xp)}
    public["published_shas"] = shas
    jp.write_text(json.dumps(public, indent=2, default=str), encoding="utf-8")
    return shas
