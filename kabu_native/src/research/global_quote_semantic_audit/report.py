"""Emit report.md / report.json / audit.xlsx only."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from research.global_quote_semantic_audit.constants import REQUIRED_ARTIFACTS, REQUIRED_SHEETS


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


def _as_rows(obj: Any) -> list[dict[str, Any]]:
    if obj is None:
        return [{"status": "empty"}]
    if isinstance(obj, list):
        if not obj:
            return [{"status": "empty"}]
        if isinstance(obj[0], dict):
            return obj
        return [{"value": v} for v in obj]
    if isinstance(obj, dict):
        return [{"key": k, "value": json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, (dict, list)) else v} for k, v in obj.items()]
    return [{"value": str(obj)}]


def _sheets(payload: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    st = payload.get("static") or {}
    imp = payload.get("impact") or {}
    lin = payload.get("lineage") or {}
    diff = payload.get("r0_r1") or {}
    tests = payload.get("tests") or {}
    verdict = payload.get("verdict") or {}
    return {
        "README": [
            {
                "phase": "Global Quote Semantic Integrity Audit",
                "run_id": payload.get("run_id"),
                "sot_egc": payload.get("egc_sot"),
                "constraint": "audit-only; no mainline wire; submit=cancel=live_order=0",
                "artifacts": "report.md / report.json / audit.xlsx",
            }
        ],
        "SEARCH_INVENTORY": _as_rows(st.get("search_inventory")),
        "FIELD_SEMANTICS": _as_rows(st.get("field_semantics")),
        "STATIC_REFERENCES": _as_rows(st.get("static_references")),
        "RUNTIME_LINEAGE": _as_rows(lin.get("runtime")),
        "PAPER_LINEAGE": _as_rows(lin.get("paper")),
        "RESEARCH_LINEAGE": _as_rows(lin.get("research")),
        "PBV2_IMPACT": _as_rows(imp.get("pbv2")),
        "GUARD_IMPACT": _as_rows(imp.get("guard")),
        "EXIT_IMPACT": _as_rows(imp.get("exit")),
        "EXECUTION_IMPACT": _as_rows(imp.get("execution")),
        "RESEARCH_IMPACT": _as_rows(imp.get("research")),
        "R0_CURRENT": _as_rows(diff.get("r0_samples")),
        "R1_CANONICAL": _as_rows(diff.get("r1_samples")),
        "DECISION_DIFF": _as_rows(diff.get("decision_diff")),
        "ENTRY_DIFF": _as_rows([diff.get("entry_diff") or {}]),
        "EXIT_DIFF": _as_rows([diff.get("exit_diff") or {}]),
        "PNL_DIFF": _as_rows([diff.get("pnl_diff") or {}]),
        "INVALIDATED_STUDIES": [
            r for r in (imp.get("research") or [])
            if str(r.get("classification", "")).startswith("INVALID")
            or r.get("action") in ("ENDED", "CLOSED", "DO_NOT_RERUN_AS_IS")
        ] or [{"status": "none"}],
        "REPLAY_PRIORITY": sorted(
            [r for r in (imp.get("research") or []) if int(r.get("replay_priority", 99)) < 90],
            key=lambda x: int(x.get("replay_priority", 99)),
        ) or [{"status": "none"}],
        "CANONICAL_SPEC": _as_rows(payload.get("canonical_spec")),
        "TESTS": _as_rows(tests.get("rows") or [{"status": "empty"}]),
        "VERDICT": _as_rows([verdict]),
    }


def _md(payload: Mapping[str, Any]) -> str:
    v = payload.get("verdict") or {}
    s = (payload.get("static") or {}).get("summary") or {}
    d = payload.get("r0_r1") or {}
    ed = d.get("entry_diff") or {}
    xd = d.get("exit_diff") or {}
    lines = [
        "# Global Quote Semantic Integrity Audit",
        "",
        f"- run_id: `{payload.get('run_id')}`",
        f"- final_verdict: **{v.get('final_verdict')}**",
        f"- mainline_fix_required: `{v.get('mainline_fix_required')}`",
        f"- canonical_normalizer_implemented: `{v.get('canonical_normalizer_implemented')}` (research-only, not wired)",
        "",
        "## Kabu field meanings",
        "",
        "- BidPrice = Sell1 = true best ask",
        "- AskPrice = Buy1 = true best bid",
        "- BidQty = Sell1Qty = true ask qty",
        "- AskQty = Buy1Qty = true bid qty",
        "",
        "## Canonical",
        "",
        "- canonical_best_bid = Buy1.Price",
        "- canonical_best_ask = Sell1.Price",
        "",
        "## Reference counts (curated)",
        "",
        f"- runtime_reachable: {s.get('runtime_reachable')}",
        f"- correct (A+B): {s.get('correct_refs')}",
        f"- inverted sites (C|D): {s.get('inverted_site_count')}",
        f"- unknown (E): {s.get('unknown_refs')}",
        f"- runtime C/D: {s.get('runtime_reachable_cd')}",
        "",
        "## R0 vs R1 (proxy)",
        "",
        f"- n_events: {d.get('n_total')}",
        f"- mapping_ok_rate: {d.get('mapping_ok_rate')}",
        f"- token_flip_rate: {d.get('token_flip_rate')}",
        f"- gate_flip_rate: {d.get('gate_flip_rate')}",
        f"- entry only_r0 / only_r1 / both: {ed.get('only_r0_would_pass_board_gate')} / {ed.get('only_r1_would_pass_board_gate')} / {ed.get('both_pass')}",
        f"- exit top_imb invert rate: {xd.get('top_imbalance_exact_invert_rate')}",
        "",
        "## Safety counters",
        "",
        f"- submit={payload.get('submit')} cancel={payload.get('cancel')} live_order={payload.get('live_order')}",
        f"- mainline_changed={payload.get('mainline_changed')}",
        "",
    ]
    return "\n".join(lines)


def emit_artifacts(out_dir: Path, payload: Mapping[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for p in list(out_dir.iterdir()):
        if p.is_file() and p.name not in REQUIRED_ARTIFACTS:
            p.unlink()
    (out_dir / "report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    (out_dir / "report.md").write_text(_md(payload), encoding="utf-8")
    write_xlsx(out_dir / "audit.xlsx", _sheets(payload))
    for p in list(out_dir.iterdir()):
        if p.is_file() and p.name not in REQUIRED_ARTIFACTS:
            p.unlink()
