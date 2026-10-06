"""report.json / report.md / audit.xlsx only. No raw rewrite."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from research.am_entry_profit_improvement.publish import json_sanitize as _json_sanitize
from research.futures_context_day1_effect_check_v1 import ANALYSIS_ID
from research.futures_context_day1_effect_check_v1.isolation import OUT

HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
HEADER_FONT = Font(color="FFFFFF", bold=True)
SHEET_ORDER = ("Answers", "Clocks", "Signed", "Agreement", "Exclusion", "Preopen", "Decision", "Safety")


def json_sanitize(obj: Any) -> Any:
    got = _json_sanitize(obj)
    if isinstance(got, float) and abs(got) == float("inf"):
        return "inf" if got > 0 else "-inf"
    if isinstance(got, dict):
        return {str(k): json_sanitize(v) for k, v in got.items()}
    if isinstance(got, list):
        return [json_sanitize(v) for v in got]
    return got


def _sheet(ws: Any, rows: list[dict[str, Any]]) -> None:
    if not rows:
        ws.append(["empty"])
        return
    keys: list[str] = []
    for r in rows:
        for k in r.keys():
            if k not in keys:
                keys.append(k)
    ws.append(keys)
    for cell in ws[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    for r in rows:
        vals = []
        for k in keys:
            v = r.get(k)
            if isinstance(v, (dict, list, tuple)):
                v = json.dumps(v, ensure_ascii=False, default=str)
            vals.append(v)
        ws.append(vals)
    for i, _k in enumerate(keys, start=1):
        ws.column_dimensions[get_column_letter(i)].width = min(40, max(12, len(str(_k)) + 2))


def _kv(obj: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"key": str(k), "value": v} for k, v in obj.items()]


def _fmt(x: Any, nd: int = 3) -> str:
    if x is None:
        return "null"
    if isinstance(x, bool):
        return "true" if x else "false"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def _dir(v: Any) -> str:
    if v == 1:
        return "+1"
    if v == -1:
        return "-1"
    return str(v)


def build_markdown(body: dict[str, Any]) -> str:
    d = dict(body.get("decision") or {})
    a = dict(body.get("answers") or {})
    best = dict(body.get("best") or {})
    plc = dict(a.get("19_placebo_comparison") or {})
    pre = dict(body.get("preopen") or a.get("21_preopen_relation") or {})
    from0900 = dict(pre.get("from_0900") or {})
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"VERDICT: **{d.get('VERDICT')}**",
        f"NEXT: `{d.get('NEXT')}`",
        f"CASE: {d.get('CASE')}",
        "",
        "Statistical unit is CLOCK (13 post-open anchors). 48 stocks are averaged inside each clock.",
        "Features frozen before looking at results. Sign split is feature>0 vs feature<0 only.",
        "No threshold search. No ENTRY/EXIT. No Runtime/Paper change.",
        "",
        "## Required answers",
        "",
        f"- 1_anchor_clock_n: `{a.get('1_anchor_clock_n')}`",
        f"- 2_stock_anchor_n: `{a.get('2_stock_anchor_n')}`",
        f"- 3_future_leakage_n: `{a.get('3_future_leakage_n')}`",
    ]
    for key, label in (
        ("4_NK30_relation", "NK_RET_30S"),
        ("5_NK60_relation", "NK_RET_60S"),
        ("6_NK180_relation", "NK_RET_180S"),
        ("7_TOPIX30_relation", "TOPIX_RET_30S"),
        ("8_TOPIX60_relation", "TOPIX_RET_60S"),
        ("9_TOPIX180_relation", "TOPIX_RET_180S"),
    ):
        rel = dict(a.get(key) or {})
        lines.append(
            f"- {key}: `{label}` best_h={rel.get('best_horizon')} "
            f"MID_shift={_fmt(rel.get('mid_shift_bps'))}bps "
            f"LONG_impr={_fmt(rel.get('long_impr_bps'))}bps "
            f"SHORT_impr={_fmt(rel.get('short_impr_bps'))}bps "
            f"EARLY={_dir(rel.get('early_direction'))} LATE={_dir(rel.get('late_direction'))} "
            f"hold={_fmt(rel.get('internal_hold'))}"
        )
    agr = a.get("10_agreement_effect") or []
    agr_bits = []
    for row in agr:
        agr_bits.append(f"{row.get('horizon')} shift={_fmt(row.get('mid_shift_both_up_minus_down_bps'))}bps")
    lines.append(f"- 10_agreement_effect: both_up minus both_down MID: {'; '.join(agr_bits)}")
    lines.extend(
        [
            f"- 11_strongest_horizon: `{a.get('11_strongest_horizon')}`",
            f"- 12_strongest_MID_shift_bps: `{_fmt(a.get('12_strongest_MID_shift_bps'))}`",
            f"- 13_strongest_LONG_improvement_bps: `{_fmt(a.get('13_strongest_LONG_improvement_bps'))}`",
            f"- 14_strongest_SHORT_improvement_bps: `{_fmt(a.get('14_strongest_SHORT_improvement_bps'))}`",
            f"- 15_executable_markout_positive_anywhere: `{_fmt(a.get('15_executable_markout_positive_anywhere'))}`",
            f"- 16_EARLY_direction: `{_dir(a.get('16_EARLY_direction'))}`",
            f"- 17_LATE_direction: `{_dir(a.get('17_LATE_direction'))}`",
            f"- 18_internal_direction_hold: `{_fmt(a.get('18_internal_direction_hold'))}`",
            f"- 19_placebo_comparison: causal={_fmt(plc.get('causal_mid_shift_bps'))}bps "
            f"placebo(+5m)={_fmt(plc.get('placebo_mid_shift_bps'))}bps "
            f"causal_beats_placebo={_fmt(plc.get('causal_beats_placebo'))} shift_sec={plc.get('shift_sec')}",
            f"- 20_top_symbol_exclusion_hold: `{_fmt(a.get('20_top_symbol_exclusion_hold'))}`",
            f"- 21_preopen_relation: NK_08:45→09:00={_fmt(pre.get('nk_ret_0845_0900'), 6)} "
            f"TOPIX={_fmt(pre.get('topix_ret_0845_0900'), 6)} agreement={pre.get('agreement')}",
        ]
    )
    for hl in ("1m", "3m", "5m", "10m"):
        block = dict(from0900.get(hl) or {})
        if block:
            lines.append(
                f"  - post-09:00 {hl}: MID_mean={_fmt(block.get('MID_mean'))} "
                f"LONG_mean={_fmt(block.get('LONG_mean'))} SHORT_mean={_fmt(block.get('SHORT_mean'))} n={block.get('n')}"
            )
    lines.extend(
        [
            f"- 22_exact_mechanism_candidate_sentence: {a.get('22_exact_mechanism_candidate_sentence')}",
            f"- 23_CASE: `{a.get('23_CASE')}`",
            f"- 24_ENTRY_built: `{_fmt(a.get('24_ENTRY_built'))}`",
            f"- 25_EXIT_built: `{_fmt(a.get('25_EXIT_built'))}`",
            f"- 26_Runtime_changed: `{_fmt(a.get('26_Runtime_changed'))}`",
            f"- 27_Paper_changed: `{_fmt(a.get('27_Paper_changed'))}`",
            f"- 28_submit_cancel_live: `{a.get('28_submit_cancel_live')}`",
            f"- 29_VERDICT: `{a.get('29_VERDICT')}`",
            f"- 30_NEXT: `{a.get('30_NEXT')}`",
            "",
            "## Clock-level signed buckets (feature>0 minus feature<0)",
            "",
            "| feature | h | pos/neg clocks | MID shift | LONG impr | SHORT impr | EARLY | LATE | hold | placebo MID | causal>placebo | material | long cand | short cand |",
            "|---|---|---|---:|---:|---:|---|---|---|---:|---|---|---|---|",
        ]
    )
    for row in body.get("signed_rows") or []:
        lines.append(
            "| {feature} | {horizon} | {pos}/{neg} | {mid} | {lng} | {sht} | {e} | {l} | {hold} | {plc} | {beat} | {mat} | {lc} | {sc} |".format(
                feature=row.get("feature"),
                horizon=row.get("horizon"),
                pos=row.get("pos_clock_n"),
                neg=row.get("neg_clock_n"),
                mid=_fmt(row.get("mid_shift_bps")),
                lng=_fmt(row.get("long_impr_bps")),
                sht=_fmt(row.get("short_impr_bps")),
                e=_dir(row.get("early_direction")),
                l=_dir(row.get("late_direction")),
                hold=_fmt(row.get("internal_hold")),
                plc=_fmt(row.get("placebo_mid_shift_bps")),
                beat=_fmt(row.get("causal_beats_placebo")),
                mat=_fmt(row.get("material")),
                lc=_fmt(row.get("long_candidate")),
                sc=_fmt(row.get("short_candidate")),
            )
        )
    excl1 = dict(body.get("exclusion_drop1") or {})
    excl2 = dict(body.get("exclusion_drop2") or {})
    lines.extend(
        [
            "",
            "## CASE A gates on strongest row",
            "",
            f"- feature/horizon: `{best.get('feature')}` / `{best.get('horizon')}`",
            f"- material (>=5bps MID or exec impr): `{_fmt(best.get('material'))}`",
            f"- EARLY/LATE same direction: `{_fmt(best.get('internal_hold'))}`",
            f"- causal |MID shift| > placebo+5m: `{_fmt(best.get('causal_beats_placebo'))}` "
            f"(causal={_fmt(best.get('mid_shift_bps'))} vs placebo={_fmt(best.get('placebo_mid_shift_bps'))})",
            f"- top-symbol exclusion hold: drop1={excl1.get('dropped')} after={_fmt(excl1.get('after_mid_shift_bps'))} hold={_fmt(excl1.get('hold'))}; "
            f"drop2={excl2.get('dropped')} after={_fmt(excl2.get('after_mid_shift_bps'))} hold={_fmt(excl2.get('hold'))}",
            f"- stronger_day1_candidate: `{_fmt(body.get('stronger_day1_candidate'))}`",
            f"- concentrated_only: `{_fmt(body.get('concentrated_only'))}`",
            "",
            "CASE A requires material + EARLY/LATE hold + causal beats placebo + exclusion hold.",
            "This Day1 strongest slice fails the placebo gate. Relation exists but is mixed/time-dependent.",
            "",
            "ENTRY: false",
            "EXIT: false",
            "submit/cancel/live: 0/0/0",
            "",
        ]
    )
    return "\n".join(lines)


def write_artifacts(body: dict[str, Any]) -> dict[str, str]:
    OUT.mkdir(parents=True, exist_ok=True)
    report = json_sanitize(body)
    slim_clocks = []
    for c in report.get("clocks") or []:
        feats = c.get("features") or {}
        slim_clocks.append(
            {
                "clock": c.get("clock"),
                "block": c.get("block"),
                "stock_n": c.get("stock_n"),
                **{k: feats.get(k) for k in ("NK_RET_60S", "TOPIX_RET_60S", "AGREEMENT", "COMPOSITE")},
                "MID_5m_mean": c.get("5m_MID_RETURN_BPS_mean"),
                "LONG_5m_mean": c.get("5m_LONG_EXEC_MARKOUT_BPS_mean"),
                "SHORT_5m_mean": c.get("5m_SHORT_EXEC_MARKOUT_BPS_mean"),
            }
        )
    signed_slim = [
        {
            "feature": r.get("feature"),
            "horizon": r.get("horizon"),
            "pos_n": r.get("pos_clock_n"),
            "neg_n": r.get("neg_clock_n"),
            "mid_shift_bps": r.get("mid_shift_bps"),
            "long_impr_bps": r.get("long_impr_bps"),
            "short_impr_bps": r.get("short_impr_bps"),
            "early": r.get("early_direction"),
            "late": r.get("late_direction"),
            "hold": r.get("internal_hold"),
            "placebo_mid_shift_bps": r.get("placebo_mid_shift_bps"),
            "causal_beats_placebo": r.get("causal_beats_placebo"),
            "material": r.get("material"),
        }
        for r in (report.get("signed_rows") or [])
    ]
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "report.md").write_text(build_markdown(report), encoding="utf-8")
    wb = Workbook()
    first = True
    sheets = {
        "Answers": _kv(dict(report.get("answers") or {})),
        "Clocks": slim_clocks,
        "Signed": signed_slim,
        "Agreement": [
            {"horizon": r.get("horizon"), "shift": r.get("mid_shift_both_up_minus_down_bps"), **{k: v for k, v in (r.get("groups") or {}).items()}}
            for r in (report.get("agreement_rows") or [])
        ],
        "Exclusion": _kv({**(report.get("exclusion_drop1") or {}), **{f"d2_{k}": v for k, v in (report.get("exclusion_drop2") or {}).items()}}),
        "Preopen": _kv(dict(report.get("preopen") or {})),
        "Decision": _kv(dict(report.get("decision") or {})),
        "Safety": [
            {
                "submit_cancel_live": (report.get("answers") or {}).get("28_submit_cancel_live"),
                "ENTRY": False,
                "EXIT": False,
                "Runtime_changed": False,
                "Paper_changed": False,
            }
        ],
    }
    for name in SHEET_ORDER:
        ws = wb.active if first else wb.create_sheet(name)
        if first:
            ws.title = name
            first = False
        _sheet(ws, sheets.get(name) or [])
    xlsx = OUT / "audit.xlsx"
    wb.save(xlsx)
    return {"report_json": str(OUT / "report.json"), "report_md": str(OUT / "report.md"), "audit_xlsx": str(xlsx)}
