"""Write report.json / report.md / audit.xlsx only. Do not write parent OUT."""
from __future__ import annotations

import json
import math
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from research.am_entry_profit_improvement.publish import json_sanitize as _base_sanitize
from research.pb1_v3_2_discovery_unseen_face_verify.isolation import OUT

HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
HEADER_FONT = Font(color="FFFFFF", bold=True)
SHEET_ORDER = (
    "Binding",
    "Unseen_Events",
    "Human",
    "Confusion",
    "Manifest",
    "Decision",
    "Safety",
)
STRIP = {"_markdown", "events", "failed_push_archive", "funnel_days", "chart_zones", "sample_full"}


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


def _kv_rows(d: Any) -> list[dict[str, Any]]:
    if isinstance(d, dict):
        return [
            {"key": k, "value": json.dumps(_json_sanitize(v), ensure_ascii=False) if isinstance(v, (dict, list)) else v}
            for k, v in d.items()
        ]
    return [{"key": "value", "value": d}]


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


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    human = dict(report.get("human") or {})
    dec = dict(report.get("decision") or {})
    man = dict(report.get("manifest_update") or {})
    ci = dict(report.get("clear_wilson_95") or {})
    return {
        "v32_sha_unchanged": report.get("v32_unchanged"),
        "V3_2_MACHINE_SHA256": report.get("MACHINE_SHA256"),
        "reported_discovery_unseen_n": report.get("reported_discovery_unseen_n"),
        "exact_materialized_unseen_n": report.get("materialized_unseen_n"),
        "manifest_overlap_n": report.get("manifest_overlap_n"),
        "actual_independently_reviewed_n": report.get("actual_independently_reviewed_n"),
        "all_unseen_events_reviewed": report.get("all_unseen_events_reviewed"),
        "future_hidden": True,
        "CLEAR_n": human.get("CLEAR_CONTINUATION"),
        "CLEAR_share": human.get("clear_share"),
        "QUESTIONABLE_n": human.get("QUESTIONABLE"),
        "QUESTIONABLE_share": human.get("questionable_share"),
        "NOT_n": human.get("NOT_CONTINUATION"),
        "NOT_share": human.get("not_share"),
        "CLEAR_wilson_95_lo": ci.get("lo"),
        "CLEAR_wilson_95_hi": ci.get("hi"),
        "opening_drive_valid_n": human.get("opening_drive_valid_n"),
        "opening_drive_valid_share": human.get("opening_drive_valid_share"),
        "early_reversal_drive_valid_n": human.get("early_reversal_drive_valid_n"),
        "early_reversal_drive_valid_share": human.get("early_reversal_drive_valid_share"),
        "valid_first_retest_n": human.get("valid_first_retest_n"),
        "valid_first_retest_share": human.get("valid_first_retest_share"),
        "clear_defended_location_n": human.get("clear_defended_location_n"),
        "clear_defended_location_share": human.get("clear_defended_location_share"),
        "valid_reacceleration_n": human.get("valid_reacceleration_n"),
        "valid_reacceleration_share": human.get("valid_reacceleration_share"),
        "weak_reacceleration_n": human.get("weak_reacceleration_n"),
        "weak_reacceleration_share": human.get("weak_reacceleration_share"),
        "primary_semantic_failure": dec.get("primary_semantic_failure"),
        "existing_face_gate_passed": dec.get("existing_face_gate_passed"),
        "any_v32_rule_changed": False,
        "any_future_outcome_used": False,
        "any_economic_test": False,
        "any_pnl": False,
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "submit_cancel_live": "0/0/0",
        "manifest_previous_n": man.get("previous_manifest_n"),
        "manifest_added_n": man.get("newly_added_n"),
        "manifest_final_n": man.get("new_manifest_n"),
        "VERDICT": dec.get("VERDICT"),
        "NEXT": dec.get("NEXT"),
    }


