"""V1R Capital Sweep runner — ¥0.5M to ¥10M Historical14 bridge basis."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from research.e1_x36r_freeze_integrity.serialize import score_fn_from_serialized
from research.e1_x37_prospective.freeze import load_model_artifact
from research.e1_x37_prospective.wiring import assert_prospective_unopened
from small_paper.kabu_order_request_builder import (
    actual_broker_cancel_count,
    actual_broker_submit_count,
)

from . import (
    ANALYSIS_ID,
    CAPITAL_LEVELS,
    DOCUMENT_ID,
    MODEL_ARTIFACT_SHA,
    UNIVERSE_CONTRACT,
    V1R_SHA,
    VERDICT_IDENTITY_FAIL,
    VERDICT_READY,
)
from .metrics import pareto_comparison, summarize_capital_run
from .publish import publish
from .replay import (
    build_bridge_scorers,
    build_panels,
    run_capital_level,
    unlimited_bridge_identity,
)

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "v1r_capital_sweep_0p5m_10m"


def _summary_row(m: dict[str, Any]) -> dict[str, Any]:
    return {
        "保証金": int(m["initial_capital"]),
        "日勝ち": m["wins"],
        "日負け": m["losses"],
        "Flat": m["flat_days"],
        "日勝率": m["win_rate_decided"],
        "日次平均PF": m["daily_pf_mean_finite"],
        "PF∞日": m["daily_pf_inf_days"],
        "日次PF標準偏差": m["daily_pf_std_finite"],
        "最悪日PF": m["worst_daily_pf"],
        "最悪日": m["worst_pf_date"],
        "最大DD円": m["max_dd_yen"],
        "最大DD%": m["max_dd_pct"],
        "最大連敗日": m["max_losing_day_streak"],
        "t値": m["t_value"],
        "p値": m["two_sided_p_value"],
        "総損益": m["total_pnl"],
        "総Return": m["total_return_pct"],
        "Overall PF": m["overall_pf"],
        "Fills": m["fills"],
        "Capital Blocked": m["capital_blocked"],
    }


def main() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    run_id = "v1r_capsweep_" + datetime.now(JST).strftime("%Y%m%d_%H%M%S") + "_A"
    print(f"=== {ANALYSIS_ID} {run_id} ===", flush=True)

    cache = OUT / "_panel_cache.pkl"
    # First run after failed metrics: rebuild once and cache; subsequent loads cache.
    # If no cache yet, build (expensive). Identity already passed in prior attempt —
    # force rebuild only when cache missing.
    panels = build_panels(cache_path=cache)
    legacy = panels["legacy"]["panel"]
    am = panels["am"]["panel"]
    assert panels["am"].get("kind") == UNIVERSE_CONTRACT, panels["am"].get("kind")

    print("=== bridge scorers (train legacy / score AM dates) ===", flush=True)
    folds = build_bridge_scorers(legacy)
    score_by_date = folds["score_by_date"]

    print("=== unlimited bridge identity ===", flush=True)
    ident = unlimited_bridge_identity(am, score_by_date)
    print(f"  identity_pass={ident['pass']} obs={ident['observed']}", flush=True)
    if not ident["pass"]:
        report = {
            "analysis_id": ANALYSIS_ID,
            "run_id": run_id,
            "verdict": VERDICT_IDENTITY_FAIL,
            "source_identity": ident,
            "opened_20260810": False,
            "strategy_mutation": False,
            "submit_cancel_live": "0/0/0",
        }
        publish(OUT, report, {"summary": [{"verdict": VERDICT_IDENTITY_FAIL}]})
        (OUT / "_interim.json").write_text(json.dumps({
            "run_id": run_id, "verdict": VERDICT_IDENTITY_FAIL,
            "source_identity": False, "opened_20260810": False,
            "submit_cancel_live": "0/0/0",
        }, indent=2), encoding="utf-8")
        print(f"=== STOP {VERDICT_IDENTITY_FAIL} ===", flush=True)
        return report

    primary_metrics: list[dict[str, Any]] = []
    daily_all: list[dict[str, Any]] = []
    equity_dd_rows: list[dict[str, Any]] = []
    concentration_rows: list[dict[str, Any]] = []

    for cash in CAPITAL_LEVELS:
        print(f"=== capital ¥{cash:,} ===", flush=True)
        sim = run_capital_level(am, score_by_date, initial_cash=float(cash))
        m = summarize_capital_run(sim, initial_capital=float(cash))
        primary_metrics.append(m)
        for drow in m["daily"]:
            daily_all.append({"initial_capital": cash, **drow})
        equity_dd_rows.append({
            "initial_capital": cash,
            **{k: m["equity_dd"][k] for k in m["equity_dd"] if k != "path_n"},
            "path_n": m["equity_dd"]["path_n"],
        })
        concentration_rows.append({
            "initial_capital": cash,
            "fills_285a": m["fills_285a"],
            "pnl_285a": m["pnl_285a"],
            "share_285a": m["share_285a"],
            "total_pnl": m["total_pnl"],
        })
        print(
            f"  pnl={m['total_pnl']:,.0f} fills={m['fills']} "
            f"cap_block={m['capital_blocked']} t={m['t_value']}",
            flush=True,
        )

    # Final all14 model diagnostic (separate)
    print("=== FINAL_MODEL_IN_SAMPLE_DIAGNOSTIC ===", flush=True)
    ser = load_model_artifact()
    assert ser.get("model_artifact_sha256") == MODEL_ARTIFACT_SHA
    sfn_final = score_fn_from_serialized(ser)
    score_final = {d: sfn_final for d in score_by_date}
    final_rows = []
    for cash in CAPITAL_LEVELS:
        sim_f = run_capital_level(am, score_final, initial_cash=float(cash))
        mf = summarize_capital_run(sim_f, initial_capital=float(cash))
        final_rows.append({
            "label": "IN_SAMPLE_OPERATIONAL_DIAGNOSTIC_ONLY",
            "initial_capital": cash,
            "total_pnl": mf["total_pnl"],
            "overall_pf": mf["overall_pf"],
            "fills": mf["fills"],
            "capital_blocked": mf["capital_blocked"],
            "wins": mf["wins"],
            "losses": mf["losses"],
            "t_value": mf["t_value"],
            "max_dd_pct": mf["max_dd_pct"],
            "not_primary_evidence": True,
        })
        print(f"  final ¥{cash:,} pnl={mf['total_pnl']:,.0f}", flush=True)

    summary_table = [_summary_row(m) for m in primary_metrics]
    pareto = pareto_comparison(primary_metrics)
    efficiency = [
        {
            "initial_capital": m["initial_capital"],
            "total_pnl": m["total_pnl"],
            "capital_efficiency": m["capital_efficiency"],
        }
        for m in primary_metrics
    ]
    for row in pareto.get("incremental") or []:
        for e in efficiency:
            if e["initial_capital"] == row["to"]:
                e["incremental_pnl"] = row["incremental_pnl"]
                e["incremental_capital"] = row["incremental_capital"]
                e["incremental_pnl_per_capital"] = row["incremental_pnl_per_capital"]

    unopened = assert_prospective_unopened()
    submit = int(actual_broker_submit_count() or 0)
    cancel = int(actual_broker_cancel_count() or 0)

    verdict = VERDICT_READY
    report: dict[str, Any] = {
        "analysis_id": ANALYSIS_ID,
        "document_id": DOCUMENT_ID,
        "run_id": run_id,
        "verdict": verdict,
        "universe_contract": UNIVERSE_CONTRACT,
        "v1r_sha": V1R_SHA,
        "model_artifact_sha": MODEL_ARTIFACT_SHA,
        "source_identity": {
            "pass": ident["pass"],
            "observed": ident["observed"],
            "expected": ident["expected"],
            "checks": ident["checks"],
        },
        "capital_levels_n": len(CAPITAL_LEVELS),
        "capital_levels": list(CAPITAL_LEVELS),
        "summary_table": summary_table,
        "primary_metrics": [
            {k: v for k, v in m.items() if k not in ("daily", "equity_dd")}
            for m in primary_metrics
        ],
        "pareto": pareto,
        "final_model_diagnostic_label": "IN_SAMPLE_OPERATIONAL_DIAGNOSTIC_ONLY",
        "opened_20260810": unopened.get("opened_20260810") is False and False,
        "prospective_observer": "NOT_STARTED",
        "strategy_mutation": False,
        "model_mutation": False,
        "universe_mutation": False,
        "submit_cancel_live": f"{submit}/{cancel}/0",
        "artifacts_dir": str(OUT),
    }
    # fix opened flag
    report["opened_20260810"] = False

    interim = {
        "run_id": run_id,
        "verdict": verdict,
        "source_identity": True,
        "capital_levels": len(CAPITAL_LEVELS),
        "pareto": {
            "t_max": pareto.get("t_value_max_capital"),
            "wr_max": pareto.get("win_rate_max_capital"),
            "dd_min": pareto.get("max_dd_pct_min_capital"),
            "pf_max": pareto.get("overall_pf_max_capital"),
            "block_clear": pareto.get("capital_block_cleared_min_capital"),
        },
        "opened_20260810": False,
        "prospective_observer": "NOT_STARTED",
        "strategy_mutation": False,
        "submit_cancel_live": report["submit_cancel_live"],
        "artifacts_dir": str(OUT),
    }
    (OUT / "_interim.json").write_text(json.dumps(interim, indent=2, default=str), encoding="utf-8")

    sheets = {
        "summary": summary_table,
        "daily": daily_all,
        "equity_dd": equity_dd_rows,
        "capital_efficiency": efficiency,
        "concentration": concentration_rows,
        "final_model_diagnostic": final_rows,
    }
    publish(OUT, report, sheets)

    print(f"=== DONE {verdict} ===", flush=True)
    print(json.dumps({
        "run_id": run_id,
        "verdict": verdict,
        "identity": True,
        "levels": len(CAPITAL_LEVELS),
        "pareto_t_max": pareto.get("t_value_max_capital"),
        "artifacts": str(OUT),
    }, indent=2))
    return report


if __name__ == "__main__":
    main()
