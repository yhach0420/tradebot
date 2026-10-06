"""Write report.json / report.md / audit.xlsx only under v22_entry_coverage_rca/."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_redesign.isolation import V22_OUT
from research.simple_tech_redesign.v22_spec import ANALYSIS_ID

SHEET_ORDER = (
    "Precommit",
    "Identity",
    "Funnel",
    "CohortA",
    "CohortB",
    "CohortC",
    "FilledPath",
    "NonfilledPath",
    "State1m3m5m",
    "MissedOpportunity",
    "Decision",
    "Reporting",
    "Integrity",
    "Non_Interference",
)

REQUIRED_KEYS = (
    "ANALYSIS_ID",
    "STRATEGY_STACK_PARITY",
    "SIGNAL_N",
    "EXECUTION_EVALUABLE_N",
    "EXECUTION_UNEVALUABLE_N",
    "E4_FILLED_N",
    "E4_NONFILLED_N",
    "FUNNEL_REASON_COUNTS",
    "FILLED_PATH_METRICS",
    "NONFILLED_PATH_METRICS",
    "1M_3M_5M_STATE_SUMMARY",
    "MISSED_OPPORTUNITY_STRUCTURE",
    "PRIMARY_ENTRY_COVERAGE_DEFICIENCY",
    "TRUE_OOS",
    "VERDICT",
    "NEXT",
)


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    V22_OUT.mkdir(parents=True, exist_ok=True)
    extra = [p for p in V22_OUT.iterdir() if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}]
    for p in extra:
        p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (V22_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (V22_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
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
    wb.save(V22_OUT / "audit.xlsx")


def _shape_lines(shape: dict[str, Any] | None, title: str) -> list[str]:
    if not shape:
        return [f"{title}: n/a (no future path)"]
    lines = [
        f"{title}: SHAPE_POSITIVE=`{shape.get('SHAPE_POSITIVE')}` KIND_VOTES=`{shape.get('KIND_VOTES')}` ROBUST=`{shape.get('SHAPE_ROBUST')}`",
    ]
    for kind, block in dict(shape.get("kinds") or {}).items():
        lines.append(
            f"- {kind}: positive_horizons={block.get('POSITIVE_HORIZON_N')} coverage={block.get('COVERAGE_FRAC')}"
        )
        for hid, pack in dict(block.get("horizons") or {}).items():
            lines.append(
                f"  +{hid}s mean={pack.get('MEAN')} median={pack.get('MEDIAN')} "
                f"pos/neg_days={pack.get('POSITIVE_DAY_N')}/{pack.get('NEGATIVE_DAY_N')} "
                f"ex_best={pack.get('EX_BEST_DAY')} ex_top3={pack.get('EX_TOP3_DAY')} drop_top_sym={pack.get('DROP_TOP_SYMBOL')}"
            )
    return lines


def build_markdown(report: dict[str, Any]) -> str:
    req = dict(report.get("required") or {})
    funnel = dict(req.get("FUNNEL_REASON_COUNTS") or {})
    miss = dict(req.get("MISSED_OPPORTUNITY_STRUCTURE") or {})
    dec = dict(report.get("decision") or {})
    pack_a = dict(report.get("cohort_a") or {})
    pack_b = dict(report.get("cohort_b") or {})
    pack_c = dict(report.get("cohort_c") or {})
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"VERDICT: **{req.get('VERDICT')}**",
        f"PRIMARY_ENTRY_COVERAGE_DEFICIENCY: `{req.get('PRIMARY_ENTRY_COVERAGE_DEFICIENCY')}`",
        f"CASE: `{req.get('CASE')}`",
        f"NEXT: {req.get('NEXT')}",
        "",
        f"STRATEGY_STACK_PARITY: `{req.get('STRATEGY_STACK_PARITY')}`",
        f"TRUE_OOS: `{req.get('TRUE_OOS')}`",
        "",
        "## Funnel",
        f"- SIGNAL_N = `{req.get('SIGNAL_N')}`",
        f"- EXECUTION_EVALUABLE_N = `{req.get('EXECUTION_EVALUABLE_N')}`",
        f"- EXECUTION_UNEVALUABLE_N = `{req.get('EXECUTION_UNEVALUABLE_N')}`",
        f"- E4_FILLED_N = `{req.get('E4_FILLED_N')}`",
        f"- E4_NONFILLED_N = `{req.get('E4_NONFILLED_N')}`",
        f"- FUNNEL_REASON_COUNTS = `{json.dumps(funnel, ensure_ascii=False, default=str)}`",
        "",
        "## Cohorts",
        f"- A filled N=`{pack_a.get('N')}` days=`{pack_a.get('DAY_N')}`",
        f"- B evaluable nonfill N=`{pack_b.get('N')}` days=`{pack_b.get('DAY_N')}`",
        f"- C unevaluable N=`{pack_c.get('N')}` days=`{pack_c.get('DAY_N')}` (no virtual fill path)",
        "",
        *_shape_lines(pack_a.get("shape"), "FILLED_PATH_METRICS"),
        "",
        *_shape_lines(pack_b.get("shape"), "NONFILLED_PATH_METRICS"),
        "",
        "## Q1–Q4",
        f"- Q1 E4_NONFILL positive path: `{dec.get('Q1_E4_NONFILL_POSITIVE_PATH')}` robust=`{dec.get('Q1_ROBUST')}`",
        f"- Q2 current signal too few as primary: `{dec.get('Q2_CURRENT_SIGNAL_TOO_FEW_PRIMARY')}`",
        f"- Q3 near-miss current pullback: `{dec.get('Q3_NEAR_MISS_PULLBACK')}`",
        f"- Q4 complementary archetype: `{dec.get('Q4_COMPLEMENTARY_ARCHETYPE')}` id=`{dec.get('Q4_ARCHETYPE_ID')}`",
        "",
        "## Missed opportunity (frozen V7 TF1 Ask→Bid 60/180/300, not EXIT)",
        f"- Q3=`{miss.get('Q3_NEAR_MISS_PULLBACK')}` Q4=`{miss.get('Q4_COMPLEMENTARY_ARCHETYPE')}` `{miss.get('Q4_ARCHETYPE_ID')}`",
        "",
        "## Locks",
        "- ENTRY unchanged. EXIT unchanged. Sizing unchanged.",
        "- FIXED180 is historical benchmark only. 180s EXIT PnL was not a selection metric.",
        "- Time horizons are diagnostic rulers only.",
        "- TRUE_OOS=false. RUNTIME_CANDIDATE=false. POSITION_SIZING_SPEC_FROZEN=false.",
        "",
        "STOP.",
        "",
    ]
    return "\n".join(lines)
