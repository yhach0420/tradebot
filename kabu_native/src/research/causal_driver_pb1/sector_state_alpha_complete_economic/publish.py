"""Publish the feasibility report. Does not retune the strategy."""
from __future__ import annotations

import json
from typing import Any

from research.causal_driver_pb1.sector_state_alpha_complete_economic import (
    ANALYSIS_ID,
    CASE_CLOSED,
    CASE_FAIL,
    CASE_INVALID,
    CASE_PASS,
    NEXT_PASS,
    NEXT_STOP,
)
from research.causal_driver_pb1.sector_state_alpha_complete_economic.economics import _compact
from research.causal_driver_pb1.sector_state_alpha_complete_economic.isolation import OUT, assert_write_root, write_overlap_n
from research.causal_driver_pb1.sector_state_alpha_complete_economic.verify import identity_check

TRADE_FIELDS = (
    "date", "symbol", "entry_type", "episode_id", "signal_t", "entry_t", "entry_px",
    "exit_t", "exit_px", "exit_reason", "same_clock_dual", "session_flat_fill",
    "gross_pnl_yen", "execution_cost_yen", "net_pnl_yen", "net_bps", "holding_min",
)


def _trade(row: dict[str, Any]) -> dict[str, Any]:
    out = {k: row.get(k) for k in TRADE_FIELDS}
    out["exit_reasons"] = ",".join(str(x) for x in list(row.get("exit_reasons") or []))
    return out


def _attribution(result: dict[str, Any]) -> list[str]:
    econ = result.get("economics") or {}
    gates = econ.get("gates") or {}
    notes = [
        f"alpha episodes {result.get('alpha_episode_n')}; target opportunities {result.get('target_alpha_opportunity_n')}",
        f"PB1 confirmed {result.get('PB1_confirmed_n')}; PB1 rejected {result.get('PB1_rejected_n')}; direction mismatch {result.get('ALPHA_DIRECTION_MISMATCH_n')}",
        f"E0 qualified {result.get('E0_qualified_n')}; E1 qualified {result.get('E1_qualified_n')}; overlap {result.get('e0_e1_overlap_n')}",
        f"filled {result.get('filled_trade_n')}; CAP blocked {result.get('CAP_blocked_n')}; same-symbol blocked {result.get('same_symbol_blocked_n')}",
        f"observability reject before fill {result.get('observability_end_before_fill_reject_n')}; other reject {result.get('other_reject_n')}",
    ]
    for name, row in (result.get("exit_breakdown") or {}).items():
        notes.append(f"exit {name} n={row.get('trade_n')} net={row.get('net_pnl_yen')}")
    for name, ok in gates.items():
        if not ok:
            notes.append(f"gate failed {name}")
    folds = econ.get("folds") or {}
    for name, row in folds.items():
        notes.append(f"fold {name} trades={row.get('trade_n')} net={row.get('net_pnl_yen')}")
    return notes


