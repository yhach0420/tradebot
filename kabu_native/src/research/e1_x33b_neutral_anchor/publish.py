"""Publish X33B artifacts + optional freeze manifests."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from openpyxl import Workbook

from . import ANCHOR_ID


def freeze_anchor_manifest(
    *,
    semantics: dict[str, Any],
    dependency: dict[str, Any],
    prefix: dict[str, Any],
) -> dict[str, Any]:
    body = {
        "manifest_id": "NEUTRAL_FIXED_CLOCK_ANCHOR_V1",
        "anchor_id": ANCHOR_ID,
        "role": "research_observation_anchor",
        "no_runtime_entry_reflect": True,
        "clock_grid": semantics.get("clock_grid_definition"),
        "source_functions": semantics.get("source_functions"),
        "symbol_pool": "CANDIDATE_SYMBOL_POOL (X30/X33 population symbols by day)",
        "future_dependency": False,
        "prefix_invariance": prefix.get("status"),
        "dependency_manifest_sha256": dependency.get("sha256"),
        "execution_contract": "actual ask Sell1 → bid Buy1 horizons (X28)",
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
        f"- X33 canonical: `{report.get('x33_identity', {}).get('canonical_run_id')}`",
        f"- next: `{report.get('next_phase')}`",
        f"- manifest: `{report.get('neutral_manifest_created')}` SHA `{report.get('manifest_sha')}`",
        "",
        "## Neutrality",
        f"- matched Δ300/600: `{report.get('matched')}`",
        f"- coverage: `{report.get('coverage', {}).get('coverage_share')}`",
        f"- prefix: `{report.get('prefix_invariance', {}).get('status')}`",
        "",
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
