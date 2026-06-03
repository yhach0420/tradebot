#!/usr/bin/env python3
"""
Phase252: Universe Counterfactual (review-only)

現行40（vol_liq 上位40）vs 改善Universe（除外・優先ルールで再構築40）を
過去トレードでカウンターファクト比較（PF / PnL / trade_count）。

除外候補: 情報・通信業, 非鉄金属, 300円未満
優先候補: 電気機器, TOPIX Small 1, TOPIX Small 2

出力: kabu_native/results/reports/phase252_universe_counterfactual.json
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore[misc, assignment]

JST = ZoneInfo("Asia/Tokyo") if ZoneInfo else None

UNIVERSE_SIZE = 40

# Phase251/ JPX 表記: 情報・通信業 / 非鉄金属
EXCLUDE_SECTORS = frozenset({"情報・通信業", "情報通信", "非鉄金属"})
PRIORITY_SECTOR = "電気機器"
PRIORITY_SCALES = frozenset({"TOPIX Small 1", "TOPIX Small 2"})

SECTOR_PRIORITY_MULT = 1.25
SCALE_PRIORITY_MULT = 1.15


def _load_phase251_module(script_dir: Path):
    path = script_dir / "run_phase251_universe_discovery.py"
    spec = importlib.util.spec_from_file_location("phase251", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load phase251 module from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _now_jst_iso() -> str:
    dt = datetime.now().astimezone(JST) if JST else datetime.now()
    return dt.isoformat(timespec="seconds")


def _latest_features_csv(reports_dir: Path) -> Path | None:
    candidates = sorted(reports_dir.glob("features_*.csv"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _latest_operational_universe_csv(reports_dir: Path) -> Path | None:
    """Prefer price_risk PM universe (latest operational dynamic40 snapshot)."""
    candidates = sorted(reports_dir.glob("universe_core10_dynamic40_price_risk_pm_*.csv"))
    if not candidates:
        candidates = sorted(reports_dir.glob("universe_core10_dynamic40_pm_*.csv"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _read_operational_dynamic40(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if str(row.get("universe_slot") or "").lower() != "dynamic":
                continue
            sym = str(row.get("symbol") or "").strip()
            if not sym:
                continue
            if not sym.endswith(".T"):
                sym = f"{sym}.T"
            try:
                vl = float(row.get("volatility_liquidity_score") or 0)
            except ValueError:
                vl = 0.0
            try:
                close = float(row.get("close_price") or row.get("close") or 0)
            except ValueError:
                close = None
            rows.append(
                {
                    "symbol": sym,
                    "volatility_liquidity_score": vl,
                    "close": close,
                    "rank": int(row.get("rank") or 0),
                    "source_bucket": row.get("source_bucket"),
                    "selection": "operational_dynamic40_csv",
                }
            )
    rows.sort(key=lambda r: r.get("rank") or 999)
    return rows[:UNIVERSE_SIZE]


def _load_universe_by_trade_date(reports_dir: Path) -> dict[str, set[str]]:
    """Map YYYYMMDD -> union(dynamic symbols) from AM+PM operational CSV that day."""
    by_date: dict[str, set[str]] = {}
    patterns = (
        "universe_core10_dynamic40_price_risk_am_*.csv",
        "universe_core10_dynamic40_price_risk_pm_*.csv",
        "universe_core10_dynamic40_am_*.csv",
        "universe_core10_dynamic40_pm_*.csv",
    )
    for pat in patterns:
        for path in reports_dir.glob(pat):
            stem = path.stem
            day = stem.split("_")[-1]
            if len(day) != 8 or not day.isdigit():
                continue
            syms = {r["symbol"] for r in _read_operational_dynamic40(path)}
            by_date.setdefault(day, set()).update(syms)
    return by_date


def _trade_yyyymmdd(trade: Any) -> str | None:
    path = str(getattr(trade, "source_path", "") or "")
    for part in Path(path).parts:
        if len(part) == 8 and part.isdigit() and part.startswith("202"):
            return part
    return None


def _read_features(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            sym = str(row.get("symbol") or "").strip()
            if not sym:
                continue
            if not sym.endswith(".T"):
                sym = f"{sym}.T"
            try:
                vl = float(row.get("volatility_liquidity_score") or 0)
            except ValueError:
                vl = 0.0
            try:
                close = float(row.get("close") or 0)
            except ValueError:
                close = None
            rows.append(
                {
                    "symbol": sym,
                    "volatility_liquidity_score": vl,
                    "close": close,
                    "trading_value": row.get("trading_value"),
                    "trade_date": row.get("trade_date"),
                }
            )
    return rows


def _sector(sym: str, jpx: dict[str, dict[str, Any]]) -> str:
    return str((jpx.get(sym) or {}).get("sector_33_name") or "unknown")


def _scale(sym: str, jpx: dict[str, dict[str, Any]]) -> str:
    return str((jpx.get(sym) or {}).get("scale_category") or "unknown")


def _is_excluded(sym: str, *, close: float | None, jpx: dict[str, dict[str, Any]]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    sec = _sector(sym, jpx)
    if sec in EXCLUDE_SECTORS:
        reasons.append(f"sector:{sec}")
    if close is not None and close < 300:
        reasons.append("price_lt_300")
    return bool(reasons), reasons


def _adjusted_score(base_vl: float, sym: str, jpx: dict[str, dict[str, Any]]) -> float:
    score = base_vl
    sec = _sector(sym, jpx)
    scale = _scale(sym, jpx)
    if sec == PRIORITY_SECTOR:
        score *= SECTOR_PRIORITY_MULT
    if scale in PRIORITY_SCALES:
        score *= SCALE_PRIORITY_MULT
    return score


def _select_current_top40(feature_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(feature_rows, key=lambda r: r["volatility_liquidity_score"], reverse=True)
    out: list[dict[str, Any]] = []
    for i, row in enumerate(ranked[:UNIVERSE_SIZE], start=1):
        sym = row["symbol"]
        out.append(
            {
                "rank": i,
                "symbol": sym,
                "volatility_liquidity_score": row["volatility_liquidity_score"],
                "adjusted_score": row["volatility_liquidity_score"],
                "close": row.get("close"),
                "sector_33_name": None,
                "scale_category": None,
                "selection": "vol_liq_top40",
            }
        )
    return out


def _select_improved_top40(
    feature_rows: list[dict[str, Any]],
    jpx: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Returns (selected rows, excluded candidates that were in top-80 by raw vol_liq)."""
    ranked_raw = sorted(feature_rows, key=lambda r: r["volatility_liquidity_score"], reverse=True)

    eligible: list[dict[str, Any]] = []
    excluded_log: list[dict[str, Any]] = []
    for row in ranked_raw:
        sym = row["symbol"]
        close = row.get("close")
        if isinstance(close, str):
            try:
                close = float(close)
            except ValueError:
                close = None
        is_ex, reasons = _is_excluded(sym, close=close, jpx=jpx)
        meta = jpx.get(sym, {})
        entry = {
            "symbol": sym,
            "volatility_liquidity_score": row["volatility_liquidity_score"],
            "adjusted_score": _adjusted_score(row["volatility_liquidity_score"], sym, jpx),
            "close": close,
            "sector_33_name": meta.get("sector_33_name"),
            "scale_category": meta.get("scale_category"),
            "exclude_reasons": reasons,
        }
        if is_ex:
            excluded_log.append(entry)
        else:
            eligible.append(entry)

    eligible.sort(key=lambda r: r["adjusted_score"], reverse=True)
    selected: list[dict[str, Any]] = []
    for i, row in enumerate(eligible[:UNIVERSE_SIZE], start=1):
        selected.append({**row, "rank": i, "selection": "improved_top40"})

    return selected, excluded_log


