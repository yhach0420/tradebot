#!/usr/bin/env python3
"""
Phase254: close < 300 除外を Universe 改善候補として評価（review-only）

比較: 現行 Universe vs close<300 除外 Universe
対象: 全 push_replay / replay / live

出力: kabu_native/results/reports/phase254_price_floor_adoption_review.json
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore[misc, assignment]

JST = ZoneInfo("Asia/Tokyo") if ZoneInfo else None
UNIVERSE_SIZE = 40
PRICE_FLOOR = 300.0

STOP_EXIT_REASONS = frozenset(
    {
        "stop_hit",
        "hard_stop",
        "breakout_failure",
    }
)


@dataclass(frozen=True)
class TradeRow:
    symbol: str
    entry_price: float | None
    pnl_pct: float
    source_kind: str
    source_path: str
    exit_reason: str


def _load_phase252_module(script_dir: Path):
    path = script_dir / "run_phase252_universe_counterfactual.py"
    spec = importlib.util.spec_from_file_location("phase252", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load phase252 from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _now_jst_iso() -> str:
    dt = datetime.now().astimezone(JST) if JST else datetime.now()
    return dt.isoformat(timespec="seconds")


def _norm_symbol(sym: str) -> str:
    sym = str(sym or "").strip()
    if not sym:
        return ""
    return sym if sym.endswith(".T") else f"{sym}.T"


def _infer_kind(path: Path) -> str:
    s = str(path).replace("\\", "/").lower()
    if "/results/replay/" in s:
        return "replay"
    if "/push_replay_" in s:
        return "push_replay"
    if "/live_" in s:
        return "live"
    return "unknown"


def _iter_replay_trades(path: Path) -> Iterable[TradeRow]:
    kind = _infer_kind(path)
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            sym = _norm_symbol(row.get("symbol") or "")
            if not sym:
                continue
            try:
                ep = float(row["entry_price"]) if row.get("entry_price") not in (None, "") else None
            except ValueError:
                ep = None
            try:
                pnl = float(row.get("pnl_pct") or 0.0)
            except ValueError:
                pnl = 0.0
            yield TradeRow(
                symbol=sym,
                entry_price=ep,
                pnl_pct=pnl,
                source_kind=kind,
                source_path=str(path),
                exit_reason=str(row.get("exit_reason") or ""),
            )


def _iter_structural_trades(path: Path) -> Iterable[TradeRow]:
    kind = _infer_kind(path)
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            sym = _norm_symbol(row.get("symbol") or "")
            if not sym:
                continue
            try:
                ep = float(row["entry_price"]) if row.get("entry_price") not in (None, "") else None
            except ValueError:
                ep = None
            try:
                pnl = float(row.get("realized_pnl_pct") or 0.0)
            except ValueError:
                pnl = 0.0
            yield TradeRow(
                symbol=sym,
                entry_price=ep,
                pnl_pct=pnl,
                source_kind=kind,
                source_path=str(path),
                exit_reason=str(row.get("close_reason") or ""),
            )


def _pf(pnls: list[float]) -> float | None:
    gp = sum(p for p in pnls if p > 0)
    gl = abs(sum(p for p in pnls if p < 0))
    if gl > 0:
        return gp / gl
    if gp > 0:
        return None
    return None


def _metrics(trades: list[TradeRow]) -> dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {
            "trade_count": 0,
            "pnl_pct_sum": 0.0,
            "profit_factor": None,
            "win_rate": None,
            "stop_rate": None,
            "avg_pnl_pct": None,
        }
    pnls = [t.pnl_pct for t in trades]
    wins = sum(1 for p in pnls if p > 0)
    stops = sum(1 for t in trades if t.exit_reason in STOP_EXIT_REASONS)
    return {
        "trade_count": n,
        "pnl_pct_sum": round(sum(pnls), 6),
        "profit_factor": _pf(pnls),
        "win_rate": round(wins / n, 4),
        "stop_rate": round(stops / n, 4),
        "avg_pnl_pct": round(sum(pnls) / n, 6),
    }


def _load_features_by_date(reports_dir: Path) -> dict[str, list[dict[str, Any]]]:
    by_date: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(reports_dir.glob("features_*.csv")):
        day = path.stem.replace("features_", "")
        if len(day) != 8 or not day.isdigit():
            continue
        by_date[day] = []
        with path.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                sym = _norm_symbol(row.get("symbol") or "")
                if not sym:
                    continue
                try:
                    vl = float(row.get("volatility_liquidity_score") or 0)
                except ValueError:
                    vl = 0.0
                try:
                    close = float(row["close"]) if row.get("close") not in (None, "") else None
                except ValueError:
                    close = None
                by_date[day].append(
                    {
                        "symbol": sym,
                        "volatility_liquidity_score": vl,
                        "close": close,
                    }
                )
    return by_date


def _select_price_floor_top40(
    feature_rows: list[dict[str, Any]],
    jpx: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    ranked = sorted(feature_rows, key=lambda r: r["volatility_liquidity_score"], reverse=True)
    out: list[dict[str, Any]] = []
    for row in ranked:
        close = row.get("close")
        if close is not None and float(close) < PRICE_FLOOR:
            continue
        sym = row["symbol"]
        meta = jpx.get(sym, {})
        out.append(
            {
                "symbol": sym,
                "close": close,
                "volatility_liquidity_score": row["volatility_liquidity_score"],
                "sector_33_name": meta.get("sector_33_name"),
                "scale_category": meta.get("scale_category"),
            }
        )
        if len(out) >= UNIVERSE_SIZE:
            break
    for i, row in enumerate(out, start=1):
        row["rank"] = i
    return out


def _composition_summary(symbols: list[dict[str, Any]]) -> dict[str, Any]:
    sectors = Counter(str(r.get("sector_33_name") or "unknown") for r in symbols)
    scales = Counter(str(r.get("scale_category") or "unknown") for r in symbols)
    prices = [float(r["close"]) for r in symbols if r.get("close") is not None]
    return {
        "symbol_count": len(symbols),
        "sector_33_distribution": dict(sectors.most_common()),
        "scale_category_distribution": dict(scales.most_common()),
        "close_min": min(prices) if prices else None,
        "close_median": sorted(prices)[len(prices) // 2] if prices else None,
        "close_max": max(prices) if prices else None,
        "symbols_below_300": sum(1 for p in prices if p < PRICE_FLOOR),
    }


def _apply_price_floor_with_refill(
    current_syms: set[str],
    feature_rows: list[dict[str, Any]],
) -> tuple[set[str], list[str], list[str]]:
    """
    現行 dynamic 集合から close<300 を除外し、同数だけ vol_liq 上位で補充。
    Returns (new_set, removed_symbols, replacement_symbols).
    """
    close_map = {r["symbol"]: r.get("close") for r in feature_rows}
    kept = {
        s
        for s in current_syms
        if close_map.get(s) is None or float(close_map[s]) >= PRICE_FLOOR
    }
    removed = sorted(current_syms - kept)
    need = max(0, len(current_syms) - len(kept))
    replacements: list[str] = []
    if need > 0:
        ranked = sorted(feature_rows, key=lambda r: r["volatility_liquidity_score"], reverse=True)
        for row in ranked:
            sym = row["symbol"]
            if sym in kept:
                continue
            c = row.get("close")
            if c is not None and float(c) < PRICE_FLOOR:
                continue
            replacements.append(sym)
            if len(replacements) >= need:
                break
    return kept | set(replacements), removed, replacements


def _diff_universe(
    current: list[dict[str, Any]],
    revised: list[dict[str, Any]],
) -> dict[str, Any]:
    cur_set = {r["symbol"] for r in current}
    new_set = {r["symbol"] for r in revised}
    removed = sorted(cur_set - new_set)
    added = sorted(new_set - cur_set)
    kept = sorted(cur_set & new_set)
    cur_by_sym = {r["symbol"]: r for r in current}
    removed_detail = [
        {
            "symbol": sym,
            "close": cur_by_sym[sym].get("close"),
            "volatility_liquidity_score": cur_by_sym[sym].get("volatility_liquidity_score"),
            "sector_33_name": cur_by_sym[sym].get("sector_33_name"),
            "removal_reason": "close_lt_300"
            if (cur_by_sym[sym].get("close") is not None and float(cur_by_sym[sym]["close"]) < PRICE_FLOOR)
            else "dropped_by_rerank",
        }
        for sym in removed
    ]
    new_by_sym = {r["symbol"]: r for r in revised}
    added_detail = [
        {
            "symbol": sym,
            "close": new_by_sym[sym].get("close"),
            "volatility_liquidity_score": new_by_sym[sym].get("volatility_liquidity_score"),
            "sector_33_name": new_by_sym[sym].get("sector_33_name"),
            "scale_category": new_by_sym[sym].get("scale_category"),
        }
        for sym in added
    ]
    return {
        "symbols_removed_count": len(removed),
        "symbols_added_count": len(added),
        "symbols_unchanged_count": len(kept),
        "overlap_rate": round(len(kept) / UNIVERSE_SIZE, 4) if UNIVERSE_SIZE else None,
        "removed_symbols": removed,
        "replacement_symbols": added,
        "removed_detail": removed_detail,
        "replacement_detail": added_detail,
        "unchanged_symbols": kept,
    }


def main() -> int:
    script = Path(__file__).resolve()
    native_root = script.parents[1]
    repo_root = script.parents[2]
    reports_dir = native_root / "results" / "reports"

    parser = argparse.ArgumentParser(description="Phase254 price floor adoption review")
    parser.add_argument("--results-root", type=Path, default=native_root / "results")
    parser.add_argument(
        "--out",
        type=Path,
        default=reports_dir / "phase254_price_floor_adoption_review.json",
    )
    args = parser.parse_args()

    phase252 = _load_phase252_module(script.parent)
    phase251 = phase252._load_phase251_module(script.parent)
    results_root = args.results_root if args.results_root.is_absolute() else (repo_root / args.results_root)
    out_path = args.out if args.out.is_absolute() else (repo_root / args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    jpx = phase251._read_jpx_master(repo_root)
    features_by_date = _load_features_by_date(reports_dir)
    if not features_by_date:
        print("features_*.csv not found", file=sys.stderr)
        return 2
    latest_features_day = max(features_by_date)
    latest_features = features_by_date[latest_features_day]

    universe_path = phase252._latest_operational_universe_csv(reports_dir)
    if universe_path is None:
        print("operational universe CSV not found", file=sys.stderr)
        return 2

    current_rows = phase252._read_operational_dynamic40(universe_path)
    phase252._enrich_universe_rows(current_rows, jpx)
    for row in current_rows:
        if row.get("close") is None:
            for fr in latest_features:
                if fr["symbol"] == row["symbol"]:
                    row["close"] = fr.get("close")
                    break

    price_floor_rows = _select_price_floor_top40(latest_features, jpx)
    snapshot_diff = _diff_universe(current_rows, price_floor_rows)

    universe_by_date = phase252._load_universe_by_trade_date(reports_dir)
    # 取引日別: 現行集合に price floor + 補充を適用
    price_floor_by_date: dict[str, set[str]] = {}
    greenfield_by_date: dict[str, set[str]] = {}
    per_day_diffs: list[dict[str, Any]] = []
    for day, cur_syms in sorted(universe_by_date.items()):
        feat = features_by_date.get(day) or latest_features
        pf_syms, removed, replacements = _apply_price_floor_with_refill(cur_syms, feat)
        price_floor_by_date[day] = pf_syms
        greenfield_rows = _select_price_floor_top40(feat, jpx)
        greenfield_by_date[day] = {r["symbol"] for r in greenfield_rows}
        per_day_diffs.append(
            {
                "trade_date": day,
                "current_symbol_count": len(cur_syms),
                "price_floor_refill_symbol_count": len(pf_syms),
                "symbols_removed_count": len(removed),
                "symbols_added_count": len(replacements),
                "removed_symbols": removed,
                "replacement_symbols": replacements,
            }
        )

    replay_paths = sorted((results_root / "replay").rglob("trades.csv"))
    small_paths = sorted((results_root / "small_paper").rglob("structural_trades.csv"))
    trades: list[TradeRow] = []
    for p in replay_paths:
        trades.extend(list(_iter_replay_trades(p)))
    for p in small_paths:
        trades.extend(list(_iter_structural_trades(p)))
    if not trades:
        print("No trades found", file=sys.stderr)
        return 2

    def _trade_day(t: TradeRow) -> str | None:
        return phase252._trade_yyyymmdd(t)

    def _in_current(t: TradeRow) -> bool:
        day = _trade_day(t)
        return bool(day and day in universe_by_date and t.symbol in universe_by_date[day])

    def _in_price_floor_refill(t: TradeRow) -> bool:
        day = _trade_day(t)
        return bool(day and day in price_floor_by_date and t.symbol in price_floor_by_date[day])

    def _in_greenfield(t: TradeRow) -> bool:
        day = _trade_day(t)
        return bool(day and day in greenfield_by_date and t.symbol in greenfield_by_date[day])

    current_trades = [t for t in trades if _in_current(t)]
    price_floor_trades = [t for t in trades if _in_price_floor_refill(t)]
    greenfield_trades = [t for t in trades if _in_greenfield(t)]

    close_by_sym = {r["symbol"]: r.get("close") for r in latest_features}

    def _symbol_close(sym: str, t: TradeRow) -> float | None:
        c = close_by_sym.get(sym)
        if c is not None:
            return float(c)
        if t.entry_price is not None:
            return float(t.entry_price)
        return None

    # Filter-only on same operational population (Phase253 B equivalent)
    filter_only_trades = [
        t
        for t in current_trades
        if (_c := _symbol_close(t.symbol, t)) is None or _c >= PRICE_FLOOR
    ]

    def _by_source(rows: list[TradeRow]) -> list[dict[str, Any]]:
        out = []
        for kind in sorted({t.source_kind for t in rows}):
            sub = [t for t in rows if t.source_kind == kind]
            out.append({"source_kind": kind, **_metrics(sub)})
        return out

    payload: dict[str, Any] = {
        "phase": 254,
        "purpose": "close < 300 除外を Universe 改善候補として評価",
        "generated_at_jst": _now_jst_iso(),
        "constraints": {
            "review_only": True,
            "no_production_change": True,
            "no_entry_change": True,
            "no_yaml_change": True,
        },
        "rules": {
            "price_floor_jpy": PRICE_FLOOR,
            "current_universe": "operational dynamic40 (AM+PM union by trade_date)",
            "price_floor_universe": "operational dynamic: remove close<300, refill same count from vol_liq",
            "greenfield_price_floor_universe": "features vol_liq top40 after excluding close < 300 (reference)",
            "stop_rate_reasons": sorted(STOP_EXIT_REASONS),
            "metrics_note": "PnL/profit_factor は pnl_pct ベース（Phase251/252 同様）",
        },
        "inputs": {
            "operational_universe_csv": str(universe_path),
            "features_latest_date": latest_features_day,
            "features_dates_available": sorted(features_by_date),
            "trade_files": {
                "replay_trades_csv": len(replay_paths),
                "structural_trades_csv": len(small_paths),
            },
        },
        "universe_snapshot_latest": {
            "current_operational_dynamic40": {
                "symbols": current_rows,
                "composition": _composition_summary(current_rows),
            },
            "price_floor_dynamic40": {
                "symbols": price_floor_rows,
                "composition": _composition_summary(price_floor_rows),
            },
            "composition_change": snapshot_diff,
            "monitoring_watchlist_change": {
                "label": "監視銘柄（dynamic40）構成変化 — 最新スナップショット",
                "before_symbols": [r["symbol"] for r in current_rows],
                "after_symbols": [r["symbol"] for r in price_floor_rows],
                **_diff_universe(current_rows, price_floor_rows),
            },
        },
        "universe_by_trade_date_summary": {
            "days": len(per_day_diffs),
            "avg_symbols_removed_per_day": round(
                sum(d["symbols_removed_count"] for d in per_day_diffs) / max(1, len(per_day_diffs)),
                2,
            ),
            "avg_symbols_added_per_day": round(
                sum(d["symbols_added_count"] for d in per_day_diffs) / max(1, len(per_day_diffs)),
                2,
            ),
            "per_day": per_day_diffs,
        },
        "trade_performance": {
            "all_sources_combined": {
                "current_operational_by_trade_date": _metrics(current_trades),
                "price_floor_universe_refill_by_trade_date": _metrics(price_floor_trades),
                "greenfield_price_floor_top40_by_trade_date": _metrics(greenfield_trades),
                "operational_population_price_filter_only": {
                    "note": "同一母集団で close<300 トレードのみ除外（枠替えなし・Phase253-B）",
                    **_metrics(filter_only_trades),
                },
                "delta_refill_vs_current": {
                    "trade_count": _metrics(price_floor_trades)["trade_count"]
                    - _metrics(current_trades)["trade_count"],
                    "pnl_pct_sum": round(
                        _metrics(price_floor_trades)["pnl_pct_sum"]
                        - _metrics(current_trades)["pnl_pct_sum"],
                        6,
                    ),
                    "win_rate": round(
                        (_metrics(price_floor_trades)["win_rate"] or 0)
                        - (_metrics(current_trades)["win_rate"] or 0),
                        4,
                    )
                    if _metrics(price_floor_trades)["win_rate"] is not None
                    else None,
                    "stop_rate": round(
                        (_metrics(price_floor_trades)["stop_rate"] or 0)
                        - (_metrics(current_trades)["stop_rate"] or 0),
                        4,
                    )
                    if _metrics(price_floor_trades)["stop_rate"] is not None
                    else None,
                    "avg_pnl_pct": round(
                        (_metrics(price_floor_trades)["avg_pnl_pct"] or 0)
                        - (_metrics(current_trades)["avg_pnl_pct"] or 0),
                        6,
                    )
                    if _metrics(price_floor_trades)["avg_pnl_pct"] is not None
                    else None,
                },
            },
            "by_source_kind": {
                "current_operational_by_trade_date": _by_source(current_trades),
                "price_floor_universe_refill_by_trade_date": _by_source(price_floor_trades),
            },
        },
    }

    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    cur = payload["trade_performance"]["all_sources_combined"]["current_operational_by_trade_date"]
    pf = payload["trade_performance"]["all_sources_combined"]["price_floor_universe_refill_by_trade_date"]
    filt = payload["trade_performance"]["all_sources_combined"]["operational_population_price_filter_only"]
    print(f"Wrote: {out_path}")
    print(
        f"universe removed={snapshot_diff['symbols_removed_count']} "
        f"added={snapshot_diff['symbols_added_count']}"
    )
    print(
        f"trades current: n={cur['trade_count']} pnl={cur['pnl_pct_sum']} pf={cur['profit_factor']} "
        f"wr={cur['win_rate']} stop={cur['stop_rate']}"
    )
    print(
        f"trades price_floor_refill: n={pf['trade_count']} pnl={pf['pnl_pct_sum']} pf={pf['profit_factor']} "
        f"wr={pf['win_rate']} stop={pf['stop_rate']}"
    )
    print(
        f"trades filter_only: n={filt['trade_count']} pnl={filt['pnl_pct_sum']} pf={filt['profit_factor']} "
        f"wr={filt['win_rate']} stop={filt['stop_rate']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
