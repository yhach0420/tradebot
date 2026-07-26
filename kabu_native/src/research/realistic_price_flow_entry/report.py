"""Emit RPFE report.md / report.json / audit.xlsx."""
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
    (out_dir / "report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    (out_dir / "report.md").write_text(_md(payload), encoding="utf-8")
    write_xlsx(out_dir / "audit.xlsx", _sheets(payload))


def _md(p: Mapping[str, Any]) -> str:
    v = p.get("verdict") or {}
    ev = p.get("evaluation") or {}
    methods = ev.get("methods") or {}
    m0 = (methods.get("R0_PBv2") or {}).get("oos") or {}
    m5 = (methods.get("R5_A_OR_B_PRICE") or {}).get("oos") or {}
    dyn = ev.get("dynamic_coverage") or {}
    pc = ev.get("pattern_counts") or {}
    lines = [
        "# Realistic Price-Flow Entry (RPFE)",
        "",
        f"- run_id: `{p.get('run_id')}`",
        f"- sot: `{p.get('sot_run')}`",
        f"- final_verdict: **{v.get('final')}**",
        f"- codes: {', '.join(v.get('codes') or [])}",
        "",
        "## 結論",
        "",
        v.get("summary") or "",
        "",
        "## Integrity checklist",
        "",
        str(v.get("checklist")),
        "",
        f"- state_machine: {(ev.get('state_machine_integrity') or {}).get('verdict')}",
        f"- price_cross: {(ev.get('price_cross_integrity') or {}).get('verdict')}",
        f"- early_stop: {(ev.get('early_stop_label_audit') or {}).get('verdict')}",
        f"- day_matched: {(ev.get('matched_comparison') or {}).get('verdict')}",
        f"- integrity_pass: {v.get('integrity_pass')}",
        "",
        "## Pattern A — Pullback Reclaim（真時間）",
        "",
        "1観測最大1段階。PRICE_TRIGGERED+ENTRYは実micro-highクロス時のみ同一観測可。",
        "",
        "## Pattern B — Compression Breakout（真時間）",
        "",
        "持ち合い30–120秒必須。PRICEはrange-high実クロス＋2tick/5秒保持。",
        "",
        "## Trigger counts",
        "",
        f"- A PRICE: {pc.get('A_PRICE')}  A FLOW: {pc.get('A_FLOW')}",
        f"- B PRICE: {pc.get('B_PRICE')}  B FLOW: {pc.get('B_FLOW')}",
        "",
        "## PBv2 vs R5 (PRICE OR)",
        "",
        f"- PBv2 pnl_5bps: {m0.get('total_pnl_5bps')} PF_5bps: {m0.get('PF_5bps')} n: {m0.get('n')}",
        f"- R5 pnl_5bps: {m5.get('total_pnl_5bps')} PF_5bps: {m5.get('PF_5bps')} n: {m5.get('n')}",
        f"- STOP PBv2/R5: {m0.get('stop_rate')} / {m5.get('stop_rate')}",
        f"- early_STOP PBv2/R5: {m0.get('early_stop_rate')} / {m5.get('early_stop_rate')}",
        f"- NP PBv2/R5: {m0.get('np_rate')} / {m5.get('np_rate')}",
        "",
        "## Dynamic coverage",
        "",
        f"- complete_rows: {dyn.get('complete_rows_total')}",
        f"- oos_days: {dyn.get('n_oos_days')}",
        f"- verdict: {dyn.get('verdict')}",
        "",
        "## CAP=5",
        "",
    ]
    for mid in ("R0_PBv2", "R5_A_OR_B_PRICE", "R7_PBv2_OR_RPFE", "R8_PBv2_AND_RPFE"):
        cap = (methods.get(mid) or {}).get("cap5") or {}
        lines.append(
            f"- {mid}: trades={cap.get('accepted_trades')} pnl5={cap.get('pnl_5bps')} PF={cap.get('pf')}"
        )
    lines += [
        "",
        "## 採用しない理由 / 次データ",
        "",
        v.get("no_production_reason") or "",
        "",
        v.get("next_data_need") or "",
        "",
        "## Safety",
        "",
        f"- submit={p.get('submit')} cancel={p.get('cancel')} live_order={p.get('live_order')}",
        f"- mainline_unchanged={p.get('mainline_unchanged')}",
        "",
    ]
    return "\n".join(lines) + "\n"


def _sheets(p: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    ev = p.get("evaluation") or {}
    methods = ev.get("methods") or {}
    sheets: dict[str, list[dict[str, Any]]] = {
        "README": [{"title": "RPFE", "run_id": p.get("run_id"), "verdict": (p.get("verdict") or {}).get("final")}],
        "DATA_SOURCES": p.get("data_sources") or [{"source": "none"}],
        "FEATURE_LINEAGE": p.get("feature_lineage") or [{"feature": "none"}],
        "COVERAGE_BY_DAY": [{"day": k, **v} for k, v in sorted((p.get("coverage_by_day") or {}).items())] or [{"day": "none"}],
        "STATE_DEFINITIONS": p.get("state_definitions") or [{"state": "none"}],
        "STATE_TRANSITIONS": p.get("state_transitions") or [{"note": "see TRIGGER_EVENTS history"}],
        "INVALIDATIONS": p.get("invalidations") or [{"reason": "see state_machine.invalid_reason"}],
        "PULLBACK_RECLAIM": p.get("pullback_reclaim") or [{"pattern": "A"}],
        "COMPRESSION_BREAKOUT": p.get("compression_breakout") or [{"pattern": "B"}],
        "PRICE_ONLY_RESULTS": _method_rows(methods, ("R1_Pullback_PRICE", "R3_Compression_PRICE", "R5_A_OR_B_PRICE")),
        "FLOW_CONFIRM_RESULTS": _method_rows(methods, ("R2_Pullback_FLOW", "R4_Compression_FLOW", "R6_A_OR_B_FLOW")),
        "DYNAMIC_COVERAGE": [ev.get("dynamic_coverage") or {"status": "empty"}],
        "WALK_FORWARD_FOLDS": [{"oos_days": ",".join(ev.get("oos_days") or []), "folds_n": ev.get("folds_n")}],
        "THRESHOLD_HISTORY": (ev.get("threshold_history") or [])[:2000] or [{"status": "empty"}],
        "TRIGGER_EVENTS": (ev.get("triggers") or [])[:5000] or [{"status": "empty"}],
        "PBV2_COMPARISON": _method_rows(methods, tuple(methods.keys())),
        "MATCHED_COMPARISON": _flatten_matched(ev.get("matched_comparison")),
        "STATE_MACHINE_AUDIT": [ev.get("state_machine_integrity") or {"status": "empty"}],
        "PRICE_CROSS_AUDIT": [ev.get("price_cross_integrity") or {"status": "empty"}],
        "CAP5_RESULTS": [{"method": mid, **(m.get("cap5") or {})} for mid, m in methods.items()] or [{"status": "empty"}],
        "LARGE_RISE_CAPTURE": [
            {"method": mid, "large_rise_capture": (m.get("oos") or {}).get("large_rise_capture")}
            for mid, m in methods.items()
        ]
        or [{"status": "empty"}],
        "MISSED_RISES": p.get("missed_rises") or [{"status": "see large_rise from SoT panel"}],
        "EARLY_STOP_ANALYSIS": [
            {
                "method": mid,
                "early_stop_rate": (m.get("oos") or {}).get("early_stop_rate"),
                "stop_rate": (m.get("oos") or {}).get("stop_rate"),
                **((m.get("oos") or {}).get("stop_hold_buckets") or {}),
            }
            for mid, m in methods.items()
        ]
        or [{"status": "empty"}],
        "EARLY_STOP_LABEL_AUDIT": [ev.get("early_stop_label_audit") or {"status": "empty"}],
        "NOPROGRESS_ANALYSIS": [
            {"method": mid, "np_rate": (m.get("oos") or {}).get("np_rate")} for mid, m in methods.items()
        ]
        or [{"status": "empty"}],
        "MFE_MAE": [{"method": mid, **(m.get("mfe_mae") or {})} for mid, m in methods.items()] or [{"status": "empty"}],
        "DAILY_RESULTS": _daily_rows(methods),
        "SYMBOL_DEPENDENCY": p.get("symbol_dependency") or [{"status": "empty"}],
        "VERDICT": [p.get("verdict") or {"final": "RPFE_OFFLINE_ONLY"}],
    }
    return sheets


def _method_rows(methods: Mapping[str, Any], keys: Sequence[str]) -> list[dict[str, Any]]:
    out = []
    for mid in keys:
        m = methods.get(mid) or {}
        o = m.get("oos") or {}
        out.append({"method": mid, **{k: o.get(k) for k in ("n", "total_pnl_5bps", "PF_5bps", "stop_rate", "np_rate", "large_rise_capture", "winner_capture", "pos_days", "neg_days", "early_stop_rate", "metric_integrity_blocked")}})
    return out or [{"method": "none"}]


def _daily_rows(methods: Mapping[str, Any]) -> list[dict[str, Any]]:
    out = []
    for mid, m in methods.items():
        for d in (m.get("oos") or {}).get("daily") or []:
            out.append({"method": mid, **d})
    return out or [{"status": "empty"}]


def _flatten_matched(matched: Any) -> list[dict[str, Any]]:
    if not matched:
        return [{"status": "empty"}]
    rows: list[dict[str, Any]] = [
        {
            "verdict": matched.get("verdict"),
            "gate_ok": matched.get("gate_ok"),
            "concentration_warning": matched.get("concentration_warning"),
            "matched_days": ",".join(matched.get("matched_days") or []),
        }
    ]
    for key in (
        "same_day_same_n",
        "same_day_same_cap_usage",
        "same_day_same_opportunity_window",
        "pbv2_native_ranking",
        "rpfe_ranking",
        "random_repeated_baseline",
    ):
        block = matched.get(key) or {}
        if key == "random_repeated_baseline":
            rows.append({"arm": key, **block})
            continue
        ma = block.get("method_a") or {}
        mb = block.get("method_b") or {}
        rows.append(
            {
                "arm": key,
                "n_matched": block.get("n_matched"),
                "days_with_match": block.get("days_with_match"),
                "a_pnl_5bps": ma.get("total_pnl_5bps"),
                "b_pnl_5bps": mb.get("total_pnl_5bps"),
                "a_PF_5bps": ma.get("PF_5bps"),
                "b_PF_5bps": mb.get("PF_5bps"),
            }
        )
    return rows