def _enrich_universe_rows(rows: list[dict[str, Any]], jpx: dict[str, dict[str, Any]]) -> None:
    for row in rows:
        sym = row["symbol"]
        meta = jpx.get(sym, {})
        row["sector_33_name"] = meta.get("sector_33_name")
        row["scale_category"] = meta.get("scale_category")


def _symbol_set(rows: list[dict[str, Any]]) -> set[str]:
    return {str(r["symbol"]) for r in rows}


def main() -> int:
    script = Path(__file__).resolve()
    native_root = script.parents[1]
    repo_root = script.parents[2]
    reports_dir = native_root / "results" / "reports"

    parser = argparse.ArgumentParser(description="Phase252 universe counterfactual (review-only)")
    parser.add_argument("--results-root", type=Path, default=native_root / "results")
    parser.add_argument(
        "--features-csv",
        type=Path,
        default=None,
        help="features_YYYYMMDD.csv (default: latest in results/reports)",
    )
    parser.add_argument(
        "--universe-csv",
        type=Path,
        default=None,
        help="operational universe CSV (default: latest price_risk PM)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=reports_dir / "phase252_universe_counterfactual.json",
    )
    args = parser.parse_args()

    phase251 = _load_phase251_module(script.parent)
    results_root = args.results_root if args.results_root.is_absolute() else (repo_root / args.results_root)
    out_path = args.out if args.out.is_absolute() else (repo_root / args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    features_path = args.features_csv
    if features_path is None:
        features_path = _latest_features_csv(reports_dir)
    if features_path is None or not features_path.is_file():
        print("features_*.csv not found", file=sys.stderr)
        return 2

    feature_rows = _read_features(features_path)
    jpx = phase251._read_jpx_master(repo_root)

    universe_path = args.universe_csv
    if universe_path is None:
        universe_path = _latest_operational_universe_csv(reports_dir)
    if universe_path is None or not universe_path.is_file():
        print("operational universe CSV not found", file=sys.stderr)
        return 2

    current_rows = _read_operational_dynamic40(universe_path)
    _enrich_universe_rows(current_rows, jpx)
    for row in current_rows:
        row["adjusted_score"] = row.get("volatility_liquidity_score")

    improved_rows, excluded_by_rule = _select_improved_top40(feature_rows, jpx)
    vol_liq_top40_rows = _select_current_top40(feature_rows)
    _enrich_universe_rows(vol_liq_top40_rows, jpx)

    current_set = _symbol_set(current_rows)
    improved_set = _symbol_set(improved_rows)
    universe_by_date = _load_universe_by_trade_date(reports_dir)

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

    def _filter_trades(sym_set: set[str]) -> list[Any]:
        return [t for t in trades if t.symbol in sym_set]

    def _filter_trades_by_session_universe() -> list[Any]:
        """Match trade to that day's AM+PM dynamic40 union when date folder exists."""
        kept: list[Any] = []
        for t in trades:
            day = _trade_yyyymmdd(t)
            if day and day in universe_by_date:
                if t.symbol in universe_by_date[day]:
                    kept.append(t)
            elif t.symbol in current_set:
                kept.append(t)
        return kept

    baseline_trades = _filter_trades(current_set)
    baseline_trades_by_date = _filter_trades_by_session_universe()
    improved_trades = _filter_trades(improved_set)

    def _trade_excluded(t: Any) -> bool:
        close = None
        for row in feature_rows:
            if row["symbol"] == t.symbol:
                close = row.get("close")
                break
        if close is None and t.entry_price is not None:
            close = t.entry_price
        return _is_excluded(t.symbol, close=close, jpx=jpx)[0]

    all_trades_exclusion_filtered = [t for t in trades if not _trade_excluded(t)]
    operational_exclusion_filtered = [t for t in baseline_trades_by_date if not _trade_excluded(t)]

    # Counterfactual: apply exclusion rules to baseline universe (no refill)
    baseline_exclusion_only = {
        s
        for s in current_set
        if not _is_excluded(
            s,
            close=next((r.get("close") for r in current_rows if r["symbol"] == s), None),
            jpx=jpx,
        )[0]
    }
    exclusion_only_trades = _filter_trades(baseline_exclusion_only)

    def _sym_metrics(sym_set: set[str]) -> dict[str, Any]:
        subset = [t for t in trades if t.symbol in sym_set]
        by_sym: dict[str, list[Any]] = {}
        for t in subset:
            by_sym.setdefault(t.symbol, []).append(t)
        return {
            sym: phase251._summarize(rows)
            for sym, rows in sorted(by_sym.items(), key=lambda x: -len(x[1]))
        }

    payload: dict[str, Any] = {
        "phase": 252,
        "purpose": "Universe Counterfactual: 現行40 vs 改善Universe（review-only）",
        "generated_at_jst": _now_jst_iso(),
        "constraints": {
            "review_only": True,
            "no_entry_change": True,
            "no_yaml_change": True,
            "no_production_change": True,
        },
        "rules": {
            "universe_size": UNIVERSE_SIZE,
            "current_selection": "operational dynamic40 from universe_core10_dynamic40 CSV (universe_slot=dynamic)",
            "improved_exclusions": {
                "sectors": sorted(EXCLUDE_SECTORS),
                "price": "close < 300 (from features)",
            },
            "improved_priorities": {
                "sector": PRIORITY_SECTOR,
                "sector_multiplier": SECTOR_PRIORITY_MULT,
                "scales": sorted(PRIORITY_SCALES),
                "scale_multiplier": SCALE_PRIORITY_MULT,
                "selection": "eligible pool ranked by adjusted_score, top 40",
            },
        },
        "inputs": {
            "features_csv": str(features_path),
            "features_row_count": len(feature_rows),
            "operational_universe_csv": str(universe_path),
            "jpx_master_loaded": bool(jpx),
            "trade_sources": {
                "replay_trades_csv": len(replay_paths),
                "structural_trades_csv": len(small_paths),
            },
            "session_universe_dates_loaded": len(universe_by_date),
        },
        "universes": {
            "current_operational_dynamic40": current_rows,
            "improved_rebuilt_dynamic40": improved_rows,
            "reference_vol_liq_top40_from_features": vol_liq_top40_rows,
            "overlap_symbols": sorted(current_set & improved_set),
            "only_in_current": sorted(current_set - improved_set),
            "only_in_improved": sorted(improved_set - current_set),
            "excluded_by_improved_rules_sample": excluded_by_rule[:20],
        },
        "comparison": {
            "current_operational_dynamic40": {
                "symbol_count": len(current_set),
                "label": "現行40（最新 operational dynamic40 CSV）",
                **phase251._summarize(baseline_trades),
            },
            "current_operational_by_trade_date": {
                "symbol_count": len({t.symbol for t in baseline_trades_by_date}),
                "label": "現行40（取引日ごと AM+PM dynamic40 ユニオン）",
                **phase251._summarize(baseline_trades_by_date),
            },
            "improved_rebuilt_dynamic40": {
                "symbol_count": len(improved_set),
                "label": "改善Universe（除外+優先で features から再構築40）",
                **phase251._summarize(improved_trades),
            },
            "delta_improved_minus_current_operational": {
                "trade_count": phase251._summarize(improved_trades)["trade_count"]
                - phase251._summarize(baseline_trades)["trade_count"],
                "pnl_pct_sum": round(
                    phase251._summarize(improved_trades)["pnl_pct_sum"]
                    - phase251._summarize(baseline_trades)["pnl_pct_sum"],
                    6,
                ),
                "profit_factor_note": "PF は単純差分ではなく各集合で gross_profit/loss から算出",
            },
            "baseline_exclusion_only_no_refill": {
                "symbol_count": len(baseline_exclusion_only),
                "note": "現行40から除外ルールのみ適用（枠40の再充填なし）",
                **phase251._summarize(exclusion_only_trades),
            },
            "all_trades_exclusion_filter_only": {
                "label": "全トレードに除外ルールのみ（40枠・優先なし）",
                **phase251._summarize(all_trades_exclusion_filtered),
            },
            "operational_by_date_exclusion_filter_only": {
                "label": "現行運用ユニバース一致トレードに除外ルールのみ",
                **phase251._summarize(operational_exclusion_filtered),
            },
        },
        "comparison_by_source_kind": [],
        "trades_removed_by_improved_vs_current": phase251._summarize(
            [t for t in baseline_trades if t.symbol not in improved_set]
        ),
        "trades_gained_by_improved_vs_current": phase251._summarize(
            [t for t in improved_trades if t.symbol not in current_set]
        ),
        "per_symbol_in_current": _sym_metrics(current_set),
        "per_symbol_in_improved": _sym_metrics(improved_set),
    }

    for kind in sorted({t.source_kind for t in trades}):
        bt = [t for t in baseline_trades if t.source_kind == kind]
        it = [t for t in improved_trades if t.source_kind == kind]
        payload["comparison_by_source_kind"].append(
            {
                "source_kind": kind,
                "current_operational_dynamic40": phase251._summarize(bt),
                "improved_rebuilt_dynamic40": phase251._summarize(it),
            }
        )

    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    c = payload["comparison"]["current_operational_dynamic40"]
    i = payload["comparison"]["improved_rebuilt_dynamic40"]
    cd = payload["comparison"]["current_operational_by_trade_date"]
    print(f"Wrote: {out_path}")
    print(
        f"current(op): trades={c['trade_count']} pnl={c['pnl_pct_sum']:.4f} pf={c['profit_factor']}"
    )
    print(
        f"current(by_date): trades={cd['trade_count']} pnl={cd['pnl_pct_sum']:.4f} pf={cd['profit_factor']}"
    )
    print(
        f"improved: trades={i['trade_count']} pnl={i['pnl_pct_sum']:.4f} pf={i['profit_factor']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
