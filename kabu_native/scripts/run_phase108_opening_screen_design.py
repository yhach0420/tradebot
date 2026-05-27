#!/usr/bin/env python3
"""
Phase 108: Opening dynamic50 design — previous day + 09:00–09:XX opening features (shadow only).

Outputs design JSON, top50 CSVs at checkpoints, churn analysis.
Does not change production pilot YAML or register PUSH symbols.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parents[2]
NATIVE = ROOT / "kabu_native"
REPORTS = NATIVE / "results" / "reports"
PUSH_ROOT = NATIVE / "data" / "push_jsonl"
CHECKPOINTS = ("09:05", "09:10", "09:15", "09:20")
CHURN_PAIRS = (("09:05", "09:10"), ("09:10", "09:15"), ("09:15", "09:20"), ("09:05", "09:20"))
CHURN_THRESHOLD = 0.25


def _bootstrap() -> None:
    for p in (NATIVE / "src", ROOT):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def write_top50_csv(path: Path, picks: list[Any]) -> None:
    fields = (
        "rank",
        "symbol",
        "symbol_key",
        "exchange",
        "market",
        "opening_daytrade_score",
        "previous_day_vol_liq_score",
        "volatility_liquidity_score",
        "volume_surge_5",
        "atr_pct",
        "trading_value_prev",
        "gap_pct",
        "price_change_pct_5m",
        "range_pct_5m",
        "volume_5m",
        "trading_value_proxy",
        "early_momentum_score",
        "has_opening_push",
        "prev_data_source",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for p in picks:
            prev = p.previous_day
            op = p.opening
            w.writerow(
                {
                    "rank": p.rank,
                    "symbol": p.symbol,
                    "symbol_key": p.symbol_key,
                    "exchange": p.exchange,
                    "market": p.market,
                    "opening_daytrade_score": p.opening_daytrade_score,
                    "previous_day_vol_liq_score": p.previous_day_vol_liq_score,
                    "volatility_liquidity_score": prev.volatility_liquidity_score if prev else "",
                    "volume_surge_5": prev.volume_surge_5 if prev else "",
                    "atr_pct": prev.atr_pct if prev else "",
                    "trading_value_prev": prev.trading_value if prev else "",
                    "gap_pct": op.gap_pct if op else "",
                    "price_change_pct_5m": op.price_change_pct if op else "",
                    "range_pct_5m": op.range_pct if op else "",
                    "volume_5m": op.volume_5m if op else "",
                    "trading_value_proxy": op.trading_value_proxy if op else "",
                    "early_momentum_score": op.early_momentum_score if op else "",
                    "has_opening_push": op.has_push_snapshot if op else False,
                    "prev_data_source": prev.data_source if prev else "",
                }
            )


def determine_verdict(
    *,
    prev_coverage: float,
    opening_coverage_0905: int,
    max_churn: float,
    yfinance_ok: bool,
    push_symbol_count: int,
    push_limit: int = 50,
) -> tuple[str, list[str]]:
    notes: list[str] = []
    if not yfinance_ok and opening_coverage_0905 < push_limit:
        notes.append("no previous-day source and insufficient PUSH opening snapshots")
        return "need_intraday_opening_data", notes

    if max_churn > CHURN_THRESHOLD:
        notes.append(f"max pairwise churn_rate={max_churn:.2%} > {CHURN_THRESHOLD:.0%}")
        return "intraday_rotation_too_churny", notes

    if opening_coverage_0905 < push_limit:
        notes.append(
            f"opening PUSH at 09:05 covers {opening_coverage_0905}/{push_limit} symbols "
            f"(push_jsonl watchlist={push_symbol_count}); full-market opening needs "
            "PUSH expansion or minute-bar store"
        )
        if yfinance_ok and prev_coverage >= 0.1:
            notes.append(
                f"previous_day yfinance coverage={prev_coverage:.1%}; "
                "5-min shadow churn low — recommend 09:05-fixed dynamic50 for PUSH"
            )
            return "use_0905_fixed_dynamic50", notes
        return "need_intraday_opening_data", notes

    if prev_coverage >= 0.5 and opening_coverage_0905 >= push_limit:
        notes.append("full-market previous-day + opening snapshots available")
        return "opening_dynamic50_design_ready", notes

    if yfinance_ok and prev_coverage >= 0.1:
        notes.append(f"partial prev coverage={prev_coverage:.1%}; opening PUSH OK")
        return "opening_dynamic50_design_ready", notes

    notes.append(f"prev_coverage={prev_coverage:.1%} yfinance={yfinance_ok}")
    return "need_intraday_opening_data", notes


def main() -> int:
    _bootstrap()
    from universe.dynamic_build import resolve_symbol_master, load_dynamic_config
    from universe.opening_screen import (
        CHECKPOINTS,
        PUSH_LIMIT,
        PreviousDayFeatures,
        churn_between,
        compute_opening_daytrade_scores,
        fetch_previous_day_yfinance,
        opening_features_from_push,
        load_push_window_first_last,
        select_top50,
    )

    parser = argparse.ArgumentParser(description="Phase 108 opening dynamic50 design")
    parser.add_argument("--trade-date", default=None, help="YYYY-MM-DD")
    parser.add_argument("--day-stamp", default=None, help="YYYYMMDD")
    parser.add_argument("--fetch-yfinance", action="store_true", default=True)
    parser.add_argument("--no-yfinance", action="store_true")
    parser.add_argument("--yfinance-max", type=int, default=800, help="Cap yfinance symbols for speed")
    args = parser.parse_args()

    if args.trade_date:
        trade_d = date.fromisoformat(args.trade_date)
        day_stamp = args.day_stamp or trade_d.strftime("%Y%m%d")
    else:
        day_stamp = args.day_stamp or datetime.now(JST).strftime("%Y%m%d")
        trade_d = date(int(day_stamp[:4]), int(day_stamp[4:6]), int(day_stamp[6:8]))

    use_yf = args.fetch_yfinance and not args.no_yfinance
    push_day = PUSH_ROOT / trade_d.isoformat()
    cfg = load_dynamic_config(NATIVE / "configs" / "universe_dynamic_trial.yaml")
    _, entries = resolve_symbol_master(ROOT, cfg.symbol_master_paths)

    symbol_meta: dict[str, dict[str, Any]] = {}
    all_symbols: list[str] = []
    for e in entries:
        sym = f"{e.parsed.code}.T"
        symbol_meta[sym] = {
            "exchange": e.parsed.exchange,
            "symbol_key": e.parsed.symbol_key,
            "market": e.market,
        }
        all_symbols.append(sym)

    from universe.opening_screen import _norm_symbol

    push_symbols = (
        [_norm_symbol(p.stem) for p in push_day.glob("*.jsonl")] if push_day.is_dir() else []
    )

    prev_by_sym: dict[str, PreviousDayFeatures] = {}
    if use_yf:
        yf_syms = all_symbols[: args.yfinance_max]
        prev_by_sym = fetch_previous_day_yfinance(yf_syms, trade_d)

    prev_cov = len(prev_by_sym) / max(len(all_symbols), 1)

    checkpoint_picks: dict[str, list[Any]] = {}
    checkpoint_scores: dict[str, dict[str, float]] = {}
    opening_cov: dict[str, int] = {}

    for cp in CHECKPOINTS:
        first, last = load_push_window_first_last(push_day, cutoff_hhmm=cp)
        opening_by_sym = {}
        for sym, payload in last.items():
            opening_by_sym[sym] = opening_features_from_push(
                sym,
                payload,
                checkpoint=cp,
                first_payload=first.get(sym),
            )
        opening_cov[cp] = len(opening_by_sym)
        scores = compute_opening_daytrade_scores(all_symbols, prev_by_sym, opening_by_sym)
        checkpoint_scores[cp] = scores
        checkpoint_picks[cp] = select_top50(
            scores,
            prev_by_sym=prev_by_sym,
            opening_by_sym=opening_by_sym,
            symbol_meta=symbol_meta,
        )

    churn_rows: list[dict[str, Any]] = []
    max_churn = 0.0
    for cp_from, cp_to in CHURN_PAIRS:
        syms_a = [p.symbol for p in checkpoint_picks[cp_from]]
        syms_b = [p.symbol for p in checkpoint_picks[cp_to]]
        ch = churn_between(syms_a, syms_b)
        ch["from_checkpoint"] = cp_from
        ch["to_checkpoint"] = cp_to
        max_churn = max(max_churn, ch["churn_rate"])
        churn_rows.append(ch)

    verdict, verdict_notes = determine_verdict(
        prev_coverage=prev_cov,
        opening_coverage_0905=opening_cov.get("09:05", 0),
        max_churn=max_churn,
        yfinance_ok=bool(prev_by_sym),
        push_symbol_count=len(push_symbols),
    )

    out_0905 = REPORTS / f"opening_dynamic50_0905_{day_stamp}.csv"
    out_0910 = REPORTS / f"opening_dynamic50_0910_{day_stamp}.csv"
    out_churn = REPORTS / f"opening_dynamic50_churn_{day_stamp}.csv"
    out_json = REPORTS / f"phase108_opening_screen_design_{day_stamp}.json"

    write_top50_csv(out_0905, checkpoint_picks["09:05"])
    write_top50_csv(out_0910, checkpoint_picks["09:10"])

    with out_churn.open("w", encoding="utf-8", newline="") as f:
        fields = [
            "from_checkpoint",
            "to_checkpoint",
            "churn_rate",
            "added_count",
            "removed_count",
            "stayed_count",
            "added_symbols",
            "removed_symbols",
        ]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in churn_rows:
            w.writerow(
                {
                    **{k: row[k] for k in fields if k not in ("added_symbols", "removed_symbols")},
                    "added_symbols": "|".join(row["added_symbols"]),
                    "removed_symbols": "|".join(row["removed_symbols"]),
                }
            )

    primary = (
        "dynamic50 = rank(previous_day vol_liq + opening 09:00–09:XX features); "
        "static27 removed. "
    )
    if verdict == "use_0905_fixed_dynamic50":
        primary += (
            "Shadow: 5-min recalculation churn is low; fix universe at 09:05 for PUSH. "
            "Expand PUSH/jsonl to full market for opening scores."
        )
    elif verdict == "need_intraday_opening_data":
        primary += "Full-market 09:05 opening data not in repo (PUSH cap ~27 symbols)."
    elif verdict == "intraday_rotation_too_churny":
        primary += "5-min rotation churn too high for safe PUSH swap."
    else:
        primary += "Design formula and shadow CSVs ready."

    report: dict[str, Any] = {
        "phase": 108,
        "day_stamp": day_stamp,
        "trade_date": trade_d.isoformat(),
        "primary_conclusion": primary,
        "verdict": verdict,
        "verdict_notes": verdict_notes,
        "verdict_options": {
            "A": "opening_dynamic50_design_ready",
            "B": "need_intraday_opening_data",
            "C": "intraday_rotation_too_churny",
            "D": "use_0905_fixed_dynamic50",
        },
        "design": {
            "static27_used": False,
            "push_limit": PUSH_LIMIT,
            "checkpoints": list(CHECKPOINTS),
            "opening_window": "09:00-09:XX JST per checkpoint",
            "previous_day_features": [
                "trading_value",
                "atr_pct",
                "intraday_range_pct",
                "volume_surge_5",
                "volatility_liquidity_score",
            ],
            "opening_features": [
                "price_change_pct",
                "range_pct",
                "volume_5m",
                "trading_value_proxy",
                "gap_pct",
                "early_momentum_score",
            ],
            "opening_daytrade_score_formula": (
                "0.35*rank(prev_vol_liq) + 0.25*rank(early_momentum) "
                "+ 0.20*rank(early_tv) + 0.20*rank(early_range)"
            ),
            "five_min_rotation": "shadow calculate only; no PUSH register swap in Phase108",
            "kabu_register_limit_note": "50 symbols max on PUSH; 5-min swap needs separate Phase",
        },
        "data_coverage": {
            "tradable_symbol_count": len(all_symbols),
            "push_jsonl_symbol_count": len(push_symbols),
            "push_jsonl_path": _rel(push_day),
            "previous_day_yfinance_count": len(prev_by_sym),
            "previous_day_coverage_pct": round(prev_cov, 4),
            "opening_push_coverage_by_checkpoint": opening_cov,
        },
        "churn_summary": {
            "max_churn_rate": max_churn,
            "threshold": CHURN_THRESHOLD,
            "pairs": churn_rows,
        },
        "outputs": {
            "opening_dynamic50_0905_csv": _rel(out_0905),
            "opening_dynamic50_0910_csv": _rel(out_0910),
            "opening_dynamic50_churn_csv": _rel(out_churn),
            "phase108_json": _rel(out_json),
        },
        "constraints_confirmed": [
            "no_production_pilot_yaml_change",
            "no_overwrite_universe_intraday_full",
            "no_entry_exit_quality_vol_liq_cap_change",
            "no_symbol_hardcode",
            "no_time_of_day_entry_filter",
            "shadow_dry_run_only",
            "no_push_register_in_phase108",
        ],
    }
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "verdict": verdict,
                "prev_coverage": round(prev_cov, 3),
                "opening_0905": opening_cov.get("09:05"),
                "max_churn": max_churn,
                "json": _rel(out_json),
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
