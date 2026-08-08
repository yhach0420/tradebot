"""Publish X33 + optional CAUSAL_ANCHOR_MANIFEST_V1."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from openpyxl import Workbook

from . import ANCHOR_ID, CLUSTER_WINDOW_SEC


def freeze_manifest(
    *,
    dependency: dict[str, Any],
    prefix: dict[str, Any],
) -> dict[str, Any]:
    body = {
        "manifest_id": "CAUSAL_ANCHOR_MANIFEST_V1",
        "anchor_id": ANCHOR_ID,
        "source_functions": dependency.get("source_functions"),
        "feature_eligibility_semantics": "feature_status == OK (quality_status OK + CurrentPrice; path features only)",
        "cluster_window_sec": CLUSTER_WINDOW_SEC,
        "cluster_first_semantics": "CLUSTER_FIRST_ANCHOR",
        "future_dependency": False,
        "prefix_invariance_result": prefix.get("status"),
        "dependency_manifest_sha256": dependency.get("sha256"),
        "no_runtime_entry_reflect": True,
    }
    raw = json.dumps(body, sort_keys=True, default=str).encode()
    body["sha256"] = hashlib.sha256(raw).hexdigest()
    return body


def write_md(path: Path, report: dict[str, Any]) -> None:
    lines = [
        f"# {report.get('analysis_id')}",
        "",
        f"- run_id: `{report.get('run_id')}`",
        f"- verdict: `{report.get('verdict')}`",
        f"- next phase: `{report.get('next_phase')}`",
        f"- manifest: `{report.get('causal_anchor_manifest_created')}` SHA `{report.get('manifest_sha')}`",
        "",
        "## Arms",
        f"- OLD: `{report.get('old_summary')}`",
        f"- PARENT: `{report.get('parent_summary')}`",
        f"- CAUSAL: `{report.get('causal_summary')}`",
        f"- CONTROL: `{report.get('control_summary')}`",
        "",
        "## Causal vs parent",
        f"- raw/matched: `{report.get('causal_parent')}`",
        f"- causal-old: `{report.get('causal_old')}`",
        "",
        f"- prefix invariance: `{report.get('prefix_invariance')}`",
        f"- 20260810 opened: `{report.get('opened_20260810')}`",
        f"- tests: `{report.get('tests')}`",
        f"- A/B: `{report.get('ab_determinism')}`",
        f"- submit/cancel/live: `{report.get('safety', {}).get('submit_cancel_live')}`",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_xlsx(path: Path, sheets: dict[str, list[dict[str, Any]]]) -> None:
    wb = Workbook()
    first = True
    for name, rows in sheets.items():
        ws = wb.active if first else wb.create_sheet(name[:31])
        if first:
            ws.title = name[:31]
            first = False
        if not rows:
            ws.append(["empty"])
            continue
        hdr = list(rows[0].keys())
        ws.append(hdr)
        for r in rows:
            ws.append([
                json.dumps(r.get(h), default=str)[:32000]
                if isinstance(r.get(h), (dict, list)) else r.get(h)
                for h in hdr
            ])
    wb.save(path)


def publish(out: Path, report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    write_md(out / "report.md", report)
    write_xlsx(out / "audit.xlsx", sheets)
