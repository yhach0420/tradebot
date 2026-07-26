"""Emit report.md / report.json / audit.xlsx only."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


REQUIRED_SHEETS = [
    "README",
    "SOURCE_AUDIT",
    "FIELD_LINEAGE",
    "RAW_PAYLOAD_SAMPLES",
    "AGGREGATION_AUDIT",
    "QUOTE_VALIDITY",
    "CROSSED_QUOTES",
    "LOCKED_QUOTES",
    "TIMESTAMP_AUDIT",
    "ATOMIC_BOARD",
    "CONFIRMATION_FIXED",
    "ENTRY_EXECUTION",
    "EXIT_EXECUTION",
    "FILL_DELAY",
    "FILL_COVERAGE",
    "HISTORICAL_RECONSTRUCTION",
    "CAP5",
    "DAILY_RESULTS",
    "SYMBOL_DEPENDENCY",
    "DAY_DEPENDENCY",
    "PROSPECTIVE_CAPTURE_SPEC",
    "CAPTURE_QUALITY",
    "VERDICT",
]


def write_xlsx(path: Path, sheets: Mapping[str, Sequence[Mapping[str, Any]]]) -> None:
    from openpyxl import Workbook

    def _cell(v: Any) -> Any:
        if v is None or isinstance(v, (int, float, bool, str)):
            return v
        if isinstance(v, datetime):
            return v.isoformat()
        return json.dumps(v, ensure_ascii=False, default=str)

    wb = Workbook()
    wb.remove(wb.active)
    for name in REQUIRED_SHEETS:
        rows = list(sheets.get(name) or [{"status": "empty"}])
        ws = wb.create_sheet(str(name)[:31])
        keys: list[str] = []
        for r in rows:
            for k in r.keys():
                if k not in keys:
                    keys.append(k)
        if not keys:
            ws.append(["empty"])
            continue
        ws.append(keys)
        for r in rows:
            ws.append([_cell(r.get(k)) for k in keys])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def emit_artifacts(out_dir: Path, payload: Mapping[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for p in out_dir.iterdir():
        if p.is_file() and p.name not in ("report.md", "report.json", "audit.xlsx"):
            p.unlink()
    (out_dir / "report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (out_dir / "report.md").write_text(_md(payload), encoding="utf-8")
    write_xlsx(out_dir / "audit.xlsx", _sheets(payload))
    # enforce only 3 files
    for p in out_dir.iterdir():
        if p.is_file() and p.name not in ("report.md", "report.json", "audit.xlsx"):
            p.unlink()


def _md(p: Mapping[str, Any]) -> str:
    v = p.get("verdict") or {}
    lin = p.get("lineage") or {}
    gate = p.get("reconstruction_gate") or {}
    ev = p.get("evaluation") or {}
    e1x1 = (p.get("historical_pairs") or {}).get("E1_X1") or {}
    lines = [
        "# Execution-Grade Quote Reconstruction & Prospective Capture",
        "",
        f"- run_id: `{p.get('run_id')}`",
        f"- final: **{v.get('final')}**",
        f"- codes: {', '.join(v.get('codes') or [])}",
        "",
        v.get("summary") or "",
        "",
        "## Lineage",
        "",
        f"- raw atomic: {lin.get('raw_board_atomic')}",
        f"- field mapping: {lin.get('field_mapping')}",
        f"- crossed root: {lin.get('crossed_root_cause')}",
        f"- true book valid rate: {lin.get('true_book_valid_rate')}",
        f"- kabu named crossed rate: {lin.get('kabu_named_crossed_rate')}",
        "",
        "## Reconstruction gate",
        "",
        str(gate),
        "",
        "## Frozen confirmations / coverage",
        "",
        f"- strict: {ev.get('n_strict')}",
        f"- Ask E1 coverage: {ev.get('ask_coverage_E1')}",
        f"- Bid X1 coverage: {ev.get('bid_coverage_X1')}",
        "",
        "## E1_X1 (formal only if gate ready)",
        "",
        f"- n={e1x1.get('n_traded')} pnl={e1x1.get('total_pnl_5bps')} PF={e1x1.get('PF_5bps')} "
        f"cap5={(e1x1.get('cap5') or {}).get('pnl_5bps')} formal={e1x1.get('formal')}",
        "",
        f"- submit={p.get('submit')} cancel={p.get('cancel')} live_order={p.get('live_order')}",
        f"- mainline_unchanged={p.get('mainline_unchanged')}",
        "",
    ]
    return "\n".join(lines) + "\n"


def _sheets(p: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    lin = p.get("lineage") or {}
    cross = p.get("crossed_audit") or {}
    gate = p.get("reconstruction_gate") or {}
    ev = p.get("evaluation") or {}
    pairs = p.get("historical_pairs") or {}
    e1x1 = pairs.get("E1_X1") or {}
    dep = e1x1.get("dependency") or {}
    empty = [{"status": "empty"}]

    entry_cov = [{"scenario": k, **v} for k, v in (ev.get("entry_scenarios") or {}).items()]
    exit_cov = [{"scenario": k, **{kk: vv for kk, vv in v.items() if kk != "sample"}} for k, v in (ev.get("exit_scenarios") or {}).items()]
    # flatten samples out of entry for FILL sheets
    entry_samples = []
    for k, v in (ev.get("entry_scenarios") or {}).items():
        for s in (v.get("sample") or [])[:20]:
            entry_samples.append({"scenario": k, **{kk: vv for kk, vv in s.items() if kk != "sample"}})

    cap_rows = [{"pair": k, **(s.get("cap5") or {}), "formal": s.get("formal"), "n": s.get("n_traded"), "pnl": s.get("total_pnl_5bps"), "PF": s.get("PF_5bps")} for k, s in pairs.items()]
    daily = [{"day": d, "pnl": pnl} for d, pnl in (e1x1.get("day_pnl") or {}).items()] or empty
    sym_dep = [{"symbol": s, "pnl": pnl} for s, pnl in sorted((dep.get("symbol_pnl") or {}).items(), key=lambda kv: -kv[1])[:40]] or empty
    day_dep = [{"day": d, "pnl": pnl} for d, pnl in (dep.get("day_pnl") or {}).items()] or empty

    return {
        "README": [{"title": "Execution Grade Confirmation", "run_id": p.get("run_id"), "verdict": (p.get("verdict") or {}).get("final")}],
        "SOURCE_AUDIT": [
            {
                "sot_causal": p.get("sot_causal"),
                "sot_v3": p.get("sot_v3"),
                "sot_v2": p.get("sot_v2"),
                "capture_root": "data/market_capture",
                "push_cache": "results/research/volume_confirmed_impulse_entry/_push_cache",
            }
        ],
        "FIELD_LINEAGE": lin.get("field_lineage") or empty,
        "RAW_PAYLOAD_SAMPLES": lin.get("samples") or empty,
        "AGGREGATION_AUDIT": [lin.get("aggregation_audit") or {"status": "empty"}],
        "QUOTE_VALIDITY": [
            {
                "valid_rate": cross.get("valid_rate"),
                "total": cross.get("total_board_events"),
                "valid": cross.get("valid_board_events"),
                "true_crossed_rate": cross.get("crossed_rate_true_book"),
                "kabu_crossed_rate": cross.get("kabu_named_crossed_rate"),
            }
        ],
        "CROSSED_QUOTES": cross.get("examples") or empty,
        "LOCKED_QUOTES": [{"locked_board_events": cross.get("locked_board_events")}],
        "TIMESTAMP_AUDIT": [
            {
                "non_monotonic": cross.get("non_monotonic_timestamp"),
                "pass": gate.get("timestamp_monotonic_pass"),
            }
        ],
        "ATOMIC_BOARD": [
            {
                "raw_board_atomic": lin.get("raw_board_atomic"),
                "same_payload_rate": lin.get("same_payload_atomic_rate"),
                "true_book_valid_rate": lin.get("true_book_valid_rate"),
            }
        ],
        "CONFIRMATION_FIXED": p.get("confirmation_fixed_samples") or empty,
        "ENTRY_EXECUTION": entry_samples or entry_cov or empty,
        "EXIT_EXECUTION": (p.get("exit_fill_samples") or [])[:40] or empty,
        "FILL_DELAY": entry_cov or empty,
        "FILL_COVERAGE": [
            {
                "ask_E1": ev.get("ask_coverage_E1"),
                "bid_X1": ev.get("bid_coverage_X1"),
                "ask_filled_n": ev.get("ask_filled_n"),
                "bid_filled_n": ev.get("bid_filled_n"),
                "n_strict": ev.get("n_strict"),
            }
        ],
        "HISTORICAL_RECONSTRUCTION": [gate],
        "CAP5": cap_rows or empty,
        "DAILY_RESULTS": daily,
        "SYMBOL_DEPENDENCY": sym_dep,
        "DAY_DEPENDENCY": day_dep,
        "PROSPECTIVE_CAPTURE_SPEC": [p.get("prospective") or {"status": "empty"}],
        "CAPTURE_QUALITY": p.get("capture_quality") or empty,
        "VERDICT": [p.get("verdict") or {"final": "NO_PRODUCTION_CHANGE"}],
    }
