"""Write report.json / report.md / audit.xlsx only."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from research.am_entry_profit_improvement.publish import json_sanitize as _json_sanitize
from research.support_resistance_test_design_audit_v1.isolation import OUT

HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
HEADER_FONT = Font(color="FFFFFF", bold=True)
SHEET_ORDER = (
    "Binding",
    "Zone_Density",
    "Zone_Overlap",
    "Reaction_Salience",
    "Random_Chart_Sample_200",
    "Retest_Semantics",
    "State_Transition_Samples",
    "Event_Independence",
    "First_Interaction_Design",
    "Matched_Control_Design",
    "Outcome_Metric_Review",
    "Minute_Limit_Limitation",
    "Root_Cause",
    "Safety",
)


def json_sanitize(obj: Any) -> Any:
    got = _json_sanitize(obj)
    if isinstance(got, float) and abs(got) == float("inf"):
        return "inf" if got > 0 else "-inf"
    if isinstance(got, dict):
        return {str(k): json_sanitize(v) for k, v in got.items()}
    if isinstance(got, (list, tuple)):
        return [json_sanitize(x) for x in got]
    return got


def _kv_rows(d: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for k, v in d.items():
        if isinstance(v, (dict, list, tuple)):
            v = json.dumps(json_sanitize(v), ensure_ascii=False)[:32000]
        rows.append({"key": str(k), "value": v})
    return rows


def _write_sheet(ws, rows: list[dict[str, Any]]) -> None:
    if not rows:
        ws.append(["key", "value"])
        ws.append(["empty", True])
        return
    cols = []
    seen = set()
    for r in rows:
        for k in r.keys():
            if k not in seen:
                seen.add(k)
                cols.append(k)
    ws.append(cols)
    for cell in ws[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
    for r in rows:
        ws.append([r.get(c) if not isinstance(r.get(c), (dict, list)) else json.dumps(json_sanitize(r.get(c)), ensure_ascii=False)[:32000] for c in cols])
    for i, _c in enumerate(cols, start=1):
        ws.column_dimensions[get_column_letter(i)].width = min(28, max(12, len(str(_c)) + 2))


def flatten_traces(traces: list[dict[str, Any]], cap_bars: int = 90) -> list[dict[str, Any]]:
    out = []
    for i, tr in enumerate(traces, start=1):
        bars = list(tr.get("bars") or [])[:cap_bars]
        if not bars:
            out.append(
                {
                    "sample_id": i,
                    "symbol": tr.get("symbol"),
                    "date": tr.get("date"),
                    "zone_id": tr.get("zone_id"),
                    "cleared_before_retest": tr.get("cleared_before_retest"),
                    "lingering": tr.get("lingering"),
                    "n_hold": tr.get("n_hold"),
                    "n_fail": tr.get("n_fail"),
                    "bar_i": None,
                    "t": None,
                    "c": None,
                    "events": "NO_BARS",
                }
            )
            continue
        for j, b in enumerate(bars):
            out.append(
                {
                    "sample_id": i,
                    "symbol": tr.get("symbol"),
                    "date": tr.get("date"),
                    "block": tr.get("block"),
                    "zone_id": tr.get("zone_id"),
                    "cleared_before_retest": tr.get("cleared_before_retest"),
                    "lingering": tr.get("lingering"),
                    "n_retest": tr.get("n_retest"),
                    "n_hold": tr.get("n_hold"),
                    "n_fail": tr.get("n_fail"),
                    "bar_i": j,
                    "t": b.get("t"),
                    "c": b.get("c"),
                    "h": b.get("h"),
                    "l": b.get("l"),
                    "in_zone": b.get("in_zone"),
                    "waiting_retest": b.get("waiting_retest"),
                    "flipped": b.get("flipped"),
                    "broke_above": b.get("broke_above"),
                    "bars_inside": b.get("bars_inside"),
                    "events": b.get("events"),
                }
            )
    return out


def build_sheets(report: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    bind = dict(report.get("bind") or {})
    dens = dict(report.get("density") or {})
    sal = dict(report.get("salience") or {})
    sem = dict(report.get("semantics") or {})
    minute = dict(report.get("minute") or {})
    dec = dict(report.get("decision") or {})
    safety = dict(report.get("safety") or {})
    chart_index = list(report.get("chart_index") or []) + list(report.get("chart_zone_index") or [])
    density_rows = list(report.get("density_rows") or [])
    # compact density distribution + a thin per-day extract
    overlap_rows = [
        {
            "date": r.get("date"),
            "symbol": r.get("symbol"),
            "block": r.get("block"),
            "n_res_active": r.get("n_res_active"),
            "n_sup_active": r.get("n_sup_active"),
            "res_material_overlap_pairs": r.get("res_material_overlap_pairs"),
            "sup_material_overlap_pairs": r.get("sup_material_overlap_pairs"),
            "res_nested_pairs": r.get("res_nested_pairs"),
            "sup_nested_pairs": r.get("sup_nested_pairs"),
            "res_min_center_dist_atr": r.get("res_min_center_dist_atr"),
            "sup_min_center_dist_atr": r.get("sup_min_center_dist_atr"),
            "res_shared_members": r.get("res_shared_members"),
            "sup_shared_members": r.get("sup_shared_members"),
            "n_res_already_broken": r.get("n_res_already_broken"),
            "n_sup_already_broken": r.get("n_sup_already_broken"),
        }
        for r in density_rows
    ]
    sal_rows = list(report.get("salience_rows") or [])
    if len(sal_rows) > 5000:
        sal_rows = sal_rows[:: max(1, len(sal_rows) // 5000)][:5000]
    return {
        "Binding": _kv_rows(
            {
                "analysis_id": report.get("analysis_id"),
                "parent_verdict": report.get("parent_verdict"),
                "bind_ok": bind.get("ok"),
                "split_sha256": (bind.get("split") or {}).get("split_sha256"),
                "block_sha256": (bind.get("blocks") or {}).get("block_sha256"),
                "research_pool_n": bind.get("research_pool_n"),
                "rca_deferred": report.get("rca_deferred"),
                "old_confirmation_opened": False,
                "frozen_validation_opened": False,
                "no_pnl_parameter_tuning": True,
                "zone_artifacts_preserved": bind.get("zone_artifacts_preserved"),
                "VERDICT": dec.get("VERDICT"),
                "NEXT": dec.get("NEXT"),
            }
        ),
        "Zone_Density": _kv_rows(dens) + [
            {
                "key": "_row",
                "date": r.get("date"),
                "symbol": r.get("symbol"),
                "block": r.get("block"),
                "sector": r.get("sector"),
                "vol_regime": r.get("vol_regime"),
                "n_res_active": r.get("n_res_active"),
                "n_sup_active": r.get("n_sup_active"),
                "n_res_near_2atr": r.get("n_res_near_2atr"),
                "n_sup_near_2atr": r.get("n_sup_near_2atr"),
                "n_res_stale_3atr": r.get("n_res_stale_3atr"),
                "n_sup_stale_3atr": r.get("n_sup_stale_3atr"),
                "nearest_res_above_open_atr": r.get("nearest_res_above_open_atr"),
                "nearest_sup_below_open_atr": r.get("nearest_sup_below_open_atr"),
                "clutter": r.get("clutter"),
            }
            for r in density_rows[:: max(1, len(density_rows) // 4000)][:4000]
        ]
        if density_rows
        else _kv_rows(dens),
        "Zone_Overlap": _kv_rows(
            {
                "material_overlap_pair_total": dens.get("material_overlap_pair_total"),
                "symbol_days_with_material_overlap": dens.get("symbol_days_with_material_overlap"),
                "nested_pair_total": dens.get("nested_pair_total"),
                "shared_reaction_members_total": dens.get("shared_reaction_members_total"),
                "shared_members_note": "Greedy clustering assigns each reaction to exactly one cluster. Overlap is geometric band overlap, not shared members.",
                "do_not_merge_yet": True,
            }
        )
        + overlap_rows[:: max(1, len(overlap_rows) // 4000)][:4000],
        "Reaction_Salience": _kv_rows(sal) + sal_rows,
        "Random_Chart_Sample_200": chart_index,
        "Retest_Semantics": _kv_rows(sem),
        "State_Transition_Samples": flatten_traces(list(report.get("traces") or [])),
        "Event_Independence": _kv_rows(minute) + list(report.get("matched_rows") or []),
        "First_Interaction_Design": _kv_rows(dict(report.get("first_interaction_design") or {})),
        "Matched_Control_Design": _kv_rows(dict(report.get("matched_control_design") or {})),
        "Outcome_Metric_Review": _kv_rows(dict(report.get("outcome_metric_review") or {})),
        "Minute_Limit_Limitation": _kv_rows(dict(report.get("minute_limit_limitation") or {})),
        "Root_Cause": list(report.get("root_cause") or []),
        "Safety": _kv_rows(safety),
    }


def build_markdown(report: dict[str, Any]) -> str:
    a = dict(report.get("answers") or {})
    d = dict(report.get("decision") or {})
    lines = [
        "# SUPPORT_RESISTANCE_TEST_DESIGN_AUDIT_V1",
        "",
        f"VERDICT: **{d.get('VERDICT')}**",
        f"NEXT: **{d.get('NEXT')}**",
        "",
        str(d.get("INTERPRETATION") or ""),
        "",
        "The prior MULTI_TOUCH_ZONE_NO_INCREMENTAL_INFORMATION_V1 result is a statement about that test design. It is not a statement that support/resistance has no market information.",
        "",
        f"Median active resistance zones per symbol-day? **{a.get('Median active resistance zones per symbol-day?')}**",
        f"Median support zones? **{a.get('Median support zones?')}**",
        f"How many zones overlap materially? **{a.get('How many zones overlap materially?')}**",
        f"Do machine zones visually correspond to obvious chart zones? **{a.get('Do machine zones visually correspond to obvious chart zones?')}**",
        f"How many reaction points are structurally weak? **{a.get('How many reaction points are structurally weak?')}**",
        f"Are interaction labels independent? **{a.get('Are interaction labels independent?')}**",
        f"Unique symbol × zone × day episodes? **{a.get('How many unique symbol × zone × day episodes exist?')}**",
        f"Raw events per true interaction? **{a.get('How many raw events are emitted per true interaction?')}**",
        f"Mean bars_inside 57–80 consistent with intended setup? **{a.get('Is mean bars_inside 57–80 consistent with the intended setup?')}**",
        f"5pp continuation appropriate primary gate? **{a.get('Was 5pp continuation an appropriate primary gate?')}**",
        f"Old NO_INCREMENTAL_INFORMATION still justified? **{a.get('Is the old MULTI_TOUCH_ZONE_NO_INCREMENTAL_INFORMATION_V1 still justified?')}**",
        f"No PnL parameter tuning? **{a.get('No PnL parameter tuning?')}**",
        f"Old Confirmation opened? **{a.get('Old Confirmation opened?')}** Frozen Validation opened? **{a.get('Frozen Validation opened?')}**",
        f"submit/cancel/live: **{a.get('submit/cancel/live?')}**",
        "",
        "STOP.",
        "",
    ]
    return "\n".join(lines)


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    payload = json_sanitize(
        {
            k: v
            for k, v in report.items()
            if k
            not in {
                "density_rows",
                "salience_rows",
                "_markdown",
                "traces",
                "matched_rows",
                "chart_zone_index",
            }
        }
    )
    # keep chart index and compact traces in json; drop raw density
    (OUT / "report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "report.md").write_text(str(report.get("_markdown") or build_markdown(report)), encoding="utf-8")
    wb = Workbook()
    first = True
    for name in SHEET_ORDER:
        ws = wb.active if first else wb.create_sheet()
        first = False
        ws.title = name[:31]
        _write_sheet(ws, list(sheets.get(name) or []))
    wb.save(OUT / "audit.xlsx")
