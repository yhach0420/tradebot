"""Emit report.md / report.json / audit.xlsx only."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


def write_xlsx(path: Path, sheets: Mapping[str, Sequence[Mapping[str, Any]]]) -> None:
    from openpyxl import Workbook

    def _cell(v: Any) -> Any:
        if v is None or isinstance(v, (int, float, bool, str)):
            return v
        if isinstance(v, datetime):
            return v.isoformat()
        return json.dumps(v, ensure_ascii=False, default=str)

    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(str(name)[:31])
        if not rows:
            ws.append(["empty"])
            continue
        keys: list[str] = []
        for r in rows:
            for k in r.keys():
                if k not in keys:
                    keys.append(k)
        ws.append(keys)
        for r in rows:
            ws.append([_cell(r.get(k)) for k in keys])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def emit_artifacts(out_dir: Path, payload: Mapping[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for p in out_dir.iterdir():
        if p.is_file() and p.name not in ("report.md", "report.json", "audit.xlsx"):
            p.unlink()
    slim = {k: v for k, v in payload.items() if k not in ("baseline_matches",)}
    # keep matches in json but capped for size — full in xlsx
    if "baseline" in slim and isinstance(slim["baseline"], dict):
        pass
    (out_dir / "report.json").write_text(json.dumps(slim, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (out_dir / "report.md").write_text(_md(payload), encoding="utf-8")
    write_xlsx(out_dir / "audit.xlsx", _sheets(payload))
    # update completion path
    comp = payload.get("completion")
    if isinstance(comp, dict):
        comp["32_artifact_path"] = str(out_dir)


def _md(p: Mapping[str, Any]) -> str:
    v = p.get("verdict") or {}
    b = p.get("baseline") or {}
    pos = p.get("position_state") or {}
    caps = (p.get("cap5") or {}).get("portfolios") or {}
    dd = p.get("trade_level_dd") or {}
    lines = [
        "# Price-Flow EXIT Integrity / CAP=5 Revalidation",
        "",
        f"- run_id: `{p.get('run_id')}`",
        f"- final_verdict: **{v.get('final')}**",
        f"- codes: {', '.join(v.get('codes') or [])}",
        "",
        "## 結論",
        "",
        v.get("summary") or "",
        "",
        "## 1. EXIT baseline parity",
        "",
        f"- matched: {b.get('n_matched')} / actual {b.get('n_actual')}",
        f"- exact reason: {b.get('exact_reason_match_rate')}",
        f"- family reason: {b.get('family_reason_match_rate')}",
        f"- exit time diff: {b.get('exit_time_diff_sec')}",
        f"- pnl diff: {b.get('pnl_diff_yen100')}",
        f"- total pnl abs pct: {b.get('total_pnl_abs_pct')}",
        f"- gates: {b.get('gates')}",
        f"- verdict: {b.get('verdict')}",
        "",
        "## 2. Position state",
        "",
        str(pos),
        "",
        "## 3. CAP=5 event replay",
        "",
    ]
    for pid in ("P0", "P1", "P2", "P3", "P4", "P5"):
        c = caps.get(pid) or {}
        lines.append(
            f"- {pid}: accepted={c.get('accepted')} cap_blocked={c.get('cap_blocked')} "
            f"same_sym={c.get('same_symbol_blocked')} pnl5={c.get('pnl_5bps')} PF={c.get('PF_5bps')} "
            f"tradeDD={c.get('max_dd_trade_sequence')}"
        )
    lines += [
        "",
        "## 4. Trade-level DD (P1 PBv2+X6)",
        "",
        str(dd.get("P1_PBv2_X6")),
        "",
        "## 5. Dependency",
        "",
    ]
    for d in p.get("dependencies") or []:
        lines.append(
            f"- {d.get('label')}: top1_sym={d.get('top1_symbol_pnl_share')} top1_day={d.get('top1_day_pnl_share')} "
            f"verdict={d.get('verdict')}"
        )
    ab = p.get("x6_ablation") or {}
    lines += ["", "## 6. X6 ablation", "", str(ab.get("ablation")), "", "## 7. VCIE no-overlap / 285A.T", "", str((p.get("vcie_no_overlap") or {}).get("focus_285A")), ""]
    lines += [
        "",
        "## Safety",
        "",
        f"- submit={p.get('submit')} cancel={p.get('cancel')} live_order={p.get('live_order')}",
        f"- mainline_unchanged={p.get('mainline_unchanged')} entry_unchanged={p.get('entry_unchanged')} exit_rules_unchanged={p.get('exit_rules_unchanged')}",
        "",
        "## 本線採用しない理由",
        "",
        v.get("no_production_reason") or "",
        "OOS 3日のため PRICE_FLOW_EXIT_INSUFFICIENT_OOS。EDGE_CONFIRMED 条件未達。",
        "",
    ]
    return "\n".join(lines) + "\n"


def _as_rows(obj: Any) -> list[dict[str, Any]]:
    if obj is None:
        return [{"status": "empty"}]
    if isinstance(obj, list):
        if not obj:
            return [{"status": "empty"}]
        if isinstance(obj[0], dict):
            return obj
        return [{"value": x} for x in obj]
    if isinstance(obj, dict):
        return [obj]
    return [{"value": str(obj)}]


def _sheets(p: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    b = p.get("baseline") or {}
    caps = (p.get("cap5") or {}).get("portfolios") or {}
    dd = p.get("trade_level_dd") or {}
    deps = p.get("dependencies") or []
    ab = p.get("x6_ablation") or {}
    vcie = p.get("vcie_no_overlap") or {}
    focus = vcie.get("focus_285A") or {}

    sym_rows = []
    day_rows = []
    loo_rows = []
    for d in deps:
        for s, pnl in (d.get("symbol_pnl") or {}).items():
            sym_rows.append({"label": d.get("label"), "symbol": s, "pnl_5bps": pnl, "n": (d.get("symbol_trades") or {}).get(s)})
        for day, pnl in (d.get("day_pnl") or {}).items():
            day_rows.append({"label": d.get("label"), "day": day, "pnl_5bps": pnl})
        for r in d.get("leave_one_symbol_out") or []:
            loo_rows.append({"label": d.get("label"), "type": "symbol", **r})
        for r in d.get("leave_one_day_out") or []:
            loo_rows.append({"label": d.get("label"), "type": "day", **r})

    intraday = []
    for label, block in dd.items():
        intraday.append({"portfolio": label, **block})

    daily = []
    for d in deps:
        for day, pnl in (d.get("day_pnl") or {}).items():
            daily.append({"label": d.get("label"), "day": day, "pnl_5bps": pnl, "dd_note": "daily closed pnl (not portfolio DD)"})

    return {
        "README": [
            {
                "title": "Price-Flow EXIT Integrity",
                "run_id": p.get("run_id"),
                "sot": str((p.get("sot") or {}).get("price_flow_exit")),
                "verdict": (p.get("verdict") or {}).get("final"),
                "submit": 0,
                "cancel": 0,
                "live_order": 0,
            }
        ],
        "BASELINE_MATCHING": p.get("baseline_matches") or [{"status": "empty"}],
        "BASELINE_UNMATCHED": p.get("baseline_unmatched") or [{"status": "empty"}],
        "BASELINE_PARITY": [{k: v for k, v in b.items() if k not in ("matches_preview",)}],
        "POSITION_STATE": [p.get("position_state") or {"status": "empty"}],
        "OVERLAPPING_ENTRIES": p.get("overlapping_entries") or [{"status": "empty"}],
        "CAP5_EVENT_LOG": p.get("cap5_event_log") or [{"status": "empty"}],
        "CAP5_BLOCKED": p.get("cap5_blocked") or [{"status": "empty"}],
        "CAP5_RESULTS": [caps[k] for k in ("P0", "P1", "P2", "P3", "P4", "P5") if k in caps] or [{"status": "empty"}],
        "TRADE_LEVEL_EQUITY": (p.get("equity_p1") or [])[:500] or [{"status": "empty"}],
        "INTRADAY_DD": intraday or [{"status": "empty"}],
        "DAILY_DD": daily or [{"status": "empty"}],
        "SYMBOL_DEPENDENCY": sym_rows or [{"status": "empty"}],
        "DAY_DEPENDENCY": day_rows or [{"status": "empty"}],
        "LEAVE_ONE_OUT": loo_rows or [{"status": "empty"}],
        "X6_REASON_ATTRIBUTION": ab.get("reason_attribution") or [{"status": "empty"}],
        "X6_ABLATION": ab.get("ablation") or [{"status": "empty"}],
        "VCIE_NO_OVERLAP": (vcie.get("comparisons") or [])
        + [{"section": "285A", **r} for r in (focus.get("entries") or [])]
        + [{"section": "285A_summary", "pnl_before": focus.get("pnl_before_overlap_filter"), "pnl_after": focus.get("pnl_after_no_overlap"), "n": focus.get("n_entries")}],
        "PBV2_RESULTS": _as_rows(p.get("pbv2_results")),
        "VCIE_RESULTS": _as_rows(p.get("vcie_results")),
        "VERDICT": [p.get("verdict") or {"final": "PRICE_FLOW_EXIT_OFFLINE_ONLY"}],
    }