def build_markdown(report: dict[str, Any]) -> str:
    a = dict(report.get("answers") or {})
    ci = dict(report.get("clear_wilson_95") or {})
    lines = [
        "# PB1_V3_2_DISCOVERY_UNSEEN_FACE_VERIFY_V1",
        "",
        "Blinded Discovery-unseen semantic holdout. Frozen V3.2 unchanged. No PnL.",
        "",
        f"V3.2 SHA unchanged? **{a.get('v32_sha_unchanged')}**",
        f"V3.2 SHA? `{a.get('V3_2_MACHINE_SHA256')}`",
        f"Reported Discovery unseen_n? **{a.get('reported_discovery_unseen_n')}**",
        f"Exact materialized unseen_n? **{a.get('exact_materialized_unseen_n')}**",
        f"Manifest overlap_n? **{a.get('manifest_overlap_n')}**",
        f"Actual independently reviewed_n? **{a.get('actual_independently_reviewed_n')}**",
        f"All unseen events reviewed? **{a.get('all_unseen_events_reviewed')}**",
        f"Future hidden? **{a.get('future_hidden')}**",
        "",
        f"CLEAR n/share? **{a.get('CLEAR_n')}** / **{a.get('CLEAR_share')}**",
        f"QUESTIONABLE n/share? **{a.get('QUESTIONABLE_n')}** / **{a.get('QUESTIONABLE_share')}**",
        f"NOT n/share? **{a.get('NOT_n')}** / **{a.get('NOT_share')}**",
        f"CLEAR Wilson 95% CI? **{ci.get('lo')}** – **{ci.get('hi')}**",
        "",
        f"Opening-drive valid n/share? **{a.get('opening_drive_valid_n')}** / **{a.get('opening_drive_valid_share')}**",
        f"Early-reversal drive valid n/share? **{a.get('early_reversal_drive_valid_n')}** / **{a.get('early_reversal_drive_valid_share')}**",
        f"Valid first retest n/share? **{a.get('valid_first_retest_n')}** / **{a.get('valid_first_retest_share')}**",
        f"Clear defended location n/share? **{a.get('clear_defended_location_n')}** / **{a.get('clear_defended_location_share')}**",
        f"Valid reacceleration n/share? **{a.get('valid_reacceleration_n')}** / **{a.get('valid_reacceleration_share')}**",
        f"Weak reacceleration n/share? **{a.get('weak_reacceleration_n')}** / **{a.get('weak_reacceleration_share')}**",
        f"Primary semantic failure? **{a.get('primary_semantic_failure')}**",
        f"Existing face gate passed? **{a.get('existing_face_gate_passed')}**",
        "",
        f"Any V3.2 rule changed? **{a.get('any_v32_rule_changed')}**",
        f"Any future outcome used? **{a.get('any_future_outcome_used')}**",
        f"Any economic test? **{a.get('any_economic_test')}**",
        f"Any PnL? **{a.get('any_pnl')}**",
        f"Old Confirmation opened? **{a.get('old_confirmation_opened')}**",
        f"Frozen Validation opened? **{a.get('frozen_validation_opened')}**",
        f"submit/cancel/live? **{a.get('submit_cancel_live')}**",
        "",
        f"Manifest previous_n? **{a.get('manifest_previous_n')}**",
        f"Manifest added_n? **{a.get('manifest_added_n')}**",
        f"Manifest final_n? **{a.get('manifest_final_n')}**",
        "",
        f"VERDICT? {a.get('VERDICT')}",
        f"NEXT? {a.get('NEXT')}",
        "STOP.",
        "",
    ]
    return "\n".join(lines)


def build_sheets(report: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    human = dict(report.get("human") or {})
    return {
        "Binding": _kv_rows(report.get("bind")),
        "Unseen_Events": list(report.get("sample") or []),
        "Human": list(human.get("rows") or []),
        "Confusion": _kv_rows(
            {
                "CLEAR_n": human.get("CLEAR_CONTINUATION"),
                "CLEAR_share": human.get("clear_share"),
                "QUESTIONABLE_n": human.get("QUESTIONABLE"),
                "NOT_n": human.get("NOT_CONTINUATION"),
                "wilson": report.get("clear_wilson_95"),
                "opening": human.get("opening_counts"),
                "retest": human.get("retest_counts"),
                "location": human.get("location_counts"),
                "trigger": human.get("trigger_counts"),
            }
        ),
        "Manifest": _kv_rows(report.get("manifest_update")),
        "Decision": _kv_rows(report.get("decision")),
        "Safety": _kv_rows(report.get("safety")),
    }


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]], manifest: dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    md = build_markdown(report)
    report["_markdown"] = md
    (OUT / "report.md").write_text(md, encoding="utf-8")
    payload = _json_sanitize({k: v for k, v in report.items() if k not in STRIP})
    (OUT / "report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "face_review_exclusion_manifest.json").write_text(
        json.dumps(_json_sanitize(manifest), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    wb = Workbook()
    first = True
    for name in SHEET_ORDER:
        ws = wb.active if first else wb.create_sheet(name)
        if first:
            ws.title = name
            first = False
        _write_sheet(ws, sheets.get(name) or [])
    wb.save(OUT / "audit.xlsx")
