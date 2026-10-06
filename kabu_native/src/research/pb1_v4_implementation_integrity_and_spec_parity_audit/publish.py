"""Write report.json / report.md / audit.xlsx only. V4 OUT is not written."""
from __future__ import annotations

import json
import math
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from research.am_entry_profit_improvement.publish import json_sanitize as _base_sanitize
from research.pb1_v4_implementation_integrity_and_spec_parity_audit.isolation import OUT

HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
HEADER_FONT = Font(color="FFFFFF", bold=True)
SHEET_ORDER = (
    "Binding",
    "Counts",
    "Invariants",
    "Identity",
    "Leakage",
    "S1_Parity",
    "S2_Focus",
    "S3_Parity",
    "S4_CLEAR",
    "Negatives",
    "E0_E1",
    "Decision",
    "Safety",
)
STRIP = {"_markdown", "rows", "funnel_days", "setups", "e0_events", "e1_events", "violating_ids"}


def _json_sanitize(obj: Any) -> Any:
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    out = _base_sanitize(obj)
    if isinstance(out, float) and not math.isfinite(out):
        return None
    if isinstance(out, dict):
        return {str(k): _json_sanitize(v) for k, v in out.items() if k not in STRIP}
    if isinstance(out, list):
        return [_json_sanitize(x) for x in out]
    return out


def _sheet(ws, headers: list[str], rows: list[list[Any]]) -> None:
    for i, h in enumerate(headers, 1):
        cell = ws.cell(1, i, h)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
    for r, row in enumerate(rows, 2):
        for c, val in enumerate(row, 1):
            if isinstance(val, (dict, list)):
                val = json.dumps(val, ensure_ascii=False)[:32000]
            ws.cell(r, c, val)
    for i in range(1, len(headers) + 1):
        ws.column_dimensions[get_column_letter(i)].width = 28


