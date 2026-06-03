#!/usr/bin/env python3
"""
Phase255: close<300 除外の安定性確認（日別 before/after）

母集団: Phase254 と同じ（operational by trade_date + price floor refill）

出力: kabu_native/results/reports/phase255_price_floor_stability.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore[misc, assignment]

JST = ZoneInfo("Asia/Tokyo") if ZoneInfo else None
PNL_EPS = 1e-9
PF_EPS = 1e-6


def _load_phase254_module(script_dir: Path):
    path = script_dir / "run_phase254_price_floor_adoption_review.py"
    spec = importlib.util.spec_from_file_location("phase254", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load phase254 from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _now_jst_iso() -> str:
    dt = datetime.now().astimezone(JST) if JST else datetime.now()
    return dt.isoformat(timespec="seconds")


def _classify_day(pnl_before: float, pnl_after: float) -> str:
    if pnl_after > pnl_before + PNL_EPS:
        return "improved"
    if pnl_after < pnl_before - PNL_EPS:
        return "worsened"
    return "unchanged"


def _classify_pf(pf_before: float | None, pf_after: float | None) -> str:
    if pf_before is None and pf_after is None:
        return "unchanged"
    if pf_before is None:
        return "improved" if pf_after is not None and pf_after > 1.0 else "unchanged"
    if pf_after is None:
        return "worsened" if pf_before is not None and pf_before > 1.0 else "unchanged"
    if pf_after > pf_before + PF_EPS:
        return "improved"
    if pf_after < pf_before - PF_EPS:
        return "worsened"
    return "unchanged"


def main() -> int:
    script = Path(__file__).resolve()
    native_root = script.parents[1]
    repo_root = script.parents[2]
    reports_dir = native_root / "results" / "reports"

    parser = argparse.ArgumentParser(description="Phase255 price floor daily stability")
    parser.add_argument("--results-root", type=Path, default=native_root / "results")
    parser.add_argument(
        "--out",
        type=Path,
        default=reports_dir / "phase255_price_floor_stability.json",
    )
    args = parser.parse_args()

    p254 = _load_phase254_module(script.parent)
    phase252 = p254._load_phase252_module(script.parent)
    results_root = args.results_root if args.results_root.is_absolute() else (repo_root / args.results_root)
    out_path = args.out if args.out.is_absolute() else (repo_root / args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    features_by_date = p254._load_features_by_date(reports_dir)
    if not features_by_date:
        print("features_*.csv not found", file=sys.stderr)
        return 2
    latest_features = features_by_date[max(features_by_date)]

    universe_by_date = phase252._load_universe_by_trade_date(reports_dir)
    price_floor_by_date: dict[str, set[str]] = {}
    for day, cur_syms in universe_by_date.items():
        feat = features_by_date.get(day) or latest_features
        pf_syms, _, _ = p254._apply_price_floor_with_refill(cur_syms, feat)
        price_floor_by_date[day] = pf_syms

    replay_paths = sorted((results_root / "replay").rglob("trades.csv"))
    small_paths = sorted((results_root / "small_paper").rglob("structural_trades.csv"))
    trades: list[p254.TradeRow] = []
    for p in replay_paths:
        trades.extend(list(p254._iter_replay_trades(p)))
    for p in small_paths:
        trades.extend(list(p254._iter_structural_trades(p)))

    if not trades:
        print("No trades found", file=sys.stderr)
        return 2

    # Assign each trade to calendar day and universe arm
    before_by_day: dict[str, list[p254.TradeRow]] = defaultdict(list)
    after_by_day: dict[str, list[p254.TradeRow]] = defaultdict(list)

    for t in trades:
        day = phase252._trade_yyyymmdd(t)
        if not day or day not in universe_by_date:
            continue
        if t.symbol in universe_by_date[day]:
            before_by_day[day].append(t)
        if t.symbol in price_floor_by_date[day]:
            after_by_day[day].append(t)

    all_days = sorted(set(universe_by_date) | set(before_by_day) | set(after_by_day))
    daily_rows: list[dict[str, Any]] = []
    pnl_counts = {"improved": 0, "worsened": 0, "unchanged": 0}
    pf_counts = {"improved": 0, "worsened": 0, "unchanged": 0}

    for day in all_days:
        bt = before_by_day.get(day, [])
        at = after_by_day.get(day, [])
        mb = p254._metrics(bt)
        ma = p254._metrics(at)
        pnl_b = float(mb["pnl_pct_sum"])
        pnl_a = float(ma["pnl_pct_sum"])
        verdict_pnl = _classify_day(pnl_b, pnl_a)
        verdict_pf = _classify_pf(mb["profit_factor"], ma["profit_factor"])
        pnl_counts[verdict_pnl] += 1
        pf_counts[verdict_pf] += 1
        daily_rows.append(
            {
                "trade_date": day,
                "PF_before": mb["profit_factor"],
                "PF_after": ma["profit_factor"],
                "PnL_before": pnl_b,
                "PnL_after": pnl_a,
                "trade_count_before": mb["trade_count"],
                "trade_count_after": ma["trade_count"],
                "delta_pnl": round(pnl_a - pnl_b, 6),
                "delta_trade_count": ma["trade_count"] - mb["trade_count"],
                "verdict_by_pnl": verdict_pnl,
                "verdict_by_pf": verdict_pf,
            }
        )

    total_before = p254._metrics([t for rows in before_by_day.values() for t in rows])
    total_after = p254._metrics([t for rows in after_by_day.values() for t in rows])

    payload: dict[str, Any] = {
        "phase": 255,
        "purpose": "close<300 除外の安定性確認（日別 before/after）",
        "generated_at_jst": _now_jst_iso(),
        "constraints": {
            "review_only": True,
            "no_entry_change": True,
            "no_yaml_change": True,
            "no_production_change": True,
        },
        "population": {
            "name": "Phase254_same",
            "description": "operational dynamic40 by trade_date (before) vs price-floor refill (after)",
            "universe_days": len(universe_by_date),
            "trade_days_with_any_trade": len({d for d in all_days if before_by_day.get(d) or after_by_day.get(d)}),
        },
        "classification_rules": {
            "primary_verdict": "PnL_after vs PnL_before",
            "improved": f"PnL_after > PnL_before + {PNL_EPS}",
            "worsened": f"PnL_after < PnL_before - {PNL_EPS}",
            "unchanged": "otherwise",
            "secondary_verdict": "PF_after vs PF_before (reference)",
        },
        "stability_summary": {
            "by_pnl": {
                "improved_days": pnl_counts["improved"],
                "worsened_days": pnl_counts["worsened"],
                "unchanged_days": pnl_counts["unchanged"],
                "total_days": len(daily_rows),
                "improved_share": round(pnl_counts["improved"] / len(daily_rows), 4) if daily_rows else None,
            },
            "by_pf": {
                "improved_days": pf_counts["improved"],
                "worsened_days": pf_counts["worsened"],
                "unchanged_days": pf_counts["unchanged"],
                "total_days": len(daily_rows),
            },
        },
        "totals_all_days": {
            "before": {
                "PF": total_before["profit_factor"],
                "PnL": total_before["pnl_pct_sum"],
                "trade_count": total_before["trade_count"],
            },
            "after": {
                "PF": total_after["profit_factor"],
                "PnL": total_after["pnl_pct_sum"],
                "trade_count": total_after["trade_count"],
            },
        },
        "daily": daily_rows,
    }

    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    s = payload["stability_summary"]["by_pnl"]
    print(f"Wrote: {out_path}")
    print(
        f"days={s['total_days']} improved={s['improved_days']} "
        f"worsened={s['worsened_days']} unchanged={s['unchanged_days']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
