#!/usr/bin/env python3
"""
Phase309-lite: Check whether scenario D outlier metrics are driven by 20260518.

Output: phase309_d_outlier_check.json
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native/results/reports/phase309_d_outlier_check.json"
P308_REPORT = REPO / "kabu_native/results/reports/phase308_rebuilt_entry_score_review.json"
P308_CHECKPOINT = REPO / "kabu_native/results/reports/phase308_rebuilt_entry_score_review.checkpoint.json"
OUTLIER_DAY = "20260518"


def _pf_from_wins_losses(wins: float, losses: float) -> Any:
    loss_abs = abs(losses)
    if loss_abs <= 0:
        return None if wins <= 0 else "inf"
    return round(wins / loss_abs, 4)


def _gross_wins_losses(pf: Any, pnl: float) -> tuple[float, float]:
    if pnl == 0 or pf is None:
        return 0.0, 0.0
    if pf == "inf":
        return float(pnl), 0.0
    pf_f = float(pf)
    if abs(pf_f - 1.0) < 1e-9:
        return max(pnl, 0.0), abs(min(pnl, 0.0))
    # W - L = pnl, W/L = pf  =>  L = pnl / (pf - 1), W = pf * L
    loss = pnl / (pf_f - 1.0)
    win = pf_f * loss
    return float(win), float(loss)


def _aggregate_days(day_metrics: list[dict[str, Any]]) -> dict[str, Any]:
    tc = sum(int(d.get("trade_count") or 0) for d in day_metrics)
    pnl = sum(float(d.get("total_pnl_pct") or 0) for d in day_metrics)
    wins = 0.0
    losses = 0.0
    for d in day_metrics:
        w, l = _gross_wins_losses(d.get("profit_factor"), float(d.get("total_pnl_pct") or 0))
        wins += w
        losses += l
    return {
        "trade_count": tc,
        "profit_factor": _pf_from_wins_losses(wins, losses),
        "total_pnl_pct": round(pnl, 4),
        "avg_pnl_pct": round(pnl / tc, 6) if tc else None,
    }


def _slice_metrics(
    daily: dict[str, dict[str, Any]],
    *,
    exclude_days: Optional[set[str]] = None,
    only_days: Optional[set[str]] = None,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for day, sc in sorted(daily.items()):
        if exclude_days and day in exclude_days:
            continue
        if only_days and day not in only_days:
            continue
        d = sc.get("D") or {}
        if int(d.get("trade_count") or 0) > 0:
            rows.append({**d, "day": day})
    out = _aggregate_days(rows)
    out["days_included"] = [r["day"] for r in rows]
    out["daily_breakdown"] = [
        {
            "day": r["day"],
            "trade_count": r.get("trade_count"),
            "profit_factor": r.get("profit_factor"),
            "total_pnl_pct": r.get("total_pnl_pct"),
        }
        for r in rows
    ]
    return out


def _verdict(full: dict[str, Any], only: dict[str, Any], exclude: dict[str, Any]) -> dict[str, Any]:
    full_pnl = float(full.get("total_pnl_pct") or 0)
    only_pnl = float(only.get("total_pnl_pct") or 0)
    only_tc = int(only.get("trade_count") or 0)
    full_tc = int(full.get("trade_count") or 0)
    pnl_share = only_pnl / full_pnl if full_pnl else 0.0
    tc_share = only_tc / full_tc if full_tc else 0.0
    driving = pnl_share >= 0.5 or (tc_share >= 0.5 and only_pnl > exclude.get("total_pnl_pct", 0))
    return {
        "is_20260518_driving_result": driving,
        "pnl_share_from_20260518": round(pnl_share, 4),
        "trade_count_share_from_20260518": round(tc_share, 4),
        "rationale": [
            f"20260518: tc={only_tc} PnL={only_pnl} PF={only.get('profit_factor')} "
            f"({round(pnl_share*100,2)}% of full PnL)",
            f"exclude 20260518: tc={exclude.get('trade_count')} PnL={exclude.get('total_pnl_pct')} "
            f"PF={exclude.get('profit_factor')}",
            "is_20260518_driving_result=true when 20260518 contributes majority of PnL or dominates vs remainder.",
        ],
    }


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    report = json.loads(P308_REPORT.read_text(encoding="utf-8"))
    ck = json.loads(P308_CHECKPOINT.read_text(encoding="utf-8"))
    daily = ck.get("daily_by_scenario") or {}
    d_full = dict((report.get("comparison") or {}).get("D") or {})

    full = {
        "trade_count": d_full.get("trade_count"),
        "profit_factor": d_full.get("profit_factor"),
        "total_pnl_pct": d_full.get("total_pnl_pct"),
        "source": "phase308_comparison.D",
    }
    only_0518 = _slice_metrics(daily, only_days={OUTLIER_DAY})
    exclude_0518 = _slice_metrics(daily, exclude_days={OUTLIER_DAY})
    verdict = _verdict(full, only_0518, exclude_0518)

    out_doc = {
        "phase": "309-lite",
        "title": "d_outlier_check",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "constraint": "review only",
        "scenario": "D",
        "scenario_definition": (report.get("scenarios") or {}).get("D"),
        "outlier_day": OUTLIER_DAY,
        "full_period": full,
        "exclude_20260518": exclude_0518,
        "only_20260518": only_0518,
        "verdict": verdict,
    }
    OUT.write_text(json.dumps(out_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT}")
    print(
        f"full tc={full['trade_count']} PF={full['profit_factor']} PnL={full['total_pnl_pct']} | "
        f"exclude tc={exclude_0518['trade_count']} PF={exclude_0518['profit_factor']} "
        f"PnL={exclude_0518['total_pnl_pct']} | driving={verdict['is_20260518_driving_result']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
