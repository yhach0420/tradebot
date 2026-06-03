#!/usr/bin/env python3
"""
Phase253: Phase252 改善要因の分解（review-only）

母集団: 現行運用 Universe（Phase252 current_operational_by_trade_date）
  - 取引日ごと AM+PM dynamic40 ユニオンに一致するトレード

シナリオ:
  A 現状
  B price < 300 除外のみ
  C 情報通信除外のみ
  D 非鉄金属除外のみ
  E price<300 + 情報通信
  F price<300 + 非鉄
  G 情報通信 + 非鉄

出力: kabu_native/results/reports/phase253_exclusion_attribution.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore[misc, assignment]

JST = ZoneInfo("Asia/Tokyo") if ZoneInfo else None

INFO_SECTORS = frozenset({"情報・通信業", "情報通信"})
NONFERROUS_SECTORS = frozenset({"非鉄金属"})

SCENARIOS: dict[str, str] = {
    "A": "現状（除外なし）",
    "B": "price < 300 除外のみ",
    "C": "情報通信除外のみ",
    "D": "非鉄金属除外のみ",
    "E": "price < 300 + 情報通信",
    "F": "price < 300 + 非鉄金属",
    "G": "情報通信 + 非鉄金属",
}


def _load_phase252_module(script_dir: Path):
    path = script_dir / "run_phase252_universe_counterfactual.py"
    spec = importlib.util.spec_from_file_location("phase252", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load phase252 module from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _now_jst_iso() -> str:
    dt = datetime.now().astimezone(JST) if JST else datetime.now()
    return dt.isoformat(timespec="seconds")


def _summarize_with_avg(trades: list[Any], phase251: Any) -> dict[str, Any]:
    base = phase251._summarize(trades)
    n = base["trade_count"]
    avg = (base["pnl_pct_sum"] / n) if n else None
    return {
        "trade_count": n,
        "pnl_pct_sum": round(base["pnl_pct_sum"], 6),
        "profit_factor": base["profit_factor"],
        "avg_pnl_pct": round(avg, 6) if avg is not None else None,
    }


def main() -> int:
    script = Path(__file__).resolve()
    native_root = script.parents[1]
    repo_root = script.parents[2]
    reports_dir = native_root / "results" / "reports"

    parser = argparse.ArgumentParser(description="Phase253 exclusion attribution (review-only)")
    parser.add_argument("--results-root", type=Path, default=native_root / "results")
    parser.add_argument(
        "--out",
        type=Path,
        default=reports_dir / "phase253_exclusion_attribution.json",
    )
    args = parser.parse_args()

    phase252 = _load_phase252_module(script.parent)
    phase251 = phase252._load_phase251_module(script.parent)

    results_root = args.results_root if args.results_root.is_absolute() else (repo_root / args.results_root)
    out_path = args.out if args.out.is_absolute() else (repo_root / args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    features_path = phase252._latest_features_csv(reports_dir)
    if features_path is None or not features_path.is_file():
        print("features_*.csv not found", file=sys.stderr)
        return 2

    feature_rows = phase252._read_features(features_path)
    close_by_sym: dict[str, float | None] = {r["symbol"]: r.get("close") for r in feature_rows}
    jpx = phase251._read_jpx_master(repo_root)
    universe_by_date = phase252._load_universe_by_trade_date(reports_dir)

    replay_paths = sorted((results_root / "replay").rglob("trades.csv"))
    small_paths = sorted((results_root / "small_paper").rglob("structural_trades.csv"))
    trades: list[Any] = []
    for p in replay_paths:
        trades.extend(list(phase251._iter_replay_trades_csv(p)))
    for p in small_paths:
        trades.extend(list(phase251._iter_structural_trades_csv(p)))
    if not trades:
        print(f"No trades under {results_root}", file=sys.stderr)
        return 2

    def _in_operational_universe(t: Any) -> bool:
        day = phase252._trade_yyyymmdd(t)
        if day and day in universe_by_date:
            return t.symbol in universe_by_date[day]
        return False

    baseline = [t for t in trades if _in_operational_universe(t)]
    if not baseline:
        print("No trades in operational-by-date universe", file=sys.stderr)
        return 2

    def _close_for(sym: str, trade: Any) -> float | None:
        c = close_by_sym.get(sym)
        if c is not None:
            return float(c)
        if trade.entry_price is not None:
            return float(trade.entry_price)
        return None

    def _is_info(sym: str) -> bool:
        return phase252._sector(sym, jpx) in INFO_SECTORS

    def _is_nonferrous(sym: str) -> bool:
        return phase252._sector(sym, jpx) in NONFERROUS_SECTORS

    def _is_low_price(sym: str, trade: Any) -> bool:
        c = _close_for(sym, trade)
        return c is not None and c < 300

    def _excluded_B(t: Any) -> bool:
        return _is_low_price(t.symbol, t)

    def _excluded_C(t: Any) -> bool:
        return _is_info(t.symbol)

    def _excluded_D(t: Any) -> bool:
        return _is_nonferrous(t.symbol)

    filters: dict[str, Callable[[Any], bool]] = {
        "A": lambda t: False,
        "B": _excluded_B,
        "C": _excluded_C,
        "D": _excluded_D,
        "E": lambda t: _excluded_B(t) or _excluded_C(t),
        "F": lambda t: _excluded_B(t) or _excluded_D(t),
        "G": lambda t: _excluded_C(t) or _excluded_D(t),
    }

    scenario_results: dict[str, Any] = {}
    removed_breakdown: dict[str, Any] = {}

    for key in "ABCDEFG":
        is_excluded = filters[key]
        kept = [t for t in baseline if not is_excluded(t)]
        removed = [t for t in baseline if is_excluded(t)]
        scenario_results[key] = {
            "label": SCENARIOS[key],
            **_summarize_with_avg(kept, phase251),
            "trades_removed": len(removed),
            "pnl_pct_removed": round(sum(t.pnl_pct for t in removed), 6),
        }
        removed_breakdown[key] = phase251._summarize(removed)

    a = scenario_results["A"]
    attribution_vs_a: dict[str, Any] = {}
    for key in "BCDEFG":
        s = scenario_results[key]
        attribution_vs_a[key] = {
            "delta_trade_count": s["trade_count"] - a["trade_count"],
            "delta_pnl_pct_sum": round(s["pnl_pct_sum"] - a["pnl_pct_sum"], 6),
            "delta_avg_pnl_pct": round((s["avg_pnl_pct"] or 0) - (a["avg_pnl_pct"] or 0), 6)
            if s["avg_pnl_pct"] is not None and a["avg_pnl_pct"] is not None
            else None,
            "profit_factor": s["profit_factor"],
        }

    # Marginal contribution hints (single-filter scenarios vs A)
    marginal: dict[str, Any] = {}
    for key, label in (
        ("B", "price_lt_300"),
        ("C", "info_comms"),
        ("D", "nonferrous"),
    ):
        r = scenario_results[key]
        marginal[label] = {
            "trades_removed": r["trades_removed"],
            "pnl_pct_removed": r["pnl_pct_removed"],
            "pnl_pct_sum_after_filter": r["pnl_pct_sum"],
            "profit_factor_after_filter": r["profit_factor"],
        }

    payload: dict[str, Any] = {
        "phase": 253,
        "purpose": "Phase252 改善要因の分解（除外ルール単独・組合せ）",
        "generated_at_jst": _now_jst_iso(),
        "constraints": {
            "review_only": True,
            "no_entry_change": True,
            "no_yaml_change": True,
            "no_production_change": True,
        },
        "population": {
            "name": "current_operational_by_trade_date",
            "description": "取引日ごと AM+PM dynamic40 ユニオンに一致するトレード（Phase252 同定義）",
            "baseline_trade_count": len(baseline),
            "session_universe_dates_loaded": len(universe_by_date),
            "features_csv": str(features_path),
            "jpx_master_loaded": bool(jpx),
        },
        "rules": {
            "price_lt_300": "features.close または entry_price < 300",
            "info_comms_sectors": sorted(INFO_SECTORS),
            "nonferrous_sectors": sorted(NONFERROUS_SECTORS),
        },
        "scenarios": scenario_results,
        "removed_trades_summary": removed_breakdown,
        "attribution_vs_A": attribution_vs_a,
        "marginal_single_filters": marginal,
    }

    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote: {out_path}")
    for key in "ABCDEFG":
        s = scenario_results[key]
        print(
            f"{key}: trades={s['trade_count']} pnl={s['pnl_pct_sum']:.4f} "
            f"pf={s['profit_factor']} avg={s['avg_pnl_pct']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
