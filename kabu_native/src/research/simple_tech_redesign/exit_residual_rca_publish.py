"""Write report.json / report.md / audit.xlsx only under exit_residual_loss_architecture_rca/."""
from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook

from research.am_entry_profit_improvement.publish import json_sanitize
from research.simple_tech_entry_family.publish import _sheet, kv_rows
from research.simple_tech_redesign.exit_residual_rca_spec import ANALYSIS_ID, RANK_METRICS, RESIDUAL_CLASSES
from research.simple_tech_redesign.isolation import EXIT_RESIDUAL_RCA_OUT

SHEET_ORDER = (
    "summary",
    "integrity",
    "decision",
    "identity",
    "classes",
    "ranks",
    "questions",
    "trades",
)


def _cell(v: Any) -> Any:
    if isinstance(v, (dict, list, tuple)):
        return json.dumps(json_sanitize(v), ensure_ascii=False, default=str)
    return v


def write_artifacts(report: dict[str, Any], sheets: dict[str, list[dict[str, Any]]]) -> None:
    EXIT_RESIDUAL_RCA_OUT.mkdir(parents=True, exist_ok=True)
    extra = [p for p in EXIT_RESIDUAL_RCA_OUT.iterdir() if p.is_file() and p.name not in {"report.json", "report.md", "audit.xlsx"}]
    for p in extra:
        p.unlink()
    body = {k: v for k, v in report.items() if k != "_markdown"}
    (EXIT_RESIDUAL_RCA_OUT / "report.json").write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (EXIT_RESIDUAL_RCA_OUT / "report.md").write_text(str(report.get("_markdown") or ""), encoding="utf-8")
    wb = Workbook()
    first = True
    for name in SHEET_ORDER:
        rows = sheets.get(name) or [{"empty": True}]
        clean = [{k: _cell(v) for k, v in dict(r).items()} for r in rows]
        if first:
            ws = wb.active
            ws.title = name[:31]
            first = False
        else:
            ws = wb.create_sheet(name[:31])
        _sheet(ws, clean)
    wb.save(EXIT_RESIDUAL_RCA_OUT / "audit.xlsx")


def _fmt(v: Any) -> str:
    if v is None:
        return "None"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        if abs(v) >= 100:
            return f"{v:,.1f}"
        return f"{v:.4f}"
    if isinstance(v, dict):
        return json.dumps(json_sanitize(v), ensure_ascii=False, default=str)
    if isinstance(v, list):
        return ", ".join(str(x) for x in v) if v else "none"
    return str(v)


def _class_row(cohort: str, name: str, pack: dict[str, Any]) -> dict[str, Any]:
    return {
        "cohort": cohort,
        "residual_class": name,
        "trade_n": pack.get("trade_n"),
        "CORE_n": pack.get("CORE_n"),
        "ADDED_n": pack.get("ADDED_n"),
        "session_close_total_pnl": pack.get("session_close_total_pnl"),
        "gross_loss": pack.get("gross_loss"),
        "peak_executable_profit": pack.get("peak_executable_profit"),
        "peak_to_close_giveback": pack.get("peak_to_close_giveback"),
        "median_giveback": pack.get("median_giveback"),
        "below_be_terminal_loss": pack.get("below_be_terminal_loss"),
        "below_be_after_be": pack.get("below_be_after_be"),
        "positive_peak_reached_n": pack.get("positive_peak_reached_n"),
        "break_even_reached_n": pack.get("break_even_reached_n"),
        "positive_trade_n": pack.get("positive_trade_n"),
        "worst_trade_pnl": pack.get("worst_trade_pnl"),
        "temporary_negative_excursion_total": pack.get("temporary_negative_excursion_total"),
        "top_day": pack.get("top_day"),
        "top_day_share": pack.get("top_day_share"),
        "top_day_warn": pack.get("top_day_warn"),
        "top_symbol": pack.get("top_symbol"),
        "top_symbol_share": pack.get("top_symbol_share"),
        "top_symbol_warn": pack.get("top_symbol_warn"),
    }


