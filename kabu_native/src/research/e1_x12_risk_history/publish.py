"""Publish E1_X12 cumulative risk-history artifacts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research.e1_x6_provisional.util import sha256_file

SHEETS = [
    "Index", "DateRegistry", "CollectionPrecommits", "DailyManifest", "DailyQuality",
    "SymbolDayRisk", "HistoryCoverage", "AsOfRecurring", "PolicyEvaluableDays",
    "CapitalBaseStatus", "SpecialQuoteStatus", "Tests", "Determinism", "Safety", "ChangeLog",
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
            {"item": "run_id", "value": report.get("run_id")},
            {"item": "status", "value": report.get("status")},
            {"item": "registry_sha", "value": (report.get("date_registry") or {}).get("registry_sha256")},
            {"item": "valid_days", "value": (report.get("history_coverage") or {}).get("risk_history_days_valid")},
            {"item": "remaining", "value": report.get("days_remaining_to_20")},
            {"item": "newly_classified", "value": report.get("newly_classified_date")},
        ],
        "DateRegistry": sh.get("DateRegistry") or [],
        "CollectionPrecommits": sh.get("CollectionPrecommits") or [],
        "DailyManifest": sh.get("DailyManifest") or [],
        "DailyQuality": sh.get("DailyQuality") or [],
        "SymbolDayRisk": sh.get("SymbolDayRisk") or [],
        "HistoryCoverage": sh.get("HistoryCoverage") or [],
        "AsOfRecurring": sh.get("AsOfRecurring") or [],
        "PolicyEvaluableDays": sh.get("PolicyEvaluableDays") or [],
        "CapitalBaseStatus": sh.get("CapitalBaseStatus") or [],
        "SpecialQuoteStatus": sh.get("SpecialQuoteStatus") or [],
        "Tests": tests.get("rows") or [{"test": "n/a", "outcome": "n/a"}],
        "Determinism": _kv(det),
        "Safety": _kv(report.get("safety") or {}),
        "ChangeLog": [
            {"change": "E1_X12_RISK_INFRASTRUCTURE_COLLECTION", "note": "accumulating; no policy freeze"},
            {"change": "20260803", "note": "ALPHA_PROSPECTIVE_RESERVED preserved"},
            {"change": "20260721", "note": "REFERENCE_PRICE_BOOTSTRAP_DAY explicit"},
        ],
    }
    public = {k: v for k, v in report.items() if not k.startswith("_")}
    # shrink bulky date_registry duplication in json — keep sha + summary
    if "date_registry" in public and isinstance(public["date_registry"], dict):
        public["date_registry_summary"] = {
            "registry_sha256": public["date_registry"].get("registry_sha256"),
            "n": public["date_registry"].get("n"),
            "status_counts": {},
        }
        from collections import Counter
        public["date_registry_summary"]["status_counts"] = dict(
            Counter(r["status"] for r in public["date_registry"].get("rows") or [])
        )
    public["tests"] = {
        "exit_code": tests.get("exit_code"),
        "passed": tests.get("passed"),
        "failed": tests.get("failed"),
        "total": tests.get("total"),
    }
    public["determinism"] = det

    jp, mp, xp = out_dir / "report.json", out_dir / "report.md", out_dir / "audit.xlsx"
    jp.write_text(json.dumps(public, indent=2, default=str), encoding="utf-8")
    hc = report.get("history_coverage") or {}
    md = [
        f"# {report.get('document_id')}",
        "",
        f"- run_id: `{report.get('run_id')}`",
        f"- **status: `{report.get('status')}`**",
        f"- registry_sha: `{(report.get('date_registry') or {}).get('registry_sha256')}`",
        f"- newly classified: `{report.get('newly_classified_date')}`",
        f"- valid history days: {hc.get('risk_history_days_valid')} / 20 (remaining {report.get('days_remaining_to_20')})",
        f"- alpha-reserved untouched: {[r.get('date') for r in (report.get('alpha_reserved_untouched') or [])]}",
        f"- unclassified (do not open): {[r.get('date') for r in (report.get('unclassified_do_not_open') or [])]}",
        f"- panel reconciliation: {(report.get('panel_day_reconciliation') or {}).get('reconciliation_pass')}",
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