def build(result: dict[str, Any], before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    mutated = before.get("strategy_sha256") != after.get("strategy_sha256") or before.get("runner_sha256") != after.get("runner_sha256")
    if not before.get("ok"):
        verdict, nxt = CASE_CLOSED, NEXT_STOP
    elif (not after.get("ok")) or mutated or not result.get("ok"):
        verdict, nxt = CASE_INVALID, NEXT_STOP
    elif (result.get("economics") or {}).get("all_gates_pass"):
        verdict, nxt = CASE_PASS, NEXT_PASS
    else:
        verdict, nxt = CASE_FAIL, NEXT_STOP
    trades = [_trade(r) for r in list(result.get("trades") or [])]
    econ = dict(result.get("economics") or {})
    return {
        "analysis_id": ANALYSIS_ID,
        "verdict": verdict,
        "next": nxt,
        "identity_before": before,
        "identity_after": after,
        "strategy_mutated_during_run": mutated,
        "prospective_data_opened": False,
        "prospective_rows_read": int(result.get("prospective_rows_read") or 0),
        "research_only": True,
        "submit": 0,
        "cancel": 0,
        "live": 0,
        "v4_changed": False,
        "v5_created": False,
        "funnel": {k: result.get(k) for k in (
            "eligible_day_n", "eligible_first", "eligible_last", "alpha_episode_n", "target_alpha_opportunity_n",
            "PB1_confirmed_n", "PB1_rejected_n", "ALPHA_DIRECTION_MISMATCH_n", "E0_qualified_n", "E1_qualified_n",
            "e0_e1_overlap_n", "admit_attempt_n", "filled_trade_n", "CAP_blocked_n", "same_symbol_blocked_n",
            "observability_end_before_fill_reject_n", "other_reject_n", "same_clock_dual_n", "other_fail_close_n",
            "pb1_e0_emitted_n", "pb1_e1_emitted_n",
        )},
        "occupancy": result.get("occupancy") or {},
        "exit_breakdown": result.get("exit_breakdown") or {},
        "economics": {k: econ.get(k) for k in (
            "primary_8bps", "costs", "folds", "monthly", "symbols", "positive_folds", "positive_fold_n",
            "gates", "all_gates_pass", "concentration",
        )},
        "entry_path": {
            "E0": _compact(list(result.get("e0_fills") or [])),
            "E1": _compact(list(result.get("e1_fills") or [])),
        },
        "episodes": {
            "episode_n": result.get("alpha_episode_n"),
            "episodes_with_trade_n": len({str(r.get("episode_id") or "") for r in trades if r.get("episode_id")}),
            "trades_per_episode": (
                float(len(trades) / int(result["alpha_episode_n"])) if result.get("alpha_episode_n") else None
            ),
            "net_pnl_per_episode": (
                float((econ.get("primary_8bps") or {}).get("net_pnl_yen") or 0.0) / int(result["alpha_episode_n"])
                if result.get("alpha_episode_n") else None
            ),
        },
        "attribution": _attribution(result) if verdict == CASE_FAIL else [],
        "trades": trades,
        "classification": "RETROSPECTIVE_DEVELOPMENT_ECONOMIC_FEASIBILITY",
    }


def _put(wb: Any, name: str, rows: list[dict[str, Any]]) -> None:
    ws = wb.create_sheet(name)
    if not rows:
        ws.append(["none"])
        return
    keys: list[str] = []
    flat = []
    for row in rows:
        item = {}
        for k, v in row.items():
            if isinstance(v, (dict, list)):
                v = json.dumps(v, ensure_ascii=False, sort_keys=True)
            item[k] = v
            if k not in keys:
                keys.append(k)
        flat.append(item)
    ws.append(keys)
    for item in flat:
        ws.append([item.get(k) for k in keys])


def publish(report: dict[str, Any]) -> None:
    import pandas as pd
    from openpyxl import Workbook

    assert_write_root()
    if write_overlap_n("", "") != 0:
        raise RuntimeError("write_isolation")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    primary = (report.get("economics") or {}).get("primary_8bps") or {}
    lines = [
        f"# {ANALYSIS_ID}",
        "",
        f"VERDICT: {report['verdict']}",
        f"NEXT: {report['next']}",
        "",
        "Classification: RETROSPECTIVE_DEVELOPMENT_ECONOMIC_FEASIBILITY.",
        "This is not holdout, validation, or prospective.",
        "",
        f"eligible days: {(report.get('funnel') or {}).get('eligible_day_n')}",
        f"filled trades: {(report.get('funnel') or {}).get('filled_trade_n')}",
        f"8bps net pnl: {primary.get('net_pnl_yen')}",
        f"8bps PF: {primary.get('profit_factor')}",
        f"gates: {json.dumps((report.get('economics') or {}).get('gates'), sort_keys=True)}",
        "",
        "No strategy threshold was changed after these results.",
        "Prospective data was not opened.",
    ]
    if report.get("attribution"):
        lines.extend(["", "Attribution:"])
        lines.extend(f"- {x}" for x in report["attribution"])
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    wb = Workbook()
    default = wb.active
    wb.remove(default)
    funnel = report.get("funnel") or {}
    _put(wb, "Manifest", [{"analysis_id": ANALYSIS_ID, "verdict": report["verdict"], "next": report["next"], "classification": report["classification"]}])
    _put(wb, "Identity_Check", [report.get("identity_before", {}).get("checks") or {}])
    _put(wb, "Date_Coverage", [{"eligible_day_n": funnel.get("eligible_day_n"), "first": funnel.get("eligible_first"), "last": funnel.get("eligible_last"), "prospective_rows_read": 0}])
    _put(wb, "Alpha_Episodes", [report.get("episodes") or {}])
    _put(wb, "Opportunity_Funnel", [funnel])
    _put(wb, "PB1", [{"confirmed": funnel.get("PB1_confirmed_n"), "rejected": funnel.get("PB1_rejected_n"), "direction_mismatch": funnel.get("ALPHA_DIRECTION_MISMATCH_n")}])
    _put(wb, "E0_E1", [{"E0_qualified": funnel.get("E0_qualified_n"), "E1_qualified": funnel.get("E1_qualified_n"), "overlap": funnel.get("e0_e1_overlap_n"), **(report.get("entry_path") or {})}])
    _put(wb, "Admissions", [{"admit_attempt_n": funnel.get("admit_attempt_n"), "filled": funnel.get("filled_trade_n"), "observability_reject": funnel.get("observability_end_before_fill_reject_n"), "other_reject": funnel.get("other_reject_n")}])
    _put(wb, "Occupancy", [report.get("occupancy") or {}])
    _put(wb, "Trades", report.get("trades") or [])
    _put(wb, "Exit_Reasons", list((report.get("exit_breakdown") or {}).values()) + [{"same_clock_dual_n": funnel.get("same_clock_dual_n"), "other_fail_close_n": funnel.get("other_fail_close_n")}])
    _put(wb, "Economics_8bps", [primary])
    _put(wb, "Cost_Robustness", list(((report.get("economics") or {}).get("costs") or {}).values()))
    _put(wb, "Chronology", list(((report.get("economics") or {}).get("folds") or {}).values()))
    _put(wb, "Monthly", (report.get("economics") or {}).get("monthly") or [])
    _put(wb, "Symbol", (report.get("economics") or {}).get("symbols") or [])
    _put(wb, "Concentration", [((report.get("economics") or {}).get("concentration") or {})])
    _put(wb, "Economic_Gates", [(report.get("economics") or {}).get("gates") or {}])
    _put(wb, "Prospective_Firewall", [{"PROSPECTIVE_DATA_OPENED": False, "prospective_rows_read": 0}])
    _put(wb, "Safety", [{"research_only": True, "submit": 0, "cancel": 0, "live": 0, "V4_CHANGED": False, "V5_CREATED": False}])
    wb.save(OUT / "audit.xlsx")
    _ = pd
