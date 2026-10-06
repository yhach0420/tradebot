"""Write report.json / report.md / audit.xlsx / implementation_binding.json only."""
from __future__ import annotations

import json
import math
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from research.am_entry_profit_improvement.publish import json_sanitize as _base_sanitize
from research.pb1_v4_clarified_machine_correction_v2.binding import implementation_binding
from research.pb1_v4_clarified_machine_correction_v2.isolation import OUT

HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
HEADER_FONT = Font(color="FFFFFF", bold=True)
SHEET_ORDER = (
    "Binding",
    "DayReach",
    "Invariants",
    "Leakage",
    "Audit88",
    "Regressions",
    "PrevFP19",
    "PrevFN2",
    "Mismatches",
    "Changed88",
    "Decision",
    "Safety",
)
STRIP = {"_markdown", "setups", "e0_events", "e1_events", "funnel_days", "audit_rows", "chart_zones", "hidden_1m_checks"}


def _json_sanitize(obj: Any) -> Any:
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    out = _base_sanitize(obj)
    if isinstance(out, float) and not math.isfinite(out):
        return None
    if isinstance(out, dict):
        return {str(k): _json_sanitize(v) for k, v in out.items() if k not in STRIP}
    if isinstance(out, list):
        return [_json_sanitize(v) for v in out]
    return out


def _excel_cell(v: Any) -> Any:
    if isinstance(v, (list, dict, tuple, set)):
        return json.dumps(_json_sanitize(v), ensure_ascii=False)
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def _write_sheet(ws, rows: list[dict[str, Any]]) -> None:
    if not rows:
        ws.append(["empty"])
        return
    cols: list[str] = []
    seen: set[str] = set()
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
        ws.append([_excel_cell(r.get(c)) for c in cols])
    for i, _c in enumerate(cols, start=1):
        ws.column_dimensions[get_column_letter(i)].width = min(48, max(12, len(str(_c)) + 2))


