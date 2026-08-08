"""Publish Single EXIT Revision artifacts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research.e1_x6_provisional.util import sha256_file

SHEETS = [
    "Index", "Precommit", "Identity", "ExitRegistry", "BaselineTrades", "RevisionTrades",
    "ArmEvents", "FloorEvents", "GivebackEpisodes", "MechanismEfficacy", "SideEffects",
    "Economics", "Daily", "DayDeletion", "SymbolDeletion", "Concentration",
    "Verdict", "Tests", "Determinism", "Safety", "ChangeLog",
]


def _kv(d: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for k, v in d.items():
        if isinstance(v, (dict, list)):
            rows.append({"key": k, "value": json.dumps(v, default=str)[:8000]})
        else:
            rows.append({"key": k, "value": v})
    return rows


def _cell(v: Any) -> Any:
    if v is None:
        return ""
    if isinstance(v, (dict, list, tuple)):
        return json.dumps(v, default=str)[:32000]
    if isinstance(v, bool):
        return str(v)
    return v


def _write_xlsx(path: Path, sheets: dict[str, list[dict[str, Any]]]) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.active)
    for name in SHEETS:
        ws = wb.create_sheet(name[:31])
        rows = sheets.get(name) or [{"note": "empty"}]
        keys: list[str] = []
        for r in rows:
            for k in r:
                if k not in keys:
                    keys.append(k)
        ws.append(keys)
        for r in rows:
            ws.append([_cell(r.get(k)) for k in keys])
    wb.save(path)


def publish(report: dict[str, Any], tests: dict[str, Any], det: dict[str, Any], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    sheets_data = report.pop("_sheets", {})
    conc = report.get("concentration") or {}
    rev_econ = report.get("revision_economics") or {}
    base_econ = report.get("baseline_economics") or {}

    sheets = {
        "Index": [
            {"item": "analysis_id", "value": report.get("analysis_id")},
            {"item": "run_id", "value": report.get("run_id")},
            {"item": "verdict", "value": report.get("verdict")},
            {"item": "revision_id", "value": report.get("revision_id")},
        ],
        "Precommit": _kv(report.get("precommit") or {}),
        "Identity": [
            {"item": "baseline_identity", "value": report.get("baseline_identity")},
            {"item": "baseline_n", "value": (report.get("baseline_summary") or {}).get("n_pass")},
            {"item": "revision_n", "value": (report.get("revision_summary") or {}).get("n_pass")},
        ],
        "ExitRegistry": [
            {"exit": "PFQ_X_PROGRESS_STRUCT", "role": "baseline"},
            {"exit": "PFQ_X_PROGRESS_BE5_FLOOR0", "role": "revision", "reason": "PLUS5_BREAKEVEN_FLOOR"},
        ],
        "BaselineTrades": sheets_data.get("BaselineTrades") or [],
        "RevisionTrades": sheets_data.get("RevisionTrades") or [],
        "ArmEvents": sheets_data.get("ArmEvents") or [],
        "FloorEvents": sheets_data.get("FloorEvents") or [],
        "GivebackEpisodes": sheets_data.get("GivebackEpisodes") or [],
        "MechanismEfficacy": _kv(report.get("mechanism") or {}),
        "SideEffects": _kv(report.get("side_effects") or {}),
        "Economics": [
            {"side": "baseline", **{k: base_econ.get(k) for k in (
                "n_trades", "total_pnl_yen_100", "profit_factor", "win_rate", "average_trade",
                "median_trade", "max_drawdown", "positive_days", "negative_days", "daily_median_pnl",
            )}},
            {"side": "revision", **{k: rev_econ.get(k) for k in (
                "n_trades", "total_pnl_yen_100", "profit_factor", "win_rate", "average_trade",
                "median_trade", "max_drawdown", "positive_days", "negative_days", "daily_median_pnl",
            )}},
            {"side": "diff", **(report.get("economics_diff") or {})},
        ],
        "Daily": [
            {"side": "baseline", "day": d, "pnl": v}
            for d, v in sorted((base_econ.get("day_pnl") or {}).items())
        ] + [
            {"side": "revision", "day": d, "pnl": v}
            for d, v in sorted((rev_econ.get("day_pnl") or {}).items())
        ],
        "DayDeletion": [{"excluded_day": d, "remaining_pnl": v} for d, v in sorted((conc.get("leave_one_day_out") or {}).items())],
        "SymbolDeletion": [{"excluded_symbol": s, "remaining_pnl": v} for s, v in sorted((conc.get("leave_one_symbol_out") or {}).items())],
        "Concentration": _kv({k: conc.get(k) for k in (
            "max_day_share", "max_symbol_share", "top_day", "top_symbol", "top_trade_episode",
            "ex_top1_trade_pnl", "ex_top1_symbol_pnl", "ex_top1_day_pnl", "lodo_all_nonneg",
        )}),
        "Verdict": _kv(report.get("verdict_detail") or {}) + _kv(report.get("pfq_current_line") or {}),
        "Tests": tests.get("rows") or [{"note": "n/a"}],
        "Determinism": _kv(det),
        "Safety": _kv(report.get("safety") or {}),
        "ChangeLog": [
            {"change": "PFQ_X_PROGRESS_BE5_FLOOR0", "note": "research-only single EXIT revision"},
            {"change": "baseline_PROGRESS_STRUCT", "note": "unchanged"},
            {"change": "ENTRY", "note": "unchanged PFQ_UPDATE_Q70"},
            {"change": "current_line", "note": (report.get("pfq_current_line") or {}).get("status")},
        ],
    }

    public = {k: v for k, v in report.items() if not k.startswith("_")}
    public["tests"] = {
        "exit_code": tests.get("exit_code"),
        "passed": tests.get("passed"),
        "failed": tests.get("failed"),
        "total": tests.get("total"),
    }
    public["determinism"] = det

    jp = out_dir / "report.json"
    mp = out_dir / "report.md"
    xp = out_dir / "audit.xlsx"
    jp.write_text(json.dumps(public, indent=2, default=str), encoding="utf-8")

    vd = report.get("verdict_detail") or {}
    mech = report.get("mechanism") or {}
    side = report.get("side_effects") or {}
    lines = [
        f"# {report.get('analysis_id')}",
        "",
        f"- run_id: `{report.get('run_id')}`",
        f"- verdict: `{report.get('verdict')}`",
        f"- PFQ current-line: `{((report.get('pfq_current_line') or {}).get('status'))}`",
        f"- frozen: `{vd.get('frozen_design_candidate')}`",
        "",
        "## Economics",
        "",
        f"- baseline PnL/PF: {base_econ.get('total_pnl_yen_100')} / {base_econ.get('profit_factor')}",
        f"- revision PnL/PF: {rev_econ.get('total_pnl_yen_100')} / {rev_econ.get('profit_factor')}",
        "",
        "## Mechanism",
        "",
        f"- giveback_n: {mech.get('original_giveback_n')}",
        f"- prevented: {mech.get('prevented_nonpositive_giveback_n')}",
        f"- gap_through: {mech.get('gap_through_floor_n')}",
        f"- positive_to_nonpositive: {side.get('revision_positive_to_nonpositive_n')}",
        "",
        f"- positive_days: {rev_econ.get('positive_days')}",
        f"- daily_median: {rev_econ.get('daily_median_pnl')}",
        f"- ex_top1 trade/symbol/day: {conc.get('ex_top1_trade_pnl')} / {conc.get('ex_top1_symbol_pnl')} / {conc.get('ex_top1_day_pnl')}",
        "",
        f"- tests: {tests.get('passed')}/{tests.get('total')}",
        f"- A/B: {det.get('ab_match')}",
        f"- submit/cancel/live: 0/0/0",
        "",
    ]
    mp.write_text("\n".join(lines), encoding="utf-8")
    _write_xlsx(xp, sheets)
    shas = {"report.json": sha256_file(jp), "report.md": sha256_file(mp), "audit.xlsx": sha256_file(xp)}
    public["published_shas"] = shas
    jp.write_text(json.dumps(public, indent=2, default=str), encoding="utf-8")
    return shas