def _kv_rows(d: dict[str, Any]) -> list[list[Any]]:
    return [[k, v] for k, v in d.items()]


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    c = dict(report.get("counts") or {})
    inv = dict(report.get("invariants") or {})
    ident = dict(report.get("identity") or {})
    leak = dict(report.get("leakage") or {})
    par = dict(report.get("parity") or {})
    e01 = dict(par.get("e0_e1") or report.get("e0_e1") or {})
    dec = dict(report.get("decision") or {})
    s4_9432 = dict(par.get("9432") or report.get("focus_9432") or {})
    miss = dict(par.get("clear_miss_layer_n") or {})
    s1c = dict(par.get("s1_mismatch_class_counts") or {})
    stages = dict(c.get("stages") or {})
    return {
        "V4_SHA_unchanged": bool(report.get("v4_source_unchanged")) and str(report.get("V4_MACHINE_SHA256")) == str(report.get("FROZEN_V4_SHA")),
        "Any_rule_changed": False,
        "What_S0_S1_S2_S3_S4_mean": c.get("rename"),
        "Strict_funnel_counts": False,
        "If_yes_why_S3_gt_S2": c.get("why_reported_S3_gt_S2"),
        "S0_event_n": (stages.get("S0") or {}).get("event_n"),
        "S0_unique_symbol_date_n": (stages.get("S0") or {}).get("unique_symbol_date_n"),
        "S0_unique_setup_id_n": (stages.get("S0") or {}).get("unique_setup_id_n"),
        "S0_transition_in_n": (stages.get("S0") or {}).get("transition_in_n"),
        "S0_transition_out_n": (stages.get("S0") or {}).get("transition_out_n"),
        "S1_event_n": (stages.get("S1") or {}).get("event_n"),
        "S1_unique_symbol_date_n": (stages.get("S1") or {}).get("unique_symbol_date_n"),
        "S1_unique_setup_id_n": (stages.get("S1") or {}).get("unique_setup_id_n"),
        "S1_transition_in_n": (stages.get("S1") or {}).get("transition_in_n"),
        "S1_transition_out_n": (stages.get("S1") or {}).get("transition_out_n"),
        "S2_event_n": (stages.get("S2") or {}).get("event_n"),
        "S2_unique_symbol_date_n": (stages.get("S2") or {}).get("unique_symbol_date_n"),
        "S2_unique_setup_id_n": (stages.get("S2") or {}).get("unique_setup_id_n"),
        "S2_transition_in_n": (stages.get("S2") or {}).get("transition_in_n"),
        "S2_transition_out_n": (stages.get("S2") or {}).get("transition_out_n"),
        "S3_event_n": (stages.get("S3") or {}).get("event_n"),
        "S3_unique_symbol_date_n": (stages.get("S3") or {}).get("unique_symbol_date_n"),
        "S3_unique_setup_id_n": (stages.get("S3") or {}).get("unique_setup_id_n"),
        "S3_transition_in_n": (stages.get("S3") or {}).get("transition_in_n"),
        "S3_transition_out_n": (stages.get("S3") or {}).get("transition_out_n"),
        "S4_event_n": (stages.get("S4") or {}).get("event_n"),
        "S4_unique_symbol_date_n": (stages.get("S4") or {}).get("unique_symbol_date_n"),
        "S4_unique_setup_id_n": (stages.get("S4") or {}).get("unique_setup_id_n"),
        "S4_transition_in_n": (stages.get("S4") or {}).get("transition_in_n"),
        "S4_transition_out_n": (stages.get("S4") or {}).get("transition_out_n"),
        "S1_without_S0_n": inv.get("S1_without_S0_n"),
        "S2_without_S1_n": inv.get("S2_without_S1_n"),
        "S3_without_S2_n": inv.get("S3_without_S2_n"),
        "S4_without_S3_n": inv.get("S4_without_S3_n"),
        "Any_setup_identity_mismatch": ident.get("setup_identity_mismatch"),
        "Any_legacy_V3_V3_2_rule_leakage": leak.get("legacy_rule_leakage"),
        "NO_MEANINGFUL_ROOM_explicitly_part_of_frozen_V4_spec": leak.get("NO_MEANINGFUL_ROOM_explicitly_in_frozen_v4_spec"),
        "STRUCTURALLY_BLOCKED_explicitly_part_of_frozen_V4_spec": leak.get("STRUCTURALLY_BLOCKED_explicitly_in_frozen_v4_spec"),
        "Why_only_7_of_19_CLEAR_reached_S4": (par.get("spec_17_vs_machine_7") or {}).get("why"),
        "CLEAR_miss_upstream_S1": int(miss.get("S1") or 0),
        "CLEAR_miss_S2": int(miss.get("S2") or 0),
        "CLEAR_miss_S3": int(miss.get("S3") or 0),
        "CLEAR_miss_S4": int(miss.get("S4") or 0),
        "mismatch_implementation_bug_n": int(s1c.get("IMPLEMENTATION_BUG") or 0),
        "mismatch_spec_ambiguity_n": int(s1c.get("SPEC_AMBIGUITY") or 0),
        "mismatch_expected_threshold_disagreement_n": int(s1c.get("EXPECTED_THRESHOLD_DISAGREEMENT") or 0),
        "6787_location_mismatch_cause": par.get("6787_cause"),
        "6963_location_mismatch_cause": par.get("6963_cause"),
        "6857_location_mismatch_cause": par.get("6857_cause"),
        "9432_failure_layer_mismatch_cause": s4_9432.get("cause"),
        "Negative_final_reject_accuracy": par.get("negative_final_reject_accuracy"),
        "Negative_correct_layer_accuracy": par.get("negative_correct_layer_accuracy"),
        "E0_invariant_valid": e01.get("e0_invariant_valid"),
        "E1_invariant_valid": e01.get("e1_invariant_valid"),
        "SAME_BAR_ENTRY": e01.get("SAME_BAR_ENTRY"),
        "Any_future_outcome_used": False,
        "Any_PnL": False,
        "Prospective_data_consumed": False,
        "Old_Confirmation_opened": False,
        "Frozen_Validation_opened": False,
        "submit_cancel_live": "0/0/0",
        "VERDICT": dec.get("VERDICT"),
        "NEXT": dec.get("NEXT"),
    }


