"""Write report.json / report.md / audit.xlsx only under v29_terminal_failure_sequence_rca/."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_redesign.isolation import V29_OUT
from research.simple_tech_redesign.v29_spec import ANALYSIS_ID, SEQUENCE_IDS

SHEET_ORDER = (
    "summary",
    "integrity",
    "event_sequence",
    "family_A_reclaim",
    "family_B_price_structure",
    "family_C_recovery_participation",
    "class_comparison",
    "core_added",
    "day_robustness",
    "v28_false_positive_audit",
)

REQUIRED_KEYS = (
    "V27_FILL_IDENTITY_PARITY",
    "V28_FILL_IDENTITY_PARITY",
    "SIGNAL_N",
    "EXECUTION_EVALUABLE_N",
    "CORE_FILL_N",
    "ADDED_FILL_N",
    "TOTAL_RESEARCH_FILL_N",
    "DAMAGE_ONSET_N",
    "K6_USED_FOR_SELECTION",
    "FAMILY_B_SWING_AVAILABLE",
    "SUPPORTED_SEQUENCES",
    "PRIMARY_EXIT_SEQUENCE_MECHANISM",
    "EXIT_POLICY_CREATED",
    "ENTRY_CHANGED",
    "SIZING_CHANGED",
    "TRUE_OOS",
    "NON_INTERFERENCE_PASS",
    "VERDICT",
    "NEXT",
)

FAMILY_A_IDS = (
    "A_NO_RECLAIM",
    "A_RECLAIM_THEN_MAINTAIN",
    "A_RECLAIM_THEN_RELOSS",
    "A_RECLAIM_THEN_RELOSS_GIVEN_RECLAIM",
)
FAMILY_B_IDS = (
    "B_PRICE_NEVER_LOST",
    "B_PRICE_LOST_NO_RECLAIM",
    "B_PRICE_RECLAIM_THEN_MAINTAIN",
    "B_PRICE_RECLAIM_THEN_RELOSS",
)
FAMILY_C_IDS = (
    "C_NO_RECLAIM_ATTEMPT",
    "C_RECLAIM_ATTEMPT_WITH_VOL_DET",
    "C_RECLAIM_ATTEMPT_WITHOUT_VOL_DET",
)


def _tests(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [t for t in list((report.get("summary") or {}).get("tests") or []) if isinstance(t, dict)]


def _test_row(t: dict[str, Any]) -> dict[str, Any]:
    day = dict(t.get("day") or {})
    return {
        "seq_id": t.get("seq_id"),
        "SUPPORTED": t.get("SUPPORTED"),
        "distinct_from_v27": t.get("distinct_from_v27"),
        "failure_like": t.get("failure_like"),
        "floors_ok": t.get("floors_ok"),
        "direction_ok": t.get("direction_ok"),
        "core_ok": t.get("core_ok"),
        "added_ok": t.get("added_ok"),
        "day_ok": t.get("day_ok"),
        "protected_n": t.get("protected_n"),
        "failure_n": t.get("failure_n"),
        "sequence_hit_protected_n": t.get("sequence_hit_protected_n"),
        "sequence_hit_failure_n": t.get("sequence_hit_failure_n"),
        "protected_hit_rate": t.get("protected_hit_rate"),
        "failure_hit_rate": t.get("failure_hit_rate"),
        "bad_protected_rate_ratio": t.get("bad_protected_rate_ratio"),
        "core_protected_hit_rate": t.get("core_protected_hit_rate"),
        "core_good_n": t.get("core_good_n"),
        "core_good_hit_n": t.get("core_good_hit_n"),
        "core_good_hit_rate": t.get("core_good_hit_rate"),
        "core_dip_n": t.get("core_dip_n"),
        "core_dip_hit_n": t.get("core_dip_hit_n"),
        "core_dip_hit_rate": t.get("core_dip_hit_rate"),
        "added_failure_hit_rate": t.get("added_failure_hit_rate"),
        "added_protected_hit_rate": t.get("added_protected_hit_rate"),
        "AM_N": t.get("AM_N"),
        "PM_N": t.get("PM_N"),
        "fail_days": t.get("fail_days"),
        "prot_days": t.get("prot_days"),
        "fail_symbols": t.get("fail_symbols"),
        "prot_symbols": t.get("prot_symbols"),
        "symbol_coverage": t.get("symbol_coverage"),
        "fail_hit_symbol_n": t.get("fail_hit_symbol_n"),
        "top_fail_hit_symbol": t.get("top_fail_hit_symbol"),
        "top_fail_hit_share": t.get("top_fail_hit_share"),
        "agree_days": day.get("agree_days"),
        "disagree_days": day.get("disagree_days"),
        "usable_days": day.get("usable_days"),
        "day_agreement": f"{day.get('agree_days')} agree / {day.get('disagree_days')} disagree",
    }


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    V29_OUT.mkdir(parents=True, exist_ok=True)
    extra = [p for p in V29_OUT.iterdir() if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}]
    for p in extra:
        p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    summary = dict(body.get("summary") or {})
    summary.pop("event_rows", None)
    tests = []
    for t in list(summary.get("tests") or []):
        if isinstance(t, dict):
            tests.append({k: v for k, v in t.items() if k != "daily"})
    summary["tests"] = tests
    v28 = dict(summary.get("V28_TECH_EXIT_AUDIT") or {})
    recov = dict(summary.get("K6_RECOVERED_BEFORE_AUDIT") or {})
    v28.pop("trades", None)
    recov.pop("trades", None)
    summary["V28_TECH_EXIT_AUDIT"] = v28
    summary["K6_RECOVERED_BEFORE_AUDIT"] = recov
    body["summary"] = summary
    (V29_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (V29_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
    wb = Workbook()
    first = True
    for name in SHEET_ORDER:
        rows = sheets.get(name) or [{"empty": True}]
        if first:
            ws = wb.active
            ws.title = name[:31]
            first = False
        else:
            ws = wb.create_sheet(name[:31])
        _sheet(ws, rows)
    wb.save(V29_OUT / "audit.xlsx")


def build_sheets(report: dict[str, Any], leak: dict[str, Any], reporting: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    req = dict(report.get("required") or {})
    summary = dict(report.get("summary") or {})
    tests = _tests(report)
    by_id = {str(t.get("seq_id") or ""): t for t in tests}
    events = list(summary.get("event_rows") or [])
    day_rows = []
    for t in tests:
        for d in list(t.get("daily") or []):
            row = dict(d)
            row["seq_id"] = t.get("seq_id")
            day_rows.append(row)
    v28 = dict(summary.get("V28_TECH_EXIT_AUDIT") or {})
    recov = dict(summary.get("K6_RECOVERED_BEFORE_AUDIT") or {})
    v28_sheet: list[dict[str, Any]] = [
        {"block": "TECH_EXIT", "path_type": "ALL", "n": v28.get("n"), "note": "V28 secondary diagnostic; not selection"},
    ]
    for p, body in dict(v28.get("by_path") or {}).items():
        v28_sheet.append(
            {
                "block": "TECH_EXIT",
                "path_type": p,
                "n": dict(body or {}).get("n"),
                "family_a": dict(body or {}).get("family_a"),
                "seq_hits": dict(body or {}).get("seq_hits"),
            }
        )
    for tr in list(v28.get("trades") or []):
        row = dict(tr)
        row["block"] = "TECH_EXIT_TRADE"
        v28_sheet.append(row)
    v28_sheet.append(
        {
            "block": "RECOVERED_BEFORE_K6",
            "path_type": "ALL",
            "n": recov.get("n"),
            "family_a": recov.get("family_a"),
            "by_class": recov.get("by_class"),
            "seq_hits": recov.get("seq_hits"),
            "note": "Trade-level analog of V28 episode recoveries; not a new rule",
        }
    )
    for tr in list(recov.get("trades") or []):
        row = dict(tr)
        row["block"] = "RECOVERED_BEFORE_TRADE"
        v28_sheet.append(row)
    return {
        "summary": kv_rows(req),
        "integrity": kv_rows({**dict(leak), **dict(reporting), "NON_INTERFERENCE_PASS": req.get("NON_INTERFERENCE_PASS")}),
        "event_sequence": events or [{"empty": True}],
        "family_A_reclaim": [_test_row(by_id[s]) for s in FAMILY_A_IDS if s in by_id] or [{"empty": True}],
        "family_B_price_structure": [_test_row(by_id[s]) for s in FAMILY_B_IDS if s in by_id]
        + [{"FAMILY_B_SWING_AVAILABLE": summary.get("FAMILY_B_SWING_AVAILABLE"), "reason": summary.get("FAMILY_B_SWING_REASON")}],
        "family_C_recovery_participation": [_test_row(by_id[s]) for s in FAMILY_C_IDS if s in by_id] or [{"empty": True}],
        "class_comparison": [_test_row(by_id[s]) for s in SEQUENCE_IDS if s in by_id] or [{"empty": True}],
        "core_added": [_test_row(by_id[s]) for s in SEQUENCE_IDS if s in by_id] or [{"empty": True}],
        "day_robustness": day_rows or [{"empty": True}],
        "v28_false_positive_audit": v28_sheet or [{"empty": True}],
    }


def build_markdown(report: dict[str, Any]) -> str:
    req = dict(report.get("required") or {})
    dec = dict(report.get("decision") or {})
    summary = dict(report.get("summary") or {})
    prim = dict(dec.get("PRIMARY_EXIT_SEQUENCE_MECHANISM") or {})
    day = dict(prim.get("day") or {})
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"VERDICT: **{req.get('VERDICT')}**",
        f"CASE: `{dec.get('CASE')}`",
        f"NEXT: {req.get('NEXT')}",
        "",
        "V26/V27/V28 official artifacts frozen. Origin is first completed 3m bar with EMA9<=EMA21. "
        "K6 is a result column only. No EXIT policy. No PnL selection. No threshold or combination search.",
        "",
        f"V27_FILL_IDENTITY_PARITY = `{req.get('V27_FILL_IDENTITY_PARITY')}`",
        f"SIGNAL_N = `{req.get('SIGNAL_N')}` EXECUTION_EVALUABLE_N = `{req.get('EXECUTION_EVALUABLE_N')}`",
        f"CORE_FILL_N = `{req.get('CORE_FILL_N')}` ADDED_FILL_N = `{req.get('ADDED_FILL_N')}` "
        f"TOTAL_RESEARCH_FILL_N = `{req.get('TOTAL_RESEARCH_FILL_N')}`",
        f"DAMAGE_ONSET_N = `{summary.get('DAMAGE_ONSET_N')}` NO_DAMAGE_N = `{summary.get('NO_DAMAGE_N')}`",
        f"K6_USED_FOR_SELECTION = `{req.get('K6_USED_FOR_SELECTION')}`",
        f"FAMILY_B_SWING_AVAILABLE = `{summary.get('FAMILY_B_SWING_AVAILABLE')}`",
        "",
        f"PRIMARY_EXIT_SEQUENCE_MECHANISM = `{prim.get('seq_id')}`",
        f"SUPPORTED_SEQUENCES = `{req.get('SUPPORTED_SEQUENCES')}`",
        f"protected_hit_rate = `{prim.get('protected_hit_rate')}` failure_hit_rate = `{prim.get('failure_hit_rate')}`",
        f"day agreement = `{day.get('agree_days')} agree / {day.get('disagree_days')} disagree`",
        f"core_good_hit_rate = `{prim.get('core_good_hit_rate')}` core_dip_hit_rate = `{prim.get('core_dip_hit_rate')}`",
        "",
        f"VS_V27: {dec.get('VS_V27_SIMPLE_RECOVERY')}",
        "",
        f"EXIT_POLICY_CREATED=`{req.get('EXIT_POLICY_CREATED')}` ENTRY_CHANGED=`{req.get('ENTRY_CHANGED')}` "
        f"SIZING_CHANGED=`{req.get('SIZING_CHANGED')}` TRUE_OOS=`{req.get('TRUE_OOS')}` "
        f"NON_INTERFERENCE_PASS=`{req.get('NON_INTERFERENCE_PASS')}`",
        "",
        "STOP.",
        "",
    ]
    return "\n".join(lines)