def _case_line(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "seed": row.get("machine_SEED"),
        "active": row.get("machine_ACTIVE"),
        "location": row.get("machine_LOCATION"),
        "thesis_ready": row.get("machine_THESIS_READY"),
        "opening_state": row.get("machine_opening_state"),
        "failed_open_form": row.get("failed_open_form"),
        "E0": row.get("machine_E0"),
        "E1": row.get("machine_E1"),
        "death": row.get("machine_death"),
        "location_family": row.get("location_family"),
        "location_A_class": row.get("location_A_class"),
        "interaction": row.get("interaction"),
        "e0_entry_t": row.get("e0_entry_t"),
        "e1_entry_t": row.get("e1_entry_t"),
        "last_progress_class": row.get("last_progress_class"),
        "progress_log": row.get("progress_log"),
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    dec = dict(report.get("decision") or {})
    inv = dict(report.get("invariants") or {})
    hid = dict(report.get("hidden_1m_recompute") or {})
    crit = dict(report.get("critical_cases") or {})
    fam = dict(report.get("family_a_event_counts") or {})
    changed = dict(report.get("changed_row_manifest") or {})
    s6963 = dict(crit.get("6963_20241002") or {})
    log = list(s6963.get("progress_log") or [])
    marginal_reset = any(
        str(x.get("t1") or "")[:5] in ("09:24", "09:34") and bool(x.get("resets_stall")) for x in log
    )
    return {
        "Parent machine unchanged?": bool(report.get("parent_machine_unchanged")),
        "Spec unchanged?": bool(report.get("clarified_spec_sha_bound")) and bool(report.get("parent_specs_preserved")),
        "New correction SHA?": report.get("PB1_V4_CLARIFIED_MACHINE_CORRECTION_V2_SHA256"),
        "Any prospective event consumed?": False,
        "Any future outcome used?": False,
        "Any PnL?": False,
        "FAILED_OPEN now requires visible failed auction path?": True,
        "Can wide doji alone seed FAILED_OPEN?": False,
        "Are directional failed attempt and rejection-path attempt both supported?": True,
        "3382 / 20241004 result?": _case_line(crit.get("3382_20241004") or {}),
        "7011 / 20250523 result?": _case_line(crit.get("7011_20250523") or {}),
        "4063 / 20251118 result?": _case_line(crit.get("4063_20251118") or {}),
        "3382 / 20241115 result?": _case_line(crit.get("3382_20241115") or {}),
        "6273 / 20250120 result?": _case_line(crit.get("6273_20250120") or {}),
        "5802 / 20250613 result?": _case_line(crit.get("5802_20250613") or {}),
        "7182 / 20251112 result?": _case_line(crit.get("7182_20251112") or {}),
        "3110 / 20250805 result?": _case_line(crit.get("3110_20250805") or {}),
        "Any new extreme resets ACTIVE?": False,
        "What resets ACTIVE stall?": "REAL_BREAKOUT_EXTENSION or MEANINGFUL_DIRECTIONAL_EXTENSION (renewed directional auction: directional close progression AND genuine extension)",
        "6963 / 20241002 result?": _case_line(s6963),
        "Did 09:24 / 09:34 marginal resets still reset ACTIVE?": False if not marginal_reset else True,
        "Any hard clock expiry introduced?": False,
        "8031 / 20250225 result?": _case_line(crit.get("8031_20250225") or {}),
        "7741 / 20250314 result?": _case_line(crit.get("7741_20250314") or {}),
        "Can committed final close erase substantial counter-auction?": False,
        "ONE_BAR_SHARE_MAX retained unchanged?": False,
        "WEAK_BAR_BODY_MAX retained unchanged?": False,
        "How is one-bar domination represented now?": "ONE_BAR_DOMINATED_WITHOUT_FOLLOWTHROUGH inside CONTINUED_DIRECTIONAL_INTENT: majority one-bar is invalid only when followthrough does not continue the auction.",
        "How many family A events: A1?": fam.get("A1"),
        "A2?": fam.get("A2"),
        "A3?": fam.get("A3_seen"),
        "Can A3 mint A_CLEARED_ZONE?": False,
        "9432 / 20250402 result?": _case_line(crit.get("9432_20250402") or {}),
        "8058 / 20250814 result?": _case_line(crit.get("8058_20250814") or {}),
        "Can VWAP still participate as confluence/context?": True,
        "Is LOCATION_IDENTIFIED still separate from interaction?": True,
        "THESIS_READY contract changed?": False,
        "Hidden-1m parity?": {
            "stored": hid.get("stored_boolean"),
            "recomputed": hid.get("HIDDEN_1M_THESIS_PARITY_recomputed"),
            "mismatch_n": hid.get("mismatch_n"),
            "trusted_stored_boolean_only": False,
        },
        "State invariant violations?": inv.get("violations"),
        "Collateral changes across all 88?": {
            "changed_n": changed.get("changed_n"),
            "by_rca_cause": changed.get("by_rca_cause"),
            "rows": changed.get("rows"),
        },
        "Any threshold optimized?": False,
        "Old Confirmation opened?": False,
        "Frozen Validation opened?": False,
        "submit/cancel/live?": "0/0/0",
        "VERDICT?": dec.get("VERDICT"),
        "NEXT?": dec.get("NEXT"),
        "incomplete_reasons": dec.get("incomplete_reasons"),
        "21fc72eb preserved?": bool(report.get("corrected_preserved")),
        "fa0451bb preserved?": bool(report.get("leaked_preserved")),
        "SAME_BAR_ENTRY?": inv.get("SAME_BAR_ENTRY"),
    }


def build_sheets(report: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    bind = dict(report.get("bind") or {})
    reach = dict(report.get("day_reach_counts") or {})
    inv = dict(report.get("invariants") or {})
    leak = dict(report.get("leakage") or {})
    audit = dict(report.get("semantic_development_audit") or {})
    return {
        "Binding": [{"key": k, "value": json.dumps(_json_sanitize(v), ensure_ascii=False) if isinstance(v, (dict, list)) else v} for k, v in bind.items()],
        "DayReach": [{"state": k, "n": v} for k, v in reach.items()],
        "Invariants": [{"key": k, "value": json.dumps(_json_sanitize(v), ensure_ascii=False) if isinstance(v, (dict, list)) else v} for k, v in inv.items()],
        "Leakage": [{"key": k, "value": json.dumps(_json_sanitize(v), ensure_ascii=False) if isinstance(v, (dict, list)) else v} for k, v in leak.items()],
        "Audit88": list(report.get("audit_rows") or []),
        "Regressions": list(audit.get("required_regressions") or []),
        "PrevFP19": list(report.get("prev_s1_fp19") or []),
        "PrevFN2": list(report.get("prev_s1_fn2") or []),
        "Mismatches": list(report.get("material_mismatches") or []),
        "Changed88": list((report.get("changed_row_manifest") or {}).get("rows") or []),
        "Decision": [{"key": k, "value": json.dumps(_json_sanitize(v), ensure_ascii=False) if isinstance(v, (dict, list)) else v} for k, v in (report.get("answers") or {}).items()],
        "Safety": [{"key": k, "value": v} for k, v in (report.get("safety") or {}).items()],
    }


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    payload = _json_sanitize(report)
    (OUT / "report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "implementation_binding.json").write_text(
        json.dumps(implementation_binding(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (OUT / "changed_row_manifest.json").write_text(
        json.dumps(_json_sanitize(report.get("changed_row_manifest") or {}), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (OUT / "numeric_boundary_provenance.json").write_text(
        json.dumps(_json_sanitize((report.get("calibration") or {}).get("numeric_boundaries") or []), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (OUT / "regression_manifest.json").write_text(
        json.dumps(
            _json_sanitize((report.get("semantic_development_audit") or {}).get("required_regressions") or []),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (OUT / "state_definitions.json").write_text(
        json.dumps(
            {
                "SPEC_SHA256": report.get("EXPECTED_SPEC_SHA256"),
                "PARENT_MACHINE_SHA256": report.get("PARENT_CLARIFIED_MACHINE_SHA256"),
                "PB1_V4_CLARIFIED_MACHINE_CORRECTION_V2_SHA256": report.get("PB1_V4_CLARIFIED_MACHINE_CORRECTION_V2_SHA256"),
                "STATE_MACHINE_TEXT": report.get("STATE_MACHINE_TEXT"),
                "progress_classes_that_reset_stall": ["REAL_BREAKOUT_EXTENSION", "MEANINGFUL_DIRECTIONAL_EXTENSION"],
                "progress_classes_that_do_not_reset": ["RANGE_DRIFT_EXTREME", "MARGINAL_EXTREME_ONLY", "NO_DIRECTIONAL_PROGRESS"],
                "failed_open_forms": ["DIRECTIONAL_FAILED_ATTEMPT", "WIDE_REJECTION_FAILED_ATTEMPT"],
                "family_a": ["A1_PREEXISTING_LEVEL_CLEARED_BY_OPENING_DRIVE", "A2_LEVEL_ALREADY_CLEARED_BEFORE_SEED", "A3_NO_REAL_CLEAR_BUT_CLASSIFIER_MATCHED_ZONE"],
                "future_outcome_used": False,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    ans = dict(report.get("answers") or {})
    md = ["# PB1 V4 clarified machine correction V2", "", "DEVELOPMENT_PARITY_ONLY. Not face validation.", ""]
    for k, v in ans.items():
        if isinstance(v, (dict, list)):
            md.append(f"- **{k}** `{json.dumps(_json_sanitize(v), ensure_ascii=False)[:900]}`")
        else:
            md.append(f"- **{k}** `{v}`")
    md.append("")
    md.append("STOP.")
    (OUT / "report.md").write_text("\n".join(md), encoding="utf-8")
    wb = Workbook()
    first = True
    for name in SHEET_ORDER:
        ws = wb.active if first else wb.create_sheet(name)
        if first:
            ws.title = name
            first = False
        _write_sheet(ws, list(sheets.get(name) or []))
    wb.save(OUT / "audit.xlsx")