def build_sheets(report: dict[str, Any], answers: dict[str, Any], safety: dict[str, Any]) -> dict[str, tuple[list[str], list[list[Any]]]]:
    par = dict(report.get("parity") or {})
    leak = dict(report.get("leakage") or {})
    c = dict(report.get("counts") or {})
    inv = dict(report.get("invariants") or {})
    ident = dict(report.get("identity") or {})
    stages = dict(c.get("stages") or {})
    count_rows = []
    for name in ("S0", "S1", "S2", "S3", "S4"):
        st = dict(stages.get(name) or {})
        count_rows.append(
            [
                name,
                st.get("meaning"),
                st.get("event_n"),
                st.get("unique_symbol_date_n"),
                st.get("unique_setup_id_n"),
                st.get("transition_in_n"),
                st.get("transition_out_n"),
                st.get("is_strict_funnel_count"),
            ]
        )
    return {
        "Binding": (
            ["key", "value"],
            _kv_rows(
                {
                    "ok": (report.get("bind") or {}).get("ok"),
                    "live_v4": (report.get("bind") or {}).get("live_v4_machine_sha256"),
                    "frozen": report.get("FROZEN_V4_SHA"),
                    "unchanged": report.get("v4_source_unchanged"),
                }
            ),
        ),
        "Counts": (
            ["stage", "meaning", "event_n", "unique_symbol_date_n", "unique_setup_id_n", "transition_in_n", "transition_out_n", "strict_funnel"],
            count_rows,
        ),
        "Invariants": (
            ["check", "n", "required_zero"],
            [
                ["S1_without_S0", inv.get("S1_without_S0_n"), True],
                ["S2_without_S1", inv.get("S2_without_S1_n"), True],
                ["S3_without_S2", inv.get("S3_without_S2_n"), True],
                ["S4_without_S3", inv.get("S4_without_S3_n"), True],
            ],
        ),
        "Identity": (["key", "value"], _kv_rows({k: ident.get(k) for k in ident if k not in ("missing_identity_field_ids", "s1_dir_invalid_ids")})),
        "Leakage": (
            ["rule", "in_impl", "in_spec", "flag", "origin"],
            [
                [r.get("rule"), r.get("in_v4_location_py"), r.get("explicitly_in_frozen_v4_spec"), r.get("flag"), r.get("origin")]
                for r in list(leak.get("rules") or [])
            ],
        ),
        "S1_Parity": (
            ["rca_id", "symbol", "date", "human", "machine_S1", "machine_state", "class"],
            [
                [r.get("rca_id"), r.get("symbol"), r.get("date"), r.get("human_opening_state"), r.get("machine_S1"), r.get("machine_opening_state"), r.get("class")]
                for r in list(par.get("s1_mismatches") or [])
            ],
        ),
        "S2_Focus": (
            ["case", "json"],
            [
                ["6787", par.get("6787_cause")],
                ["6963", par.get("6963_cause")],
                ["6857", par.get("6857_cause")],
                ["9432", par.get("9432")],
            ],
        ),
        "S3_Parity": (["key", "value"], _kv_rows(dict(par.get("s3") or {}))),
        "S4_CLEAR": (
            ["rca_id", "symbol", "date", "layer", "class", "death", "family"],
            [
                [r.get("rca_id"), r.get("symbol"), r.get("date"), r.get("failure_layer"), r.get("class"), r.get("machine_death"), r.get("location_family")]
                for r in list(par.get("clear_misses") or [])
            ],
        ),
        "Negatives": (
            ["rca_id", "symbol", "date", "human_layer", "machine_layer", "CORRECT_FINAL_REJECT", "CORRECT_FAILURE_LAYER", "S4", "death"],
            [
                [n.get("rca_id"), n.get("symbol"), n.get("date"), n.get("fail_layer"), n.get("machine_fail_layer"), n.get("CORRECT_FINAL_REJECT"), n.get("CORRECT_FAILURE_LAYER"), n.get("machine_S4"), n.get("machine_death")]
                for n in list(par.get("negatives") or [])
            ],
        ),
        "E0_E1": (["key", "value"], _kv_rows(dict(par.get("e0_e1") or {}))),
        "Decision": (["key", "value"], _kv_rows(answers)),
        "Safety": (["key", "value"], _kv_rows(safety)),
    }


def write_artifacts(report: dict[str, Any], answers: dict[str, Any], safety: dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    payload = _json_sanitize({**report, "answers": answers, "safety": safety})
    (OUT / "report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md = report.get("_markdown") or ""
    (OUT / "report.md").write_text(str(md), encoding="utf-8")
    wb = Workbook()
    first = True
    sheets = build_sheets(report, answers, safety)
    for name in SHEET_ORDER:
        headers, rows = sheets[name]
        ws = wb.active if first else wb.create_sheet(name)
        if first:
            ws.title = name
            first = False
        _sheet(ws, headers, rows)
    wb.save(OUT / "audit.xlsx")
