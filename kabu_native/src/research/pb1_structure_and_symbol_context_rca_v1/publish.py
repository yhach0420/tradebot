"""Write report.json / report.md / audit.xlsx only."""
from __future__ import annotations

import json
import math
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from research.am_entry_profit_improvement.publish import json_sanitize as _base_sanitize
from research.pb1_structure_and_symbol_context_rca_v1.isolation import OUT

HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
HEADER_FONT = Font(color="FFFFFF", bold=True)
SHEET_ORDER = (
    "Binding",
    "Class_Counts",
    "By_Class",
    "By_Flip",
    "Trigger_Structure",
    "Block_Composition",
    "OR_vs_Structure",
    "Reward_Geometry",
    "Archetypes",
    "Sectors",
    "Market_x_Class",
    "First_Passage",
    "Control_First_Passage",
    "Human_Sample",
    "Cause_Hierarchy",
    "Decision",
    "Safety",
)
STRIP = {"_markdown", "events", "pairs", "recs", "eligible", "market_days", "chart_zones"}


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


def json_sanitize(obj: Any) -> Any:
    return _json_sanitize(obj)


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
        ws.append([r.get(c) for c in cols])
    for i, _c in enumerate(cols, start=1):
        ws.column_dimensions[get_column_letter(i)].width = min(48, max(12, len(str(_c)) + 2))


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    cc = dict(report.get("class_counts") or {})
    byc = dict(report.get("by_class") or {})
    trig = dict(report.get("trigger_structure") or {})
    blocks = dict(report.get("by_block") or {})
    ors = dict(report.get("or_vs_structure") or {})
    fl = dict(report.get("by_flip") or {})
    arch = dict(report.get("archetypes") or {})
    sec = dict(report.get("sectors") or {})
    mkt = dict(report.get("market_x_class") or {})
    human = dict(report.get("human") or {})
    hier = dict(report.get("hierarchy") or {})
    flags = dict(hier.get("flags") or {})
    dec = dict(report.get("decision") or {})
    reclaim = dict(trig.get("RECLAIM_RETEST_MICRO_HIGH") or {})
    failed = dict(trig.get("FAILED_PUSH_THEN_CLOSE_BACK") or {})
    return {
        "BREAK_INTO_RESISTANCE_n": cc.get("BREAK_INTO_RESISTANCE"),
        "BREAK_THROUGH_RESISTANCE_n": cc.get("BREAK_THROUGH_RESISTANCE"),
        "CLEAN_FLIP_n": (fl.get("CLEAN_FLIP") or {}).get("n"),
        "RESISTANCE_TO_SUPPORT_FLIP_n": cc.get("RESISTANCE_TO_SUPPORT_FLIP"),
        "OPEN_SPACE_BREAK_n": cc.get("OPEN_SPACE_BREAK"),
        "CONGESTED_n": cc.get("STRUCTURALLY_CONGESTED"),
        "plus1R_differs_by_class": report.get("plus1r_differs_by_class"),
        "plus1R_by_class": {k: (byc.get(k) or {}).get("P_plus_1_0R_before_fail") for k in byc},
        "reclaim_coherent_in_structure": reclaim.get("flip_share"),
        "reclaim_n": reclaim.get("n"),
        "reclaim_plus1R": reclaim.get("P_plus_1_0R_before_fail"),
        "failed_push_into_share": failed.get("into_share"),
        "failed_push_congested_share": failed.get("congested_share"),
        "failed_push_flip_mix": failed.get("flip_mix"),
        "D2_into_share": ((blocks.get("D2") or {}).get("class_mix") or {}).get("BREAK_INTO_RESISTANCE_share"),
        "D3_into_share": ((blocks.get("D3") or {}).get("class_mix") or {}).get("BREAK_INTO_RESISTANCE_share"),
        "d23_more_blocked": flags.get("d23_more_blocked"),
        "structure_explains_block_instability": flags.get("d23_more_blocked"),
        "or_overlaps_prior_zone_share": ors.get("or_overlaps_prior_zone_share"),
        "or_in_open_space_share": ors.get("or_in_open_space_share"),
        "flip_matters": flags.get("flip_matters"),
        "normalized_reward_space_matters": flags.get("no_room"),
        "archetype_changes_pb1_meaning": flags.get("arch_diff"),
        "archetype_is_sector": bool(sec.get("n_sectors")) and bool(arch),
        "market_regime_remains": flags.get("mkt_diff"),
        "sample_n": report.get("sample_n"),
        "structural_classification_human_valid_share": human.get("structural_classification_human_valid_share"),
        "any_eligibility_changed": False,
        "any_threshold_optimized": False,
        "any_best_subgroup_selected": False,
        "any_pnl_optimization": False,
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "submit_cancel_live": "0/0/0",
        "PRIMARY_CAUSE": hier.get("PRIMARY_CAUSE"),
        "SECONDARY_CAUSE": hier.get("SECONDARY_CAUSE"),
        "CONTRIBUTING_CAUSE": hier.get("CONTRIBUTING_CAUSE"),
        "NOT_SUPPORTED": hier.get("NOT_SUPPORTED"),
        "VERDICT": dec.get("VERDICT"),
        "NEXT": dec.get("NEXT"),
        "market_x_class_open_vs_into": {
            "OPEN_SPACE_BREAK": (mkt.get("class_x_regime") or {}).get("OPEN_SPACE_BREAK"),
            "BREAK_INTO_RESISTANCE": (mkt.get("class_x_regime") or {}).get("BREAK_INTO_RESISTANCE"),
        },
    }


