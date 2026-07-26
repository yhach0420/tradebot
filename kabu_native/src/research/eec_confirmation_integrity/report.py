"""Emit report.md / report.json / audit.xlsx only."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


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
    for name, rows in sheets.items():
        ws = wb.create_sheet(str(name)[:31])
        if not rows:
            ws.append(["empty"])
            continue
        keys: list[str] = []
        for r in rows:
            for k in r.keys():
                if k not in keys:
                    keys.append(k)
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


def _md(p: Mapping[str, Any]) -> str:
    v = p.get("verdict") or {}
    exp = p.get("expiry") or {}
    parity = p.get("parity") or {}
    cohorts = p.get("cohorts") or {}
    dep = p.get("dependency_detail") or {}
    lines = [
        "# EEC Confirmation Causal Integrity",
        "",
        f"- run_id: `{p.get('run_id')}`",
        f"- SoT v3: `{p.get('sot_v3')}`",
        f"- SoT v2: `{p.get('sot_v2')}`",
        f"- final: **{v.get('final')}**",
        f"- codes: {', '.join(v.get('codes') or [])}",
        "",
        v.get("summary") or "",
        "",
        "## Constraints",
        "",
        "- EC2 ENTRY/EXIT/noise unchanged; offline only; submit=cancel=live_order=0",
        f"- frozen noise: `{p.get('frozen_noise')}`",
        "",
        "## Episode expiry / causal filter",
        "",
        str(exp),
        "",
        "## Economic success parity",
        "",
        f"- v2 rate: {parity.get('v2_economic_rate')} / v3 rate: {parity.get('v3_economic_rate')}",
        f"- disagree: {parity.get('disagree_n')} unexplained: {parity.get('unexplained_n')}",
        f"- verdict: {parity.get('verdict')}",
        f"- reasons: {parity.get('by_reason')}",
        "",
        "## Cohorts C0–C4",
        "",
    ]
    for k in ("C0", "C1", "C2", "C3", "C4"):
        s = cohorts.get(k) or {}
        lines.append(
            f"- {k}: n={s.get('n_traded')} pnl={s.get('total_pnl_5bps')} PF={s.get('PF_5bps')} "
            f"cap5={ (s.get('cap5') or {}).get('pnl_5bps') } "
            f"R1={ ((s.get('reality') or {}).get('R1') or {}).get('PF_5bps') } "
            f"dep_blocked={s.get('dependency_blocked')}"
        )
    lines += [
        "",
        "## Ask ENTRY scenarios",
        "",
    ]
    for k, s in (p.get("execution_scenarios") or {}).items():
        lines.append(
            f"- {k}: n={s.get('n_traded')} pnl={s.get('total_pnl_5bps')} PF={s.get('PF_5bps')} "
            f"cap5={ (s.get('cap5') or {}).get('pnl_5bps') } pos/neg={s.get('pos_days')}/{s.get('neg_days')}"
        )
    lines += [
        "",
        "## Dependency (strict C2)",
        "",
        f"- top1 symbol share: {dep.get('top1_symbol_pnl_share')} ({dep.get('top1_symbol')})",
        f"- top3 symbol share: {dep.get('top3_symbol_pnl_share')}",
        f"- top1 day share: {dep.get('top1_day_pnl_share')} ({dep.get('top1_day')})",
        f"- LOO max-symbol PF: {dep.get('pf_after_exclude_max_symbol')} pnl={dep.get('pnl_after_exclude_max_symbol')}",
        f"- LOO max-day PF: {dep.get('pf_after_exclude_max_day')} pnl={dep.get('pnl_after_exclude_max_day')}",
        f"- blocked: {dep.get('dependency_blocked')} reasons={dep.get('block_reasons')}",
        "",
        f"- submit={p.get('submit')} cancel={p.get('cancel')} live_order={p.get('live_order')}",
        "",
    ]
    return "\n".join(lines) + "\n"


def _sheets(p: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    empty = [{"status": "empty"}]
    cohorts = p.get("cohorts") or {}
    cohort_rows = []
    for k, s in cohorts.items():
        cohort_rows.append(
            {
                "cohort": k,
                "n_traded": s.get("n_traded"),
                "pnl": s.get("total_pnl_5bps"),
                "PF": s.get("PF_5bps"),
                "cap5_pnl": (s.get("cap5") or {}).get("pnl_5bps"),
                "R1_PF": ((s.get("reality") or {}).get("R1") or {}).get("PF_5bps"),
                "R3_PF": ((s.get("reality") or {}).get("R3") or {}).get("PF_5bps"),
                "pos_days": s.get("pos_days"),
                "neg_days": s.get("neg_days"),
                "dependency_blocked": s.get("dependency_blocked"),
            }
        )
    bucket_rows = [{"bucket": k, **v} for k, v in (p.get("delay_buckets") or {}).items()]
    exec_rows = [{"scenario": k, **{kk: vv for kk, vv in s.items() if kk not in ("sample_rows", "reality", "dependency", "cap5")}, "cap5_pnl": (s.get("cap5") or {}).get("pnl_5bps")} for k, s in (p.get("execution_scenarios") or {}).items()]
    return {
        "README": [{"title": "EEC Confirmation Causal Integrity", "run_id": p.get("run_id"), "verdict": (p.get("verdict") or {}).get("final")}],
        "EXPIRY": [p.get("expiry") or {"status": "empty"}],
        "CAUSAL_SAMPLES": p.get("causal_audit_samples") or empty,
        "PARITY": [p.get("parity") or {"status": "empty"}],
        "PARITY_DISAGREE": p.get("parity_disagree_samples") or empty,
        "DELAY_BUCKETS": bucket_rows or empty,
        "COHORTS": cohort_rows or empty,
        "C0_SAMPLES": (cohorts.get("C0") or {}).get("sample_rows") or empty,
        "C2_SAMPLES": (cohorts.get("C2") or {}).get("sample_rows") or empty,
        "EXEC_SCENARIOS": exec_rows or empty,
        "DEPENDENCY": [p.get("dependency_detail") or {"status": "empty"}],
        "DEP_SYMBOLS": (p.get("dependency_detail") or {}).get("symbol_table") or empty,
        "VERDICT": [p.get("verdict") or {"final": "EEC_CONFIRMATION_OFFLINE_ONLY"}],
    }
