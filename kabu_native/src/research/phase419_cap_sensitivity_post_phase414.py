"""
Phase419: CAP Sensitivity Study (Post-Phase414).

Baseline: Phase413 no_overlap_replace (Baseline B) over 20260529-20260616.
Fixed: 1.5M yen, leverage 2x, 100 shares, fixed_stop_1p2.

Research-only — no Runtime/YAML/Entry/Exit/Order/Discord changes.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.equity_dynamic_stop_shadow import PERIOD_END, PERIOD_START, enrich_trades_with_entry_price
from research.market_sector_heat import _write_csv
from research.phase271_leverage_attribution_and_robustness import simulate_audited
from research.phase400_holding_time_audit import hold_seconds
from research.structural_trade_normalize import resolve_reports_dir

JST = ZoneInfo("Asia/Tokyo")

STARTING_EQUITY = 1_500_000
LEVERAGE = 2.0
SHARES = 100
STOP_POLICY = "fixed_stop_1p2"
CAP_LEVELS: tuple[int, ...] = (1, 2, 3, 4, 5, 6)

GRID_FIELDS = [
    "candidate",
    "accepted",
    "rejected",
    "PF",
    "PnL",
    "maxDD",
    "final_equity",
    "win_rate",
    "avg_hold",
    "median_hold",
]


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _pnl_yen(sim: Mapping[str, Any]) -> float:
    start = float(sim.get("starting_equity") or sim.get("initial_equity") or STARTING_EQUITY)
    final = float(sim.get("final_equity") or start)
    return round(final - start, 2)


def _accepted_hold_stats(sim: Mapping[str, Any]) -> dict[str, Any]:
    state = sim.get("_state")
    if state is None:
        return {"avg_hold": 0.0, "median_hold": 0.0}
    holds: list[float] = []
    for log in getattr(state, "trade_log", []) or []:
        trade = log.get("trade") or {}
        et = str(trade.get("entry_time") or "")
        xt = str(log.get("exit_time") or trade.get("exit_time") or "")
        hs = float(trade.get("hold_sec") or 0.0)
        if hs <= 0:
            hs = float(hold_seconds(et, xt))
        if hs > 0:
            holds.append(hs)
    if not holds:
        return {"avg_hold": 0.0, "median_hold": 0.0}
    return {
        "avg_hold": round(statistics.mean(holds), 2),
        "median_hold": round(statistics.median(holds), 2),
    }


def _cap_reject_ev(sim: Mapping[str, Any]) -> dict[str, Any]:
    from research.phase382_capital_constrained_backtest import _day_from_ts

    rejects = sim.get("reject_log") or []
    cap_rejects = [r for r in rejects if str(r.get("reason") or "") == "max_concurrent_positions"]
    vals = [float(r.get("counterfactual_pnl") or 0.0) for r in cap_rejects]
    if not cap_rejects:
        return {
            "cap_reject_count": 0,
            "cap_reject_ev_mean_yen": 0.0,
            "cap_reject_ev_sum_yen": 0.0,
            "cap_reject_by_day": {},
            "cap_reject_reason_counts": dict(sim.get("reject_reason_counts") or {}),
        }
    by_day: dict[str, list[float]] = {}
    for r in cap_rejects:
        key = str(r.get("key") or "")
        ts = key.split("|", 1)[1] if "|" in key else ""
        day = _day_from_ts(ts) or ""
        by_day.setdefault(day, []).append(float(r.get("counterfactual_pnl") or 0.0))
    by_day_mean = {d: round(sum(v) / max(1, len(v)), 2) for d, v in by_day.items()}
    by_day_count = {d: len(v) for d, v in by_day.items()}
    return {
        "cap_reject_count": len(cap_rejects),
        "cap_reject_ev_mean_yen": round(sum(vals) / len(vals), 2),
        "cap_reject_ev_sum_yen": round(sum(vals), 2),
        "cap_reject_by_day": {
            d: {"count": by_day_count[d], "mean_counterfactual_pnl_yen": by_day_mean[d]}
            for d in sorted(by_day)
        },
        "cap_reject_reason_counts": dict(sim.get("reject_reason_counts") or {}),
    }


def _select_best_cap(rows: Sequence[Mapping[str, Any]]) -> int:
    # Primary: highest PnL; tie-breaker: lowest maxDD; then higher PF.
    best = None
    for r in rows:
        pnl = float(r.get("PnL") or 0.0)
        dd = float(r.get("maxDD") or 0.0)
        pf = float(r.get("PF") or 0.0)
        cap = int(str(r.get("candidate") or "CAP_0").split("_")[-1] or 0)
        score = (pnl, -dd, pf)
        if best is None or score > best[0]:
            best = (score, cap)
    return int(best[1] if best else 3)


def load_baseline_b_trades(repo_root: Path) -> list[dict[str, Any]]:
    # Use canonical Baseline B (681) from Phase416 collapse path, then enrich entry_price.
    from research.phase416_post_no_overlap_shadow_rebaseline import load_baseline_a_trades, load_baseline_b_trades

    raw = load_baseline_b_trades(load_baseline_a_trades(repo_root))
    enriched, _meta = enrich_trades_with_entry_price(raw, repo_root=repo_root)
    return enriched


def run_phase419_cap_sensitivity(*, repo_root: Path) -> dict[str, Any]:
    reports_dir = resolve_reports_dir(repo_root)
    trades = load_baseline_b_trades(repo_root)
    period_days = sorted({str(t.get("day") or "") for t in trades if t.get("day")})

    grid_rows: list[dict[str, Any]] = []
    cap_ev: dict[str, Any] = {}
    for cap in CAP_LEVELS:
        sim = simulate_audited(
            trades,
            starting_equity=STARTING_EQUITY,
            leverage=LEVERAGE,
            cap=cap,
            stop_policy=STOP_POLICY,
        )
        holds = _accepted_hold_stats(sim)
        row = {
            "candidate": f"CAP_{cap}",
            "accepted": int(sim.get("accepted_trade_count") or 0),
            "rejected": int(sim.get("rejected_trade_count") or 0),
            "PF": float(sim.get("profit_factor") or 0.0),
            "PnL": _pnl_yen(sim),
            "maxDD": float(sim.get("max_drawdown_yen") or 0.0),
            "final_equity": float(sim.get("final_equity") or STARTING_EQUITY),
            "win_rate": float(sim.get("win_rate") or 0.0),
            "avg_hold": float(holds.get("avg_hold") or 0.0),
            "median_hold": float(holds.get("median_hold") or 0.0),
        }
        grid_rows.append(row)
        cap_ev[f"CAP_{cap}"] = _cap_reject_ev(sim)

    best_cap = _select_best_cap(grid_rows)
    cap3 = next((r for r in grid_rows if str(r.get("candidate")) == "CAP_3"), {})
    best = next((r for r in grid_rows if str(r.get("candidate")) == f"CAP_{best_cap}"), {})
    delta_vs_cap3 = {
        "best_cap": best_cap,
        "delta_pnl_yen": round(float(best.get("PnL") or 0.0) - float(cap3.get("PnL") or 0.0), 2),
        "delta_pf": round(float(best.get("PF") or 0.0) - float(cap3.get("PF") or 0.0), 6),
        "delta_maxdd_yen": round(float(best.get("maxDD") or 0.0) - float(cap3.get("maxDD") or 0.0), 2),
        "delta_accepted": int(best.get("accepted") or 0) - int(cap3.get("accepted") or 0),
        "delta_rejected": int(best.get("rejected") or 0) - int(cap3.get("rejected") or 0),
    }

    status = "insufficient_inputs" if len(trades) < 600 or len(period_days) < 11 else "cap_sensitivity_complete"
    result = {
        "phase": "419-CAP-Sensitivity-Post-Phase414",
        "generated_at": _now_iso(),
        "status": status,
        "baseline": {
            "name": "Phase413 no_overlap_replace Baseline B",
            "period": {"start": PERIOD_START, "end": PERIOD_END or "20260616"},
        },
        "fixed_params": {
            "starting_equity": STARTING_EQUITY,
            "leverage": LEVERAGE,
            "shares": SHARES,
            "stop_policy": STOP_POLICY,
            "cap_levels": list(CAP_LEVELS),
        },
        "input_validation": {
            "trade_count": len(trades),
            "period_days": period_days,
            "period_day_count": len(period_days),
        },
        "best_cap": best_cap,
        "delta_vs_cap3": delta_vs_cap3,
        "cap_reject_expected_value": cap_ev,
        "_grid_rows": grid_rows,
        "reports_dir": str(reports_dir),
    }
    return result


def render_report_md(result: Mapping[str, Any]) -> str:
    best_cap = result.get("best_cap")
    delta = result.get("delta_vs_cap3") or {}
    lines = [
        "# Phase419 — CAP Sensitivity Study (Post-Phase414)",
        "",
        f"Generated: {result.get('generated_at')}",
        f"Status: **{result.get('status')}**",
        "",
        "## 必須回答",
        "",
        f"1. **最適CAP**: CAP{best_cap}",
        f"2. **CAP3との差**: pnl={delta.get('delta_pnl_yen')} yen, pf={delta.get('delta_pf')}, "
        f"maxDD={delta.get('delta_maxdd_yen')} yen, acceptedΔ={delta.get('delta_accepted')}, rejectedΔ={delta.get('delta_rejected')}",
        "3. **本番変更推奨か**: Researchのみ（Runtime/YAML変更は禁止）。採用判断は CAP差分と reject EV を見て別Phaseで実施。",
        "4. **資産シミュ変更推奨か**: Baseline Bでは entry_price 補完が必須（Phase418で再検証済み）。",
        "5. **rollback方法**: Runtime変更なし。将来CAP変更する場合は設定をCAP3に戻すだけ（影響は max_concurrent_positions のみ）。",
        "",
        "## Grid summary",
        "",
        "- 出力CSV: `results/reports/phase419_cap_sensitivity_grid.csv`",
        "",
        "## CAP rejects expected value (max_concurrent_positions)",
        "",
        "- 出力JSON: `results/reports/phase419_cap_sensitivity_summary.json` 内 `cap_reject_expected_value`",
        "",
    ]
    return "\n".join(lines)


@dataclass
class Phase419Job:
    repo_root: Path
    reports_dir: Path

    def run(self) -> dict[str, Any]:
        return run_phase419_cap_sensitivity(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = self.reports_dir
        reports.mkdir(parents=True, exist_ok=True)
        summary_path = reports / "phase419_cap_sensitivity_summary.json"
        grid_path = reports / "phase419_cap_sensitivity_grid.csv"
        report_path = self.repo_root / "docs" / "operations" / "phase419_cap_sensitivity_report.md"

        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _write_csv(grid_path, GRID_FIELDS, result.get("_grid_rows") or [])
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(render_report_md(result), encoding="utf-8")
        return {"summary": summary_path, "grid": grid_path, "report": report_path}