def build_markdown(report: dict[str, Any]) -> str:
    a = dict(report.get("answers") or {})
    lines = [
        "# PB1_STRUCTURE_AND_SYMBOL_CONTEXT_RCA_V1",
        "",
        "Addendum RCA. Frozen V2 machine. No redesign. No PnL.",
        "",
        f"setup_n={report.get('setup_n')} MACHINE={report.get('MACHINE_SHA256')}",
        "",
        f"BREAK_INTO_RESISTANCE? **{a.get('BREAK_INTO_RESISTANCE_n')}**",
        f"BREAK_THROUGH_RESISTANCE? **{a.get('BREAK_THROUGH_RESISTANCE_n')}**",
        f"CLEAN_FLIP / RESISTANCE_TO_SUPPORT_FLIP? **{a.get('CLEAN_FLIP_n')}** / **{a.get('RESISTANCE_TO_SUPPORT_FLIP_n')}**",
        f"OPEN_SPACE_BREAK? **{a.get('OPEN_SPACE_BREAK_n')}**",
        f"CONGESTED? **{a.get('CONGESTED_n')}**",
        "",
        f"Does +1R-before-failure differ by structural class? **{a.get('plus1R_differs_by_class')}**",
        f"+1R by class: {a.get('plus1R_by_class')}",
        "",
        f"Does RECLAIM still look coherent inside correct structural context? flip_share={a.get('reclaim_coherent_in_structure')} +1R={a.get('reclaim_plus1R')}",
        f"Where does FAILED_PUSH occur structurally? into={a.get('failed_push_into_share')} congested={a.get('failed_push_congested_share')} flip={a.get('failed_push_flip_mix')}",
        "",
        f"Did D2/D3 contain more resistance-blocked events? D2_into={a.get('D2_into_share')} D3_into={a.get('D3_into_share')} d23_more_blocked={a.get('d23_more_blocked')}",
        f"Does structural composition explain block instability? **{a.get('structure_explains_block_instability')}**",
        "",
        f"Does OR boundary often coincide with a real prior S/R zone? **{a.get('or_overlaps_prior_zone_share')}** (open_space={a.get('or_in_open_space_share')})",
        f"Does resistance→support flip matter mechanistically? **{a.get('flip_matters')}**",
        f"Does normalized reward space matter? **{a.get('normalized_reward_space_matters')}**",
        f"Do symbol behavior archetypes change PB1 meaning? **{a.get('archetype_changes_pb1_meaning')}**",
        f"Are archetype effects actually sector effects? reported composition only, no sector threshold. n_sectors in pack.",
        f"Does market regime remain important after structural context? **{a.get('market_regime_remains')}**",
        "",
        f"New blinded sample_n? **{a.get('sample_n')}**",
        f"Structural classification human-valid share? **{a.get('structural_classification_human_valid_share')}**",
        "",
        f"PRIMARY_CAUSE? **{a.get('PRIMARY_CAUSE')}**",
        f"SECONDARY_CAUSE? **{a.get('SECONDARY_CAUSE')}**",
        f"CONTRIBUTING_CAUSE? {a.get('CONTRIBUTING_CAUSE')}",
        f"NOT_SUPPORTED? {a.get('NOT_SUPPORTED')}",
        "",
        f"Any eligibility changed? {a.get('any_eligibility_changed')}",
        f"Any threshold optimized? {a.get('any_threshold_optimized')}",
        f"Any best subgroup selected? {a.get('any_best_subgroup_selected')}",
        f"Any PnL optimization? {a.get('any_pnl_optimization')}",
        f"Old Confirmation opened? {a.get('old_confirmation_opened')}",
        f"Frozen Validation opened? {a.get('frozen_validation_opened')}",
        f"submit/cancel/live? {a.get('submit_cancel_live')}",
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
        "Class_Counts": _kv_rows(report.get("class_counts")),
        "By_Class": _kv_rows(report.get("by_class")),
        "By_Flip": _kv_rows(report.get("by_flip")),
        "Trigger_Structure": _kv_rows(report.get("trigger_structure")),
        "Block_Composition": _kv_rows(report.get("by_block")),
        "OR_vs_Structure": _kv_rows(report.get("or_vs_structure")),
        "Reward_Geometry": _kv_rows(report.get("reward")),
        "Archetypes": _kv_rows(report.get("archetypes")),
        "Sectors": _kv_rows(report.get("sectors")),
        "Market_x_Class": _kv_rows(report.get("market_x_class")),
        "First_Passage": _kv_rows(report.get("first_passage")),
        "Control_First_Passage": _kv_rows(report.get("control_first_passage")),
        "Human_Sample": list(human.get("rows") or [_kv_rows(human)[0]]),
        "Cause_Hierarchy": _kv_rows(report.get("hierarchy")),
        "Decision": _kv_rows(report.get("decision")),
        "Safety": _kv_rows(report.get("safety")),
    }


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    md = build_markdown(report)
    report["_markdown"] = md
    (OUT / "report.md").write_text(md, encoding="utf-8")
    payload = _json_sanitize({k: v for k, v in report.items() if k not in STRIP})
    (OUT / "report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    wb = Workbook()
    first = True
    for name in SHEET_ORDER:
        ws = wb.active if first else wb.create_sheet(name)
        if first:
            ws.title = name
            first = False
        _write_sheet(ws, sheets.get(name) or [])
    wb.save(OUT / "audit.xlsx")
