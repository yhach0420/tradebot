"""Publish X33C artifacts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openpyxl import Workbook


def write_md(path: Path, report: dict[str, Any]) -> None:
    lat = report.get("latency") or {}
    by = lat.get("by_delay") or {}
    lines = [
        f"# {report.get('analysis_id')}",
        "",
        f"- run_id: `{report.get('run_id')}`",
        f"- verdict: `{report.get('verdict')}`",
        f"- latency_tag: `{report.get('latency_tag')}`",
        f"- anchor_id: `{report.get('anchor_id')}`",
        f"- manifest SHA: `{report.get('manifest_sha')}`",
        f"- source_run: `{report.get('source_x33b_run')}`",
        "",
        "## Episode mean",
        f"- mid300/600: `{report.get('episode_mean', {}).get('mid300')}` / `{report.get('episode_mean', {}).get('mid600')}`",
        f"- exec300/600: `{report.get('episode_mean', {}).get('exec300')}` / `{report.get('episode_mean', {}).get('exec600')}`",
        "",
        "## Symbol-session balanced",
        f"- mid300/600: `{report.get('symbol_session_balanced', {}).get('mid300')}` / `{report.get('symbol_session_balanced', {}).get('mid600')}`",
        f"- exec300/600: `{report.get('symbol_session_balanced', {}).get('exec300')}` / `{report.get('symbol_session_balanced', {}).get('exec600')}`",
        "",
        "## Spreads / drag",
        f"- entry_spread_bps: `{report.get('entry_spread_bps')}`",
        f"- exit_half_spread: `{report.get('exit_half_spread_bps')}`",
        f"- execution_drag 300/600: `{report.get('execution_drag')}`",
        "",
        "## Latency (entry drag mean)",
    ]
    for d in ("0.25", "0.5", "1.0", "2.0", "5.0"):
        block = by.get(d)
        if not block:
            lines.append(f"- 0→{d}s: insufficient / not run")
            continue
        e300 = (block.get("entry_latency_drag_300") or {}).get("mean")
        e600 = (block.get("entry_latency_drag_600") or {}).get("mean")
        lines.append(f"- 0→{d}s entry drag 300/600: `{e300}` / `{e600}`")
    lines += [
        "",
        "## Q1–Q5",
        f"- Q1: {report.get('answers', {}).get('Q1')}",
        f"- Q2: {report.get('answers', {}).get('Q2')}",
        f"- Q3: {report.get('answers', {}).get('Q3')}",
        f"- Q4: {report.get('answers', {}).get('Q4')}",
        f"- Q5: {report.get('answers', {}).get('Q5')}",
        "",
        f"- X34 implications: `{report.get('recommended_x34_implications')}`",
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