def build_sheets(report: dict[str, Any], leak: dict[str, Any], reporting: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    req = dict(report.get("required") or {})
    decision = dict(report.get("decision") or {})
    ident_rows = []
    class_rows = []
    rank_rows = []
    q_rows = [dict(decision.get("questions") or {})] if decision.get("questions") else []
    trade_rows = []
    for cohort_key, label in (("development", "DEVELOPMENT"), ("forward", "FORWARD_BURNED")):
        body = dict(report.get(cohort_key) or {})
        ident_rows.append({"cohort": label, **dict(body.get("identity") or {}), "identity_ok": body.get("identity_ok")})
        class_rows.append(_class_row(label, "ALL", dict(body.get("ALL") or {})))
        class_rows.append(_class_row(label, "PROVEN_FAILURE_DAMAGE", dict(body.get("PROVEN_FAILURE_DAMAGE") or {})))
        class_rows.append(_class_row(label, "WINNER_PROTECTION_BURDEN", dict(body.get("WINNER_PROTECTION_BURDEN") or {})))
        for name in RESIDUAL_CLASSES:
            class_rows.append(_class_row(label, name, dict((body.get("by_class") or {}).get(name) or {})))
        ranks = dict(body.get("rankings") or {})
        for metric in RANK_METRICS:
            for item in list((ranks.get(metric) or {}).get("order") or []):
                rank_rows.append(
                    {
                        "cohort": label,
                        "scope": "ALL",
                        "metric": metric,
                        **dict(item),
                    }
                )
            for scope, key in (
                ("CORE", "CORE_FAILURE_RANKS"),
                ("ADDED", "ADDED_FAILURE_RANKS"),
            ):
                for item in list(
                    (((body.get(key) or {}).get(metric) or {}).get("order") or [])
                ):
                    rank_rows.append(
                        {
                            "cohort": label,
                            "scope": scope,
                            "metric": metric,
                            **dict(item),
                        }
                    )
        for t in list(body.get("rows") or []):
            trade_rows.append({"cohort": label, **{k: t.get(k) for k in (
                "date", "symbol", "t0", "trade_id", "fill_time", "fill_price", "fill_role",
                "path_type", "residual_class", "session_close_pnl", "break_even_reached",
                "first_break_even_time", "peak_executable_pnl", "min_executable_pnl",
                "peak_to_close_giveback", "below_be_terminal_loss", "holding_duration_sec",
                "positive_peak_reached", "temporary_negative_excursion", "quote_n",
            )}})
    return {
        "summary": kv_rows(req),
        "integrity": kv_rows({**dict(leak), **dict(reporting), "NON_INTERFERENCE_PASS": req.get("NON_INTERFERENCE_PASS")}),
        "decision": kv_rows({k: v for k, v in decision.items() if k != "questions"}),
        "identity": ident_rows or [{"empty": True}],
        "classes": class_rows or [{"empty": True}],
        "ranks": rank_rows or [{"empty": True}],
        "questions": q_rows or [{"empty": True}],
        "trades": trade_rows or [{"empty": True}],
    }


def _line(name: str, pack: dict[str, Any]) -> str:
    return (
        f"- {name}: n=`{pack.get('trade_n')}` CORE/ADDED=`{pack.get('CORE_n')}/{pack.get('ADDED_n')}` "
        f"total PnL=`{_fmt(pack.get('session_close_total_pnl'))}` "
        f"gross loss=`{_fmt(pack.get('gross_loss'))}` "
        f"giveback=`{_fmt(pack.get('peak_to_close_giveback'))}` "
        f"below-BE=`{_fmt(pack.get('below_be_terminal_loss'))}` "
        f"peak profit=`{_fmt(pack.get('peak_executable_profit'))}` "
        f"top day=`{pack.get('top_day')}` share=`{_fmt(pack.get('top_day_share'))}` "
        f"top symbol=`{pack.get('top_symbol')}` share=`{_fmt(pack.get('top_symbol_share'))}`"
    )


def build_markdown(report: dict[str, Any]) -> str:
    req = dict(report.get("required") or {})
    dec = dict(report.get("decision") or {})
    q = dict(dec.get("questions") or {})
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        "CORRECTED_RERUN: **R1_RANK_FIELD_MAPPING**",
        "",
        f"VERDICT: **{req.get('VERDICT')}** CASE=`{dec.get('CASE')}`",
        f"PRIMARY_NEXT_EXIT_TARGET=`{dec.get('PRIMARY_NEXT_EXIT_TARGET')}`",
        f"TRUE_OOS=`{req.get('TRUE_OOS')}` CERTIFIED=`{req.get('CERTIFIED')}` NEW_EXIT_RULE=`false`",
        f"NEXT: {req.get('NEXT')}",
        "",
        "Occupancy Control fills only. Session-close operational exit. No Technical EXIT. "
        "Pre-CAP board path closed. Branch U BB-lower path closed. 20260903 unused.",
        "Correction is RCA-ranking-only: "
        "`GROSS_TERMINAL_LOSS -> GROSS_TERMINAL_LOSS`, "
        "`PEAK_TO_CLOSE_GIVEBACK -> peak_to_close_giveback`, "
        "`BELOW_BE_TERMINAL_LOSS -> below_be_terminal_loss`. "
        "Missing aggregates and nonzero-to-zero masking fail closed. "
        "Data, thresholds, and precommitted verdict gates are unchanged.",
        "",
        "## Occupancy identity",
        "",
    ]
    for cohort_key, label in (("development", "DEVELOPMENT"), ("forward", "FORWARD_BURNED")):
        body = dict(report.get(cohort_key) or {})
        ident = dict(body.get("identity") or {})
        got, exp = dict(ident.get("got") or {}), dict(ident.get("expected") or {})
        lines.append(
            f"- {label}: ok=`{body.get('identity_ok')}` "
            f"fill `{got.get('fill_n')}`/`{exp.get('fill_n')}` "
            f"CORE `{got.get('core_n')}`/`{exp.get('core_n')}` "
            f"ADDED `{got.get('added_n')}`/`{exp.get('added_n')}` "
            f"PnL `{_fmt(got.get('pnl'))}`/`{_fmt(exp.get('pnl'))}`"
        )
    lines.extend(["", "## Residual classes", ""])
    for cohort_key, label in (("development", "DEVELOPMENT"), ("forward", "FORWARD_BURNED")):
        body = dict(report.get(cohort_key) or {})
        lines.append(f"### {label}")
        lines.append(_line("ALL", dict(body.get("ALL") or {})))
        lines.append(_line("PROVEN_FAILURE_DAMAGE", dict(body.get("PROVEN_FAILURE_DAMAGE") or {})))
        lines.append(_line("WINNER_PROTECTION_BURDEN", dict(body.get("WINNER_PROTECTION_BURDEN") or {})))
        for name in RESIDUAL_CLASSES:
            lines.append(_line(name, dict((body.get("by_class") or {}).get(name) or {})))
        lines.append("")
        lines.append("Ranks (GROSS_TERMINAL_LOSS / GIVEBACK / BELOW_BE):")
        ranks = dict(body.get("rankings") or {})
        for metric in RANK_METRICS:
            order = list((ranks.get(metric) or {}).get("order") or [])
            txt = ", ".join(f"{e.get('rank')}.{e.get('class')}={_fmt(e.get('value'))}" for e in order)
            lines.append(f"- {metric}: {txt}")
        lines.append("")
    lines.extend(
        [
            "## Questions",
            "",
            f"1. Max residual is U_EARLY_NEVER_BE? `{q.get('Q1_max_is_U_EARLY_NEVER_BE')}`",
            f"2. Max is P_EARLY_AFTER_BE? `{q.get('Q2_max_is_P_EARLY_AFTER_BE')}`",
            f"3. Max is PTF? `{q.get('Q3_max_is_P_PROFIT_THEN_FAILURE')}`",
            f"4. PROVEN materially larger than U? `{q.get('Q4_PROVEN_materially_larger_than_U')}`",
            f"5. DEV/FWD rank1 agree? `{q.get('Q5_DEV_FWD_rank1_agree')}` family agree? `{q.get('Q5_DEV_FWD_family_agree')}`",
            f"6. Target material vs winner burden? `{q.get('Q6_target_material_vs_winner')}`",
            "",
            f"DEV rank1=`{q.get('DEV_rank1')}` FWD rank1=`{q.get('FWD_rank1')}`",
            f"DEV U/PROVEN gross=`{_fmt(q.get('DEV_U_gross'))}` / `{_fmt(q.get('DEV_PROVEN_gross'))}`",
            f"FWD U/PROVEN gross=`{_fmt(q.get('FWD_U_gross'))}` / `{_fmt(q.get('FWD_PROVEN_gross'))}`",
            f"DEV winner total PnL=`{_fmt(q.get('DEV_WINNER_total_pnl'))}`",
            "",
            "## Decision",
            "",
            f"CASE=`{dec.get('CASE')}` VERDICT=`{dec.get('VERDICT')}`",
            f"PRIMARY_NEXT_EXIT_TARGET=`{dec.get('PRIMARY_NEXT_EXIT_TARGET')}`",
            f"reasons=`{dec.get('reasons')}`",
            "",
            "STOP. No new EXIT rule implemented.",
            "",
        ]
    )
    return "\n".join(lines)
