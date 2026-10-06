"""Write report.json / report.md / audit.xlsx only under exit_lifecycle_branch_rca/."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_redesign.isolation import LIFECYCLE_OUT
from research.simple_tech_redesign.exit_lifecycle_spec import ANALYSIS_ID, P_SEQUENCE_IDS, PATH_TYPES, U_SEQUENCE_IDS

SHEET_ORDER = (
    "summary",
    "integrity",
    "branch_decomposition",
    "branch_u",
    "branch_p",
    "class_comparison",
    "core_added",
    "day_robustness",
    "inventory",
    "trade_audit",
)

REQUIRED_KEYS = (
    "V27_FILL_IDENTITY_PARITY",
    "SIGNAL_N",
    "EXECUTION_EVALUABLE_N",
    "CORE_FILL_N",
    "ADDED_FILL_N",
    "TOTAL_RESEARCH_FILL_N",
    "U_SUPPORTED",
    "P_SUPPORTED",
    "EXIT_POLICY_CREATED",
    "ENTRY_CHANGED",
    "SIZING_CHANGED",
    "TRUE_OOS",
    "NON_INTERFERENCE_PASS",
    "VERDICT",
    "NEXT",
)


def _test_row(t: dict[str, Any]) -> dict[str, Any]:
    day = dict(t.get("day") or {})
    return {
        "seq_id": t.get("seq_id"),
        "SUPPORTED": t.get("SUPPORTED"),
        "floors_ok": t.get("floors_ok"),
        "direction_ok": t.get("direction_ok"),
        "core_ok": t.get("core_ok"),
        "added_ok": t.get("added_ok"),
        "day_ok": t.get("day_ok"),
        "fail_n": t.get("fail_n"),
        "keep_n": t.get("keep_n"),
        "fail_hit_n": t.get("fail_hit_n"),
        "keep_hit_n": t.get("keep_hit_n"),
        "fail_hit_rate": t.get("fail_hit_rate"),
        "keep_hit_rate": t.get("keep_hit_rate"),
        "bad_keep_rate_ratio": t.get("bad_keep_rate_ratio"),
        "core_keep_hit_rate": t.get("core_keep_hit_rate"),
        "added_fail_hit_rate": t.get("added_fail_hit_rate"),
        "added_keep_hit_rate": t.get("added_keep_hit_rate"),
        "agree_days": day.get("agree_days"),
        "disagree_days": day.get("disagree_days"),
        "usable_days": day.get("usable_days"),
        "day_agreement": f"{day.get('agree_days')} agree / {day.get('disagree_days')} disagree",
        "fail_symbols": t.get("fail_symbols"),
        "keep_symbols": t.get("keep_symbols"),
        "top_fail_hit_symbol": t.get("top_fail_hit_symbol"),
        "top_fail_hit_share": t.get("top_fail_hit_share"),
    }


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    LIFECYCLE_OUT.mkdir(parents=True, exist_ok=True)
    extra = [p for p in LIFECYCLE_OUT.iterdir() if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}]
    for p in extra:
        p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    summary = dict(body.get("summary") or {})
    summary.pop("trades", None)
    for key in ("u_tests", "p_tests"):
        tests = []
        for t in list(summary.get(key) or []):
            if isinstance(t, dict):
                tests.append({k: v for k, v in t.items() if k != "daily"})
        summary[key] = tests
    body["summary"] = summary
    (LIFECYCLE_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (LIFECYCLE_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(LIFECYCLE_OUT / "audit.xlsx")


def build_sheets(report: dict[str, Any], leak: dict[str, Any], reporting: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    req = dict(report.get("required") or {})
    summary = dict(report.get("summary") or {})
    u_tests = [t for t in list(summary.get("u_tests") or []) if isinstance(t, dict)]
    p_tests = [t for t in list(summary.get("p_tests") or []) if isinstance(t, dict)]
    decomp_rows = []
    for scope, key in (("ALL", "decomposition"), ("CORE", "decomposition_core"), ("ADDED", "decomposition_added")):
        body = dict(summary.get(key) or {})
        for p in PATH_TYPES:
            row = dict(body.get(p) or {})
            row["scope"] = scope
            row["path_type"] = p
            decomp_rows.append(row)
    day_rows = []
    for t in u_tests + p_tests:
        for d in list(t.get("daily") or []):
            row = dict(d)
            row["seq_id"] = t.get("seq_id")
            day_rows.append(row)
    inventory = [
        {"item": "pullback_low", "status": "AVAILABLE", "definition": "min Low of frozen PULLBACK_LOOKBACK=3 1m bars at signal"},
        {"item": "recent_causal_low", "status": "SAME_AS_PULLBACK_LOW", "definition": "no separate swing definition; not invented"},
        {"item": "EMA_structure", "status": "AVAILABLE", "definition": "trend_up / EMA9 vs EMA21"},
        {"item": "BB_state", "status": "AVAILABLE", "definition": "Close vs bb_lower / bb_mid (V26 D)"},
        {"item": "RCI_reversal", "status": "AVAILABLE", "definition": "reversal_rci and RCI9 vs frozen -80"},
        {"item": "entry_trigger_price", "status": "AVAILABLE_TAUTOLOGICAL_IN_U", "definition": "fill_price; Bid<fill is UNPROVEN itself"},
        {"item": "VWAP", "status": "AVAILABLE", "definition": "1m session VWAP from attach_indicators"},
        {"item": "HH_HL_multi_bar", "status": "UNAVAILABLE", "definition": "only adjacent 1-bar hh_hl exists"},
        {"item": "giveback_pct", "status": "UNAVAILABLE", "definition": "peak_yen is audit only"},
    ]
    return {
        "summary": kv_rows(req),
        "integrity": kv_rows({**dict(leak), **dict(reporting), "NON_INTERFERENCE_PASS": req.get("NON_INTERFERENCE_PASS")}),
        "branch_decomposition": decomp_rows or [{"empty": True}],
        "branch_u": [_test_row(t) for t in u_tests] or [{"empty": True}],
        "branch_p": [_test_row(t) for t in p_tests] or [{"empty": True}],
        "class_comparison": [_test_row(t) for t in u_tests + p_tests] or [{"empty": True}],
        "core_added": [_test_row(t) for t in u_tests + p_tests] or [{"empty": True}],
        "day_robustness": day_rows or [{"empty": True}],
        "inventory": inventory,
        "trade_audit": list(summary.get("trades") or []) or [{"empty": True}],
    }


def build_markdown(report: dict[str, Any]) -> str:
    req = dict(report.get("required") or {})
    dec = dict(report.get("decision") or {})
    summary = dict(report.get("summary") or {})
    decomp = dict(summary.get("decomposition") or {})
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"VERDICT: **{req.get('VERDICT')}**",
        f"CASE: `{dec.get('CASE')}`",
        f"NEXT: {req.get('NEXT')}",
        "",
        "Not V30. V26/V27/V28/V29 frozen. Break-even is net executable yen >= 0 under compute_pnl_yen_100 ",
        "(100 shares, fees excluded). No EXIT policy. No K / reclaim / bps / giveback search.",
        "",
        f"V27_FILL_IDENTITY_PARITY = `{req.get('V27_FILL_IDENTITY_PARITY')}`",
        f"SIGNAL_N = `{req.get('SIGNAL_N')}` CORE = `{req.get('CORE_FILL_N')}` ADDED = `{req.get('ADDED_FILL_N')}` TOTAL = `{req.get('TOTAL_RESEARCH_FILL_N')}`",
        f"BRANCH_U_N = `{summary.get('BRANCH_U_N')}` BRANCH_P_N = `{summary.get('BRANCH_P_N')}`",
        "",
        f"EARLY BE rate = `{dict(decomp.get('EARLY_FAILURE') or {}).get('break_even_reached_rate')}`",
        f"DIP BE rate = `{dict(decomp.get('DIP_THEN_RECOVERY') or {}).get('break_even_reached_rate')}`",
        f"GOOD BE rate = `{dict(decomp.get('GOOD_CONTINUATION') or {}).get('break_even_reached_rate')}`",
        f"PTF BE rate = `{dict(decomp.get('PROFIT_THEN_FAILURE') or {}).get('break_even_reached_rate')}`",
        "",
        f"U_SUPPORTED = `{req.get('U_SUPPORTED')}`",
        f"P_SUPPORTED = `{req.get('P_SUPPORTED')}`",
        f"Q1 thesis = `{dec.get('Q1_EARLY_VS_DIP_UNPROVEN')}` Q1 never-BE partition = `{dec.get('Q1_NEVER_BE_PARTITION')}`",
        f"Q2 = `{dec.get('Q2_PTF_VS_GOOD_DIP_PROVEN')}` Q3 = `{dec.get('Q3_SPLIT_CLEARER_THAN_V26_V29')}`",
        "",
        f"EXIT_POLICY_CREATED=`{req.get('EXIT_POLICY_CREATED')}` TRUE_OOS=`{req.get('TRUE_OOS')}` "
        f"NON_INTERFERENCE_PASS=`{req.get('NON_INTERFERENCE_PASS')}`",
        "",
        "STOP.",
        "",
    ]
    return "\n".join(lines)
